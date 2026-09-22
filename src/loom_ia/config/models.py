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
from pathlib import Path
from typing import Final, Literal, Self

from pydantic import Field, JsonValue, PositiveFloat, PositiveInt, model_validator

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
    TenantId,
    reject_later,
)
from loom_ia.telemetry.logs import LogFormat

SCHEMA_VERSION: Final = 1
EVENT_BACKENDS: Final = ("memory", "jsonl", "sqlite")
IDEMPOTENCY_BACKENDS: Final = ("journal", "memory", "sqlite")
# Magasins qu'une clé métier peut exiger : partagés entre runs **et**
# durables. Le journal ne voit que son run, la mémoire que son process.
SHARED_IDEMPOTENCY: Final = ("sqlite",)
# Journaux rangés hors de la mémoire : ils donnent aussi le dossier des artefacts.
FILE_BACKENDS: Final = ("jsonl", "sqlite")
# Dossier des artefacts sous celui du journal, quand la config n'en donne pas.
ARTIFACTS_SUBDIR: Final = ".artifacts"


class EventsStorage(DomainModel):
    backend: str = "memory"
    # ``jsonl`` : dossier des journaux. ``sqlite`` : fichier de la base.
    # Relatif au fichier de config.
    path: Path | None = None

    @model_validator(mode="after")
    def _check_backend(self) -> Self:
        if self.backend not in EVENT_BACKENDS:
            raise ValueError(
                f"Journal {self.backend!r} : seuls {', '.join(EVENT_BACKENDS)} "
                "sont disponibles à ce jalon"
            )
        if self.backend in FILE_BACKENDS and self.path is None:
            raise ValueError(f"Journal {self.backend!r} : 'path' est obligatoire")
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
        return self

    @property
    def shared(self) -> bool:
        """Vrai si ce magasin est vu de tous les runs et survit au process (#49)."""
        return self.backend in SHARED_IDEMPOTENCY


class StorageConfig(DomainModel):
    events: EventsStorage = EventsStorage()
    artifacts: ArtifactsStorage = ArtifactsStorage()
    idempotency: IdempotencyStorage = IdempotencyStorage()

    @model_validator(mode="before")
    @classmethod
    def _later(cls, data: object) -> object:
        reject_later(data, LATER_STORAGE)
        return data

    @model_validator(mode="after")
    def _check_artifacts(self) -> Self:
        artifacts = self.artifacts
        if artifacts.backend == "memory" and artifacts.path is not None:
            raise ValueError("Artefacts 'memory' : 'path' n'a pas de sens")
        if artifacts.backend == "local" and artifacts.path is None and self.events.path is None:
            raise ValueError(
                "Artefacts 'local' : 'path' est obligatoire quand le journal n'est pas en fichiers"
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
    """Serveur MCP de l'instance (``loom mcp``)."""

    # Dossiers où un lien ``file://`` joint à un appel peut être lu ; aucun par
    # défaut : les liens ``file://`` sont refusés. Relatifs au dossier de la config.
    file_roots: tuple[Path, ...] = ()

    @model_validator(mode="before")
    @classmethod
    def _later(cls, data: object) -> object:
        reject_later(data, LATER_MCP_ACCESS)
        return data


class ServerConfig(DomainModel):
    http: HttpServer = HttpServer()
    mcp: McpAccess = McpAccess()


class LoomConfig(DomainModel):
    version: int
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
    # Remplis depuis ``agents_dir`` au chargement, ou donnés directement en Python.
    agents: tuple[AgentSpec, ...] = ()

    @model_validator(mode="before")
    @classmethod
    def _later(cls, data: object) -> object:
        reject_later(data, LATER_ROOT)
        return data

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
