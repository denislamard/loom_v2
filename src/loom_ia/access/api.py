# SPDX-License-Identifier: Apache-2.0
"""Accès Python : la façade ``Loom`` (N1).

Une instance rassemble ce qui était jusqu'ici assemblé à la main : la
configuration, les agents, le journal, les clients de modèle. Les deux autres
accès du jalon J1.6 — l'API REST et le serveur MCP — passent par elle, si
bien qu'un run donne le même journal quel que soit le chemin emprunté.

    async with Loom.from_config("loom.yaml") as loom:
        result = await loom.run("demo", "Bonjour")
        print(result.text)

``stream`` mêle les événements du journal et les morceaux du modèle, dans
l'ordre où ils arrivent : c'est la même source pour le direct de la CLI et
pour le flux SSE.

Sous-runs (C5) : ``stream``, ``follow`` et ``events`` rendent par défaut
l'arbre du run — ses événements et ceux de ses sous-runs, dans l'ordre du
journal ; chaque événement dit à quel run il appartient (``run_id``,
``agent``). ``subruns=False`` s'en tient au run lui-même. Les morceaux du
modèle d'un sous-run ne sont pas diffusés : sa réponse arrive avec le
``tool.completed`` de l'appel.

Fichiers (G1 à G3) : ``run`` et ``stream`` acceptent des pièces jointes
(``Attachment``), validées puis rangées dans le stockage d'artefacts de
l'instance ; ``RunResult.artifacts`` liste les fichiers du run, et
``artifact(uri)`` en rend les octets.

    photo = Attachment.from_path("photo.jpg")
    result = await loom.run("assistant", "Que montre la photo ?", attachments=[photo])

Disjoncteurs (#10) : ceux des modèles et des serveurs MCP sont communs à
tous les runs de l'instance. Un modèle écarté après ses échecs l'est pour
tous ses agents, qui passent directement à leur secours.

Clients (L1, #33) : un run appartient toujours à un client — ``default``
en mode librairie, où il ne surcharge rien. ``tenant=`` le nomme sur
``run``, ``stream`` et ``submit``, et il se relit ensuite sur chaque
opération qui vise un run ou une session. Les agents sont montés **par
client** : deux clients du même agent n'ont ni les mêmes modèles, ni les
mêmes secrets, ni les mêmes connexions MCP.

Budgets et quotas d'un client (L3, J5.1b) : ils bornent ce qu'un client a le
droit de **lancer**, et sont donc vérifiés avant d'ouvrir le run — un budget
de journée épuisé lève ``BudgetExhausted``, un débit dépassé
``QuotaExceeded``, et rien n'est écrit au journal. Les deux portent les
secondes à attendre.

Résultat (J3) : ``RunResult`` dit, en plus de la réponse, si elle a été
gardée sans respecter son contrat ou son juge (``unverified``), la
consommation ventilée du run et de ses sous-runs (``report`` : par run, par
rôle, par modèle) et les verdicts des juges (``verdicts``). Un échec a un
type (``error_type`` : ``guard.judge``, ``model.auth``…) et un message
lisible (``error``). Les trois accès rendent ce même résultat.
"""

import asyncio
import logging
import re
from collections.abc import AsyncGenerator, Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path
from typing import Final, Self

from pydantic import AwareDatetime, Field, JsonValue, NonNegativeInt, PositiveInt

from loom_ia.adapters.queue import Handler
from loom_ia.adapters.stores import PLAIN, JournalCodec, NotifyingEventStore, SealingCodec
from loom_ia.adapters.usage import InMemoryUsageCounter
from loom_ia.agents.registry import AgentRegistry
from loom_ia.agents.spec import AgentSpec
from loom_ia.config import ConfigError, LoomConfig, load_config
from loom_ia.config.compaction import COMPACTION_AGENT
from loom_ia.config.models import TriggerSpec
from loom_ia.config.references import Registry
from loom_ia.core.events import (
    ApprovalGranted,
    ApprovalRejected,
    ApprovalRequested,
    Event,
    EventDraft,
    EventQuery,
    JudgeEvaluated,
    RunCompleted,
    RunFailed,
    SessionCompacted,
)
from loom_ia.core.model import (
    DEFAULT_TENANT,
    Approver,
    ArtifactRecord,
    Attachment,
    BudgetPeriod,
    CallerContext,
    CriterionScore,
    JudgesMode,
    Message,
    ModelChunk,
    PendingApproval,
    RunId,
    RunKind,
    RunState,
    RunStatus,
    SessionId,
    SpanId,
    Spent,
    TenantId,
    Usage,
    new_id,
    new_run_id,
)
from loom_ia.core.model.base import DomainModel
from loom_ia.core.ports import (
    ArtifactStore,
    ChunkCallback,
    EventStore,
    Job,
    JobKind,
    SecretProvider,
    ServedQueue,
    SessionRecord,
    TaskQueue,
    UsageCounter,
)
from loom_ia.core.projections import RunTree, fold
from loom_ia.core.template import Template
from loom_ia.engine import (
    CircuitBreakers,
    ClaimConflict,
    RunContext,
    SessionWriter,
    SessionWriters,
    begin_run,
    cancellation,
    drive,
    run_scope,
)
from loom_ia.runtime import (
    Agent,
    announce,
    build_agent,
    create_artifact_store,
    create_bus,
    create_event_store,
    create_idempotency_store,
    create_keyring,
    create_mcp_pool,
    create_task_queue,
    encryption_warnings,
    load_registry,
    storage_warnings,
)
from loom_ia.sessions import CompactionJob, CompactionPlan, write_snapshot
from loom_ia.tenancy import (
    EnvironmentSecrets,
    Quota,
    RoutedArtifactStore,
    RoutedEventStore,
    Tenant,
    TenantConsumption,
    TenantRouter,
    Tenants,
    TenantUsage,
    UnknownTenant,
)
from loom_ia.usage import UsageReport, usage_report

logger = logging.getLogger(__name__)

# Ce qu'un run donne à voir pendant qu'il se déroule.
type StreamItem = Event | ModelChunk

# File servie par un courtier (5.3b) : combien de fois un travail attend la fin
# d'un bail avant d'être abandonné au journal, et de combien on dépasse ce bail.
MAX_CLAIM_WAITS: Final = 20
CLAIM_MARGIN: Final = 0.5

# Bornes d'une liste de runs (K5) : runs rendus, et journaux ouverts pour les
# trouver. La seconde est celle qui tient le coût d'une lecture sans projection.
RUNS_LIMIT: Final = 50
SESSIONS_READ: Final = 50
# Un identifiant de livraison, et la session qu'un déclencheur en tire : ce que
# le journal JSONL accepte comme nom de fichier (adapters/stores/jsonl.py).
_SAFE_ID: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")

# Ce qu'un appelant peut demander au plus : une lecture par journal ouvert, donc
# une borne bien plus basse que celle d'une recherche d'événements.
RUNS_MAX: Final = 200
SESSIONS_MAX: Final = 200


def is_final(event: Event) -> bool:
    """Vrai sur l'événement qui clôt un run."""
    return isinstance(event.payload, RunCompleted | RunFailed)


class AgentNotAllowed(PermissionError):
    """Cet agent n'est pas ouvert à ce client (L1)."""

    def __init__(self, agent: str, tenant_id: TenantId) -> None:
        super().__init__(f"Agent {agent!r} non ouvert au client {tenant_id!r}")
        self.agent = agent
        self.tenant_id = tenant_id


class UnknownRun(KeyError):
    """Aucun run de cet identifiant dans le journal."""

    def __init__(self, run_id: RunId) -> None:
        super().__init__(f"Run {run_id} inconnu")
        self.run_id = run_id


class UnknownApproval(KeyError):
    """Aucune demande d'approbation en attente pour cet appel."""

    def __init__(self, run_id: RunId, call_id: str) -> None:
        super().__init__(f"Run {run_id} : aucune approbation en attente pour l'appel {call_id!r}")


class UnknownSession(KeyError):
    """Aucune session de cet identifiant dans le journal."""

    def __init__(self, session_id: SessionId) -> None:
        super().__init__(f"Session {session_id} inconnue")
        self.session_id = session_id


class UnknownTrigger(KeyError):
    """Aucune porte d'entrée de ce nom dans la configuration (H6)."""

    def __init__(self, name: str, declared: Sequence[str]) -> None:
        known = ", ".join(declared) or "aucun"
        super().__init__(f"Déclencheur {name!r} non déclaré (déclencheurs : {known})")
        self.name = name


class DeliveryRefused(ValueError):
    """La livraison porte un identifiant ou une session inutilisables (H6)."""


class Triggered(DomainModel):
    """Ce qu'une livraison a ouvert (H6, J5.4c)."""

    trigger: str
    run_id: RunId
    session_id: SessionId
    status: RunStatus
    # Vrai si cette livraison avait déjà été reçue : le run est retrouvé, pas
    # rouvert. Les plateformes réessaient, et une relance ne part qu'une fois.
    repeated: bool = False


class SessionDeletion(DomainModel):
    """Ce qu'a retiré la suppression d'une session (RGPD, F7)."""

    session_id: SessionId
    events: NonNegativeInt = 0
    artifacts: NonNegativeInt = 0
    # Clés d'idempotence oubliées : celles d'un magasin partagé, le magasin
    # ``journal`` gardant les siennes dans les événements ci-dessus.
    keys: NonNegativeInt = 0


class SessionSwept(DomainModel):
    """Une session que la rétention a retirée, ou retirerait (5.5c)."""

    tenant_id: TenantId
    session_id: SessionId
    # Dernière écriture : c'est elle, et elle seule, qui décide.
    last_write: AwareDatetime
    # Longueur du journal, connue par la marque de la session : vraie dans les
    # deux modes, sans rien ouvrir.
    events: NonNegativeInt = 0
    # Fichiers et clés : connus en supprimant, donc ``None`` en essai à blanc.
    # Un zéro y mentirait — « aucun fichier » n'est pas « je n'ai pas regardé ».
    artifacts: NonNegativeInt | None = None
    keys: NonNegativeInt | None = None


class RetentionReport(DomainModel):
    """Ce qu'un balayage a fait, ou ferait (5.5c).

    ``dry_run`` dit lequel des deux : sans lui, rien n'a été supprimé. Le
    rapport dit aussi son **prix** — combien de sessions ont été regardées —
    et la borne appliquée à chaque client, parce qu'une rétention qui n'efface
    rien et une rétention non déclarée se ressemblent trop.
    """

    dry_run: bool
    # Borne par client, telle qu'elle a été lue ; ``None`` : aucune règle.
    days: dict[TenantId, NonNegativeInt | None] = Field(
        default_factory=dict[TenantId, NonNegativeInt | None]
    )
    scanned: NonNegativeInt = 0
    kept: NonNegativeInt = 0
    swept: tuple[SessionSwept, ...] = ()

    @property
    def events(self) -> int:
        """Événements retirés, ou qui le seraient."""
        return sum(session.events for session in self.swept)


def _masked_approvals(approvals: tuple[PendingApproval, ...]) -> tuple[PendingApproval, ...]:
    """Demandes d'approbation sans leurs arguments ni leur motif.

    Conséquence assumée : **approuver demande de lire**. Une clé qui a
    ``approve`` sans ``read_content`` trancherait à l'aveugle, et
    ``loom validate`` le signale.
    """
    return tuple(
        approval.model_copy(update={"arguments": {}, "reason": ""}) for approval in approvals
    )


class RunSummary(DomainModel):
    """Un run de la session, tel que la fiche le montre (F7)."""

    run_id: RunId
    agent: str
    status: RunStatus
    # Run délégant, pour le run d'un sous-agent (#4).
    parent_run_id: RunId | None = None
    iterations: NonNegativeInt = 0
    usage: Usage = Usage()
    cost_usd: float = 0.0

    @classmethod
    def of(cls, state: RunState) -> Self:
        return cls(
            run_id=state.run_id,
            agent=state.agent,
            status=state.status,
            parent_run_id=state.parent_run_id,
            iterations=state.iterations,
            usage=state.usage,
            cost_usd=state.cost_usd,
        )


class RunListed(DomainModel):
    """Un run tel qu'une liste le montre (K5) : de quoi le situer et le retrouver.

    Aucun contenu ici, et c'est voulu : ni la réponse, ni le message d'erreur,
    seulement son **type**. Une liste n'a donc pas à être masquée (5.2a), et la
    portée ``read`` suffit à la lire.
    """

    run_id: RunId
    session_id: SessionId
    agent: str
    status: RunStatus
    kind: RunKind = "normal"
    parent_run_id: RunId | None = None
    # Premier et dernier événement du run au journal.
    started_at: AwareDatetime
    updated_at: AwareDatetime
    iterations: NonNegativeInt = 0
    usage: Usage = Usage()
    cost_usd: float = 0.0
    # Type de l'échec (``guard.judge``, ``model.auth``, ``timeout``…), jamais
    # son message : celui-là est du contenu, et se lit sur le run.
    error_type: str | None = None

    @classmethod
    def of(cls, state: RunState, events: Sequence[Event]) -> Self:
        return cls(
            run_id=state.run_id,
            session_id=state.session_id,
            agent=state.agent,
            status=state.status,
            kind=state.kind,
            parent_run_id=state.parent_run_id,
            started_at=events[0].ts,
            updated_at=events[-1].ts,
            iterations=state.iterations,
            usage=state.usage,
            cost_usd=state.cost_usd,
            error_type=state.error_type,
        )


class RunPage(DomainModel):
    """Une page de runs, et ce qu'il a fallu lire pour l'obtenir (K5).

    ``scanned`` et ``truncated`` disent le prix et l'incomplétude : la liste
    est tirée du journal, session par session, sans projection à tenir à jour.
    """

    runs: tuple[RunListed, ...] = ()
    # Journaux de session ouverts pour cette page.
    scanned: NonNegativeInt = 0
    # Vrai si une borne a arrêté la recherche : il peut y en avoir d'autres.
    truncated: bool = False


class SessionInfo(DomainModel):
    """Ce qu'une session contient, sans dérouler son journal (F7).

    Les événements eux-mêmes se relisent avec ``export_session``.
    """

    session_id: SessionId
    # Dernier ``seq`` écrit : la taille du journal.
    last_seq: NonNegativeInt = 0
    updated_at: AwareDatetime
    # Les runs de la session, dans l'ordre où ils y sont entrés ; ceux des
    # sous-agents compris, chacun nommant son délégant.
    runs: tuple[RunSummary, ...] = ()
    # Ce qui attend un humain, tous runs confondus (#17) : sans cela,
    # l'appelant devrait ouvrir chaque run pour savoir ce qu'on lui demande.
    pending_approvals: tuple[PendingApproval, ...] = ()

    def masked(self) -> Self:
        """Fiche sans le contenu : les runs et leurs coûts, pas les arguments."""
        return self.model_copy(
            update={"pending_approvals": _masked_approvals(self.pending_approvals)}
        )


class JudgeVerdict(DomainModel):
    """Verdict d'un juge sur une sortie du run ou d'un de ses sous-runs (``judge.evaluated``)."""

    run_id: RunId
    agent: str
    judge: str
    # ``output`` (réponse finale) ou ``role:<nom>``.
    target: str
    # Appel du rôle jugé (``tool.called``) ; None pour la réponse finale.
    call_id: str | None = None
    model_id: str
    # Tous les critères atteignent leur seuil.
    passed: bool
    # Un critère bloquant est sous son seuil : la sortie a été refusée.
    blocked: bool
    attempt: PositiveInt = 1
    criteria: tuple[CriterionScore, ...] = ()

    def masked(self) -> Self:
        """Verdict sans ses motifs : les notes restent, la prose part."""
        return self.model_copy(
            update={"criteria": tuple(c.model_copy(update={"reason": ""}) for c in self.criteria)}
        )

    @classmethod
    def of(cls, event: Event, verdict: JudgeEvaluated) -> Self:
        return cls(
            run_id=event.run_id,
            agent=event.agent or "",
            judge=verdict.judge,
            target=verdict.target,
            call_id=verdict.call_id,
            model_id=verdict.model_id,
            passed=verdict.passed,
            blocked=verdict.blocked,
            attempt=verdict.attempt,
            criteria=verdict.criteria,
        )


class RunResult(DomainModel):
    """Ce qu'un run a produit, tiré de son état final et de son journal."""

    run_id: RunId
    session_id: SessionId
    agent: str
    status: RunStatus
    text: str = ""
    output: Message | None = None
    # Échec : son type (``guard.judge``, ``guard.contract``, ``model.auth``,
    # ``policy.<nom>``…) et son message, lisible tel quel.
    error_type: str | None = None
    error: str | None = None
    iterations: int = 0
    usage: Usage = Usage()
    cost_usd: float = 0.0
    # Fichiers du run : pièces jointes, fichiers produits par les outils, déports (G3).
    artifacts: tuple[ArtifactRecord, ...] = ()
    # Réponse structurée : l'objet JSON validé par le schéma de sortie (A7).
    data: JsonValue = None
    # Réponse gardée bien qu'elle ne respecte pas son contrat (``on_failure: unverified``).
    unverified: bool = False
    # Consommation ventilée du run et de ses sous-runs : par run, par rôle, par modèle.
    report: UsageReport | None = None
    # Verdicts des juges du run et de ses sous-runs, dans l'ordre du journal.
    verdicts: tuple[JudgeVerdict, ...] = ()
    # Approbations qu'il faut trancher pour que ce run avance (#17), celles
    # de ses sous-runs comprises : l'appelant n'a pas à savoir qu'un
    # sous-agent existe pour savoir ce qu'on lui demande.
    pending_approvals: tuple[PendingApproval, ...] = ()

    def masked(self) -> Self:
        """Résultat sans le contenu, pour une clé qui n'a pas ``read_content``.

        Ce qui reste est ce dont une supervision a besoin : l'agent, le
        statut, les itérations, la consommation, la ventilation, et le verdict
        de chaque juge — sans les motifs, qui citent la sortie. Ce qui part est
        la correspondance : réponse, objet structuré, message d'erreur, noms de
        fichiers et arguments des approbations en attente.

        Le type de l'erreur reste : « pourquoi ça a échoué » n'est pas du
        contenu, et sans lui il n'y aurait plus rien à superviser.
        """
        return self.model_copy(
            update={
                "text": "",
                "output": None,
                "error": None,
                "data": None,
                "artifacts": tuple(a.model_copy(update={"name": None}) for a in self.artifacts),
                "verdicts": tuple(v.masked() for v in self.verdicts),
                "pending_approvals": _masked_approvals(self.pending_approvals),
            }
        )

    @classmethod
    def of(cls, state: RunState, events: Sequence[Event] = ()) -> Self:
        """Résultat d'un état final.

        ``events`` : le journal de sa session, d'où viennent le rapport et les verdicts.
        """
        tree = RunTree(state.run_id).select(events)
        return cls(
            run_id=state.run_id,
            session_id=state.session_id,
            agent=state.agent,
            status=state.status,
            text=state.output.text if state.output else "",
            output=state.output,
            error_type=state.error_type,
            error=state.error,
            iterations=state.iterations,
            usage=state.usage,
            cost_usd=state.cost_usd,
            artifacts=state.artifacts,
            data=state.output_data,
            unverified=state.unverified,
            report=usage_report(tree, state.session_id, state.run_id) if tree else None,
            verdicts=tuple(
                JudgeVerdict.of(event, payload)
                for event in tree
                if isinstance(payload := event.payload, JudgeEvaluated)
            ),
            pending_approvals=_awaited(state, tree),
        )

    @property
    def ok(self) -> bool:
        return self.status is RunStatus.COMPLETED

    @property
    def produced(self) -> tuple[ArtifactRecord, ...]:
        """Fichiers produits par les outils du run."""
        return tuple(a for a in self.artifacts if a.origin == "tool_output")


def _awaited(state: RunState, tree: Sequence[Event]) -> tuple[PendingApproval, ...]:
    """Demandes d'approbation en attente dans tout l'arbre d'un run (#17).

    Un sous-agent qui se met en pause arrête son parent : ce qu'il faut
    trancher pour que le run avance n'est pas forcément dans le run lui-même.
    """
    runs = dict.fromkeys(event.run_id for event in tree if event.run_id != state.run_id)
    inner = [
        approval
        for run_id in runs
        for approval in fold([e for e in tree if e.run_id == run_id], run_id).awaiting
    ]
    return (*state.awaiting, *inner)


def _usable(what: str, value: str) -> str:
    """Un identifiant qui peut servir de nom de journal, ou un refus qui le dit.

    Une livraison porte ce que l'appelant veut : l'en-tête d'un service tiers
    finit en ``run_id``, donc en nom de session, donc en nom de fichier pour le
    journal JSONL. Il est contrôlé à la porte, et non au moment d'écrire.
    """
    if not _SAFE_ID.fullmatch(value):
        raise DeliveryRefused(
            f"{what} inutilisable : {value!r} (attendu lettres, chiffres, '.', '_' ou '-')"
        )
    return value


def _keeps(
    run: RunListed,
    *,
    agent: str | None,
    status: Sequence[RunStatus],
    since: datetime | None,
    until: datetime | None,
) -> bool:
    """Vrai si ce run passe les filtres d'une liste."""
    return all(
        (
            agent is None or run.agent == agent,
            not status or run.status in status,
            since is None or run.updated_at >= since,
            until is None or run.updated_at < until,
        )
    )


def _by_run(events: Sequence[Event]) -> dict[RunId, list[Event]]:
    """Événements d'un journal rangés par run, dans l'ordre où les runs y sont entrés.

    Les marqueurs de session sont écartés : ils portent l'identifiant de leur
    session, pas celui d'un run.
    """
    owned: dict[RunId, list[Event]] = {}
    for event in events:
        if event.category != "session":
            owned.setdefault(event.run_id, []).append(event)
    return owned


def _run_states(events: Sequence[Event]) -> list[RunState]:
    """État de chaque run d'un journal, dans l'ordre où ils y sont entrés."""
    return [fold(own, run_id) for run_id, own in _by_run(events).items()]


def _awaiting_runs(tree: Sequence[Event]) -> list[RunState]:
    """Runs de l'arbre qui attendent une approbation, dans l'ordre du journal."""
    return [state for state in _run_states(tree) if state.awaiting]


def _where(
    tree: Sequence[Event], run_id: RunId, call_id: str
) -> tuple[SpanId | None, SpanId | None]:
    """Span de la demande qu'une décision vient trancher (#17).

    Une décision prise hors de la boucle — par REST, par la CLI, par
    ``approve()`` — n'a pas d'étape à elle : elle se range donc **là où la
    demande attendait**, et les deux se lisent d'un bloc, comme lorsqu'un
    approbateur en ligne tranche dans le lot. Sans demande retrouvée, le span
    racine du run vaut.
    """
    for event in reversed(tree):
        if (
            event.run_id == run_id
            and isinstance(request := event.payload, ApprovalRequested)
            and request.call_id == call_id
        ):
            return event.span_id, event.parent_span_id
    return None, None


def _unfinished(events: Sequence[Event]) -> list[RunState]:
    """Runs racine d'un journal qui peuvent encore avancer (#27, H3).

    Un sous-run n'y est pas : son parent le reprend en rejouant l'appel
    d'outil qui l'a lancé. Un run de compaction non plus — il sera refait si
    la session en a besoin.

    Les marqueurs de session sont écartés : ils portent l'identifiant de leur
    session, pas celui d'un run, et une session résumée en a.
    """
    states: list[RunState] = []
    runs = (e.run_id for e in events if e.category != "session")
    for run_id in dict.fromkeys(runs):
        state = fold([e for e in events if e.run_id == run_id], RunId(run_id))
        if state.parent_run_id is None and state.kind == "normal" and not state.finished:
            states.append(state)
    return states


class Loom:
    """Une configuration chargée, prête à faire tourner ses agents."""

    def __init__(
        self,
        config: LoomConfig,
        *,
        store: EventStore | None = None,
        registry: Registry | None = None,
        environ: Mapping[str, str] | None = None,
        artifacts: ArtifactStore | None = None,
        breakers: CircuitBreakers | None = None,
        secrets: SecretProvider | None = None,
        counter: UsageCounter | None = None,
    ) -> None:
        self._config = config
        # Identité de cette instance : c'est elle qui prend les concessions sur
        # les runs qu'elle pilote (#27), et qui signe ses nouvelles sur le bus
        # (5.3c) pour ne pas se réécouter.
        self._worker_id = f"worker-{new_id()[-12:]}"
        self._registry = registry if registry is not None else load_registry(config)
        self._agents = AgentRegistry.from_config(config)
        # Clients (L1) : résolus ici, pour qu'une surcharge incohérente soit
        # une erreur de démarrage et non une erreur au premier run du client.
        provider: SecretProvider = (
            secrets if secrets is not None else EnvironmentSecrets(config, environ)
        )
        self._tenants = Tenants(config, environ=environ, secrets=provider)
        # Sceau des contenus au repos (5.5b) : un trousseau quand la config le
        # déclare, et c'est lui qui donne son codec au journal et sa clé aux
        # fichiers. Un seul trousseau pour l'instance : les clés d'un client
        # sont résolues une fois, pas à chaque événement.
        self._keyring = create_keyring(config, provider)
        codec: JournalCodec = PLAIN if self._keyring is None else SealingCodec(self._keyring)
        shared = store if store is not None else create_event_store(config, codec=codec)
        self._shared_store = shared
        # Un journal fourni par l'appelant reste à lui de fermer.
        self._owns_store = store is None
        # Stockage des fichiers, commun aux agents ; même règle de fermeture.
        self._shared_artifacts = (
            artifacts
            if artifacts is not None
            else create_artifact_store(config, keyring=self._keyring)
        )
        self._owns_artifacts = artifacts is None
        # Isolation physique (#34) : un client qui déclare son propre stockage
        # a le sien, les autres partagent celui de la racine. Le routeur est
        # un journal comme un autre : le reste de l'instance ne le voit pas.
        self._router = TenantRouter(
            self._tenants,
            events=partial(create_event_store, codec=codec),
            artifacts=partial(create_artifact_store, keyring=self._keyring),
            shared_events=shared,
            shared_artifacts=self._shared_artifacts,
        )
        inner: EventStore = RoutedEventStore(self._router) if self._router.routed else shared
        self._artifacts: ArtifactStore = (
            RoutedArtifactStore(self._router) if self._router.routed else self._shared_artifacts
        )
        # Bus des nouvelles d'écriture (5.3c) : sans lui, un abonné ne voit
        # que ce que son propre process écrit. Un journal notifiant fourni par
        # l'appelant garde le sien, s'il en a un.
        self._bus = create_bus(config) if not isinstance(inner, NotifyingEventStore) else None
        self._store = (
            inner
            if isinstance(inner, NotifyingEventStore)
            else NotifyingEventStore(inner, bus=self._bus, source=self._worker_id)
        )
        # Tâche qui suit le bus, lancée à l'entrée du contexte.
        self._following: asyncio.Task[None] | None = None
        # Profil prod : ce qui avertit ailleurs refuse ici (M4, 5.5a).
        announce(config, storage_warnings(config))
        # Le sceau, lui, avertit dans tous les profils : un client dont la clé a
        # été effacée est un état voulu, pas une config à corriger, et refuser
        # de démarrer arrêterait le service de tous les autres (5.5b).
        announce(config, encryption_warnings(config, self._keyring), refuse=False)
        # Magasin d'idempotence partagé par les agents de l'instance (#49) ;
        # ``None`` quand chaque run se sert de son journal.
        self._idempotency = create_idempotency_store(config)
        self._environ = environ
        # Un agent monté par client : ses modèles, ses secrets et ses
        # connexions MCP ne sont pas ceux du voisin.
        self._built: dict[tuple[str, TenantId], Agent] = {}
        # Connexions MCP de portée shared, communes à tous les agents.
        self._mcp = create_mcp_pool(config, environ)
        # Disjoncteurs des modèles et des serveurs MCP, communs à tous les runs.
        self._breakers = breakers if breakers is not None else CircuitBreakers()
        # Consommation par client et par période (J5.1b) : le compteur est un
        # cache du journal, et c'est lui qui rend un budget de journée lisible
        # avant chaque lancement sans relire toutes les sessions du client.
        self._counter = counter if counter is not None else InMemoryUsageCounter()
        self._owns_counter = counter is None
        self._usage = TenantUsage(self._counter, self._store)
        # Débit accordé aux clients, commun aux runs de l'instance (L3).
        self._quota = Quota()
        # Un écrivain par session : deux runs d'une même session écrivent par
        # le même, et ne se refusent pas l'un l'autre (#22).
        self._writers = SessionWriters()
        # Runs pilotés ici, pour que ``cancel`` puisse les atteindre (A5).
        self._driving: dict[RunId, asyncio.Task[RunState]] = {}
        # Compaction : agent interne et file de tâches, seulement si la config
        # la déclare (#23).
        compaction = config.sessions.compaction
        self._compaction = (
            CompactionJob(
                self.context,
                self._store,
                self._writers,
                CompactionPlan(
                    agent=COMPACTION_AGENT,
                    over_tokens=compaction.over_tokens,
                    hard_tokens=compaction.hard_tokens,
                    keep_last=compaction.keep_last,
                    fidelity_check=compaction.fidelity_check,
                ),
            )
            if compaction is not None
            else None
        )
        handlers: dict[JobKind, Handler] = {
            "run": self._piloted_job,
            "resume": self._piloted_job,
            "expire_approval": self._piloted_job,
        }
        if self._compaction is not None:
            handlers["compaction"] = self._compacted
        # La file existe toujours depuis 4.2b : elle porte les runs de fond,
        # que la compaction soit configurée ou non. Servie par un courtier
        # (5.3b), elle ne fait que publier : les traitements ci-dessus ne
        # servent alors qu'aux workers, où la même instance les consomme.
        self._queue: TaskQueue = create_task_queue(config, handlers)

    @classmethod
    def from_config(
        cls,
        path: Path | str,
        *,
        environ: Mapping[str, str] | None = None,
        profile: str | None = None,
    ) -> Self:
        """Charge un fichier de configuration et ouvre l'instance.

        ``profile`` l'emporte sur ``LOOM_PROFILE`` et sur ``profile:`` du
        fichier (M4).
        """
        return cls(load_config(Path(path), profile=profile), environ=environ)

    # --- Ce que l'instance héberge -------------------------------------------

    @property
    def config(self) -> LoomConfig:
        return self._config

    @property
    def worker_id(self) -> str:
        """Identité que ce process inscrit dans la concession d'un run (#27).

        C'est elle qui départage deux pilotes au journal : un `run.claimed`
        la porte, et la lire dit qui tient le run — utile dès qu'il y a
        plusieurs workers (5.3b).
        """
        return self._worker_id

    @property
    def store(self) -> NotifyingEventStore:
        """Journal de l'instance, abonnable pendant qu'un run se déroule."""
        return self._store

    @property
    def artifacts(self) -> ArtifactStore:
        """Stockage des fichiers des runs de l'instance."""
        return self._artifacts

    @property
    def registry(self) -> Registry:
        return self._registry

    @property
    def agents(self) -> tuple[AgentSpec, ...]:
        return tuple(self._agents)

    @property
    def names(self) -> tuple[str, ...]:
        return self._agents.names

    def exposed(self, access: str, tenant_id: TenantId | None = None) -> tuple[AgentSpec, ...]:
        """Agents publiés par un point d'accès (``rest`` ou ``mcp``), pour un client."""
        published = self._agents.exposed("rest" if access == "rest" else "mcp")
        if tenant_id is None:
            # Personne n'est nommé : rien à filtrer, et surtout pas au nom
            # d'un client par défaut qui n'existe peut-être pas.
            return published
        allowed = self._tenants.get(tenant_id)
        return tuple(spec for spec in published if allowed.allows(spec.name))

    def register(self, name: str, obj: object) -> None:
        """Rend un objet Python référençable par son nom dans la config.

        À faire avant le premier run de l'agent qui s'en sert : les outils
        sont résolus au premier montage de l'agent, puis gardés.
        """
        self._registry.add(name, obj, source="register()")

    # --- Faire tourner un agent ----------------------------------------------

    @property
    def tenants(self) -> tuple[TenantId, ...]:
        """Clients de l'instance ; ``default`` seul quand la config n'en nomme aucun."""
        return self._tenants.ids

    def tenant(self, tenant_id: TenantId | None = None) -> Tenant:
        """Un client et ses surcharges ; lève ``UnknownTenant`` s'il n'est pas déclaré."""
        return self._tenants.get(tenant_id)

    def _for(
        self, agent: str, context: CallerContext | None, tenant: TenantId | None
    ) -> tuple[CallerContext, Tenant]:
        """Contexte appelant et client d'un lancement, l'agent vérifié ouvert à lui."""
        caller = context or CallerContext()
        if tenant is not None and tenant != caller.tenant_id:
            if context is not None and "tenant_id" in context.model_fields_set:
                raise ValueError(
                    f"Client contradictoire : tenant={tenant!r} et "
                    f"context.tenant_id={caller.tenant_id!r}"
                )
            caller = caller.model_copy(update={"tenant_id": tenant})
        found = self._tenants.get(caller.tenant_id)
        if not found.allows(agent):
            raise AgentNotAllowed(agent, found.id)
        return caller, found

    async def _admitted(
        self, agent: str, context: CallerContext | None, tenant: TenantId | None
    ) -> tuple[CallerContext, Tenant]:
        """Ce qu'il faut vérifier avant d'ouvrir un run, dans l'ordre du moins cher.

        Le client, puis l'agent qu'on lui ouvre, puis son débit (en mémoire),
        puis son budget de période (qui peut relire le journal). Rien n'est
        écrit tant que les quatre ne sont pas passés : un run refusé n'a pas
        existé.
        """
        caller, found = self._for(agent, context, tenant)
        self._quota.check(found.id, found.quotas.runs_per_minute)
        await self._usage.check(found)
        return caller, found

    def _judged(self, judges: JudgesMode) -> JudgesMode:
        """Ce que l'appelant demande aux juges, si le profil le permet (M4).

        ``skip`` retire un contrôle : c'est bon pour une mise au point, et
        c'est précisément ce qu'un profil ``prod`` refuse. Sans profil déclaré,
        rien ne change — la portée ``admin`` de l'accès REST reste la seule
        barrière, comme depuis 3.3.
        """
        if judges == "skip" and self.config.strict:
            raise ConfigError(
                "Profil prod : judges='skip' retire un contrôle et n'y est pas permis"
            )
        return judges

    async def run(
        self,
        agent: str,
        message: str | Message,
        *,
        attachments: Sequence[Attachment] = (),
        session_id: SessionId | None = None,
        context: CallerContext | None = None,
        run_id: RunId | None = None,
        on_chunk: ChunkCallback | None = None,
        judges: JudgesMode = "auto",
        approver: Approver | None = None,
        tenant: TenantId | None = None,
    ) -> RunResult:
        """Fait tourner un run jusqu'au bout et renvoie ce qu'il a produit.

        Une pièce jointe refusée (format, taille) lève ``AttachmentError``
        avant que le run ne commence. ``judges`` (#21) : ``auto``, chaque juge
        selon son ``when`` ; ``force``, tous (audit, évals) ; ``skip``, aucun.

        ``approver`` (#28) : un approbateur en ligne, appelé dans la boucle
        pour chaque appel qui demande une approbation. Le run ne passe alors
        jamais en pause — c'est le mode des scripts, de la CLI et des tests.
        Sans lui, l'approbation est asynchrone : le run s'arrête en ``PAUSED``
        et ``approve()`` le reprend.
        """
        caller, who = await self._admitted(agent, context, tenant)
        ctx = self.context(agent, who.id, on_chunk=on_chunk, approver=approver)
        state = await self._start(ctx, message, attachments, session_id, caller, run_id, judges)
        return await self._result(state)

    async def stream(
        self,
        agent: str,
        message: str | Message,
        *,
        attachments: Sequence[Attachment] = (),
        session_id: SessionId | None = None,
        context: CallerContext | None = None,
        run_id: RunId | None = None,
        subruns: bool = True,
        judges: JudgesMode = "auto",
        approver: Approver | None = None,
        tenant: TenantId | None = None,
    ) -> AsyncGenerator[StreamItem]:
        """Événements du journal et morceaux du modèle, dans l'ordre d'arrivée.

        Le run est lancé en tâche de fond ; abandonner l'itération l'annule.
        Son résultat se relit ensuite avec ``result(run_id)``. Avec
        ``subruns``, les événements des sous-runs sont mêlés au flux.
        ``judges`` et ``approver`` : comme pour ``run``.
        """
        run_id = run_id or new_run_id()
        items: asyncio.Queue[StreamItem | None] = asyncio.Queue()

        async def on_chunk(chunk: ModelChunk) -> None:
            items.put_nowait(chunk)

        caller, who = await self._admitted(agent, context, tenant)
        ctx = self.context(agent, who.id, on_chunk=on_chunk, approver=approver)
        tree = RunTree(run_id, subruns=subruns)
        # Écoute posée avant le démarrage : l'arbre se reconnaît dans l'ordre d'écriture.
        with self._store.listen(items.put_nowait, accept=tree.admit):
            task = asyncio.create_task(
                self._start(ctx, message, attachments, session_id, caller, run_id, judges)
            )
            task.add_done_callback(lambda _: items.put_nowait(None))
            try:
                while (item := await items.get()) is not None:
                    yield item
            finally:
                if not task.done():
                    task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
            # Le run est terminé : une erreur de la boucle ressort ici.
            await task

    async def resume(
        self,
        run_id: RunId,
        *,
        session_id: SessionId | None = None,
        tenant_id: TenantId | None = None,
        on_chunk: ChunkCallback | None = None,
    ) -> RunResult:
        """Reprend un run interrompu, là où son journal s'est arrêté."""
        state = await self.state(run_id, session_id=session_id, tenant_id=tenant_id)
        ctx = self.context(state.agent, state.context.tenant_id, on_chunk=on_chunk)
        writer = await self._writer(state.context.tenant_id, state.session_id)
        return await self._result(await self._piloted(ctx, state, writer))

    def context(
        self,
        agent: str,
        tenant_id: TenantId | None = None,
        *,
        on_chunk: ChunkCallback | None = None,
        approver: Approver | None = None,
    ) -> RunContext:
        """Contexte d'exécution d'un agent pour un client, monté au premier appel.

        Le client de modèle et les outils sont gardés d'un run à l'autre ; le
        contexte est gelé, ``on_chunk`` en donne donc une copie. Le montage a
        lieu **par client** (L1) : la configuration qu'il voit, ses secrets,
        ses outils et ses connexions MCP lui appartiennent.
        """
        tenant = self._tenants.get(tenant_id)
        key = (agent, tenant.id)
        built = self._built.get(key)
        if built is None:
            built = build_agent(
                tenant.config,
                agent,
                self._store,
                registry=self._registry,
                environ=self._environ,
                mcp_pool=self._mcp,
                artifacts=self._artifacts,
                idempotency=self._idempotency,
                agents=partial(self.context, tenant_id=tenant.id),
                breakers=self._breakers,
                tenant=tenant,
            )
            self._built[key] = built
        # La concession appartient à l'instance, pas à la config de l'agent.
        context = replace(
            built.context, worker_id=self._worker_id, lease=self._config.execution.lease
        )
        if on_chunk is None and approver is None:
            return context
        return replace(
            context,
            on_chunk=on_chunk or context.on_chunk,
            approver=approver or context.approver,
        )

    # --- Relire un run --------------------------------------------------------

    async def events(
        self,
        run_id: RunId,
        *,
        session_id: SessionId | None = None,
        tenant_id: TenantId | None = None,
        after_seq: int = 0,
        subruns: bool = True,
    ) -> list[Event]:
        """Événements d'un run dans l'ordre du journal, et ceux de ses sous-runs.

        ``subruns=False`` s'en tient au run. ``after_seq`` ne rend que la
        suite ; l'arbre, lui, se reconnaît depuis le début de la session.
        """
        tenant = tenant_id or DEFAULT_TENANT
        session = session_id or SessionId(run_id)
        if not subruns:
            own = RunTree(run_id, subruns=False)
            read = await self._store.read(tenant, session, after_seq=after_seq, run_id=run_id)
            return own.select(read)
        tree = RunTree(run_id)
        return [
            event
            for event in tree.select(await self._store.read(tenant, session))
            if event.seq > after_seq
        ]

    async def follow(
        self,
        run_id: RunId,
        *,
        session_id: SessionId | None = None,
        tenant_id: TenantId | None = None,
        after_seq: int = 0,
        subruns: bool = True,
    ) -> AsyncGenerator[Event]:
        """Événements déjà écrits, puis les suivants si le run tourne encore.

        Avec ``subruns``, ceux de ses sous-runs suivent aussi. L'itération
        s'arrête sur la clôture du run demandé, même si elle précède
        ``after_seq``. Un run inconnu lève ``UnknownRun``.
        """
        tenant = tenant_id or DEFAULT_TENANT
        session = session_id or SessionId(run_id)
        tree = RunTree(run_id, subruns=subruns)

        def in_session(event: Event) -> bool:
            return event.tenant_id == tenant and event.session_id == session

        # Abonnement posé avant la lecture : rien ne se perd entre les deux. Le
        # filtre de l'arbre s'applique ici, dans l'ordre du journal.
        async with self._store.subscribe(accept=in_session) as live:
            written = await self._store.read(tenant, session)
            if not any(event.run_id == run_id for event in written):
                raise UnknownRun(run_id)
            seen = 0
            for event in written:
                seen = event.seq
                if not tree.admit(event):
                    continue
                if event.seq > after_seq:
                    yield event
                if _closes(event, run_id):
                    return
            async for event in live:
                if event.seq <= seen or not tree.admit(event):
                    continue
                yield event
                if _closes(event, run_id):
                    return

    async def state(
        self,
        run_id: RunId,
        *,
        session_id: SessionId | None = None,
        tenant_id: TenantId | None = None,
    ) -> RunState:
        """État d'un run, reconstruit depuis son journal."""
        events = await self.events(
            run_id, session_id=session_id, tenant_id=tenant_id, subruns=False
        )
        if not events:
            raise UnknownRun(run_id)
        return fold(events, run_id)

    async def result(
        self,
        run_id: RunId,
        *,
        session_id: SessionId | None = None,
        tenant_id: TenantId | None = None,
    ) -> RunResult:
        """Ce qu'un run a produit, relu depuis son journal : réponse, coûts, verdicts."""
        tenant = tenant_id or DEFAULT_TENANT
        events = await self._store.read(tenant, session_id or SessionId(run_id))
        if not any(event.run_id == run_id for event in events):
            raise UnknownRun(run_id)
        return RunResult.of(fold(events, run_id), events)

    async def consumption(
        self,
        tenant_id: TenantId | None = None,
        *,
        period: BudgetPeriod = "day",
    ) -> TenantConsumption:
        """Ce qu'un client a dépensé sur la journée ou le mois en cours (L3, J5.1b).

        Relu dans le journal à chaque appel, et non pris au compteur : c'est
        une question qu'un humain pose, et elle doit valoir même pour un client
        sans budget — dont le compteur ne retient rien.
        """
        tenant = self._tenants.get(tenant_id)
        return await self._usage.consumption(tenant.id, tenant.budget, period)

    async def report(
        self,
        run_id: RunId | None = None,
        *,
        session_id: SessionId | None = None,
        tenant_id: TenantId | None = None,
    ) -> UsageReport:
        """Rapport de consommation (J5) : d'un run et de ses sous-runs, ou de toute une session.

        Sans ``run_id``, ``session_id`` est requis.
        """
        if run_id is None and session_id is None:
            raise ValueError("report() demande un run_id ou un session_id")
        tenant = tenant_id or DEFAULT_TENANT
        session = session_id or SessionId(str(run_id))
        events = await self._store.read(tenant, session)
        if run_id is not None and not any(e.run_id == run_id for e in events):
            raise UnknownRun(run_id)
        return usage_report(events, session, run_id)

    async def artifact(self, uri: str) -> bytes:
        """Octets d'un fichier du stockage ; lève ``ArtifactNotFound``."""
        return await self._artifacts.get(uri)

    # --- Sessions (F7) --------------------------------------------------------

    async def sessions(self, *, tenant_id: TenantId | None = None) -> list[SessionRecord]:
        """Sessions du client, de la plus récemment écrite à la plus ancienne."""
        return await self._store.sessions(tenant_id or DEFAULT_TENANT)

    async def runs(
        self,
        *,
        tenant_id: TenantId | None = None,
        agent: str | None = None,
        status: Sequence[RunStatus] = (),
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = RUNS_LIMIT,
        sessions: int = SESSIONS_READ,
    ) -> RunPage:
        """Runs du client, du plus récent au plus ancien (K5, #32).

        Lus **au journal**, session par session, de la plus récemment écrite à
        la plus ancienne : les chiffres sont exactement ceux de la fiche d'une
        session, et il n'y a aucune projection à tenir à jour, à reconstruire
        ou à resynchroniser. Le prix est une lecture par session ouverte, et
        deux bornes le tiennent : ``limit`` runs rendus au plus, ``sessions``
        journaux ouverts au plus. Quand une borne arrête la recherche, la page
        le dit (``truncated``) — il peut y avoir d'autres runs derrière.

        ``since`` et ``until`` se comparent à la **dernière** écriture du run.
        Un sous-run est listé comme les autres, en nommant son délégant.
        """
        tenant = tenant_id or DEFAULT_TENANT
        known = await self._store.sessions(tenant)
        found: list[RunListed] = []
        scanned = 0
        truncated = False
        for record in known:
            if scanned >= sessions or len(found) >= limit:
                truncated = True
                break
            scanned += 1
            events = await self._store.read(tenant, record.session_id)
            for own in _by_run(events).values():
                listed = RunListed.of(fold(own, own[0].run_id), own)
                if _keeps(listed, agent=agent, status=status, since=since, until=until):
                    found.append(listed)
        found.sort(key=lambda run: run.updated_at, reverse=True)
        if len(found) > limit:
            found, truncated = found[:limit], True
        return RunPage(runs=tuple(found), scanned=scanned, truncated=truncated)

    async def query(self, query: EventQuery) -> list[Event]:
        """Événements du journal qui satisfont ces critères (#32, K5).

        C'est la lecture des traces : le journal **est** la trace, et
        ``EventQuery`` est la façon de l'interroger. L'ordre est celui des
        identifiants d'événement — donc celui du temps —, et ``after`` reprend
        la pagination où elle s'était arrêtée.
        """
        return await self._store.query(query)

    async def session(
        self, session_id: SessionId, *, tenant_id: TenantId | None = None
    ) -> SessionInfo:
        """Fiche d'une session : ses runs, et ce qu'il faut trancher pour qu'elle avance.

        Le journal est lu une fois et replié run par run. Une session inconnue
        lève ``UnknownSession`` — un journal vide n'existe pas.
        """
        events = await self._store.read(tenant_id or DEFAULT_TENANT, session_id)
        if not events:
            raise UnknownSession(session_id)
        states = _run_states(events)
        return SessionInfo(
            session_id=session_id,
            last_seq=events[-1].seq,
            updated_at=events[-1].ts,
            runs=tuple(RunSummary.of(state) for state in states),
            pending_approvals=tuple(approval for state in states for approval in state.awaiting),
        )

    async def export_session(
        self, session_id: SessionId, *, tenant_id: TenantId | None = None
    ) -> list[Event]:
        """Tous les événements d'une session, dans l'ordre du journal.

        Les fichiers n'y sont pas : chaque événement porte leur URI, et
        ``artifact(uri)`` en rend les octets.
        """
        events = await self._store.read(tenant_id or DEFAULT_TENANT, session_id)
        if not events:
            raise UnknownSession(session_id)
        return events

    async def delete_session(
        self, session_id: SessionId, *, tenant_id: TenantId | None = None
    ) -> SessionDeletion:
        """Supprime physiquement une session : ses clés, ses fichiers, son journal (RGPD).

        Le journal part en dernier : tant qu'il est là, on sait ce qu'il reste
        à retirer. Les clés d'idempotence partent avec lui — une clé métier
        peut porter une référence client (« relance:D-2026-042 »), et l'effet
        qu'elle protégeait n'a plus de trace de toute façon.
        """
        tenant = tenant_id or DEFAULT_TENANT
        keys = 0
        if self._idempotency is not None:
            keys = await self._idempotency.forget(tenant, session_id)
        artifacts = await self._artifacts.delete(tenant, session_id)
        events = await self._store.delete(tenant, session_id)
        self._writers.forget(tenant, session_id)
        return SessionDeletion(session_id=session_id, events=events, artifacts=artifacts, keys=keys)

    async def apply_retention(
        self,
        *,
        tenant_id: TenantId | None = None,
        dry_run: bool = True,
        now: datetime | None = None,
    ) -> RetentionReport:
        """Efface les sessions dormantes au-delà de la borne (`storage.retention`, 5.5c).

        Une session est choisie sur sa **dernière écriture**, et sur rien
        d'autre : aucun contenu n'est lu, si bien qu'un journal scellé dont la
        clé a disparu s'efface aussi — c'est là que la place serait perdue pour
        de bon. Conséquence assumée : une session qui portait un run en pause
        part comme les autres si elle est restée muette plus longtemps que la
        borne.

        ``dry_run`` est le défaut : le rapport dit ce qui partirait, et rien
        n'est supprimé. Les fichiers et les clés d'une session ne se comptent
        qu'en la supprimant, donc ils restent inconnus (``None``) en essai à
        blanc ; le nombre d'événements, lui, est la longueur du journal, que la
        marque de la session donne sans rien ouvrir.

        Rien ne se déclenche tout seul : c'est `loom retention` qui appelle, et
        la plateforme qui le met à l'heure (5.4c).
        """
        moment = now or datetime.now(UTC)
        tenants = (tenant_id,) if tenant_id is not None else self._config.tenant_ids
        days = {tenant: self._config.retention_days(tenant) for tenant in tenants}
        scanned = 0
        kept = 0
        swept: list[SessionSwept] = []
        for tenant, bound in days.items():
            if bound is None:
                continue
            limit = moment - timedelta(days=bound)
            for record in await self._store.sessions(tenant):
                scanned += 1
                if record.updated_at >= limit:
                    kept += 1
                    continue
                swept.append(await self._sweep(tenant, record, dry_run=dry_run))
        return RetentionReport(
            dry_run=dry_run, days=days, scanned=scanned, kept=kept, swept=tuple(swept)
        )

    async def _sweep(
        self, tenant: TenantId, record: SessionRecord, *, dry_run: bool
    ) -> SessionSwept:
        found = SessionSwept(
            tenant_id=tenant,
            session_id=record.session_id,
            last_write=record.updated_at,
            events=record.last_seq,
        )
        if dry_run:
            return found
        removed = await self.delete_session(record.session_id, tenant_id=tenant)
        return found.model_copy(
            update={
                "events": removed.events,
                "artifacts": removed.artifacts,
                "keys": removed.keys,
            }
        )

    # --- Cycle de vie ---------------------------------------------------------

    async def aclose(self) -> None:
        """Ferme les clients de modèle, les connexions MCP, et les stockages venus de la config."""
        # Les tâches de fond se servent des agents : on les attend d'abord.
        await self._queue.aclose()
        for built in self._built.values():
            await built.aclose()
        if self._mcp is not None:
            await self._mcp.aclose()
        self._built.clear()
        self._writers.clear()
        # Les stockages créés pour un client appartiennent au routeur, quoi
        # qu'il advienne de celui de la racine.
        await self._router.aclose()
        if self._owns_store:
            await self._shared_store.aclose()
        if self._owns_artifacts:
            await self._shared_artifacts.aclose()
        # Le bus d'abord : sa fermeture met fin au suivi, qu'on attend ensuite.
        if self._bus is not None:
            await self._bus.aclose()
        following, self._following = self._following, None
        if following is not None:
            await following
        closing = getattr(self._idempotency, "aclose", None)
        if closing is not None:
            await closing()
        if self._owns_counter:
            await self._counter.aclose()

    async def __aenter__(self) -> Self:
        """Ouvre l'instance, et met une oreille sur le bus s'il y en a un.

        Le suivi du bus demande une boucle : il ne peut pas démarrer à la
        construction. Une instance utilisée sans ``async with`` écrit et lit
        normalement, mais ne verra pas ce que les autres process écrivent.
        """
        if self._bus is not None and self._following is None:
            self._following = asyncio.create_task(self._store.follow(), name="loom-bus")
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def _piloted(
        self, ctx: RunContext, state: RunState, writer: SessionWriter | None
    ) -> RunState:
        """Pilote un run dans une tâche suivie, que ``cancel`` sait retrouver.

        Si l'appelant est annulé, la tâche l'est aussi : elle n'écrit rien de
        plus et le run reste reprenable. Seul ``cancel`` écrit ``run.cancelled``.
        """
        task = asyncio.create_task(
            drive(
                ctx,
                state.run_id,
                session_id=state.session_id,
                tenant_id=state.context.tenant_id,
                writer=writer,
            )
        )
        self._driving[state.run_id] = task
        try:
            return await task
        finally:
            self._driving.pop(state.run_id, None)
            if not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task

    async def cancel(
        self,
        run_id: RunId,
        *,
        session_id: SessionId | None = None,
        tenant_id: TenantId | None = None,
        by: str | None = None,
    ) -> bool:
        """Arrête un run et écrit ``run.cancelled`` (A5) ; faux s'il est déjà fini.

        Le run piloté ici est d'abord interrompu, puis clos au journal. Un run
        que cette instance ne pilote pas — repris ailleurs, ou laissé en plan
        par un plantage — est clos directement.

        Un run annulé est **terminal** : il ne se reprend pas. Un run seulement
        interrompu, lui, ne laisse rien au journal et repart où il en était.
        """
        task = self._driving.get(run_id)
        if task is not None and not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        state = await self.state(run_id, session_id=session_id, tenant_id=tenant_id)
        if state.finished:
            return False
        # ``state.session_id`` vaut le run_id pour un run anonyme : l'écrivain
        # partagé de l'instance vaut donc dans les deux cas.
        writer = await self._writers.open(self._store, state.context.tenant_id, state.session_id)
        await writer.append(cancellation(state, by=by))
        return True

    async def approve(
        self,
        run_id: RunId,
        *,
        call_id: str | None = None,
        by: str | None = None,
        reason: str = "",
        arguments: dict[str, JsonValue] | None = None,
        session_id: SessionId | None = None,
        tenant_id: TenantId | None = None,
    ) -> tuple[str, ...]:
        """Autorise un appel que le run attend, et remet son pilotage en file (#17).

        Sans ``call_id``, toutes les demandes en attente sont accordées —
        le cas courant, un seul appel à valider. ``arguments`` corrige ceux de
        l'appel, et ne vaut que pour un ``call_id`` désigné. ``by`` est
        l'identité de l'approbateur : c'est tout l'audit qu'il y aura.

        Rend les appels accordés ; vide si le run n'attendait rien.
        """
        return await self._decided(
            run_id,
            call_id,
            session_id=session_id,
            tenant_id=tenant_id,
            payload=lambda asked: ApprovalGranted(
                call_id=asked.call_id,
                tool_name=asked.tool_name,
                by=by,
                reason=reason,
                arguments=arguments if call_id is not None else None,
            ),
        )

    async def reject(
        self,
        run_id: RunId,
        *,
        call_id: str | None = None,
        by: str | None = None,
        reason: str = "",
        session_id: SessionId | None = None,
        tenant_id: TenantId | None = None,
    ) -> tuple[str, ...]:
        """Refuse un appel que le run attend (#17) : le motif revient au modèle.

        Le run n'échoue pas : l'appel rend une erreur, et l'orchestrateur fait
        ce qu'il peut de ce refus.
        """
        return await self._decided(
            run_id,
            call_id,
            session_id=session_id,
            tenant_id=tenant_id,
            payload=lambda asked: ApprovalRejected(
                call_id=asked.call_id, tool_name=asked.tool_name, by=by, reason=reason
            ),
        )

    async def _decided(
        self,
        run_id: RunId,
        call_id: str | None,
        *,
        session_id: SessionId | None,
        tenant_id: TenantId | None,
        payload: Callable[[PendingApproval], ApprovalGranted | ApprovalRejected],
    ) -> tuple[str, ...]:
        """Écrit une décision d'approbation, puis remet la racine en file.

        La demande est cherchée dans tout l'arbre du run : on tranche là où on
        a lu, et c'est toujours la racine qui repart — c'est elle qui rejouera
        l'appel menant au sous-run qui attend.
        """
        tenant = tenant_id or DEFAULT_TENANT
        events = await self._store.read(tenant, session_id or SessionId(run_id))
        tree = RunTree(run_id).select(events)
        if not tree:
            raise UnknownRun(run_id)
        root = fold([e for e in tree if e.run_id == run_id], run_id)
        if root.finished and not _awaited(root, tree):
            return ()
        decided: list[tuple[RunState, PendingApproval]] = []
        for owner in _awaiting_runs(tree):
            decided += [
                (owner, a) for a in owner.awaiting if call_id is None or a.call_id == call_id
            ]
        if call_id is not None and not decided:
            raise UnknownApproval(run_id, call_id)
        if not decided:
            return ()
        writer = await self._writers.open(self._store, tenant, root.session_id)
        drafts: list[EventDraft] = []
        for owner, asked in decided:
            span, parent = _where(tree, owner.run_id, asked.call_id)
            drafts.append(
                run_scope(owner).draft(payload(asked), span_id=span, parent_span_id=parent)
            )
        await writer.append(drafts)
        await self._queue.submit(
            Job(
                kind="resume",
                tenant_id=tenant,
                session_id=root.session_id,
                run_id=root.root_run_id,
            ),
            key=f"run:{root.root_run_id}",
        )
        return tuple(asked.call_id for _, asked in decided)

    async def submit(
        self,
        agent: str,
        message: str | Message,
        *,
        attachments: Sequence[Attachment] = (),
        session_id: SessionId | None = None,
        context: CallerContext | None = None,
        run_id: RunId | None = None,
        judges: JudgesMode = "auto",
        tenant: TenantId | None = None,
        trigger: str | None = None,
    ) -> RunId:
        """Ouvre un run et met son pilotage en file ; rend son identifiant (H5).

        Le run est **inscrit au journal avant le retour** : l'identifiant rendu
        désigne un run qui existe, qu'on peut suivre (``follow``), interroger
        (``state``) ou arrêter (``cancel``) aussitôt. Son résultat se relit
        ensuite avec ``result(run_id)``, ou s'attend avec ``drain()``.

        Une pièce jointe refusée lève ``AttachmentError`` avant l'ouverture.
        """
        caller, who = await self._admitted(agent, context, tenant)
        ctx = self.context(agent, who.id)
        if self._compaction is not None and session_id is not None:
            await self._compaction.ensure_fits(who.id, session_id)
        state = await begin_run(
            ctx,
            message,
            attachments=attachments,
            session_id=session_id,
            context=caller,
            run_id=run_id,
            judges=self._judged(judges),
            trigger=trigger,
            writer=await self._writer(who.id, session_id),
        )
        await self._queue.submit(
            Job(
                kind="run",
                tenant_id=who.id,
                session_id=state.session_id,
                run_id=state.run_id,
            ),
            key=f"run:{state.run_id}",
        )
        return state.run_id

    # --- Déclencheurs (H6, J5.4c) ---------------------------------------------

    @property
    def triggers(self) -> tuple[TriggerSpec, ...]:
        """Portes d'entrée déclarées par la configuration."""
        return self.config.triggers

    def trigger_spec(self, name: str) -> TriggerSpec:
        """Un déclencheur par son nom ; lève ``UnknownTrigger``."""
        for spec in self.config.triggers:
            if spec.name == name:
                return spec
        raise UnknownTrigger(name, [spec.name for spec in self.config.triggers])

    async def trigger(
        self,
        name: str,
        payload: JsonValue = None,
        *,
        delivery_id: str | None = None,
        tenant_id: TenantId | None = None,
    ) -> Triggered:
        """Ouvre le run d'un déclencheur, depuis la charge reçue (H6).

        Le message est rendu depuis ``{{ payload.… }}``, plus ``{{ trigger }}``
        et ``{{ delivery }}``. Une variable absente donne une chaîne vide : une
        charge incomplète fait un message plus pauvre, pas une livraison
        perdue.

        ``delivery_id`` devient le ``run_id`` : une **seconde livraison de la
        même chose retrouve son run** au lieu d'en ouvrir un autre
        (``repeated``). C'est ce qui rend une porte tenable — les plateformes
        réessaient, et une relance ne doit partir qu'une fois.

        Le run est lancé en arrière-plan : celui qui livre attend un accusé,
        pas une réponse de modèle.
        """
        spec = self.trigger_spec(name)
        tenant = tenant_id or DEFAULT_TENANT
        values: dict[str, JsonValue] = {
            "payload": payload,
            "trigger": name,
            "delivery": delivery_id or "",
        }
        run_id = RunId(_usable("Identifiant de livraison", delivery_id)) if delivery_id else None
        session_id = None
        if spec.session is not None:
            rendered = Template.parse(spec.session).render(values)
            session_id = SessionId(_usable(f"Session du déclencheur {name!r}", rendered))
        if run_id is not None:
            seen = await self._already(name, run_id, session_id, tenant)
            if seen is not None:
                return seen
        message = Template.parse(spec.message).render(values)
        opened = await self.submit(
            spec.agent,
            message,
            session_id=session_id,
            context=CallerContext(tenant_id=tenant, metadata={"trigger": name}),
            run_id=run_id,
            tenant=tenant,
            trigger=name,
        )
        state = await self.state(opened, session_id=session_id, tenant_id=tenant)
        return Triggered(
            trigger=name, run_id=opened, session_id=state.session_id, status=state.status
        )

    async def _already(
        self, name: str, run_id: RunId, session_id: SessionId | None, tenant: TenantId
    ) -> Triggered | None:
        """Le run de cette livraison s'il existe déjà, sinon rien."""
        try:
            state = await self.state(run_id, session_id=session_id, tenant_id=tenant)
        except UnknownRun:
            return None
        return Triggered(
            trigger=name,
            run_id=run_id,
            session_id=state.session_id,
            status=state.status,
            repeated=True,
        )

    async def recover(
        self, *, session_id: SessionId | None = None, tenant_id: TenantId | None = None
    ) -> tuple[RunId, ...]:
        """Remet en file les runs racine laissés en plan, et rend leurs identifiants (H3).

        À appeler soi-même : une instance ne redémarre pas les runs d'un autre
        process à l'insu de son appelant. Un run déjà piloté par un worker
        vivant sera refusé par sa concession, donc le remettre en file est sans
        risque. Un run dont l'agent n'est plus déclaré est ignoré, avec un
        avertissement.

        Sans ``session_id``, toutes les sessions du locataire sont balayées —
        c'est la reprise au démarrage d'un process. Avec, une seule l'est.
        """
        tenant = tenant_id or DEFAULT_TENANT
        known = set(self.names)
        found: list[RunId] = []
        sessions = (
            [session_id]
            if session_id is not None
            else [record.session_id for record in await self.store.sessions(tenant)]
        )
        for session in sessions:
            events = await self._store.read(tenant, session)
            for state in _unfinished(events):
                if state.agent not in known:
                    logger.warning(
                        "Reprise : run %s ignoré, agent %r absent de la configuration",
                        state.run_id,
                        state.agent,
                    )
                    continue
                await self._queue.submit(
                    Job(
                        kind="run",
                        tenant_id=tenant,
                        session_id=state.session_id,
                        run_id=state.run_id,
                    ),
                    key=f"run:{state.run_id}",
                )
                found.append(state.run_id)
        return tuple(found)

    async def _piloted_job(self, job: Job) -> None:
        """Pilote un run mis en file : soumission (H5) ou reprise (H3)."""
        assert job.run_id is not None, "un travail `run` désigne toujours un run"
        try:
            state = await self.state(job.run_id, session_id=job.session_id, tenant_id=job.tenant_id)
        except UnknownRun:
            logger.warning("Travail `run` : run %s introuvable", job.run_id)
            return
        if state.finished:
            return
        ctx = self.context(state.agent, state.context.tenant_id)
        writer = await self._writer(state.context.tenant_id, state.session_id)
        try:
            final = await self._piloted(ctx, state, writer)
        except ClaimConflict as conflict:
            # Un autre pilote le tient : c'est le but de la concession.
            logger.info("%s", conflict)
            await self._after_the_lease(job, conflict)
            return
        # Un run de fond a droit au même traitement qu'un run appelé en direct :
        # snapshot d'historique, compaction en file, réveil d'une approbation.
        await self._result(final)

    async def _after_the_lease(self, job: Job, conflict: ClaimConflict) -> None:
        """Repose le travail pour la fin du bail, quand la file est servie (H6).

        Avec une file en mémoire, le pilote qui tient le run est dans ce
        process : il le mènera au bout, et il n'y a rien à reprogrammer.

        Avec un courtier, c'est autre chose. Un worker tué n'acquitte pas son
        travail, donc le courtier le redélivre **tout de suite** — mais le bail
        du mort court encore, et personne ne le renouvellera. Le second pilote
        arrive donc trop tôt, et s'il en restait là le run attendrait la
        prochaine reprise au démarrage. On repose le travail pour l'après-bail,
        et c'est ainsi qu'un run passe d'un worker à l'autre sans qu'on s'en
        occupe.

        Si le bail appartient à un pilote bien vivant, la même chose se répète
        au rythme des renouvellements : c'est une surveillance, et elle
        s'arrête d'elle-même quand le run se termine. Bornée quand même — au
        bout de ``MAX_CLAIM_WAITS`` attentes, on laisse le run au journal.
        """
        if not self._config.storage.queue.brokered:
            return
        waited = job.params.get("claim_waits")
        waits = int(waited) + 1 if isinstance(waited, int) else 1
        if waits > MAX_CLAIM_WAITS:
            logger.warning(
                "Run %s : bail tenu par %s après %d attentes, travail abandonné — "
                "le run reste au journal, une reprise le retrouvera",
                job.run_id,
                conflict.worker_id,
                MAX_CLAIM_WAITS,
            )
            return
        left = (conflict.lease_until - datetime.now(UTC)).total_seconds()
        await self._queue.submit(
            replace(job, params={**job.params, "claim_waits": waits}),
            key=f"run:{job.run_id}",
            delay=max(left, 0.0) + CLAIM_MARGIN,
        )

    async def _result(self, state: RunState) -> RunResult:
        """Résultat d'un run qui vient de s'arrêter, avec son rapport et ses verdicts."""
        events = await self._store.read(state.context.tenant_id, state.session_id)
        await self._snapshot(state, events)
        await self._schedule(state, events)
        await self._expiring(state, events)
        result = RunResult.of(state, events)
        await self._counted(state, result)
        return result

    async def _counted(self, state: RunState, result: RunResult) -> None:
        """Porte au compteur du client ce que ce run a coûté, sous-runs compris.

        Seulement pour un run racine : la consommation d'un sous-agent est déjà
        dans le total de son parent. Le compteur pose une valeur par run, donc
        repasser ici pour le même run — une reprise, par exemple — ne double
        rien. Un compteur qui refuse n'a pas à faire échouer un run terminé.
        """
        if state.parent_run_id is not None:
            return
        if result.report is None:
            return
        total = result.report.total
        try:
            tenant = self._tenants.get(state.context.tenant_id)
        except UnknownTenant:
            # Un client retiré de la config depuis le lancement du run.
            return
        try:
            await self._usage.record(
                tenant, state.run_id, Spent(total.usage, total.cost, total.calls)
            )
        except Exception:
            logger.warning("Consommation du run %s non enregistrée", state.run_id, exc_info=True)

    async def compact(
        self, session_id: SessionId, *, tenant_id: TenantId | None = None
    ) -> SessionCompacted | None:
        """Résume maintenant les échanges anciens d'une session (#23).

        Ne tient pas compte du seuil ``over_tokens`` : c'est la compaction à
        la demande. Rend ``None`` si la session n'a rien de neuf à résumer, ou
        si la configuration ne déclare pas de compaction.
        """
        if self._compaction is None:
            return None
        return await self._compaction.compact(tenant_id or DEFAULT_TENANT, session_id, limit=0)

    async def drain(self) -> None:
        """Attend les tâches de fond en cours : runs soumis, résumés."""
        await self._queue.drain()

    # --- Worker (5.3b) --------------------------------------------------------

    async def work(self, *, jobs: int = 1) -> None:
        """Consomme la file jusqu'à l'arrêt : ce que fait ``loom worker`` (H6).

        Ne rend la main qu'une fois ``stop_work`` demandé **et** les tâches en
        cours menées au bout. Une file qui exécute chez elle n'a rien à
        consommer : la demande est alors refusée en nommant ce qu'il faut
        déclarer.
        """
        queue = self._served()
        logger.info("Worker en écoute, %d tâche(s) de front", jobs)
        await queue.serve(jobs=jobs)

    async def stop_work(self) -> None:
        """Demande au worker de s'arrêter : plus de tâche prise, celles en cours finissent."""
        await self._served().stop()

    def _served(self) -> ServedQueue:
        queue = self._queue
        if not isinstance(queue, ServedQueue):
            raise ConfigError(
                "Un worker demande une file servie par un courtier "
                f"('storage.queue.backend: rabbitmq'), pas {self._config.storage.queue.backend!r} "
                "— qui exécute déjà ses tâches dans le process qui les met en file"
            )
        return queue

    async def _compacted(self, job: Job) -> None:
        """Tâche de compaction, sortie de la file."""
        if self._compaction is not None:
            await self._compaction.compact(job.tenant_id, job.session_id, triggered_by=job.run_id)

    async def _schedule(self, state: RunState, events: Sequence[Event]) -> None:
        """Met un résumé en file si la session a dépassé son seuil (#23)."""
        if self._compaction is None or not state.finished:
            return
        if state.parent_run_id is not None or state.kind != "normal":
            return
        up_to_seq = self._compaction.pending(events)
        if up_to_seq is None:
            return
        await self._queue.submit(
            Job(
                kind="compaction",
                tenant_id=state.context.tenant_id,
                session_id=state.session_id,
                run_id=state.run_id,
            ),
            key=self._compaction.key(state.session_id, up_to_seq),
        )

    async def _expiring(self, state: RunState, events: Sequence[Event]) -> None:
        """Ramène un run en attente à l'échéance de sa première demande (#17).

        Le travail ne décide rien : c'est ``expire_at``, au journal, qui fait
        expirer une demande, et ``drive`` le lit en reprenant. Le travail ne
        fait qu'y revenir au bon moment, pour que personne n'ait à repasser.
        Différé, il ne retient ni ``drain()`` ni la fermeture : s'il est
        abandonné, la demande n'en est pas moins périmée.

        Les échéances sont cherchées dans tout l'arbre : quand c'est un
        sous-agent qui attend, la racine n'a pas de demande à elle, et
        personne ne serait venu la réveiller.
        """
        tree = RunTree(state.run_id).select(events)
        deadlines = [a.expire_at for a in _awaited(state, tree) if a.expire_at is not None]
        if not deadlines:
            return
        await self._queue.submit(
            Job(
                kind="expire_approval",
                tenant_id=state.context.tenant_id,
                session_id=state.session_id,
                run_id=state.run_id,
            ),
            key=f"expire:{state.run_id}",
            delay=max((min(deadlines) - datetime.now(UTC)).total_seconds(), 0.0),
        )

    async def _snapshot(self, state: RunState, events: Sequence[Event]) -> None:
        """Matérialise l'historique de la session, si le gain le justifie (§11.2).

        Le snapshot n'est qu'une vue : s'il ne peut pas être écrit, le run
        reste juste et la session se relit entièrement.
        """
        if state.parent_run_id is not None or not state.finished:
            return
        if state.session_id == SessionId(state.run_id):
            # Run sans session nommée : personne ne relira ce journal.
            return
        try:
            writer = await self._writers.open(
                self._store, state.context.tenant_id, state.session_id
            )
            await write_snapshot(writer, events, state, every=self._config.sessions.snapshot_every)
        except Exception:
            logger.warning(
                "Session %s : snapshot d'historique non écrit", state.session_id, exc_info=True
            )

    async def _writer(
        self, tenant_id: TenantId, session_id: SessionId | None
    ) -> SessionWriter | None:
        """Écrivain partagé d'une session nommée ; None pour un run anonyme."""
        if session_id is None:
            return None
        return await self._writers.open(self._store, tenant_id, session_id)

    async def _start(
        self,
        ctx: RunContext,
        message: str | Message,
        attachments: Sequence[Attachment],
        session_id: SessionId | None,
        context: CallerContext | None,
        run_id: RunId | None,
        judges: JudgesMode = "auto",
    ) -> RunState:
        tenant = (context or CallerContext()).tenant_id
        if self._compaction is not None and session_id is not None:
            # Filet de sécurité : une session trop longue est résumée avant
            # que le run ne commence (#23).
            await self._compaction.ensure_fits(tenant, session_id)
        writer = await self._writer(tenant, session_id)
        state = await begin_run(
            ctx,
            message,
            attachments=attachments,
            session_id=session_id,
            context=context,
            run_id=run_id,
            judges=self._judged(judges),
            writer=writer,
        )
        return await self._piloted(ctx, state, writer)


def _closes(event: Event, run_id: RunId) -> bool:
    """Vrai sur la clôture du run suivi (celle d'un sous-run ne compte pas)."""
    return event.run_id == run_id and is_final(event)
