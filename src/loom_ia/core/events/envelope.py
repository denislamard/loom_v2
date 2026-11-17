# SPDX-License-Identifier: Apache-2.0
"""Enveloppe commune des événements du journal (point 22).

Un événement, c'est une enveloppe (identifiants, horodatage, coordonnées dans
l'arbre des runs et des spans, champs de recherche) et une charge typée
(``core/events/payloads.py``).

Un ``EventDraft`` est un événement pas encore écrit : c'est le store qui lui
attribue son numéro d'ordre ``seq`` dans le journal de la session et le
transforme en ``Event``. ``type``, ``category``, ``status`` et ``facets`` sont
recopiés de la charge à la construction, puis vérifiés à chaque relecture.
``RunScope`` porte les coordonnées d'un run et fabrique ses brouillons.
"""

from datetime import UTC, datetime
from typing import Final, Literal, Self, cast

from pydantic import AwareDatetime, Field, PositiveInt, model_validator

from loom_ia.core.events.payloads import (
    DurablePayload,
    EventCategory,
    EventStatus,
    FacetValue,
)
from loom_ia.core.model.base import DomainModel
from loom_ia.core.model.ids import (
    EventId,
    RunId,
    SessionId,
    SpanId,
    TenantId,
    new_event_id,
    new_span_id,
)

SCHEMA_VERSION: Final = 1
"""Version du schéma des événements écrits ; une autre version est refusée à la lecture.

Ajouter un champ facultatif ne la change pas : un lecteur plus ancien ignore
ce qu'il ne connaît pas.
"""


def _now() -> datetime:
    """Instant présent, en UTC : horodatage par défaut d'un événement."""
    return datetime.now(UTC)


class _EventFields(DomainModel):
    """Champs communs à un brouillon et à un événement écrit ; voir ``Event``."""

    event_id: EventId = Field(default_factory=new_event_id)
    ts: AwareDatetime = Field(default_factory=_now)
    schema_version: Literal[1] = SCHEMA_VERSION

    tenant_id: TenantId
    session_id: SessionId
    run_id: RunId
    root_run_id: RunId
    span_id: SpanId
    parent_span_id: SpanId | None = None

    type: str
    category: EventCategory
    status: EventStatus
    agent: str | None = None
    role: str | None = None
    facets: dict[str, FacetValue] = Field(default_factory=dict)

    payload: DurablePayload

    @model_validator(mode="before")
    @classmethod
    def _reject_unknown_version(cls, data: object) -> object:
        """Refuse un événement d'une autre version de schéma, avec un message explicite (point 22)."""
        if not isinstance(data, dict):
            return data
        fields = cast(dict[str, object], data)
        version = fields.get("schema_version", SCHEMA_VERSION)
        if version != SCHEMA_VERSION:
            raise ValueError(f"Version de schéma d'événement non prise en charge : {version!r} (attendue : {SCHEMA_VERSION})")
        return fields

    @model_validator(mode="after")
    def _check_derived_fields(self) -> Self:
        """Vérifie que ``type``, ``category``, ``status`` et ``facets`` sont ceux de la charge."""
        expected = {
            "type": self.payload.type,
            "category": self.payload.category,
            "status": self.payload.event_status,
            "facets": self.payload.facets(),
        }
        actual = {
            "type": self.type,
            "category": self.category,
            "status": self.status,
            "facets": self.facets,
        }
        for name, value in expected.items():
            if actual[name] != value:
                raise ValueError(f"Champ {name!r} incohérent avec le payload : {actual[name]!r} ≠ {value!r}")
        return self


class EventDraft(_EventFields):
    """Événement construit par le moteur, avant écriture : il n'a pas encore de ``seq``.

    Mêmes champs qu'``Event``, sauf ``seq``. Le moteur l'obtient par
    ``RunScope.draft`` ; le store le change en ``Event`` en l'écrivant.
    """

    def to_event(self, seq: int) -> Event:
        """L'événement écrit, à ce rang du journal de la session.

        Args:
            seq: Numéro d'ordre attribué par le store, à partir de 1.

        Returns:
            L'événement, revalidé.
        """
        return Event.model_validate({**dict(self), "seq": seq})


class Event(_EventFields):
    """Événement écrit dans le journal d'une session : une enveloppe et sa charge.

    Le journal fait foi : l'historique, l'état d'un run, les traces, la
    consommation et le rejeu en sont déduits (point 22).

    Attributes:
        event_id: Identifiant unique de l'événement.
        ts: Horodatage de création, avec fuseau (UTC à la création).
        schema_version: Version du schéma ; seule ``1`` est acceptée.
        tenant_id: Client (espace isolé, fonction L1).
        session_id: Session dont le journal porte l'événement.
        run_id: Run qui l'a écrit.
        root_run_id: Run racine de l'arbre : lui-même pour un run racine,
            l'ancêtre commun pour un sous-run.
        span_id: Span de l'événement : celui du run, d'une étape ou d'un
            appel.
        parent_span_id: Span parent ; ``None`` pour le span d'un run racine.
        type: Type de la charge (``model.responded``…), recopié pour la
            recherche.
        category: Catégorie de la charge, recopiée.
        status: Statut de la charge (``ok``, ``warning``, ``error``),
            recopié.
        agent: Agent du run.
        role: Rôle LLM délégué qui a fait l'appel de modèle, ou
            ``judge:<nom>`` pour l'appel d'un juge ; ``None`` pour
            l'orchestrateur.
        facets: Champs de recherche tirés de la charge (``facets()``).
        payload: La charge, discriminée par ``type``.
        seq: Numéro d'ordre dans le journal de la session, à partir de 1 ;
            attribué par le store.
    """

    seq: PositiveInt


class RunScope(DomainModel):
    """Coordonnées d'un run dans le journal : fabrique ses ``EventDraft``.

    Attributes:
        tenant_id: Client du run.
        session_id: Session du run.
        run_id: Identifiant du run.
        root_run_id: Run racine de son arbre.
        agent: Agent du run, recopié dans chaque événement.
        span_id: Span racine du run : parent par défaut des spans de ses
            étapes. Engendré par défaut.
        parent_span_id: Span parent du run : celui de l'appel qui l'a lancé,
            pour un sous-run ; ``None`` pour un run racine.
    """

    tenant_id: TenantId
    session_id: SessionId
    run_id: RunId
    root_run_id: RunId
    agent: str
    span_id: SpanId = Field(default_factory=new_span_id)
    parent_span_id: SpanId | None = None

    def draft(
        self,
        payload: DurablePayload,
        *,
        span_id: SpanId | None = None,
        parent_span_id: SpanId | None = None,
        role: str | None = None,
    ) -> EventDraft:
        """Brouillon d'événement pour ce run, avec ses champs de recherche tirés de la charge.

        Args:
            payload: La charge de l'événement.
            span_id: Span de l'événement ; sans lui, l'événement appartient au
                span racine du run.
            parent_span_id: Parent de ``span_id`` ; par défaut, le span racine
                du run.
            role: Rôle LLM délégué qui a fait l'appel, ou ``judge:<nom>``.

        Returns:
            Le brouillon, prêt à écrire.
        """
        if span_id is None:
            span_id, parent_span_id = self.span_id, self.parent_span_id
        elif parent_span_id is None:
            parent_span_id = self.span_id
        return EventDraft(
            tenant_id=self.tenant_id,
            session_id=self.session_id,
            run_id=self.run_id,
            root_run_id=self.root_run_id,
            span_id=span_id,
            parent_span_id=parent_span_id,
            type=payload.type,
            category=payload.category,
            status=payload.event_status,
            agent=self.agent,
            role=role,
            facets=payload.facets(),
            payload=payload,
        )
