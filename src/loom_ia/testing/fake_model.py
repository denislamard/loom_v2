# SPDX-License-Identifier: Apache-2.0
"""Faux modèle scripté, pour les tests et les exemples (O2).

Chaque appel consomme la réponse suivante du script et la découpe en
morceaux, comme le ferait un vrai fournisseur. Un élément du script peut
être un message, une exception à lever, ou une fonction qui reçoit la
requête et renvoie l'un des deux.

    model = ScriptedModel(
        tool_call_message(("c1", "calculer", {"expr": "12*7+3"})),
        Message.assistant("87"),
    )
"""

import asyncio
from collections import deque
from collections.abc import AsyncGenerator, Callable

from loom_ia.core.model import (
    Message,
    ModelChunk,
    ModelRequest,
    Usage,
    message_to_chunks,
)

type Reply = Message | Exception
type ScriptItem = Reply | Callable[[ModelRequest], Reply]

DEFAULT_USAGE = Usage(input_tokens=100, output_tokens=20)


class ScriptExhausted(AssertionError):
    """Le modèle a été appelé plus de fois que prévu par le script."""


class ScriptedModel:
    """Implémente ``ModelClient`` en rejouant un script."""

    def __init__(
        self,
        *script: ScriptItem,
        provider: str = "fake",
        usage: Usage = DEFAULT_USAGE,
        fragment_size: int = 8,
        delay: float = 0.0,
    ) -> None:
        self._script: deque[ScriptItem] = deque(script)
        self._provider = provider
        self.usage = usage
        self.fragment_size = fragment_size
        # Pause avant chaque morceau, pour simuler la latence.
        self.delay = delay
        self.requests: list[ModelRequest] = []

    @property
    def provider(self) -> str:
        return self._provider

    @property
    def remaining(self) -> int:
        """Réponses du script pas encore consommées."""
        return len(self._script)

    def add(self, *script: ScriptItem) -> None:
        self._script.extend(script)

    async def aclose(self) -> None:
        pass

    async def stream(self, request: ModelRequest) -> AsyncGenerator[ModelChunk]:
        self.requests.append(request)
        if not self._script:
            raise ScriptExhausted(f"Appel n°{len(self.requests)} non prévu par le script")
        item = self._script.popleft()
        reply = item(request) if callable(item) else item
        if isinstance(reply, Exception):
            raise reply
        for chunk in message_to_chunks(reply, usage=self.usage, fragment_size=self.fragment_size):
            if self.delay:
                await asyncio.sleep(self.delay)
            yield chunk
