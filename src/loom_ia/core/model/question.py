# SPDX-License-Identifier: Apache-2.0
"""Question posée par le modèle à l'utilisateur, et sa réponse.

Distincte d'une approbation : c'est le **modèle** qui la pose, en appelant
l'outil fourni ``ask`` ; c'est l'**utilisateur de la session** qui y répond,
et sa réponse est une **information** rendue au modèle comme résultat de
l'appel. Une approbation, elle, vient de la config (``approval``, politiques),
est tranchée par un approbateur (portée ``approve``), et dit oui ou non à un
appel déjà formé.

Le run attend la réponse en ``PAUSED``, comme une approbation : même
mécanique de pause, de concession rendue et de reprise ; attente distincte.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Final

from pydantic import Field

from loom_ia.core.model.base import DomainModel
from loom_ia.core.model.ids import RunId

MAX_QUESTIONS: Final = 4
MAX_OPTIONS: Final = 6
MAX_HEADER: Final = 24
MAX_TEXT: Final = 2_000


class QuestionOption(DomainModel):
    """Un choix proposé ; l'utilisateur peut toujours répondre autre chose."""

    label: Annotated[str, Field(min_length=1, max_length=200)]
    description: Annotated[str | None, Field(max_length=500)] = None


class Question(DomainModel):
    """Une question : son texte, un intitulé court, et des choix s'il y en a."""

    question: Annotated[str, Field(min_length=1, max_length=MAX_TEXT)]
    header: Annotated[str | None, Field(max_length=MAX_HEADER)] = None
    options: Annotated[tuple[QuestionOption, ...], Field(max_length=MAX_OPTIONS)] = ()
    # Plusieurs choix permis.
    multiple: bool = False


class QuestionReply(DomainModel):
    """La réponse de l'utilisateur.

    ``answers`` : une liste par question, dans leur ordre (les libellés choisis,
    ou un texte libre). ``text`` : un message libre qui répond à l'ensemble —
    ce qu'il a tapé dans la conversation au lieu de remplir les questions.
    L'un des deux, pas les deux.
    """

    answers: tuple[tuple[str, ...], ...] | None = None
    text: str | None = None
    by: str | None = None
    # Passerelle qui a porté la réponse (``as_user``, D-5).
    via: str | None = None


class PendingQuestion(DomainModel):
    """Question écrite au journal : elle attend tant qu'elle n'a ni réponse ni expiration."""

    call_id: str
    questions: tuple[Question, ...]
    # Run qui l'a posée : le run lui-même, ou le sous-run d'un sous-agent.
    run_id: RunId | None = None
    expire_at: datetime | None = None
    reply: QuestionReply | None = None
    expired: bool = False

    @property
    def open(self) -> bool:
        return self.reply is None and not self.expired

    def stale(self, now: datetime) -> bool:
        return self.expire_at is not None and now >= self.expire_at


@dataclass(frozen=True, slots=True)
class NoAnswer:
    """Le répondeur en ligne n'a pas de réponse (formulaire refusé ou fermé).

    ``expired`` : la question est close sans réponse (rejeu d'une question
    expirée au journal, évaluation sans humain) ; le modèle l'apprend.
    Sinon, elle reste posée et le run attend.
    """

    reason: str = ""
    expired: bool = False


# Répondeur en ligne (elicitation MCP, script) : il répond dans la boucle, sans
# que le run passe par PAUSED. Ce qu'il rend est écrit au journal comme une
# réponse ordinaire. ``NoAnswer`` laisse le run en pause.
type Answerer = Callable[[PendingQuestion], Awaitable[QuestionReply | NoAnswer]]
