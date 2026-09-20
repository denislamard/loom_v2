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
from pathlib import Path
from typing import Final, Literal, Self

from pydantic import Field, JsonValue, PositiveFloat, PositiveInt, model_validator

from loom_ia.agents.spec import AgentSpec
from loom_ia.config.keys import ALGORITHM, matches
from loom_ia.config.later import (
    LATER_API_KEY,
    LATER_MCP_ACCESS,
    LATER_ROOT,
    LATER_SESSIONS,
    LATER_STORAGE,
    LATER_TELEMETRY,
)
from loom_ia.core.model import (
    AttachmentPolicy,
    Budgets,
    DomainModel,
    McpServerSpec,
    ModelSpec,
    reject_later,
)
from loom_ia.telemetry.logs import LogFormat

SCHEMA_VERSION: Final = 1
EVENT_BACKENDS: Final = ("memory", "jsonl", "sqlite")
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


class StorageConfig(DomainModel):
    events: EventsStorage = EventsStorage()
    artifacts: ArtifactsStorage = ArtifactsStorage()

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
    """Vie d'une session : historique matérialisé, puis compaction (J4.1b)."""

    # Événements ajoutés depuis le dernier marqueur avant qu'un snapshot de
    # l'historique soit écrit (§11.2). Le relire coûte moins que de rejouer
    # ce qu'il couvre ; l'écrire recopie l'historique dans le journal.
    snapshot_every: PositiveInt = 50

    @model_validator(mode="before")
    @classmethod
    def _later(cls, data: object) -> object:
        reject_later(data, LATER_SESSIONS)
        return data


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


type Scope = Literal["run", "read", "read_content", "approve", "admin"]


class ApiKey(DomainModel):
    """Clé déclarée dans la config, par son empreinte seulement (#39)."""

    id: str = Field(min_length=1)
    # Empreinte ``sha256:…`` donnée par ``loom keys create``.
    hash: str
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
    sessions: SessionsConfig = SessionsConfig()
    execution: ExecutionConfig = ExecutionConfig()
    # Budgets par défaut des agents ; un agent les surcharge par son ``budget`` (J4).
    budgets: Budgets = Budgets()
    telemetry: TelemetryConfig = TelemetryConfig()
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
        if self.version != SCHEMA_VERSION:
            raise ValueError(
                f"Version de config {self.version!r} non prise en charge "
                f"(attendue : {SCHEMA_VERSION})"
            )
        _reject_doubles("Agent", [agent.name for agent in self.agents])
        _reject_doubles("Clé", [key.id for key in self.security.api_keys])
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
        return self

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

    def budget_of(self, agent: str) -> Budgets:
        """Budgets d'un agent : ceux de la racine, surchargés par son ``budget``."""
        spec = next((a for a in self.agents if a.name == agent), None)
        return self.budgets.merged(spec.budget if spec is not None else None)

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
