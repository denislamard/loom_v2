# SPDX-License-Identifier: Apache-2.0
"""État d'un run (#3, #4).

Le ``RunState`` n'est jamais stocké : c'est la projection des événements de
son ``run_id`` (#24). Il ne contient que des données sérialisables.
"""

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Final, Literal

from pydantic import Field, JsonValue, NonNegativeFloat, NonNegativeInt

from loom_ia.core.model.base import DomainModel
from loom_ia.core.model.budget import RunBudget, Spent
from loom_ia.core.model.context import CallerContext
from loom_ia.core.model.ids import RunId, SessionId, SpanId
from loom_ia.core.model.judge import JudgesMode
from loom_ia.core.model.media import ArtifactRecord
from loom_ia.core.model.messages import Message
from loom_ia.core.model.usage import Usage

# Durée d'une concession sur un run, en secondes (#27) : renouvelée au tiers,
# reprise par un autre worker si son porteur se tait plus longtemps.
DEFAULT_LEASE: Final = 60.0

# Un run de compaction résume une session (#23) : il vit dans le journal de
# cette session, mais n'entre ni dans son historique ni dans son arbre.
type RunKind = Literal["normal", "compaction"]

# Pourquoi un run a été arrêté avant sa fin (A5). Un dépassement du délai
# maximal n'est pas ici : il est subi, donc écrit en ``run.failed`` avec
# ``error_type: timeout`` (A6).
type CancelReason = Literal["requested", "parent"]


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


@dataclass(frozen=True, slots=True)
class Approved:
    """L'approbateur autorise l'appel, avec d'autres arguments s'il le veut (#17)."""

    by: str | None = None
    reason: str = ""
    arguments: dict[str, JsonValue] | None = None


@dataclass(frozen=True, slots=True)
class Rejected:
    """L'approbateur refuse l'appel : le motif revient au modèle (#17)."""

    reason: str = ""
    by: str | None = None


type ApprovalDecision = Approved | Rejected


type ApprovalVerdict = Literal["granted", "rejected", "expired"]


class ApprovalOutcome(DomainModel):
    """Ce qu'une demande d'approbation est devenue (#17)."""

    verdict: ApprovalVerdict
    # Qui a tranché : clé d'API, utilisateur, ou nom rendu par un approbateur
    # en ligne. C'est l'audit demandé par #17, et il n'est nulle part ailleurs.
    by: str | None = None
    reason: str = ""
    # Arguments corrigés en accordant ; absents, ceux de la demande valent.
    arguments: dict[str, JsonValue] | None = None


class PendingApproval(DomainModel):
    """Demande d'approbation écrite au journal (#17, D10).

    Elle attend tant qu'``outcome`` est absent, et c'est ce qui tient le run
    en ``PAUSED``. Une décision la referme sans l'effacer : le déroulé se
    relit entier, et la reprise sait quoi faire de l'appel.
    """

    call_id: str
    tool_name: str
    arguments: dict[str, JsonValue] = Field(default_factory=dict)
    reason: str = ""
    # Politique qui l'a exigée ; absente pour un outil en ``approval: always``.
    policy: str | None = None
    scope: str = "approve"
    expire_at: datetime | None = None
    outcome: ApprovalOutcome | None = None
    # Un ``tool.called`` a été écrit depuis l'accord : il a servi à un lancement,
    # et ne couvre pas le suivant (#17, #18). Déduit de l'ordre du journal.
    launched: bool = False

    def stale(self, now: datetime) -> bool:
        """Demande sans réponse dont la date est passée.

        C'est le journal qui fait foi, pas le travail différé : une file
        perdue dans un redémarrage ne peut pas laisser une demande
        approuvable indéfiniment.
        """
        return self.outcome is None and self.expire_at is not None and now >= self.expire_at


# Approbateur en ligne (#28) : il décide dans la boucle, sans que le run
# passe par l'état PAUSED. Ce qu'il rend est écrit au journal comme une
# décision ordinaire — l'audit de #17 vaut aussi pour les scripts.
type Approver = Callable[[PendingApproval], Awaitable[ApprovalDecision]]


class PendingRepair(DomainModel):
    """Réparation décidée par une politique (``Retry``), pas encore demandée au modèle."""

    policy: str
    point: Literal["after_model", "on_output"]
    feedback: str
    # Faux : l'orchestrateur répare sans outils (échec de forme).
    tools: bool = True


class RunClaim(DomainModel):
    """Concession en cours sur un run : qui le pilote, et jusqu'à quand (#27).

    Un seul pilote à la fois. Le porteur la renouvelle tant qu'il vit ; s'il
    meurt, elle expire et un autre worker peut reprendre le run.
    """

    worker_id: str
    lease_until: datetime

    def alive(self, now: datetime) -> bool:
        return now < self.lease_until


def state_size(value: JsonValue) -> int:
    """Taille d'une valeur du state : son JSON compact, en octets UTF-8 (D7)."""
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode())


class StateWrite(DomainModel):
    """Écritures du state de la session portées par le résultat réussi d'un appel.

    ``turn`` est la position, dans les messages du run, de la réponse du
    modèle qui a demandé l'appel, et ``rank`` le rang de l'appel dans cette
    réponse : c'est l'ordre des appels, et non celui d'arrivée des résultats,
    qui départage deux écritures d'une même clé (D9).
    """

    call_id: str
    turn: NonNegativeInt
    rank: NonNegativeInt
    values: dict[str, JsonValue]


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
    # Run ordinaire, ou run système de compaction (#23).
    kind: RunKind = "normal"
    context: CallerContext = CallerContext()
    # Juges choisis par l'appelant (#21) : selon leur ``when``, tous, ou aucun.
    judges: JudgesMode = "auto"
    # Part de budget reçue du run parent (``budget_share``) ; les limites de
    # l'agent s'y ajoutent, la plus basse l'emporte.
    budget: RunBudget | None = None

    status: RunStatus = RunStatus.READY_FOR_MODEL
    # Numéro de la dernière étape commencée.
    step: NonNegativeInt = 0
    # Temps passé à piloter ce run : somme des étapes terminées. C'est lui que
    # borne le délai maximal d'un agent (A6) — pas l'horloge depuis
    # ``run.started``, qui compterait aussi l'attente en file, une pause
    # d'approbation ou une nuit entre un plantage et sa reprise.
    active_ms: NonNegativeFloat = 0.0
    # Appels du modèle orchestrateur (borné par max_iterations).
    iterations: NonNegativeInt = 0
    messages: tuple[Message, ...] = ()
    pending_calls: tuple[PendingCall, ...] = ()
    usage: Usage = Usage()
    cost_usd: NonNegativeFloat = 0.0
    # Appels de modèle du run : orchestrateur, rôles et juges (``max_calls``) ;
    # ceux d'un sous-agent se comptent dans son propre run.
    model_calls: NonNegativeInt = 0
    # Limites de budget déjà signalées (``budget.exceeded``) : ``run.max_cost``…
    exceeded: tuple[str, ...] = ()
    # Modèle courant des emplacements qui ont basculé vers un secours (``main``,
    # rôle, ``judge:<nom>``) : le run le garde jusqu'à la fin (adhérence, #10).
    models: dict[str, str] = Field(default_factory=dict[str, str])
    # Fichiers du run (pièces jointes, sorties d'outils, déports), une fois par URI.
    artifacts: tuple[ArtifactRecord, ...] = ()
    # Écritures du state de la session faites par les outils du run, dans
    # l'ordre des appels (D9) ; un résultat en erreur n'en porte pas.
    state_writes: tuple[StateWrite, ...] = ()

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
    # Échec : son type (``guard.judge``, ``model.auth``, ``policy.<nom>``…) et son message.
    error_type: str | None = None
    error: str | None = None
    # Motif de l'annulation, s'il y en a eu une (A5).
    cancelled: CancelReason | None = None
    # Dernière concession prise sur ce run (#27) ; None si personne ne l'a
    # encore pilotée, ou si le journal est antérieur à 4.2b.
    claim: RunClaim | None = None
    # Approbations du run (#17), dans l'ordre des demandes. Celles qui
    # attendent encore tiennent le run en PAUSED ; les autres gardent leur
    # décision, que la reprise applique à l'appel.
    approvals: tuple[PendingApproval, ...] = ()
    # Vrai après run.completed, run.failed ou run.cancelled : plus rien n'est accepté.
    finished: bool = False
    # Dernier événement appliqué.
    last_seq: NonNegativeInt = 0

    @property
    def state_delta(self) -> dict[str, JsonValue]:
        """State écrit par ce run : ses écritures fusionnées dans l'ordre des appels.

        Une valeur ``None`` y reste : appliquée au state de la session, elle
        efface la clé (D10).
        """
        delta: dict[str, JsonValue] = {}
        for write in self.state_writes:
            delta |= write.values
        return delta

    @property
    def spent(self) -> Spent:
        """Consommation du run : usage et coût (sous-agents compris), ses appels de modèle."""
        return Spent(self.usage, self.cost_usd, self.model_calls)

    def pending(self, call_id: str) -> PendingCall | None:
        return next((c for c in self.pending_calls if c.call_id == call_id), None)

    @property
    def awaiting(self) -> tuple[PendingApproval, ...]:
        """Demandes d'approbation sans réponse : le run les attend (#17).

        Un run fini, ou déjà passé à son état final, n'attend plus rien : une
        demande restée sans réponse (run annulé en pause, échec à l'échéance)
        reste dans ``approvals``, pour l'historique, mais personne ne peut plus
        la trancher.
        """
        if self.finished or self.status.is_terminal:
            return ()
        return tuple(a for a in self.approvals if a.outcome is None)

    def approval(self, call_id: str) -> PendingApproval | None:
        """Dernière demande d'approbation de cet appel, tranchée ou non.

        Un appel peut en porter plusieurs : la première couvre son lancement ;
        une autre vient si l'effet de ce lancement devient d'état inconnu.
        """
        return next((a for a in reversed(self.approvals) if a.call_id == call_id), None)

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
