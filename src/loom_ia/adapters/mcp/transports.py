# SPDX-License-Identifier: Apache-2.0
"""Ouverture d'une session MCP selon le transport déclaré (D3).

Une fabrique de session prend le gestionnaire des messages du serveur
(notifications ``tools/list_changed``…) et ouvre une ``ClientSession``
initialisée. Les secrets sont lus dans l'environnement à la création de la
fabrique : une variable absente est une erreur de config, signalée au montage.
"""

import os
import sys
import threading
from collections import deque
from collections.abc import AsyncGenerator, Callable, Mapping
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Final, TextIO, cast

from mcp import ClientSession, McpError, StdioServerParameters, types
from mcp.client.session import MessageHandlerFnT
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client

from loom_ia import __version__
from loom_ia.core.model import McpServerSpec

type SessionFactory = Callable[[MessageHandlerFnT], AbstractAsyncContextManager[ClientSession]]


class McpConfigError(ValueError):
    """Serveur MCP inutilisable en l'état (variable d'environnement absente…)."""


CLIENT_INFO = types.Implementation(name="loom-ia", version=__version__)

# Lignes de stderr d'un serveur stdio gardées pour expliquer un échec d'ouverture.
STDERR_LINES: Final = 20
# Longueur gardée d'une ligne : un serveur peut écrire une trace d'un seul tenant.
STDERR_LINE_CHARS: Final = 300


class McpStartError(ConnectionError):
    """Serveur stdio qui s'arrête ou refuse l'ouverture, avec ce qu'il a écrit sur stderr.

    Le SDK MCP ne dit que « Connection closed » ou ``BrokenResourceError`` : ni
    le code de sortie (il ne donne pas accès au processus), ni ce que le
    serveur a écrit. Un chemin faux ou une config illisible ne se voyaient
    qu'en relançant le serveur à la main (test D3 du 18/10).
    """


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
        tail = _StderrTail()
        opened = False
        try:
            async with stdio_client(params, errlog=tail.writer) as (read, write):
                async with ClientSession(
                    read, write, message_handler=handler, client_info=CLIENT_INFO
                ) as session:
                    await session.initialize()
                    opened = True
                    yield session
        except Exception as exc:
            if opened:
                raise
            tail.close()
            raise McpStartError(_started(exc, tail.lines())) from exc
        finally:
            tail.close()

    return open_session


class _StderrTail:
    """Le stderr d'un serveur stdio, recopié sur le nôtre et dont on garde la fin.

    Le SDK branche le stderr du processus sur un fichier : on lui donne
    l'extrémité d'écriture d'un tuyau, et un fil lit l'autre. Chaque ligne
    part sur notre stderr, comme avant — la console ne change pas —, et les
    ``STDERR_LINES`` dernières restent en mémoire. Le fil s'arrête quand plus
    personne n'écrit : le processus est mort et notre extrémité est fermée.
    """

    def __init__(self) -> None:
        read_fd, write_fd = os.pipe()
        self.writer: TextIO = os.fdopen(write_fd, "w", encoding="utf-8")
        self._reader = os.fdopen(read_fd, "r", encoding="utf-8", errors="replace")
        self._lines: deque[str] = deque(maxlen=STDERR_LINES)
        self._thread = threading.Thread(target=self._pump, name="mcp-stderr", daemon=True)
        self._thread.start()

    def _pump(self) -> None:
        with self._reader:
            for line in self._reader:
                try:
                    sys.stderr.write(line)
                    sys.stderr.flush()
                except OSError, ValueError:
                    pass
                text = line.rstrip()
                if text:
                    self._lines.append(text[:STDERR_LINE_CHARS])

    def lines(self) -> list[str]:
        return list(self._lines)

    def close(self) -> None:
        """Ferme notre extrémité, puis laisse au fil le temps de lire ce qui reste."""
        if not self.writer.closed:
            self.writer.close()
        self._thread.join(timeout=2.0)


def _started(error: BaseException, lines: list[str]) -> str:
    """Ce que l'on sait d'une ouverture ratée : la cause, et la fin du stderr du serveur."""
    cause = _innermost(error)
    said = (
        cause.error.message if isinstance(cause, McpError) else (str(cause) or type(cause).__name__)
    )
    if lines:
        shown = " | ".join(lines[-5:])
        return (
            f"le serveur s'est arrêté ou a refusé l'ouverture ({said}) ; "
            f"fin de son stderr : {shown}"
        )
    return f"le serveur s'est arrêté ou a refusé l'ouverture ({said}), sans rien écrire sur stderr"


def _innermost(error: BaseException) -> BaseException:
    """Première exception d'un groupe (anyio regroupe celles de ses tâches)."""
    if not isinstance(error, BaseExceptionGroup):
        return error
    inner = cast(tuple[BaseException, ...], error.exceptions)  # pyright: ignore[reportUnknownMemberType]
    return _innermost(inner[0]) if inner else cast(BaseException, error)


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
