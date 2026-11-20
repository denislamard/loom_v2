# SPDX-License-Identifier: Apache-2.0
"""La connexion WSS sortante vers la console : TLS, clé d'instance, poignée de main, reconnexion (docs/analyse-exortateur-loom.md, § 5 et § 6.5).

loom ouvre la connexion, et c'est la seule : rien n'entre dans loom par
ailleurs. Elle ne met jamais un run en attente — une console injoignable ou
lente n'arrête et ne ralentit aucun run (objectif 1) ; ``send`` rend faux
quand la connexion n'est pas ouverte, sans attendre.

- **TLS** : la vérification du certificat de la console ne se désactive pas ;
  TLS 1.2 au moins ; ``ca_file`` ajoute une autorité à celles du système ; le
  certificat client (mTLS) se lit sur disque, sa clé privée dans
  l'environnement — chargée par un fichier en mémoire (``memfd``), jamais
  écrite sur le disque.
- **Clé d'instance** : présentée à la poignée de main, dans l'en-tête
  ``Authorization: Bearer …`` ; jamais dans l'adresse, ni dans un log.
- **Poignée de main** : ``hello`` (contrats que loom sait servir), puis
  ``welcome`` (contrat retenu) ou ``error`` et fermeture.
- **Reconnexion** : attente exponentielle avec une part d'aléatoire, plafonnée,
  pour que toutes les instances ne reviennent pas ensemble après une panne de
  la console. Une clé ou un contrat refusés attendent bien plus longtemps, avec
  un avertissement clair : réessayer vite n'y changerait rien.
- Les pings sont ceux de WebSocket, que ``websockets`` gère.
"""

import asyncio
import logging
import os
import random
import ssl
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, InvalidStatus, WebSocketException

from loom_ia.config.console import ConsoleConfig
from loom_ia.console.contract import (
    CONTRACTS,
    ByePayload,
    CommandPayload,
    CommandResultPayload,
    ContractError,
    Envelope,
    ErrorPayload,
    HelloPayload,
    Payload,
    WelcomePayload,
    decode,
    encode,
)

logger = logging.getLogger(__name__)

RETRY_BASE: Final = 1.0
"""Première attente avant de se reconnecter, en secondes."""

RETRY_CAP: Final = 60.0
"""Attente maximale entre deux tentatives ordinaires, en secondes."""

REFUSED_DELAY: Final = 300.0
"""Attente après une clé ou un contrat refusés, en secondes : réessayer vite n'y changerait rien."""

HANDSHAKE_TIMEOUT: Final = 10.0
"""Délai d'ouverture de la connexion, puis d'attente du ``welcome``, en secondes."""

CLOSE_TIMEOUT: Final = 5.0
"""Délai laissé à la fermeture propre (``bye``), en secondes."""

MAX_FRAME: Final = 4 * 1024 * 1024
"""Taille maximale d'une trame reçue, en octets."""

type ChannelState = Literal["idle", "connecting", "connected", "refused", "closed"]
"""État du canal : jamais ouvert, en cours, ouvert, refusé par la console (clé, contrat), fermé."""

type MessageHandler = Callable[[Envelope, Payload], Awaitable[None]]
"""Ce que le canal appelle pour chaque message reçu après la poignée de main."""


class ChannelRefused(Exception):
    """La console refuse la connexion : clé refusée ou aucun contrat commun."""


@dataclass(frozen=True, slots=True)
class ChannelSettings:
    """Ce qu'il faut pour se connecter : adresse, clé, TLS, et le ``hello`` à présenter.

    Attributes:
        url: Adresse de la console (``wss://…``, ou ``ws://`` en dev).
        key: Clé de l'instance ; jamais journalisée.
        hello: Ce que loom dit de lui à chaque connexion.
        ssl_context: Contexte TLS ; ``None`` pour ``ws://``.
    """

    url: str
    key: str
    hello: HelloPayload
    ssl_context: ssl.SSLContext | None = None

    def __repr__(self) -> str:
        """Sans la clé : un repr finit souvent dans un log."""
        return f"ChannelSettings(url={self.url!r}, key=***, hello={self.hello!r})"


def tls_context(console: ConsoleConfig, environ: Mapping[str, str]) -> ssl.SSLContext | None:
    """Le contexte TLS de la console : ``None`` pour ``ws://``.

    Args:
        console: La section, activée.
        environ: Où lire la clé privée du certificat client.

    Returns:
        Un contexte qui vérifie toujours le certificat de la console, TLS 1.2
        au moins, avec l'autorité et le certificat client déclarés.

    Raises:
        ValueError: Variable de la clé du certificat client vide.
        OSError: Fichier d'autorité ou de certificat illisible.
        ssl.SSLError: Certificat ou clé invalides.
    """
    if console.scheme != "wss":
        return None
    context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    tls = console.tls
    if tls.ca_file is not None:
        context.load_verify_locations(cafile=tls.ca_file)
    if tls.client_cert is not None and tls.client_key_env is not None:
        key = environ.get(tls.client_key_env, "")
        if not key:
            raise ValueError(f"console.tls : la variable {tls.client_key_env} (clé du certificat client) est vide")
        _load_client_cert(context, tls.client_cert, key)
    return context


def _load_client_cert(context: ssl.SSLContext, cert: Path, key: str) -> None:
    """Charge le certificat client et sa clé, la clé passant par un fichier en mémoire, jamais écrit sur le disque."""
    fd = os.memfd_create("loom-console-key", os.MFD_CLOEXEC)
    try:
        os.write(fd, key.encode())
        context.load_cert_chain(certfile=cert, keyfile=f"/proc/self/fd/{fd}")
    finally:
        os.close(fd)


def retry_delay(failures: int, *, rand: Callable[[float, float], float] = random.uniform) -> float:
    """L'attente avant la tentative suivante : exponentielle, plafonnée, et pour moitié tirée au hasard.

    Args:
        failures: Échecs consécutifs, à partir de 1.
        rand: Le tirage (remplacé dans les essais).

    Returns:
        L'attente, en secondes.
    """
    ceiling = min(RETRY_CAP, RETRY_BASE * 2 ** max(0, failures - 1))
    return ceiling / 2 + rand(0.0, ceiling / 2)


class ConsoleChannel:
    """Tient la connexion vers la console, la rouvre quand elle tombe, et remet ce qui arrive.

    ``run`` tourne jusqu'à ``aclose`` : connexion, poignée de main, réception ;
    puis attente et nouvelle tentative. ``send`` envoie sur la connexion
    ouverte, ou rend faux tout de suite.

    Args:
        settings: Adresse, clé, TLS et ``hello``.
        on_message: Appelé pour chaque message reçu après la poignée de main ;
            sans lui, une commande est refusée (``commands_disabled``) et le
            reste est ignoré.
    """

    def __init__(self, settings: ChannelSettings, *, on_message: MessageHandler | None = None) -> None:
        self._settings = settings
        self._on_message = on_message
        self._connection: ClientConnection | None = None
        self._state: ChannelState = "idle"
        self._contract: int | None = None
        self._closing = False
        self._connected = asyncio.Event()
        self._stop = asyncio.Event()

    @property
    def state(self) -> ChannelState:
        """L'état du canal."""
        return self._state

    @property
    def contract(self) -> int | None:
        """Le contrat de la connexion ouverte ; ``None`` sans connexion."""
        return self._contract if self._state == "connected" else None

    @property
    def url(self) -> str:
        """L'adresse de la console."""
        return self._settings.url

    async def wait_connected(self) -> None:
        """Attend que la connexion soit ouverte (à borner par ``asyncio.timeout``)."""
        await self._connected.wait()

    async def run(self) -> None:
        """Se connecte, sert la connexion, et recommence jusqu'à ``aclose``.

        N'échoue jamais : une panne est journalisée, puis on attend avant de
        réessayer. Une annulation arrête la boucle.
        """
        failures = 0
        while not self._closing:
            self._state = "connecting"
            delay: float
            try:
                await self._session()
                failures = 0
                delay = RETRY_BASE
            except ChannelRefused as refusal:
                self._state = "refused"
                logger.warning("Console %s : %s ; nouvelle tentative dans %g s", self._settings.url, refusal, REFUSED_DELAY)
                delay = REFUSED_DELAY
            except (OSError, TimeoutError, WebSocketException, ContractError) as error:
                failures += 1
                delay = retry_delay(failures)
                logger.warning("Console %s injoignable (%s) ; nouvelle tentative dans %.1f s", self._settings.url, _said(error), delay)
            except asyncio.CancelledError:
                raise
            except Exception:
                failures += 1
                delay = retry_delay(failures)
                logger.exception("Console %s : erreur du canal ; nouvelle tentative dans %.1f s", self._settings.url, delay)
            finally:
                self._connection = None
                self._connected.clear()
                self._contract = None
            if self._closing:
                break
            if self._state != "refused":
                self._state = "connecting"
            try:
                async with asyncio.timeout(delay):
                    await self._stop.wait()
            except TimeoutError:
                pass
        self._state = "closed"

    async def send(self, payload: Payload) -> bool:
        """Envoie un message sur la connexion ouverte.

        Args:
            payload: La charge.

        Returns:
            Vrai si le message est parti ; faux sans connexion ouverte ou si elle
            vient de tomber — l'appelant le renverra, rien n'attend ici.
        """
        connection = self._connection
        if connection is None or self._state != "connected":
            return False
        try:
            await connection.send(encode(payload, contract=self._contract))
        except ConnectionClosed:
            return False
        return True

    async def aclose(self, reason: str = "shutdown") -> None:
        """Dit ``bye`` si la connexion est ouverte, la ferme, et arrête ``run``.

        Args:
            reason: Motif du ``bye`` (``shutdown``, ``reload``…).
        """
        self._closing = True
        self._stop.set()
        connection = self._connection
        if connection is None:
            return
        try:
            async with asyncio.timeout(CLOSE_TIMEOUT):
                if self._state == "connected":
                    await connection.send(encode(ByePayload(reason=reason), contract=self._contract))
                await connection.close()
        except (TimeoutError, ConnectionClosed, OSError) as error:
            logger.debug("Console %s : fermeture écourtée (%s)", self._settings.url, _said(error))

    async def _session(self) -> None:
        """Une connexion : ouverture, poignée de main, puis réception jusqu'à sa fin."""
        settings = self._settings
        try:
            connection = await connect(
                settings.url,
                additional_headers={"Authorization": f"Bearer {settings.key}"},
                ssl=settings.ssl_context,
                open_timeout=HANDSHAKE_TIMEOUT,
                max_size=MAX_FRAME,
                user_agent_header=f"loom-ia/{settings.hello.loom_version}",
            )
        except InvalidStatus as error:
            status = error.response.status_code
            if status in (401, 403):
                raise ChannelRefused(f"clé de l'instance refusée (HTTP {status}) — vérifier la variable de 'console.key_env'") from None
            raise
        async with connection:
            self._connection = connection
            self._contract = await self._handshake(connection)
            self._state = "connected"
            self._connected.set()
            logger.info("Console %s : connectée, contrat %d", settings.url, self._contract)
            async for frame in connection:
                await self._received(connection, frame)

    async def _handshake(self, connection: ClientConnection) -> int:
        """Présente ``hello`` et attend ``welcome`` ; rend le contrat retenu."""
        hello = self._settings.hello
        await connection.send(encode(hello, contract=None))
        async with asyncio.timeout(HANDSHAKE_TIMEOUT):
            frame = await connection.recv()
        _, payload = decode(frame)
        if isinstance(payload, ErrorPayload):
            if payload.code == "no_common_contract":
                raise ChannelRefused(
                    f"aucun contrat commun (loom sait servir {', '.join(map(str, CONTRACTS))}) : {payload.message} — mettre à jour loom ou la console"
                )
            if payload.code == "unauthorized":
                raise ChannelRefused(f"clé de l'instance refusée : {payload.message}")
            raise ContractError("invalid_message", f"poignée de main refusée ({payload.code}) : {payload.message}")
        if not isinstance(payload, WelcomePayload):
            raise ContractError("invalid_message", "la console devait répondre welcome")
        if payload.contract not in hello.contracts:
            raise ChannelRefused(f"la console retient le contrat {payload.contract}, que loom ne sert pas ({', '.join(map(str, hello.contracts))})")
        return payload.contract

    async def _received(self, connection: ClientConnection, frame: str | bytes) -> None:
        """Un message reçu : décodé, puis remis ; un message non conforme reçoit ``error``."""
        try:
            envelope, payload = decode(frame)
        except ContractError as error:
            logger.warning("Console %s : message refusé (%s)", self._settings.url, error)
            await connection.send(encode(ErrorPayload(code=error.code, message=str(error)), contract=self._contract))
            return
        if isinstance(payload, ByePayload):
            logger.info("Console %s : fermeture demandée (%s)", self._settings.url, payload.reason)
            await connection.close()
            return
        if isinstance(payload, ErrorPayload):
            logger.warning("Console %s : erreur %s — %s", self._settings.url, payload.code, payload.message)
            return
        if self._on_message is not None:
            try:
                await self._on_message(envelope, payload)
            except Exception:
                logger.exception("Console %s : message %s non traité", self._settings.url, envelope.type)
            return
        if isinstance(payload, CommandPayload):
            refused = CommandResultPayload(command_id=payload.command_id, outcome="refused", reason="commands_disabled")
            await connection.send(encode(refused, contract=self._contract))


def _said(error: BaseException) -> str:
    """Une erreur en une ligne, sans rien de la clé : son type, et son message s'il en a un."""
    text = str(error).strip()
    return f"{type(error).__name__}: {text}" if text else type(error).__name__
