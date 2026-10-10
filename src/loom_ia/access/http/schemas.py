# SPDX-License-Identifier: Apache-2.0
"""Corps des requêtes et des réponses de l'API REST (N2)."""

from typing import Annotated, Final, Self

from pydantic import AfterValidator, Field, JsonValue

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

# Profondeur permise à un JSON que l'appelant glisse dans le journal (``metadata``,
# ``arguments``). Le journal se relit avec la limite de récursion du JSON de
# pydantic, enveloppe de l'événement comprise (200 niveaux en tout) : plus profond,
# l'événement s'écrit mais ne se relit plus, et sa session est perdue.
MAX_JSON_DEPTH: Final = 64


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


# Pas de ``type`` ici : pydantic en ferait une définition nommée du document OpenAPI.
Shallow = Annotated[dict[str, JsonValue], AfterValidator(shallow)]


class AgentInfo(DomainModel):
    """Un agent tel que l'API le publie."""

    name: str
    description: str = ""

    @classmethod
    def of(cls, spec: AgentSpec) -> Self:
        return cls(name=spec.name, description=spec.description)


class RunRequest(DomainModel):
    """Demande de lancement d'un run."""

    message: str = Field(min_length=1)
    # Journal auquel rattacher le run ; par défaut le run est son propre journal.
    session_id: SessionId | None = None
    # Identifiant choisi par l'appelant, pour suivre le run dès son départ.
    run_id: RunId | None = None
    user_id: str | None = None
    metadata: Shallow = Field(default_factory=dict[str, JsonValue])
    # Juges (#21) : ``auto`` selon leur ``when`` ; ``force``, tous (audit) ;
    # ``skip``, aucun — réservé aux clés de portée ``admin``.
    judges: JudgesMode = "auto"
    # Arrière-plan (H5) : la réponse est l'identifiant du run, pas son résultat.
    background: bool = False

    def context(self, tenant_id: TenantId) -> CallerContext:
        """Contexte appelant du run ; le client vient de la clé, jamais du corps (#34)."""
        return CallerContext(
            tenant_id=tenant_id, user_id=self.user_id, metadata=dict(self.metadata)
        )


class RunAccepted(DomainModel):
    """Réponse d'un lancement en arrière-plan : le run existe, il n'a pas fini."""

    run_id: RunId
    session_id: SessionId
    status: RunStatus


class Decision(DomainModel):
    """Ce qu'un approbateur dit en tranchant (#17)."""

    # Appel visé ; sans lui, toutes les demandes en attente du run sont tranchées.
    call_id: str | None = None
    # Identité de l'approbateur. Sans elle, c'est la clé d'API qui signe ;
    # une passerelle la renseigne pour nommer l'humain qui a tranché.
    by: str | None = None
    reason: str = ""


class Approval(Decision):
    """Un accord, avec les arguments corrigés s'il y en a."""

    # Ne vaut que pour un ``call_id`` désigné : corriger à l'aveugle les
    # arguments de plusieurs appels n'aurait pas de sens.
    arguments: Shallow | None = None


class Decided(DomainModel):
    """Appels tranchés par une décision ; vide si le run n'attendait rien.

    Ce qui reste à trancher se relit sur le run (``pending_approvals``) :
    le dire ici serait une seconde vérité à tenir à jour.
    """

    run_id: RunId
    calls: tuple[str, ...] = ()


class Cancellation(DomainModel):
    """Qui demande l'arrêt d'un run (A5)."""

    by: str | None = None


class Cancelled(DomainModel):
    """Issue d'une demande d'arrêt : faux si le run était déjà fini."""

    run_id: RunId
    cancelled: bool
