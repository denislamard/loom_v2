# SPDX-License-Identifier: Apache-2.0
"""Payloads typés des événements durables (#22).

Chaque classe déclare son ``type`` (``<catégorie>.<action au passé>``), sa
catégorie et ses facettes : les champs de recherche que l'enveloppe recopie
pour que les stores les indexent sans connaître les payloads.

Événements des jalons J1 et J2. Les autres types (guards, approbations,
artefacts, compaction…) arrivent avec leurs phases.

Un appel de modèle fait par un rôle délégué (C2) est journalisé dans le run
de l'orchestrateur, entre le ``tool.called`` et le ``tool.completed`` de
l'appel : ``call_id`` le relie à cet appel, et l'enveloppe porte le nom du
rôle.
"""

from typing import Annotated, ClassVar, Literal

from pydantic import Field, JsonValue, NonNegativeFloat, NonNegativeInt, PositiveInt

from loom_ia.core.model.base import DomainModel
from loom_ia.core.model.content import ToolOutput
from loom_ia.core.model.context import CallerContext
from loom_ia.core.model.ids import EventId, RunId
from loom_ia.core.model.messages import Message
from loom_ia.core.model.run_state import RunStatus
from loom_ia.core.model.streaming import ModelErrorKind, StopReason
from loom_ia.core.model.tooling import ToolKind
from loom_ia.core.model.usage import Usage

type EventCategory = Literal[
    "run", "message", "model", "tool", "guard", "approval", "artifact", "session"
]
type EventStatus = Literal["ok", "warning", "error"]
type FacetValue = str | int | float | bool | None


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
    output: Message | None = None
    # Événement qui porte la sortie quand elle vient d'un outil terminal (#13).
    output_event_id: EventId | None = None
    iterations: NonNegativeInt = 0
    usage: Usage = Usage()
    cost_usd: NonNegativeFloat = 0.0


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
    category: ClassVar[EventCategory] = "message"

    type: Literal["message.user"] = "message.user"
    message: Message


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

    def facets(self) -> dict[str, FacetValue]:
        return {**super().facets(), "tokens": self.usage.total_tokens}


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

    @property
    def event_status(self) -> EventStatus:
        return "warning"


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


class ToolCompleted(Payload):
    category: ClassVar[EventCategory] = "tool"
    facet_fields: ClassVar[tuple[str, ...]] = ("tool_name", "latency_ms", "size")

    type: Literal["tool.completed"] = "tool.completed"
    call_id: str
    tool_name: str
    output: ToolOutput
    latency_ms: NonNegativeFloat = 0.0
    size: NonNegativeInt = 0

    @property
    def event_status(self) -> EventStatus:
        return "error" if self.output.is_error else "ok"

    def facets(self) -> dict[str, FacetValue]:
        return {**super().facets(), "is_error": self.output.is_error}


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
    | ToolCompleted,
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
)
