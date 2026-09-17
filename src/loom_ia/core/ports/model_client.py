# SPDX-License-Identifier: Apache-2.0
"""Port des modèles (#11).

Un adaptateur n'implémente qu'une chose : le flux de morceaux neutres.
``complete`` en déduit la réponse complète.
"""

from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Protocol

from loom_ia.core.model.streaming import (
    ModelChunk,
    ModelRequest,
    ModelResponse,
    ResponseAccumulator,
)

type ChunkCallback = Callable[[ModelChunk], Awaitable[None]]


class ModelClient(Protocol):
    @property
    def provider(self) -> str:
        """Nom du fournisseur, recopié dans ``model.responded``."""
        ...

    def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
        """Morceaux de la réponse, dans l'ordre d'arrivée."""
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
    async for chunk in client.stream(request):
        accumulator.add(chunk)
        if on_chunk is not None:
            await on_chunk(chunk)
    return accumulator.result(model_id=request.model_id, provider=client.provider)
