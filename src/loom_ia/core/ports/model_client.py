# SPDX-License-Identifier: Apache-2.0
"""Port des modèles : le client d'un fournisseur et ses erreurs (points 10 et 11, fonctions B1 à B3).

Un adaptateur n'implémente qu'une chose : le flux de morceaux neutres
(streaming comme primitive de base, point 11). ``complete`` en déduit la
réponse complète. Toute erreur du fournisseur est traduite en ``ModelError``,
dont le genre décide des nouvelles tentatives et de la bascule vers un modèle
de secours.

Implémentations fournies (``adapters/models``) : Anthropic (``sdk:
anthropic``), OpenAI en API ``chat`` et ``responses`` (``sdk: openai``, aussi
pour Together, vLLM, Ollama…) et un modèle simulé (``sdk: fake``).
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
from loom_ia.core.model.usage import Usage

type ChunkCallback = Callable[[ModelChunk], Awaitable[None]]
"""Fonction appelée avec chaque morceau du flux, au passage (diffusion en direct)."""

RETRYABLE_ERRORS: Final[frozenset[ModelErrorKind]] = frozenset({"transient", "overloaded"})
"""Genres d'erreur qui donnent lieu à de nouvelles tentatives sur le même modèle."""


class ModelError(Exception):
    """Échec d'un appel de modèle, classé de façon neutre (fonction B3).

    Genres (``kind``) et suite donnée :

    - ``transient``, ``overloaded`` : nouvelles tentatives, puis bascule vers
      le secours ;
    - ``quota_exhausted`` : bascule aussitôt ;
    - ``context_overflow`` : bascule vers un secours à plus grande fenêtre ;
    - ``auth``, ``invalid_request``, ``content_filtered`` : l'appel échoue ;
    - ``truncated`` : sortie coupée par la limite de tokens (``max_tokens``) :
      jamais une réponse, l'appel échoue ;
    - ``unavailable`` : toute la chaîne est écartée par ses disjoncteurs.

    Args:
        kind: Genre de l'erreur.
        message: Message, repris au journal (``model.retried``,
            ``run.failed``).
        http_status: Statut HTTP de la réponse du fournisseur, s'il y en a
            eu une.
        retry_after: Délai demandé par le fournisseur (``Retry-After``), en
            secondes.
        by_client: Vrai si l'appel a été arrêté à dessein par le client de
            modèle lui-même — le rejeu qui s'arrête à sa première divergence
            (phase 6.2b) — et non par une panne : le moteur le note sans le
            crier.

    Attributes:
        kind: Genre de l'erreur.
        message: Le message.
        http_status: Statut HTTP, ou ``None``.
        retry_after: Délai demandé, ou ``None``.
        by_client: Arrêt voulu par le client.
        usage: Ce que le fournisseur a annoncé avoir consommé avant l'échec,
            quand il l'a dit (début d'un flux coupé, sortie tronquée) :
            facturé quand même. ``None`` par défaut ; l'adaptateur le pose.
    """

    def __init__(
        self,
        kind: ModelErrorKind,
        message: str,
        *,
        http_status: int | None = None,
        retry_after: float | None = None,
        by_client: bool = False,
    ) -> None:
        """Crée l'erreur ; les arguments sont décrits dans la docstring de la classe."""
        super().__init__(message)
        self.kind: ModelErrorKind = kind
        self.message = message
        self.http_status = http_status
        self.retry_after = retry_after
        self.by_client = by_client
        self.usage: Usage | None = None

    @property
    def retryable(self) -> bool:
        """Vrai si l'erreur donne lieu à de nouvelles tentatives (``transient``, ``overloaded``)."""
        return self.kind in RETRYABLE_ERRORS

    def __repr__(self) -> str:
        return f"ModelError({self.kind!r}, {self.message!r}, http_status={self.http_status})"


def stopped_by_client(error: BaseException) -> bool:
    """Vrai si une erreur, ou ce qui l'a causée, est un arrêt voulu par le client de modèle.

    Un juge enrobe l'erreur de son modèle dans une erreur de politique : on
    remonte donc la chaîne des causes (``__cause__``).

    Args:
        error: L'erreur reçue.

    Returns:
        Vrai si un ``ModelError`` de la chaîne porte ``by_client``.
    """
    seen: BaseException | None = error
    while seen is not None:
        if isinstance(seen, ModelError) and seen.by_client:
            return True
        seen = seen.__cause__
    return False


class ModelClient(Protocol):
    """Client d'un fournisseur de modèles : il rend la réponse en flux de morceaux neutres."""

    @property
    def provider(self) -> str:
        """Nom du fournisseur, recopié dans ``model.responded``."""
        ...

    def stream(self, request: ModelRequest) -> AsyncGenerator[ModelChunk]:
        """Appelle le modèle et rend sa réponse en morceaux, dans l'ordre d'arrivée.

        Args:
            request: La requête neutre : modèle, système, messages, outils,
                réglages, fichiers résolus.

        Yields:
            Les morceaux neutres : texte, appels d'outils, raisonnement,
            consommation, puis un ``Stopped`` final.

        Raises:
            ModelError: Toute erreur du fournisseur, classée par genre.
        """
        ...

    async def aclose(self) -> None:
        """Libère les connexions du client."""
        ...


class AnsweringClient(ABC):
    """Client qui peut connaître d'avance sa réponse entière, et la rend telle quelle (phase 6.2a).

    C'est le client du rejeu : ses réponses viennent du journal. Passer par le
    flux de morceaux les reconstruirait, et la reconstruction perd ce que les
    morceaux ne portent pas (les métadonnées d'un bloc de texte ou d'un appel
    d'outil) — la requête suivante, qui contient cette réponse, aurait alors une
    autre empreinte, et le rejeu verrait une divergence qu'il a lui-même créée.
    ``ModelCall`` le reconnaît et prend sa réponse sans flux.

    En variante (phase 6.2b), une requête que le journal ne connaît pas part pour
    de vrai : ``answer`` rend alors ``None``, et ``ModelCall`` lit le flux du
    client, comme pour tout autre.
    """

    @abstractmethod
    async def answer(self, request: ModelRequest) -> ModelResponse | None:
        """La réponse connue à une requête, sans appel au modèle.

        Args:
            request: La requête d'origine, références de fichiers non
                résolues : celle dont l'empreinte est au journal. ``stream``
                reçoit la même, fichiers résolus.

        Returns:
            La réponse enregistrée ; ``None`` : il faut la demander au modèle
            (``stream``), ce que fait la variante.

        Raises:
            ModelError: Le client refuse la requête (rejeu identique : elle
                n'est pas au journal).
        """

    @abstractmethod
    def stream(self, request: ModelRequest) -> AsyncGenerator[ModelChunk]:
        """Morceaux de la réponse d'un vrai appel, quand ``answer`` n'en a pas ; comme ``ModelClient.stream``."""


def require_end(accumulator: ResponseAccumulator) -> None:
    """Vérifie qu'un flux s'est terminé proprement, par un ``Stopped``.

    Connexion coupée, flux vide : ce que le flux a livré est un début de
    réponse, pas une réponse, et ne doit jamais finir en run réussi. L'erreur
    est rejouable, donc soumise aux nouvelles tentatives, au secours et au
    disjoncteur. Une réponse proprement terminée, même sans contenu, n'est pas
    concernée.

    Args:
        accumulator: Ce que le flux a livré.

    Raises:
        ModelError: Genre ``transient`` (« Flux interrompu : le fournisseur
            a fermé le flux sans signal de fin »).
    """
    if not accumulator.stopped:
        raise ModelError("transient", "Flux interrompu : le fournisseur a fermé le flux sans signal de fin")


async def complete(
    client: ModelClient,
    request: ModelRequest,
    *,
    on_chunk: ChunkCallback | None = None,
) -> ModelResponse:
    """Consomme le flux d'un client et renvoie la réponse complète.

    Args:
        client: Le client de modèle.
        request: La requête.
        on_chunk: Reçoit chaque morceau au passage (diffusion en direct).

    Returns:
        La réponse assemblée, avec le nom du modèle et du fournisseur.

    Raises:
        ModelError: Erreur du fournisseur, ou flux sans fin propre
            (``require_end``).
    """
    accumulator = ResponseAccumulator()
    async with aclosing(client.stream(request)) as chunks:
        async for chunk in chunks:
            accumulator.add(chunk)
            if on_chunk is not None:
                await on_chunk(chunk)
    require_end(accumulator)
    return accumulator.result(model_id=request.model_id, provider=client.provider)
