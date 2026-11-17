# SPDX-License-Identifier: Apache-2.0
"""Corps des requêtes et des réponses de l'API REST (fonction N2)."""

from typing import Annotated, Final, Self

from pydantic import AfterValidator, Field, JsonValue, model_validator

from loom_ia.agents.spec import AgentSpec
from loom_ia.core.model import (
    CallerContext,
    JudgesMode,
    RunId,
    RunStatus,
    SessionId,
    TenantId,
)
from loom_ia.core.model.base import DomainModel

MAX_JSON_DEPTH: Final = 64
"""Profondeur permise à un JSON que l'appelant glisse dans le journal
(``metadata``, ``arguments``). Le journal se relit avec la limite de
récursion du JSON de pydantic, enveloppe de l'événement comprise (200
niveaux en tout) : plus profond, l'événement s'écrit mais ne se relit plus,
et sa session est perdue.
"""


def shallow(value: JsonValue) -> JsonValue:
    """``value`` si ses objets et listes ne s'emboîtent pas sur plus de ``MAX_JSON_DEPTH``.

    Sinon ``ValueError``. Le parcours est itératif : le JSON qu'on vérifie peut être
    plus profond que la pile de l'interpréteur.
    """
    pending: list[tuple[JsonValue, int]] = [(value, 1)]
    while pending:
        item, depth = pending.pop()
        if isinstance(item, dict):
            children = list(item.values())
        elif isinstance(item, list):
            children = item
        else:
            continue
        if depth > MAX_JSON_DEPTH:
            raise ValueError(f"JSON imbriqué sur plus de {MAX_JSON_DEPTH} niveaux")
        pending.extend((child, depth + 1) for child in children)
    return value


Shallow = Annotated[dict[str, JsonValue], AfterValidator(shallow)]
"""Pas de ``type`` ici : pydantic en ferait une définition nommée du document OpenAPI."""


class AgentInfo(DomainModel):
    """Un agent tel que l'API le publie.

    Attributes:
        name: Nom de l'agent.
        description: Sa description.
    """

    name: str
    description: str = ""

    @classmethod
    def of(cls, spec: AgentSpec) -> Self:
        """L'agent tel que l'API le publie : son nom et sa description."""
        return cls(name=spec.name, description=spec.description)


class RunRequest(DomainModel):
    """Demande de lancement d'un run.

    Attributes:
        message: La demande, non vide.
        session_id: Journal auquel rattacher le run ; par défaut, le run est
            son propre journal.
        run_id: Identifiant choisi par l'appelant, pour suivre le run dès son
            départ.
        as_user: Utilisateur déclaré au nom duquel une passerelle agit
            (décision D-5 des utilisateurs et rôles) : réservé à une clé qui a
            ``act_as``, et borné aux rôles qu'elle endosse (``act_as_roles``).
            Sans lui, c'est l'utilisateur de la clé qui agit et qui signe.
        metadata: Métadonnées libres, inscrites au contexte appelant du run ;
            profondeur bornée.
        judges: Juges (point 21) : ``auto`` selon leur ``when`` ; ``force``,
            tous (audit) ; ``skip``, aucun — réservé aux clés de portée
            ``admin``.
        background: Arrière-plan (fonction H5) : la réponse est l'identifiant
            du run, pas son résultat.
    """

    message: str = Field(min_length=1)
    session_id: SessionId | None = None
    run_id: RunId | None = None
    as_user: str | None = None
    metadata: Shallow = Field(default_factory=dict[str, JsonValue])
    judges: JudgesMode = "auto"
    background: bool = False

    def context(self, tenant_id: TenantId) -> CallerContext:
        """Contexte appelant du run ; le client vient de la clé, jamais du corps (point 34).

        Le lanceur non plus : c'est l'utilisateur de la clé, ou celui d'``as_user``,
        que le SDK y inscrit (décision D-5 des utilisateurs et rôles).
        """
        return CallerContext(tenant_id=tenant_id, metadata=dict(self.metadata))


class RunAccepted(DomainModel):
    """Réponse d'un lancement en arrière-plan : le run existe, il n'a pas fini.

    Attributes:
        run_id: Identifiant du run.
        session_id: Sa session.
        status: Son statut au moment de la réponse.
    """

    run_id: RunId
    session_id: SessionId
    status: RunStatus


class Decision(DomainModel):
    """Ce qu'un approbateur dit en tranchant (point 17).

    Attributes:
        call_id: Appel visé ; sans lui, toutes les demandes en attente du run
            sont tranchées.
        as_user: Utilisateur déclaré au nom duquel une passerelle agit
            (décision D-5 des utilisateurs et rôles) : réservé à une clé qui a
            ``act_as``, et borné aux rôles qu'elle endosse (``act_as_roles``).
            Sans lui, c'est l'utilisateur de la clé qui agit et qui signe.
        reason: Motif de la décision ; un refus le renvoie au modèle.
    """

    call_id: str | None = None
    as_user: str | None = None
    reason: str = ""


class Approval(Decision):
    """Un accord, avec les arguments corrigés s'il y en a.

    Attributes:
        arguments: Arguments corrigés de l'appel. Ne vaut que pour un
            ``call_id`` désigné : corriger à l'aveugle les arguments de
            plusieurs appels n'aurait pas de sens.
    """

    arguments: Shallow | None = None

    @model_validator(mode="after")
    def _arguments_need_a_call(self) -> Self:
        if self.arguments is not None and self.call_id is None:
            raise ValueError("arguments corrige un appel désigné : indiquer call_id")
        return self


class Answer(DomainModel):
    """La réponse de l'utilisateur à une question que le run attend (wait_answer).

    Attributes:
        call_id: Question visée ; sans elle, la seule question en attente du
            run.
        answers: Une liste de réponses par question (libellés choisis ou texte
            libre) ; ou bien ``text``.
        text: Un message libre qui répond à l'ensemble ; ou bien ``answers``.
        as_user: Utilisateur déclaré au nom duquel une passerelle agit
            (décision D-5 des utilisateurs et rôles) : réservé à une clé qui a
            ``act_as``, et borné aux rôles qu'elle endosse (``act_as_roles``).
            Sans lui, c'est l'utilisateur de la clé qui agit et qui signe.
    """

    call_id: str | None = None
    answers: list[list[str]] | None = None
    text: str | None = None
    as_user: str | None = None


class Answered(DomainModel):
    """L'appel auquel la réponse a été donnée ; le run reprend en tâche de fond.

    Attributes:
        run_id: Le run qui reprend.
        call_id: La question à laquelle la réponse a été donnée.
    """

    run_id: RunId
    call_id: str


class Decided(DomainModel):
    """Appels tranchés par une décision ; vide si le run n'attendait rien.

    Ce qui reste à trancher se relit sur le run (``pending_approvals``) :
    le dire ici serait une seconde vérité à tenir à jour.

    Attributes:
        run_id: Le run.
        calls: Appels tranchés.
    """

    run_id: RunId
    calls: tuple[str, ...] = ()


class Cancellation(DomainModel):
    """Qui demande l'arrêt d'un run (fonction A5).

    Attributes:
        as_user: Utilisateur déclaré au nom duquel une passerelle agit
            (décision D-5 des utilisateurs et rôles) : réservé à une clé qui a
            ``act_as``, et borné aux rôles qu'elle endosse (``act_as_roles``).
            Sans lui, c'est l'utilisateur de la clé qui agit et qui signe.
    """

    as_user: str | None = None


class Cancelled(DomainModel):
    """Issue d'une demande d'arrêt : faux si le run était déjà fini.

    Attributes:
        run_id: Le run.
        cancelled: Vrai si le run a été arrêté ; faux s'il était déjà fini.
    """

    run_id: RunId
    cancelled: bool
