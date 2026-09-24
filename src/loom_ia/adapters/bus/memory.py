# SPDX-License-Identifier: Apache-2.0
"""Bus en mémoire : le défaut, et de quoi éprouver le branchement (#5).

Il ne franchit aucune frontière de process — dans un seul process, le journal
notifiant remet déjà ses écritures à ses abonnés, donc ce bus ne sert à rien
en exploitation. Il sert à deux choses : garder un montage uniforme (il y a
toujours un bus, même quand il n'y a rien à traverser), et éprouver le
suivi — curseurs, rattrapage, nouvelles ignorées — sans service à installer.

Chaque abonné a sa file, sans borne : un abonné lent retarde sa propre
lecture, jamais la publication.
"""

import asyncio
from collections.abc import AsyncIterator

from loom_ia.core.ports.bus import Notice


class InMemoryBus:
    """Bus du process : ce qui est publié va aux abonnés de cette instance."""

    def __init__(self) -> None:
        self._queues: list[asyncio.Queue[Notice | None]] = []
        self._closed = False

    def __repr__(self) -> str:
        return f"InMemoryBus({len(self._queues)} abonné(s))"

    @property
    def subscribers(self) -> int:
        return len(self._queues)

    async def publish(self, notice: Notice) -> None:
        for queue in tuple(self._queues):
            queue.put_nowait(notice)

    async def notices(self) -> AsyncIterator[Notice]:
        queue: asyncio.Queue[Notice | None] = asyncio.Queue()
        self._queues.append(queue)
        try:
            while True:
                notice = await queue.get()
                if notice is None:
                    return
                yield notice
        finally:
            if queue in self._queues:
                self._queues.remove(queue)

    async def aclose(self) -> None:
        self._closed = True
        for queue in tuple(self._queues):
            queue.put_nowait(None)
