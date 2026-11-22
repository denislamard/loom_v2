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
    CONTRACTS,
    AckPayload,
    ContractError,
    Envelope,
    ErrorPayload,
    EventsPayload,
    HelloPayload,
    Payload,
    SecuritySyncPayload,
    SessionDeletedPayload,
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
        contracts: Contrats qu'elle sait servir ; le plus récent commun est
            retenu. Par défaut, ceux de loom.
        host: Adresse d'écoute.
        port: Port d'écoute ; 0 en tire un libre.
        ssl_context: Contexte TLS serveur, pour éprouver ``wss://`` et le mTLS.
        auto_ack: Acquitte chaque lot d'événements et chaque ``session.deleted`` reçus.
        watcher: Appelé pour chaque chose reçue.
        security: Part envoyée juste après ``welcome`` à une instance dont le
            ``hello`` dit ``security_source: console`` (``sync`` la change).
    """

    key: str | None = None
    contracts: Sequence[int] = CONTRACTS
    host: str = "127.0.0.1"
    port: int = 0
    ssl_context: ssl.SSLContext | None = None
    auto_ack: bool = True
    watcher: Watcher | None = None
    security: SecuritySyncPayload | None = None
    received: list[Received] = field(default_factory=list[Received])
    _server: Server | None = None
    _connections: dict[ServerConnection, int] = field(default_factory=dict[ServerConnection, int])
    _shared: set[ServerConnection] = field(default_factory=set[ServerConnection])
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

    async def wait_until(self, check: Callable[[], bool]) -> None:
        """Attend que ``check`` soit vrai, réévalué à chaque chose reçue (à borner par ``asyncio.timeout``)."""
        async with self._arrived:
            await self._arrived.wait_for(check)

    async def send(self, payload: Payload, *, contract: int | None = None) -> None:
        """Envoie un message à chaque instance connectée, sous le contrat de sa connexion (ou ``contract``)."""
        for connection, retained in tuple(self._connections.items()):
            try:
                await connection.send(encode(payload, contract=retained if contract is None else contract))
            except ConnectionClosed:
                continue

    async def sync(self, part: SecuritySyncPayload) -> None:
        """Retient la part et l'envoie à chaque instance connectée en sécurité partagée, comme la console à chaque modification.

        Args:
            part: La part (``console.shared.make_part``).
        """
        self.security = part
        for connection in tuple(self._shared):
            contract = self._connections.get(connection)
            if contract is None:
                continue
            try:
                await connection.send(encode(part, contract=contract))
            except ConnectionClosed:
                continue
        self._note(Received("sync", payload=part, detail=f"version {part.version}"))

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
            self._connections[connection] = contract
            if connection in self._shared and self.security is not None:
                await connection.send(encode(self.security, contract=contract))
            async for frame in connection:
                await self._received(connection, frame, peer, contract)
        except ConnectionClosed:
            pass
        finally:
            self._shared.discard(connection)
            if connection in self._connections:
                del self._connections[connection]
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
        if payload.security_source == "console":
            self._shared.add(connection)
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
        if not self.auto_ack:
            return
        if isinstance(payload, EventsPayload):
            await self.ack(payload.batch_id)
        elif isinstance(payload, SessionDeletedPayload):
            await self.ack(envelope.id)

    async def ack(self, batch_id: str) -> None:
        """Acquitte un lot (ou un ``session.deleted``, par son identifiant de message), comme la console une fois enregistré."""
        for connection, contract in tuple(self._connections.items()):
            try:
                await connection.send(encode(AckPayload(batch_id=batch_id), contract=contract))
            except ConnectionClosed:
                continue
        self._note(Received("ack", detail=batch_id))

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
