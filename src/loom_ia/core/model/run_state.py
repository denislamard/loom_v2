# SPDX-License-Identifier: Apache-2.0
"""État d'un run (#3, #4).

Le ``RunState`` n'est jamais stocké : c'est la projection des événements de
son ``run_id`` (#24). Il ne contient que des données sérialisables.
"""

from enum import StrEnum
from typing import Literal

from pydantic import Field, JsonValue, NonNegativeFloat, NonNegativeInt

from loom_ia.core.model.base import DomainModel
from loom_ia.core.model.context import CallerContext
from loom_ia.core.model.ids import RunId, SessionId, SpanId
from loom_ia.core.model.judge import JudgesMode
from loom_ia.core.model.media import ArtifactRecord
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
    # Arguments remplacés par une politique ``before_tool`` : repris tels quels
    # si l'appel est relancé après une interruption (#2).
    replaced_arguments: dict[str, JsonValue] | None = None


class PendingRepair(DomainModel):
    """Réparation décidée par une politique (``Retry``), pas encore demandée au modèle."""

    policy: str
    point: Literal["after_model", "on_output"]
    feedback: str
    # Faux : l'orchestrateur répare sans outils (échec de forme).
    tools: bool = True


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
    # Juges choisis par l'appelant (#21) : selon leur ``when``, tous, ou aucun.
    judges: JudgesMode = "auto"

    status: RunStatus = RunStatus.READY_FOR_MODEL
    # Numéro de la dernière étape commencée.
    step: NonNegativeInt = 0
    # Appels du modèle orchestrateur (borné par max_iterations).
    iterations: NonNegativeInt = 0
    messages: tuple[Message, ...] = ()
    pending_calls: tuple[PendingCall, ...] = ()
    usage: Usage = Usage()
    cost_usd: NonNegativeFloat = 0.0
    # Fichiers du run (pièces jointes, sorties d'outils, déports), une fois par URI.
    artifacts: tuple[ArtifactRecord, ...] = ()

    # Réparations de l'orchestrateur demandées par chaque politique (``Retry`` à
    # ``after_model`` ou ``on_output``), bornées par son ``max_attempts``.
    retries: dict[str, NonNegativeInt] = Field(default_factory=dict[str, NonNegativeInt])
    # ``Retry`` journalisé dont le message de réparation n'est pas encore écrit.
    pending_repair: PendingRepair | None = None
    # Positions des messages de réparation dans ``messages`` : avec la réponse
    # refusée qui les précède, ils sont exclus de l'historique de session (#20).
    repairs: tuple[NonNegativeInt, ...] = ()
    # Prochaine réponse de l'orchestrateur sans outils (réparation de forme).
    repair_without_tools: bool = False
    # Réponse finale remplacée par une politique ``on_output``.
    replaced_output: Message | None = None

    output: Message | None = None
    # Réponse finale structurée : l'objet JSON validé par le schéma de sortie (A7).
    output_data: JsonValue = None
    # Réponse finale gardée bien qu'un contrat ou un juge la refuse (``unverified``).
    unverified: bool = False
    # Appel dont le résultat est devenu la réponse finale (#13).
    terminal_call_id: str | None = None
    error: str | None = None
    # Vrai après run.completed ou run.failed : plus aucun événement accepté.
    finished: bool = False
    # Dernier événement appliqué.
    last_seq: NonNegativeInt = 0

    def pending(self, call_id: str) -> PendingCall | None:
        return next((c for c in self.pending_calls if c.call_id == call_id), None)

    @property
    def attachments(self) -> tuple[ArtifactRecord, ...]:
        """Pièces jointes de la demande, dans l'ordre où elles ont été données."""
        return tuple(a for a in self.artifacts if a.origin == "attachment")

    @property
    def offloaded(self) -> bool:
        """Vrai si au moins un résultat a été déporté dans ce run (#16)."""
        return any(a.origin == "offload" for a in self.artifacts)

    def artifact(self, uri: str) -> ArtifactRecord | None:
        return next((a for a in self.artifacts if a.uri == uri), None)
