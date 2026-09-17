# SPDX-License-Identifier: Apache-2.0
"""Journal d'événements en mémoire : tests et runs sans pause (#22)."""

from collections.abc import Sequence

from loom_ia.core.events import Event, EventDraft, EventQuery
from loom_ia.core.model import RunId, SessionId, TenantId
from loom_ia.core.ports import SequenceConflict, journal_key


class InMemoryEventStore:
    """Journal non durable, limité au process.

    ``append`` ne contient aucun ``await`` : le contrôle de séquence et
    l'écriture sont donc atomiques pour la boucle asyncio.
    """

    def __init__(self) -> None:
        self._journals: dict[tuple[TenantId, SessionId], list[Event]] = {}

    async def append(
        self, drafts: Sequence[EventDraft], *, expected_seq: int | None
    ) -> list[Event]:
        if not drafts:
            return []
        key = journal_key(drafts)
        journal = self._journals.setdefault(key, [])
        last = journal[-1].seq if journal else 0
        if expected_seq is not None and expected_seq != last:
            raise SequenceConflict(key[1], expected_seq, last)
        events = [draft.to_event(last + i) for i, draft in enumerate(drafts, start=1)]
        journal.extend(events)
        return events

    async def read(
        self,
        tenant_id: TenantId,
        session_id: SessionId,
        *,
        after_seq: int = 0,
        run_id: RunId | None = None,
    ) -> list[Event]:
        journal = self._journals.get((tenant_id, session_id), [])
        return [e for e in journal if e.seq > after_seq and (run_id is None or e.run_id == run_id)]

    async def query(self, query: EventQuery) -> list[Event]:
        candidates = [
            event
            for (tenant_id, session_id), journal in self._journals.items()
            if tenant_id == query.tenant_id
            and (query.session_id is None or session_id == query.session_id)
            for event in journal
        ]
        return query.select(candidates)

    async def last_seq(self, tenant_id: TenantId, session_id: SessionId) -> int:
        journal = self._journals.get((tenant_id, session_id))
        return journal[-1].seq if journal else 0

    async def aclose(self) -> None:
        return None
