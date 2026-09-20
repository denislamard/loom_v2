# SPDX-License-Identifier: Apache-2.0
"""Écrivain d'une session : tous ses runs écrivent par lui (#4, #22).

Chaque écriture annonce le dernier ``seq`` qu'elle connaît (écriture
optimiste) : deux écrivains indépendants sur la même session se
contrediraient. Or un sous-agent écrit son run dans le journal de son parent,
pendant que le parent écrit le sien, et plusieurs sous-agents peuvent tourner
en parallèle. Ils partagent donc un seul écrivain : un verrou ordonne les
écritures, et le dernier ``seq`` est tenu à jour pour tous.

Deux runs d'une même session lancés en parallèle le partagent aussi, par le
registre ``SessionWriters`` de l'instance. Un autre process — un worker, un
second serveur — reste hors de portée du registre : son écriture fait avancer
le journal et la nôtre est refusée. Le conflit n'invalide rien, car nos
brouillons parlent de notre run et pas du sien : l'écrivain relit la position
et réécrit, un nombre borné de fois.

La compaction écrit sans ce contrôle (``checked=False``, #23) : son événement
ne couvre que des événements anciens, et son worker ne partage aucun écrivain
avec les runs en cours.
"""

import asyncio
import logging
from collections.abc import Sequence
from typing import Final, Self

from loom_ia.core.events import Event, EventDraft
from loom_ia.core.model import SessionId, TenantId
from loom_ia.core.ports import EventStore, SequenceConflict

logger = logging.getLogger(__name__)

# Écritures refusées d'affilée avant d'abandonner : au-delà, ce n'est plus un
# croisement mais un journal écrit sans répit par ailleurs.
MAX_ATTEMPTS: Final = 5
# Écrivains gardés par le registre ; au-delà, le moins récemment utilisé est
# oublié (un run qui le tient encore continue de s'en servir).
MAX_WRITERS: Final = 256


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

    async def append(self, drafts: Sequence[EventDraft], *, checked: bool = True) -> list[Event]:
        """Écrit les brouillons à la suite, dans l'ordre, et les renvoie numérotés.

        Un conflit de séquence est repris : la position est relue chez le
        store, puis l'écriture rejouée telle quelle.
        """
        batch = list(drafts)
        async with self._lock:
            attempt = 0
            while True:
                attempt += 1
                try:
                    events = await self.store.append(
                        batch, expected_seq=self.last_seq if checked else None
                    )
                except SequenceConflict as conflict:
                    if attempt >= MAX_ATTEMPTS:
                        raise
                    logger.debug(
                        "Journal %s : écriture reprise (seq %d → %d, tentative %d)",
                        self.session_id,
                        self.last_seq,
                        conflict.actual,
                        attempt,
                    )
                    self.last_seq = conflict.actual
                else:
                    if events:
                        self.last_seq = events[-1].seq
                    return events

    def __repr__(self) -> str:
        return f"SessionWriter({self.tenant_id}/{self.session_id}, seq {self.last_seq})"


class SessionWriters:
    """Registre des écrivains d'une instance : un par session."""

    def __init__(self, max_writers: int = MAX_WRITERS) -> None:
        self._writers: dict[tuple[TenantId, SessionId], SessionWriter] = {}
        self._lock = asyncio.Lock()
        self._max = max_writers

    async def open(
        self, store: EventStore, tenant_id: TenantId, session_id: SessionId
    ) -> SessionWriter:
        """Écrivain de cette session, créé au premier appel."""
        key = (tenant_id, session_id)
        async with self._lock:
            writer = self._writers.pop(key, None)
            if writer is None or writer.store is not store:
                writer = await SessionWriter.open(store, tenant_id, session_id)
            self._writers[key] = writer
            while len(self._writers) > self._max:
                self._writers.pop(next(iter(self._writers)))
            return writer

    def forget(self, tenant_id: TenantId, session_id: SessionId) -> None:
        """Oublie l'écrivain d'une session supprimée (F7)."""
        self._writers.pop((tenant_id, session_id), None)

    def clear(self) -> None:
        self._writers.clear()

    def __len__(self) -> int:
        return len(self._writers)

    def __repr__(self) -> str:
        return f"SessionWriters({len(self._writers)} session(s))"
