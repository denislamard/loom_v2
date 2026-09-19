# SPDX-License-Identifier: Apache-2.0
"""Écrivain d'une session : un run et ses sous-runs écrivent dans le même journal (#4, #22).

Chaque écriture annonce le dernier ``seq`` qu'elle connaît (écriture
optimiste) : deux écrivains indépendants sur la même session se
contrediraient. Or un sous-agent écrit son run dans le journal de son parent,
pendant que le parent écrit le sien, et plusieurs sous-agents peuvent tourner
en parallèle. Ils partagent donc un seul écrivain : un verrou ordonne les
écritures, et le dernier ``seq`` est tenu à jour pour tous.
"""

import asyncio
from collections.abc import Sequence
from typing import Self

from loom_ia.core.events import Event, EventDraft
from loom_ia.core.model import SessionId, TenantId
from loom_ia.core.ports import EventStore


class SessionWriter:
    """Écritures ordonnées dans le journal d'une session."""

    def __init__(
        self, store: EventStore, tenant_id: TenantId, session_id: SessionId, last_seq: int
    ) -> None:
        self.store = store
        self.tenant_id = tenant_id
        self.session_id = session_id
        self.last_seq = last_seq
        self._lock = asyncio.Lock()

    @classmethod
    async def open(cls, store: EventStore, tenant_id: TenantId, session_id: SessionId) -> Self:
        """Écrivain qui part du dernier événement de la session."""
        return cls(store, tenant_id, session_id, await store.last_seq(tenant_id, session_id))

    async def append(self, drafts: Sequence[EventDraft]) -> list[Event]:
        """Écrit les brouillons à la suite, dans l'ordre, et les renvoie numérotés."""
        async with self._lock:
            events = await self.store.append(list(drafts), expected_seq=self.last_seq)
            if events:
                self.last_seq = events[-1].seq
            return events

    def __repr__(self) -> str:
        return f"SessionWriter({self.tenant_id}/{self.session_id}, seq {self.last_seq})"
