# SPDX-License-Identifier: Apache-2.0
"""Un serveur MCP référencé par un agent : source d'outils (#19, D3).

La référence choisit les outils exposés (``include`` ou ``exclude``), leur
préfixe (``serveur__outil``, ou ``alias__outil``) et leurs déclarations :
annotations du serveur, puis ``mcp_servers[].tools``, puis la référence de
l'agent, de la plus faible à la plus forte.

Un outil dont le nom préfixé ne respecte pas le format des API (lettres,
chiffres, ``_``, ``-``, 64 caractères au plus) est écarté avec un
avertissement. La clé d'idempotence de l'appel part dans ``_meta``.
"""

import logging
from collections.abc import AsyncGenerator, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Final, cast

from mcp import McpError, types
from pydantic import JsonValue, ValidationError

from loom_ia.adapters.mcp.convert import declared, description, input_schema, to_output
from loom_ia.adapters.mcp.pool import McpPool
from loom_ia.adapters.mcp.server import ConnectionLost, McpServer
from loom_ia.adapters.mcp.transports import SessionFactory
from loom_ia.core.model import (
    MCP_PREFIX_SEPARATOR,
    McpServerSpec,
    ToolOutput,
    ToolOverrides,
    ToolSpec,
)
from loom_ia.core.ports import SourceContext, SourceUnavailable, Tool, ToolContext, ToolError

logger = logging.getLogger(__name__)

# Clé de ``_meta`` qui porte la clé d'idempotence d'un appel (§9.5).
IDEMPOTENCY_META: Final = "loom-ia/idempotency_key"

LOST: Final = (
    "Connexion au serveur perdue pendant l'appel : l'outil a peut-être produit son effet. "
    "Il n'a pas été relancé automatiquement ; vérifie avant de le rappeler."
)


@dataclass(frozen=True, kw_only=True)
class McpSelection:
    """Ce qu'un agent prend d'un serveur."""

    prefix: str
    include: tuple[str, ...] | None = None
    exclude: tuple[str, ...] | None = None
    required: bool = False
    # Déclarations propres à l'agent, par nom d'outil du serveur.
    tools: Mapping[str, ToolOverrides] = field(default_factory=dict[str, ToolOverrides])


@dataclass(frozen=True, slots=True)
class McpTool:
    """Outil d'un serveur MCP, sous son nom préfixé."""

    spec: ToolSpec
    server: McpServer
    # Nom de l'outil chez le serveur.
    remote_name: str

    async def invoke(self, arguments: dict[str, JsonValue], context: ToolContext) -> ToolOutput:
        meta: dict[str, Any] = {IDEMPOTENCY_META: context.idempotency_key}
        try:
            result = await self.server.call(
                self.remote_name,
                cast(dict[str, Any], arguments),
                meta=meta,
                retry=self.spec.safe_to_retry,
            )
        except SourceUnavailable as exc:
            raise ToolError(f"Serveur MCP {self.server.name} indisponible : {exc.message}") from exc
        except ConnectionLost as exc:
            raise ToolError(LOST) from exc
        except McpError as exc:
            raise ToolError(
                f"Erreur du serveur MCP {self.server.name} : {exc.error.message}"
            ) from exc
        return to_output(result)


class McpSource:
    """Source d'outils : un serveur, tel qu'un agent le référence."""

    def __init__(
        self,
        spec: McpServerSpec,
        selection: McpSelection,
        *,
        factory: SessionFactory,
        pool: McpPool | None = None,
    ) -> None:
        if spec.scope == "shared" and pool is None:
            raise ValueError(f"Serveur MCP {spec.name!r} de portée shared : pool requis")
        self.spec = spec
        self.selection = selection
        self._factory = factory
        self._pool = pool
        self._warned = False

    @property
    def name(self) -> str:
        return self.spec.name

    @property
    def required(self) -> bool:
        return self.selection.required

    def __repr__(self) -> str:
        return f"McpSource({self.spec.name!r}, préfixe {self.selection.prefix!r})"

    @asynccontextmanager
    async def open(self, context: SourceContext) -> AsyncGenerator[Sequence[Tool]]:
        shared = self._pool is not None and self.spec.scope == "shared"
        server = (
            self._pool.server(self.spec)
            if self._pool is not None and shared
            else McpServer(self.spec, self._factory)
        )
        try:
            listed = await server.tools()
            yield self.select(server, listed)
        finally:
            if not shared:
                await server.aclose()

    def select(self, server: McpServer, listed: Sequence[types.Tool]) -> list[Tool]:
        """Outils exposés à l'agent, sous leur nom préfixé et avec leurs déclarations."""
        chosen = self.selection
        names = {tool.name for tool in listed}
        if not self._warned:
            self._warn_unknown(names)
            self._warned = True
        tools: list[Tool] = []
        for tool in listed:
            if chosen.include is not None and tool.name not in chosen.include:
                continue
            if chosen.exclude is not None and tool.name in chosen.exclude:
                continue
            spec = self._spec(tool)
            if spec is not None:
                tools.append(McpTool(spec=spec, server=server, remote_name=tool.name))
        return tools

    def _spec(self, tool: types.Tool) -> ToolSpec | None:
        side_effects, idempotent = declared(tool.annotations)
        name = f"{self.selection.prefix}{MCP_PREFIX_SEPARATOR}{tool.name}"
        try:
            spec = ToolSpec(
                name=name,
                description=description(tool),
                input_schema=input_schema(tool),
                kind="mcp",
                side_effects=side_effects,
                idempotent=idempotent,
            )
        except ValidationError:
            logger.warning(
                "Outil MCP %r du serveur %s écarté : nom %r hors du format des API "
                "(lettres, chiffres, _ et -, 64 caractères au plus)",
                tool.name,
                self.spec.name,
                name,
            )
            return None
        return spec.overridden(self.spec.tools.get(tool.name)).overridden(
            self.selection.tools.get(tool.name)
        )

    def _warn_unknown(self, names: set[str]) -> None:
        chosen = self.selection
        checks = {
            "include": chosen.include or (),
            "exclude": chosen.exclude or (),
            "tools (agent)": tuple(chosen.tools),
            "tools (serveur)": tuple(self.spec.tools),
        }
        for label, declared_names in checks.items():
            unknown = sorted(set(declared_names) - names)
            if unknown:
                logger.warning(
                    "Serveur MCP %s : %s cite des outils inconnus du serveur : %s",
                    self.spec.name,
                    label,
                    ", ".join(unknown),
                )
