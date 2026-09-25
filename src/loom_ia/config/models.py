# SPDX-License-Identifier: Apache-2.0
"""Schéma de la configuration (M1, M2, #35, #50).

Les modèles Pydantic sont le schéma : le YAML n'est qu'une façon de les
remplir, et une configuration écrite en Python (M2) les construit
directement. Tout champ inconnu est refusé ; une clé prévue pour une phase
suivante donne une erreur qui nomme cette phase.

Sous-ensemble des jalons J1 et J2 ; le schéma complet est dans
``docs/conception.md`` §17.
"""

import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Final, Literal, Self

from pydantic import (
    AwareDatetime,
    Field,
    JsonValue,
    PositiveFloat,
    PositiveInt,
    model_validator,
)

from loom_ia.agents.spec import AGENT_NAME_PATTERN, AgentSpec
from loom_ia.config.compaction import COMPACTION_AGENT, CompactionConfig, compaction_agent
from loom_ia.config.keys import ALGORITHM, matches
from loom_ia.config.later import (
    LATER_API_KEY,
    LATER_MCP_ACCESS,
    LATER_ROOT,
    LATER_STORAGE,
    LATER_TELEMETRY,
    LATER_TENANT,
)
from loom_ia.core.model import (
    DEFAULT_TENANT,
    Approval,
    AttachmentPolicy,
    Budgets,
    DomainModel,
    McpServerSpec,
    ModelSpec,
    Quotas,
    RateLimit,
    TenantId,
    reject_later,
)
from loom_ia.core.template import Template, TemplateError
from loom_ia.telemetry.logs import LogFormat

SCHEMA_VERSION: Final = 1
EVENT_BACKENDS: Final = ("memory", "jsonl", "sqlite", "postgres")
IDEMPOTENCY_BACKENDS: Final = ("journal", "memory", "sqlite", "postgres", "redis")
# Magasins qu'une clé métier peut exiger : partagés entre runs **et**
# durables. Le journal ne voit que son run, la mémoire que son process.
SHARED_IDEMPOTENCY: Final = ("sqlite", "postgres", "redis")
# Journaux rangés hors de la mémoire : ils donnent aussi le dossier des artefacts.
FILE_BACKENDS: Final = ("jsonl", "sqlite")
# Journaux qui survivent au process : ce qu'exige une approbation (#28).
DURABLE_BACKENDS: Final = ("jsonl", "sqlite", "postgres")
# Stockages dont le raccordement passe par un DSN, jamais par un chemin.
DSN_BACKENDS: Final = ("postgres",)
QUEUE_BACKENDS: Final = ("asyncio", "rabbitmq")
BUS_BACKENDS: Final = ("memory", "postgres", "redis")
# Bus qui franchissent la frontière d'un process : eux seuls servent à
# quelque chose quand le service tourne à plusieurs.
SHARED_BUSES: Final = ("postgres", "redis")
# Files servies par un courtier : les tâches tournent dans un autre process,
# celui de ``loom worker``, et pas dans celui qui les met en file.
BROKERED_QUEUES: Final = ("rabbitmq",)
# Rôle applicatif Postgres par défaut, celui que crée le DDL de loom. Écrit
# ici plutôt qu'importé : la config ne dépend pas d'un pilote de base.
DEFAULT_DB_ROLE: Final = "loom_app"
# Dossier des artefacts sous celui du journal, quand la config n'en donne pas.
ARTIFACTS_SUBDIR: Final = ".artifacts"


class EventsStorage(DomainModel):
    backend: str = "memory"
    # ``jsonl`` : dossier des journaux. ``sqlite`` : fichier de la base.
    # Relatif au fichier de config.
    path: Path | None = None
    # ``postgres`` : **nom** de la variable d'environnement qui porte le DSN.
    # La config ne porte jamais un secret, seulement où le lire (§16.3).
    dsn_env: str | None = None
    # Rôle applicatif pris par chaque connexion : c'est à lui que s'applique
    # la politique de lignes, et il n'a pas le droit de modifier un événement
    # écrit. ``null`` s'en passe — la politique tient encore, par ``FORCE``,
    # mais le journal redevient modifiable.
    role: str | None = DEFAULT_DB_ROLE

    @model_validator(mode="after")
    def _check_backend(self) -> Self:
        if self.backend not in EVENT_BACKENDS:
            raise ValueError(
                f"Journal {self.backend!r} : seuls {', '.join(EVENT_BACKENDS)} "
                "sont disponibles à ce jalon"
            )
        if self.backend in FILE_BACKENDS and self.path is None:
            raise ValueError(f"Journal {self.backend!r} : 'path' est obligatoire")
        if self.backend in DSN_BACKENDS:
            if self.dsn_env is None:
                raise ValueError(
                    f"Journal {self.backend!r} : 'dsn_env' est obligatoire — le nom de la "
                    "variable d'environnement qui porte le DSN, jamais le DSN lui-même"
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
    """Stockage des fichiers : pièces jointes, sorties d'outils, résultats déportés (G2).

    Sans ``backend``, il suit le journal : dossier ``.artifacts`` sous celui
    d'un journal ``jsonl``, mémoire pour un journal ``memory``.
    """

    backend: Literal["local", "memory"] | None = None
    # Dossier du stockage ``local``, relatif au fichier de config.
    path: Path | None = None


class IdempotencyStorage(DomainModel):
    """Magasin des clés d'idempotence (#18, #49).

    ``journal`` n'a pas de stockage propre : chaque run garde ses
    enregistrements dans son journal, ce qui suffit aux clés techniques d'un
    appel. ``memory`` voit tout un process, mais ne survit pas à sa
    fermeture. Une clé **métier** exige d'être vue de partout et de durer :
    seul ``sqlite`` s'en charge, et le chargement le vérifie.
    """

    backend: str = "journal"
    # Fichier de la base ``sqlite``, relatif au fichier de config. Sa propre
    # base : ses écritures ne se disputent pas le verrou du journal.
    path: Path | None = None
    # ``postgres`` : nom de la variable qui porte le DSN. Peut être celle du
    # journal — les deux tables cohabitent sans se gêner, Postgres ne se
    # verrouille pas par fichier.
    dsn_env: str | None = None
    # ``redis`` : nom de la variable qui porte l'URL du serveur.
    url_env: str | None = None
    role: str | None = DEFAULT_DB_ROLE

    @model_validator(mode="after")
    def _check_backend(self) -> Self:
        if self.backend not in IDEMPOTENCY_BACKENDS:
            raise ValueError(
                f"Magasin d'idempotence {self.backend!r} : seuls "
                f"{', '.join(IDEMPOTENCY_BACKENDS)} sont disponibles à ce jalon"
            )
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
                "Magasin d'idempotence 'redis' : 'url_env' est obligatoire — le nom de la "
                "variable d'environnement qui porte l'URL, jamais l'URL elle-même"
            )
        if self.backend != "redis" and self.url_env is not None:
            raise ValueError(f"Magasin d'idempotence {self.backend!r} : 'url_env' n'a pas de sens")
        return self

    @property
    def shared(self) -> bool:
        """Vrai si ce magasin est vu de tous les runs et survit au process (#49)."""
        return self.backend in SHARED_IDEMPOTENCY


class QueueStorage(DomainModel):
    """File des tâches de fond : runs soumis, reprises, résumés (#27).

    ``asyncio`` exécute dans le process qui met en file : c'est le mode
    librairie, et la durabilité vient du journal, pas de la file. ``rabbitmq``
    ne fait que **publier** — les tâches tournent dans les workers
    (``loom worker``), et un process qui met en file sans worker derrière voit
    ses tâches attendre.
    """

    backend: str = "asyncio"
    # ``rabbitmq`` : nom de la variable d'environnement qui porte l'URL du
    # courtier (``amqp://…``). La config ne porte jamais un secret (§16.3).
    url_env: str | None = None

    @model_validator(mode="after")
    def _check_backend(self) -> Self:
        if self.backend not in QUEUE_BACKENDS:
            raise ValueError(
                f"File {self.backend!r} : seuls {', '.join(QUEUE_BACKENDS)} "
                "sont disponibles à ce jalon"
            )
        if self.backend == "rabbitmq" and self.url_env is None:
            raise ValueError(
                "File 'rabbitmq' : 'url_env' est obligatoire — le nom de la variable "
                "d'environnement qui porte l'URL du courtier, jamais l'URL elle-même"
            )
        if self.backend != "rabbitmq" and self.url_env is not None:
            raise ValueError(f"File {self.backend!r} : 'url_env' n'a pas de sens")
        return self

    @property
    def brokered(self) -> bool:
        """Vrai si les tâches tournent dans un autre process que celui qui les met en file."""
        return self.backend in BROKERED_QUEUES


class BusStorage(DomainModel):
    """Bus des nouvelles d'écriture, entre les process d'un même service (#5).

    ``memory`` ne franchit aucune frontière : dans un seul process, le journal
    remet déjà ses écritures à ses abonnés. Les deux autres servent dès qu'un
    `loom serve` doit suivre un run piloté par un `loom worker`.
    """

    backend: str = "memory"
    # ``postgres`` : nom de la variable qui porte le DSN — celle du journal si
    # c'est la même base. ``redis`` : nom de la variable qui porte l'URL.
    dsn_env: str | None = None
    url_env: str | None = None

    @model_validator(mode="after")
    def _check_backend(self) -> Self:
        if self.backend not in BUS_BACKENDS:
            raise ValueError(
                f"Bus {self.backend!r} : seuls {', '.join(BUS_BACKENDS)} "
                "sont disponibles à ce jalon"
            )
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
                f"Bus {self.backend!r} : {attendu!r} est obligatoire — le nom de la variable "
                f"d'environnement qui porte {porte}, jamais {porte} elle-même"
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
    """Sceau des contenus au repos, avec la clé de chaque client (#30, §11.5).

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
    """

    keys: Annotated[tuple[str, ...], Field(min_length=1)]


class StorageConfig(DomainModel):
    events: EventsStorage = EventsStorage()
    artifacts: ArtifactsStorage = ArtifactsStorage()
    idempotency: IdempotencyStorage = IdempotencyStorage()
    queue: QueueStorage = QueueStorage()
    bus: BusStorage = BusStorage()
    # Sans ce bloc, les contenus sont rangés en clair : c'est le journal de
    # toujours, et l'isolation par client, les portées de clés et la
    # suppression RGPD restent ce qui les protège.
    encryption: EncryptionStorage | None = None

    @model_validator(mode="before")
    @classmethod
    def _later(cls, data: object) -> object:
        reject_later(data, LATER_STORAGE)
        return data

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
            raise ValueError(
                "Artefacts 'local' : 'path' est obligatoire quand le journal n'est pas en fichiers"
            )
        if self.events.backend in DSN_BACKENDS and artifacts.backend is None:
            # Un journal durable qui laisserait les artefacts en mémoire
            # garderait la trace de fichiers disparus à la fermeture : le
            # piège est trop silencieux pour un défaut.
            raise ValueError(
                f"Journal {self.events.backend!r} : déclarer 'storage.artifacts' — 'local' "
                "avec son 'path', ou 'memory' si les artefacts n'ont pas à survivre"
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


class SessionsConfig(DomainModel):
    """Vie d'une session : historique matérialisé et compaction (F1 à F4)."""

    # Événements ajoutés depuis le dernier marqueur avant qu'un snapshot de
    # l'historique soit écrit (§11.2). Le relire coûte moins que de rejouer
    # ce qu'il couvre ; l'écrire recopie l'historique dans le journal.
    snapshot_every: PositiveInt = 50
    # Sans ce bloc, une session n'est jamais résumée : elle grandit jusqu'à
    # la fenêtre du modèle.
    compaction: CompactionConfig | None = None


class ToolsExecution(DomainModel):
    # Délai par défaut d'un outil ; ``null`` retire la limite.
    timeout: PositiveFloat | None = 30.0
    validate_arguments: bool = True
    # Au-delà, en caractères, le résultat est déporté (#16) ; ``null`` le désactive.
    offload_over: PositiveInt | None = 50_000


class ExecutionConfig(DomainModel):
    tools: ToolsExecution = ToolsExecution()
    # Pièces jointes acceptées à l'entrée d'un run (G1) : images, taille maximale.
    attachments: AttachmentPolicy = AttachmentPolicy()
    # Délai laissé aux tâches de fond (compaction) à la fermeture de l'instance.
    shutdown_timeout: PositiveFloat = 30.0
    # Durée de la concession prise sur un run par l'instance qui le pilote
    # (#27), en secondes ; elle est renouvelée au tiers tant qu'il tourne.
    # Passée, un autre worker peut reprendre le run — ce qui n'arrive que si
    # le porteur est mort.
    lease: PositiveFloat = 60.0


class LoggingConfig(DomainModel):
    level: str = "INFO"
    format: LogFormat = "console"

    @model_validator(mode="after")
    def _check_level(self) -> Self:
        known = logging.getLevelNamesMapping()
        if self.level.upper() not in known:
            raise ValueError(
                f"Niveau de log {self.level!r} inconnu (attendus : {', '.join(known)})"
            )
        return self


class TelemetryConfig(DomainModel):
    logging: LoggingConfig = LoggingConfig()

    @model_validator(mode="before")
    @classmethod
    def _later(cls, data: object) -> object:
        reject_later(data, LATER_TELEMETRY)
        return data


class TenantSpec(DomainModel):
    """Un client, et la liste fermée de ce qu'il surcharge (L1, #33, #34, §17.8).

    Tout le reste de la configuration lui est commun : mêmes agents, mêmes
    prompts, mêmes outils. Un client ne redéfinit que ce qui le distingue —
    les modèles qu'il paie, les secrets qu'il apporte, ce qu'on lui permet.
    Les prompts ne sont **pas** surchargeables (§6) : seules les variables
    qu'ils citent le sont, ce qui garde la logique métier dans un seul
    endroit.
    """

    id: TenantId
    # Agents que ce client peut lancer ; vide signifie tous ceux de la config.
    agents: tuple[str, ...] = ()
    # Outils retirés à ce client, sous le nom que voit le modèle (préfixe MCP
    # compris) : un rôle, un sous-agent ou un outil qu'on ne lui ouvre pas.
    tools_deny: tuple[str, ...] = ()
    # Correspondance des modèles : {modèle de la config: modèle de ce client}.
    # Elle vaut partout — orchestrateur, rôles, juges, chaînes de secours,
    # compaction —, la cible devant être déclarée dans ``models``.
    models: dict[str, str] = Field(default_factory=dict[str, str])
    # Approbation imposée par outil, quoi qu'en dise sa déclaration (#17) :
    # c'est le client qui sait ce qui l'engage.
    approvals: dict[str, Approval] = Field(default_factory=dict[str, Approval])
    # Secrets : {nom attendu par la config: variable d'environnement de ce
    # client}. Ce qui n'y est pas reste lu dans l'environnement commun.
    secrets: dict[str, str] = Field(default_factory=dict[str, str])
    # Variables citées par les prompts système ({{ entreprise }}).
    variables: dict[str, JsonValue] = Field(default_factory=dict[str, JsonValue])
    # Budgets de ce client : ils surchargent ceux de la racine clé par clé, et
    # portent en plus ses plafonds par période (``tenant``, J5.1b).
    budgets: Budgets | None = None
    # Débit accordé à ce client, quel que soit l'accès emprunté (L3).
    quotas: Quotas = Quotas()
    # Stockage propre à ce client (isolation physique, ``TenantRouter``) ;
    # sans lui, celui de la racine, où seul le ``tenant_id`` le distingue.
    storage: StorageConfig | None = None

    @model_validator(mode="before")
    @classmethod
    def _later(cls, data: object) -> object:
        reject_later(data, LATER_TENANT)
        return data

    @model_validator(mode="after")
    def _check(self) -> Self:
        if not self.id.strip():
            raise ValueError("Client : 'id' ne peut pas être vide")
        for agent in self.agents:
            if not re.fullmatch(AGENT_NAME_PATTERN, agent):
                raise ValueError(f"Client {self.id!r} : nom d'agent invalide : {agent!r}")
        for source, target in self.models.items():
            if source == target:
                raise ValueError(
                    f"Client {self.id!r} : le modèle {source!r} se remplace par lui-même"
                )
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
        return self

    def allows(self, agent: str) -> bool:
        return not self.agents or agent in self.agents


type Scope = Literal["run", "read", "read_content", "approve", "admin"]


class ApiKey(DomainModel):
    """Clé déclarée dans la config, par son empreinte seulement (#39)."""

    id: str = Field(min_length=1)
    # Empreinte ``sha256:…`` donnée par ``loom keys create``.
    hash: str
    # Client au nom duquel cette clé agit (L1) ; ``default`` en mono-client.
    tenant: TenantId = DEFAULT_TENANT
    # Débit de cette clé, vérifié par l'accès HTTP (#39) ; ``null`` : aucun.
    rate_limit: RateLimit | None = None
    # Fin de validité (J5.2a) ; ``null`` : la clé ne périme pas. Une clé déjà
    # expirée n'empêche pas le démarrage — elle est refusée à l'appel, et
    # ``loom validate`` la signale. Une rotation, c'est deux clés déclarées en
    # même temps, l'ancienne portant sa date de fin.
    expires: AwareDatetime | None = None
    scopes: tuple[Scope, ...] = ("run", "read")
    # Agents autorisés ; vide signifie tous.
    agents: tuple[str, ...] = ()

    @model_validator(mode="before")
    @classmethod
    def _later(cls, data: object) -> object:
        reject_later(data, LATER_API_KEY)
        return data

    @model_validator(mode="after")
    def _check_hash(self) -> Self:
        if not self.hash.startswith(f"{ALGORITHM}:"):
            raise ValueError(f"Empreinte de clé attendue sous la forme '{ALGORITHM}:…'")
        return self

    def accepts(self, key: str) -> bool:
        return matches(key, self.hash)

    def expired(self, now: datetime | None = None) -> bool:
        """Vrai si la clé a passé sa date de fin."""
        if self.expires is None:
            return False
        return (now or datetime.now(UTC)) >= self.expires

    def allows(self, agent: str) -> bool:
        return not self.agents or agent in self.agents


class SecurityConfig(DomainModel):
    api_keys: tuple[ApiKey, ...] = ()


class HttpServer(DomainModel):
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    # Préfixe commun des routes, par exemple ``/loom``.
    base_path: str = ""

    @model_validator(mode="after")
    def _check_base_path(self) -> Self:
        if self.base_path and not self.base_path.startswith("/"):
            raise ValueError("'base_path' doit commencer par '/'")
        return self


class McpAccess(DomainModel):
    """Serveur MCP de l'instance : stdio (``loom mcp``) et HTTP (J5.2b)."""

    # Dossiers où un lien ``file://`` joint à un appel peut être lu ; aucun par
    # défaut : les liens ``file://`` sont refusés. Relatifs au dossier de la config.
    file_roots: tuple[Path, ...] = ()
    # Monte le serveur MCP dans l'application REST, sous ``<base_path>/mcp``.
    # Il expose des **outils** à un LLM tiers : il exige des clés d'API.
    http: bool = False
    # Origines acceptées par le transport (spec MCP, protection contre le
    # rebinding DNS). Une requête **sans** ``Origin`` passe — un client natif
    # n'en envoie pas ; avec un ``Origin``, il doit figurer ici.
    allowed_origins: tuple[str, ...] = ()
    # Hôtes acceptés, **en plus** de l'adresse d'écoute que loom ajoute lui-même
    # (``127.0.0.1:*``, ``localhost:*``, et ``server.http.host``). À renseigner
    # derrière un nom de domaine ou un proxy.
    allowed_hosts: tuple[str, ...] = ()

    @model_validator(mode="before")
    @classmethod
    def _later(cls, data: object) -> object:
        reject_later(data, LATER_MCP_ACCESS)
        return data


class ServerConfig(DomainModel):
    http: HttpServer = HttpServer()
    mcp: McpAccess = McpAccess()


class TriggerSpec(DomainModel):
    """Une porte d'entrée déclarée : ce qu'un appel extérieur ouvre (H6, J5.4c).

    Un déclencheur existe parce que celui qui appelle **ne connaît pas l'API
    de loom** : un planificateur, un CRM, une passerelle de paiement envoient
    leur charge à eux. La config dit donc quoi en faire — quel agent, et quel
    message, rendu depuis la charge reçue.

    Le client vient de la **clé d'API** et de nulle part ailleurs (#34) : un
    déclencheur ne le nomme pas, sans quoi une clé pourrait lancer un run au
    nom d'un autre. La même liste ``agents`` d'une clé borne ce qu'elle peut
    déclencher, puisqu'un déclencheur nomme son agent.
    """

    # Dernier segment de la route (``POST /v1/hooks/<name>``) : un identifiant
    # sûr, qui sert aussi de facette au journal.
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    agent: str
    # Gabarit du message, rendu sur ``{payload, trigger, delivery}``. Une
    # variable absente donne une chaîne vide (#50) : une charge incomplète ne
    # fait pas échouer la livraison, elle fait un message plus pauvre.
    message: str
    # Gabarit de la session, pour rattacher les livraisons d'un même sujet au
    # même journal. Sans lui, chaque run a sa session, comme ailleurs.
    session: str | None = None
    # En-tête qui porte l'identifiant de livraison (``X-Delivery-Id``,
    # ``Stripe-Id``…). Il devient le ``run_id``, si bien qu'une seconde
    # livraison **retrouve** le run au lieu d'en ouvrir un autre.
    delivery_header: str | None = None

    @property
    def templates(self) -> tuple[str, ...]:
        """Les gabarits à analyser au chargement."""
        return (self.message,) if self.session is None else (self.message, self.session)


type Profile = Literal["dev", "prod"]
PROFILES: Final[tuple[Profile, ...]] = ("dev", "prod")


class LoomConfig(DomainModel):
    version: int
    # Profil actif (M4) : `dev` assouplit, `prod` durcit, absent ne change
    # rien. Posé au chargement, où l'option et la variable d'environnement
    # l'emportent sur le fichier.
    profile: Profile | None = None
    # Surcharges partielles par profil, fusionnées au chargement : les objets
    # en profondeur, les listes remplacées.
    profiles: dict[Profile, dict[str, JsonValue]] = Field(
        default_factory=dict[Profile, dict[str, JsonValue]]
    )
    # Dossier du fichier de config, posé au chargement : les modules voisins
    # sont importables et les chemins relatifs s'y rapportent.
    base_dir: Path | None = None
    # Modules chargés au démarrage, qui enregistrent leurs outils (#50).
    imports: tuple[str, ...] = ()
    agents_dir: Path = Path("agents")
    prompts_dir: Path = Path("prompts")
    models: tuple[ModelSpec, ...] = ()
    # Serveurs MCP, référencés par les agents (#19).
    mcp_servers: tuple[McpServerSpec, ...] = ()
    storage: StorageConfig = StorageConfig()
    # Snapshots d'historique et compaction ; l'agent interne ``_compaction``
    # en sort (voir ``all_agents``).
    sessions: SessionsConfig = SessionsConfig()
    execution: ExecutionConfig = ExecutionConfig()
    # Budgets par défaut des agents ; un agent les surcharge par son ``budget`` (J4).
    budgets: Budgets = Budgets()
    telemetry: TelemetryConfig = TelemetryConfig()
    # Clients (L1, #33) : sans cette liste, seul ``default`` existe ; avec
    # elle, la liste est fermée et un client inconnu est refusé.
    tenants: tuple[TenantSpec, ...] = ()
    security: SecurityConfig = SecurityConfig()
    server: ServerConfig = ServerConfig()
    # Portes d'entrée déclarées (H6) : ce qu'un appel extérieur ouvre.
    triggers: tuple[TriggerSpec, ...] = ()
    # Remplis depuis ``agents_dir`` au chargement, ou donnés directement en Python.
    agents: tuple[AgentSpec, ...] = ()

    @model_validator(mode="before")
    @classmethod
    def _later(cls, data: object) -> object:
        reject_later(data, LATER_ROOT)
        return data

    @property
    def strict(self) -> bool:
        """Vrai en profil ``prod`` : ce qui avertit ailleurs y est une erreur."""
        return self.profile == "prod"

    @property
    def lax(self) -> bool:
        """Vrai en profil ``dev`` : ce qui refuse ailleurs y est un avertissement."""
        return self.profile == "dev"

    @model_validator(mode="after")
    def _check(self) -> Self:
        self.check()
        return self

    def check(self) -> None:
        """Contrôles de cohérence (M5).

        Détaché du validateur pour être rejoué sur la configuration résolue
        d'un client : la correspondance des modèles d'un client doit passer
        les mêmes contrôles que la configuration d'origine (L1, #34).
        """
        if self.version != SCHEMA_VERSION:
            raise ValueError(
                f"Version de config {self.version!r} non prise en charge "
                f"(attendue : {SCHEMA_VERSION})"
            )
        _reject_doubles("Agent", [agent.name for agent in self.agents])
        if any(agent.name == COMPACTION_AGENT for agent in self.agents):
            raise ValueError(
                f"Agent {COMPACTION_AGENT!r} : ce nom est réservé à l'agent interne de "
                "compaction, produit par 'sessions.compaction'"
            )
        compaction = self.sessions.compaction
        if compaction is not None:
            self._check_chain(f"Compaction ({COMPACTION_AGENT})", (compaction.model,))
        _reject_doubles("Clé", [key.id for key in self.security.api_keys])
        self._check_triggers()
        if self.server.mcp.http and not self.security.api_keys:
            # Le MCP publie des outils à un LLM tiers, sur le réseau : sans
            # clé, n'importe qui les appellerait. Le stdio, lui, n'a pas de
            # clé, mais c'est le process qui l'a lancé qui décide.
            raise ValueError(
                "'server.mcp.http' expose les agents comme outils : déclarer au moins une "
                "clé dans 'security.api_keys' (loom keys create)"
            )
        self._check_tenants()
        servers = [server.name for server in self.mcp_servers]
        _reject_doubles("Serveur MCP", servers)
        for agent in self.agents:
            for ref in agent.mcp_tools:
                if ref.mcp not in servers:
                    declared = ", ".join(servers) or "aucun"
                    raise ValueError(
                        f"Agent {agent.name!r} : serveur MCP {ref.mcp!r} non déclaré "
                        f"dans mcp_servers (serveurs : {declared})"
                    )
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
            for name, _, judge in agent.judges:
                # Le verdict passe par un outil imposé (tool_choice: required).
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
        agents = {agent.name: agent for agent in self.agents}
        for agent in self.agents:
            for ref in agent.subagents:
                if ref.budget_share is not None and not self.budget_of(agent.name).run.limited:
                    raise ValueError(
                        f"Agent {agent.name!r}, sous-agent {ref.tool_name!r} : budget_share "
                        "demande un budget du run (max_cost, max_tokens ou max_calls)"
                    )
                target = agents.get(ref.agent)
                if target is None:
                    raise ValueError(
                        f"Agent {agent.name!r}, sous-agent {ref.tool_name!r} : agent "
                        f"{ref.agent!r} non déclaré (agents : {', '.join(agents)})"
                    )
                if not (ref.description or target.description):
                    raise ValueError(
                        f"Agent {agent.name!r}, sous-agent {ref.tool_name!r} : description "
                        f"manquante (ni dans la référence ni dans l'agent {ref.agent!r})"
                    )

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
                raise ValueError(
                    f"{label} : agent {trigger.agent!r} non déclaré (agents : {declared})"
                )
            for source in trigger.templates:
                try:
                    Template.parse(source)
                except TemplateError as exc:
                    raise ValueError(f"{label} : {exc}") from exc

    def _check_tenants(self) -> None:
        """Clients déclarés une fois, sur des agents et des modèles qui existent (L1, M5).

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
                    raise ValueError(
                        f"Client {tenant.id!r} : agent {agent!r} non déclaré "
                        f"(agents : {', '.join(sorted(agents)) or 'aucun'})"
                    )
            for source, target in tenant.models.items():
                for model, which in ((source, "modèle"), (target, "modèle de remplacement")):
                    if model not in models:
                        raise ValueError(
                            f"Client {tenant.id!r} : {which} {model!r} non déclaré "
                            f"(modèles : {', '.join(sorted(models)) or 'aucun'})"
                        )
        if not self.tenants:
            return
        declared = {tenant.id for tenant in self.tenants}
        for key in self.security.api_keys:
            if key.tenant not in declared:
                raise ValueError(
                    f"Clé {key.id!r} : client {key.tenant!r} non déclaré "
                    f"(clients : {', '.join(sorted(declared))})"
                )

    def _check_chain(
        self, label: str, chain: tuple[str, ...], *, vision: bool = False, tools: bool = False
    ) -> None:
        """Modèles d'une chaîne de secours déclarés, avec les capacités exigées (B9, #10)."""
        ids = [spec.id for spec in self.models]
        for position, model in enumerate(chain):
            which = "modèle" if position == 0 else "modèle de secours"
            if model not in ids:
                raise ValueError(
                    f"{label} : {which} {model!r} non déclaré "
                    f"(modèles connus : {', '.join(ids) or 'aucun'})"
                )
            capabilities = self.model_spec(model).capabilities
            if vision and not capabilities.vision:
                raise ValueError(
                    f"{label} : il reçoit les pièces jointes, mais le {which} {model!r} "
                    "n'a pas la capacité vision (capabilities.vision: true)"
                )
            if tools and not capabilities.tools:
                raise ValueError(
                    f"{label} : il appelle des outils, mais le {which} {model!r} ne sait pas "
                    "le faire (capabilities.tools: false)"
                )

    @property
    def all_agents(self) -> tuple[AgentSpec, ...]:
        """Agents déclarés, plus l'agent interne de compaction s'il est configuré."""
        compaction = self.sessions.compaction
        if compaction is None:
            return self.agents
        return (*self.agents, compaction_agent(compaction))

    def budget_of(self, agent: str) -> Budgets:
        """Budgets d'un agent : ceux de la racine, surchargés par son ``budget``."""
        spec = next((a for a in self.agents if a.name == agent), None)
        return self.budgets.merged(spec.budget if spec is not None else None)

    @property
    def tenant_ids(self) -> tuple[TenantId, ...]:
        """Clients déclarés ; ``default`` seul quand la config n'en nomme aucun (#33)."""
        if not self.tenants:
            return (DEFAULT_TENANT,)
        return tuple(tenant.id for tenant in self.tenants)

    def tenant_spec(self, tenant_id: TenantId) -> TenantSpec | None:
        """Fiche d'un client déclaré ; ``None`` s'il ne surcharge rien."""
        for tenant in self.tenants:
            if tenant.id == tenant_id:
                return tenant
        return None

    def mcp_server(self, name: str) -> McpServerSpec:
        """Définition d'un serveur MCP par son nom."""
        for server in self.mcp_servers:
            if server.name == name:
                return server
        raise KeyError(name)

    def model_spec(self, model_id: str) -> ModelSpec:
        """Définition d'un modèle par son identifiant."""
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
