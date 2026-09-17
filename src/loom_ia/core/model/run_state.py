# SPDX-License-Identifier: Apache-2.0
"""État d'un run (#3, #4).

Le ``RunState`` n'est jamais stocké : c'est la projection des événements de
son ``run_id`` (#24). Il ne contient que des données sérialisables.
"""

from enum import StrEnum

from pydantic import Field, JsonValue, NonNegativeFloat, NonNegativeInt

from loom_ia.core.model.base import DomainModel
from loom_ia.core.model.context import CallerContext
from loom_ia.core.model.ids import RunId, SessionId, SpanId
from loom_ia.core.model.messages import Message
from loom_ia.core.model.usage import Usage


class RunStatus(StrEnum):
    READY_FOR_MODEL = "ready_for_model"
    AWAITING_TOOLS = "awaiting_tools"
    WAITING_CHILD = "waiting_child"
    PAUSED = "paused"
    FINALIZING = "finalizing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self in {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED}


class PendingCall(DomainModel):
    """Appel d'outil demandé par le modèle et pas encore terminé."""

    call_id: str
    name: str
    arguments: dict[str, JsonValue] = Field(default_factory=dict)
    # Vrai dès que ``tool.called`` est écrit : sert à la reprise (#18, #26).
    started: bool = False
    # Run enfant quand l'outil est un sous-agent (#4).
    child_run_id: RunId | None = None


class RunState(DomainModel):
    run_id: RunId
    session_id: SessionId
    root_run_id: RunId
    # Span racine du run, repris à chaque reprise.
    span_id: SpanId
    parent_span_id: SpanId | None = None
    parent_run_id: RunId | None = None
    parent_call_id: str | None = None
    depth: NonNegativeInt = 0
    agent: str
    context: CallerContext = CallerContext()

    status: RunStatus = RunStatus.READY_FOR_MODEL
    # Numéro de la dernière étape commencée.
    step: NonNegativeInt = 0
    # Appels du modèle orchestrateur (borné par max_iterations).
    iterations: NonNegativeInt = 0
    messages: tuple[Message, ...] = ()
    pending_calls: tuple[PendingCall, ...] = ()
    usage: Usage = Usage()
    cost_usd: NonNegativeFloat = 0.0

    output: Message | None = None
    error: str | None = None
    # Vrai après run.completed ou run.failed : plus aucun événement accepté.
    finished: bool = False
    # Dernier événement appliqué.
    last_seq: NonNegativeInt = 0

    def pending(self, call_id: str) -> PendingCall | None:
        return next((c for c in self.pending_calls if c.call_id == call_id), None)
