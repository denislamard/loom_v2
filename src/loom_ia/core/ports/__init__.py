# SPDX-License-Identifier: Apache-2.0
"""Ports : les contrats entre le moteur et l'extérieur, que remplissent les adaptateurs.

Le moteur ne connaît que ces contrats ; ``loom_ia.adapters`` les remplit, et
``runtime/wiring.py`` choisit l'adaptateur selon la config.

- ``event_store`` : le journal (``EventStore``) — mémoire, JSONL, SQLite,
  Postgres (``storage.events``) ;
- ``bus`` : les nouvelles d'écriture entre process (``EventBus``) — mémoire,
  Postgres, Redis (``storage.bus``) ;
- ``queue`` : la file des tâches de fond (``TaskQueue``) — asyncio, RabbitMQ
  (``storage.queue``) ;
- ``artifact_store`` : les fichiers hors journal (``ArtifactStore``) — mémoire,
  dossier local (``storage.artifacts``) ;
- ``idempotency`` : la mémoire des effets déjà produits
  (``IdempotencyStore``) — journal, mémoire, SQLite, Postgres, Redis
  (``storage.idempotency``) ;
- ``cipher`` : le sceau des contenus au repos (``Cipher``, ``Keyring``) —
  AES-256-GCM (``storage.encryption``) ;
- ``model_client`` : l'appel d'un modèle (``ModelClient``) — Anthropic,
  OpenAI (chat et responses), modèle simulé ;
- ``tool`` : l'outil (``Tool``) et la source d'outils (``ToolSource``) ;
- ``policy`` : la politique branchée aux points d'accroche (``Policy``) ;
- ``secrets`` : la table des secrets d'un client (``SecretProvider``) ;
- ``usage_counter`` : la consommation d'un client par période
  (``UsageCounter``) ;
- ``exchanges`` : l'enregistrement des échanges HTTP bruts avec un
  fournisseur.
"""

from loom_ia.core.ports.artifact_store import ArtifactNotFound, ArtifactStore
from loom_ia.core.ports.bus import BusUnavailable, EventBus, Notice
from loom_ia.core.ports.cipher import (
    Cipher,
    Keyring,
    MissingKey,
    SealBroken,
    SealError,
)
from loom_ia.core.ports.event_store import (
    EventStore,
    JournalBusy,
    JournalCorrupted,
    SequenceConflict,
    SessionRecord,
    StorageNotReady,
    UnusableId,
    journal_key,
)
from loom_ia.core.ports.exchanges import ExchangeLog, RawExchange, exchange_log, recording
from loom_ia.core.ports.idempotency import IdempotencyStore, KeyScope
from loom_ia.core.ports.model_client import (
    RETRYABLE_ERRORS,
    AnsweringClient,
    ChunkCallback,
    ModelClient,
    ModelError,
    complete,
    require_end,
    stopped_by_client,
)
from loom_ia.core.ports.policy import Policy
from loom_ia.core.ports.queue import Job, JobKind, JobState, ServedQueue, TaskQueue
from loom_ia.core.ports.secrets import SecretProvider
from loom_ia.core.ports.tool import (
    AssessedTool,
    CallAssessment,
    ReuseNote,
    RunResults,
    SeenFiles,
    SourceContext,
    SourceUnavailable,
    Tool,
    ToolContext,
    ToolError,
    ToolSource,
    ToolSourceFactory,
    UnknownEffect,
    assessed,
    idempotency_key,
    strictest_side_effects,
)
from loom_ia.core.ports.usage_counter import UsageCounter

__all__ = [
    "RETRYABLE_ERRORS",
    "AnsweringClient",
    "ArtifactNotFound",
    "ArtifactStore",
    "AssessedTool",
    "BusUnavailable",
    "CallAssessment",
    "ChunkCallback",
    "Cipher",
    "EventBus",
    "EventStore",
    "ExchangeLog",
    "IdempotencyStore",
    "Job",
    "JobKind",
    "JobState",
    "JournalBusy",
    "JournalCorrupted",
    "KeyScope",
    "Keyring",
    "MissingKey",
    "ModelClient",
    "ModelError",
    "Notice",
    "Policy",
    "RawExchange",
    "ReuseNote",
    "RunResults",
    "SealBroken",
    "SealError",
    "SecretProvider",
    "SeenFiles",
    "SequenceConflict",
    "ServedQueue",
    "SessionRecord",
    "SourceContext",
    "SourceUnavailable",
    "StorageNotReady",
    "TaskQueue",
    "Tool",
    "ToolContext",
    "ToolError",
    "ToolSource",
    "ToolSourceFactory",
    "UnknownEffect",
    "UnusableId",
    "UsageCounter",
    "assessed",
    "complete",
    "exchange_log",
    "idempotency_key",
    "journal_key",
    "recording",
    "require_end",
    "stopped_by_client",
    "strictest_side_effects",
]
