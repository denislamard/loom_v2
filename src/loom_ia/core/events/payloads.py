# SPDX-License-Identifier: Apache-2.0
"""Payloads typés des événements durables (#22).

Chaque classe déclare son ``type`` (``<catégorie>.<action au passé>``), sa
catégorie et ses facettes : les champs de recherche que l'enveloppe recopie
pour que les stores les indexent sans connaître les payloads.

Événements des jalons J1 et J2, des politiques (J3.1) et des guards (J3.2). Les autres types
(guards, approbations, compaction…) arrivent avec leurs phases.

Un appel de modèle fait par un rôle délégué (C2) est journalisé dans le run
de l'orchestrateur, entre le ``tool.called`` et le ``tool.completed`` de
l'appel : ``call_id`` le relie à cet appel, et l'enveloppe porte le nom du
rôle.

Un sous-agent (C5, #4) tourne dans un run enfant du même journal : son
``run.started`` porte ``parent_run_id``, ``parent_call_id`` et ``depth``, et
l'enveloppe le ``root_run_id`` de l'arbre. Côté parent, le ``tool.called`` de
l'appel donne l'identifiant de l'enfant (``child_run_id``), et le
``tool.completed`` sa consommation, ajoutée à celle du parent.

Guards (#20) : chaque contrôle d'une sortie écrit un ``guard.checked``,
réussi ou non, avant la décision qu'il motive.

Budgets (J4) : une limite atteinte écrit un ``budget.exceeded``, une fois
par limite, avant le ``policy.decided`` qui arrête le run (``on_exceed: stop``).

Juges (#21) : l'appel du modèle d'un juge est journalisé dans le run jugé
(``model.retried``, ``model.responded`` avec ``judge``, enveloppe au nom de
``judge:<nom>``) : son coût s'ajoute au run, pas ses itérations. Son verdict
est un ``judge.evaluated`` (notes par critère), suivi du ``guard.checked``.

Politiques (#2) : toute décision autre que ``Continue`` écrit un
``policy.decided`` avant son effet, dans le span de l'étape ou de l'appel
concerné. Une demande de réparation (``Retry``) est suivie d'un
``message.user`` de ``kind: repair`` qui porte le diagnostic.

Les fichiers ne sont jamais dans le journal : ``artifact.stored`` annonce
qu'un fichier a été rangé dans le stockage d'artefacts, et les messages ne
portent que sa référence. Un bloc d'octets (``inline_data``) y est refusé.
"""

from typing import Annotated, ClassVar, Final, Literal, Self

from pydantic import (
    Field,
    JsonValue,
    NonNegativeFloat,
    NonNegativeInt,
    PositiveInt,
    model_validator,
)

from loom_ia.core.model.base import DomainModel
from loom_ia.core.model.budget import BudgetLimit, BudgetScope, OnExceed, RunBudget
from loom_ia.core.model.content import ToolOutput, has_inline_data
from loom_ia.core.model.context import CallerContext
from loom_ia.core.model.ids import EventId, RunId
from loom_ia.core.model.judge import CriterionScore, JudgesMode
from loom_ia.core.model.media import ArtifactOrigin, ArtifactRecord
from loom_ia.core.model.messages import Message
from loom_ia.core.model.policy import CheckOutcome, CheckResolution, DecisionKind, HookPoint
from loom_ia.core.model.run_state import RunStatus
from loom_ia.core.model.streaming import ModelErrorKind, StopReason
from loom_ia.core.model.tooling import ToolKind
from loom_ia.core.model.usage import Usage

type EventCategory = Literal[
    "run", "message", "model", "tool", "guard", "policy", "approval", "artifact", "session"
]
type EventStatus = Literal["ok", "warning", "error"]
type FacetValue = str | int | float | bool | None


INLINE_REFUSED: Final = "{label} : octets de fichier interdits dans le journal (référence attendue)"


def _no_inline_data(message: Message | None, label: str) -> None:
    if message is not None and has_inline_data(message.blocks):
        raise ValueError(INLINE_REFUSED.format(label=label))


class Payload(DomainModel):
    category: ClassVar[EventCategory]
    facet_fields: ClassVar[tuple[str, ...]] = ()

    @property
    def event_status(self) -> EventStatus:
        return "ok"

    def facets(self) -> dict[str, FacetValue]:
        values: dict[str, FacetValue] = {}
        for name in self.facet_fields:
            value: FacetValue = getattr(self, name)
            values[name] = value
        return values


# --- Run ---------------------------------------------------------------------


class RunStarted(Payload):
    category: ClassVar[EventCategory] = "run"
    facet_fields: ClassVar[tuple[str, ...]] = ("kind",)

    type: Literal["run.started"] = "run.started"
    kind: Literal["normal", "compaction"] = "normal"
    context: CallerContext = CallerContext()
    parent_run_id: RunId | None = None
    parent_call_id: str | None = None
    depth: NonNegativeInt = 0
    # Run qui a déclenché celui-ci (compaction, #23).
    triggered_by: RunId | None = None
    # Juges choisis par l'appelant (#21) ; un sous-run hérite du choix de son parent.
    judges: JudgesMode = "auto"
    # Part de budget donnée par le run parent à un sous-run (``budget_share``, #4).
    budget: RunBudget | None = None

    def facets(self) -> dict[str, FacetValue]:
        facets = super().facets()
        if self.judges != "auto":
            # Absente sinon : les journaux antérieurs restent lisibles.
            facets["judges"] = self.judges
        return facets


type Effect = Literal["model_call", "tool_batch", "finalize", "wait_child"]


class StepStarted(Payload):
    category: ClassVar[EventCategory] = "run"
    facet_fields: ClassVar[tuple[str, ...]] = ("effect",)

    type: Literal["step.started"] = "step.started"
    step_no: PositiveInt
    state: RunStatus
    effect: Effect


class StepCompleted(Payload):
    category: ClassVar[EventCategory] = "run"
    facet_fields: ClassVar[tuple[str, ...]] = ("outcome", "duration_ms")

    type: Literal["step.completed"] = "step.completed"
    step_no: PositiveInt
    duration_ms: NonNegativeFloat
    events_emitted: NonNegativeInt = 0
    outcome: Literal["ok", "error", "cancelled"] = "ok"

    @property
    def event_status(self) -> EventStatus:
        return "ok" if self.outcome == "ok" else "error"


class RunTransitioned(Payload):
    category: ClassVar[EventCategory] = "run"
    facet_fields: ClassVar[tuple[str, ...]] = ("from_state", "to_state")

    type: Literal["run.transitioned"] = "run.transitioned"
    from_state: RunStatus
    to_state: RunStatus
    step_no: NonNegativeInt = 0
    # Événement déclencheur (#3).
    cause_type: str | None = None
    cause_event_id: EventId | None = None


class RunCompleted(Payload):
    category: ClassVar[EventCategory] = "run"
    facet_fields: ClassVar[tuple[str, ...]] = ("iterations", "cost_usd")

    type: Literal["run.completed"] = "run.completed"
    # Réponse finale. Absente quand elle est la sortie d'un outil terminal,
    # sauf si une politique ``on_output`` l'a remplacée.
    output: Message | None = None
    # Événement qui porte la sortie quand elle vient d'un outil terminal (#13).
    output_event_id: EventId | None = None
    iterations: NonNegativeInt = 0
    usage: Usage = Usage()
    cost_usd: NonNegativeFloat = 0.0
    # Réponse structurée : l'objet JSON validé par le schéma de sortie de l'agent (A7).
    data: JsonValue = None
    # Réponse gardée bien qu'un contrat ou un juge la refuse (``on_failure: unverified``).
    unverified: bool = False

    @model_validator(mode="after")
    def _check_output(self) -> Self:
        _no_inline_data(self.output, "run.completed")
        return self

    @property
    def event_status(self) -> EventStatus:
        return "warning" if self.unverified else "ok"

    def facets(self) -> dict[str, FacetValue]:
        facets = super().facets()
        if self.unverified:
            # Absente sinon : les journaux antérieurs restent lisibles.
            facets["unverified"] = True
        return facets


class RunFailed(Payload):
    category: ClassVar[EventCategory] = "run"
    facet_fields: ClassVar[tuple[str, ...]] = ("error_type",)

    type: Literal["run.failed"] = "run.failed"
    error_type: str
    error: str
    iterations: NonNegativeInt = 0
    usage: Usage = Usage()
    cost_usd: NonNegativeFloat = 0.0

    @property
    def event_status(self) -> EventStatus:
        return "error"


# --- Messages et modèles -----------------------------------------------------


class UserMessage(Payload):
    """Message adressé au modèle orchestrateur.

    ``request`` : la demande de l'utilisateur. ``repair`` : le diagnostic
    d'une réponse refusée par une politique (#20) ; avec la réponse refusée,
    il est exclu de l'historique de session. ``tools`` dit si l'orchestrateur
    garde ses outils pour réparer.
    """

    category: ClassVar[EventCategory] = "message"

    type: Literal["message.user"] = "message.user"
    message: Message
    kind: Literal["request", "repair"] = "request"
    # Politique qui a demandé la réparation.
    policy: str | None = None
    tools: bool = True

    @model_validator(mode="after")
    def _check_message(self) -> Self:
        _no_inline_data(self.message, "message.user")
        return self

    def facets(self) -> dict[str, FacetValue]:
        # Absente pour une demande : les journaux antérieurs restent lisibles.
        return {"kind": self.kind} if self.kind != "request" else {}


class ModelResponded(Payload):
    category: ClassVar[EventCategory] = "model"
    facet_fields: ClassVar[tuple[str, ...]] = (
        "model_id",
        "provider",
        "stop_reason",
        "cost_usd",
        "latency_ms",
    )

    type: Literal["model.responded"] = "model.responded"
    model_id: str
    provider: str
    message: Message
    usage: Usage = Usage()
    cost_usd: NonNegativeFloat = 0.0
    stop_reason: StopReason = "end"
    latency_ms: NonNegativeFloat = 0.0
    attempts: PositiveInt = 1
    # Empreinte de la requête envoyée : détection de divergence au rejeu (#31).
    request_hash: str
    # Appel d'outil servi par cette réponse (rôle délégué) ; None pour l'orchestrateur.
    call_id: str | None = None
    # Juge qui a fait cet appel (#21) : sa réponse n'entre pas dans la conversation.
    judge: str | None = None

    @model_validator(mode="after")
    def _check_message(self) -> Self:
        _no_inline_data(self.message, "model.responded")
        return self

    def facets(self) -> dict[str, FacetValue]:
        facets: dict[str, FacetValue] = {**super().facets(), "tokens": self.usage.total_tokens}
        if self.judge is not None:
            facets["judge"] = self.judge
        return facets


class ModelRetried(Payload):
    """Tentative d'appel échouée, suivie d'une nouvelle tentative (#10)."""

    category: ClassVar[EventCategory] = "model"
    facet_fields: ClassVar[tuple[str, ...]] = ("model_id", "provider", "error_kind", "attempt")

    type: Literal["model.retried"] = "model.retried"
    model_id: str
    provider: str
    # Numéro de la tentative qui a échoué.
    attempt: PositiveInt
    error_kind: ModelErrorKind
    error: str
    http_status: int | None = None
    # Attente avant la tentative suivante, en secondes.
    delay_s: NonNegativeFloat
    # Appel d'outil servi par cet appel de modèle (rôle délégué).
    call_id: str | None = None
    # Juge qui a fait cet appel (#21).
    judge: str | None = None

    @property
    def event_status(self) -> EventStatus:
        return "warning"

    def facets(self) -> dict[str, FacetValue]:
        facets = super().facets()
        if self.judge is not None:
            facets["judge"] = self.judge
        return facets


# --- Outils ------------------------------------------------------------------


class ToolCalled(Payload):
    category: ClassVar[EventCategory] = "tool"
    facet_fields: ClassVar[tuple[str, ...]] = ("tool_name", "tool_kind")

    type: Literal["tool.called"] = "tool.called"
    call_id: str
    tool_name: str
    tool_kind: ToolKind
    # Tels que le modèle les a écrits, références ``$ref`` comprises.
    arguments: dict[str, JsonValue] = Field(default_factory=dict)
    # Références résolues dans les arguments avant l'exécution (#12).
    refs: tuple[str, ...] = ()
    # Nouvelle exécution d'un appel interrompu (#18).
    resumed: bool = False
    # Run enfant d'un sous-agent : repris, et non relancé, après une interruption (#4).
    child_run_id: RunId | None = None


class ToolCompleted(Payload):
    category: ClassVar[EventCategory] = "tool"
    facet_fields: ClassVar[tuple[str, ...]] = ("tool_name", "latency_ms", "size")

    type: Literal["tool.completed"] = "tool.completed"
    call_id: str
    tool_name: str
    output: ToolOutput
    latency_ms: NonNegativeFloat = 0.0
    size: NonNegativeInt = 0
    # Consommation d'un sous-agent (tout son run), ajoutée à celle du parent (#4).
    usage: Usage | None = None
    cost_usd: NonNegativeFloat = 0.0

    @model_validator(mode="after")
    def _check_output(self) -> Self:
        if has_inline_data(self.output.blocks):
            raise ValueError(INLINE_REFUSED.format(label="tool.completed"))
        return self

    @property
    def event_status(self) -> EventStatus:
        return "error" if self.output.is_error else "ok"

    def facets(self) -> dict[str, FacetValue]:
        facets: dict[str, FacetValue] = {**super().facets(), "is_error": self.output.is_error}
        if self.output.offloaded is not None:
            # Absente quand elle est fausse : les journaux antérieurs restent lisibles.
            facets["offloaded"] = True
        return facets


class ToolSourceUnavailable(Payload):
    """Source d'outils injoignable au début du run : ses outils sont retirés (#19)."""

    category: ClassVar[EventCategory] = "tool"
    facet_fields: ClassVar[tuple[str, ...]] = ("source", "required")

    type: Literal["tool.source_unavailable"] = "tool.source_unavailable"
    source: str
    error: str
    # Vrai si le run ne peut pas s'en passer : il échoue.
    required: bool = False

    @property
    def event_status(self) -> EventStatus:
        return "error" if self.required else "warning"


# --- Politiques --------------------------------------------------------------


class PolicyDecided(Payload):
    """Décision d'une politique autre que ``Continue`` (#2), ou d'une règle du moteur.

    Écrite avant son effet. Selon la décision : ``reason`` porte le motif
    (``Deny``, ``Stop``, ``Replace``), le diagnostic (``Retry``) ou l'erreur
    (``Fail``). Un ``Replace`` d'arguments garde les nouveaux arguments, repris
    tels quels si l'appel est relancé ; un ``Replace`` de la réponse finale
    garde la réponse. Un ``Replace`` de requête ou de résultat se lit dans
    l'événement suivant (``model.responded``, ``tool.completed``).

    ``continue`` n'est écrit que par exception : politique en erreur laissée
    passer (``on_error: allow``, ``error``), ou règle du moteur qui écarte un
    comportement déclaré (outil terminal appelé avec d'autres, #13).
    """

    category: ClassVar[EventCategory] = "policy"
    facet_fields: ClassVar[tuple[str, ...]] = ("policy", "point", "decision")

    type: Literal["policy.decided"] = "policy.decided"
    policy: str
    point: HookPoint
    decision: DecisionKind
    reason: str = ""
    # Appel d'outil concerné (``before_tool``, ``after_tool``).
    call_id: str | None = None
    # ``Retry`` : numéro de la réparation demandée par cette politique dans le run.
    attempt: PositiveInt | None = None
    # ``Retry`` : l'orchestrateur garde ses outils pour réparer.
    tools: bool | None = None
    # ``Replace`` à ``before_tool`` : arguments qui partent à la place de ceux du modèle.
    arguments: dict[str, JsonValue] | None = None
    # ``Replace`` à ``on_output`` : réponse finale retenue.
    output: Message | None = None
    # Décision imposée par une erreur de la politique (exception, délai, décision non permise).
    error: bool = False

    @model_validator(mode="after")
    def _check_output(self) -> Self:
        _no_inline_data(self.output, "policy.decided")
        return self

    @property
    def event_status(self) -> EventStatus:
        if self.decision == "fail":
            return "error"
        if self.error or self.decision == "continue":
            return "warning"
        return "ok"


class GuardChecked(Payload):
    """Contrôle d'une sortie par un guard, réussi ou non (#20).

    Écrit avant la décision qu'il motive (``policy.decided``). ``attempt``
    compte les contrôles de cette sortie : 1, puis 2 après une réparation…
    ``resolution`` dit la suite d'un échec : réparation demandée, ou, une fois
    les réparations épuisées, ``fail``, ``unverified`` ou ``fallback``.
    """

    category: ClassVar[EventCategory] = "guard"
    facet_fields: ClassVar[tuple[str, ...]] = ("guard", "target", "outcome")

    type: Literal["guard.checked"] = "guard.checked"
    guard: str
    # ``output`` (réponse finale), ``role:<nom>`` ou ``tool:<nom>``.
    target: str
    outcome: CheckOutcome
    reason: str = ""
    attempt: PositiveInt = 1
    normalized: bool = False
    resolution: CheckResolution | None = None
    # Politique qui a fait le contrôle.
    policy: str | None = None
    call_id: str | None = None

    @property
    def event_status(self) -> EventStatus:
        if self.outcome != "failed":
            return "ok"
        return "error" if self.resolution == "fail" else "warning"


class BudgetExceeded(Payload):
    """Limite d'un budget atteinte, vue avant un appel de l'orchestrateur (J4).

    Écrit une fois par limite et par run ; avec ``action: stop``, il précède le
    ``policy.decided`` qui fait passer le run en ``FINALIZING``.
    """

    category: ClassVar[EventCategory] = "policy"
    facet_fields: ClassVar[tuple[str, ...]] = ("scope", "limit", "action")

    type: Literal["budget.exceeded"] = "budget.exceeded"
    scope: BudgetScope
    limit: BudgetLimit
    # Plafond, et consommation au moment du contrôle ($, tokens ou appels).
    value: NonNegativeFloat
    spent: NonNegativeFloat
    action: OnExceed
    policy: str | None = None

    @property
    def event_status(self) -> EventStatus:
        return "warning"

    @property
    def key(self) -> str:
        """Limite concernée : ``run.max_cost``, ``session.max_tokens``…"""
        return f"{self.scope}.{self.limit}"


class JudgeEvaluated(Payload):
    """Verdict d'un juge sur une sortie : une note par critère (#21).

    Écrit après l'appel du modèle du juge, avant le ``guard.checked`` qui en
    tire la suite. ``blocked`` : un critère bloquant est sous son seuil, et la
    sortie est refusée.
    """

    category: ClassVar[EventCategory] = "guard"
    facet_fields: ClassVar[tuple[str, ...]] = ("judge", "target", "model_id", "passed", "blocked")

    type: Literal["judge.evaluated"] = "judge.evaluated"
    judge: str
    # ``output`` (réponse finale) ou ``role:<nom>``.
    target: str
    model_id: str
    criteria: tuple[CriterionScore, ...] = Field(min_length=1)
    # Tous les critères atteignent leur seuil.
    passed: bool
    blocked: bool
    attempt: PositiveInt = 1
    # Politique qui a fait le contrôle.
    policy: str | None = None
    call_id: str | None = None

    @model_validator(mode="after")
    def _check_verdict(self) -> Self:
        if self.passed != all(c.passed for c in self.criteria):
            raise ValueError("judge.evaluated : 'passed' incohérent avec les notes")
        if self.blocked != any(c.blocking and not c.passed for c in self.criteria):
            raise ValueError("judge.evaluated : 'blocked' incohérent avec les notes")
        return self

    @property
    def event_status(self) -> EventStatus:
        return "ok" if self.passed else "warning"


# --- Artefacts ---------------------------------------------------------------


class ArtifactStored(Payload):
    """Fichier rangé dans le stockage d'artefacts (G2, #16).

    Pièce jointe de la demande (écrit avant ``message.user``), fichier produit
    par un outil ou résultat déporté (écrits avant le ``tool.completed`` de
    l'appel, dans son span).
    """

    category: ClassVar[EventCategory] = "artifact"
    facet_fields: ClassVar[tuple[str, ...]] = ("origin", "media_type", "size")

    type: Literal["artifact.stored"] = "artifact.stored"
    uri: str
    media_type: str
    size: NonNegativeInt
    name: str | None = None
    origin: ArtifactOrigin
    # Appel d'outil qui l'a produit (sortie ou déport).
    call_id: str | None = None

    @property
    def record(self) -> ArtifactRecord:
        return ArtifactRecord(
            uri=self.uri,
            media_type=self.media_type,
            size=self.size,
            name=self.name,
            origin=self.origin,
            call_id=self.call_id,
        )


type DurablePayload = Annotated[
    RunStarted
    | StepStarted
    | StepCompleted
    | RunTransitioned
    | RunCompleted
    | RunFailed
    | UserMessage
    | ModelResponded
    | ModelRetried
    | ToolCalled
    | ToolCompleted
    | ToolSourceUnavailable
    | PolicyDecided
    | GuardChecked
    | JudgeEvaluated
    | BudgetExceeded
    | ArtifactStored,
    Field(discriminator="type"),
]

DURABLE_PAYLOADS: tuple[type[Payload], ...] = (
    RunStarted,
    StepStarted,
    StepCompleted,
    RunTransitioned,
    RunCompleted,
    RunFailed,
    UserMessage,
    ModelResponded,
    ModelRetried,
    ToolCalled,
    ToolCompleted,
    ToolSourceUnavailable,
    PolicyDecided,
    GuardChecked,
    JudgeEvaluated,
    BudgetExceeded,
    ArtifactStored,
)
