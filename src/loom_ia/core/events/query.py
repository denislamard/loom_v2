# SPDX-License-Identifier: Apache-2.0
"""Recherche d'événements par enveloppe et facettes (#22)."""

from pydantic import AwareDatetime, Field

from loom_ia.core.events.envelope import Event
from loom_ia.core.events.payloads import EventCategory, EventStatus, FacetValue
from loom_ia.core.model.base import DomainModel
from loom_ia.core.model.ids import EventId, RunId, SessionId, TenantId


class EventQuery(DomainModel):
    """Critères de recherche. Le client est toujours obligatoire (#34).

    Les résultats sont triés par ``event_id`` (UUIDv7, donc par date) ;
    ``after`` reprend la pagination après l'événement indiqué.
    """

    tenant_id: TenantId
    session_id: SessionId | None = None
    run_id: RunId | None = None
    types: tuple[str, ...] = ()
    categories: tuple[EventCategory, ...] = ()
    status: tuple[EventStatus, ...] = ()
    agent: str | None = None
    role: str | None = None
    tool_name: str | None = None
    model_id: str | None = None
    # Égalité sur n'importe quelle facette.
    facets: dict[str, FacetValue] = Field(default_factory=dict)
    since: AwareDatetime | None = None
    until: AwareDatetime | None = None
    after: EventId | None = None
    limit: int = Field(default=100, ge=1, le=10_000)

    def matches(self, event: Event) -> bool:
        """Vrai si l'événement satisfait tous les critères (hors pagination)."""
        checks = (
            event.tenant_id == self.tenant_id,
            self.session_id is None or event.session_id == self.session_id,
            self.run_id is None or event.run_id == self.run_id,
            not self.types or event.type in self.types,
            not self.categories or event.category in self.categories,
            not self.status or event.status in self.status,
            self.agent is None or event.agent == self.agent,
            self.role is None or event.role == self.role,
            self.since is None or event.ts >= self.since,
            self.until is None or event.ts < self.until,
        )
        if not all(checks):
            return False
        wanted = dict(self.facets)
        if self.tool_name is not None:
            wanted["tool_name"] = self.tool_name
        if self.model_id is not None:
            wanted["model_id"] = self.model_id
        return all(
            name in event.facets and event.facets[name] == value for name, value in wanted.items()
        )

    def select(self, events: list[Event]) -> list[Event]:
        """Filtre, trie et pagine une liste d'événements."""
        found = sorted((e for e in events if self.matches(e)), key=lambda e: e.event_id)
        if self.after is not None:
            found = [e for e in found if e.event_id > self.after]
        return found[: self.limit]
