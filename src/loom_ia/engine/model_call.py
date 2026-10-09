# SPDX-License-Identifier: Apache-2.0
"""Appel d'un modèle : fichiers, fenêtre de contexte, délais, nouvelles tentatives (B3, B7, #14).

La politique de retry est celle de loom-ia, pas celle des SDK (désactivée) :
chaque tentative ratée est annoncée par un ``model.retried``, que le moteur
écrit dans le journal avant d'attendre. Un appel relancé l'est en entier ; si
des morceaux avaient déjà été diffusés, ``on_chunk`` reçoit d'abord un
``StreamReset``.

Avant la première tentative, les références de fichiers de la requête sont
résolues selon les capacités du modèle (``MediaResolver``) ; la fenêtre de
contexte est estimée sur la requête non résolue, plus une part fixe par image.

Fin de l'appel : une tentative ne rend une réponse que si son flux a une fin
propre (``Stopped``) et n'a pas été arrêté par la limite de tokens. Un flux
coupé ou vide est une erreur ``transient`` (nouvelles tentatives, secours,
disjoncteur) ; une sortie arrêtée par ``max_tokens`` est une erreur
``truncated``, définitive : refaire le même appel couperait au même endroit.
La réponse d'un journal (``AnsweringClient``) n'est pas contrôlée : elle a été
acceptée quand le journal a été écrit, et le rejeu la ressert telle quelle.

Échanges bruts (J6.1b) : avec ``raw_max_bytes``, chaque tentative ouvre un
registre (``recording``) où le client HTTP de l'adaptateur dépose ce qu'il a
envoyé et reçu ; à la fin de la tentative, réussie ou non, chaque échange sort
en ``model.exchanged``, **avant** le ``model.retried`` ou la réponse.
"""

import asyncio
import json
import logging
import random
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import aclosing, nullcontext
from dataclasses import dataclass
from typing import Final

from loom_ia.core.events import ModelExchanged, ModelResponded, ModelRetried
from loom_ia.core.model import (
    Message,
    ModelRequest,
    ModelResponse,
    ModelSpec,
    ResponseAccumulator,
    StreamReset,
)
from loom_ia.core.ports import (
    AnsweringClient,
    ArtifactStore,
    ChunkCallback,
    ExchangeLog,
    ModelClient,
    ModelError,
    recording,
    require_end,
)
from loom_ia.engine.exchange import Recorded, exchanged
from loom_ia.engine.media import IMAGE_TOKENS, MediaResolver

logger = logging.getLogger(__name__)

# Approximation du nombre de caractères JSON par token (B7).
CHARS_PER_TOKEN: Final = 4

type Sleep = Callable[[float], Awaitable[None]]


def responded(
    request: ModelRequest,
    response: ModelResponse,
    spec: ModelSpec,
    *,
    attempts: int,
    latency_ms: float,
    message: Message | None = None,
    call_id: str | None = None,
) -> ModelResponded:
    """Événement d'une réponse obtenue ; ``message`` remplace celui de la réponse."""
    return ModelResponded(
        model_id=response.model_id,
        provider=response.provider,
        message=message if message is not None else response.message,
        usage=response.usage,
        cost_usd=spec.pricing.cost(response.usage),
        stop_reason=response.stop_reason,
        latency_ms=latency_ms,
        attempts=attempts,
        request_hash=request.request_hash(),
        request_parts=request.request_parts(),
        call_id=call_id,
    )


def estimate_tokens(request: ModelRequest) -> int:
    """Estimation grossière des tokens d'entrée d'une requête."""
    payload = request.model_dump(mode="json", include={"system", "messages", "tools"})
    return len(json.dumps(payload, ensure_ascii=False)) // CHARS_PER_TOKEN


@dataclass
class _Progress:
    """Morceaux déjà diffusés pendant une tentative."""

    delivered: int = 0


class ModelCall:
    """Exécute les appels d'un modèle selon sa définition."""

    def __init__(
        self,
        client: ModelClient,
        spec: ModelSpec,
        *,
        on_chunk: ChunkCallback | None = None,
        artifacts: ArtifactStore | None = None,
        sleep: Sleep = asyncio.sleep,
        jitter: Callable[[], float] = random.random,
    ) -> None:
        self.client = client
        self.spec = spec
        self.on_chunk = on_chunk
        self.media = MediaResolver(spec, artifacts)
        self._sleep = sleep
        self._jitter = jitter
        # Borne d'un corps brut ; ``None`` : les échanges ne sont pas gardés.
        # C'est le client qui la porte (``Recorded``) : monté pour un client de
        # loom qui a demandé ses échanges, il les garde partout où il sert —
        # orchestrateur, rôles, juges, secours.
        self.raw_max_bytes = client.raw_max_bytes if isinstance(client, Recorded) else None

    async def run(
        self, request: ModelRequest
    ) -> AsyncGenerator[ModelExchanged | ModelRetried | ModelResponse]:
        """Émet un ``ModelRetried`` par tentative ratée, puis la réponse.

        Avec la capture des échanges bruts, chaque tentative émet d'abord ses
        ``ModelExchanged``. Lève ``ModelError`` si l'erreur n'est pas
        rejouable, si les tentatives sont épuisées, ou si un fichier de la
        requête ne peut pas être envoyé — après les échanges de la dernière
        tentative, qui sont justement ceux qu'on voudra lire.
        """
        self._check_context(request)
        sent = await self.media.resolve(request)
        attempt = 1
        while True:
            progress = _Progress()
            log = ExchangeLog() if self.raw_max_bytes is not None else None
            failure: ModelError | None = None
            response: ModelResponse | None = None
            try:
                # Le registre n'est ouvert que le temps de la tentative, sans
                # rien céder entre-temps : il reste dans le contexte de la tâche.
                with recording(log) if log is not None else nullcontext():
                    response = await self._attempt(sent, progress)
            except ModelError as error:
                failure = error
            for payload in self._exchanged(log, sent, attempt):
                yield payload
            if failure is None:
                assert response is not None
                yield response
                return
            delay = self._retry_delay(failure, attempt)
            if delay is None:
                raise failure
            logger.warning(
                "Tentative %d/%d du modèle %s ratée (%s) : nouvel essai dans %.1f s",
                attempt,
                self.spec.retry.max_attempts,
                self.spec.id,
                failure.kind,
                delay,
            )
            yield ModelRetried(
                model_id=request.model_id,
                provider=self.client.provider,
                attempt=attempt,
                error_kind=failure.kind,
                error=failure.message,
                http_status=failure.http_status,
                delay_s=delay,
            )
            attempt += 1
            if progress.delivered and self.on_chunk is not None:
                await self.on_chunk(StreamReset(attempt=attempt))
            await self._sleep(delay)

    # --- Interne ---------------------------------------------------------

    def _exchanged(
        self, log: ExchangeLog | None, request: ModelRequest, attempt: int
    ) -> list[ModelExchanged]:
        if log is None or self.raw_max_bytes is None:
            return []
        return [
            exchanged(
                raw,
                attempt=attempt,
                model_id=request.model_id,
                provider=self.client.provider,
                max_bytes=self.raw_max_bytes,
            )
            for raw in log.exchanges
        ]

    def _check_context(self, request: ModelRequest) -> None:
        window = self.spec.capabilities.context_window
        if window is None:
            return
        estimated = estimate_tokens(request) + IMAGE_TOKENS * self.media.images(request)
        needed = estimated + (request.max_tokens or 0)
        if needed > window:
            raise ModelError(
                "context_overflow",
                f"Requête estimée à {estimated} tokens, plus {request.max_tokens or 0} en "
                f"sortie : dépasse la fenêtre de {window} tokens du modèle {self.spec.id}",
            )

    def _retry_delay(self, error: ModelError, attempt: int) -> float | None:
        """Attente avant la tentative suivante, ou None pour abandonner."""
        policy = self.spec.retry
        if not error.retryable or attempt >= policy.max_attempts:
            return None
        if error.retry_after is not None:
            return error.retry_after if error.retry_after <= policy.max_delay else None
        return policy.backoff(attempt, self._jitter())

    async def _attempt(self, request: ModelRequest, progress: _Progress) -> ModelResponse:
        if isinstance(self.client, AnsweringClient):
            # Rejeu (J6.2a) : la réponse est connue, entière ; sans flux, rien
            # ne se perd en route. En variante (J6.2b), une requête inconnue
            # du journal part pour de vrai, par le flux.
            known = await self.client.answer(request)
            if known is not None:
                return known
        timeouts = self.spec.timeouts
        accumulator = ResponseAccumulator()
        total = asyncio.timeout(timeouts.total)
        waiting = "premier morceau"
        try:
            async with total, aclosing(self.client.stream(request)) as chunks:
                limit = timeouts.first_token
                while True:
                    gap = asyncio.timeout(limit)
                    try:
                        async with gap:
                            chunk = await anext(chunks)
                    except StopAsyncIteration:
                        break
                    except TimeoutError as exc:
                        if gap.expired():
                            raise ModelError(
                                "transient", f"Délai dépassé : aucun {waiting} en {limit:g} s"
                            ) from exc
                        raise
                    accumulator.add(chunk)
                    if self.on_chunk is not None:
                        progress.delivered += 1
                        await self.on_chunk(chunk)
                    limit, waiting = timeouts.idle, "nouveau morceau"
        except TimeoutError as exc:
            if total.expired():
                raise ModelError(
                    "transient", f"Délai total dépassé : {timeouts.total:g} s"
                ) from exc
            raise
        # Seul le vrai appel est contrôlé : la réponse d'un journal (``answer``) revient plus haut.
        if accumulator.stop_reason == "max_tokens":
            produced = accumulator.usage.output_tokens
            after = f" après {produced} tokens" if produced else ""
            raise ModelError(
                "truncated",
                f"Réponse tronquée : le modèle {self.spec.id} s'est arrêté sur sa limite de "
                f"tokens (max_tokens ou fenêtre de contexte){after} ; une sortie coupée n'est "
                "pas une réponse. Augmenter max_tokens ou raccourcir la demande.",
            )
        require_end(accumulator)
        return accumulator.result(model_id=request.model_id, provider=self.client.provider)
