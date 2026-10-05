# SPDX-License-Identifier: Apache-2.0
"""Export des runs terminés vers les collecteurs (K4, #29, §14.1).

Un abonné du journal, qui ne regarde qu'une chose : l'événement qui **clôt**
un run (``run.completed``, ``run.failed``, ``run.cancelled``). À ce moment, il
relit le run dans le journal, en tire les spans (``run_spans``) et les remet
aux collecteurs. Trois conséquences, voulues :

- **un run part entier, une fois** — même repris par un autre worker après
  une pause ou une panne, c'est le process qui le clôt qui l'exporte, et il
  le relit depuis le journal, pas depuis ce qu'il a vu passer ;
- **chaque process exporte ce qu'il écrit** (``listen(own=True)``) : deux
  workers suivis par un même bus n'envoient jamais la même trace deux fois ;
- **un run inachevé n'est pas exporté** — en pause, il n'apparaît qu'à sa
  fin ; et un process qui meurt entre sa dernière écriture et l'envoi perd
  cette trace, faute de curseur gardé quelque part. Le journal, lui, a tout.

L'export ne met jamais un run en échec : une panne de collecteur est un
avertissement dans les logs.
"""

import asyncio
import logging
from collections.abc import Callable, Sequence
from typing import Protocol

from loom_ia.core.events import Event
from loom_ia.core.model import RunId, SessionId, TenantId
from loom_ia.core.ports import EventStore
from loom_ia.telemetry.redaction import Redactor
from loom_ia.telemetry.spans import SpanRecord, finished, run_spans

logger = logging.getLogger(__name__)


class SpanSink(Protocol):
    """Un collecteur : reçoit des spans, les envoie à son rythme."""

    @property
    def name(self) -> str: ...

    def export(self, spans: Sequence[SpanRecord]) -> None:
        """Prend les spans en charge sans attendre le réseau."""
        ...

    async def aclose(self, grace: float) -> None:
        """Envoie ce qui reste, dans la limite du délai, et ferme."""
        ...


class RunExporter:
    """Remet aux collecteurs les spans de chaque run qui se termine ici.

    ``content_for`` dit, client par client, si le contenu part
    (``capture.exports: content``) ; ``redactor`` masque ce contenu.
    """

    def __init__(
        self,
        store: EventStore,
        sinks: Sequence[SpanSink],
        *,
        content_for: Callable[[TenantId], bool],
        redactor: Redactor,
    ) -> None:
        self._store = store
        self._sinks = tuple(sinks)
        self._content_for = content_for
        self._redactor = redactor
        self._pending: set[asyncio.Task[None]] = set()
        self._closed = False

    @property
    def sinks(self) -> tuple[SpanSink, ...]:
        return self._sinks

    @property
    def pending(self) -> int:
        return len(self._pending)

    def __call__(self, event: Event) -> None:
        """Abonné du journal : appelé à chaque écriture, n'attend rien."""
        if self._closed or not finished(event):
            return
        task = asyncio.get_running_loop().create_task(
            self._export(event.tenant_id, event.session_id, event.run_id),
            name=f"loom-export-{event.run_id}",
        )
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    async def export_run(self, tenant_id: TenantId, session_id: SessionId, run_id: RunId) -> int:
        """Exporte un run tel qu'il est au journal ; rend le nombre de spans remis."""
        events = await self._store.read(tenant_id, session_id, run_id=run_id)
        if not events:
            return 0
        spans = run_spans(events, content=self._content_for(tenant_id), redactor=self._redactor)
        for sink in self._sinks:
            try:
                sink.export(spans)
            except Exception as error:  # un collecteur ne met jamais un run en échec
                logger.warning("Export %s : run %s non remis (%s)", sink.name, run_id, error)
        return len(spans)

    async def aclose(self, grace: float) -> None:
        """Attend les exports en cours, puis vide et ferme les collecteurs."""
        self._closed = True
        if self._pending:
            _, late = await asyncio.wait(tuple(self._pending), timeout=grace)
            for task in late:
                task.cancel()
            if late:
                logger.warning("Export : %d run(s) non remis à la fermeture", len(late))
        for sink in self._sinks:
            try:
                await sink.aclose(grace)
            except Exception as error:
                logger.warning("Export %s : fermeture en erreur (%s)", sink.name, error)

    async def _export(self, tenant_id: TenantId, session_id: SessionId, run_id: RunId) -> None:
        try:
            await self.export_run(tenant_id, session_id, run_id)
        except Exception as error:
            logger.warning("Export : run %s non relu (%s)", run_id, error)
