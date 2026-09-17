# SPDX-License-Identifier: Apache-2.0
"""Enveloppe commune des événements (#22).

Un ``EventDraft`` est un événement pas encore écrit : c'est le store qui lui
attribue son numéro d'ordre ``seq`` dans le journal de la session et le
transforme en ``Event``. ``type``, ``category``, ``status`` et ``facets`` sont
recopiés du payload à la construction, puis vérifiés à chaque relecture.
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


def _now() -> datetime:
    return datetime.now(UTC)


class _EventFields(DomainModel):
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
        # Message explicite plutôt qu'une erreur de littéral (#22).
        if not isinstance(data, dict):
            return data
        fields = cast(dict[str, object], data)
        version = fields.get("schema_version", SCHEMA_VERSION)
        if version != SCHEMA_VERSION:
            raise ValueError(
                f"Version de schéma d'événement non prise en charge : {version!r} "
                f"(attendue : {SCHEMA_VERSION})"
            )
        return fields

    @model_validator(mode="after")
    def _check_derived_fields(self) -> Self:
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
                raise ValueError(
                    f"Champ {name!r} incohérent avec le payload : {actual[name]!r} ≠ {value!r}"
                )
        return self


class EventDraft(_EventFields):
    """Événement construit par le moteur, avant écriture."""

    def to_event(self, seq: int) -> Event:
        return Event.model_validate({**dict(self), "seq": seq})


class Event(_EventFields):
    """Événement écrit dans le journal."""

    seq: PositiveInt


class RunScope(DomainModel):
    """Coordonnées d'un run dans le journal : fabrique ses ``EventDraft``."""

    tenant_id: TenantId
    session_id: SessionId
    run_id: RunId
    root_run_id: RunId
    agent: str
    # Span racine du run : parent par défaut des spans de ses étapes.
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
        """Brouillon d'événement pour ce run.

        Sans ``span_id``, l'événement appartient au span racine du run.
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
