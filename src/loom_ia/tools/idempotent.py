# SPDX-License-Identifier: Apache-2.0
"""Outils dont l'effet n'est produit qu'une fois (#18, #49).

Le moteur relance un appel interrompu. Pour un outil sans effet de bord c'est
sans conséquence ; pour un outil qui envoie un courriel ou débite un compte,
c'est l'accident. ``@idempotent`` lui donne de quoi s'en garder : l'effet est
mémorisé sous une clé, et l'appel rejoué rend ce qui avait été produit au
lieu de le produire une seconde fois.

    @idempotent
    @tool(side_effects="irreversible")
    async def envoyer_relance(devis_id: str) -> str:
        '''Envoie la relance du devis.'''

Décorer, c'est déclarer : l'outil passe à ``idempotent: true``, et le moteur
le relance sans hésiter — c'est le décorateur qui tient la promesse.

**Deux portées de clé.** Sans ``key``, la clé est celle de l'appel
(``hash(run_id, call_id)``) : deux exécutions du **même** appel n'en font
qu'une. Avec ``key``, l'outil fournit une clé **métier**, tirée de ses
arguments — ``key=lambda a: f"relance:{a['devis']}"`` — et deux appels
distincts qui demandent la même chose n'en font qu'une aussi, même depuis
deux runs ou deux conversations. Une clé métier est préfixée par le client :
deux artisans ne se partagent pas « la relance du devis D-2026-042 ». Elle
l'est aussi par le **nom de l'outil** : deux outils qui tirent la même clé de
leurs arguments (un courriel et un SMS de relance) ne se bloquent pas, et ne
se rendent pas le résultat l'un de l'autre. Renommer l'outil change donc ses
clés. Elle exige un magasin partagé et durable, ce que le chargement vérifie —
le journal d'un run ne la porterait que dans ce run.

Les traces écrites avant ce préfixe (``client:clé``, sans le nom de l'outil)
sont encore lues : si l'une existe, c'est elle qui fait foi pour cet appel —
résultat rendu, réservation respectée, réservation périmée reprise sous elle.
Sans cela, le déploiement ferait refaire tout effet dont la trace date d'avant
lui. Elles ne portent pas le nom de l'outil : pendant leur durée de vie
(24 h par défaut, ou le ``ttl`` de l'outil), deux outils de même clé s'y
confondent encore, comme avant. Ce repli est à retirer une rétention après le
déploiement de tous les workers.

Un appel qui rend un effet déjà mémorisé le **dit** : le moteur écrit un
``idempotency.reused`` dans le journal du run, avec la clé. Sans lui, on y
lirait un appel, un résultat venu d'ailleurs, et rien qui l'explique.

Fenêtre résiduelle : l'effet est mémorisé après coup. Une interruption entre
l'effet et son enregistrement laisse la clé réservée sans résultat. Avec un
magasin partagé, cette réservation périmée se voit, et l'outil dit ce qu'il
advient (``on_unknown``) ; avec le magasin ``journal``, elle ne laisse aucune
trace et l'appel rejoué refait l'effet.

Mémoriser ne fait jamais échouer un appel dont l'effet a eu lieu. Si le magasin
tombe en panne à cet instant, l'appel rend son **vrai** résultat et l'échec est
journalisé (``logger``, niveau erreur) : le présenter comme une panne ferait
relancer le modèle sous une autre clé, donc refaire l'effet. La clé reste alors
réservée sans résultat — la fenêtre résiduelle ci-dessus, ni plus ni moins. Un
résultat qui dépasse ``MAX_RECORDED`` caractères est mémorisé sous une forme
**réduite** : l'appel rend son vrai résultat, et un rejeu rend un résultat qui
dit que l'effet a eu lieu, que son résultat n'a pas été conservé, et de ne pas
le relancer (les références d'artefacts, elles, sont gardées).

Chaque prise de clé a son **jeton de détenteur**, opaque : le magasin le retient
avec la clé, et ``complete`` comme ``release`` ne s'appliquent que s'il
correspond. Si la réservation a expiré et qu'un autre l'a reprise, l'outil
resté en vie ne peut ni effacer ni écraser celle de son successeur. Un magasin
tiers dont ces méthodes n'ont pas de paramètre ``holder`` est appelé comme avant.

Si l'outil lève, la réservation est rendue : un outil qui échoue est réputé
n'avoir rien produit. C'est le contrat demandé à son auteur — produire
l'effet puis lever ferait mentir la promesse. Un délai (celui de l'outil) ou
une annulation (arrêt du run, du process) n'est pas un échec de l'outil : on
ignore où il en était, la réservation n'est **pas** rendue et expire d'elle-même,
après quoi l'effet est d'état inconnu.
"""

import inspect
import json
import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Final, overload

from pydantic import JsonValue

from loom_ia.core.model import MAX_RECORDED, ResultTooLarge, ToolOutput, ToolSpec
from loom_ia.core.ports import IdempotencyStore, KeyScope, ToolContext, ToolError, UnknownEffect
from loom_ia.tools.python import FunctionTool

logger = logging.getLogger(__name__)

# Durée d'une réservation sans délai propre à l'outil : au-delà, elle est
# réputée abandonnée et l'état de son effet devient inconnu.
DEFAULT_RESERVATION: Final = 300.0

# Clé métier d'un appel, tirée de ses arguments.
type KeyMaker = Callable[[dict[str, JsonValue]], str]

# Quelqu'un d'autre tient la clé et n'a pas fini : ce n'est pas un échec, et
# le modèle ne doit surtout pas en relancer un second.
BUSY: Final = "Cet appel est déjà en cours ailleurs : ne le relance pas, attends son résultat."

# Réservation périmée : l'exécution précédente n'a pas rendu la clé, et rien
# ne dit si son effet a eu lieu (#18).
UNKNOWN_EFFECT: Final = (
    "État inconnu : une exécution précédente de cet appel s'est interrompue et "
    "a peut-être produit son effet. Il n'a pas été relancé ; vérifie avant de "
    "le rappeler."
)

# Ce que rend un appel rejoué dont l'effet a eu lieu, mais dont le résultat était
# trop gros pour être mémorisé : le dire, plutôt que de laisser croire à un échec.
OVERSIZED: Final = (
    "Cet appel a déjà produit son effet, mais son résultat ({size} caractères) dépasse "
    "la limite de {limit} caractères mémorisables : il n'a pas été conservé. L'effet "
    "n'est pas relancé ; vérifie son résultat à la source."
)


class IdempotentTool[**P, R]:
    """Outil dont chaque appel ne produit son effet qu'une fois.

    Reste appelable comme la fonction d'origine : la mémorisation n'a lieu que
    par ``invoke``, qui est le chemin du moteur.

    ``key`` : clé métier tirée des arguments ; sans elle, celle de l'appel.
    ``reservation`` : durée de la prise de clé ; par défaut le délai de
    l'outil, sinon ``DEFAULT_RESERVATION``. ``ttl`` : durée pendant laquelle
    le résultat reste mémorisé ; sans elle, celle du magasin.
    ``retry_unknown`` : reprendre une réservation périmée et refaire l'effet,
    au lieu de le signaler — à ne vouloir que si refaire est moins grave que
    ne pas faire, par exemple quand l'API appelée dédoublonne elle-même.
    """

    def __init__(
        self,
        tool: FunctionTool[P, R],
        *,
        key: KeyMaker | None = None,
        reservation: float | None = None,
        ttl: float | None = None,
        retry_unknown: bool = False,
    ) -> None:
        self._tool = tool
        self._spec = tool.spec.model_copy(
            update={"idempotent": True, "business_key": key is not None}
        )
        self._key = key
        self.reservation = reservation
        self.ttl = ttl
        self.retry_unknown = retry_unknown

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    @property
    def held_for(self) -> float:
        """Durée d'une réservation de cet outil."""
        return self.reservation or self._spec.timeout or DEFAULT_RESERVATION

    def key_for(self, arguments: dict[str, JsonValue], context: ToolContext) -> str:
        """Clé sous laquelle cet appel mémorise son effet.

        Une clé métier est préfixée par le client et par l'outil : elle vaut
        pour lui seul, et pour cet outil seul. La clé technique porte déjà
        l'identité du run, donc de son client, et celle de l'appel.
        """
        if self._key is None:
            return context.idempotency_key
        return f"{context.tenant_id}:{self._spec.name}:{self._key(arguments)}"

    def __call__(self, *args: P.args, **kwargs: P.kwargs) -> R:
        return self._tool(*args, **kwargs)

    async def invoke(self, arguments: dict[str, JsonValue], context: ToolContext) -> ToolOutput:
        store = context.idempotency
        if store is None:
            # Appelé hors moteur : il n'y a pas de reprise à craindre.
            return await self._tool.invoke(arguments, context)
        key = self.key_for(arguments, context)
        record = await store.get(key)
        if record is None and self._key is not None:
            # Trace d'avant le préfixe par l'outil : si elle existe, elle fait foi.
            older = f"{context.tenant_id}:{self._key(arguments)}"
            record = await store.get(older)
            if record is not None:
                key = older
        if record is not None:
            if record.status == "completed":
                logger.info(
                    "Outil %s : effet déjà produit sous %s, résultat mémorisé rendu",
                    self._spec.name,
                    key,
                    extra={"run_id": context.run_id, "tenant_id": context.tenant_id},
                )
                # Le moteur en fait un `idempotency.reused` : sans ce mot, le
                # journal du run montrerait un appel sans effet, inexpliqué.
                if context.on_reuse is not None:
                    context.on_reuse(key)
                return ToolOutput.model_validate(record.result)
            if record.alive(datetime.now(UTC)):
                raise ToolError(BUSY)
            if not (self.retry_unknown or context.replay_unknown):
                logger.warning(
                    "Outil %s : réservation %s périmée, effet d'état inconnu",
                    self._spec.name,
                    key,
                    extra={"run_id": context.run_id, "tenant_id": context.tenant_id},
                )
                raise UnknownEffect(UNKNOWN_EFFECT)
        scope = KeyScope(tenant_id=context.tenant_id, session_id=context.session_id)
        proof = _proof(store)
        if not await store.reserve(key, self.held_for, scope, **proof):
            raise ToolError(BUSY)
        try:
            output = await self._tool.invoke(arguments, context)
        except Exception:
            # Seule une exception levée **par l'outil** rend la clé : il est
            # réputé n'avoir rien produit. Un délai ou une annulation venus de
            # l'extérieur (``CancelledError`` n'est pas une ``Exception``) ne
            # disent rien de l'effet, qui a pu avoir lieu : la clé reste prise
            # jusqu'à son expiration, et l'appel suivant tombe sur l'état
            # inconnu (#18) au lieu de refaire l'effet.
            await store.release(key, **proof)
            raise
        await self._record(store, key, output, proof, context)
        return output

    async def _record(
        self,
        store: IdempotencyStore,
        key: str,
        output: ToolOutput,
        proof: dict[str, str],
        context: ToolContext,
    ) -> None:
        """Mémorise l'effet qui vient d'avoir lieu, sans jamais en faire une panne.

        L'effet est produit : l'appel en rend le résultat, quoi que le magasin
        en dise. Un échec d'enregistrement n'est que journalisé.
        """
        payload = output.model_dump(mode="json")
        where = {"run_id": context.run_id, "tenant_id": context.tenant_id}
        try:
            try:
                await store.complete(key, payload, self.ttl, **proof)
            except ResultTooLarge:
                size = len(json.dumps(payload, ensure_ascii=False))
                logger.warning(
                    "Outil %s : résultat de %d caractères trop gros pour être mémorisé sous %s, "
                    "forme réduite enregistrée",
                    self._spec.name,
                    size,
                    key,
                    extra=where,
                )
                await store.complete(key, _reduced(output, size), self.ttl, **proof)
        except Exception:
            logger.exception(
                "Outil %s : effet produit, mais non mémorisé sous %s ; la clé reste réservée "
                "sans résultat, le prochain appel sera d'état inconnu",
                self._spec.name,
                key,
                extra=where,
            )

    def __repr__(self) -> str:
        kind = "métier" if self._key is not None else "technique"
        return f"IdempotentTool({self._spec.name!r}, clé {kind})"


def _proof(store: IdempotencyStore) -> dict[str, str]:
    """Le jeton de détenteur d'une prise de clé, sous la forme d'arguments du port.

    Vide pour un magasin dont une des trois méthodes n'a pas de paramètre
    ``holder`` : écrit avant le jeton, il est appelé comme avant.
    """
    for method in (store.reserve, store.complete, store.release):
        parameters = inspect.signature(method).parameters
        if "holder" not in parameters and not any(
            parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()
        ):
            return {}
    return {"holder": uuid.uuid4().hex}


def _reduced(output: ToolOutput, size: int) -> JsonValue:
    """Ce qu'on mémorise d'un résultat trop gros : un constat, et ses références."""
    note = ToolOutput.text(
        OVERSIZED.format(size=size, limit=MAX_RECORDED), is_error=output.is_error
    )
    return note.model_copy(
        update={"artifacts": output.artifacts, "offloaded": output.offloaded}
    ).model_dump(mode="json")


@overload
def idempotent[**P, R](tool: FunctionTool[P, R], /) -> IdempotentTool[P, R]: ...


@overload
def idempotent[**P, R](
    *,
    key: KeyMaker | None = None,
    reservation: float | None = None,
    ttl: float | None = None,
    retry_unknown: bool = False,
) -> Callable[[FunctionTool[P, R]], IdempotentTool[P, R]]: ...


def idempotent[**P, R](
    tool: FunctionTool[P, R] | None = None,
    /,
    *,
    key: KeyMaker | None = None,
    reservation: float | None = None,
    ttl: float | None = None,
    retry_unknown: bool = False,
) -> IdempotentTool[P, R] | Callable[[FunctionTool[P, R]], IdempotentTool[P, R]]:
    """Mémorise l'effet de l'outil sous sa clé, avec ou sans options."""

    def wrap(target: FunctionTool[P, R]) -> IdempotentTool[P, R]:
        return IdempotentTool(
            target, key=key, reservation=reservation, ttl=ttl, retry_unknown=retry_unknown
        )

    return wrap if tool is None else wrap(tool)
