# SPDX-License-Identifier: Apache-2.0
"""Connexions partagées entre les runs : une par clé de pool (#19, #34).

La clé vaut le nom du serveur en portée ``shared`` — une connexion pour tout
le process — et le nom du serveur **et du client** en portée ``tenant`` : le
serveur est le même, ses identifiants ne le sont pas, et deux clients ne
doivent jamais se retrouver sur la même session MCP.
"""

from collections.abc import Callable

from loom_ia.adapters.mcp.server import McpServer
from loom_ia.adapters.mcp.transports import SessionFactory
from loom_ia.core.model import McpServerSpec


class McpPool:
    """Serveurs partagés par les runs d'une instance, sous leur clé de pool."""

    def __init__(self, factory: Callable[[McpServerSpec], SessionFactory]) -> None:
        self._factory = factory
        self._servers: dict[str, McpServer] = {}

    def server(
        self, spec: McpServerSpec, key: str | None = None, factory: SessionFactory | None = None
    ) -> McpServer:
        """Le serveur partagé de ``spec`` sous cette clé ; créé au premier appel.

        ``factory`` est celle de l'appelant : en portée ``tenant``, elle porte
        les identifiants de son client, que le pool ne connaît pas.
        """
        pooled = key or spec.name
        server = self._servers.get(pooled)
        if server is None:
            opened = factory if factory is not None else self._factory(spec)
            server = McpServer(spec, opened, idle_timeout=spec.idle_timeout)
            self._servers[pooled] = server
        return server

    async def aclose(self) -> None:
        for server in self._servers.values():
            await server.aclose()
        self._servers.clear()
