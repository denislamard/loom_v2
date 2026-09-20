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

from pydantic import Field, PositiveFloat, PositiveInt, model_validator

from loom_ia.agents.spec import AgentSpec
from loom_ia.config.keys import ALGORITHM, matches
from loom_ia.config.later import (
    LATER_API_KEY,
    LATER_MCP_ACCESS,
    LATER_ROOT,
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
EVENT_BACKENDS: Final = ("memory", "jsonl")
# Dossier des artefacts sous celui du journal JSONL, quand la config n'en donne pas.
ARTIFACTS_SUBDIR: Final = ".artifacts"


class EventsStorage(DomainModel):
    backend: str = "memory"
    # Dossier des journaux JSONL, relatif au fichier de config.
    path: Path | None = None

    @model_validator(mode="after")
    def _check_backend(self) -> Self:
        if self.backend not in EVENT_BACKENDS:
            raise ValueError(
                f"Journal {self.backend!r} : seuls {' et '.join(EVENT_BACKENDS)} "
                "sont disponibles au jalon J1"
            )
        if self.backend == "jsonl" and self.path is None:
            raise ValueError("Journal 'jsonl' : 'path' est obligatoire")
        return self


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
        return "local" if self.events.backend == "jsonl" else "memory"

    @property
    def artifacts_path(self) -> Path | None:
        """Dossier du stockage ``local`` : celui déclaré, sinon ``.artifacts`` sous le journal."""
        if self.artifacts_backend != "local":
            return None
        if self.artifacts.path is not None:
            return self.artifacts.path
        return None if self.events.path is None else self.events.path / ARTIFACTS_SUBDIR


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
        ids = [spec.id for spec in self.models]
        _reject_doubles("Modèle", ids)
        known = ", ".join(ids) or "aucun"
        for agent in self.agents:
            if agent.main.model not in ids:
                raise ValueError(
                    f"Agent {agent.name!r} : modèle {agent.main.model!r} non déclaré "
                    f"(modèles connus : {known})"
                )
            for role in agent.roles:
                if role.model not in ids:
                    raise ValueError(
                        f"Agent {agent.name!r}, rôle {role.name!r} : modèle {role.model!r} "
                        f"non déclaré (modèles connus : {known})"
                    )
                if role.wants_attachments and not self.model_spec(role.model).capabilities.vision:
                    raise ValueError(
                        f"Agent {agent.name!r}, rôle {role.name!r} : il reçoit les pièces "
                        f"jointes, mais le modèle {role.model!r} n'a pas la capacité vision "
                        "(capabilities.vision: true)"
                    )
            for name, _, judge in agent.judges:
                if judge.model not in ids:
                    raise ValueError(
                        f"Agent {agent.name!r}, juge {name!r} : modèle {judge.model!r} "
                        f"non déclaré (modèles connus : {known})"
                    )
                if judge.wants_attachments and not self.model_spec(judge.model).capabilities.vision:
                    raise ValueError(
                        f"Agent {agent.name!r}, juge {name!r} : il reçoit les pièces "
                        f"jointes, mais le modèle {judge.model!r} n'a pas la capacité vision "
                        "(capabilities.vision: true)"
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


def _reject_doubles(kind: str, names: list[str]) -> None:
    doubles = sorted({name for name in names if names.count(name) > 1})
    if doubles:
        raise ValueError(f"{kind} déclaré deux fois : {', '.join(doubles)}")
