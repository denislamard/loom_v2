# SPDX-License-Identifier: Apache-2.0
"""Rejouer un run en variante : autre modèle ou autre config, et comparer (fonction K6, point 31, jalon J6.2b).

La variante monte l'agent du run comme le rejeu identique — en mémoire, sous
le même identifiant, la logique de loom tournant pour de vrai —, mais le monde
n'est plus seulement servi par le journal :

- **un appel de modèle** dont la requête est identique à une requête du
  journal reçoit la réponse enregistrée, avant comme après la divergence ;
  sinon, il part pour de vrai (``VariantModelClient``) ;
- **un appel d'outil** est d'abord cherché au journal par son nom et ses
  arguments (pas par ``call_id`` : un vrai modèle en fabrique de nouveaux).
  Retrouvé, il est lu. Sinon : sa **doublure** s'il en a une ; un sous-agent
  est relancé, en variante lui aussi ; un outil à **effets de bord**
  (``side_effects`` autre que ``none``) n'est **jamais** exécuté, et le
  modèle reçoit une erreur qui le dit ; un outil sans effets de bord
  s'exécute pour de vrai (``VariantTools``) ;
- **une approbation** reprend la décision du journal pour un appel retrouvé ;
  rien ne part pour un appel doublé ou refusé, qui est donc accordé ; un
  appel qui s'exécuterait pour de vrai est refusé, personne n'étant là pour
  trancher.

``swap_models`` change le modèle d'une étape de l'agent (``main``, un rôle,
``judge:<nom>``) sans toucher au fichier de config.
"""

import inspect
from collections.abc import AsyncGenerator, Callable, Mapping
from contextlib import aclosing
from dataclasses import dataclass
from typing import Final, Literal

from pydantic import JsonValue

from loom_ia.agents.spec import OUTPUT_JUDGE, AgentSpec, RoleSpec
from loom_ia.config import LoomConfig
from loom_ia.core.model import (
    MAIN_ROLE,
    ApprovalDecision,
    Approved,
    ModelChunk,
    ModelRequest,
    ModelResponse,
    ModelSpec,
    NoAnswer,
    PendingApproval,
    PendingCall,
    PendingQuestion,
    QuestionReply,
    Rejected,
    ResponseAccumulator,
    RunId,
    ToolOutput,
)
from loom_ia.core.ports import AnsweringClient, ModelClient, ModelError, strictest_side_effects
from loom_ia.engine import AnyTool, Consumption
from loom_ia.replay.book import (
    Divergence,
    ReplayBook,
    ReplayError,
    ToolCallRecord,
    provider_of,
    recorded_response,
)
from loom_ia.tools.python import to_output

type ToolFate = Literal["journal", "double", "relaunched", "refused", "run"]
"""Ce qu'un appel d'outil du rejeu en variante devient."""
type Double = Callable[..., object]
"""Une doublure : appelée avec les arguments de l'appel, comme un outil Python
(synchrone ou non) ; sa valeur est traduite comme celle d'un outil.
"""

JUDGE_PREFIX: Final = "judge:"
"""Préfixe de l'étape d'un juge (``judge:<nom>``), telle que le journal la nomme."""
REFUSED: Final = (
    "Non exécuté : rejeu en variante. L'outil {name} a des effets de bord "
    "({effects}) et cet appel n'est pas au journal sous ces arguments ; il n'est "
    "jamais réexécuté."
)
"""Erreur rendue au modèle pour un outil à effets de bord que le journal ne connaît pas sous ces arguments."""
NOBODY: Final = "rejeu en variante : cet appel s'exécuterait pour de vrai, et personne n'est là pour l'approuver"
"""Motif du refus d'une approbation pour un appel qui s'exécuterait pour de vrai."""
_FATES: Final[Mapping[ToolFate, str]] = {
    "journal": "lu au journal",
    "double": "remplacé par sa doublure",
    "relaunched": "sous-agent relancé, en variante",
    "refused": "non exécuté : effets de bord",
    "run": "exécuté pour de vrai (sans effets de bord)",
}


class VariantModelClient(AnsweringClient):
    """Client de modèle de la variante : le journal s'il connaît la requête, sinon le vrai.

    Le vrai client n'est créé qu'au premier appel qui part : un modèle que la
    variante ne fait que relire n'a pas besoin de sa clé.
    """

    def __init__(self, book: ReplayBook, spec: ModelSpec, make: Callable[[], ModelClient]) -> None:
        self.book = book
        self.spec = spec
        self._make = make
        self._real: ModelClient | None = None

    @property
    def provider(self) -> str:
        """Fournisseur du vrai client s'il est créé, sinon celui que l'adaptateur du modèle écrirait."""
        return self._real.provider if self._real is not None else provider_of(self.spec)

    async def answer(self, request: ModelRequest) -> ModelResponse | None:
        """La réponse du journal à une requête identique ; ``None`` sinon : l'appel part pour de vrai, en flux (``stream``).

        Args:
            request: La requête.
        """
        recorded = self.book.known(request)
        return recorded_response(recorded) if recorded is not None else None

    async def stream(self, request: ModelRequest) -> AsyncGenerator[ModelChunk]:
        """Un vrai appel ; ce qu'il a coûté est noté une fois la réponse complète."""
        real = self._client()
        accumulator = ResponseAccumulator()
        async with aclosing(real.stream(request)) as chunks:
            async for chunk in chunks:
                accumulator.add(chunk)
                yield chunk
        usage = accumulator.result(model_id=request.model_id, provider=real.provider).usage
        self.book.paid(self.spec.pricing.cost(usage), usage)

    async def aclose(self) -> None:
        """Ferme le vrai client, s'il a été créé."""
        if self._real is not None:
            await self._real.aclose()

    def _client(self) -> ModelClient:
        if self._real is None:
            try:
                self._real = self._make()
            except ValueError as exc:
                # Clé absente, extra manquant : la variante ne peut pas appeler ce modèle.
                raise ModelError("auth", f"variante : le modèle {self.spec.id} ne peut pas être appelé — {exc}") from exc
        return self._real


@dataclass(frozen=True, slots=True)
class _Decision:
    """Ce que le rejeu en variante fait d'un appel d'outil, décidé une fois.

    Attributes:
        fate: Le sort de l'appel.
        record: L'appel du journal qui le sert (``journal``).
        effects: Effets de bord déclarés de l'outil (``refused``), pour le dire
            au modèle.
    """

    fate: ToolFate
    record: ToolCallRecord | None = None
    effects: str = ""


class VariantTools:
    """Outils de la variante (``ToolReplay``) : lus, doublés, refusés, ou exécutés."""

    def __init__(self, book: ReplayBook, doubles: Mapping[str, Double] | None = None) -> None:
        self.book = book
        self.doubles: dict[str, Double] = dict(doubles or {})
        # Sort de chaque appel du rejeu, par run et ``call_id`` (un identifiant
        # d'appel n'est unique que dans son run) : décidé une fois.
        self.fates: dict[tuple[str, str], ToolFate] = {}
        self._decisions: dict[tuple[str, str], _Decision] = {}

    def serves(self, tool: AnyTool, call: PendingCall, run_id: RunId) -> bool:
        """Vrai si la variante répond à la place de l'outil : lu au journal, doublé ou refusé.

        Un rôle tourne toujours. Le sort de chaque appel est décidé une fois
        (``_decide``) ; un sous-agent relancé ou un outil sans effets de bord
        s'exécute pour de vrai.

        Args:
            tool: L'outil appelé.
            call: L'appel.
            run_id: Run qui appelle.
        """
        if tool.spec.kind == "role":
            # De la logique : elle tourne, son modèle passe par le client de la variante.
            return False
        key = (run_id, call.call_id)
        decision = self._decisions.get(key)
        if decision is None:
            decision = self._decide(tool, call)
            self._decisions[key] = decision
            self.fates[key] = decision.fate
        return decision.fate in ("journal", "double", "refused")

    def from_journal(self, run_id: RunId, call_id: str) -> bool:
        """Vrai pour un appel lu au journal de ce même run.

        Doublé, refusé, ou lu au journal d'un autre run de l'arbre, le résultat
        est neuf : le state s'y pose comme en direct.
        """
        # Doublé ou refusé, le résultat est neuf : le state s'y pose comme en direct.
        # Lu dans le journal d'un autre run de l'arbre aussi : son state n'est pas
        # celui que ce run-ci a posé. Seul un appel lu au journal a un ``record``.
        decision = self._decisions.get((run_id, call_id))
        record = decision.record if decision is not None else None
        return record is not None and record.run_id == run_id

    async def output(
        self,
        run_id: RunId,
        call_id: str,
        name: str,
        arguments: Mapping[str, JsonValue],
        resolved: Mapping[str, JsonValue],
    ) -> tuple[ToolOutput, Consumption | None]:
        """Le résultat de l'appel selon son sort.

        Lu au journal (son sous-run lu avec lui), sa doublure (références
        résolues, arguments remplacés compris), ou l'erreur qui dit au modèle
        qu'il n'est pas exécuté.
        """
        decision = self._decisions[run_id, call_id]
        match decision.fate:
            case "journal":
                assert decision.record is not None
                self.book.read_through(decision.record)
                return self.book.recorded_output(decision.record)
            case "double":
                # Une doublure reçoit ce que l'outil aurait reçu : références
                # résolues, arguments remplacés compris.
                return await doubled(self.doubles[name], name, resolved), None
            case _:
                return ToolOutput.error(REFUSED.format(name=name, effects=decision.effects)), None

    async def answer(self, run_id: RunId, pending: PendingQuestion) -> QuestionReply | NoAnswer:
        """La réponse du journal si la question y est ; sinon personne ne répond en variante."""
        found = self.book.recorded_answer(run_id, pending.call_id)
        if found is not None:
            return found
        return NoAnswer(reason="personne ne répond en variante", expired=True)

    async def approve(self, run_id: RunId, pending: PendingApproval) -> ApprovalDecision:
        """Décision en ligne : celle du journal, ou accordée quand rien ne part pour de vrai."""
        decision = self._decisions.get((run_id, pending.call_id))
        if decision is None or decision.fate in ("run", "relaunched"):
            return Rejected(reason=NOBODY, by="rejeu")
        if decision.fate == "journal":
            assert decision.record is not None
            record = decision.record
            recorded = self.book.recorded_decision(record.run_id, record.called.call_id)
            if recorded is not None:
                return recorded
            return Approved(by="rejeu", reason="appel lu au journal : rien ne part")
        return Approved(by="rejeu", reason=f"appel {_FATES[decision.fate]} : rien ne part")

    def _decide(self, tool: AnyTool, call: PendingCall) -> _Decision:
        """Le sort d'un appel, et la divergence qu'il marque s'il n'est pas au journal.

        Retrouvé au journal par son nom et ses arguments : lu. Sinon, dans l'ordre :
        sa doublure, un sous-agent relancé, un refus s'il a des effets de bord
        (jugés avant les références et les politiques), une exécution réelle.

        Args:
            tool: L'outil appelé.
            call: L'appel.
        """
        record = self.book.match_tool(call.name, call.arguments)
        if record is not None:
            return _Decision("journal", record)
        # Jugé avant les références et les politiques : l'évaluation ne fait que durcir.
        effects = strictest_side_effects(tool, call.arguments)
        fate: ToolFate
        if call.name in self.doubles:
            fate = "double"
        elif tool.spec.kind == "agent":
            fate = "relaunched"
        elif effects != "none":
            fate = "refused"
        else:
            fate = "run"
        self.book.diverge(
            Divergence(
                kind="tool",
                where=f"appel d'outil {call.name} : absent du journal sous ces arguments",
                detail=_FATES[fate] + (f" ({effects})" if fate == "refused" else ""),
            )
        )
        return _Decision(fate, effects=effects)


async def doubled(double: Double, name: str, arguments: Mapping[str, JsonValue]) -> ToolOutput:
    """Le résultat d'une doublure, traduit comme celui d'un outil ; son erreur, dite au modèle."""
    try:
        value = double(**arguments)
        if inspect.isawaitable(value):
            value = await value
        return to_output(value)
    except Exception as exc:
        return ToolOutput.error(f"Doublure de {name} en erreur : {type(exc).__name__}: {exc}")


def fate_label(fate: ToolFate) -> str:
    """Le sort d'un appel du rejeu en variante, en clair.

    Args:
        fate: ``journal``, ``double``, ``relaunched``, ``refused`` ou ``run``.
    """
    return _FATES[fate]


def swap_models(config: LoomConfig, agent: str, models: Mapping[str, str]) -> LoomConfig:
    """La config où les étapes nommées de ``agent`` appellent un autre modèle déclaré.

    Une étape : ``main`` (l'orchestrateur), le nom d'un rôle, ou ``judge:<nom>``
    — les noms que le journal donne aux appels de modèle. Les secours restent
    ceux de la config.
    """
    if not models:
        return config
    found = [spec for spec in config.agents if spec.name == agent]
    if not found:
        raise ReplayError(f"Variante : l'agent {agent!r} n'est pas dans la config")
    spec = found[0]
    declared = {model.id for model in config.models}
    unknown = sorted({model for model in models.values() if model not in declared})
    if unknown:
        raise ReplayError(f"Variante : modèle(s) non déclaré(s) dans la config : {', '.join(unknown)}")
    steps = _steps(spec)
    wrong = sorted(set(models) - set(steps))
    if wrong:
        raise ReplayError(f"Variante : étape(s) inconnue(s) de l'agent {agent!r} : {', '.join(wrong)} (étapes : {', '.join(steps)})")
    changed = _swapped(spec, models)
    agents = tuple(changed if a.name == agent else a for a in config.agents)
    return config.model_copy(update={"agents": agents})


def _steps(spec: AgentSpec) -> list[str]:
    """Les étapes d'un agent qui appellent un modèle, sous leur nom au journal."""
    return [
        MAIN_ROLE,
        *(role.name for role in spec.roles),
        *(f"{JUDGE_PREFIX}{name}" for name, _, _ in spec.judges),
    ]


def _swapped(spec: AgentSpec, models: Mapping[str, str]) -> AgentSpec:
    """La spec de l'agent où chaque étape nommée appelle son nouveau modèle : ``main``, le juge final, les rôles et leurs juges."""
    update: dict[str, object] = {}
    if MAIN_ROLE in models:
        update["main"] = spec.main.model_copy(update={"model": models[MAIN_ROLE]})
    judge = spec.judge
    final_judge = f"{JUDGE_PREFIX}{(judge.name or OUTPUT_JUDGE) if judge else ''}"
    if judge is not None and final_judge in models:
        update["judge"] = judge.model_copy(update={"model": models[final_judge]})
    roles: list[RoleSpec] = []
    for role in spec.roles:
        changes: dict[str, object] = {}
        if role.name in models:
            changes["model"] = models[role.name]
        if role.judge is not None:
            key = f"{JUDGE_PREFIX}{role.judge.name or role.name}"
            if key in models:
                changes["judge"] = role.judge.model_copy(update={"model": models[key]})
        roles.append(role.model_copy(update=changes) if changes else role)
    update["roles"] = tuple(roles)
    return spec.model_copy(update=update)
