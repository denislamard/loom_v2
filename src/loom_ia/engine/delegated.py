# SPDX-License-Identifier: Apache-2.0
"""Outils délégués : rôles (C2) et sous-agents (C5).

Un outil ordinaire (port ``Tool``) reçoit ses arguments et rend un résultat.
Un outil délégué lit en plus l'état du run, et produit des événements pendant
son appel : les appels de modèle d'un rôle sont journalisés dans le span de
cet appel, avant son ``tool.completed``.

Le type est nominal et interne au moteur : un outil fourni par l'utilisateur
ne peut pas écrire dans le journal.

Un outil délégué peut dépendre du run : le rôle vision n'existe que si le run
a des pièces jointes, ``artifact_read`` que si un résultat a été déporté, un
sous-agent que si la profondeur le permet. ``available`` le dit ; un outil
indisponible n'est pas montré au modèle.

Un rôle peut être réparé (#20) : il rend, avec sa sortie, l'échange qui l'a
produite (``Exchange`` : requête et réponse). Si une politique ``after_tool``
refuse la sortie (``Retry``), l'exécuteur lui demande de réparer
(``repair``) : le même modèle, dans la même conversation, reçoit le
diagnostic et répond de nouveau.

Un sous-agent lance un run enfant (``child_run_id``) : son identifiant est
choisi avant l'appel et écrit dans le ``tool.called``, pour que la reprise
retrouve l'enfant. L'enfant écrit dans le journal du parent, par le même
écrivain (``SessionWriter``), et sa consommation revient au parent
(``Consumption``).
"""

from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator, Mapping
from dataclasses import dataclass, field
from typing import Self

from pydantic import JsonValue

from loom_ia.core.events import ModelResponded, ModelRetried
from loom_ia.core.model import (
    ArtifactRefBlock,
    Message,
    ModelRequest,
    PendingCall,
    RunId,
    RunState,
    SpanId,
    ToolOutput,
    ToolSpec,
    Usage,
)
from loom_ia.core.ports import ArtifactStore, ChunkCallback, ToolContext
from loom_ia.engine.refs import ResultIndex
from loom_ia.engine.writer import SessionWriter


@dataclass(frozen=True, slots=True)
class Consumption:
    """Consommation d'un run enfant, à ajouter à celle du parent."""

    usage: Usage
    cost_usd: float


@dataclass(frozen=True, slots=True)
class Exchange:
    """Conversation d'un appel délégué : la requête envoyée et la réponse retenue.

    Gardée par l'exécuteur le temps de l'appel, pour une réparation.
    """

    request: ModelRequest
    answer: Message


# Ce qu'un outil délégué peut produire pendant son appel, avant son résultat.
type DelegatedPayload = ModelRetried | ModelResponded


@dataclass(frozen=True, slots=True)
class RunView:
    """Ce qu'un outil délégué voit du run : état au début du lot, résultats, fichiers.

    ``writer`` écrit dans le journal du run ; ``spans`` donne le span de
    chaque appel du lot, et ``children`` le run enfant choisi pour un appel.
    """

    state: RunState
    results: ResultIndex
    artifacts: ArtifactStore | None = None
    writer: SessionWriter | None = None
    spans: Mapping[str, SpanId] = field(default_factory=dict[str, SpanId])
    children: Mapping[str, RunId] = field(default_factory=dict[str, RunId])
    # Diffusion en direct de la sortie d'un rôle terminal (backlog #009).
    on_chunk: ChunkCallback | None = None

    @classmethod
    def of(
        cls,
        state: RunState,
        artifacts: ArtifactStore | None = None,
        *,
        writer: SessionWriter | None = None,
        spans: Mapping[str, SpanId] | None = None,
    ) -> Self:
        return cls(
            state=state,
            results=ResultIndex(state.messages, artifacts),
            artifacts=artifacts,
            writer=writer,
            spans=dict(spans or {}),
        )

    @property
    def attachments(self) -> tuple[ArtifactRefBlock, ...]:
        """Pièces jointes de la demande, en références."""
        return tuple(
            ArtifactRefBlock(uri=a.uri, media_type=a.media_type, size=a.size, name=a.name)
            for a in self.state.attachments
        )


class DelegatedTool(ABC):
    """Outil exécuté par le moteur lui-même, qui lit le run et journalise son travail."""

    @property
    @abstractmethod
    def spec(self) -> ToolSpec: ...

    def available(self, run: RunView) -> bool:
        """Vrai si l'outil a un sens dans ce run ; sinon il est masqué au modèle."""
        return True

    def child_run_id(self, call: PendingCall) -> RunId | None:
        """Run enfant de cet appel, choisi avant son lancement ; None si l'outil n'en crée pas."""
        return None

    async def check(self, arguments: dict[str, JsonValue], run: RunView) -> str | None:
        """Motif de refus avant tout lancement, destiné au modèle ; None si l'appel peut partir.

        Un appel refusé ici n'a pas de ``tool.called`` : il n'a rien fait.
        """
        return None

    @abstractmethod
    def run(
        self, arguments: dict[str, JsonValue], context: ToolContext, run: RunView
    ) -> AsyncGenerator[DelegatedPayload | Consumption | Exchange | ToolOutput]:
        """Événements de l'appel, sa consommation (run enfant), son échange, puis son résultat."""
        ...

    def repair(
        self,
        exchange: Exchange,
        feedback: str,
        *,
        policy: str,
        context: ToolContext,
        run: RunView,
    ) -> AsyncGenerator[DelegatedPayload | Consumption | Exchange | ToolOutput] | None:
        """Nouvelle réponse après un refus (``Retry``), ou None si l'outil ne se répare pas."""
        return None
