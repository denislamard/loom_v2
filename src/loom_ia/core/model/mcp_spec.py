# SPDX-License-Identifier: Apache-2.0
"""Définition d'un serveur MCP, déclarée sous ``mcp_servers`` dans ``loom.yaml`` (point 19, conception §17.5, fonction D3).

Un serveur est déclaré une fois dans ``mcp_servers`` et référencé par les
agents. La config ne contient jamais de secret : seulement le nom des
variables d'environnement qui les portent (``env_from``, ``headers_env``).

Portées : ``shared`` (défaut), une connexion par process partagée par les
runs ; ``run``, une connexion ouverte et fermée avec chaque run ;
``tenant`` (phase 5.1a), une connexion par client, partagée par ses runs et
ouverte avec ses secrets à lui — deux artisans ne parlent jamais au même
CRM sous la même session.

Les outils d'un serveur sont préfixés par son nom (``crm__rechercher``). Un
agent référence un serveur dans ses ``tools`` et peut en choisir les outils.

Exemple dans ``loom.yaml`` ::

    mcp_servers:
    - name: crm
      transport: stdio
      command: uv
      args: [run, serveur_crm.py]
      env_from: { CRM_TOKEN: CRM_TOKEN }
    - name: notes
      transport: http
      url: https://notes.example.com/mcp
      headers_env: { Authorization: NOTES_AUTH }
      scope: tenant
"""

from pathlib import Path
from typing import Final, Literal, Self

from pydantic import Field, PositiveFloat, model_validator

from loom_ia.core.model.base import DomainModel
from loom_ia.core.model.model_spec import CircuitBreaker
from loom_ia.core.model.tooling import ToolOverrides

type McpTransport = Literal["stdio", "http"]
"""Transport d'un serveur MCP : ``stdio`` (programme lancé par loom) ou ``http``."""
type McpScope = Literal["shared", "run", "tenant"]
"""Portée d'une connexion MCP : par process, par run, ou par client."""

MCP_NAME_PATTERN: Final = r"^[A-Za-z0-9-]+(_[A-Za-z0-9-]+)*$"
"""Forme d'un nom de serveur ou d'un alias : il préfixe les outils (``crm__rechercher``), donc sans ``__``."""

MCP_PREFIX_SEPARATOR: Final = "__"
"""Séparateur entre le préfixe (le serveur) et le nom de l'outil."""


class McpServerSpec(DomainModel):
    """Un serveur MCP déclaré sous ``mcp_servers`` dans ``loom.yaml``.

    Attributes:
        name: Nom du serveur, 40 caractères au plus, sans ``__`` : il préfixe
            ses outils (``crm__rechercher``).
        transport: ``stdio`` ou ``http``.
        command: stdio : programme lancé (obligatoire en stdio).
        args: stdio : ses arguments.
        env: stdio : variables passées en clair.
        env_from: stdio : variables lues dans l'environnement de loom,
            ``{VAR_DU_SERVEUR: VAR_DE_LOOM}`` ; un client peut les rediriger
            vers les siennes.
        cwd: stdio : dossier de lancement ; par défaut celui du fichier de
            config.
        url: http : point d'accès (obligatoire en http).
        headers_env: http : en-têtes lus dans l'environnement,
            ``{En-tête: VARIABLE}``.
        scope: ``shared`` (défaut) : une connexion pour tout le process ;
            ``tenant`` : une par client (fonction L1, point 34) ; ``run`` :
            une par run.
        connect_timeout: Délai d'établissement de la connexion,
            initialisation comprise, en secondes. Défaut : 10.
        idle_timeout: Portée ``shared`` : fermeture après ce délai sans
            utilisation, en secondes ; ``None`` la garde ouverte. Défaut :
            300.
        tools: Déclarations par outil (effets, approbation, délai…), qui
            s'ajoutent à ce que le serveur annonce.
        circuit_breaker: Disjoncteur sur les connexions ratées de suite
            (backlog #011) ; actif par défaut (5 échecs, 60 s), ``null`` le
            retire.

    Raises:
        ValueError: ``command`` manque en stdio ou ``url`` en http ; ou un
            champ de l'autre transport est donné (« … sans effet en
            stdio »).
    """

    name: str = Field(pattern=MCP_NAME_PATTERN, max_length=40)
    transport: McpTransport
    command: str | None = None
    args: tuple[str, ...] = ()
    env: dict[str, str] = Field(default_factory=dict)
    env_from: dict[str, str] = Field(default_factory=dict)
    cwd: Path | None = None
    url: str | None = None
    headers_env: dict[str, str] = Field(default_factory=dict)
    scope: McpScope = "shared"
    connect_timeout: PositiveFloat = 10.0
    idle_timeout: PositiveFloat | None = 300.0
    tools: dict[str, ToolOverrides] = Field(default_factory=dict)
    circuit_breaker: CircuitBreaker | None = CircuitBreaker()

    @model_validator(mode="after")
    def _check_transport(self) -> Self:
        """Exige le champ obligatoire du transport, et refuse ceux de l'autre transport."""
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
            raise ValueError(f"Serveur {self.name!r} : {required!r} obligatoire en {self.transport}")
        misplaced = [key for key, value in other.items() if value]
        if misplaced:
            raise ValueError(f"Serveur {self.name!r} : {', '.join(misplaced)} sans effet en {self.transport}")
        return self
