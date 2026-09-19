# SPDX-License-Identifier: Apache-2.0
"""Connexions de portée ``shared`` : une par serveur et par process (#19)."""

from collections.abc import Callable

from loom_ia.adapters.mcp.server import McpServer
from loom_ia.adapters.mcp.transports import SessionFactory
from loom_ia.core.model import McpServerSpec


class McpPool:
    """Serveurs partagés par tous les runs et tous les agents d'une instance."""

    def __init__(self, factory: Callable[[McpServerSpec], SessionFactory]) -> None:
        self._factory = factory
        self._servers: dict[str, McpServer] = {}

    def server(self, spec: McpServerSpec) -> McpServer:
        """Le serveur partagé de ``spec`` ; créé au premier appel, sans connexion."""
        server = self._servers.get(spec.name)
        if server is None:
            server = McpServer(spec, self._factory(spec), idle_timeout=spec.idle_timeout)
            self._servers[spec.name] = server
        return server

    async def aclose(self) -> None:
        for server in self._servers.values():
            await server.aclose()
        self._servers.clear()
