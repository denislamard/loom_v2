# SPDX-License-Identifier: Apache-2.0
"""Un serveur MCP vu du client : connexion, reconnexion, cache des outils (#19, D3).

La session du SDK est tenue par une tâche dédiée : ses contextes (anyio) sont
ouverts et refermés dans la même tâche, quelle que soit celle qui l'utilise.

- **Connexion à la demande**, avec un délai (``connect_timeout``).
- **Contrôle de santé** : un ``ping`` à chaque réutilisation d'une connexion
  pour un nouveau run ; une connexion morte est rouverte.
- **Reconnexion avec backoff** : après un échec, la tentative suivante attend
  1, 2, 5, 10 puis 30 s ; entre-temps, le serveur est déclaré indisponible.
- **Appel interrompu par une perte de connexion** : rejoué une fois, après
  reconnexion, seulement si l'outil peut l'être sans risque (#18).
- **Cache des outils**, vidé sur ``notifications/tools/list_changed`` et à
  chaque nouvelle connexion.
- **Fermeture après inactivité** (``idle_timeout``), pour la portée ``shared``.
"""

import asyncio
import logging
import time
from collections.abc import AsyncGenerator, Callable, Sequence
from contextlib import asynccontextmanager, suppress
from typing import Any, Final, cast

import anyio
import httpx
from mcp import ClientSession, McpError, types
from mcp.shared.session import RequestResponder

from loom_ia.adapters.mcp.transports import SessionFactory
from loom_ia.core.model import McpServerSpec
from loom_ia.core.ports import SourceUnavailable

logger = logging.getLogger(__name__)

# Attentes successives avant une nouvelle tentative de connexion, en secondes.
BACKOFF: Final = (1.0, 2.0, 5.0, 10.0, 30.0)
# Délai laissé à une session pour se fermer proprement.
CLOSE_TIMEOUT: Final = 5.0

type Clock = Callable[[], float]


class ConnectionLost(Exception):
    """Connexion perdue pendant un appel : l'appel a peut-être produit son effet."""


def is_connection_lost(error: BaseException) -> bool:
    """Vrai si l'erreur vient du transport, et non de l'outil ou du protocole."""
    if isinstance(error, McpError):
        return error.error.code == types.CONNECTION_CLOSED
    return isinstance(
        error,
        anyio.ClosedResourceError
        | anyio.BrokenResourceError
        | anyio.EndOfStream
        | ConnectionError
        | httpx.TransportError,
    )


class _Held:
    """Une session ouverte par une tâche dédiée, jusqu'à ``close``."""

    def __init__(self, name: str, factory: SessionFactory, server: McpServer) -> None:
        self._name = name
        self._factory = factory
        self._server = server
        self._ready = asyncio.Event()
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self.session: ClientSession | None = None
        self.error: BaseException | None = None

    async def start(self, delay: float) -> ClientSession:
        """Ouvre la session ; ``TimeoutError`` si elle n'est pas prête en ``delay`` secondes."""
        self._task = asyncio.create_task(self._hold(), name=f"mcp:{self._name}")
        try:
            async with asyncio.timeout(delay):
                await self._ready.wait()
        except TimeoutError:
            await self.close(force=True)
            raise
        if self.session is None:
            raise self.error or RuntimeError("session fermée pendant l'ouverture")
        return self.session

    async def _hold(self) -> None:
        try:
            async with self._factory(self._server.on_message) as session:
                self.session = session
                self._ready.set()
                await self._stop.wait()
        except Exception as exc:
            self.error = exc
            if self._ready.is_set():
                logger.debug("Session MCP %s terminée : %r", self._name, exc)
        finally:
            self.session = None
            self._ready.set()

    async def close(self, *, force: bool = False) -> None:
        self._stop.set()
        task = self._task
        if task is None or task.done():
            return
        if not force:
            await asyncio.wait({task}, timeout=CLOSE_TIMEOUT)
        if not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task


class McpServer:
    """Connexion gérée à un serveur déclaré."""

    def __init__(
        self,
        spec: McpServerSpec,
        factory: SessionFactory,
        *,
        idle_timeout: float | None = None,
        clock: Clock = time.monotonic,
        backoff: Sequence[float] = BACKOFF,
    ) -> None:
        self.spec = spec
        self._factory = factory
        self._idle_timeout = idle_timeout
        self._clock = clock
        self._backoff = tuple(backoff)
        self._lock = asyncio.Lock()
        self._held: _Held | None = None
        self._tools: tuple[types.Tool, ...] | None = None
        self._failures = 0
        self._retry_at = 0.0
        self._last_error = ""
        self._in_flight = 0
        self._last_used = clock()
        self._idle_task: asyncio.Task[None] | None = None
        self._closed = False

    @property
    def name(self) -> str:
        return self.spec.name

    @property
    def connected(self) -> bool:
        return self._held is not None and self._held.session is not None

    async def tools(self) -> tuple[types.Tool, ...]:
        """Outils du serveur, après un contrôle de santé de la connexion."""
        async with self._use(check=True) as session:
            if self._tools is None:
                self._tools = await self._list(session)
            return self._tools

    async def call(
        self, name: str, arguments: dict[str, Any], *, meta: dict[str, Any], retry: bool
    ) -> types.CallToolResult:
        """Appelle un outil ; rejoue une fois après une perte de connexion si ``retry``."""
        attempts = 2 if retry else 1
        attempt = 1
        while True:
            async with self._use() as session:
                try:
                    return await session.call_tool(name, arguments, meta=meta)
                except Exception as exc:
                    if not is_connection_lost(exc):
                        raise
                    lost = exc
            await self._drop()
            logger.warning(
                "Connexion au serveur MCP %s perdue pendant l'appel de %s (tentative %d/%d)",
                self.name,
                name,
                attempt,
                attempts,
            )
            if attempt >= attempts:
                raise ConnectionLost(
                    f"Connexion au serveur MCP {self.name} perdue pendant l'appel : {lost!r}"
                ) from lost
            attempt += 1

    async def on_message(
        self,
        message: RequestResponder[types.ServerRequest, types.ClientResult]
        | types.ServerNotification
        | Exception,
    ) -> None:
        """Messages spontanés du serveur : seul le changement de liste d'outils compte."""
        if isinstance(message, types.ServerNotification) and isinstance(
            message.root, types.ToolListChangedNotification
        ):
            logger.info("Liste d'outils du serveur MCP %s modifiée", self.name)
            self._tools = None

    async def aclose(self) -> None:
        self._closed = True
        if self._idle_task is not None:
            self._idle_task.cancel()
        await self._drop()

    # --- Interne ---------------------------------------------------------

    @asynccontextmanager
    async def _use(self, *, check: bool = False) -> AsyncGenerator[ClientSession]:
        session = await self._session(check=check)
        self._in_flight += 1
        try:
            yield session
        finally:
            self._in_flight -= 1
            self._last_used = self._clock()
            self._arm_idle()

    async def _session(self, *, check: bool) -> ClientSession:
        async with self._lock:
            held = self._held
            if held is not None and held.session is not None:
                if not check or await self._healthy(held.session):
                    return held.session
            await self._drop_locked()
            return await self._connect_locked()

    async def _healthy(self, session: ClientSession) -> bool:
        try:
            async with asyncio.timeout(self.spec.connect_timeout):
                await session.send_ping()
        except Exception as exc:
            logger.warning("Serveur MCP %s : contrôle de santé raté (%r)", self.name, exc)
            return False
        return True

    async def _connect_locked(self) -> ClientSession:
        if self._closed:
            raise SourceUnavailable(self.name, "connexion fermée")
        now = self._clock()
        if now < self._retry_at:
            raise SourceUnavailable(
                self.name,
                f"{self._last_error} ; nouvelle tentative dans {self._retry_at - now:.0f} s",
            )
        held = _Held(self.name, self._factory, self)
        try:
            session = await held.start(self.spec.connect_timeout)
        except Exception as exc:
            message = (
                f"pas de réponse en {self.spec.connect_timeout:g} s"
                if isinstance(exc, TimeoutError)
                else _describe(exc)
            )
            delay = self._backoff[min(self._failures, len(self._backoff) - 1)]
            self._failures += 1
            self._retry_at = self._clock() + delay
            self._last_error = message
            raise SourceUnavailable(self.name, message) from exc
        self._failures = 0
        self._retry_at = 0.0
        self._held = held
        self._tools = None
        logger.info("Serveur MCP %s connecté", self.name)
        return session

    async def _list(self, session: ClientSession) -> tuple[types.Tool, ...]:
        tools: list[types.Tool] = []
        cursor: str | None = None
        try:
            while True:
                page = await session.list_tools(
                    params=types.PaginatedRequestParams(cursor=cursor) if cursor else None
                )
                tools += page.tools
                cursor = page.nextCursor
                if not cursor:
                    return tuple(tools)
        except Exception as exc:
            if is_connection_lost(exc):
                await self._drop()
            raise SourceUnavailable(self.name, f"liste des outils : {_describe(exc)}") from exc

    async def _drop(self) -> None:
        async with self._lock:
            await self._drop_locked()

    async def _drop_locked(self) -> None:
        held, self._held = self._held, None
        self._tools = None
        if held is not None:
            await held.close()

    def _arm_idle(self) -> None:
        if self._idle_timeout is None or self._closed:
            return
        if self._idle_task is None or self._idle_task.done():
            self._idle_task = asyncio.create_task(self._watch_idle(self._idle_timeout))

    async def _watch_idle(self, limit: float) -> None:
        while self._held is not None:
            # Pendant un appel, rien ne ferme : on revérifie un délai plus tard.
            wait = limit if self._in_flight else self._last_used + limit - self._clock()
            await asyncio.sleep(max(0.0, wait))
            idle = self._clock() - self._last_used
            if self._in_flight == 0 and idle >= limit:
                logger.info("Serveur MCP %s fermé après %.0f s d'inactivité", self.name, idle)
                await self._drop()
                return


def _describe(error: BaseException) -> str:
    """Message court d'une erreur de connexion, y compris dans un groupe d'exceptions."""
    cause = _innermost(error)
    if isinstance(cause, McpError):
        return cause.error.message
    kind = type(cause).__name__
    text = str(cause)
    return f"{kind}: {text}" if text else kind


def _innermost(error: BaseException) -> BaseException:
    """Première exception d'un groupe (anyio regroupe celles de ses tâches)."""
    if not isinstance(error, BaseExceptionGroup):
        return error
    inner = cast(tuple[BaseException, ...], error.exceptions)  # pyright: ignore[reportUnknownMemberType]
    return _innermost(inner[0]) if inner else cast(BaseException, error)
