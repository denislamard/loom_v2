# SPDX-License-Identifier: Apache-2.0
"""Ouverture d'une session MCP selon le transport déclaré (D3).

Une fabrique de session prend le gestionnaire des messages du serveur
(notifications ``tools/list_changed``…) et ouvre une ``ClientSession``
initialisée. Les secrets sont lus dans l'environnement à la création de la
fabrique : une variable absente est une erreur de config, signalée au montage.
"""

import importlib.metadata
from collections.abc import AsyncGenerator, Callable, Mapping
from contextlib import AbstractAsyncContextManager, asynccontextmanager

from mcp import ClientSession, StdioServerParameters, types
from mcp.client.session import MessageHandlerFnT
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client

from loom_ia.core.model import McpServerSpec

type SessionFactory = Callable[[MessageHandlerFnT], AbstractAsyncContextManager[ClientSession]]


class McpConfigError(ValueError):
    """Serveur MCP inutilisable en l'état (variable d'environnement absente…)."""


def _version() -> str:
    try:
        return importlib.metadata.version("loom-ia")
    except importlib.metadata.PackageNotFoundError:
        return "dev"


CLIENT_INFO = types.Implementation(name="loom-ia", version=_version())


def session_factory(spec: McpServerSpec, *, environ: Mapping[str, str]) -> SessionFactory:
    """Fabrique des sessions du serveur ``spec``."""
    if spec.transport == "stdio":
        return _stdio(spec, environ)
    return _http(spec, environ)


def _secret(spec: McpServerSpec, variable: str, environ: Mapping[str, str]) -> str:
    value = environ.get(variable, "").strip()
    if not value:
        raise McpConfigError(
            f"Serveur MCP {spec.name!r} : la variable d'environnement {variable} "
            "est absente ou vide"
        )
    return value


def _stdio(spec: McpServerSpec, environ: Mapping[str, str]) -> SessionFactory:
    if spec.command is None:
        raise McpConfigError(f"Serveur MCP {spec.name!r} : 'command' manquant")
    env = {
        **spec.env,
        **{child: _secret(spec, parent, environ) for child, parent in spec.env_from.items()},
    }
    params = StdioServerParameters(
        command=spec.command, args=list(spec.args), env=env or None, cwd=spec.cwd
    )

    @asynccontextmanager
    async def open_session(handler: MessageHandlerFnT) -> AsyncGenerator[ClientSession]:
        async with stdio_client(params) as (read, write):
            async with ClientSession(
                read, write, message_handler=handler, client_info=CLIENT_INFO
            ) as session:
                await session.initialize()
                yield session

    return open_session


def _http(spec: McpServerSpec, environ: Mapping[str, str]) -> SessionFactory:
    if spec.url is None:
        raise McpConfigError(f"Serveur MCP {spec.name!r} : 'url' manquante")
    url = spec.url
    headers = {name: _secret(spec, var, environ) for name, var in spec.headers_env.items()}

    @asynccontextmanager
    async def open_session(handler: MessageHandlerFnT) -> AsyncGenerator[ClientSession]:
        async with create_mcp_http_client(headers=headers or None) as http:
            async with streamable_http_client(url, http_client=http) as (read, write, _):
                async with ClientSession(
                    read, write, message_handler=handler, client_info=CLIENT_INFO
                ) as session:
                    await session.initialize()
                    yield session

    return open_session
