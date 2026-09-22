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
exige un magasin partagé et durable, ce que le chargement vérifie — le
journal d'un run ne la porterait que dans ce run.

Un appel qui rend un effet déjà mémorisé le **dit** : le moteur écrit un
``idempotency.reused`` dans le journal du run, avec la clé. Sans lui, on y
lirait un appel, un résultat venu d'ailleurs, et rien qui l'explique.

Fenêtre résiduelle : l'effet est mémorisé après coup. Une interruption entre
l'effet et son enregistrement laisse la clé réservée sans résultat. Avec un
magasin partagé, cette réservation périmée se voit, et l'outil dit ce qu'il
advient (``on_unknown``) ; avec le magasin ``journal``, elle ne laisse aucune
trace et l'appel rejoué refait l'effet.

Si l'outil lève, la réservation est rendue : un outil qui échoue est réputé
n'avoir rien produit. C'est le contrat demandé à son auteur — produire
l'effet puis lever ferait mentir la promesse.
"""

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Final, overload

from pydantic import JsonValue

from loom_ia.core.model import ToolOutput, ToolSpec
from loom_ia.core.ports import KeyScope, ToolContext, ToolError, UnknownEffect
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

        Une clé métier est préfixée par le client : elle vaut pour lui seul.
        La clé technique porte déjà l'identité du run, donc de son client.
        """
        if self._key is None:
            return context.idempotency_key
        return f"{context.tenant_id}:{self._key(arguments)}"

    def __call__(self, *args: P.args, **kwargs: P.kwargs) -> R:
        return self._tool(*args, **kwargs)

    async def invoke(self, arguments: dict[str, JsonValue], context: ToolContext) -> ToolOutput:
        store = context.idempotency
        if store is None:
            # Appelé hors moteur : il n'y a pas de reprise à craindre.
            return await self._tool.invoke(arguments, context)
        key = self.key_for(arguments, context)
        record = await store.get(key)
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
        if not await store.reserve(key, self.held_for, scope):
            raise ToolError(BUSY)
        try:
            output = await self._tool.invoke(arguments, context)
        except BaseException:
            await store.release(key)
            raise
        await store.complete(key, output.model_dump(mode="json"), self.ttl)
        return output

    def __repr__(self) -> str:
        kind = "métier" if self._key is not None else "technique"
        return f"IdempotentTool({self._spec.name!r}, clé {kind})"


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
