# SPDX-License-Identifier: Apache-2.0
"""Faux modèle scripté, pour les tests et les exemples (fonction O2).

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
"""Une réponse du script : un message, ou une exception à lever."""
type ScriptItem = Reply | Callable[[ModelRequest], Reply]
"""Un élément du script : une réponse, ou une fonction qui la tire de la requête."""

DEFAULT_USAGE = Usage(input_tokens=100, output_tokens=20)
"""Usage annoncé par défaut à chaque réponse : 100 tokens en entrée, 20 en sortie."""


class ScriptExhausted(AssertionError):
    """Le modèle a été appelé plus de fois que prévu par le script."""


class ScriptedModel:
    """Implémente ``ModelClient`` en rejouant un script.

    Les requêtes reçues sont gardées dans ``requests`` ; l'usage annoncé est
    le même pour chaque réponse.

    Args:
        *script: Réponses, dans l'ordre : message, exception à lever, ou
            fonction ``(requête) -> réponse``.
        provider: Nom du fournisseur annoncé (``fake``).
        usage: Usage annoncé à chaque réponse.
        fragment_size: Taille des morceaux de texte diffusés.
        delay: Pause avant chaque morceau, en secondes (latence simulée).

    Attributes:
        usage: Usage annoncé à chaque réponse.
        fragment_size: Taille des morceaux.
        delay: Pause avant chaque morceau.
        requests: Les requêtes reçues, dans l'ordre.
    """

    def __init__(
        self,
        *script: ScriptItem,
        provider: str = "fake",
        usage: Usage = DEFAULT_USAGE,
        fragment_size: int = 8,
        delay: float = 0.0,
    ) -> None:
        """Garde le script ; les arguments sont décrits dans la docstring de la classe."""
        self._script: deque[ScriptItem] = deque(script)
        self._provider = provider
        self.usage = usage
        self.fragment_size = fragment_size
        # Pause avant chaque morceau, pour simuler la latence.
        self.delay = delay
        self.requests: list[ModelRequest] = []

    @property
    def provider(self) -> str:
        """Nom du fournisseur annoncé."""
        return self._provider

    @property
    def remaining(self) -> int:
        """Réponses du script pas encore consommées."""
        return len(self._script)

    def add(self, *script: ScriptItem) -> None:
        """Ajoute des réponses à la fin du script."""
        self._script.extend(script)

    async def aclose(self) -> None:
        """Ne ferme rien."""
        pass

    async def stream(self, request: ModelRequest) -> AsyncGenerator[ModelChunk]:
        """Consomme la réponse suivante du script et la diffuse en morceaux.

        Args:
            request: La requête, gardée dans ``requests``.

        Yields:
            Les morceaux de la réponse, avec l'usage.

        Raises:
            ScriptExhausted: Le script est épuisé.
            Exception: L'exception que le script prévoit à cet appel.
        """
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
