# SPDX-License-Identifier: Apache-2.0
"""Outils délégués : rôles (C2), puis sous-agents (C5).

Un outil ordinaire (port ``Tool``) reçoit ses arguments et rend un résultat.
Un outil délégué lit en plus l'état du run, et produit des événements pendant
son appel : les appels de modèle d'un rôle sont journalisés dans le span de
cet appel, avant son ``tool.completed``.

Le type est nominal et interne au moteur : un outil fourni par l'utilisateur
ne peut pas écrire dans le journal.

Un outil délégué peut dépendre du run : le rôle vision n'existe que si le run
a des pièces jointes, ``artifact_read`` que si un résultat a été déporté.
``available`` le dit ; un outil indisponible n'est pas montré au modèle.
"""

from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Self

from pydantic import JsonValue

from loom_ia.core.events import ModelResponded, ModelRetried
from loom_ia.core.model import ArtifactRefBlock, RunState, ToolOutput, ToolSpec
from loom_ia.core.ports import ArtifactStore, ToolContext
from loom_ia.engine.refs import ResultIndex

# Événements qu'un outil délégué peut produire.
type DelegatedPayload = ModelRetried | ModelResponded


@dataclass(frozen=True, slots=True)
class RunView:
    """Ce qu'un outil délégué voit du run : état au début du lot, résultats, fichiers."""

    state: RunState
    results: ResultIndex
    artifacts: ArtifactStore | None = None

    @classmethod
    def of(cls, state: RunState, artifacts: ArtifactStore | None = None) -> Self:
        return cls(
            state=state,
            results=ResultIndex(state.messages, artifacts),
            artifacts=artifacts,
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

    async def check(self, arguments: dict[str, JsonValue], run: RunView) -> str | None:
        """Motif de refus avant tout lancement, destiné au modèle ; None si l'appel peut partir.

        Un appel refusé ici n'a pas de ``tool.called`` : il n'a rien fait.
        """
        return None

    @abstractmethod
    def run(
        self, arguments: dict[str, JsonValue], context: ToolContext, run: RunView
    ) -> AsyncGenerator[DelegatedPayload | ToolOutput]:
        """Événements de l'appel, puis son résultat en dernier."""
        ...
