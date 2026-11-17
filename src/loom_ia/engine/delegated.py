# SPDX-License-Identifier: Apache-2.0
"""Outils délégués : rôles (fonction C2) et sous-agents (fonction C5).

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

Un rôle peut être réparé (point 20) : il rend, avec sa sortie, l'échange qui l'a
produite (``Exchange`` : requête et réponse). Si une politique ``after_tool``
refuse la sortie (``Retry``), l'exécuteur lui demande de réparer
(``repair``) : le même modèle, dans la même conversation, reçoit le
diagnostic et répond de nouveau.

Un sous-agent lance un run enfant (``child_run_id``) : son identifiant est
choisi avant l'appel et écrit dans le ``tool.called``, pour que la reprise
retrouve l'enfant. L'enfant écrit dans le journal du parent, par le même
écrivain (``SessionWriter``), et sa consommation revient au parent
(``Consumption``).

Implémentations : ``RoleTool`` (``engine/roles.py``), ``AgentTool``
(``engine/subagents.py``), ``ArtifactReadTool`` (``engine/offload.py``).
"""

from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Self

from pydantic import JsonValue

from loom_ia.core.events import (
    CircuitOpened,
    ModelExchanged,
    ModelFellBack,
    ModelResponded,
    ModelRetried,
    RunScope,
)
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
    """Consommation d'un run enfant, à ajouter à celle du parent.

    Attributes:
        usage: Tokens consommés par l'enfant.
        cost_usd: Coût de l'enfant, en dollars.
    """

    usage: Usage
    cost_usd: float


@dataclass(frozen=True, slots=True)
class Waiting:
    """Le run délégué s'est arrêté en attendant une décision humaine (fonction H4, point 17).

    Ce n'est pas un résultat : l'appel reste en suspens, sans ``tool.completed``.
    Le parent passe en ``WAITING_CHILD`` et reprendra en rejouant cet appel,
    lequel reprendra l'enfant là où il en était.

    Attributes:
        call_id: Appel du parent qui reste en suspens.
        run_id: Run délégué qui attend : celui du sous-agent.
    """

    call_id: str
    run_id: RunId


@dataclass(frozen=True, slots=True)
class Exchange:
    """Conversation d'un appel délégué : la requête envoyée et la réponse retenue.

    Gardée par l'exécuteur le temps de l'appel, pour une réparation.

    Attributes:
        request: Requête envoyée au modèle.
        answer: Réponse retenue.
        model: Modèle de la chaîne qui a répondu (identifiant dans la
            config) : c'est lui qui répare.
    """

    request: ModelRequest
    answer: Message
    model: str | None = None


type DelegatedPayload = ModelExchanged | ModelRetried | ModelFellBack | CircuitOpened | ModelResponded
"""Ce qu'un outil délégué peut produire pendant son appel, avant son résultat."""


@dataclass(frozen=True, slots=True)
class RunView:
    """Ce qu'un outil délégué voit du run : état au début du lot, résultats, fichiers.

    ``writer`` écrit dans le journal du run et ``scope`` situe ce qu'il écrit ;
    ``spans`` donne le span de chaque appel du lot, et ``children`` le run
    enfant choisi pour un appel.

    Attributes:
        state: État du run au début du lot.
        results: Index des résultats d'outils du run (``ResultIndex``), pour
            les références ``$ref``.
        artifacts: Magasin de fichiers, s'il y en a un.
        writer: Écrivain du journal du run.
        scope: Run qui écrit : champs communs des événements.
        spans: Span de chaque appel du lot, par ``call_id``.
        step_span: Span de l'étape en cours : parent de celui de chaque
            appel du lot.
        children: Run enfant choisi pour un appel, par ``call_id``.
        on_chunk: Diffusion en direct de la sortie d'un rôle terminal
            (backlog #009).
        turns: Historique de la session avant ce run, groupé par tour ; un
            rôle le reçoit s'il le déclare (point 12).
        summary: Dernier résumé de compaction de la session ; même règle.
        session_state: State de la session au début du lot, tel que les
            outils et les rôles le lisent : clés déclarées, valeurs par
            défaut comprises. Vide dans un sous-run, qui ne le reçoit pas
            (décision D3 du state de session).
    """

    state: RunState
    results: ResultIndex
    artifacts: ArtifactStore | None = None
    writer: SessionWriter | None = None
    scope: RunScope | None = None
    spans: Mapping[str, SpanId] = field(default_factory=dict[str, SpanId])
    step_span: SpanId | None = None
    children: Mapping[str, RunId] = field(default_factory=dict[str, RunId])
    on_chunk: ChunkCallback | None = None
    turns: tuple[tuple[Message, ...], ...] = ()
    summary: str | None = None
    session_state: Mapping[str, JsonValue] = field(default_factory=dict[str, JsonValue])

    @property
    def session(self) -> tuple[Message, ...]:
        """Historique de la session avant ce run, à plat."""
        return tuple(message for turn in self.turns for message in turn)

    @classmethod
    def of(
        cls,
        state: RunState,
        artifacts: ArtifactStore | None = None,
        *,
        writer: SessionWriter | None = None,
        scope: RunScope | None = None,
        spans: Mapping[str, SpanId] | None = None,
        step_span: SpanId | None = None,
        turns: tuple[tuple[Message, ...], ...] = (),
        summary: str | None = None,
        session_state: Mapping[str, JsonValue] | None = None,
    ) -> Self:
        """Vue d'un run, l'index des résultats construit depuis ses messages.

        Args:
            state: État du run.
            artifacts: Magasin de fichiers.
            writer: Écrivain du journal du run.
            scope: Run qui écrit.
            spans: Span de chaque appel du lot.
            step_span: Span de l'étape en cours.
            turns: Historique de la session avant ce run, par tour.
            summary: Dernier résumé de compaction.
            session_state: State de la session ; copié en lecture seule.

        Returns:
            La vue ; ``children`` et ``on_chunk`` restent vides.
        """
        return cls(
            state=state,
            results=ResultIndex(state.messages, artifacts),
            artifacts=artifacts,
            writer=writer,
            scope=scope,
            spans=dict(spans or {}),
            step_span=step_span,
            turns=turns,
            summary=summary,
            session_state=MappingProxyType(dict(session_state or {})),
        )

    @property
    def attachments(self) -> tuple[ArtifactRefBlock, ...]:
        """Pièces jointes de la demande, en références."""
        return tuple(ArtifactRefBlock(uri=a.uri, media_type=a.media_type, size=a.size, name=a.name) for a in self.state.attachments)


class DelegatedTool(ABC):
    """Outil exécuté par le moteur lui-même, qui lit le run et journalise son travail.

    Interne au moteur : un outil fourni par l'utilisateur ne peut pas en être
    un. L'exécuteur appelle ``available``, ``child_run_id``, ``check``, puis
    ``run``, et ``repair`` si une politique ``after_tool`` rend ``Retry``.
    """

    @property
    @abstractmethod
    def spec(self) -> ToolSpec:
        """Description de l'outil montrée au modèle : nom, description, schéma des arguments."""
        ...

    def available(self, run: RunView) -> bool:
        """Vrai si l'outil a un sens dans ce run ; sinon il est masqué au modèle.

        Args:
            run: La vue du run.
        """
        return True

    def child_run_id(self, call: PendingCall) -> RunId | None:
        """Run enfant de cet appel, choisi avant son lancement ; ``None`` si l'outil n'en crée pas.

        L'identifiant est écrit dans le ``tool.called``, pour que la reprise
        retrouve l'enfant.

        Args:
            call: L'appel du modèle.
        """
        return None

    async def check(self, arguments: dict[str, JsonValue], run: RunView) -> str | None:
        """Motif de refus avant tout lancement, destiné au modèle ; None si l'appel peut partir.

        Un appel refusé ici n'a pas de ``tool.called`` : il n'a rien fait.

        Args:
            arguments: Arguments de l'appel.
            run: La vue du run.

        Returns:
            Le motif ; ``None`` si l'appel peut partir.
        """
        return None

    @abstractmethod
    def run(
        self, arguments: dict[str, JsonValue], context: ToolContext, run: RunView
    ) -> AsyncGenerator[DelegatedPayload | Consumption | Exchange | ToolOutput | Waiting]:
        """Événements de l'appel, sa consommation, son échange, puis son résultat.

        ``Waiting`` tient lieu de résultat quand le run délégué s'arrête en
        attendant une décision humaine : il n'y en a pas encore.

        Args:
            arguments: Arguments de l'appel.
            context: Contexte de l'appel (run, session, client, annulation).
            run: La vue du run.

        Yields:
            Les événements de modèle (``DelegatedPayload``), la consommation
            d'un enfant, l'échange à garder pour une réparation, puis le
            résultat (``ToolOutput``) ou ``Waiting``, en dernier.
        """
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
        """Nouvelle réponse après un refus (``Retry``), ou ``None`` si l'outil ne se répare pas.

        Args:
            exchange: L'échange qui a produit la sortie refusée.
            feedback: Diagnostic de la politique, donné au modèle.
            policy: Nom de la politique qui a refusé.
            context: Contexte de l'appel.
            run: La vue du run.

        Returns:
            Un générateur comme celui de ``run``, sans ``Waiting`` ; ``None``
            par défaut.
        """
        return None
