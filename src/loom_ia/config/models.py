# SPDX-License-Identifier: Apache-2.0
"""Schéma de la configuration (fonctions M1 et M2, points 35 et 50).

Les modèles Pydantic sont le schéma : le YAML n'est qu'une façon de les
remplir, et une configuration écrite en Python (fonction M2) les construit
directement. Tout champ inconnu est refusé ; une clé prévue pour une phase
suivante donne une erreur qui nomme cette phase.

Sous-ensemble des jalons J1 et J2 ; le schéma complet est dans
``docs/conception.md`` §17.

``LoomConfig`` est la racine de ``loom.yaml`` ; un agent se décrit à part
(``agents/spec.py``). ``loom schema`` publie le schéma JSON des deux, pour
l'éditeur.
"""

import logging
import re
from collections.abc import Collection, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Final, Literal, Self, cast

from pydantic import (
    AwareDatetime,
    Field,
    JsonValue,
    PositiveFloat,
    PositiveInt,
    model_validator,
)
from pydantic.json_schema import SkipJsonSchema

from loom_ia.agents.spec import AGENT_NAME_PATTERN, AgentAccess, AgentSpec
from loom_ia.config.compaction import COMPACTION_AGENT, CompactionConfig, compaction_agent
from loom_ia.config.console import ConsoleConfig
from loom_ia.config.keys import ALGORITHM, FINGERPRINT, matches
from loom_ia.config.telemetry import (
    CaptureConfig,
    ExporterConfig,
    RedactionConfig,
    TenantTelemetry,
)
from loom_ia.core.events import DURABLE_PAYLOADS
from loom_ia.core.model import (
    DEFAULT_LEASE,
    DEFAULT_TENANT,
    MCP_NAME_PATTERN,
    Approval,
    AttachmentPolicy,
    Budgets,
    DomainModel,
    KeepImages,
    McpServerSpec,
    ModelSpec,
    Quotas,
    RateLimit,
    TenantId,
    ToolOverrides,
    state_size,
)
from loom_ia.core.template import Template, TemplateError
from loom_ia.telemetry.logs import LogFormat

SCHEMA_VERSION: Final = 1
"""Version du schéma de la configuration (``version``)."""
EVENT_BACKENDS: Final = ("memory", "jsonl", "sqlite", "postgres")
"""Backends du journal (``storage.events.backend``)."""
IDEMPOTENCY_BACKENDS: Final = ("journal", "memory", "sqlite", "postgres", "redis")
"""Backends du magasin d'idempotence (``storage.idempotency.backend``)."""
SHARED_IDEMPOTENCY: Final = ("sqlite", "postgres", "redis")
"""Magasins qu'une clé métier peut exiger : partagés entre runs **et**
durables. Le journal ne voit que son run, la mémoire que son process.
"""
FILE_BACKENDS: Final = ("jsonl", "sqlite")
"""Journaux rangés hors de la mémoire : ils donnent aussi le dossier des artefacts."""
DURABLE_BACKENDS: Final = ("jsonl", "sqlite", "postgres")
"""Journaux qui survivent au process : ce qu'exige une approbation (point 28)."""
DSN_BACKENDS: Final = ("postgres",)
"""Stockages dont le raccordement passe par un DSN, jamais par un chemin."""
QUEUE_BACKENDS: Final = ("asyncio", "rabbitmq")
"""Backends de la file des tâches de fond (``storage.queue.backend``)."""
BUS_BACKENDS: Final = ("memory", "postgres", "redis")
"""Backends du bus entre process (``storage.bus.backend``)."""
REQUIRE_TOOL: Final = "loom.require_tool"
"""Politique fournie qui impose un appel d'outil (``policies.builtin``) : la
config ne dépend pas des politiques, d'où le nom écrit ici.
"""
SHARED_BUSES: Final = ("postgres", "redis")
"""Bus qui franchissent la frontière d'un process : eux seuls servent à quelque
chose quand le service tourne à plusieurs.
"""
BROKERED_QUEUES: Final = ("rabbitmq",)
"""Files servies par un courtier : les tâches tournent dans un autre process,
celui de ``loom worker``, et pas dans celui qui les met en file.
"""
DEFAULT_DB_ROLE: Final = "loom_app"
"""Rôle applicatif Postgres par défaut, celui que crée le DDL de loom. Écrit
ici plutôt qu'importé : la config ne dépend pas d'un pilote de base.
"""
ARTIFACTS_SUBDIR: Final = ".artifacts"
"""Dossier des artefacts sous celui du journal, quand la config n'en donne pas."""


class PostgresPoolSettings(DomainModel):
    """Taille et délais du pool de connexions d'un stockage Postgres.

    Un client qui déclare son propre stockage a son propre pool : ces valeurs
    s'appliquent à chacun. Les délais sont en secondes ; ils ne valaient pas
    avant (une attente pouvait durer sans fin).

    Attributes:
        min_size: Connexions ouvertes au moins (1 par défaut).
        max_size: Connexions ouvertes au plus (10 par défaut) ; au moins
            ``min_size``.
        command_timeout: Durée maximale d'une instruction, en secondes (60 par
            défaut) ; au-delà, elle est interrompue.
        lock_timeout: Attente d'un verrou (journal d'une session, schéma) avant
            d'y renoncer, en secondes (10 par défaut).
        acquire_timeout: Attente d'une connexion libre quand les ``max_size``
            sont prises, en secondes (30 par défaut).
    """

    min_size: int = Field(default=1, ge=1)
    max_size: int = Field(default=10, ge=1)
    command_timeout: float = Field(default=60.0, gt=0)
    lock_timeout: float = Field(default=10.0, gt=0)
    acquire_timeout: float = Field(default=30.0, gt=0)

    @model_validator(mode="after")
    def _check_sizes(self) -> Self:
        if self.min_size > self.max_size:
            raise ValueError(f"Pool Postgres : min_size ({self.min_size}) dépasse max_size ({self.max_size})")
        return self


class EventsStorage(DomainModel):
    """Journal des événements (``storage.events``).

    Backends : ``memory`` (défaut, perdu à l'arrêt), ``jsonl`` (un fichier par
    session sous ``path``), ``sqlite`` (une base en fichier), ``postgres``
    (DSN lu dans ``dsn_env``). ``path`` et ``dsn_env`` ne valent que pour leur
    backend ; ``pool`` seulement pour ``postgres``.

    Exemple ::

        storage:
          events: {backend: jsonl, path: .loom/journal}

    Attributes:
        backend: ``memory``, ``jsonl``, ``sqlite`` ou ``postgres``.
        path: ``jsonl`` : dossier des journaux. ``sqlite`` : fichier de la
            base. Relatif au fichier de config ; obligatoire pour ces deux
            backends.
        dsn_env: ``postgres`` : **nom** de la variable d'environnement qui
            porte le DSN (obligatoire). La config ne porte jamais un secret,
            seulement où le lire (conception §16.3).
        role: ``postgres`` : rôle applicatif pris par chaque connexion
            (``loom_app`` par défaut) : c'est à lui que s'applique la politique
            de lignes, et il n'a pas le droit de modifier un événement écrit.
            ``null`` s'en passe — la politique tient encore, par ``FORCE``,
            mais le journal redevient modifiable.
        pool: ``postgres`` : taille et délais du pool.
    """

    backend: str = "memory"
    path: Path | None = None
    dsn_env: str | None = None
    role: str | None = DEFAULT_DB_ROLE
    pool: PostgresPoolSettings = PostgresPoolSettings()

    @model_validator(mode="after")
    def _check_backend(self) -> Self:
        """Backend connu, et seulement les réglages qui valent pour lui.

        Raises:
            ValueError: Backend inconnu ; ``path`` ou ``dsn_env`` manquant ou
                de trop ; ``pool`` hors de ``postgres``.
        """
        if "pool" in self.model_fields_set and self.backend not in DSN_BACKENDS:
            raise ValueError(f"Journal {self.backend!r} : 'pool' n'a pas de sens")
        if self.backend not in EVENT_BACKENDS:
            raise ValueError(f"Journal {self.backend!r} : seuls {', '.join(EVENT_BACKENDS)} sont disponibles à ce jalon")
        if self.backend in FILE_BACKENDS and self.path is None:
            raise ValueError(f"Journal {self.backend!r} : 'path' est obligatoire")
        if self.backend in DSN_BACKENDS:
            if self.dsn_env is None:
                raise ValueError(
                    f"Journal {self.backend!r} : 'dsn_env' est obligatoire — le nom de la variable d'environnement qui porte le DSN, jamais le DSN lui-même"
                )
            if self.path is not None:
                raise ValueError(f"Journal {self.backend!r} : 'path' n'a pas de sens")
        elif self.dsn_env is not None:
            raise ValueError(f"Journal {self.backend!r} : 'dsn_env' n'a pas de sens")
        return self

    @property
    def directory(self) -> Path | None:
        """Dossier du journal : celui des fichiers JSONL, celui de la base SQLite."""
        if self.path is None:
            return None
        return self.path if self.backend == "jsonl" else self.path.parent


class ArtifactsStorage(DomainModel):
    """Stockage des fichiers : pièces jointes, sorties d'outils, résultats déportés (fonction G2).

    Sans ``backend``, il suit le journal : dossier ``.artifacts`` sous celui
    d'un journal ``jsonl``, mémoire pour un journal ``memory``.

    Attributes:
        backend: ``local`` (dossier) ou ``memory`` ; ``None`` : suit le
            journal.
        path: Dossier du stockage ``local``, relatif au fichier de config ;
            obligatoire quand le journal n'est pas en fichiers.
    """

    backend: Literal["local", "memory"] | None = None
    path: Path | None = None


class IdempotencyStorage(DomainModel):
    """Magasin des clés d'idempotence (``storage.idempotency`` ; points 18 et 49).

    ``journal`` (défaut) n'a pas de stockage propre : chaque run garde ses
    enregistrements dans son journal, ce qui suffit aux clés techniques d'un
    appel. ``memory`` voit tout un process, mais ne survit pas à sa
    fermeture. Une clé **métier** exige d'être vue de partout et de durer :
    seuls ``sqlite``, ``postgres`` et ``redis`` s'en chargent, et le chargement
    le vérifie.

    Attributes:
        backend: ``journal``, ``memory``, ``sqlite``, ``postgres`` ou
            ``redis``.
        path: ``sqlite`` : fichier de la base (obligatoire), relatif au fichier
            de config. Sa propre base : ses écritures ne se disputent pas le
            verrou du journal.
        dsn_env: ``postgres`` : nom de la variable qui porte le DSN
            (obligatoire). Peut être celle du journal — les deux tables
            cohabitent sans se gêner, Postgres ne se verrouille pas par
            fichier.
        url_env: ``redis`` : nom de la variable qui porte l'URL du serveur.
        role: ``postgres`` : rôle applicatif des connexions (``loom_app`` par
            défaut).
        pool: ``postgres`` : taille et délais du pool.
    """

    backend: str = "journal"
    path: Path | None = None
    dsn_env: str | None = None
    url_env: str | None = None
    role: str | None = DEFAULT_DB_ROLE
    pool: PostgresPoolSettings = PostgresPoolSettings()

    @model_validator(mode="after")
    def _check_backend(self) -> Self:
        """Backend connu, et seulement les réglages qui valent pour lui.

        Raises:
            ValueError: Backend inconnu ; ``path``, ``dsn_env`` ou ``url_env``
                manquant ou de trop ; ``pool`` hors de ``postgres``.
        """
        if "pool" in self.model_fields_set and self.backend != "postgres":
            raise ValueError(f"Magasin d'idempotence {self.backend!r} : 'pool' n'a pas de sens")
        if self.backend not in IDEMPOTENCY_BACKENDS:
            raise ValueError(f"Magasin d'idempotence {self.backend!r} : seuls {', '.join(IDEMPOTENCY_BACKENDS)} sont disponibles à ce jalon")
        if self.backend == "sqlite" and self.path is None:
            raise ValueError("Magasin d'idempotence 'sqlite' : 'path' est obligatoire")
        if self.backend != "sqlite" and self.path is not None:
            raise ValueError(f"Magasin d'idempotence {self.backend!r} : 'path' n'a pas de sens")
        if self.backend in DSN_BACKENDS and self.dsn_env is None:
            raise ValueError(
                f"Magasin d'idempotence {self.backend!r} : 'dsn_env' est obligatoire — le nom "
                "de la variable d'environnement qui porte le DSN, jamais le DSN lui-même"
            )
        if self.backend not in DSN_BACKENDS and self.dsn_env is not None:
            raise ValueError(f"Magasin d'idempotence {self.backend!r} : 'dsn_env' n'a pas de sens")
        if self.backend == "redis" and self.url_env is None:
            raise ValueError(
                "Magasin d'idempotence 'redis' : 'url_env' est obligatoire — le nom de la variable d'environnement qui porte l'URL, jamais l'URL elle-même"
            )
        if self.backend != "redis" and self.url_env is not None:
            raise ValueError(f"Magasin d'idempotence {self.backend!r} : 'url_env' n'a pas de sens")
        return self

    @property
    def shared(self) -> bool:
        """Vrai si ce magasin est vu de tous les runs et survit au process (point 49)."""
        return self.backend in SHARED_IDEMPOTENCY


class QueueStorage(DomainModel):
    """File des tâches de fond : runs soumis, reprises, résumés (point 27).

    ``asyncio`` exécute dans le process qui met en file : c'est le mode
    librairie, et la durabilité vient du journal, pas de la file. ``rabbitmq``
    ne fait que **publier** — les tâches tournent dans les workers
    (``loom worker``), et un process qui met en file sans worker derrière voit
    ses tâches attendre.

    Attributes:
        backend: ``asyncio`` (défaut) ou ``rabbitmq``.
        url_env: ``rabbitmq`` : nom de la variable d'environnement qui porte
            l'URL du courtier (``amqp://…``), obligatoire. La config ne porte
            jamais un secret (conception §16.3).
        max_running: ``asyncio`` : tâches menées de front (16 par défaut) ; les
            suivantes attendent leur tour. Une reprise remet tous les runs
            laissés en plan d'un coup (point 27, fonction H3).
    """

    backend: str = "asyncio"
    url_env: str | None = None
    max_running: PositiveInt = 16

    @model_validator(mode="after")
    def _check_backend(self) -> Self:
        """Backend connu ; ``url_env`` pour ``rabbitmq`` seulement, ``max_running`` pour ``asyncio`` seulement.

        Raises:
            ValueError: Backend inconnu, ou réglage manquant ou de trop.
        """
        if self.backend not in QUEUE_BACKENDS:
            raise ValueError(f"File {self.backend!r} : seuls {', '.join(QUEUE_BACKENDS)} sont disponibles à ce jalon")
        if self.brokered and "max_running" in self.model_fields_set:
            raise ValueError(
                f"File {self.backend!r} : 'max_running' ne vaut que pour la file en mémoire — "
                "les tâches tournent dans les workers, que borne 'loom worker --jobs'"
            )
        if self.backend == "rabbitmq" and self.url_env is None:
            raise ValueError(
                "File 'rabbitmq' : 'url_env' est obligatoire — le nom de la variable d'environnement qui porte l'URL du courtier, jamais l'URL elle-même"
            )
        if self.backend != "rabbitmq" and self.url_env is not None:
            raise ValueError(f"File {self.backend!r} : 'url_env' n'a pas de sens")
        return self

    @property
    def brokered(self) -> bool:
        """Vrai si les tâches tournent dans un autre process que celui qui les met en file."""
        return self.backend in BROKERED_QUEUES


class BusStorage(DomainModel):
    """Bus des nouvelles d'écriture, entre les process d'un même service (point 5).

    ``memory`` ne franchit aucune frontière : dans un seul process, le journal
    remet déjà ses écritures à ses abonnés. Les deux autres servent dès qu'un
    `loom serve` doit suivre un run piloté par un `loom worker`.

    Attributes:
        backend: ``memory`` (défaut), ``postgres`` ou ``redis``.
        dsn_env: ``postgres`` : nom de la variable qui porte le DSN — celle du
            journal si c'est la même base.
        url_env: ``redis`` : nom de la variable qui porte l'URL.
    """

    backend: str = "memory"
    dsn_env: str | None = None
    url_env: str | None = None

    @model_validator(mode="after")
    def _check_backend(self) -> Self:
        """Backend connu ; ``dsn_env`` pour ``postgres``, ``url_env`` pour ``redis``, rien pour ``memory``.

        Raises:
            ValueError: Backend inconnu, ou variable manquante ou de trop.
        """
        if self.backend not in BUS_BACKENDS:
            raise ValueError(f"Bus {self.backend!r} : seuls {', '.join(BUS_BACKENDS)} sont disponibles à ce jalon")
        attendu = {"postgres": "dsn_env", "redis": "url_env"}.get(self.backend)
        # La clé de trop d'abord : elle dit mieux la confusion qu'une clé
        # manquante (``url_env`` pour un bus Postgres, par exemple).
        for name in ("dsn_env", "url_env"):
            if name != attendu and getattr(self, name) is not None:
                juste = f", c'est {attendu!r} qu'il faut" if attendu else ""
                raise ValueError(f"Bus {self.backend!r} : {name!r} n'a pas de sens{juste}")
        if attendu is not None and getattr(self, attendu) is None:
            porte = "le DSN" if attendu == "dsn_env" else "l'URL"
            raise ValueError(
                f"Bus {self.backend!r} : {attendu!r} est obligatoire — le nom de la variable d'environnement qui porte {porte}, jamais {porte} elle-même"
            )
        return self

    @property
    def shared(self) -> bool:
        """Vrai si ce bus porte les nouvelles au-delà de ce process."""
        return self.backend in SHARED_BUSES

    @property
    def variable(self) -> str | None:
        """Nom de la variable d'environnement qui porte le raccordement."""
        return self.dsn_env or self.url_env


class EncryptionStorage(DomainModel):
    """Sceau des contenus au repos, avec la clé de chaque client (``storage.encryption`` ; point 30, conception §11.5).

    Déclarer ce bloc scelle la **charge** de chaque événement écrit et les
    **octets** de chaque fichier rangé. L'enveloppe du journal reste en clair
    — identifiants, horodatage, type, statut, agent, facettes —, si bien que
    tout ce qui se filtre continue de se filtrer ; ce qui part, c'est le
    contenu.

    ``keys`` ne porte pas de clés mais des **noms de secrets**, comme partout
    ailleurs dans la config. Chaque client redirige ce nom vers sa variable
    (``secrets: {journal_key: MARTIN_JOURNAL_KEY}``) : c'est ainsi qu'une clé
    est propre à un client, et effacer sa variable rend son journal illisible
    pour de bon — *crypto-shredding* —, sans toucher à celui du voisin.

    Plusieurs noms, dans l'ordre : le premier ferme, tous ouvrent. C'est le
    renouvellement d'une clé, l'ancienne restant le temps que les anciens
    journaux servent.

    Attributes:
        keys: Noms des secrets qui portent les clés, un au moins ; la première
            scelle, les suivantes ne font que lire (rotation).
    """

    keys: Annotated[tuple[str, ...], Field(min_length=1)]


class RetentionStorage(DomainModel):
    """Combien de temps une session dormante est gardée (``storage.retention`` ; point 30, conception §11.5).

    ``events_days`` est un nombre de jours **depuis la dernière écriture** d'une
    session. Au-delà, la session part en entier — journal, fichiers, clés
    d'idempotence —, comme une suppression RGPD demandée à la main. ``null``
    (le défaut) n'efface rien : c'est le comportement de toujours.

    La borne se décide sur la **marque** d'une session (sa dernière écriture),
    jamais sur son contenu : c'est ce qui permet d'effacer un journal scellé
    dont la clé a disparu (jalon J5.5b) — là justement où la place est perdue si on ne
    peut pas la reprendre. Une session **est donc effacée même si un run y
    attendait une approbation** : une borne plus courte que le délai d'une
    approbation détruit des approbations en attente, et c'est au déploiement de
    ne pas s'y mettre.

    Rien ne s'efface tout seul : ``loom retention`` balaie, et c'est la
    plateforme qui le met à l'heure (comme les portes de 5.4c).

    Attributes:
        events_days: Jours depuis la dernière écriture d'une session au-delà
            desquels elle est effacée ; ``null`` : jamais.
    """

    events_days: PositiveInt | None = None


class StorageConfig(DomainModel):
    """Où loom range ce qu'il garde (``storage``), à la racine ou pour un client.

    Un chiffrement exige un journal durable ; un journal ``postgres`` exige
    de déclarer ``artifacts``.

    Attributes:
        events: Le journal des événements.
        artifacts: Le stockage des fichiers.
        idempotency: Le magasin d'idempotence.
        queue: La file des tâches de fond.
        bus: Le bus des nouvelles d'écriture entre process.
        encryption: Sceau des contenus au repos. Sans ce bloc, les contenus
            sont rangés en clair : c'est le journal de toujours, et l'isolation
            par client, les portées de clés et la suppression RGPD restent ce
            qui les protège.
        retention: Rétention des sessions dormantes ; sans borne, rien ne
            s'efface.
    """

    events: EventsStorage = EventsStorage()
    artifacts: ArtifactsStorage = ArtifactsStorage()
    idempotency: IdempotencyStorage = IdempotencyStorage()
    queue: QueueStorage = QueueStorage()
    bus: BusStorage = BusStorage()
    encryption: EncryptionStorage | None = None
    retention: RetentionStorage = RetentionStorage()

    @model_validator(mode="after")
    def _check_encryption(self) -> Self:
        if self.encryption is None:
            return self
        if self.events.backend == "memory":
            # Rien n'est rangé, donc rien n'est scellé : la config promettrait
            # une protection que le journal n'a pas les moyens de tenir.
            raise ValueError(
                "Chiffrement déclaré avec un journal 'memory' : rien n'y est écrit, donc rien "
                "n'y est scellé — déclarer un journal durable (jsonl, sqlite, postgres)"
            )
        doubles = [name for name in self.encryption.keys if self.encryption.keys.count(name) > 1]
        if doubles:
            raise ValueError(f"Chiffrement : secret déclaré deux fois — {doubles[0]!r}")
        return self

    @model_validator(mode="after")
    def _check_artifacts(self) -> Self:
        artifacts = self.artifacts
        if artifacts.backend == "memory" and artifacts.path is not None:
            raise ValueError("Artefacts 'memory' : 'path' n'a pas de sens")
        if artifacts.backend == "local" and artifacts.path is None and self.events.path is None:
            raise ValueError("Artefacts 'local' : 'path' est obligatoire quand le journal n'est pas en fichiers")
        if self.events.backend in DSN_BACKENDS and artifacts.backend is None:
            # Un journal durable qui laisserait les artefacts en mémoire
            # garderait la trace de fichiers disparus à la fermeture : le
            # piège est trop silencieux pour un défaut.
            raise ValueError(
                f"Journal {self.events.backend!r} : déclarer 'storage.artifacts' — 'local' avec son 'path', ou 'memory' si les artefacts n'ont pas à survivre"
            )
        return self

    @property
    def artifacts_backend(self) -> Literal["local", "memory"]:
        """Stockage d'artefacts effectif : celui déclaré, sinon celui qui suit le journal."""
        if self.artifacts.backend is not None:
            return self.artifacts.backend
        return "local" if self.events.backend in FILE_BACKENDS else "memory"

    @property
    def artifacts_path(self) -> Path | None:
        """Dossier du stockage ``local`` : celui déclaré, sinon ``.artifacts`` sous le journal."""
        if self.artifacts_backend != "local":
            return None
        if self.artifacts.path is not None:
            return self.artifacts.path
        directory = self.events.directory
        return None if directory is None else directory / ARTIFACTS_SUBDIR


class StateLimits(DomainModel):
    """Taille du state d'une session (``sessions.state`` ; décision D7 du state de session), en octets de JSON compact.

    Le state part avec chaque appel de l'orchestrateur qui le reçoit, et
    l'estimation qui déclenche la compaction ne le compte pas : il reste petit.

    Attributes:
        max_value_bytes: Taille maximale d'une valeur, écrite par un outil ou
            posée par l'application (1024 par défaut).
        max_total_bytes: Taille maximale de tout le state de la session (4096
            par défaut).
    """

    max_value_bytes: PositiveInt = 1024
    max_total_bytes: PositiveInt = 4096

    @model_validator(mode="after")
    def _check_sizes(self) -> Self:
        if self.max_value_bytes > self.max_total_bytes:
            raise ValueError(f"sessions.state : max_value_bytes ({self.max_value_bytes}) dépasse max_total_bytes ({self.max_total_bytes})")
        return self


class SessionMedia(DomainModel):
    """Fichiers des tours précédents dans les requêtes d'une session (fonction F4).

    Attributes:
        keep_images: ``all`` (défaut) : un modèle vision revoit toutes les
            images de la session, à chaque appel, relues dans le stockage
            d'artefacts. ``last_run`` : une image d'un tour précédent est
            remplacée par une note (nom, type, taille) ; seules celles du run
            en cours partent en octets. Pas par défaut : pour une image rendue
            par un outil, le modèle peut alors désavouer la description qu'il
            en avait faite (essai de la fonction G3, 20/10).
    """

    keep_images: KeepImages = "all"


class SessionsConfig(DomainModel):
    """Vie d'une session (``sessions``) : historique matérialisé, compaction (fonctions F1 à F4), médias, state.

    Attributes:
        snapshot_every: Événements ajoutés depuis le dernier marqueur avant
            qu'un snapshot de l'historique soit écrit (50 par défaut ;
            conception §11.2). Le relire coûte moins que de rejouer ce qu'il
            couvre ; l'écrire recopie l'historique dans le journal.
        compaction: Résumé des sessions longues. Sans ce bloc, une session
            n'est jamais résumée : elle grandit jusqu'à la fenêtre du modèle.
        media: Images des tours précédents dans les requêtes (fonction F4).
        state: Limites du state de la session ; ses clés sont déclarées par les
            agents.
    """

    snapshot_every: PositiveInt = 50
    compaction: CompactionConfig | None = None
    media: SessionMedia = SessionMedia()
    state: StateLimits = StateLimits()


class ToolsExecution(DomainModel):
    """Exécution des outils (``execution.tools``), pour tous les agents ; un agent la surcharge par son ``execution.tools``.

    Attributes:
        timeout: Délai par défaut d'un outil, en secondes (30) ; ``null``
            retire la limite.
        validate_arguments: Contrôle des arguments contre le schéma de l'outil
            (vrai par défaut).
        offload_over: Au-delà, en caractères, le résultat est déporté (50 000
            par défaut ; point 16) ; ``null`` le désactive.
        order: Ordre des appels d'un même tour : ``effects`` (défaut), un appel
            à effet attend ceux qui le précèdent et ceux qui le suivent
            l'attendent ; ``free``, tous partent ensemble (comme avant 2.0.20).
        threads: Threads du pool des outils synchrones (``loom-outil``), à part
            de celui d'asyncio, qui sert le journal et les fichiers (16 par
            défaut ; décision B-2 du délai et des threads). Un pool par
            process.
        grace: Après un délai dépassé, attente au plus du travail en thread
            d'un appel à effet avant de lancer les appels suivants du tour, en
            secondes (30) ; ``null`` : sans borne.
    """

    timeout: PositiveFloat | None = 30.0
    validate_arguments: bool = True
    offload_over: PositiveInt | None = 50_000
    order: Literal["effects", "free"] = "effects"
    threads: PositiveInt = 16
    grace: PositiveFloat | None = 30.0


class ExecutionConfig(DomainModel):
    """Exécution des runs (``execution``) : outils, pièces jointes, arrêt, concession.

    Attributes:
        tools: Exécution des outils.
        attachments: Pièces jointes acceptées à l'entrée d'un run (fonction G1)
            : formats d'image, taille et nombre maximaux.
        shutdown_timeout: Délai laissé aux tâches de fond (compaction) à la
            fermeture de l'instance, en secondes (30).
        lease: Durée de la concession prise sur un run par l'instance qui le
            pilote (point 27), en secondes ; elle est renouvelée au tiers tant
            qu'il tourne. Passée, un autre worker peut reprendre le run — ce
            qui n'arrive que si le porteur est mort.
    """

    tools: ToolsExecution = ToolsExecution()
    attachments: AttachmentPolicy = AttachmentPolicy()
    shutdown_timeout: PositiveFloat = 30.0
    lease: PositiveFloat = DEFAULT_LEASE


def _declares(data: object, key: str) -> bool:
    """Vrai si le bloc brut (avant validation) porte cette clé."""
    return isinstance(data, dict) and key in cast(dict[object, object], data)


class LoggingConfig(DomainModel):
    """Logs du process (``telemetry.logging``).

    Attributes:
        level: Niveau : ``DEBUG``, ``INFO`` (défaut), ``WARNING``, ``ERROR``…
        format: ``console`` (texte, défaut) ou ``json`` (une ligne JSON par
            log).
    """

    level: str = "INFO"
    format: LogFormat = "console"

    @model_validator(mode="after")
    def _check_level(self) -> Self:
        known = logging.getLevelNamesMapping()
        if self.level.upper() not in known:
            raise ValueError(f"Niveau de log {self.level!r} inconnu (attendus : {', '.join(known)})")
        return self


class TelemetryConfig(DomainModel):
    """Logs, capture des exports, masquage et collecteurs (``telemetry`` ; fonctions K3, K4 et K7, conception §17.7).

    Attributes:
        logging: Logs du process.
        capture: Ce que les exports emportent : métadonnées ou contenu.
        redaction: Motifs masqués dans ce qui sort.
        exporters: Collecteurs OpenTelemetry. Sans collecteur, rien ne sort du
            journal que les logs.
    """

    logging: LoggingConfig = LoggingConfig()
    capture: CaptureConfig = CaptureConfig()
    redaction: RedactionConfig = RedactionConfig()
    exporters: tuple[ExporterConfig, ...] = ()

    @model_validator(mode="before")
    @classmethod
    def _bus_elsewhere(cls, data: object) -> object:
        if _declares(data, "bus"):
            # Prévu ici par la conception, réalisé ailleurs : le bus est un
            # stockage partagé entre process (jalon J5.3c), pas un réglage d'export.
            raise ValueError("'telemetry.bus' : le bus se déclare dans 'storage.bus' (5.3c) ; la file d'un abonné n'a pas de borne réglable")
        return data


class TenantSpec(DomainModel):
    """Un client, et la liste fermée de ce qu'il surcharge (fonction L1, points 33 et 34, conception §17.8).

    Tout le reste de la configuration lui est commun : mêmes agents, mêmes
    prompts, mêmes outils. Un client ne redéfinit que ce qui le distingue —
    les modèles qu'il paie, les secrets qu'il apporte, ce qu'on lui permet.
    Les prompts ne sont **pas** surchargeables (conception §6) : seules les
    variables qu'ils citent le sont, ce qui garde la logique métier dans un
    seul endroit.

    Exemple ::

        tenants:
          - id: dupont
            agents: [support]
            models: {SONNET: HAIKU}
            secrets: {ANTHROPIC_API_KEY: DUPONT_ANTHROPIC_KEY}
            variables: {entreprise: Dupont SARL}

    Attributes:
        id: Identifiant du client.
        agents: Agents que ce client peut lancer ; vide signifie tous ceux de
            la config.
        tools_deny: Outils retirés à ce client, sous le nom que voit le modèle
            (préfixe MCP compris) : un rôle, un sous-agent ou un outil qu'on ne
            lui ouvre pas.
        models: Correspondance des modèles : {modèle de la config: modèle de ce
            client}. Elle vaut partout — orchestrateur, rôles, juges, chaînes
            de secours, compaction —, la cible devant être déclarée dans
            ``models``.
        approvals: Approbation imposée par outil, quoi qu'en dise sa
            déclaration (point 17) : c'est le client qui sait ce qui l'engage.
        secrets: Secrets : {nom attendu par la config: variable d'environnement
            de ce client}. Ce qui n'y est pas reste lu dans l'environnement
            commun.
        variables: Variables citées par les prompts système (``{{ entreprise
            }}``).
        budgets: Budgets de ce client : ils surchargent ceux de la racine clé
            par clé, et portent en plus ses plafonds par période (``tenant``,
            jalon J5.1b).
        quotas: Débit accordé à ce client, quel que soit l'accès emprunté
            (``runs_per_minute`` ; fonction L3).
        storage: Stockage propre à ce client (isolation physique,
            ``TenantRouter``) ; sans lui, celui de la racine, où seul le
            ``tenant_id`` le distingue. Le bloc déclare ``events`` : le défaut
            d'un journal est la mémoire, et un client qui n'aurait que
            ``artifacts`` perdrait le sien à chaque arrêt. ``encryption`` et
            ``idempotency`` ne s'y déclarent pas.
        retention: Rétention propre à ce client, à côté de ``storage`` et non
            dedans : le bloc ``storage`` dit **où** ses données vivent, et le
            déclarer lui donnerait son propre journal. Combien de temps elles
            vivent est une autre question — souvent contractuelle, et propre au
            client (jalon J5.5c).
        telemetry: Capture des exports propre à ce client (jalon J6.1a) : la
            sienne, clé par clé, sur celle de la racine. Ses collecteurs et ses
            motifs restent communs.
    """

    id: TenantId
    agents: tuple[str, ...] = ()
    tools_deny: tuple[str, ...] = ()
    models: dict[str, str] = Field(default_factory=dict[str, str])
    approvals: dict[str, Approval] = Field(default_factory=dict[str, Approval])
    secrets: dict[str, str] = Field(default_factory=dict[str, str])
    variables: dict[str, JsonValue] = Field(default_factory=dict[str, JsonValue])
    budgets: Budgets | None = None
    quotas: Quotas = Quotas()
    storage: StorageConfig | None = None
    retention: RetentionStorage | None = None
    telemetry: TenantTelemetry = TenantTelemetry()

    @model_validator(mode="after")
    def _check(self) -> Self:
        """Ce qu'un client peut déclarer, et seulement cela.

        Raises:
            ValueError: ``id`` vide ; ``storage.encryption`` ou
                ``storage.idempotency`` propres au client ; ``storage`` sans
                ``events`` ; nom d'agent invalide ; modèle remplacé par
                lui-même.
        """
        if not self.id.strip():
            raise ValueError("Client : 'id' ne peut pas être vide")
        for agent in self.agents:
            if not re.fullmatch(AGENT_NAME_PATTERN, agent):
                raise ValueError(f"Client {self.id!r} : nom d'agent invalide : {agent!r}")
        for source, target in self.models.items():
            if source == target:
                raise ValueError(f"Client {self.id!r} : le modèle {source!r} se remplace par lui-même")
        if self.storage is not None and self.storage.encryption is not None:
            # Le sceau est une propriété du déploiement, la clé est celle du
            # client : les mélanger ferait de « scellé » une question à poser
            # client par client, alors que la réponse doit être la même pour
            # tout le journal.
            raise ValueError(
                f"Client {self.id!r} : 'storage.encryption' ne se surcharge pas — le sceau se "
                "déclare à la racine, et ce qui est propre à un client, c'est sa clé (une "
                "redirection 'secrets' vers sa variable)"
            )
        if self.storage is not None and self.storage.idempotency != IdempotencyStorage():
            # Le port d'idempotence n'a le client que sur ``reserve`` : un
            # magasin par client demanderait de le porter jusqu'à ``get``.
            raise ValueError(
                f"Client {self.id!r} : 'storage.idempotency' propre à un client est prévu "
                "pour le jalon J5.3 (magasins de service) ; les clés sont déjà préfixées "
                "par le client dans le magasin commun"
            )
        if self.storage is not None and "events" not in self.storage.model_fields_set:
            # Déclarer ``storage`` donne au client son propre journal (le routeur
            # le crée) ; sans ``events``, ce serait le défaut, en mémoire : perdu
            # à l'arrêt, et hors de la garde de durabilité. Hériter du journal de
            # la racine serait l'autre lecture, mais c'est déjà ce que fait un
            # client sans bloc ``storage`` : un bloc muet sur ``events`` ne dit
            # pas lequel des deux est voulu, et le refus le fait choisir.
            raise ValueError(
                f"Client {self.id!r} : 'storage' ne déclare pas 'events' — son journal serait "
                "en mémoire, perdu à chaque arrêt, sans que rien ne le dise. Déclarer "
                "'storage.events' (par exemple {backend: jsonl, path: …}, ou {backend: memory} "
                "si c'est voulu), ou retirer le bloc 'storage' pour partager le journal de "
                "la racine"
            )
        return self

    def allows(self, agent: str) -> bool:
        """Vrai si ce client peut lancer cet agent (``agents`` vide : tous)."""
        return not self.agents or agent in self.agents


type Permission = Literal[
    "run",
    "answer",
    "cancel",
    "approve",
    "self_approve",
    "amend",
    "read",
    "read_content",
    "admin",
    "act_as",
]
"""Permission d'un rôle d'accès (décision D-3 des utilisateurs et rôles)."""
PERMISSIONS: Final[tuple[Permission, ...]] = (
    "run",
    "answer",
    "cancel",
    "approve",
    "self_approve",
    "amend",
    "read",
    "read_content",
    "admin",
    "act_as",
)
"""Ordre d'affichage (``loom validate``) et liste fermée des permissions (décision D-3 des utilisateurs et rôles)."""
ACCESS_NAME_PATTERN: Final = AGENT_NAME_PATTERN
"""Identifiants d'utilisateur et de rôle d'accès : le motif des noms d'agent.
Ils s'écrivent au journal (``by``) et sur la ligne de commande (``--user``).
"""
LOOPBACK_HOSTS: Final = frozenset({"127.0.0.1", "localhost", "::1"})
"""Adresses de la boucle locale : les seules où ``server.http.user`` est permis (décision D-1b des utilisateurs et rôles)."""

type RoleProblem = Literal[
    "id_invalid",
    "permissions_missing",
    "permission_twice",
    "agent_twice",
    "endorsed_twice",
    "agent_invalid",
    "self_approve_alone",
    "act_as_without_roles",
    "roles_without_act_as",
    "endorses_itself",
]
"""Une règle d'un rôle d'accès enfreinte, en code (``role_problems``) : une console la rédige dans sa langue."""

type EndorsementProblem = Literal["endorsed_undeclared", "endorses_gateway"]
"""Un rôle endossable fautif (``endorsement_problems``) : non déclaré, ou qui a lui-même ``act_as``."""

type UserProblem = Literal["roles_missing", "role_twice", "role_undeclared"]
"""Une règle des rôles d'un utilisateur enfreinte, en code (``user_problems``)."""

_ROLE_MESSAGES: Final[Mapping[RoleProblem, str]] = {
    "id_invalid": "identifiant invalide : {detail}",
    "permissions_missing": "une permission au moins est attendue",
    "permission_twice": "permission déclaré deux fois : {detail}",
    "agent_twice": "agent déclaré deux fois : {detail}",
    "endorsed_twice": "rôle endossable déclaré deux fois : {detail}",
    "agent_invalid": "nom d'agent invalide : {detail}",
    "self_approve_alone": "'self_approve' sans 'approve' — elle ne vaut que dans le même rôle qu'approve",
    "act_as_without_roles": "'act_as' demande 'act_as_roles', les rôles que ce rôle peut endosser",
    "roles_without_act_as": "'act_as_roles' sans la permission 'act_as'",
    "endorses_itself": "un rôle ne s'endosse pas lui-même (act_as_roles)",
}
"""La phrase de chaque code, après « Rôle d'accès 'id' : » ; ``{detail}`` : les doublons, ou l'agent invalide."""


def role_problems(
    role_id: str,
    permissions: Sequence[Permission],
    agents: Sequence[str] = (),
    act_as_roles: Sequence[str] = (),
) -> tuple[RoleProblem, ...]:
    """Ce que les champs d'un rôle d'accès enfreignent, en codes, dans l'ordre des contrôles d'``AccessRole``.

    Les règles d'``AccessRole``, contraintes de champ comprises (identifiant,
    une permission au moins), sur des champs bruts : une console contrôle un
    formulaire avec elles avant de pouvoir construire le rôle, et rédige chaque
    code dans sa langue. ``AccessRole`` lève la phrase de loom du premier.

    Args:
        role_id: L'identifiant du rôle.
        permissions: Ses permissions.
        agents: Les agents auxquels il est borné ; vide : tous.
        act_as_roles: Les rôles qu'il endosse.

    Returns:
        Les codes ; vide : le rôle se construit.
    """
    return tuple(code for code, _ in _role_findings(role_id, permissions, agents, act_as_roles))


def endorsement_problems(act_as_roles: Sequence[str], declared: Mapping[str, Collection[Permission]]) -> tuple[tuple[EndorsementProblem, str], ...]:
    """Les rôles endossables fautifs d'un rôle, chacun avec son code, dans l'ordre de ``act_as_roles``.

    ``endorsed_undeclared`` : le rôle n'est pas déclaré ; ``endorses_gateway`` :
    il a lui-même ``act_as`` — une passerelle agirait au nom de quelqu'un au nom
    de quelqu'un d'autre. ``check_security_references`` lève la phrase de loom du premier.

    Args:
        act_as_roles: Les rôles qu'un rôle endosse.
        declared: Les permissions de chaque rôle déclaré, par identifiant.

    Returns:
        Chaque rôle endossable fautif, avec son code ; vide : aucun.
    """
    found: list[tuple[EndorsementProblem, str]] = []
    for endorsed in act_as_roles:
        permissions = declared.get(endorsed)
        if permissions is None:
            found.append(("endorsed_undeclared", endorsed))
        elif "act_as" in permissions:
            found.append(("endorses_gateway", endorsed))
    return tuple(found)


def user_problems(roles: Sequence[str], declared: Collection[str] | None = None) -> tuple[tuple[UserProblem, str | None], ...]:
    """Ce que les rôles d'un utilisateur enfreignent, en codes : aucun rôle, un rôle deux fois, un rôle non déclaré.

    ``UserSpec`` lève la phrase de loom des doublons, ``check_security_references``
    celle du premier rôle non déclaré.

    Args:
        roles: Les rôles de l'utilisateur.
        declared: Les identifiants des rôles déclarés ; ``None`` : non contrôlé.

    Returns:
        Chaque problème, avec le rôle qu'il vise (``None`` pour ``roles_missing``) :
        les doublons triés, puis les rôles non déclarés dans leur ordre.
    """
    found: list[tuple[UserProblem, str | None]] = []
    if not roles:
        found.append(("roles_missing", None))
    found.extend(("role_twice", role) for role in _doubles(roles))
    if declared is not None:
        found.extend(("role_undeclared", role) for role in dict.fromkeys(roles) if role not in declared)
    return tuple(found)


def _role_findings(role_id: str, permissions: Sequence[Permission], agents: Sequence[str], act_as_roles: Sequence[str]) -> list[tuple[RoleProblem, str]]:
    """Les règles d'un rôle enfreintes, chacune avec ce que sa phrase en dit (doublons, agent invalide)."""
    found: list[tuple[RoleProblem, str]] = []
    if re.fullmatch(ACCESS_NAME_PATTERN, role_id) is None:
        found.append(("id_invalid", repr(role_id)))
    if not permissions:
        found.append(("permissions_missing", ""))
    if doubles := _doubles(permissions):
        found.append(("permission_twice", ", ".join(doubles)))
    if doubles := _doubles(agents):
        found.append(("agent_twice", ", ".join(doubles)))
    if doubles := _doubles(act_as_roles):
        found.append(("endorsed_twice", ", ".join(doubles)))
    invalid = [agent for agent in agents if not re.fullmatch(AGENT_NAME_PATTERN, agent)]
    if invalid:
        found.append(("agent_invalid", repr(invalid[0])))
    if "self_approve" in permissions and "approve" not in permissions:
        # self_approve étend approve aux demandes de ses propres runs (décision D-7) :
        # seule, elle n'ouvrirait rien, et se lirait pourtant comme un droit.
        found.append(("self_approve_alone", ""))
    if "act_as" in permissions and not act_as_roles:
        # Une passerelle sans borne agirait au nom de n'importe qui, gérant compris (décision D-5b).
        found.append(("act_as_without_roles", ""))
    if act_as_roles and "act_as" not in permissions:
        found.append(("roles_without_act_as", ""))
    if role_id in act_as_roles:
        found.append(("endorses_itself", ""))
    return found


def _doubles(names: Sequence[str]) -> list[str]:
    """Les noms cités plus d'une fois, triés."""
    return sorted({name for name in names if names.count(name) > 1})


class AccessRole(DomainModel):
    """Rôle d'accès : des permissions, éventuellement bornées à des agents (décision D-3 des utilisateurs et rôles).

    À ne pas confondre avec les rôles LLM d'un agent (``roles:`` dans
    ``agent.yaml``), qui délèguent à un modèle : un rôle d'accès dit ce qu'un
    utilisateur peut faire sur loom, et ne se déclare que dans
    ``security.roles``.

    Permissions : ``run``, ``answer``, ``cancel``, ``approve``,
    ``self_approve`` (avec ``approve``), ``amend``, ``read``, ``read_content``,
    ``admin``, ``act_as`` (avec ``act_as_roles``).

    Attributes:
        id: Identifiant du rôle : lettres, chiffres, ``_`` et ``-``.
        description: Libellé affiché par ``loom validate``.
        permissions: Les permissions ; une au moins.
        agents: Agents auxquels le rôle est borné ; vide signifie tous.
        act_as_roles: Rôles qu'une passerelle peut endosser (décision D-5b) :
            obligatoire avec ``act_as``, interdit sans. Ils restreignent les
            rôles effectifs de l'utilisateur au nom duquel elle agit.
    """

    id: str = Field(pattern=ACCESS_NAME_PATTERN)
    description: str = ""
    permissions: tuple[Permission, ...] = Field(min_length=1)
    agents: tuple[str, ...] = ()
    act_as_roles: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _check(self) -> Self:
        """Permissions cohérentes, agents et rôles endossés valides.

        Les règles sont celles de ``role_problems`` (codes) ; la phrase est celle
        du premier code enfreint.

        Raises:
            ValueError: ``self_approve`` sans ``approve`` ; ``act_as`` sans
                ``act_as_roles`` ou l'inverse ; un rôle qui s'endosse lui-même ; nom
                d'agent invalide ; doublon.
        """
        found = _role_findings(self.id, self.permissions, self.agents, self.act_as_roles)
        if found:
            code, detail = found[0]
            raise ValueError(f"Rôle d'accès {self.id!r} : " + _ROLE_MESSAGES[code].format(detail=detail))
        return self

    def allows(self, agent: str) -> bool:
        """Vrai si le rôle vaut pour cet agent."""
        return not self.agents or agent in self.agents


class UserSpec(DomainModel):
    """Utilisateur : une personne ou un service qui agit sur loom (décisions D-1 et D-2 des utilisateurs et rôles).

    Il porte les droits, par ses rôles d'accès, et le client au nom duquel il
    agit. Une clé d'API ne fait que le reconnaître (décision D-4) : une
    personne garde la même identité au journal, quel que soit l'accès qu'elle
    emprunte.

    Attributes:
        id: Identifiant de l'utilisateur, écrit au journal (``by``) et donné à
            ``--user``.
        name: Nom affiché (journal, CLI) ; l'identifiant sinon.
        roles: Ses rôles d'accès, déclarés dans ``security.roles`` ; un au
            moins.
        tenant: Client de l'utilisateur (``default`` par défaut) : celui de
            toutes ses clés.
    """

    id: str = Field(pattern=ACCESS_NAME_PATTERN)
    name: str | None = Field(default=None, min_length=1)
    roles: tuple[str, ...] = Field(min_length=1)
    tenant: TenantId = DEFAULT_TENANT

    @model_validator(mode="after")
    def _check(self) -> Self:
        doubled = [role for code, role in user_problems(self.roles) if code == "role_twice" and role is not None]
        if doubled:
            raise ValueError(f"Utilisateur {self.id!r} : rôle déclaré deux fois : {', '.join(doubled)}")
        return self


class ApiKey(DomainModel):
    """Clé déclarée dans la config, par son empreinte seulement (point 39).

    Elle identifie un utilisateur (décision D-4 des utilisateurs et rôles) : le
    client et les droits sont les siens. ``scopes`` et ``agents`` ne peuvent
    que les restreindre. ``loom keys create`` donne la clé et le bloc à coller.

    Attributes:
        id: Identifiant de la clé, affiché par ``loom validate``.
        hash: Empreinte donnée par ``loom keys create`` : ``sha256:`` et 64
            chiffres hexadécimaux minuscules, vérifiés au chargement.
        user: Utilisateur que cette clé identifie, déclaré dans
            ``security.users``.
        rate_limit: Débit de cette clé (``per_minute``), vérifié par l'accès
            HTTP (point 39) ; ``null`` : aucun.
        expires: Fin de validité, avec fuseau (jalon J5.2a) ; ``null`` : la clé
            ne périme pas. Une clé déjà expirée n'empêche pas le démarrage —
            elle est refusée à l'appel, et ``loom validate`` la signale. Une
            rotation, c'est deux clés déclarées en même temps, l'ancienne
            portant sa date de fin.
        scopes: Permissions que la clé garde de celles de son utilisateur ;
            ``null`` : toutes. Une clé restreint, elle n'ajoute jamais
            (décision D-4).
        agents: Agents autorisés ; vide signifie tous.
    """

    id: str = Field(min_length=1)
    hash: str
    user: str = Field(pattern=ACCESS_NAME_PATTERN)
    rate_limit: RateLimit | None = None
    expires: AwareDatetime | None = None
    scopes: tuple[Permission, ...] | None = None
    agents: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _check_hash(self) -> Self:
        # Un modèle recopié tel quel (``sha256:…``) se refuse ici, au chargement,
        # plutôt qu'à chaque requête.
        if not FINGERPRINT.fullmatch(self.hash):
            raise ValueError(
                f"Empreinte de clé attendue sous la forme '{ALGORITHM}:' suivie de 64 chiffres hexadécimaux minuscules, telle que 'loom keys create' la donne"
            )
        return self

    def accepts(self, key: str) -> bool:
        """Vrai si la clé en clair a cette empreinte (comparaison à temps constant)."""
        return matches(key, self.hash)

    def expired(self, now: datetime | None = None) -> bool:
        """Vrai si la clé a passé sa date de fin."""
        if self.expires is None:
            return False
        return (now or datetime.now(UTC)) >= self.expires

    def allows(self, agent: str) -> bool:
        """Vrai si la clé ouvre cet agent (``agents`` vide : tous)."""
        return not self.agents or agent in self.agents


class AuditConfig(DomainModel):
    """Le fichier où chaque refus d'accès est écrit (``security.audit`` ; décision D-9 des utilisateurs et rôles, ``access/audit.py``).

    Une ligne JSON par refus, ajoutée par tous les accès — SDK, REST, MCP,
    CLI — de tous les processus qui partagent la config. ``path`` est relatif
    au fichier de config. ``retention_days`` borne l'âge des lignes, que
    ``loom retention`` efface au-delà ; ``null`` (le défaut) les garde toutes.

    Attributes:
        path: Fichier des refus, relatif au fichier de config ; hors du dossier
            d'un journal.
        retention_days: Âge maximal des lignes, en jours ; ``null`` : toutes
            gardées.
    """

    path: Path
    retention_days: PositiveInt | None = None


MINIMAL_AUDIT: Final = """  audit:
    path: data/audit.jsonl"""
"""Le bloc d'audit que les messages de config donnent à recopier (décision D-9 des utilisateurs et rôles)."""

MINIMAL_SECURITY: Final = f"""security:
  roles:
    - id: owner
      permissions: [run, answer, cancel, approve, self_approve, amend, read, read_content, admin]
  users:
    - id: <votre identifiant>
      roles: [owner]
{MINIMAL_AUDIT}"""
"""Ce que le message d'une config sans rôle ni utilisateur donne à recopier (décision D-1 des utilisateurs et rôles)."""


SHARED_AUDIT: Final = Path("data/audit.jsonl")
"""Fichier des refus par défaut d'une sécurité partagée (``security.source: console``)."""


class SecurityConfig(DomainModel):
    """Qui agit sur loom, et ce que chacun peut faire (``security`` ; décisions D-1 à D-5 des utilisateurs et rôles).

    Au moins un rôle et un utilisateur sont exigés (décision D-1), et le fichier
    des refus (décision D-9) : le contrôle est fait par ``LoomConfig.check``,
    pour que le message donne la config minimale plutôt que l'erreur générique
    d'un champ manquant.

    **Sécurité partagée** (``source: console``, analyse_referentiel_utilisateurs.md
    de Heddlo) : les rôles, les utilisateurs et les clés viennent de la console,
    par le canal (``security.sync``) ; ``audit.path`` vaut ``data/audit.jsonl`` par
    défaut. ``loom.yaml`` peut en donner une sécurité de départ, utilisée seulement sans cache
    valide et jusqu'à la première part (décision D-A) : la part la remplace, et le
    cache la fait ignorer. La part reçue est posée par ``Loom.apply_security``
    (``console/shared.py``), sans repasser par ce validateur.

    Exemple ::

        security:
          roles:
            - id: operateur
              permissions: [run, answer, read]
          users:
            - id: denis
              roles: [operateur]
          audit:
            path: .loom/audit.jsonl

    Attributes:
        source: ``console`` : sécurité partagée, reçue de la console ; absente :
            sécurité locale, déclarée ici.
        roles: Rôles d'accès.
        users: Utilisateurs.
        api_keys: Clés d'API, par leur empreinte.
        audit: Fichier des refus d'accès.
    """

    source: Literal["console"] | None = None
    roles: tuple[AccessRole, ...] = ()
    users: tuple[UserSpec, ...] = ()
    api_keys: tuple[ApiKey, ...] = ()
    audit: AuditConfig | None = None

    @model_validator(mode="before")
    @classmethod
    def _shared_audit(cls, data: object) -> object:
        """Sécurité partagée sans ``audit`` : ``data/audit.jsonl``."""
        if not isinstance(data, dict):
            return data
        fields = cast("dict[str, object]", data)
        if fields.get("source") == "console" and fields.get("audit") is None:
            return {**fields, "audit": {"path": str(SHARED_AUDIT)}}
        return fields

    @property
    def received(self) -> bool:
        """Vrai si la sécurité déclare quelqu'un : toujours en local, après une part qui a des utilisateurs en partagé.

        Une part sans utilisateur (les rôles partent toujours) est acceptée telle
        quelle : personne ne peut rien faire, et rien de ce qui cite un
        utilisateur ou une clé n'est contrôlé.
        """
        return bool(self.users)

    @property
    def seeded(self) -> bool:
        """Vrai si ``loom.yaml`` donne une sécurité de départ à la sécurité partagée : rôles, utilisateurs ou clés déclarés (décision D-A).

        Une fois une part appliquée, la sécurité vient de la console, et la
        propriété reste vraie tant que la sécurité déclare quelqu'un : c'est à
        l'appelant de savoir si une part a été reçue.
        """
        return self.source == "console" and bool(self.roles or self.users or self.api_keys)

    def role(self, role_id: str) -> AccessRole:
        """Rôle d'accès déclaré, par son identifiant.

        Args:
            role_id: L'identifiant.

        Raises:
            KeyError: Rôle non déclaré.
        """
        for role in self.roles:
            if role.id == role_id:
                return role
        raise KeyError(role_id)

    def user(self, user_id: str) -> UserSpec:
        """Utilisateur déclaré, par son identifiant.

        Args:
            user_id: L'identifiant.

        Raises:
            KeyError: Utilisateur non déclaré.
        """
        for user in self.users:
            if user.id == user_id:
                return user
        raise KeyError(user_id)

    def roles_of(self, user_id: str) -> tuple[AccessRole, ...]:
        """Rôles d'accès d'un utilisateur déclaré, dans l'ordre de sa déclaration.

        Args:
            user_id: L'utilisateur.
        """
        return tuple(self.role(role) for role in self.user(user_id).roles)

    def permissions_of(self, user_id: str) -> frozenset[Permission]:
        """Permissions que l'un au moins des rôles de l'utilisateur porte, tous agents confondus.

        Args:
            user_id: L'utilisateur.
        """
        return frozenset(permission for role in self.roles_of(user_id) for permission in role.permissions)


class HttpServer(DomainModel):
    """Serveur HTTP de ``loom serve`` (``server.http``).

    Attributes:
        host: Adresse d'écoute (``127.0.0.1`` par défaut).
        port: Port d'écoute (8760 par défaut : suite des ports de loom, ``docs/analyse_ports.md``).
        base_path: Préfixe commun des routes, par exemple ``/loom`` ; commence
            par ``/``.
        max_body_bytes: Plafond du corps d'une requête (JSON, déclencheurs,
            MCP, envois de fichiers) : au-delà, 413 sans lire la suite. Par
            MCP, le SDK plafonne déjà le corps à 4 Mio avant tout code de
            Loom-IA : cette valeur ne peut que l'abaisser, pas le relever. Les
            fichiers y voyagent en base64 dans le JSON (4 octets pour 3), donc
            environ 3 Mio de fichier brut ; REST accepte jusqu'à ce plafond
            (défaut 128 Mio).
        docs: Documentation interactive (``/docs``, ``/redoc``) et schéma
            (``/openapi.json``) : ils se lisent **sans clé**, et décrivent
            toutes les routes. ``null`` (défaut) : servis, sauf en profil
            strict (``prod``) ; ``true`` / ``false`` tranchent (essai de la
            fonction N2, 23/10 : ils répondaient 200 sans clé à côté d'une API
            protégée).
        user: Utilisateur au nom duquel une instance **locale sans clé** répond
            (décision D-1b) : l'usage local reste simple, et le journal nomme
            quelqu'un. Refusé hors de la boucle locale, avec des clés, avec le
            MCP HTTP, et en profil ``prod`` (``LoomConfig.check``).
    """

    host: str = "127.0.0.1"
    port: int = Field(default=8760, ge=1, le=65535)
    base_path: str = ""
    max_body_bytes: PositiveInt = 128 * 1024 * 1024
    docs: bool | None = None
    user: str | None = Field(default=None, pattern=ACCESS_NAME_PATTERN)

    @model_validator(mode="after")
    def _check_base_path(self) -> Self:
        if self.base_path and not self.base_path.startswith("/"):
            raise ValueError("'base_path' doit commencer par '/'")
        return self


class McpAccess(DomainModel):
    """Serveur MCP de l'instance : stdio (``loom mcp``) et HTTP (jalon J5.2b).

    Attributes:
        file_roots: Dossiers où un lien ``file://`` joint à un appel peut être
            lu ; aucun par défaut : les liens ``file://`` sont refusés.
            Relatifs au dossier de la config.
        http: Monte le serveur MCP dans l'application REST, sous
            ``<base_path>/mcp``. Il expose des **outils** à un LLM tiers : il
            exige des clés d'API.
        allowed_origins: Origines acceptées par le transport (spec MCP,
            protection contre le rebinding DNS). Une requête **sans**
            ``Origin`` passe — un client natif n'en envoie pas ; avec un
            ``Origin``, il doit figurer ici.
        allowed_hosts: Hôtes acceptés, **en plus** de l'adresse d'écoute que
            loom ajoute lui-même (``127.0.0.1:*``, ``localhost:*``, et
            ``server.http.host``). À renseigner derrière un nom de domaine ou
            un proxy.
    """

    file_roots: tuple[Path, ...] = ()
    http: bool = False
    allowed_origins: tuple[str, ...] = ()
    allowed_hosts: tuple[str, ...] = ()


class ServerConfig(DomainModel):
    """Serveurs de l'instance (``server``) : HTTP et MCP.

    Attributes:
        http: Serveur HTTP (``loom serve``).
        mcp: Serveur MCP (``loom mcp``, et HTTP sous ``/mcp``).
    """

    http: HttpServer = HttpServer()
    mcp: McpAccess = McpAccess()


class TriggerSpec(DomainModel):
    """Une porte d'entrée déclarée (``triggers``) : ce qu'un appel extérieur ouvre (fonction H6, jalon J5.4c).

    Un déclencheur existe parce que celui qui appelle **ne connaît pas l'API
    de loom** : un planificateur, un CRM, une passerelle de paiement envoient
    leur charge à eux. La config dit donc quoi en faire — quel agent, et quel
    message, rendu depuis la charge reçue.

    Le client vient de la **clé d'API** et de nulle part ailleurs (point 34) : un
    déclencheur ne le nomme pas, sans quoi une clé pourrait lancer un run au
    nom d'un autre. La même liste ``agents`` d'une clé borne ce qu'elle peut
    déclencher, puisqu'un déclencheur nomme son agent.

    Attributes:
        name: Dernier segment de la route (``POST /v1/hooks/<name>``) : un
            identifiant sûr, qui sert aussi de facette au journal.
        agent: Agent lancé.
        message: Gabarit du message, rendu sur ``{payload, trigger,
            delivery}``. Une variable absente donne une chaîne vide (point 50)
            : une charge incomplète ne fait pas échouer la livraison, elle fait
            un message plus pauvre.
        session: Gabarit de la session, pour rattacher les livraisons d'un même
            sujet au même journal. Sans lui, chaque run a sa session, comme
            ailleurs.
        delivery_header: En-tête qui porte l'identifiant de livraison
            (``X-Delivery-Id``, ``Stripe-Id``…). Il devient le ``run_id``, si
            bien qu'une seconde livraison **retrouve** le run au lieu d'en
            ouvrir un autre.
    """

    name: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    agent: str
    message: str
    session: str | None = None
    delivery_header: str | None = None

    @property
    def templates(self) -> tuple[str, ...]:
        """Les gabarits à analyser au chargement."""
        return (self.message,) if self.session is None else (self.message, self.session)


type Profile = Literal["dev", "prod"]
"""Profil de la config : ``dev`` assouplit, ``prod`` durcit."""
PROFILES: Final[tuple[Profile, ...]] = ("dev", "prod")
"""Les profils connus."""

BOOT_FILE: Final = "loom-boot.yaml"
"""Nom de l'amorce que la ligne de commande cherche par défaut (décision A-1)."""
CONFIG_FILE: Final = "loom.yaml"
"""Nom du fichier de config cherché dans ``config.path`` quand l'amorce n'en donne pas (décision A-2)."""
CONFIG_SOURCES: Final = ("file",)
"""Sources de config disponibles : les fichiers seulement pour l'instant (décision A-7).

Une source distante (base de données, URL) pourra s'ajouter sans changer le
format de l'amorce : elle prendra sa place dans le bloc ``config``, sous sa
propre valeur de ``source``.
"""


class FileConfigSource(DomainModel):
    """Config de loom lue dans un fichier local : le bloc ``config`` de l'amorce (``source: file``).

    Exemple ::

        config:
          source: file
          path: ./config
          file: loom.yaml

    Le dossier ``path`` est la racine des documents de la config :
    ``agents_dir``, ``resources_dir`` et les ``schema_file`` s'y rapportent.
    Les chemins de travail, eux, se rapportent à ``workpath`` (décisions A-5
    et A-6).

    Attributes:
        source: ``file``, seule source disponible pour l'instant (décision
            A-7) ; une autre valeur est refusée en la nommant.
        path: Dossier de la config, relatif au dossier de l'amorce ; par
            défaut, celui de l'amorce.
        file: Nom du fichier de config dans ``path``, sans dossier ;
            ``loom.yaml`` par défaut.
    """

    source: Literal["file"]
    path: Path = Path()
    file: str = CONFIG_FILE

    @model_validator(mode="before")
    @classmethod
    def _available(cls, data: object) -> object:
        """Refuse une source indisponible avant tout autre contrôle.

        Sans lui, ``source: postgres`` avec ses paramètres donnerait une
        erreur par clé inconnue avant celle qui compte.

        Raises:
            ValueError: ``source`` n'est pas une source disponible.
        """
        if not isinstance(data, dict):
            return data
        fields = cast("dict[str, object]", data)
        source = fields.get("source")
        if source is not None and source not in CONFIG_SOURCES:
            available = ", ".join(repr(name) for name in CONFIG_SOURCES)
            raise ValueError(f"source {source!r} non disponible : seule la source {available} l'est pour l'instant")
        return fields

    @model_validator(mode="after")
    def _bare_name(self) -> Self:
        """Le nom du fichier, sans dossier : le dossier se donne par ``path``.

        Raises:
            ValueError: ``file`` vide, ``.``, ``..`` ou contenant un dossier.
        """
        name = self.file
        if name in {"", ".", ".."} or Path(name).name != name or "\\" in name:
            raise ValueError(f"'file' : un nom de fichier est attendu, sans dossier ({name!r}) — le dossier se donne par 'path'")
        return self


class BootConfig(DomainModel):
    """L'amorce (``loom-boot.yaml``) : le premier fichier que loom lit, toujours local (décisions A-1 et A-2).

    Elle dit où trouver la config de loom, et porte ce qui doit être connu
    avant de la lire : le profil, choisi sur l'hôte, et le fichier
    d'environnement. Toutes les autres clés sont dans la config.

    Exemple ::

        version: 1
        profile: prod
        env_file: .env
        config:
          source: file
          path: ./config
          file: loom.yaml

    Attributes:
        version: Version du schéma ; 1.
        profile: Profil actif (``dev`` ou ``prod``) ; l'option
            ``--profile`` et ``LOOM_PROFILE`` l'emportent.
        env_file: Fichier d'environnement du projet, relatif au dossier de
            l'amorce ; à défaut, le ``.env`` de ce dossier.
        config: Où lire la config de loom.
    """

    version: int
    profile: Profile | None = None
    env_file: Path | None = None
    config: FileConfigSource

    @model_validator(mode="after")
    def _check(self) -> Self:
        """Version du schéma connue.

        Raises:
            ValueError: Version inconnue.
        """
        if self.version != SCHEMA_VERSION:
            raise ValueError(f"Version d'amorce {self.version!r} non prise en charge (attendue : {SCHEMA_VERSION})")
        return self


class ToolSourceSpec(DomainModel):
    """Une source d'outils fournie par un paquet installé (``tool_sources`` ; jalon J6.4b, conception §7.3).

    ``entry_point`` nomme un point d'entrée du groupe ``loom_ia.tools`` ; loom
    n'importe son paquet que si un agent référence la source. ``params`` est
    remis tel quel à la fabrique du paquet, qui le vérifie. La config ne porte
    pas de secret : la fabrique reçoit la table des secrets du client.

    Attributes:
        name: Nom dans la config (40 caractères au plus) ; il préfixe les
            outils (``carnet__chercher_devis``).
        entry_point: Nom du point d'entrée dans le groupe ``loom_ia.tools``.
        params: Paramètres remis à la fabrique du paquet.
        tools: Déclarations par outil (nom court), sur celles que la source
            annonce.
    """

    name: str = Field(pattern=MCP_NAME_PATTERN, max_length=40)
    entry_point: str = Field(min_length=1, pattern=r"^[^\s=]+$")
    params: dict[str, JsonValue] = Field(default_factory=dict[str, JsonValue])
    tools: dict[str, ToolOverrides] = Field(default_factory=dict[str, ToolOverrides])


class LoomConfig(DomainModel):
    """La configuration d'une instance : ``loom.yaml`` et les agents de ``agents_dir``.

    Chargée par ``load_config`` (profil, variables, fichiers d'agents,
    prompts, skills) ou construite en Python (fonction M2). ``check`` contrôle
    la cohérence d'ensemble : agents, modèles, serveurs MCP, sécurité,
    clients, déclencheurs, state.

    Exemple ::

        version: 1
        models:
          - id: SONNET
            sdk: anthropic
            model: claude-sonnet-5-5
            api_key_env: ANTHROPIC_API_KEY
        storage:
          events: {backend: jsonl, path: .loom/journal}
        security:
          roles: [{id: operateur, permissions: [run, read]}]
          users: [{id: denis, roles: [operateur]}]
          audit: {path: .loom/audit.jsonl}

    Attributes:
        version: Version du schéma ; 1.
        profile: Profil actif (fonction M4) : ``dev`` assouplit, ``prod``
            durcit, absent ne change rien. Posé au chargement d'après
            l'amorce, où l'option et la variable d'environnement l'emportent
            (décision A-2) ; refusé dans le fichier de config. Hors du schéma
            publié.
        profiles: Surcharges partielles par profil, fusionnées au chargement :
            les objets en profondeur, les listes remplacées.
        workpath: Dossier de travail de l'instance (décisions A-4 et A-5),
            obligatoire dans la config lue par l'amorce, quelle que soit la
            source : le journal, les artefacts, l'audit, les données des
            outils, le ``cwd`` des serveurs MCP et les modules Python voisins
            (``imports``, ``module:attr``) s'y rapportent. Un chemin relatif
            se rapporte au dossier de l'amorce ; en conteneur, un chemin
            absolu monté depuis l'hôte (``/loom-data``). Rendu absolu au
            chargement. Facultatif pour une config construite en Python : le
            dossier courant.
        boot_file: L'amorce lue (``loom-boot.yaml``), posée au chargement ;
            ``None`` pour une config construite en Python. Hors du schéma
            publié.
        config_file: Le fichier de config que l'amorce désigne, posé au
            chargement : son dossier est la racine des documents
            (``agents_dir``, ``resources_dir``, ``schema_file``). Hors du
            schéma publié.
        env_file: Fichier d'environnement du projet, posé au chargement
            d'après l'amorce (décision A-2) ; refusé dans le fichier de
            config. Hors du schéma publié.
        imports: Modules chargés au démarrage, qui enregistrent leurs outils et
            politiques (point 50).
        agents_dir: Dossier des fichiers d'agents (``agents`` par défaut), un
            ``<nom>.yaml`` par agent.
        resources_dir: Dossier des prompts et skills (``prompts`` par défaut),
            base des ``system_file`` et ``skill_file``.
        models: Modèles, référencés par leur ``id``.
        mcp_servers: Serveurs MCP, référencés par les agents (point 19).
        tool_sources: Sources d'outils fournies par des paquets installés
            (jalon J6.4b).
        storage: Stockages de la racine.
        sessions: Snapshots d'historique, compaction, médias et state ; l'agent
            interne ``_compaction`` en sort (voir ``all_agents``).
        execution: Exécution des runs et des outils.
        budgets: Budgets par défaut des agents ; un agent les surcharge par son
            ``budget`` (fonction J4).
        telemetry: Logs, capture, masquage, collecteurs.
        tenants: Clients (fonction L1, point 33) : sans cette liste, seul
            ``default`` existe ; avec elle, la liste est fermée et un client
            inconnu est refusé.
        security: Rôles d'accès, utilisateurs, clés d'API, fichier des refus.
        server: Serveurs HTTP et MCP.
        console: Canal vers la console d'administration ; ``None`` : aucun
            (docs/analyse-exortateur-loom.md).
        triggers: Portes d'entrée déclarées (fonction H6) : ce qu'un appel
            extérieur ouvre.
        agents: Les agents : remplis depuis ``agents_dir`` au chargement, ou
            donnés directement en Python.
    """

    version: int
    profile: SkipJsonSchema[Profile | None] = None
    profiles: dict[Profile, dict[str, JsonValue]] = Field(default_factory=dict[Profile, dict[str, JsonValue]])
    workpath: Path | None = None
    boot_file: SkipJsonSchema[Path | None] = None
    config_file: SkipJsonSchema[Path | None] = None
    env_file: SkipJsonSchema[Path | None] = None
    imports: tuple[str, ...] = ()
    agents_dir: Path = Path("agents")
    resources_dir: Path = Path("prompts")
    models: tuple[ModelSpec, ...] = ()
    mcp_servers: tuple[McpServerSpec, ...] = ()
    tool_sources: tuple[ToolSourceSpec, ...] = ()
    storage: StorageConfig = StorageConfig()
    sessions: SessionsConfig = SessionsConfig()
    execution: ExecutionConfig = ExecutionConfig()
    budgets: Budgets = Budgets()
    telemetry: TelemetryConfig = TelemetryConfig()
    tenants: tuple[TenantSpec, ...] = ()
    security: SecurityConfig = SecurityConfig()
    server: ServerConfig = ServerConfig()
    console: ConsoleConfig | None = None
    triggers: tuple[TriggerSpec, ...] = ()
    agents: tuple[AgentSpec, ...] = ()

    @property
    def base_dir(self) -> Path | None:
        """Le dossier de travail (``workpath``) : nom d'usage dans le code de montage.

        Les modules voisins y sont importables, et les chemins de travail
        relatifs (outils, stockage, serveurs MCP) s'y rapportent. ``None``
        pour une config construite en Python sans ``workpath`` : le dossier
        courant.
        """
        return self.workpath

    @property
    def strict(self) -> bool:
        """Vrai en profil ``prod`` : ce qui avertit ailleurs y est une erreur."""
        return self.profile == "prod"

    @property
    def docs_served(self) -> bool:
        """Vrai si ``/docs``, ``/redoc`` et ``/openapi.json`` sont servis (``server.http.docs``)."""
        chosen = self.server.http.docs
        return not self.strict if chosen is None else chosen

    @property
    def lax(self) -> bool:
        """Vrai en profil ``dev`` : ce qui refuse ailleurs y est un avertissement."""
        return self.profile == "dev"

    @model_validator(mode="after")
    def _check(self) -> Self:
        self.check()
        return self

    def check(self) -> None:
        """Contrôles de cohérence (fonction M5).

        Détaché du validateur pour être rejoué sur la configuration résolue
        d'un client : la correspondance des modèles d'un client doit passer
        les mêmes contrôles que la configuration d'origine (fonction L1, point 34).

        Lancés à la construction (validateur), et de nouveau sur la config
        d'un client, sa correspondance des modèles appliquée.

        Raises:
            ValueError: Version inconnue ; agent ``_compaction`` déclaré ;
                serveur MCP, source, modèle, agent, rôle d'accès ou utilisateur
                cité mais non déclaré ; capacité manquante (vision, outils) ;
                sécurité ou audit absents ; ``server.http.user`` mal employé ;
                state au-delà de ses limites ; capture ``content`` sans
                collecteur.
        """
        if self.version != SCHEMA_VERSION:
            raise ValueError(f"Version de config {self.version!r} non prise en charge (attendue : {SCHEMA_VERSION})")
        _reject_doubles("Agent", [agent.name for agent in self.agents])
        if any(agent.name == COMPACTION_AGENT for agent in self.agents):
            raise ValueError(f"Agent {COMPACTION_AGENT!r} : ce nom est réservé à l'agent interne de compaction, produit par 'sessions.compaction'")
        compaction = self.sessions.compaction
        if compaction is not None:
            self._check_chain(f"Compaction ({COMPACTION_AGENT})", (compaction.model,))
        self._check_security()
        self._check_triggers()
        self._check_telemetry()
        if self.server.mcp.http and not self.security.api_keys and (self.security.source is None or self.security.received):
            # Le MCP publie des outils à un LLM tiers, sur le réseau : sans
            # clé, n'importe qui les appellerait. Le stdio, lui, n'a pas de
            # clé, mais c'est le process qui l'a lancé qui décide.
            raise ValueError("'server.mcp.http' expose les agents comme outils : déclarer au moins une clé dans 'security.api_keys' (loom keys create)")
        self._check_tenants()
        self._check_console()
        servers = [server.name for server in self.mcp_servers]
        _reject_doubles("Serveur MCP", servers)
        for agent in self.agents:
            for ref in agent.mcp_tools:
                if ref.mcp not in servers:
                    declared = ", ".join(servers) or "aucun"
                    raise ValueError(f"Agent {agent.name!r} : serveur MCP {ref.mcp!r} non déclaré dans mcp_servers (serveurs : {declared})")
        sources = [source.name for source in self.tool_sources]
        _reject_doubles("Nom de source d'outils", sources)
        # Un même nom dirait deux choses au journal (``tool.source_unavailable``).
        shared = sorted(set(sources) & set(servers))
        if shared:
            raise ValueError(f"Source d'outils et serveur MCP de même nom : {', '.join(shared)} (le journal ne saurait pas lequel est indisponible)")
        for agent in self.agents:
            for ref in agent.source_tools:
                if ref.source not in sources:
                    declared = ", ".join(sources) or "aucune"
                    raise ValueError(f"Agent {agent.name!r} : source d'outils {ref.source!r} non déclarée dans tool_sources (sources : {declared})")
        _reject_doubles("Modèle", [spec.id for spec in self.models])
        for agent in self.agents:
            # Un orchestrateur qui a des outils les appelle : son modèle et ses secours aussi.
            tools = bool(agent.tools or agent.roles or agent.subagents)
            self._check_chain(f"Agent {agent.name!r}", agent.main.chain, tools=tools)
            for role in agent.roles:
                self._check_chain(
                    f"Agent {agent.name!r}, rôle {role.name!r}",
                    role.chain,
                    vision=role.wants_attachments,
                )
            if any(ref.hook == REQUIRE_TOOL for ref in agent.policies):
                # La politique fournie impose un appel d'outil : un modèle qui
                # le refuse la rendrait muette (sa requête part en ``auto``).
                for model in agent.main.chain:
                    if not self.model_spec(model).capabilities.forced_tool_choice:
                        raise ValueError(
                            f"Agent {agent.name!r} : la politique {REQUIRE_TOOL} impose un "
                            f"appel d'outil, que le modèle {model!r} refuse "
                            "(capabilities.forced_tool_choice: false)"
                        )
            for name, _, judge in agent.judges:
                # Le verdict passe par un outil imposé (tool_choice: required), sauf
                # pour un modèle qui le refuse : la consigne seule le demande alors.
                label = f"Agent {agent.name!r}, juge {name!r}"
                self._check_chain(label, judge.chain, vision=judge.wants_attachments, tools=True)
                for model in judge.chain:
                    spec = self.model_spec(model)
                    if spec.sdk == "anthropic" and _thinking({**spec.params, **judge.llm.params}):
                        raise ValueError(
                            f"{label} : le modèle {model!r} a le raisonnement étendu activé "
                            "(params.thinking), que l'API Anthropic refuse avec un outil "
                            "imposé (verdict du juge)"
                        )
        for agent in self.agents:
            self._check_state(agent)
        agents = {agent.name: agent for agent in self.agents}
        for agent in self.agents:
            for ref in agent.subagents:
                if ref.budget_share is not None and not self.budget_of(agent.name).run.limited:
                    raise ValueError(
                        f"Agent {agent.name!r}, sous-agent {ref.tool_name!r} : budget_share demande un budget du run (max_cost, max_tokens ou max_calls)"
                    )
                target = agents.get(ref.agent)
                if target is None:
                    raise ValueError(f"Agent {agent.name!r}, sous-agent {ref.tool_name!r} : agent {ref.agent!r} non déclaré (agents : {', '.join(agents)})")
                if not (ref.description or target.description):
                    raise ValueError(
                        f"Agent {agent.name!r}, sous-agent {ref.tool_name!r} : description manquante (ni dans la référence ni dans l'agent {ref.agent!r})"
                    )

    def _check_state(self, agent: AgentSpec) -> None:
        """Valeurs de la config qui iront dans le state : dans ses limites (décision D7 du state de session).

        Une valeur par défaut est lue à chaque fois que sa clé manque : si les
        valeurs par défaut ne tiennent pas ensemble, le state de la session est
        trop gros avant même la première écriture.
        """
        limits = self.sessions.state
        for key, declared in agent.state.items():
            given = [("default", declared.default)] if declared.default is not None else []
            # Une valeur ``null`` efface la clé : elle ne pèse rien dans le state.
            given += [(f"from, outil {source.tool!r} : value", source.value) for source in declared.sources if source.path is None and source.value is not None]
            for label, value in given:
                size = state_size(value)
                if size > limits.max_value_bytes:
                    raise ValueError(
                        f"Agent {agent.name!r}, state {key!r} : {label} fait {size} octets, "
                        f"au-delà de sessions.state.max_value_bytes ({limits.max_value_bytes})"
                    )
        defaults: dict[str, JsonValue] = {key: declared.default for key, declared in agent.state.items() if declared.default is not None}
        size = state_size(defaults)
        if defaults and size > limits.max_total_bytes:
            raise ValueError(
                f"Agent {agent.name!r}, state : les valeurs par défaut font {size} octets, au-delà de sessions.state.max_total_bytes ({limits.max_total_bytes})"
            )

    def _check_security(self) -> None:
        """Rôles et utilisateurs obligatoires, et tout ce qui les cite déclaré (décisions D-1 à D-5b des utilisateurs et rôles).

        Comme pour les clients, les agents ne sont contrôlés que lorsqu'il y en
        a : la première validation ne connaît pas encore ``agents_dir``.

        En sécurité partagée, rien n'est exigé tant qu'aucune part n'est
        reçue ; une part reçue passe les mêmes contrôles, sauf la borne d'un
        rôle à un agent que l'instance n'a pas (``check_security_references``).
        """
        security = self.security
        shared = security.source == "console"
        if shared:
            self._check_shared()
        elif not security.roles or not security.users:
            missing = " ni ".join(
                name
                for name, given in (
                    ("security.roles", security.roles),
                    ("security.users", security.users),
                )
                if not given
            )
            raise ValueError(f"Aucun {missing} : loom exige au moins un rôle d'accès et un utilisateur. Configuration minimale :\n{MINIMAL_SECURITY}")
        self._check_audit(security.audit)
        if shared and not security.received:
            return
        check_security_references(security, {agent.name: agent.access for agent in self.agents}, shared=shared)
        self._check_local_user({user.id for user in security.users})

    def _check_shared(self) -> None:
        """Sécurité partagée : une console activée, un seul client, pas d'utilisateur local (décisions Q-9 et Q-10 du référentiel).

        Raises:
            ValueError: Pas de section ``console`` activée ; plusieurs clients
                déclarés ; ``server.http.user`` posé.
        """
        console = self.console
        if console is None or not console.enable:
            raise ValueError("security.source: console : la sécurité vient de la console — déclarer une section 'console' avec 'enable: true'")
        if len(self.tenants) > 1:
            raise ValueError(
                f"security.source: console : {len(self.tenants)} clients déclarés ({', '.join(tenant.id for tenant in self.tenants)}) — "
                "la console n'en sert qu'un, les utilisateurs des autres ne pourraient être déclarés nulle part : garder une sécurité locale"
            )
        if self.server.http.user is not None:
            raise ValueError("security.source: console : 'server.http.user' est refusé — les utilisateurs viennent de la console, chacun présente sa clé")

    def _check_audit(self, audit: AuditConfig | None) -> None:
        """Le fichier des refus : obligatoire, et hors des dossiers de clients du journal."""
        if audit is None:
            raise ValueError(f"Aucun security.audit : loom exige le fichier où il écrit les refus d'accès. Par exemple, sous security :\n{MINIMAL_AUDIT}")
        journals = [self.storage.events] + [tenant.storage.events for tenant in self.tenants if tenant.storage is not None]
        for events in journals:
            if events.backend != "jsonl" or events.path is None:
                continue
            # Un ``.jsonl`` dans le dossier d'un client s'y lirait comme une session.
            folder = audit.path.parent
            if folder != events.path and folder.parent == events.path:
                raise ValueError(
                    f"security.audit.path ({audit.path}) est dans le dossier d'un client du "
                    f"journal ({folder}) : il s'y lirait comme "
                    f"une session — le mettre ailleurs, par exemple {events.path / 'audit.jsonl'}"
                )

    def _check_local_user(self, users: set[str]) -> None:
        """``server.http.user`` : une instance locale, sans clé, hors production (décision D-1b des utilisateurs et rôles)."""
        http = self.server.http
        if http.user is None:
            return
        label = f"'server.http.user' ({http.user!r})"
        if http.user not in users:
            raise ValueError(f"{label} : utilisateur non déclaré dans security.users (utilisateurs : {', '.join(sorted(users))})")
        if http.host not in LOOPBACK_HOSTS:
            raise ValueError(f"{label} : réservé à une instance sur la boucle locale ; l'API écoute sur {http.host}, où chacun doit présenter sa clé")
        if self.security.api_keys:
            raise ValueError(
                f"{label} : des clés sont déclarées — une requête sans clé serait refusée, ou passerait au nom de cet utilisateur : choisir l'un ou l'autre"
            )
        if self.server.mcp.http:
            raise ValueError(f"{label} : incompatible avec 'server.mcp.http', qui exige des clés")
        # Le profil prod n'est connu qu'une fois le chargement fini (option,
        # variable, fichier) : c'est le chargeur qui le refuse.

    def _check_triggers(self) -> None:
        """Déclencheurs nommés une fois, sur des agents qui existent, gabarits valides.

        Comme pour les clients, les agents ne sont contrôlés que lorsqu'il y en
        a : la première validation ne connaît pas encore ``agents_dir``.

        Un agent **non publié en REST** est accepté : un déclencheur est une
        porte déclarée, pas l'API ouverte — c'est même la façon de n'ouvrir un
        agent qu'à un planificateur.
        """
        _reject_doubles("Déclencheur", [trigger.name for trigger in self.triggers])
        agents = {agent.name for agent in self.agents}
        for trigger in self.triggers:
            label = f"Déclencheur {trigger.name!r}"
            if agents and trigger.agent not in agents:
                declared = ", ".join(sorted(agents)) or "aucun"
                raise ValueError(f"{label} : agent {trigger.agent!r} non déclaré (agents : {declared})")
            for source in trigger.templates:
                try:
                    Template.parse(source)
                except TemplateError as exc:
                    raise ValueError(f"{label} : {exc}") from exc

    def _check_console(self) -> None:
        """Section ``console`` activée : un client, une passerelle et des types qui existent (docs/analyse-exortateur-loom.md).

        Une section en ``enable: false`` n'est contrôlée que dans sa forme
        (``ConsoleConfig``). Le profil, lui, n'est posé qu'après la validation :
        c'est le chargeur qui refuse ``ws://`` hors profil ``dev``
        (``console.plain_text_refusal``).
        """
        console = self.console
        if console is None or not console.enable:
            return
        if console.scheme == "ws" and console.host not in LOOPBACK_HOSTS:
            raise ValueError(
                f"console.url : ws:// n'est accepté que vers la boucle locale ({', '.join(sorted(LOOPBACK_HOSTS))}), en profil dev ; ici {console.host}"
            )
        declared = {tenant.id for tenant in self.tenants} or {DEFAULT_TENANT}
        if console.tenant not in declared:
            raise ValueError(f"console.tenant {console.tenant!r} : client non déclaré (clients : {', '.join(sorted(declared))})")
        known = {payload.model_fields["type"].default for payload in DURABLE_PAYLOADS}
        unknown = [name for name in console.export.exclude_events if name not in known]
        if unknown:
            raise ValueError(f"console.export.exclude_events : type(s) inconnu(s) : {', '.join(unknown)} (types : {', '.join(sorted(known))})")
        _reject_doubles("console.export.exclude_events : type", list(console.export.exclude_events))
        gateway = console.commands.user
        if gateway is None:
            return
        if self.security.source == "console" and not self.security.received:
            # La passerelle viendra avec la part de la console, contrôlée à sa réception.
            return
        check_gateway(self.security, gateway, console.tenant)

    def _check_tenants(self) -> None:
        """Clients déclarés une fois, sur des agents et des modèles qui existent (fonctions L1 et M5).

        Les agents ne sont contrôlés que lorsqu'il y en a : le chargement
        valide d'abord le fichier racine seul, pour savoir où lire
        ``agents_dir``, et la liste est alors vide. C'est la seconde
        validation, agents lus, qui fait foi.
        """
        _reject_doubles("Client", [tenant.id for tenant in self.tenants])
        agents = {agent.name for agent in self.agents}
        models = {spec.id for spec in self.models}
        for tenant in self.tenants:
            for agent in tenant.agents if agents else ():
                if agent not in agents:
                    raise ValueError(f"Client {tenant.id!r} : agent {agent!r} non déclaré (agents : {', '.join(sorted(agents)) or 'aucun'})")
            for source, target in tenant.models.items():
                for model, which in ((source, "modèle"), (target, "modèle de remplacement")):
                    if model not in models:
                        raise ValueError(f"Client {tenant.id!r} : {which} {model!r} non déclaré (modèles : {', '.join(sorted(models)) or 'aucun'})")
        if not self.tenants:
            return
        declared = {tenant.id for tenant in self.tenants}
        for user in self.security.users:
            if user.tenant not in declared:
                raise ValueError(f"Utilisateur {user.id!r} : client {user.tenant!r} non déclaré (clients : {', '.join(sorted(declared))})")

    def _check_chain(self, label: str, chain: tuple[str, ...], *, vision: bool = False, tools: bool = False) -> None:
        """Modèles d'une chaîne de secours déclarés, avec les capacités exigées (fonction B9, point 10)."""
        ids = [spec.id for spec in self.models]
        for position, model in enumerate(chain):
            which = "modèle" if position == 0 else "modèle de secours"
            if model not in ids:
                raise ValueError(f"{label} : {which} {model!r} non déclaré (modèles connus : {', '.join(ids) or 'aucun'})")
            capabilities = self.model_spec(model).capabilities
            if vision and not capabilities.vision:
                raise ValueError(f"{label} : il reçoit les pièces jointes, mais le {which} {model!r} n'a pas la capacité vision (capabilities.vision: true)")
            if tools and not capabilities.tools:
                raise ValueError(f"{label} : il appelle des outils, mais le {which} {model!r} ne sait pas le faire (capabilities.tools: false)")

    def _check_telemetry(self) -> None:
        """Un contenu capturé doit avoir où aller (jalon J6.1a).

        ``content`` sans collecteur ne fait rien : le journal garde déjà tout,
        et les logs ne portent jamais de contenu. Un réglage qui ne fait rien
        se lit pourtant comme une décision — que le contenu d'un client part
        quelque part —, d'où le refus plutôt que le silence.
        """
        if self.telemetry.exporters:
            return
        wanting = [str(tenant.id) for tenant in self.tenants if tenant.telemetry.capture.exports == "content"]
        if self.telemetry.capture.exports == "content":
            wanting.insert(0, "la racine")
        if wanting:
            raise ValueError(f"Télémétrie : capture 'content' ({', '.join(wanting)}) sans collecteur — déclarer 'telemetry.exporters', ou revenir à 'metadata'")

    @property
    def all_agents(self) -> tuple[AgentSpec, ...]:
        """Agents déclarés, plus l'agent interne de compaction s'il est configuré."""
        compaction = self.sessions.compaction
        if compaction is None:
            return self.agents
        return (*self.agents, compaction_agent(compaction))

    def budget_of(self, agent: str) -> Budgets:
        """Budgets d'un agent : ceux de la racine, surchargés par son ``budget``.

        Args:
            agent: L'agent.
        """
        spec = next((a for a in self.agents if a.name == agent), None)
        return self.budgets.merged(spec.budget if spec is not None else None)

    @property
    def tenant_ids(self) -> tuple[TenantId, ...]:
        """Clients déclarés ; ``default`` seul quand la config n'en nomme aucun (point 33)."""
        if not self.tenants:
            return (DEFAULT_TENANT,)
        return tuple(tenant.id for tenant in self.tenants)

    def tenant_spec(self, tenant_id: TenantId) -> TenantSpec | None:
        """Fiche d'un client déclaré ; ``None`` s'il ne surcharge rien.

        Args:
            tenant_id: Le client.
        """
        for tenant in self.tenants:
            if tenant.id == tenant_id:
                return tenant
        return None

    def retention_days(self, tenant_id: TenantId) -> int | None:
        """Borne de rétention de ce client : la sienne, sinon celle de la racine.

        ``None`` : ce client ne perd rien. Un client peut aussi **annuler** la
        règle commune en déclarant ``retention: {events_days: null}``, ce qui se
        lit comme ce que c'est — une exception assumée, écrite noir sur blanc.

        Args:
            tenant_id: Le client.

        Returns:
            Les jours de ``events_days`` ; ``None`` : rien ne s'efface.
        """
        spec = self.tenant_spec(tenant_id)
        if spec is not None and spec.retention is not None:
            return spec.retention.events_days
        return self.storage.retention.events_days

    def capture_for(self, tenant_id: TenantId) -> CaptureConfig:
        """Capture des exports pour ce client : la sienne, clé par clé, sur celle de la racine.

        Args:
            tenant_id: Le client.
        """
        root = self.telemetry.capture
        spec = self.tenant_spec(tenant_id)
        return root if spec is None else spec.telemetry.capture.over(root)

    def tool_source(self, name: str) -> ToolSourceSpec:
        """Définition d'une source d'outils par son nom.

        Args:
            name: Nom de la source.

        Raises:
            KeyError: Source non déclarée.
        """
        for source in self.tool_sources:
            if source.name == name:
                return source
        raise KeyError(name)

    def mcp_server(self, name: str) -> McpServerSpec:
        """Définition d'un serveur MCP par son nom.

        Args:
            name: Nom du serveur.

        Raises:
            KeyError: Serveur non déclaré.
        """
        for server in self.mcp_servers:
            if server.name == name:
                return server
        raise KeyError(name)

    def model_spec(self, model_id: str) -> ModelSpec:
        """Définition d'un modèle par son identifiant.

        Args:
            model_id: Identifiant du modèle (``id``).

        Raises:
            KeyError: Modèle non déclaré.
        """
        for spec in self.models:
            if spec.id == model_id:
                return spec
        raise KeyError(model_id)


def _thinking(params: dict[str, JsonValue]) -> bool:
    """Vrai si les réglages activent le raisonnement étendu d'Anthropic (``thinking``)."""
    value = params.get("thinking")
    return isinstance(value, dict) and value.get("type") not in (None, "disabled")


def _reject_doubles(kind: str, names: list[str]) -> None:
    doubles = sorted({name for name in names if names.count(name) > 1})
    if doubles:
        raise ValueError(f"{kind} déclaré deux fois : {', '.join(doubles)}")


def check_security_references(security: SecurityConfig, agents: Mapping[str, AgentAccess], *, shared: bool = False) -> None:
    """Tout ce que la sécurité cite est déclaré, et ses règles tiennent (décisions D-3 à D-5b des utilisateurs et rôles).

    Appelé par ``LoomConfig.check`` sur la section ``security``, et sur une part
    de la console avant de l'appliquer (``console/shared.py``) — la console
    l'appelle aussi avant d'enregistrer une modification, avec les blocs
    ``access`` qu'elle a reçus de chaque instance.

    Args:
        security: La sécurité à contrôler.
        agents: Le bloc ``access`` de chaque agent de l'instance ; vide :
            agents pas encore lus, leurs contrôles sont sautés.
        shared: Sécurité partagée : un rôle borné à un agent que l'instance
            n'a pas est accepté (le référentiel sert plusieurs instances).

    Raises:
        ValueError: Doublon ; rôle, utilisateur ou agent cité mais non déclaré ;
            endossement en chaîne ; liste ``access`` qui cite un rôle sans la
            permission, ou borné à d'autres agents.
    """
    _reject_doubles("Rôle d'accès", [role.id for role in security.roles])
    _reject_doubles("Utilisateur", [user.id for user in security.users])
    _reject_doubles("Clé", [key.id for key in security.api_keys])
    roles = {role.id: role for role in security.roles}
    permissions = {role.id: role.permissions for role in security.roles}

    def undeclared(label: str, role: str) -> ValueError:
        return ValueError(f"{label} : rôle d'accès {role!r} non déclaré dans security.roles (rôles : {', '.join(roles)})")

    def declared(label: str, role: str) -> AccessRole:
        found = roles.get(role)
        if found is None:
            raise undeclared(label, role)
        return found

    for role in security.roles:
        label = f"Rôle d'accès {role.id!r}"
        for agent in role.agents if agents and not shared else ():
            if agent not in agents:
                raise ValueError(f"{label} : agent {agent!r} non déclaré (agents : {', '.join(sorted(agents)) or 'aucun'})")
        for code, endorsed in endorsement_problems(role.act_as_roles, permissions):
            if code == "endorsed_undeclared":
                raise undeclared(f"{label}, act_as_roles", endorsed)
            # Endosser un rôle qui endosse à son tour : une passerelle
            # agirait au nom de quelqu'un au nom de quelqu'un d'autre.
            raise ValueError(f"{label}, act_as_roles : {endorsed!r} a lui-même 'act_as' — on n'agit pas au nom d'autrui en chaîne")
    for user in security.users:
        for code, role in user_problems(user.roles, permissions):
            if code == "role_undeclared" and role is not None:
                raise undeclared(f"Utilisateur {user.id!r}", role)
    users = {user.id for user in security.users}
    for key in security.api_keys:
        if key.user not in users:
            raise ValueError(f"Clé {key.id!r} : utilisateur {key.user!r} non déclaré dans security.users (utilisateurs : {', '.join(sorted(users))})")
    for name, access in agents.items():
        for permission, cited in access.listed():
            for role_id in cited:
                label = f"Agent {name!r}, access"
                role = declared(label, role_id)
                if permission not in role.permissions:
                    # Une liste ne donne rien : elle restreint ceux qui ont
                    # déjà la permission. Un rôle sans elle n'y ferait rien.
                    raise ValueError(f"{label} : le rôle {role_id!r} est cité pour {permission!r}, permission qu'il n'a pas")
                if not role.allows(name):
                    raise ValueError(f"{label} : le rôle {role_id!r} est borné à d'autres agents ({', '.join(role.agents)})")


def check_gateway(security: SecurityConfig, gateway: str, tenant: str) -> None:
    """La passerelle des commandes (``console.commands.user``) : déclarée, du client de la console, avec ``act_as``.

    Args:
        security: La sécurité qui doit la déclarer.
        gateway: Son identifiant.
        tenant: Le client que la console sert.

    Raises:
        ValueError: Utilisateur non déclaré, d'un autre client, ou sans rôle qui a ``act_as``.
    """
    label = f"console.commands.user ({gateway!r})"
    users = {user.id: user for user in security.users}
    if gateway not in users:
        raise ValueError(f"passerelle absente : {label} : utilisateur non déclaré dans security.users (utilisateurs : {', '.join(sorted(users)) or 'aucun'})")
    if users[gateway].tenant != tenant:
        raise ValueError(f"{label} : il est chez le client {users[gateway].tenant!r}, la console sert {tenant!r}")
    if not any("act_as" in role.permissions for role in security.roles_of(gateway)):
        raise ValueError(f"{label} : aucun de ses rôles n'a 'act_as' — la passerelle agit au nom des utilisateurs de la console (act_as_roles)")
