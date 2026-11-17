# SPDX-License-Identifier: Apache-2.0
"""Journal d'événements en mémoire : tests et runs sans pause (point 22)."""

from collections.abc import Sequence

from loom_ia.core.events import Event, EventDraft, EventQuery
from loom_ia.core.model import EventId, RunId, SessionId, TenantId
from loom_ia.core.ports import SequenceConflict, SessionRecord, journal_key


class InMemoryEventStore:
    """Journal non durable, limité au process.

    ``append`` ne contient aucun ``await`` : le contrôle de séquence et
    l'écriture sont donc atomiques pour la boucle asyncio.
    """

    def __init__(self) -> None:
        self._journals: dict[tuple[TenantId, SessionId], list[Event]] = {}
        # Rang d'écriture de chaque événement : l'ordre où ``query`` les rend et
        # où ``after`` reprend, quelle que soit la session.
        self._rank: dict[tuple[TenantId, EventId], int] = {}
        self._written = 0

    async def append(self, drafts: Sequence[EventDraft], *, expected_seq: int | None) -> list[Event]:
        """Écrit un lot à la suite du journal d'une session (``EventStore.append``).

        Sans aucun ``await`` : contrôle de séquence et écriture sont atomiques pour
        la boucle asyncio.

        Args:
            drafts: Le lot, dans un seul journal (même client, même session).
            expected_seq: Dernier ``seq`` connu de l'appelant ; ``None`` désactive
                ce contrôle.

        Returns:
            Les événements écrits, numérotés à la suite.

        Raises:
            SequenceConflict: Le journal a avancé depuis ``expected_seq`` ; rien
                n'est écrit.
        """
        if not drafts:
            return []
        key = journal_key(drafts)
        journal = self._journals.setdefault(key, [])
        last = journal[-1].seq if journal else 0
        if expected_seq is not None and expected_seq != last:
            raise SequenceConflict(key[1], expected_seq, last)
        events = [draft.to_event(last + i) for i, draft in enumerate(drafts, start=1)]
        journal.extend(events)
        for event in events:
            self._written += 1
            self._rank[(event.tenant_id, event.event_id)] = self._written
        return events

    async def read(
        self,
        tenant_id: TenantId,
        session_id: SessionId,
        *,
        after_seq: int = 0,
        run_id: RunId | None = None,
    ) -> list[Event]:
        """Lit le journal d'une session, dans l'ordre des ``seq`` (``EventStore.read``).

        Args:
            tenant_id: Le client.
            session_id: La session.
            after_seq: Ne rend que les événements après ce ``seq`` ; 0 : tout.
            run_id: Ne rend que les événements de ce run.
        """
        journal = self._journals.get((tenant_id, session_id), [])
        return [e for e in journal if e.seq > after_seq and (run_id is None or e.run_id == run_id)]

    async def query(self, query: EventQuery) -> list[Event]:
        """Cherche des événements dans les journaux d'un client (``EventStore.query``).

        L'ordre d'écriture est le rang de chaque événement, toutes sessions
        confondues ; ``after`` reprend après l'événement indiqué (un événement
        inconnu retombe sur la comparaison des identifiants).

        Args:
            query: Les critères, l'ordre et la pagination (``EventQuery``).
        """
        tenant = query.tenant_id
        cursor = None if query.after is None else self._rank.get((tenant, query.after))
        found: list[tuple[int, Event]] = []
        for (owner, session_id), journal in self._journals.items():
            if owner != tenant or (query.session_id is not None and session_id != query.session_id):
                continue
            for event in journal:
                if not query.matches(event):
                    continue
                rank = self._rank[(tenant, event.event_id)]
                if query.after is not None and (rank <= cursor if cursor is not None else event.event_id <= query.after):
                    continue
                found.append((rank, event))
        found.sort(key=lambda item: item[0])
        return [event for _, event in found[: query.limit]]

    async def last_seq(self, tenant_id: TenantId, session_id: SessionId) -> int:
        """Dernier ``seq`` du journal d'une session ; 0 s'il est vide ou inconnu."""
        journal = self._journals.get((tenant_id, session_id))
        return journal[-1].seq if journal else 0

    async def sessions(self, tenant_id: TenantId) -> list[SessionRecord]:
        """Sessions d'un client, de la plus récemment écrite à la plus ancienne (fonction F7).

        Args:
            tenant_id: Le client.
        """
        records = [
            SessionRecord(session_id=session_id, last_seq=journal[-1].seq, updated_at=journal[-1].ts)
            for (owner, session_id), journal in self._journals.items()
            if owner == tenant_id and journal
        ]
        records.sort(key=lambda record: record.updated_at, reverse=True)
        return records

    async def delete(self, tenant_id: TenantId, session_id: SessionId) -> int:
        """Supprime le journal d'une session (RGPD) ; rend le nombre d'événements supprimés."""
        gone = self._journals.pop((tenant_id, session_id), [])
        for event in gone:
            self._rank.pop((tenant_id, event.event_id), None)
        return len(gone)

    async def aclose(self) -> None:
        """Rien à fermer."""
        return None
