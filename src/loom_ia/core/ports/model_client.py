# SPDX-License-Identifier: Apache-2.0
"""Port des modèles (#10, #11).

Un adaptateur n'implémente qu'une chose : le flux de morceaux neutres.
``complete`` en déduit la réponse complète. Toute erreur du fournisseur est
traduite en ``ModelError``, dont le type décide des nouvelles tentatives.
"""

from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import aclosing
from typing import Final, Protocol

from loom_ia.core.model.streaming import (
    ModelChunk,
    ModelErrorKind,
    ModelRequest,
    ModelResponse,
    ResponseAccumulator,
)

type ChunkCallback = Callable[[ModelChunk], Awaitable[None]]

RETRYABLE_ERRORS: Final[frozenset[ModelErrorKind]] = frozenset({"transient", "overloaded"})


class ModelError(Exception):
    """Échec d'un appel de modèle, classé de façon neutre."""

    def __init__(
        self,
        kind: ModelErrorKind,
        message: str,
        *,
        http_status: int | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.kind: ModelErrorKind = kind
        self.message = message
        self.http_status = http_status
        # Délai demandé par le fournisseur (``Retry-After``), en secondes.
        self.retry_after = retry_after

    @property
    def retryable(self) -> bool:
        return self.kind in RETRYABLE_ERRORS

    def __repr__(self) -> str:
        return f"ModelError({self.kind!r}, {self.message!r}, http_status={self.http_status})"


class ModelClient(Protocol):
    @property
    def provider(self) -> str:
        """Nom du fournisseur, recopié dans ``model.responded``."""
        ...

    def stream(self, request: ModelRequest) -> AsyncGenerator[ModelChunk]:
        """Morceaux de la réponse, dans l'ordre d'arrivée.

        Les erreurs du fournisseur sont levées en ``ModelError``.
        """
        ...

    async def aclose(self) -> None:
        """Libère les connexions."""
        ...


async def complete(
    client: ModelClient,
    request: ModelRequest,
    *,
    on_chunk: ChunkCallback | None = None,
) -> ModelResponse:
    """Consomme le flux et renvoie la réponse complète.

    ``on_chunk`` reçoit chaque morceau au passage (diffusion en direct).
    """
    accumulator = ResponseAccumulator()
    async with aclosing(client.stream(request)) as chunks:
        async for chunk in chunks:
            accumulator.add(chunk)
            if on_chunk is not None:
                await on_chunk(chunk)
    return accumulator.result(model_id=request.model_id, provider=client.provider)
