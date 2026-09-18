# SPDX-License-Identifier: Apache-2.0
"""Schéma de la configuration (M1, M2, #35, #50).

Les modèles Pydantic sont le schéma : le YAML n'est qu'une façon de les
remplir, et une configuration écrite en Python (M2) les construit
directement. Tout champ inconnu est refusé ; une clé prévue pour une phase
suivante donne une erreur qui nomme cette phase.

Sous-ensemble du jalon J1 ; le schéma complet est dans ``docs/conception.md``
§17.
"""

import logging
from pathlib import Path
from typing import Final, Literal, Self

from pydantic import Field, PositiveFloat, model_validator

from loom_ia.agents.spec import AgentSpec
from loom_ia.config.keys import ALGORITHM, matches
from loom_ia.config.later import (
    LATER_API_KEY,
    LATER_ROOT,
    LATER_SERVER,
    LATER_STORAGE,
    LATER_TELEMETRY,
)
from loom_ia.core.model import DomainModel, ModelSpec, reject_later
from loom_ia.telemetry.logs import LogFormat

SCHEMA_VERSION: Final = 1
EVENT_BACKENDS: Final = ("memory", "jsonl")


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


class StorageConfig(DomainModel):
    events: EventsStorage = EventsStorage()

    @model_validator(mode="before")
    @classmethod
    def _later(cls, data: object) -> object:
        reject_later(data, LATER_STORAGE)
        return data


class ToolsExecution(DomainModel):
    # Délai par défaut d'un outil ; ``null`` retire la limite.
    timeout: PositiveFloat | None = 30.0
    validate_arguments: bool = True


class ExecutionConfig(DomainModel):
    tools: ToolsExecution = ToolsExecution()


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


class ServerConfig(DomainModel):
    http: HttpServer = HttpServer()

    @model_validator(mode="before")
    @classmethod
    def _later(cls, data: object) -> object:
        reject_later(data, LATER_SERVER)
        return data


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
    storage: StorageConfig = StorageConfig()
    execution: ExecutionConfig = ExecutionConfig()
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
        ids = [spec.id for spec in self.models]
        _reject_doubles("Modèle", ids)
        for agent in self.agents:
            if agent.main.model not in ids:
                known = ", ".join(ids) or "aucun"
                raise ValueError(
                    f"Agent {agent.name!r} : modèle {agent.main.model!r} non déclaré "
                    f"(modèles connus : {known})"
                )
        return self

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
