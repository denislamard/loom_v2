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

Les deux se restreignent à un run (``run_id``) ou à un filtre (``accept``),
appelé à chaque écriture dans l'ordre du journal : l'arbre d'un run
(``RunTree.admit``) s'y reconnaît au fil de l'eau.

C'est la place que prendra le bus du jalon J4 : le jour où les événements
passeront par un courtier, seuls ces deux abonnements changeront de source.
"""

import asyncio
import logging
from collections.abc import AsyncGenerator, Callable, Generator, Sequence
from contextlib import asynccontextmanager, contextmanager
from typing import Self

from loom_ia.core.events import Event, EventDraft, EventQuery
from loom_ia.core.model import RunId, SessionId, TenantId, new_id
from loom_ia.core.ports import EventBus, EventStore, Notice, SessionRecord

logger = logging.getLogger(__name__)

type EventSink = Callable[[Event], None]
type EventFilter = Callable[[Event], bool]


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
    """Enrobe un journal et remet ce qu'il écrit aux abonnés du process.

    Avec un ``bus`` (5.3c), il remet aussi ce que **les autres process**
    écrivent : leurs nouvelles disent où regarder, le journal est relu, et les
    événements retrouvés vont aux mêmes abonnés. Un abonné ne sait pas d'où
    vient ce qu'il reçoit, et c'est tout l'intérêt — SSE, ``Loom.stream()``,
    la progression MCP et la CLI n'ont pas changé d'une ligne.
    """

    def __init__(self, inner: EventStore, *, bus: EventBus | None = None, source: str = "") -> None:
        self._inner = inner
        self._bus = bus
        # Qui écrit, pour ne pas se réécouter soi-même : ce que cette instance
        # écrit, elle l'a déjà remis à ses abonnés.
        self._source = source or new_id()
        # Position atteinte par journal, pour relire ce qui manque et pas plus.
        self._cursors: dict[tuple[TenantId, SessionId], int] = {}
        self._sinks: list[tuple[EventSink, RunId | None, EventFilter | None]] = []

    @property
    def inner(self) -> EventStore:
        return self._inner

    @property
    def listeners(self) -> int:
        return len(self._sinks)

    @contextmanager
    def listen(
        self,
        sink: EventSink,
        run_id: RunId | None = None,
        *,
        accept: EventFilter | None = None,
    ) -> Generator[EventSink]:
        """Appelle ``sink`` à chaque écriture, le temps du bloc.

        Sans ``run_id`` ni ``accept``, tous les événements écrits sont remis.
        ``sink`` et ``accept`` sont appelés depuis ``append`` : ils ne doivent
        rien attendre.
        """
        entry = (sink, run_id, accept)
        self._sinks.append(entry)
        try:
            yield sink
        finally:
            # ``aclose`` a pu vider la liste avant la sortie du bloc.
            if entry in self._sinks:
                self._sinks.remove(entry)

    @asynccontextmanager
    async def subscribe(
        self, run_id: RunId | None = None, *, accept: EventFilter | None = None
    ) -> AsyncGenerator[Subscription]:
        """Abonnement itérable, fermé à la sortie du bloc."""
        subscription = Subscription()
        with self.listen(subscription.offer, run_id, accept=accept):
            try:
                yield subscription
            finally:
                subscription.close()

    async def append(
        self, drafts: Sequence[EventDraft], *, expected_seq: int | None
    ) -> list[Event]:
        events = await self._inner.append(drafts, expected_seq=expected_seq)
        for event in events:
            self._offer(event)
        await self._announce(events)
        return events

    def _offer(self, event: Event) -> None:
        """Remet un événement aux abonnés que son run et leur filtre acceptent."""
        for sink, run_id, accept in tuple(self._sinks):
            if run_id is not None and event.run_id != run_id:
                continue
            if accept is None or accept(event):
                sink(event)

    # --- Bus (5.3c) -------------------------------------------------------

    @property
    def source(self) -> str:
        """Identité de cette instance sur le bus."""
        return self._source

    async def _announce(self, events: Sequence[Event]) -> None:
        """Dit aux autres process qu'il y a du neuf. N'échoue jamais.

        Une écriture au journal a eu lieu : la manquer sur le bus coûte une
        notification, pas un événement, et celui qui la rate rattrape à la
        suivante. Faire échouer l'écriture pour un bus en panne serait
        disproportionné.
        """
        if self._bus is None or not events:
            return
        first, last = events[0], events[-1]
        key = (last.tenant_id, last.session_id)
        # Ce qui vient d'être écrit ici est déjà remis : la position avance,
        # et notre propre nouvelle ne fera rien relire.
        self._cursors[key] = max(self._cursors.get(key, 0), last.seq)
        notice = Notice(
            tenant_id=last.tenant_id,
            session_id=last.session_id,
            first_seq=first.seq,
            last_seq=last.seq,
            source=self._source,
        )
        try:
            await self._bus.publish(notice)
        except Exception as error:  # le bus ne met jamais une écriture en échec
            logger.warning("Bus : nouvelle non publiée (%s)", error)

    async def follow(self) -> None:
        """Suit le bus jusqu'à sa fermeture, et remet ce que les autres écrivent."""
        if self._bus is None:
            return
        async for notice in self._bus.notices():
            if notice.source == self._source:
                continue
            try:
                await self._catch_up(notice)
            except Exception:
                logger.exception("Bus : nouvelle de %s non suivie", notice.session_id)

    async def _catch_up(self, notice: Notice) -> None:
        """Relit ce qui manque pour ce journal et le remet aux abonnés.

        Depuis notre position s'il y en a une — ce qui rattrape ce que le bus
        aurait laissé passer —, depuis le début du lot annoncé sinon.
        """
        key = (notice.tenant_id, notice.session_id)
        seen = self._cursors.get(key)
        after = notice.first_seq - 1 if seen is None else seen
        if after >= notice.last_seq:
            return
        if not self._sinks:
            # Personne n'écoute : inutile de relire, la position suffit.
            self._cursors[key] = notice.last_seq
            return
        events = await self._inner.read(notice.tenant_id, notice.session_id, after_seq=after)
        for event in events:
            self._offer(event)
        self._cursors[key] = max(notice.last_seq, events[-1].seq if events else 0)

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

    async def sessions(self, tenant_id: TenantId) -> list[SessionRecord]:
        return await self._inner.sessions(tenant_id)

    async def delete(self, tenant_id: TenantId, session_id: SessionId) -> int:
        return await self._inner.delete(tenant_id, session_id)

    async def aclose(self) -> None:
        self._sinks.clear()
        await self._inner.aclose()
