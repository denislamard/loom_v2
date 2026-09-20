# SPDX-License-Identifier: Apache-2.0
"""Chaîne de secours d'un appel de modèle (B4, #7, #10).

Un emplacement (``main``, un rôle, un juge) déclare son modèle et ses
secours, dans l'ordre : ``model: M3_MAIN, fallbacks: [SONNET]``.
``ModelChain`` appelle le modèle courant de l'emplacement, avec ses
nouvelles tentatives (``ModelCall``), puis passe au suivant selon l'erreur :

- ``transient``, ``overloaded`` : une fois les tentatives épuisées ;
- ``quota_exhausted`` : aussitôt ;
- ``context_overflow`` : vers le premier secours dont la fenêtre déclarée
  (``capabilities.context_window``) dépasse celle du modèle qui a débordé ;
- ``auth``, ``invalid_request``, ``content_filtered`` : jamais, l'appel échoue.

Un modèle écarté par son disjoncteur est sauté sans être appelé (motif
``circuit_open``). Chaque bascule écrit un ``model.fell_back`` ; un échec qui
ouvre un disjoncteur, un ``circuit.opened``. Si la fin de la chaîne est
écartée par son disjoncteur, l'appel échoue en ``unavailable``.

Adhérence : un run qui a basculé garde le secours pour cet emplacement
jusqu'à la fin (``RunState.models``, projeté des ``model.fell_back``) ; la
chaîne repart de ce modèle.

Requête d'un secours : celle du modèle précédent, avec l'identifiant, le
``max_tokens`` et les ``params`` du secours (ceux de l'emplacement
par-dessus). Le raisonnement produit par un autre modèle est retiré des
messages (#7) : un fournisseur ne relit que le sien. Le cache de prompt est
perdu, et le coût suit le tarif du secours.

Diffusion : si le modèle qui a échoué avait déjà diffusé des morceaux,
``on_chunk`` reçoit un ``StreamReset`` avant ceux du secours.
"""

import asyncio
import random
from collections.abc import AsyncGenerator, Callable, Mapping, Sequence
from contextlib import aclosing
from dataclasses import dataclass, field
from typing import Final

from pydantic import JsonValue

from loom_ia.core.events import CircuitOpened, FallbackReason, ModelFellBack, ModelRetried
from loom_ia.core.model import (
    ContentBlock,
    Message,
    ModelChunk,
    ModelErrorKind,
    ModelRequest,
    ModelResponse,
    ModelSpec,
    ReasoningBlock,
    StreamReset,
    TextBlock,
)
from loom_ia.core.ports import ArtifactStore, ChunkCallback, ModelClient, ModelError
from loom_ia.engine.circuit import CIRCUIT_ERRORS, CircuitBreakers, Tripped, model_key
from loom_ia.engine.model_call import ModelCall, Sleep

# Erreurs qui font passer au modèle suivant, tentatives épuisées.
FALLBACK_ERRORS: Final[frozenset[ModelErrorKind]] = frozenset(
    {"transient", "overloaded", "quota_exhausted"}
)

type ChainEvent = ModelRetried | ModelFellBack | CircuitOpened


@dataclass(frozen=True, slots=True)
class ModelLink:
    """Un modèle d'une chaîne : sa définition et son client."""

    spec: ModelSpec
    client: ModelClient


@dataclass(frozen=True, slots=True)
class Answered:
    """Réponse obtenue, et le modèle de la chaîne qui l'a donnée."""

    response: ModelResponse
    spec: ModelSpec
    # Requête envoyée à ce modèle, avant la résolution des fichiers.
    request: ModelRequest
    # Tentatives sur ce modèle, la dernière comprise.
    attempts: int


@dataclass
class _Tracked:
    """Relais de ``on_chunk`` qui note si des morceaux sont partis."""

    target: ChunkCallback
    delivered: bool = False

    async def __call__(self, chunk: ModelChunk) -> None:
        self.delivered = True
        await self.target(chunk)


@dataclass(frozen=True, kw_only=True)
class ModelChain:
    """Modèle d'un emplacement et ses secours, dans l'ordre."""

    links: Sequence[ModelLink]
    # ``main``, le nom d'un rôle ou ``judge:<nom>`` : recopié dans ``model.fell_back``.
    slot: str
    breakers: CircuitBreakers | None = None
    # Réglages de l'emplacement (B6) : remplacent ceux de chaque modèle.
    max_tokens: int | None = None
    params: Mapping[str, JsonValue] = field(default_factory=dict[str, JsonValue])
    on_chunk: ChunkCallback | None = None
    artifacts: ArtifactStore | None = None
    sleep: Sleep = asyncio.sleep
    jitter: Callable[[], float] = random.random

    def __post_init__(self) -> None:
        if not self.links:
            raise ValueError(f"Chaîne de modèles vide pour {self.slot!r}")

    def position(self, current: str | None) -> int:
        """Rang du modèle courant de l'emplacement ; 0 s'il n'a pas basculé."""
        for index, link in enumerate(self.links):
            if link.spec.id == current:
                return index
        # Aucun basculement, ou modèle retiré de la config depuis : début de la chaîne.
        return 0

    def link(self, current: str | None) -> ModelLink:
        """Modèle à appeler en premier : le courant de l'emplacement (adhérence)."""
        return self.links[self.position(current)]

    def request_for(self, spec: ModelSpec, request: ModelRequest) -> ModelRequest:
        """Requête adaptée à un modèle de la chaîne : ses réglages, son raisonnement seul."""
        return request.model_copy(
            update={
                "model_id": spec.model,
                "max_tokens": self.max_tokens or spec.max_tokens,
                "params": {**spec.params, **self.params},
                "messages": own_reasoning(request.messages, spec.model),
            }
        )

    async def run(
        self, request: ModelRequest, *, current: str | None = None
    ) -> AsyncGenerator[ChainEvent | Answered]:
        """Événements de l'appel (tentatives, bascules, disjoncteurs), puis la réponse.

        ``request`` est construite pour le modèle ``current`` (celui de
        ``link(current)``). Lève ``ModelError`` si aucun modèle n'a répondu.
        """
        index = start = self.position(current)
        tries = 0
        failure: ModelError | None = None
        skipped: list[str] = []
        moved: tuple[ModelLink, FallbackReason, str] | None = None
        while index < len(self.links):
            link = self.links[index]
            if moved is not None:
                source, reason, error = moved
                yield ModelFellBack(
                    slot=self.slot,
                    from_model=source.spec.id,
                    to_model=link.spec.id,
                    reason=reason,
                    error=error,
                )
            left = self._remaining(link)
            if left is not None:
                skipped.append(f"{link.spec.id} (encore {left:.0f} s)")
                moved = (link, "circuit_open", f"disjoncteur ouvert, encore {left:.0f} s")
                index += 1
                continue
            if index == start and request.model_id == link.spec.model:
                sent = request.model_copy(
                    update={"messages": own_reasoning(request.messages, link.spec.model)}
                )
            else:
                sent = self.request_for(link.spec, request)
            tracked = _Tracked(self.on_chunk) if self.on_chunk is not None else None
            call = ModelCall(
                link.client,
                link.spec,
                on_chunk=tracked,
                artifacts=self.artifacts,
                sleep=self.sleep,
                jitter=self.jitter,
            )
            attempts = 0
            response: ModelResponse | None = None
            try:
                async with aclosing(call.run(sent)) as outcomes:
                    async for outcome in outcomes:
                        attempts += 1
                        if isinstance(outcome, ModelResponse):
                            response = outcome
                        else:
                            yield outcome
            except ModelError as error:
                tries += attempts + 1
                tripped = self._failed(link, error)
                if tripped is not None:
                    yield CircuitOpened(
                        target_kind="model",
                        target=link.spec.id,
                        failures=tripped.failures,
                        cooldown_s=tripped.cooldown,
                        error=error.message,
                    )
                following = self._following(index, error)
                if following is None:
                    raise
                if tracked is not None and tracked.delivered and self.on_chunk is not None:
                    await self.on_chunk(StreamReset(attempt=tries + 1))
                failure = error
                moved = (link, error.kind, error.message)
                index = following
                continue
            if response is None:
                raise RuntimeError("Appel de modèle terminé sans réponse")
            if self.breakers is not None:
                self.breakers.succeeded(model_key(link.spec.id))
            yield Answered(response=response, spec=link.spec, request=sent, attempts=attempts)
            return
        raise _unavailable(skipped, failure)

    # --- Interne ---------------------------------------------------------

    def _remaining(self, link: ModelLink) -> float | None:
        if self.breakers is None or link.spec.circuit_breaker is None:
            return None
        return self.breakers.remaining(model_key(link.spec.id))

    def _failed(self, link: ModelLink, error: ModelError) -> Tripped | None:
        setting = link.spec.circuit_breaker
        if self.breakers is None or setting is None or error.kind not in CIRCUIT_ERRORS:
            return None
        return self.breakers.failed(model_key(link.spec.id), setting)

    def _following(self, index: int, error: ModelError) -> int | None:
        """Rang du modèle qui prend la suite après cette erreur, ou None : l'appel échoue."""
        if error.kind in FALLBACK_ERRORS:
            return index + 1 if index + 1 < len(self.links) else None
        if error.kind != "context_overflow":
            return None
        window = self.links[index].spec.capabilities.context_window
        if window is None:
            return None
        for following in range(index + 1, len(self.links)):
            larger = self.links[following].spec.capabilities.context_window
            if larger is not None and larger > window:
                return following
        return None


def own_reasoning(messages: Sequence[Message], model: str) -> tuple[Message, ...]:
    """Messages sans le raisonnement produit par un autre modèle (#7).

    Un raisonnement sans modèle (journal antérieur au marquage) est gardé.
    """
    kept: list[Message] = []
    for message in messages:
        blocks = tuple(b for b in message.blocks if not _foreign(b, model))
        if len(blocks) == len(message.blocks):
            kept.append(message)
        else:
            kept.append(message.model_copy(update={"blocks": blocks or (TextBlock(text=""),)}))
    return tuple(kept)


def _foreign(block: ContentBlock, model: str) -> bool:
    return isinstance(block, ReasoningBlock) and block.model_id not in (None, model)


def _unavailable(skipped: list[str], failure: ModelError | None) -> ModelError:
    """Fin de chaîne écartée par son disjoncteur."""
    message = f"écarté(s) par leur disjoncteur : {', '.join(skipped)}"
    if failure is not None:
        message += f" ; dernière erreur : model.{failure.kind} — {failure.message}"
    return ModelError("unavailable", message)
