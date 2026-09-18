# SPDX-License-Identifier: Apache-2.0
"""Journal qui prévient ses abonnés à chaque écriture (#22).

Les trois accès de la phase 1.6 doivent suivre un run pendant qu'il se
déroule. Plutôt que de relire le journal en boucle, cet enrobage remet
chaque événement écrit aux abonnés du process, dans l'ordre d'écriture.

Deux formes d'abonnement, la seconde bâtie sur la première :

- ``listen(sink)`` : une fonction appelée au moment de l'écriture ; c'est
  elle qui garantit l'ordre quand l'appelant mêle les événements à une autre
  source, comme les morceaux du modèle ;
- ``subscribe()`` : un objet qui s'itère en ``async for``.

C'est la place que prendra le bus du jalon J4 : le jour où les événements
passeront par un courtier, seuls ces deux abonnements changeront de source.
"""

import asyncio
from collections.abc import AsyncGenerator, Callable, Generator, Sequence
from contextlib import asynccontextmanager, contextmanager
from typing import Self

from loom_ia.core.events import Event, EventDraft, EventQuery
from loom_ia.core.model import RunId, SessionId, TenantId
from loom_ia.core.ports import EventStore

type EventSink = Callable[[Event], None]


class Subscription:
    """File d'événements alimentée par les écritures du journal.

    S'itère en ``async for`` jusqu'à la fermeture de l'abonnement. La file
    n'a pas de borne : un abonné lent retarde sa propre lecture, jamais
    l'écriture du journal.
    """

    def __init__(self) -> None:
        self._queue: asyncio.Queue[Event | None] = asyncio.Queue()
        self._closed = False

    def offer(self, event: Event) -> None:
        if not self._closed:
            self._queue.put_nowait(event)

    def close(self) -> None:
        """Termine l'itération, après les événements déjà en file."""
        if not self._closed:
            self._closed = True
            self._queue.put_nowait(None)

    @property
    def pending(self) -> int:
        return self._queue.qsize()

    def __aiter__(self) -> Self:
        return self

    async def __anext__(self) -> Event:
        event = await self._queue.get()
        if event is None:
            raise StopAsyncIteration
        return event


class NotifyingEventStore:
    """Enrobe un journal et remet ce qu'il écrit aux abonnés du process."""

    def __init__(self, inner: EventStore) -> None:
        self._inner = inner
        self._sinks: list[tuple[EventSink, RunId | None]] = []

    @property
    def inner(self) -> EventStore:
        return self._inner

    @property
    def listeners(self) -> int:
        return len(self._sinks)

    @contextmanager
    def listen(self, sink: EventSink, run_id: RunId | None = None) -> Generator[EventSink]:
        """Appelle ``sink`` à chaque écriture, le temps du bloc.

        Sans ``run_id``, tous les événements écrits sont remis. ``sink`` est
        appelé depuis ``append`` : il ne doit rien attendre.
        """
        entry = (sink, run_id)
        self._sinks.append(entry)
        try:
            yield sink
        finally:
            # ``aclose`` a pu vider la liste avant la sortie du bloc.
            if entry in self._sinks:
                self._sinks.remove(entry)

    @asynccontextmanager
    async def subscribe(self, run_id: RunId | None = None) -> AsyncGenerator[Subscription]:
        """Abonnement itérable, fermé à la sortie du bloc."""
        subscription = Subscription()
        with self.listen(subscription.offer, run_id):
            try:
                yield subscription
            finally:
                subscription.close()

    async def append(
        self, drafts: Sequence[EventDraft], *, expected_seq: int | None
    ) -> list[Event]:
        events = await self._inner.append(drafts, expected_seq=expected_seq)
        for event in events:
            for sink, run_id in tuple(self._sinks):
                if run_id is None or event.run_id == run_id:
                    sink(event)
        return events

    async def read(
        self,
        tenant_id: TenantId,
        session_id: SessionId,
        *,
        after_seq: int = 0,
        run_id: RunId | None = None,
    ) -> list[Event]:
        return await self._inner.read(tenant_id, session_id, after_seq=after_seq, run_id=run_id)

    async def query(self, query: EventQuery) -> list[Event]:
        return await self._inner.query(query)

    async def last_seq(self, tenant_id: TenantId, session_id: SessionId) -> int:
        return await self._inner.last_seq(tenant_id, session_id)

    async def aclose(self) -> None:
        self._sinks.clear()
        await self._inner.aclose()
