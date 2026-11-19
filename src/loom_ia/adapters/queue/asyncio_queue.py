# SPDX-License-Identifier: Apache-2.0
"""File de tâches en mémoire, dans le process de l'instance (point 27).

Mode librairie : une tâche asyncio par travail, sans stockage. La durabilité
vient du journal — une tâche perdue au redémarrage est retrouvée par la
reprise, pas par la file.

``key`` dédoublonne : un même travail demandé deux fois pendant qu'il tourne
n'est lancé qu'une fois (un seul résumé par session et par position, point 23).
Un travail qui se repose lui-même, depuis sa propre tâche, n'est pas un doublon :
c'est ainsi qu'un run tenu par un autre worker est repris après son bail
(``Loom._after_the_lease``).
``max_running`` borne les travaux menés de front : les suivants attendent
leur tour, ``pending``. Une reprise au démarrage remet d'un coup tous les
runs laissés en plan ; sans borne, ils partaient tous ensemble.
Une tâche qui échoue est journalisée et ne fait jamais échouer le run qui
l'a demandée.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from contextlib import AbstractAsyncContextManager, nullcontext
from dataclasses import dataclass
from typing import Final

from loom_ia.core.model import new_id
from loom_ia.core.ports import Job, JobKind, JobState

logger = logging.getLogger(__name__)

type Handler = Callable[[Job], Awaitable[None]]
"""Ce qui mène un travail de la file."""

MAX_REMEMBERED: Final = 256
"""Tâches terminées gardées pour qu'on puisse encore lire leur état."""


@dataclass(slots=True)
class _Entry:
    """Un travail de la file : la tâche, sa clé de dédoublonnage, son état et la tâche asyncio qui le mène.

    Attributes:
        job: La tâche.
        key: Clé de dédoublonnage, si la tâche en a une.
        state: État courant.
        task: Tâche asyncio qui mène le travail.
        delayed: Travail qui attend son heure (``delay``) et n'a rien commencé.
    """

    job: Job
    key: str | None
    state: JobState
    task: asyncio.Task[None] | None = None
    delayed: bool = False

    @property
    def open(self) -> bool:
        """Vrai pour un travail en attente ou en cours."""
        return self.state in {"pending", "running"}

    @property
    def busy(self) -> bool:
        """Travail en cours, par opposition à un travail qui dort encore."""
        return self.open and not self.delayed


class AsyncioTaskQueue:
    """File non durable : une ``asyncio.Task`` par travail."""

    def __init__(
        self,
        handlers: Mapping[JobKind, Handler],
        *,
        shutdown_timeout: float = 30.0,
        max_running: int | None = None,
    ) -> None:
        self._handlers = dict(handlers)
        self._shutdown_timeout = shutdown_timeout
        # Sans borne quand rien n'est dit : celle de la config est posée par le montage.
        self._places = asyncio.Semaphore(max_running) if max_running is not None else None
        self._entries: dict[str, _Entry] = {}
        self._by_key: dict[str, str] = {}
        self._closed = False

    async def submit(self, job: Job, *, key: str | None = None, delay: float | None = None) -> str:
        """Met une tâche en file et rend son identifiant (``TaskQueue.submit``).

        Avec ``key``, un travail de même clé encore ouvert n'est pas relancé : son
        identifiant est rendu. Sauf s'il s'agit du travail qui appelle : il se
        repose lui-même (après le bail d'un autre pilote), la nouvelle tâche
        prend la clé, et la fin de l'ancienne ne la libère pas. Avec ``delay``,
        le travail attend d'abord ce nombre de secondes.

        Args:
            job: La tâche.
            key: Clé de dédoublonnage.
            delay: Attente avant de commencer, en secondes.

        Raises:
            RuntimeError: La file est fermée.
        """
        if self._closed:
            raise RuntimeError("File fermée : plus de tâche acceptée")
        if key is not None:
            waiting = self._entries.get(self._by_key.get(key, ""))
            if waiting is not None and waiting.open and waiting.task is not asyncio.current_task():
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
        """État d'une tâche ; ``unknown`` si la file ne la connaît plus."""
        entry = self._entries.get(job_id)
        return entry.state if entry is not None else "unknown"

    async def cancel(self, job_id: str) -> bool:
        """Annule une tâche ouverte ; faux si elle est inconnue ou déjà terminée."""
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

    async def abandon(self) -> None:
        """Annule les tâches en cours au lieu de les attendre, et attend qu'elles s'arrêtent.

        Une tâche qui n'a pas commencé ne commence pas : ce qu'elle devait faire
        se retrouve au journal, comme après un arrêt brutal. Une tâche en route
        s'arrête là où elle en est, sans rien écrire de plus ; le temps laissé
        aux tâches pour s'arrêter est borné par ``shutdown_timeout``.
        """
        self._closed = True
        tasks = [e.task for e in self._entries.values() if e.open and e.task is not None]
        for task in tasks:
            task.cancel()
        if tasks:
            _, late = await asyncio.wait(tasks, timeout=self._shutdown_timeout)
            if late:
                logger.warning("File de tâches : %s tâche(s) ne s'arrêtent pas", len(late))
        for entry in self._entries.values():
            # Annulée avant son premier pas, la tâche n'a rien pu dire de son état.
            if entry.open and entry.task is not None and entry.task.done():
                entry.state = "cancelled"

    @property
    def open(self) -> int:
        """Tâches en attente ou en cours."""
        return sum(1 for entry in self._entries.values() if entry.open)

    def __repr__(self) -> str:
        return f"AsyncioTaskQueue({self.open} tâche(s) en cours)"

    async def _work(self, job_id: str, entry: _Entry, delay: float | None) -> None:
        """Mène un travail : attente du délai, place parmi les travaux de front, gestionnaire de son genre.

        Un échec est journalisé et marque la tâche ``failed`` sans remonter ; une
        annulation la marque ``cancelled``. La clé est libérée à la fin.
        """
        try:
            if delay:
                await asyncio.sleep(delay)
            entry.delayed = False
            async with self._place():
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

    def _place(self) -> AbstractAsyncContextManager[object]:
        """Une place parmi les travaux menés de front, ou aucune attente sans borne."""
        return self._places if self._places is not None else nullcontext()

    def _forget_old(self) -> None:
        """Oublie les tâches terminées les plus anciennes."""
        while len(self._entries) > MAX_REMEMBERED:
            for job_id, entry in self._entries.items():
                if not entry.open:
                    del self._entries[job_id]
                    break
            else:
                return
