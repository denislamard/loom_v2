# SPDX-License-Identifier: Apache-2.0
"""Recherche d'événements par enveloppe et facettes (point 22).

``EventQuery`` porte les critères ; chaque store (mémoire, JSONL, SQLite,
Postgres) les applique. C'est ce que servent ``GET /v1/events`` et la
lecture du journal par l'API Python.
"""

import re
from typing import Final

from pydantic import AwareDatetime, Field, field_validator

from loom_ia.core.events.envelope import Event
from loom_ia.core.events.payloads import EventCategory, EventStatus, FacetValue
from loom_ia.core.model.base import DomainModel
from loom_ia.core.model.ids import EventId, RunId, SessionId, TenantId

# Nommées pour que les accès les annoncent sans les redire.
EVENTS_LIMIT: Final = 100
"""Événements rendus par page quand la recherche ne dit pas ``limit``."""

EVENTS_MAX: Final = 10_000
"""Événements qu'une page peut rendre au plus (``limit`` borné à cette valeur)."""

EVENT_ID_PATTERN: Final = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
"""Forme d'un identifiant d'événement : un UUID écrit comme loom les tire, en minuscules et avec ses tirets.

Le curseur ``after`` se compare en chaîne : une autre écriture (majuscules,
sans tirets, ou un texte quelconque) se classerait ailleurs et rendrait, sans
erreur, une page vide ou fausse.
"""
_EVENT_ID: Final = re.compile(EVENT_ID_PATTERN)


class EventQuery(DomainModel):
    """Critères d'une recherche d'événements dans le journal ; le client est toujours obligatoire (point 34).

    Les résultats sont rendus **dans l'ordre où le journal les a reçus** (mémoire,
    SQLite, Postgres) et ``after`` reprend la pagination après l'événement
    indiqué *dans cet ordre*. L'``event_id`` (UUIDv7) est posé à la création du
    brouillon, avant l'écriture : un écrivain lent peut donc écrire après un
    autre un événement d'identifiant plus petit. Trier ou borner sur l'id le
    perdrait pour qui suit le journal ; l'ordre d'écriture, non. Un ``after``
    inconnu du journal (session effacée, id inventé) retombe sur la comparaison
    des identifiants. Le journal JSONL, qui a un fichier par session et aucun
    ordre commun, reste trié par ``event_id`` (``select``).

    Tous les critères donnés doivent être vrais ; un critère vide ou ``None``
    ne filtre rien.

    Attributes:
        tenant_id: Client dont lire le journal ; obligatoire, pour qu'aucune
            recherche ne traverse les clients.
        session_id: Session, ou toutes celles du client.
        run_id: Run, ou tous.
        types: Types d'événements acceptés (``tool.completed``…) ; vide :
            tous.
        categories: Catégories acceptées ; vide : toutes.
        status: Statuts acceptés (``ok``, ``warning``, ``error``) ; vide :
            tous.
        agent: Agent du run.
        role: Rôle LLM délégué, ou ``judge:<nom>``.
        tool_name: Nom d'outil, cherché dans les facettes.
        model_id: Nom du modèle chez le fournisseur
            (``claude-haiku-5-5``), cherché dans les facettes.
        model: Identifiant du modèle dans la config (``HAIKU``), cherché dans
            les facettes ; absent des journaux antérieurs.
        facets: Égalité sur n'importe quelle facette, ``{nom: valeur}``.
        since: Événements écrits à partir de cet instant (inclus).
        until: Événements écrits avant cet instant (exclu).
        after: Curseur de pagination : l'``event_id`` du dernier événement de
            la page précédente.
        limit: Événements rendus au plus. De 1 à 10 000 ; 100 par défaut.

    Raises:
        ValueError: ``after`` n'a pas la forme d'un identifiant d'événement.
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
    model: str | None = None
    facets: dict[str, FacetValue] = Field(default_factory=dict)
    since: AwareDatetime | None = None
    until: AwareDatetime | None = None
    after: EventId | None = None
    limit: int = Field(default=EVENTS_LIMIT, ge=1, le=EVENTS_MAX)

    @field_validator("after")
    @classmethod
    def _check_after(cls, value: EventId | None) -> EventId | None:
        """Refuse un curseur qui n'a pas la forme d'un identifiant d'événement."""
        # Un identifiant bien formé mais absent du journal reste permis : il
        # retombe sur la comparaison des identifiants (voir plus haut).
        if value is not None and not _EVENT_ID.fullmatch(value):
            raise ValueError(f"after : identifiant d'événement attendu (UUID en minuscules, avec ses tirets, tel que le journal l'écrit), reçu {value!r}")
        return value

    def matches(self, event: Event) -> bool:
        """Vrai si l'événement satisfait tous les critères, hors pagination (``after``, ``limit``).

        Args:
            event: Un événement écrit.

        Returns:
            Vrai si chaque critère donné est tenu, facettes comprises.
        """
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
        return all(name in event.facets and event.facets[name] == value for name, value in wanted.items())

    def wanted_facets(self) -> dict[str, FacetValue]:
        """Facettes exigées : ``facets``, plus celles que nomment ``tool_name``, ``model_id`` et ``model``.

        Les magasins SQL les traduisent dans leur requête.

        Returns:
            Nom de facette → valeur attendue.
        """
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

        Args:
            events: Événements dans l'ordre d'écriture, après le curseur.

        Returns:
            Ceux qui satisfont les critères, ``limit`` au plus.
        """
        return [e for e in events if self.matches(e)][: self.limit]

    def select(self, events: list[Event]) -> list[Event]:
        """Filtre, trie par ``event_id`` et pagine une liste d'événements.

        Sert au journal JSONL, qui n'a pas d'ordre d'écriture commun à ses
        sessions.

        Args:
            events: Événements à filtrer, dans n'importe quel ordre.

        Returns:
            Ceux qui satisfont les critères, triés par ``event_id``, après
            ``after``, ``limit`` au plus.
        """
        found = sorted((e for e in events if self.matches(e)), key=lambda e: e.event_id)
        if self.after is not None:
            found = [e for e in found if e.event_id > self.after]
        return found[: self.limit]
