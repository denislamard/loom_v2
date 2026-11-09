# SPDX-License-Identifier: Apache-2.0
"""Les threads des outils : un pool à part, et le travail qui survit à son délai (B-2).

Un outil synchrone s'exécute dans un thread. Python ne sait pas arrêter un
thread : quand le délai de l'outil expire, ou que le run est annulé, le moteur
rend la main, mais le travail continue jusqu'à son terme. Un fichier peut
s'écrire, un courriel partir, après que le modèle a lu « délai dépassé ».

Ce module donne aux outils ``in_thread``, à la place d'``asyncio.to_thread`` :

- **un pool à part** (``ToolThreads``, threads ``loom-outil``), un par instance
  ``Loom``, et non celui d'asyncio : des threads bloqués n'empêchent plus le
  journal JSONL, le stockage d'artefacts ou un autre run du process d'écrire,
  qui passent par le pool d'asyncio ;
- **le suivi de l'appel** : le travail lancé pendant un appel est noté dans son
  suivi (``tracking``, posé par le moteur autour de l'appel). Après un délai, le
  moteur sait s'il tourne encore, et peut attendre sa fin réelle ;
- **l'annulation coopérative** : le suivi porte un ``threading.Event``
  (``ToolContext.cancelled``), levé au délai et à l'annulation, qu'un outil
  regarde entre deux étapes de son travail.

Les threads du pool sont des **démons** : ils n'empêchent pas le process de
s'arrêter. La fermeture de ``Loom`` (``ToolThreads.close``) attend ceux qui
travaillent encore, au plus ``grace`` secondes, en disant lesquels ; au-delà, le
process s'arrête sans eux, en le disant aussi (D-5).

Hors du moteur (pas de suivi), ``in_thread`` revient à ``asyncio.to_thread`` :
un outil appelé directement, ou le serveur ``loom-mem-gpu``, s'exécute comme avant.

Le module ne dépend que de la bibliothèque standard : un outil l'importe sans
rien tirer du moteur.
"""

import asyncio
import contextvars
import functools
import logging
import queue
import threading
import time
from collections.abc import Callable, Generator
from concurrent.futures import Future
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Final

logger = logging.getLogger(__name__)

DEFAULT_THREADS: Final = 16
DEFAULT_GRACE: Final = 30.0

type _Job = tuple[Future[object], Callable[[], object], str]


class ToolThreads:
    """Le pool des outils synchrones d'une instance : des threads démons, ``size`` au plus.

    Un travail soumis quand tous les threads sont pris attend son tour dans la
    file ; le délai de l'outil court pendant ce temps.
    """

    def __init__(self, size: int = DEFAULT_THREADS, *, grace: float | None = DEFAULT_GRACE) -> None:
        if size < 1:
            raise ValueError("threads : au moins 1")
        self.size = size
        # Attente au plus du travail en cours à la fermeture (``aclose``).
        self.grace = grace
        self._jobs: queue.SimpleQueue[_Job | None] = queue.SimpleQueue()
        self._lock = threading.Lock()
        self._workers: list[threading.Thread] = []
        # Un jeton par thread libre, comme ``ThreadPoolExecutor`` : sans jeton, un
        # nouveau thread, tant que le pool n'est pas plein.
        self._idle = threading.Semaphore(0)
        self._closed = False
        # Travail en cours, par future : l'outil, et depuis quand.
        self._running: dict[Future[object], tuple[str, float]] = {}

    def submit(self, fn: Callable[[], object], name: str) -> Future[object]:
        future: Future[object] = Future()
        with self._lock:
            if self._closed:
                raise RuntimeError("pool des outils fermé")
            self._jobs.put((future, fn, name))
            if not self._idle.acquire(blocking=False) and len(self._workers) < self.size:
                worker = threading.Thread(
                    target=self._work, name=f"loom-outil-{len(self._workers) + 1}", daemon=True
                )
                self._workers.append(worker)
                worker.start()
        return future

    @property
    def running(self) -> list[tuple[str, float]]:
        """Le travail en cours : nom de l'outil et secondes écoulées, le plus ancien d'abord."""
        now = time.monotonic()
        with self._lock:
            found = sorted(self._running.values(), key=lambda item: item[1])
        return [(name, now - since) for name, since in found]

    def close(self, grace: float | None) -> list[str]:
        """Ferme le pool ; attend le travail en cours au plus ``grace`` s (``None`` : sans borne).

        Le travail qui attendait son tour est annulé. Rend les outils encore au
        travail à l'échéance : le process s'arrêtera sans les attendre.
        """
        with self._lock:
            self._closed = True
            workers = list(self._workers)
        cancelled = 0
        while True:
            try:
                job = self._jobs.get_nowait()
            except queue.Empty:
                break
            if job is not None and job[0].cancel():
                cancelled += 1
        for _ in workers:
            self._jobs.put(None)
        busy = self.running
        if busy:
            logger.warning(
                "Fermeture : attend la fin de %d outil(s) en arrière-plan (%s)%s",
                len(busy),
                ", ".join(f"{name}, depuis {since:.0f} s" for name, since in busy),
                f", {grace:g} s au plus" if grace is not None else "",
            )
        deadline = None if grace is None else time.monotonic() + grace
        for worker in workers:
            left = None if deadline is None else max(0.0, deadline - time.monotonic())
            worker.join(left)
        still = [name for name, _ in self.running]
        if still:
            logger.warning(
                "Fermeture sans attendre %d outil(s) encore au travail (%s) : "
                "leur effet éventuel est d'état inconnu",
                len(still),
                ", ".join(still),
            )
        return still

    async def aclose(self) -> None:
        """``close(grace)``, hors de la boucle : l'attente des threads ne la bloque pas."""
        await asyncio.to_thread(self.close, self.grace)

    def _work(self) -> None:
        while True:
            job = self._jobs.get()
            if job is None:
                return
            future, fn, name = job
            if not future.set_running_or_notify_cancel():
                continue
            with self._lock:
                self._running[future] = (name, time.monotonic())
            try:
                result = fn()
            except BaseException as exc:
                future.set_exception(exc)
            else:
                future.set_result(result)
            finally:
                with self._lock:
                    self._running.pop(future, None)
                self._idle.release()


@dataclass(slots=True)
class CallThreads:
    """Le travail en thread d'un appel d'outil, son pool, et son signal d'annulation."""

    name: str
    pool: ToolThreads | None = None
    # Levé au délai et à l'annulation : l'outil le regarde quand il le peut (D-8).
    cancelled: threading.Event = field(default_factory=threading.Event)
    futures: list[Future[object]] = field(default_factory=list[Future[object]])
    # Délai expiré ou appel annulé : un travail qui finit après est « en retard ».
    abandoned_at: float | None = None

    @property
    def running(self) -> list[Future[object]]:
        return [future for future in self.futures if not future.done()]

    def abandon(self) -> None:
        """L'appel rend la main sans attendre son travail : l'annulation est signalée, et
        ce qui finit ensuite le dit dans les logs."""
        self.cancelled.set()
        if self.abandoned_at is not None:
            return
        self.abandoned_at = time.monotonic()
        for future in self.running:
            future.add_done_callback(self._late)

    def _late(self, future: Future[object]) -> None:
        since = time.monotonic() - (self.abandoned_at or time.monotonic())
        failed = not future.cancelled() and future.exception() is not None
        logger.warning(
            "Outil %s : travail fini %.1f s après que l'appel a rendu la main (%s) ; "
            "son résultat est perdu",
            self.name,
            since,
            "en erreur" if failed else "effet produit si l'outil en a un",
        )


_current: contextvars.ContextVar[CallThreads | None] = contextvars.ContextVar(
    "loom_tool_threads", default=None
)


@contextmanager
def tracking(threads: CallThreads) -> Generator[CallThreads]:
    """Suit le travail en thread lancé pendant l'appel d'un outil (posé par le moteur)."""
    token = _current.set(threads)
    try:
        yield threads
    finally:
        _current.reset(token)


def current() -> CallThreads | None:
    """Le suivi de l'appel en cours, ou ``None`` hors du moteur."""
    return _current.get()


async def in_thread[**P, R](fn: Callable[P, R], /, *args: P.args, **kwargs: P.kwargs) -> R:
    """``asyncio.to_thread``, dans le pool des outils et suivi par l'appel en cours.

    Le contexte (``contextvars``) passe au thread, comme avec ``to_thread``. Hors
    du moteur, ou sans pool, c'est ``asyncio.to_thread``.
    """
    threads = _current.get()
    if threads is None or threads.pool is None:
        return await asyncio.to_thread(fn, *args, **kwargs)
    context = contextvars.copy_context()
    call = functools.partial(context.run, fn, *args, **kwargs)
    future = threads.pool.submit(call, threads.name)
    threads.futures.append(future)
    result = await asyncio.wrap_future(future)
    return result  # pyright: ignore[reportReturnType]
