# SPDX-License-Identifier: Apache-2.0
"""Définition d'un serveur MCP (#19, §17.5).

Un serveur est déclaré une fois dans ``mcp_servers`` et référencé par les
agents. La config ne contient jamais de secret : seulement le nom des
variables d'environnement qui les portent (``env_from``, ``headers_env``).

Portées : ``shared`` (défaut), une connexion par process partagée par les
runs ; ``run``, une connexion ouverte et fermée avec chaque run. La portée
``tenant`` arrive avec le multi-clients (J5.1).
"""

from pathlib import Path
from typing import Final, Literal, Self, cast

from pydantic import Field, PositiveFloat, model_validator

from loom_ia.core.model.base import DomainModel
from loom_ia.core.model.tooling import ToolOverrides

type McpTransport = Literal["stdio", "http"]
type McpScope = Literal["shared", "run"]

# Nom de serveur ou alias : il préfixe les outils (``crm__rechercher``), donc
# sans ``__`` pour que le préfixe se lise sans ambiguïté.
MCP_NAME_PATTERN: Final = r"^[A-Za-z0-9-]+(_[A-Za-z0-9-]+)*$"
# Séparateur entre le préfixe et le nom de l'outil.
MCP_PREFIX_SEPARATOR: Final = "__"

LATER_SCOPES: Final[dict[str, str]] = {"tenant": "J5.1 (multi-clients)"}


class McpServerSpec(DomainModel):
    name: str = Field(pattern=MCP_NAME_PATTERN, max_length=40)
    transport: McpTransport
    # stdio : programme lancé, ses arguments, son environnement.
    command: str | None = None
    args: tuple[str, ...] = ()
    # Variables passées en clair.
    env: dict[str, str] = Field(default_factory=dict)
    # Variables lues dans l'environnement de loom-ia : {VAR_DU_SERVEUR: VAR_DE_LOOM}.
    env_from: dict[str, str] = Field(default_factory=dict)
    # Dossier de lancement ; par défaut celui du fichier de config.
    cwd: Path | None = None
    # http : point d'accès, et en-têtes lus dans l'environnement {En-tête: VARIABLE}.
    url: str | None = None
    headers_env: dict[str, str] = Field(default_factory=dict)
    scope: McpScope = "shared"
    # Délai d'établissement de la connexion, initialisation comprise.
    connect_timeout: PositiveFloat = 10.0
    # Portée shared : fermeture après ce délai sans utilisation ; None la garde ouverte.
    idle_timeout: PositiveFloat | None = 300.0
    # Déclarations par outil, sur celles que le serveur annonce.
    tools: dict[str, ToolOverrides] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _later_scope(cls, data: object) -> object:
        _reject_later_scope(data)
        return data

    @model_validator(mode="after")
    def _check_transport(self) -> Self:
        stdio = {
            "command": self.command,
            "args": self.args,
            "env": self.env,
            "env_from": self.env_from,
            "cwd": self.cwd,
        }
        http = {"url": self.url, "headers_env": self.headers_env}
        own, other = (stdio, http) if self.transport == "stdio" else (http, stdio)
        required = "command" if self.transport == "stdio" else "url"
        if not own[required]:
            raise ValueError(
                f"Serveur {self.name!r} : {required!r} obligatoire en {self.transport}"
            )
        misplaced = [key for key, value in other.items() if value]
        if misplaced:
            raise ValueError(
                f"Serveur {self.name!r} : {', '.join(misplaced)} sans effet en {self.transport}"
            )
        return self


def _reject_later_scope(data: object) -> None:
    if not isinstance(data, dict):
        return
    scope = cast(dict[str, object], data).get("scope")
    if isinstance(scope, str) and scope in LATER_SCOPES:
        raise ValueError(
            f"scope {scope!r} : prévu pour le jalon {LATER_SCOPES[scope]}, "
            "pas encore pris en charge"
        )
