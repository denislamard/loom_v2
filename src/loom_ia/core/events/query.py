# SPDX-License-Identifier: Apache-2.0
"""Recherche d'événements par enveloppe et facettes (#22)."""

from typing import Final

from pydantic import AwareDatetime, Field

from loom_ia.core.events.envelope import Event
from loom_ia.core.events.payloads import EventCategory, EventStatus, FacetValue
from loom_ia.core.model.base import DomainModel
from loom_ia.core.model.ids import EventId, RunId, SessionId, TenantId

# Bornes d'une recherche : ce qu'une page rend faute de le dire, et ce qu'elle
# peut rendre au plus. Nommées pour que les accès les annoncent sans les redire.
EVENTS_LIMIT: Final = 100
EVENTS_MAX: Final = 10_000


class EventQuery(DomainModel):
    """Critères de recherche. Le client est toujours obligatoire (#34).

    Les résultats sont rendus **dans l'ordre où le journal les a reçus** (mémoire,
    SQLite, Postgres) et ``after`` reprend la pagination après l'événement
    indiqué *dans cet ordre*. L'``event_id`` (UUIDv7) est posé à la création du
    brouillon, avant l'écriture : un écrivain lent peut donc écrire après un
    autre un événement d'identifiant plus petit. Trier ou borner sur l'id le
    perdrait pour qui suit le journal ; l'ordre d'écriture, non. Un ``after``
    inconnu du journal (session effacée, id inventé) retombe sur la comparaison
    des identifiants. Le journal JSONL, qui a un fichier par session et aucun
    ordre commun, reste trié par ``event_id`` (``select``).
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
    # Nom du modèle chez le fournisseur (``claude-haiku-5-5``).
    model_id: str | None = None
    # Identifiant du modèle dans la config (``HAIKU``) : absent des journaux antérieurs.
    model: str | None = None
    # Égalité sur n'importe quelle facette.
    facets: dict[str, FacetValue] = Field(default_factory=dict)
    since: AwareDatetime | None = None
    until: AwareDatetime | None = None
    after: EventId | None = None
    limit: int = Field(default=EVENTS_LIMIT, ge=1, le=EVENTS_MAX)

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
        wanted = self.wanted_facets()
        return all(
            name in event.facets and event.facets[name] == value for name, value in wanted.items()
        )

    def wanted_facets(self) -> dict[str, FacetValue]:
        """Facettes exigées : ``facets``, plus celles que nomment ``tool_name``, ``model_id``
        et ``model``. Les magasins SQL les traduisent dans leur requête."""
        wanted = dict(self.facets)
        for name in ("tool_name", "model_id", "model"):
            value: str | None = getattr(self, name)
            if value is not None:
                wanted[name] = value
        return wanted

    def refine(self, events: list[Event]) -> list[Event]:
        """Rejoue les critères sur des événements qu'un magasin a déjà ordonnés.

        L'ordre est celui de l'écriture, fixé par le magasin avec le curseur
        ``after`` : ni tri ni curseur ici, seulement les critères et la borne.
        """
        return [e for e in events if self.matches(e)][: self.limit]

    def select(self, events: list[Event]) -> list[Event]:
        """Filtre, trie par ``event_id`` et pagine une liste d'événements."""
        found = sorted((e for e in events if self.matches(e)), key=lambda e: e.event_id)
        if self.after is not None:
            found = [e for e in found if e.event_id > self.after]
        return found[: self.limit]
