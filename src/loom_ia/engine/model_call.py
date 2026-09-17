# SPDX-License-Identifier: Apache-2.0
"""Appel d'un modèle : fenêtre de contexte, délais, nouvelles tentatives (B3, B7).

La politique de retry est celle de loom-ia, pas celle des SDK (désactivée) :
chaque tentative ratée est annoncée par un ``model.retried``, que le moteur
écrit dans le journal avant d'attendre. Un appel relancé l'est en entier ; si
des morceaux avaient déjà été diffusés, ``on_chunk`` reçoit d'abord un
``StreamReset``.
"""

import asyncio
import json
import logging
import random
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import aclosing
from dataclasses import dataclass
from typing import Final

from loom_ia.core.events import ModelRetried
from loom_ia.core.model import (
    ModelRequest,
    ModelResponse,
    ModelSpec,
    ResponseAccumulator,
    StreamReset,
)
from loom_ia.core.ports import ChunkCallback, ModelClient, ModelError

logger = logging.getLogger(__name__)

# Approximation du nombre de caractères JSON par token (B7).
CHARS_PER_TOKEN: Final = 4

type Sleep = Callable[[float], Awaitable[None]]


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
        sleep: Sleep = asyncio.sleep,
        jitter: Callable[[], float] = random.random,
    ) -> None:
        self.client = client
        self.spec = spec
        self.on_chunk = on_chunk
        self._sleep = sleep
        self._jitter = jitter

    async def run(self, request: ModelRequest) -> AsyncGenerator[ModelRetried | ModelResponse]:
        """Émet un ``ModelRetried`` par tentative ratée, puis la réponse.

        Lève ``ModelError`` si l'erreur n'est pas rejouable ou si les tentatives
        sont épuisées.
        """
        self._check_context(request)
        attempt = 1
        while True:
            progress = _Progress()
            try:
                response = await self._attempt(request, progress)
            except ModelError as error:
                delay = self._retry_delay(error, attempt)
                if delay is None:
                    raise
                failure = error
            else:
                yield response
                return
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

    def _check_context(self, request: ModelRequest) -> None:
        window = self.spec.capabilities.context_window
        if window is None:
            return
        estimated = estimate_tokens(request)
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
        return accumulator.result(model_id=request.model_id, provider=self.client.provider)
