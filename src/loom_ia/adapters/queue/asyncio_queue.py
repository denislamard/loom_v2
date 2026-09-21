# SPDX-License-Identifier: Apache-2.0
"""File de tâches en mémoire, dans le process de l'instance (#27).

Mode librairie : une tâche asyncio par travail, sans stockage. La durabilité
vient du journal — une tâche perdue au redémarrage est retrouvée par la
reprise, pas par la file.

``key`` dédoublonne : un même travail demandé deux fois pendant qu'il tourne
n'est lancé qu'une fois (un seul résumé par session et par position, #23).
Une tâche qui échoue est journalisée et ne fait jamais échouer le run qui
l'a demandée.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Final

from loom_ia.core.model import new_id
from loom_ia.core.ports import Job, JobKind, JobState

logger = logging.getLogger(__name__)

type Handler = Callable[[Job], Awaitable[None]]

# Tâches terminées gardées pour qu'on puisse encore lire leur état.
MAX_REMEMBERED: Final = 256


@dataclass(slots=True)
class _Entry:
    job: Job
    key: str | None
    state: JobState
    task: asyncio.Task[None] | None = None
    # Travail qui attend son heure (``delay``) et n'a rien commencé.
    delayed: bool = False

    @property
    def open(self) -> bool:
        return self.state in {"pending", "running"}

    @property
    def busy(self) -> bool:
        """Travail en cours, par opposition à un travail qui dort encore."""
        return self.open and not self.delayed


class AsyncioTaskQueue:
    """File non durable : une ``asyncio.Task`` par travail."""

    def __init__(
        self, handlers: Mapping[JobKind, Handler], *, shutdown_timeout: float = 30.0
    ) -> None:
        self._handlers = dict(handlers)
        self._shutdown_timeout = shutdown_timeout
        self._entries: dict[str, _Entry] = {}
        self._by_key: dict[str, str] = {}
        self._closed = False

    async def submit(self, job: Job, *, key: str | None = None, delay: float | None = None) -> str:
        if self._closed:
            raise RuntimeError("File fermée : plus de tâche acceptée")
        if key is not None:
            waiting = self._entries.get(self._by_key.get(key, ""))
            if waiting is not None and waiting.open:
                return self._by_key[key]
        job_id = new_id()
        entry = _Entry(job=job, key=key, state="pending", delayed=bool(delay))
        self._entries[job_id] = entry
        if key is not None:
            self._by_key[key] = job_id
        entry.task = asyncio.create_task(self._work(job_id, entry, delay), name=f"loom-{job.kind}")
        self._forget_old()
        return job_id

    async def state(self, job_id: str) -> JobState:
        entry = self._entries.get(job_id)
        return entry.state if entry is not None else "unknown"

    async def cancel(self, job_id: str) -> bool:
        entry = self._entries.get(job_id)
        if entry is None or not entry.open or entry.task is None:
            return False
        entry.task.cancel()
        return True

    async def drain(self) -> None:
        """Attend les tâches en cours, sans en accepter de nouvelles.

        Un travail différé qui dort encore n'est pas une tâche en cours : il
        n'a rien commencé, et l'attendre bloquerait pour toute la durée de son
        délai. Ce qu'il devait faire se retrouve de toute façon au journal.
        """
        while True:
            running = [e.task for e in self._entries.values() if e.busy and e.task is not None]
            if not running:
                return
            await asyncio.gather(*running, return_exceptions=True)

    async def aclose(self) -> None:
        """Attend les tâches en cours, puis abandonne celles qui traînent.

        Les travaux différés qui dorment encore sont abandonnés sans attendre :
        rien ne justifie de retenir la fermeture pour un réveil à venir.
        """
        self._closed = True
        for entry in self._entries.values():
            if entry.delayed and entry.task is not None:
                entry.task.cancel()
        try:
            async with asyncio.timeout(self._shutdown_timeout):
                await self.drain()
        except TimeoutError:
            logger.warning("File de tâches : %s tâche(s) abandonnée(s) à la fermeture", self.open)
            for entry in self._entries.values():
                if entry.open and entry.task is not None:
                    entry.task.cancel()

    @property
    def open(self) -> int:
        """Tâches en attente ou en cours."""
        return sum(1 for entry in self._entries.values() if entry.open)

    def __repr__(self) -> str:
        return f"AsyncioTaskQueue({self.open} tâche(s) en cours)"

    async def _work(self, job_id: str, entry: _Entry, delay: float | None) -> None:
        try:
            if delay:
                await asyncio.sleep(delay)
            entry.delayed = False
            entry.state = "running"
            handler = self._handlers.get(entry.job.kind)
            if handler is None:
                entry.state = "failed"
                logger.error("Tâche %s : aucun gestionnaire pour %r", job_id, entry.job.kind)
                return
            await handler(entry.job)
            entry.state = "done"
        except asyncio.CancelledError:
            entry.state = "cancelled"
            raise
        except Exception:
            entry.state = "failed"
            logger.warning("Tâche %s (%s) en échec", job_id, entry.job.kind, exc_info=True)
        finally:
            if entry.key is not None and self._by_key.get(entry.key) == job_id and not entry.open:
                del self._by_key[entry.key]

    def _forget_old(self) -> None:
        """Oublie les tâches terminées les plus anciennes."""
        while len(self._entries) > MAX_REMEMBERED:
            for job_id, entry in self._entries.items():
                if not entry.open:
                    del self._entries[job_id]
                    break
            else:
                return
