# SPDX-License-Identifier: Apache-2.0
"""Bus des nouvelles par ``LISTEN``/``NOTIFY`` de Postgres (#5, H6).

Le bus de qui a déjà Postgres : il n'y a rien de plus à installer, et le
journal et les nouvelles tiennent sur la même base — donc sur la même panne,
ce qui est une qualité : un service qui perd sa base n'a plus de journal à
suivre de toute façon.

Une **connexion à part**, pas celle du pool du journal : une connexion qui
écoute ne peut rien faire d'autre, et elle doit rester ouverte. Elle ne prend
pas non plus le rôle applicatif — ``LISTEN`` ne lit aucune table, donc la
politique de lignes n'a rien à y voir, et le contenu, lui, ne passe pas par
là (c'est tout l'intérêt de ne transporter qu'une nouvelle).

La charge utile d'un ``NOTIFY`` est bornée à 8 000 octets. Une nouvelle en
fait moins de deux cents, et c'est précisément pourquoi elle ne porte que des
repères.
"""

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Final

import asyncpg

from loom_ia.core.ports.bus import Notice

logger = logging.getLogger(__name__)

# Le canal d'écoute. Fixe : deux déploiements sur la même base partagent leurs
# nouvelles, ce qui est sans effet puisque chacun relit son propre journal.
CHANNEL: Final = "loom_events"

_NOTIFY: Final = "SELECT pg_notify($1, $2)"


class PostgresBus:
    """Nouvelles diffusées par le canal ``loom_events`` d'une base Postgres."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._speaking: asyncpg.Connection[asyncpg.Record] | None = None
        self._hearing: asyncpg.Connection[asyncpg.Record] | None = None
        self._queue: asyncio.Queue[Notice | None] = asyncio.Queue()
        self._closed = False

    def __repr__(self) -> str:
        return f"PostgresBus({CHANNEL!r})"

    async def publish(self, notice: Notice) -> None:
        if self._closed:
            return
        if self._speaking is None:
            self._speaking = await asyncpg.connect(self._dsn)
        # ``pg_notify`` plutôt que ``NOTIFY`` : ce dernier n'accepte pas de
        # paramètre, et la charge utile serait à échapper à la main.
        await self._speaking.execute(_NOTIFY, CHANNEL, notice.model_dump_json())

    async def notices(self) -> AsyncIterator[Notice]:
        """Écoute le canal jusqu'à la fermeture du bus."""
        self._hearing = await asyncpg.connect(self._dsn)
        await self._hearing.add_listener(CHANNEL, self._heard)
        try:
            while True:
                notice = await self._queue.get()
                if notice is None:
                    return
                yield notice
        finally:
            listening = self._hearing
            self._hearing = None
            await listening.close()

    def _heard(self, _connection: object, _pid: int, _channel: str, payload: object) -> None:
        """Appelé par asyncpg à chaque ``NOTIFY`` ; ne doit rien attendre."""
        text = payload.decode() if isinstance(payload, bytes) else str(payload)
        try:
            self._queue.put_nowait(Notice.model_validate_json(text))
        except Exception:
            logger.warning("Bus Postgres : nouvelle illisible, ignorée")

    async def aclose(self) -> None:
        self._closed = True
        self._queue.put_nowait(None)
        speaking, self._speaking = self._speaking, None
        if speaking is not None:
            await speaking.close()
