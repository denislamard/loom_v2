# SPDX-License-Identifier: Apache-2.0
"""Port des modèles (#10, #11).

Un adaptateur n'implémente qu'une chose : le flux de morceaux neutres.
``complete`` en déduit la réponse complète. Toute erreur du fournisseur est
traduite en ``ModelError``, dont le type décide des nouvelles tentatives.
"""

from abc import ABC, abstractmethod
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
        by_client: bool = False,
    ) -> None:
        super().__init__(message)
        self.kind: ModelErrorKind = kind
        self.message = message
        self.http_status = http_status
        # Délai demandé par le fournisseur (``Retry-After``), en secondes.
        self.retry_after = retry_after
        # L'appel a été arrêté par le client de modèle lui-même, à dessein — le
        # rejeu qui s'arrête à sa première divergence (J6.2b) —, et non par une
        # panne : le moteur le note sans le crier.
        self.by_client = by_client

    @property
    def retryable(self) -> bool:
        return self.kind in RETRYABLE_ERRORS

    def __repr__(self) -> str:
        return f"ModelError({self.kind!r}, {self.message!r}, http_status={self.http_status})"


def stopped_by_client(error: BaseException) -> bool:
    """Vrai si ``error``, ou ce qui l'a causée, est un arrêt voulu par le client de modèle.

    Un juge enrobe l'erreur de son modèle dans une erreur de politique : on
    remonte donc la chaîne des causes.
    """
    seen: BaseException | None = error
    while seen is not None:
        if isinstance(seen, ModelError) and seen.by_client:
            return True
        seen = seen.__cause__
    return False


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


class AnsweringClient(ABC):
    """Client qui peut connaître d'avance sa réponse entière, et la rend telle quelle (J6.2a).

    C'est le client du rejeu : ses réponses viennent du journal. Passer par le
    flux de morceaux les reconstruirait, et la reconstruction perd ce que les
    morceaux ne portent pas (les métadonnées d'un bloc de texte ou d'un appel
    d'outil) — la requête suivante, qui contient cette réponse, aurait alors une
    autre empreinte, et le rejeu verrait une divergence qu'il a lui-même créée.
    ``ModelCall`` le reconnaît et prend sa réponse sans flux.

    En variante (J6.2b), une requête que le journal ne connaît pas part pour
    de vrai : ``answer`` rend alors ``None``, et ``ModelCall`` lit le flux du
    client, comme pour tout autre.
    """

    @abstractmethod
    async def answer(self, request: ModelRequest) -> ModelResponse | None:
        """La réponse connue à cette requête ; ``None`` : la demander au modèle (``stream``).

        ``request`` est la requête d'origine, références de fichiers non résolues : celle dont
        l'empreinte est au journal. ``stream`` reçoit la même, fichiers résolus.

        ``ModelError`` si le client refuse la requête (rejeu identique : elle
        n'est pas au journal).
        """

    @abstractmethod
    def stream(self, request: ModelRequest) -> AsyncGenerator[ModelChunk]:
        """Morceaux de la réponse d'un vrai appel, quand ``answer`` n'en a pas."""


def require_end(accumulator: ResponseAccumulator) -> None:
    """Lève ``ModelError("transient")`` si le flux s'est arrêté sans ``Stopped``.

    Connexion coupée, flux vide : ce que le flux a livré est un début de
    réponse, pas une réponse, et ne doit jamais finir en run réussi. L'erreur
    est rejouable, donc soumise aux nouvelles tentatives, au secours et au
    disjoncteur. Une réponse proprement terminée, même sans contenu, n'est pas
    concernée.
    """
    if not accumulator.stopped:
        raise ModelError(
            "transient", "Flux interrompu : le fournisseur a fermé le flux sans signal de fin"
        )


async def complete(
    client: ModelClient,
    request: ModelRequest,
    *,
    on_chunk: ChunkCallback | None = None,
) -> ModelResponse:
    """Consomme le flux et renvoie la réponse complète.

    ``on_chunk`` reçoit chaque morceau au passage (diffusion en direct).
    ``ModelError`` si le flux n'a pas de fin propre (``require_end``).
    """
    accumulator = ResponseAccumulator()
    async with aclosing(client.stream(request)) as chunks:
        async for chunk in chunks:
            accumulator.add(chunk)
            if on_chunk is not None:
                await on_chunk(chunk)
    require_end(accumulator)
    return accumulator.result(model_id=request.model_id, provider=client.provider)
