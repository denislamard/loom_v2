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

from loom_ia.core.ports.bus import BusUnavailable, Notice

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
        # Les nouvelles, puis ``None`` à la fermeture du bus, ou ``BusUnavailable``
        # quand la connexion d'écoute meurt.
        self._queue: asyncio.Queue[Notice | BusUnavailable | None] = asyncio.Queue()
        # Une connexion ne fait qu'une chose à la fois : les publications se suivent.
        self._publishing = asyncio.Lock()
        self._closed = False

    def __repr__(self) -> str:
        return f"PostgresBus({CHANNEL!r})"

    async def publish(self, notice: Notice) -> None:
        if self._closed:
            return
        async with self._publishing:
            speaking = self._speaking
            if speaking is None or speaking.is_closed():
                # Jamais ouverte, ou morte depuis la dernière nouvelle.
                speaking = self._speaking = await asyncpg.connect(self._dsn)
            try:
                # ``pg_notify`` plutôt que ``NOTIFY`` : ce dernier n'accepte pas de
                # paramètre, et la charge utile serait à échapper à la main.
                await speaking.execute(_NOTIFY, CHANNEL, notice.model_dump_json())
            except Exception:
                # Connexion morte ou douteuse : on la jette, la prochaine
                # publication en ouvrira une autre.
                self._speaking = None
                speaking.terminate()
                raise

    async def notices(self) -> AsyncIterator[Notice]:
        """Écoute le canal jusqu'à la fermeture du bus.

        Si la connexion d'écoute meurt, lève ``BusUnavailable`` au lieu
        d'attendre une nouvelle qui ne viendra plus : c'est à l'appelant de
        s'abonner de nouveau, par un autre appel.
        """
        if self._closed:
            return
        connection = await asyncpg.connect(self._dsn)
        self._hearing = connection
        try:
            connection.add_termination_listener(self._lost)
            await connection.add_listener(CHANNEL, self._heard)
            while True:
                item = await self._queue.get()
                if item is None:
                    return
                if isinstance(item, BusUnavailable):
                    raise item
                yield item
        finally:
            self._hearing = None
            await connection.close()

    def _lost(self, connection: object) -> None:
        """Appelé par asyncpg à la mort d'une connexion ; ne doit rien attendre."""
        if connection is self._hearing:
            self._queue.put_nowait(BusUnavailable("la connexion d'écoute de Postgres est fermée"))

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
