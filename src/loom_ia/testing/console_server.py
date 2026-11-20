# SPDX-License-Identifier: Apache-2.0
"""``FakeConsole`` : une fausse console, pour les essais du canal et pour ``loom console listen``.

Elle fait ce que ferait la console pour une instance : elle contrôle la clé à
la poignée de main (en-tête ``Authorization``), répond ``welcome`` ou
``error``, garde tout ce qu'elle reçoit, et acquitte les lots d'événements.
Les essais la pilotent pour éprouver loom : couper la connexion, refuser la
clé ou le contrat, envoyer des commandes.

N'est pas importée par ``loom_ia.testing`` : elle demande ``websockets``
(extra ``console``).
"""

import asyncio
import hmac
import logging
import ssl
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from http import HTTPStatus
from typing import Self, cast

from websockets.asyncio.server import Server, ServerConnection, serve
from websockets.exceptions import ConnectionClosed
from websockets.http11 import Request, Response

from loom_ia.console.contract import (
    AckPayload,
    ContractError,
    Envelope,
    ErrorPayload,
    EventsPayload,
    HelloPayload,
    Payload,
    WelcomePayload,
    decode,
    encode,
)

logger = logging.getLogger(__name__)

type Watcher = Callable[["Received"], Awaitable[None] | None]
"""Appelé à chaque message reçu, ou à chaque événement de connexion (``loom console listen`` affiche)."""


@dataclass(frozen=True, slots=True)
class Received:
    """Ce que la fausse console a vu : un message, ou un événement de connexion.

    Attributes:
        kind: Type du message, ou ``connected``, ``refused``, ``closed``.
        envelope: L'enveloppe, pour un message.
        payload: La charge, pour un message.
        peer: Adresse de l'instance.
        detail: Ce qui accompagne un événement de connexion (motif d'un refus…).
    """

    kind: str
    envelope: Envelope | None = None
    payload: Payload | None = None
    peer: str = ""
    detail: str = ""


@dataclass(slots=True)
class FakeConsole:
    """Une fausse console qui écoute en WebSocket, contrôle la clé et acquitte.

    Args:
        key: Clé d'instance attendue ; ``None`` accepte toute connexion.
        contracts: Contrats qu'elle sait servir ; le plus récent commun est retenu.
        host: Adresse d'écoute.
        port: Port d'écoute ; 0 en tire un libre.
        ssl_context: Contexte TLS serveur, pour éprouver ``wss://`` et le mTLS.
        auto_ack: Acquitte chaque lot d'événements reçu.
        watcher: Appelé pour chaque chose reçue.
    """

    key: str | None = None
    contracts: Sequence[int] = (1,)
    host: str = "127.0.0.1"
    port: int = 0
    ssl_context: ssl.SSLContext | None = None
    auto_ack: bool = True
    watcher: Watcher | None = None
    received: list[Received] = field(default_factory=list[Received])
    _server: Server | None = None
    _connections: list[ServerConnection] = field(default_factory=list[ServerConnection])
    _arrived: asyncio.Condition = field(default_factory=asyncio.Condition)
    _tasks: set[asyncio.Task[None]] = field(default_factory=set[asyncio.Task[None]])

    async def __aenter__(self) -> Self:
        """Commence à écouter."""
        await self.start()
        return self

    async def __aexit__(self, *exc: object) -> None:
        """Ferme les connexions et cesse d'écouter."""
        await self.aclose()

    async def start(self) -> None:
        """Commence à écouter, sur ``port`` (ou un port libre)."""
        self._server = await serve(self._serve, self.host, self.port, process_request=self._check_key, ssl=self.ssl_context)
        self.port = self._server.sockets[0].getsockname()[1]

    async def aclose(self) -> None:
        """Ferme les connexions ouvertes et cesse d'écouter."""
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    async def serve_forever(self) -> None:
        """Écoute jusqu'à l'annulation (``loom console listen``)."""
        if self._server is None:
            await self.start()
        assert self._server is not None
        await self._server.serve_forever()

    @property
    def url(self) -> str:
        """L'adresse à donner à loom (``console.url``)."""
        scheme = "ws" if self.ssl_context is None else "wss"
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"{scheme}://{host}:{self.port}/loom"

    @property
    def connected(self) -> int:
        """Connexions ouvertes."""
        return len(self._connections)

    def of(self, kind: str) -> list[Received]:
        """Ce qui a été reçu de ce type, dans l'ordre."""
        return [item for item in self.received if item.kind == kind]

    async def wait_for(self, kind: str, count: int = 1) -> list[Received]:
        """Attend d'avoir reçu ``count`` choses de ce type (à borner par ``asyncio.timeout``)."""
        async with self._arrived:
            await self._arrived.wait_for(lambda: len(self.of(kind)) >= count)
        return self.of(kind)

    async def send(self, payload: Payload, *, contract: int = 1) -> None:
        """Envoie un message à chaque instance connectée."""
        for connection in tuple(self._connections):
            try:
                await connection.send(encode(payload, contract=contract))
            except ConnectionClosed:
                continue

    async def drop(self) -> None:
        """Coupe chaque connexion sans ``bye`` : éprouve la reconnexion de loom."""
        for connection in tuple(self._connections):
            connection.transport.abort()

    def _check_key(self, connection: ServerConnection, request: Request) -> Response | None:
        """Refuse la poignée de main sans la bonne clé (401), comme la console."""
        if self.key is None:
            return None
        given = request.headers.get("Authorization", "")
        if hmac.compare_digest(given.encode(), f"Bearer {self.key}".encode()):
            return None
        peer = _peer(connection)
        self._note(Received("refused", peer=peer, detail="clé absente" if not given else "clé refusée"))
        return connection.respond(HTTPStatus.UNAUTHORIZED, "clé de l'instance refusée\n")

    async def _serve(self, connection: ServerConnection) -> None:
        """Une instance : ``hello``, ``welcome`` ou refus, puis réception jusqu'à la fermeture."""
        peer = _peer(connection)
        try:
            contract = await self._handshake(connection, peer)
            if contract is None:
                return
            self._connections.append(connection)
            async for frame in connection:
                await self._received(connection, frame, peer, contract)
        except ConnectionClosed:
            pass
        finally:
            if connection in self._connections:
                self._connections.remove(connection)
                self._note(Received("closed", peer=peer, detail=connection.close_reason or ""))

    async def _handshake(self, connection: ServerConnection, peer: str) -> int | None:
        """Lit ``hello`` et retient le contrat le plus récent commun ; ``None`` s'il n'y en a pas."""
        try:
            envelope, payload = decode(await connection.recv())
        except ContractError as error:
            await connection.send(encode(ErrorPayload(code=error.code, message=str(error)), contract=None))
            return None
        if not isinstance(payload, HelloPayload):
            await connection.send(encode(ErrorPayload(code="invalid_message", message="hello attendu"), contract=None))
            return None
        self._note(Received("hello", envelope, payload, peer))
        common = sorted(set(payload.contracts) & set(self.contracts), reverse=True)
        if not common:
            message = f"la console sert {sorted(self.contracts)}, loom propose {sorted(payload.contracts)}"
            await connection.send(encode(ErrorPayload(code="no_common_contract", message=message), contract=None))
            self._note(Received("refused", peer=peer, detail=message))
            await connection.close()
            return None
        await connection.send(encode(WelcomePayload(contract=common[0]), contract=common[0]))
        self._note(Received("connected", peer=peer, detail=f"contrat {common[0]}"))
        return common[0]

    async def _received(self, connection: ServerConnection, frame: str | bytes, peer: str, contract: int) -> None:
        """Un message : gardé, puis acquitté si c'est un lot d'événements."""
        try:
            envelope, payload = decode(frame)
        except ContractError as error:
            self._note(Received("invalid", peer=peer, detail=str(error)))
            await connection.send(encode(ErrorPayload(code=error.code, message=str(error)), contract=contract))
            return
        self._note(Received(envelope.type, envelope, payload, peer))
        if self.auto_ack and isinstance(payload, EventsPayload):
            await connection.send(encode(AckPayload(batch_id=payload.batch_id), contract=contract))
            self._note(Received("ack", peer=peer, detail=payload.batch_id))

    def _note(self, item: Received) -> None:
        """Garde ce qui arrive, puis prévient ceux qui l'attendent et le veilleur, dans l'ordre d'arrivée."""
        self.received.append(item)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = loop.create_task(self._announce(item))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _announce(self, item: Received) -> None:
        async with self._arrived:
            self._arrived.notify_all()
        if self.watcher is not None:
            try:
                shown = self.watcher(item)
                if shown is not None:
                    await shown
            except Exception:
                logger.exception("Fausse console : le veilleur a échoué sur %s", item.kind)


def _peer(connection: ServerConnection) -> str:
    """L'adresse de l'instance, en ``hôte:port``."""
    address = cast("tuple[object, ...] | str | None", connection.remote_address)
    if isinstance(address, tuple) and len(address) >= 2:
        return f"{address[0]}:{address[1]}"
    return str(address)
