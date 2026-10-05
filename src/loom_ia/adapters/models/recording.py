# SPDX-License-Identifier: Apache-2.0
"""Client HTTP qui garde ce qu'il envoie et reçoit (#31, J6.1b).

Passé au SDK à la place de son client par défaut, quand un client de loom a
demandé les échanges bruts (``telemetry.capture.raw_exchanges``). Il ne garde
rien de lui-même : il dépose chaque échange dans le registre que le moteur a
ouvert pour la tentative (``exchange_log``), et ne fait rien s'il n'y en a pas.

On enrobe ``send``, et non le transport : c'est par ``send`` que passent tous
les appels des SDK, et c'est au-dessus des proxys que l'environnement peut
poser — un transport enrobé serait contourné par eux.

Un corps de réponse diffusé (SSE) est recopié au fil de sa lecture, et
l'échange est déposé quand le flux se ferme : son corps est alors celui que
le SDK a lu, décompressé. Les réglages du client reprennent ceux des SDK
(connexions, redirections) ; seules les options de keepalive TCP du client
par défaut d'Anthropic ne sont pas reprises.
"""

import time
from collections.abc import AsyncIterator, Callable
from typing import Any, Final

import httpx2

from loom_ia.core.ports import ExchangeLog, RawExchange, exchange_log

# Ceux des clients par défaut d'Anthropic et d'OpenAI.
LIMITS: Final = httpx2.Limits(max_connections=1000, max_keepalive_connections=100)


class RecordingClient(httpx2.AsyncClient):
    """``httpx2.AsyncClient`` qui dépose ses échanges dans le registre ouvert."""

    def __init__(self, *, transport: httpx2.AsyncBaseTransport | None = None) -> None:
        # ``transport`` sert aux essais (``MockTransport``) ; sans lui, celui de httpx.
        super().__init__(limits=LIMITS, follow_redirects=True, transport=transport)

    async def send(
        self, request: httpx2.Request, *, stream: bool = False, **options: Any
    ) -> httpx2.Response:
        log = exchange_log()
        if log is None:
            return await super().send(request, stream=stream, **options)
        started = time.perf_counter()
        sent = await request.aread()
        try:
            response = await super().send(request, stream=stream, **options)
        except Exception as error:
            log.record(
                RawExchange(
                    method=request.method,
                    url=str(request.url),
                    request_body=sent,
                    request_headers=dict(request.headers.items()),
                    duration_ms=(time.perf_counter() - started) * 1000,
                    error=f"{type(error).__name__} : {error}",
                )
            )
            raise
        if not stream or response.is_stream_consumed:
            # Lue d'un bloc — à la demande du SDK, ou déjà chargée par le
            # transport : ``content`` est là, et déjà décompressé.
            _deposit(log, request, sent, response, response.content, started)
            return response
        inner = response.stream
        if not isinstance(inner, httpx2.AsyncByteStream):
            # Un client asynchrone rend un flux asynchrone ; autre chose ne se
            # recopie pas, et l'échange part sans corps de réponse.
            _deposit(log, request, sent, response, b"", started)
            return response
        response.stream = _Copied(
            inner,
            lambda body: _deposit(log, request, sent, response, _decoded(response, body), started),
        )
        return response


class _Copied(httpx2.AsyncByteStream):
    """Flux de réponse recopié au fil de la lecture ; la copie part à la fermeture."""

    def __init__(self, inner: httpx2.AsyncByteStream, done: Callable[[bytes], None]) -> None:
        self._inner = inner
        self._done = done
        self._parts: list[bytes] = []
        self._finished = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        async for chunk in self._inner:
            self._parts.append(chunk)
            yield chunk
        self._finish()

    async def aclose(self) -> None:
        try:
            await self._inner.aclose()
        finally:
            self._finish()

    def _finish(self) -> None:
        if not self._finished:
            self._finished = True
            self._done(b"".join(self._parts))


def _deposit(
    log: ExchangeLog,
    request: httpx2.Request,
    sent: bytes,
    response: httpx2.Response,
    received: bytes,
    started: float,
) -> None:
    log.record(
        RawExchange(
            method=request.method,
            url=str(request.url),
            request_body=sent,
            response_body=received,
            status=response.status_code,
            request_headers=dict(request.headers.items()),
            response_headers=dict(response.headers.items()),
            duration_ms=(time.perf_counter() - started) * 1000,
        )
    )


def _decoded(response: httpx2.Response, received: bytes) -> bytes:
    """Un flux recopié l'est avant décodage : le corps tel que le SDK l'a lu.

    Le décodage est celui de httpx, par une réponse jetable : mêmes
    algorithmes que pour le SDK, sans toucher à leurs rouages privés.
    """
    encoding = response.headers.get("content-encoding", "identity")
    if encoding in ("", "identity"):
        return received
    try:
        return httpx2.Response(
            200, headers={"content-encoding": encoding}, stream=httpx2.ByteStream(received)
        ).read()
    except httpx2.DecodingError:
        return received
