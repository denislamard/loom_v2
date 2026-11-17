# SPDX-License-Identifier: Apache-2.0
"""Question posée par le modèle à l'utilisateur, et sa réponse (outil ``loom.ask``).

Distincte d'une approbation : c'est le **modèle** qui la pose, en appelant
l'outil fourni ``ask`` ; c'est l'**utilisateur de la session** qui y répond,
et sa réponse est une **information** rendue au modèle comme résultat de
l'appel. Une approbation, elle, vient de la config (``approval``, politiques),
est tranchée par un approbateur (portée ``approve``), et dit oui ou non à un
appel déjà formé.

Le run attend la réponse en ``PAUSED``, comme une approbation : même
mécanique de pause, de concession rendue et de reprise ; attente distincte.
Voir ``docs/wait_answer.md`` et ``docs/multi-questions.md``.

Événements : ``question.asked``, ``question.answered``, ``question.expired``.
On répond par ``loom answer``, ``POST /v1/runs/{id}/answer``, l'API Python, ou
en tapant un message dans la session.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Final

from pydantic import Field

from loom_ia.core.model.base import DomainModel
from loom_ia.core.model.ids import RunId

MAX_QUESTIONS: Final = 4
"""Questions posées au plus par un appel de ``loom.ask``."""
MAX_OPTIONS: Final = 6
"""Choix proposés au plus par question."""
MAX_HEADER: Final = 24
"""Longueur maximale de l'intitulé court d'une question, en caractères."""
MAX_TEXT: Final = 2_000
"""Longueur maximale du texte d'une question, en caractères."""


class QuestionOption(DomainModel):
    """Un choix proposé ; l'utilisateur peut toujours répondre autre chose.

    Attributes:
        label: Libellé du choix, de 1 à 200 caractères ; c'est lui qui revient
            dans la réponse.
        description: Précision montrée avec le choix, 500 caractères au plus.
    """

    label: Annotated[str, Field(min_length=1, max_length=200)]
    description: Annotated[str | None, Field(max_length=500)] = None


class Question(DomainModel):
    """Une question : son texte, un intitulé court, et des choix s'il y en a.

    Attributes:
        question: Texte de la question, de 1 à 2 000 caractères.
        header: Intitulé court, 24 caractères au plus.
        options: Choix proposés, 6 au plus ; aucun : réponse libre.
        multiple: Vrai si plusieurs choix sont permis. Défaut : faux.
    """

    question: Annotated[str, Field(min_length=1, max_length=MAX_TEXT)]
    header: Annotated[str | None, Field(max_length=MAX_HEADER)] = None
    options: Annotated[tuple[QuestionOption, ...], Field(max_length=MAX_OPTIONS)] = ()
    multiple: bool = False


class QuestionReply(DomainModel):
    """La réponse de l'utilisateur.

    ``answers`` : une liste par question, dans leur ordre (les libellés choisis,
    ou un texte libre). ``text`` : un message libre qui répond à l'ensemble —
    ce qu'il a tapé dans la conversation au lieu de remplir les questions.
    L'un des deux, pas les deux.

    Attributes:
        answers: Une liste de réponses par question, dans leur ordre.
        text: Message libre qui répond à l'ensemble.
        by: Identité de qui a répondu.
        via: Passerelle qui a porté la réponse (``as_user``, décision D-5 des
            utilisateurs et rôles).
    """

    answers: tuple[tuple[str, ...], ...] | None = None
    text: str | None = None
    by: str | None = None
    via: str | None = None


class PendingQuestion(DomainModel):
    """Question écrite au journal : elle attend tant qu'elle n'a ni réponse ni expiration.

    C'est ce que montrent l'état d'un run, ``loom sessions show`` et les accès
    à qui doit répondre.

    Attributes:
        call_id: Appel de ``loom.ask`` qui pose la question.
        questions: Les questions.
        run_id: Run qui l'a posée : le run lui-même, ou le sous-run d'un
            sous-agent.
        agent: Agent de ce run : c'est son ``access`` qui dit qui lui répond
            (``docs/loom_role_user.md`` §5.2).
        expire_at: Échéance ; ``None`` : la question attend indéfiniment.
        reply: La réponse, une fois donnée.
        expired: Vrai si la question a expiré sans réponse.
    """

    call_id: str
    questions: tuple[Question, ...]
    run_id: RunId | None = None
    agent: str | None = None
    expire_at: datetime | None = None
    reply: QuestionReply | None = None
    expired: bool = False

    @property
    def open(self) -> bool:
        """Vrai tant que la question n'a ni réponse ni expiration."""
        return self.reply is None and not self.expired

    def stale(self, now: datetime) -> bool:
        """Vrai si l'échéance est passée à cet instant (la question n'est pas encore marquée expirée)."""
        return self.expire_at is not None and now >= self.expire_at


@dataclass(frozen=True, slots=True)
class NoAnswer:
    """Le répondeur en ligne n'a pas de réponse (formulaire refusé ou fermé).

    ``expired`` : la question est close sans réponse (rejeu d'une question
    expirée au journal, évaluation sans humain) ; le modèle l'apprend.
    Sinon, elle reste posée et le run attend.

    Attributes:
        reason: Pourquoi il n'y a pas de réponse.
        expired: Vrai pour clore la question sans réponse.
    """

    reason: str = ""
    expired: bool = False


type Answerer = Callable[[PendingQuestion], Awaitable[QuestionReply | NoAnswer]]
"""Répondeur en ligne (elicitation MCP, script) : il répond dans la boucle, sans que le run passe par ``PAUSED``.

Ce qu'il rend est écrit au journal comme une réponse ordinaire ; ``NoAnswer``
laisse le run en pause, ou clôt la question si ``expired`` est vrai.
"""
