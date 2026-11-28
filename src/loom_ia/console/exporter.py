# SPDX-License-Identifier: Apache-2.0
"""L'exportateur : les événements du journal vers la console, sans perte et sans jamais retenir un run (docs/analyse-exortateur-loom.md, § 6).

Le journal fait foi ; l'exportateur n'en garde aucune copie. Il reçoit chaque
événement que **ce** process écrit (``listen(own=True)`` : chaque process
exporte sa part), le met dans une file bornée, et une tâche à part l'envoie par
lots. La console acquitte chaque lot ; les positions acquittées vont au curseur
(``cursor``), rangé avec le journal.

- **Le run n'attend jamais** : ``offer`` ne fait que filtrer et mettre en file.
  File pleine, rien n'est perdu : la session est marquée « à relire » et sera
  relue dans le journal (décision D-4).
- **Rattrapage** à chaque connexion : les sessions dont le curseur est en retard
  sur le journal sont relues après leur dernier ``seq`` acquitté ; celles
  qu'un arrêt brutal aurait empêché de noter, d'après ``flushed_at`` moins une
  marge (Q-6) ; au premier démarrage, le curseur part de la position actuelle
  de chaque session, sans rien envoyer (Q-11) — sauf pour une session qui a un
  run racine non terminé : elle part du premier événement du plus ancien, pour
  que la console voie dès le branchement ce qui attend (L-1,
  docs/analyse-securite-hello.md).
- **Ni perte ni doublon côté console** : un lot non acquitté avant une coupure
  est renvoyé ; la console écarte ce qu'elle a déjà par ``(client, session,
  seq)`` (D-3). En direct, un événement déjà parti par le rattrapage est écarté.
- **Effacement** (Q-10) : une session effacée chez loom est dite à la console
  (``session.deleted``) jusqu'à son acquittement — effacée ici, ou constatée
  absente du journal (effacée par un autre process).
"""

import asyncio
import logging
from collections import deque
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Final

from pydantic import ValidationError

from loom_ia.config.console import ExportMode
from loom_ia.console.channel import ConsoleChannel
from loom_ia.console.contract import AckPayload, EventsPayload, SessionDeletedPayload
from loom_ia.console.cursor import ConsoleCursor
from loom_ia.console.translate import public_event
from loom_ia.core.events import Event
from loom_ia.core.model import SessionId, TenantId, new_id
from loom_ia.core.ports import EventStore
from loom_ia.core.ports.cipher import SealError
from loom_ia.core.ports.event_store import JournalCorrupted, SessionRecord
from loom_ia.core.projections import ProjectionError, unfinished_roots
from loom_ia.telemetry.redaction import Redactor

logger = logging.getLogger(__name__)

CATCH_UP_MARGIN: Final = timedelta(seconds=60)
"""Marge du rattrapage (Q-6) : l'heure d'un événement est posée avant son écriture,
et les horloges de deux machines diffèrent de quelques secondes."""

FLUSH_INTERVAL: Final = 1.0
"""Écriture du curseur, au plus souvent, en secondes."""

RECONCILE_INTERVAL: Final = 300.0
"""Recherche des sessions effacées par un autre process, en secondes."""

MAX_IN_FLIGHT: Final = 32
"""Lots envoyés sans acquittement, au plus : au-delà, on attend la console."""

ACK_WAIT: Final = 5.0
"""Attente d'un acquittement quand trop de lots sont en vol, en secondes."""

_UNREADABLE: Final = (JournalCorrupted, SealError, ValidationError, ProjectionError)
"""Ce que lève un journal de session illisible : le premier démarrage passe cette session, comme ``recover()``."""


class ConsoleExporter:
    """Envoie à la console les événements que ce process écrit, et ce qu'elle n'a pas acquitté.

    Args:
        store: Le journal, où relire.
        cursor: Le curseur, rangé avec le journal.
        channel: La connexion vers la console.
        tenant: Le client que la console sert : seuls ses événements partent.
        mode: ``on_demand`` ou ``push`` (``translate.public_event``).
        redactor: Masqueur du contenu, en ``push``.
        exclude: Types d'événements qui ne partent pas.
        buffer_size: Taille de la file.
        batch_size: Événements par lot, au plus.
        batch_delay: Attente maximale d'un lot incomplet, en secondes.
    """

    def __init__(
        self,
        store: EventStore,
        cursor: ConsoleCursor,
        channel: ConsoleChannel,
        *,
        tenant: TenantId,
        mode: ExportMode,
        redactor: Redactor | None = None,
        exclude: Iterable[str] = (),
        buffer_size: int = 10_000,
        batch_size: int = 200,
        batch_delay: float = 0.2,
    ) -> None:
        self._store = store
        self._cursor = cursor
        self._channel = channel
        self._tenant = tenant
        self._mode: ExportMode = mode
        self._redactor = redactor
        self._exclude = frozenset(exclude)
        self._buffer_size = buffer_size
        self._batch_size = batch_size
        self._batch_delay = batch_delay
        self._queue: deque[Event] = deque()
        self._stale: set[SessionId] = set()
        self._acked: dict[SessionId, int] = {}
        self._dirty: dict[SessionId, int] = {}
        self._deleted: set[SessionId] = set()
        self._sent: dict[SessionId, int] = {}
        self._in_flight: dict[str, dict[SessionId, int]] = {}
        self._deletions: dict[str, SessionId] = {}
        self._flushed_at: datetime | None = None
        self._loaded = False
        self._resync = True
        self._closing = False
        self._finished = asyncio.Event()
        self._wake = asyncio.Event()
        self._space = asyncio.Event()
        self._last_flush = 0.0
        self._last_reconcile = 0.0

    # --- Ce que les autres appellent -------------------------------------------------

    def offer(self, event: Event) -> None:
        """Abonné du journal : appelé à chaque écriture de ce process, il n'attend rien.

        Une session vue pour la première fois est notée au curseur dès maintenant
        (à sa prochaine écriture), pour qu'un arrêt brutal ne la fasse pas oublier.
        """
        if self._closing or event.tenant_id != self._tenant or event.type in self._exclude:
            return
        session = event.session_id
        if session not in self._acked:
            self._acked[session] = 0
            self._dirty.setdefault(session, 0)
        if session in self._stale:
            return
        if len(self._queue) >= self._buffer_size:
            # Rien ne se perd : le journal a l'événement, la session sera relue.
            self._stale.add(session)
        else:
            self._queue.append(event)
        self._wake.set()

    def connected(self) -> None:
        """Le canal vient d'ouvrir une connexion : tout ce qui était en vol est à renvoyer."""
        self._resync = True
        self._wake.set()

    async def received(self, ack: AckPayload) -> None:
        """Un acquittement : d'un lot, ou d'un effacement de session."""
        positions = self._in_flight.pop(ack.batch_id, None)
        if positions is not None:
            for session, seq in positions.items():
                if seq > self._acked.get(session, 0):
                    self._acked[session] = seq
                    self._dirty[session] = seq
            self._space.set()
            return
        session = self._deletions.pop(ack.batch_id, None)
        if session is not None:
            self._deleted.discard(session)
            await self._cursor.forget(self._tenant, (session,))
            self._space.set()

    async def session_deleted(self, tenant: TenantId, session: SessionId) -> None:
        """Une session vient d'être effacée dans ce process : la console doit effacer sa copie (Q-10)."""
        if tenant != self._tenant:
            return
        self._forget_locally(session)
        self._deleted.add(session)
        await self._cursor.mark_deleted(self._tenant, (session,))
        self._wake.set()

    @property
    def pending(self) -> int:
        """Événements en file, pas encore envoyés."""
        return len(self._queue)

    @property
    def in_flight(self) -> int:
        """Lots envoyés, pas encore acquittés."""
        return len(self._in_flight)

    # --- La tâche --------------------------------------------------------------------

    async def run(self) -> None:
        """Envoie, rattrape et écrit le curseur jusqu'à ``aclose`` ; une panne est journalisée, puis on reprend."""
        try:
            while True:
                try:
                    await self._load()
                    if self._closing:
                        await self._finish()
                        return
                    await self._wait_for_work()
                    await self._step()
                    # Un tour sans attente réelle rend tout de même la main : les
                    # acquittements se lisent dans la même boucle.
                    await asyncio.sleep(0)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("Console : export interrompu, reprise dans %g s", FLUSH_INTERVAL)
                    self._resync = True
                    await asyncio.sleep(FLUSH_INTERVAL)
        finally:
            self._finished.set()

    async def aclose(self, grace: float) -> None:
        """Demande à la tâche d'envoyer ce qui reste et d'attendre les acquittements, dans la limite du délai.

        Ce qui n'est pas acquitté à temps sera renvoyé au prochain démarrage : le
        journal l'a, et le curseur ne l'a pas compté.
        """
        self._closing = True
        self._wake.set()
        try:
            async with asyncio.timeout(grace):
                await self._finished.wait()
        except TimeoutError:
            if self._in_flight:
                logger.warning("Console : %d lot(s) non acquittés à l'arrêt, renvoyés au prochain démarrage", len(self._in_flight))
            else:
                logger.warning("Console injoignable à l'arrêt : %d événement(s) non envoyés, rattrapés à la prochaine connexion", len(self._queue))
        try:
            await self._flush(force=True)
        except Exception as error:
            logger.warning("Console : curseur non écrit à l'arrêt (%s)", error)

    async def _finish(self) -> None:
        """Le dernier tour : attendre une connexion encore en cours, envoyer, attendre les acquittements, écrire le curseur.

        Une commande courte (``loom run``) finit souvent avant que la connexion ne
        soit ouverte : on l'attend, dans le délai d'``aclose``. Une console qui a
        refusé la clé ou le contrat ne fait rien attendre, ni une console pas encore
        jointe quand ce process n'a rien à envoyer : l'historique non acquitté
        sera rattrapé à la prochaine connexion, le curseur n'ayant pas avancé.
        """
        unsaid = self._deleted - set(self._deletions.values())
        if not self._channel.connected and not (self._queue or self._stale or self._in_flight or self._deletions or unsaid):
            await self._flush(force=True)
            return
        if not self._channel.connected and self._channel.state in ("idle", "connecting"):
            await self._channel.wait_connected()
        if self._channel.connected:
            await self._step()
        while (self._in_flight or self._deletions) and self._channel.connected:
            if not await self._wait_for_space():
                break
        await self._flush(force=True)

    # --- Le travail ------------------------------------------------------------------

    async def _load(self) -> None:
        """Lit le curseur une fois ; au premier démarrage, le pose à la position actuelle du journal (Q-11), avant les runs en cours (L-1)."""
        if self._loaded:
            return
        state = await self._cursor.load(self._tenant)
        if state is None:
            records = await self._store.sessions(self._tenant)
            # Ce qui est déjà en file a été écrit depuis l'ouverture du canal : ce
            # n'est pas de l'historique, le curseur s'arrête juste avant.
            offered: dict[SessionId, int] = {}
            for event in self._queue:
                offered.setdefault(event.session_id, event.seq)
            start = {record.session_id: min(record.last_seq, offered.get(record.session_id, record.last_seq + 1) - 1) for record in records}
            running = await self._unfinished_starts(records)
            for session, seq in running.items():
                start[session] = min(start[session], seq)
            for session, seq in start.items():
                self._acked[session] = max(self._acked.get(session, 0), seq)
            now = datetime.now(UTC)
            await self._cursor.advance(self._tenant, start, flushed_at=now)
            self._flushed_at = now
            # Les runs en cours partent par le rattrapage, relu après ces positions.
            self._resync = bool(running)
        else:
            for session, seq in state.acked.items():
                self._acked[session] = max(self._acked.get(session, 0), seq)
            self._deleted |= set(state.deleted)
            self._flushed_at = state.flushed_at
        self._loaded = True

    async def _unfinished_starts(self, records: Iterable[SessionRecord]) -> dict[SessionId, int]:
        """Pour chaque session qui a un run racine non terminé, la position juste avant le premier événement du plus ancien (L-1)."""
        starts: dict[SessionId, int] = {}
        for record in records:
            try:
                events = await self._store.read(self._tenant, record.session_id)
                running = {state.run_id for state in unfinished_roots(events)}
            except _UNREADABLE as error:
                logger.warning("Console : session %s illisible, ses runs en cours ne partent pas (%s : %s)", record.session_id, type(error).__name__, error)
                continue
            if running:
                starts[record.session_id] = min(event.seq for event in events if event.run_id in running) - 1
        if starts:
            logger.info("Console : premier branchement, %d session(s) avec un run en cours envoyée(s) depuis son début", len(starts))
        return starts

    async def _wait_for_work(self) -> None:
        """Attend du travail : un événement, une connexion, ou le terme de l'écriture du curseur."""
        unsaid = self._deleted - set(self._deletions.values())
        work = self._queue or self._stale or unsaid or self._resync
        if not (self._channel.connected and work):
            self._wake.clear()
            try:
                async with asyncio.timeout(FLUSH_INTERVAL):
                    await self._wake.wait()
            except TimeoutError:
                pass
        if 0 < len(self._queue) < self._batch_size and not self._closing:
            # Un lot incomplet attend un peu ses voisins.
            await asyncio.sleep(self._batch_delay)

    async def _step(self) -> None:
        """Un tour : rattrapage si la connexion est neuve, sessions à relire, effacements, file ; puis le curseur."""
        if self._channel.connected:
            if self._resync:
                await self._catch_up()
            await self._reread_stale()
            await self._send_deletions()
            await self._drain()
        await self._flush()
        await self._reconcile()

    async def _catch_up(self) -> None:
        """Après une (re)connexion : relit ce que la console n'a pas acquitté (§ 6.3)."""
        self._resync = False
        self._in_flight.clear()
        self._deletions.clear()
        state = await self._cursor.load(self._tenant)
        if state is not None:
            # D'autres process ont pu avancer le curseur commun.
            for session, seq in state.acked.items():
                self._acked[session] = max(self._acked.get(session, 0), seq)
            self._deleted |= set(state.deleted)
        self._sent = dict(self._acked)
        records = {record.session_id: record for record in await self._store.sessions(self._tenant)}
        await self._note_deleted(session for session in self._acked if session not in records)
        cut = None if self._flushed_at is None else self._flushed_at - CATCH_UP_MARGIN
        for session, record in records.items():
            acked = self._acked.get(session)
            if acked is not None:
                sent = acked >= record.last_seq or await self._resend(session, after=acked)
            else:
                sent = cut is None or record.updated_at < cut or await self._resend(session, after=0, since=cut)
            if not sent:
                self._resync = True
                return
        self._stale.clear()
        # La file a pu prendre, pendant ce temps, des événements déjà relus : ``_drain`` les écarte.

    async def _reread_stale(self) -> None:
        """Relit les sessions que la file pleine a laissées de côté."""
        while self._stale and self._channel.connected:
            session = self._stale.pop()
            if not await self._resend(session, after=self._sent.get(session, self._acked.get(session, 0))):
                self._stale.add(session)
                return

    async def _resend(self, session: SessionId, *, after: int, since: datetime | None = None) -> bool:
        """Relit une session dans le journal après ``after`` et l'envoie ; faux si la connexion tombe."""
        events = [
            event
            for event in await self._store.read(self._tenant, session, after_seq=after)
            if event.type not in self._exclude and (since is None or event.ts >= since)
        ]
        if not events:
            return True
        if session not in self._acked:
            self._acked[session] = 0
            self._dirty.setdefault(session, 0)
        return await self._send(events)

    async def _drain(self) -> None:
        """Envoie ce que la file contient, sans ce qui est déjà parti ou à relire."""
        events: list[Event] = []
        while self._queue:
            event = self._queue.popleft()
            session = event.session_id
            if session in self._stale or event.seq <= self._sent.get(session, self._acked.get(session, 0)):
                continue
            events.append(event)
        if events and not await self._send(events):
            # La connexion est tombée : le rattrapage de la suivante relira ces sessions.
            self._resync = True

    async def _send(self, events: Sequence[Event]) -> bool:
        """Envoie des événements par lots ; faux si la connexion tombe en route."""
        for start in range(0, len(events), self._batch_size):
            chunk = events[start : start + self._batch_size]
            while len(self._in_flight) >= MAX_IN_FLIGHT:
                if not await self._wait_for_space():
                    return False
            positions: dict[SessionId, int] = {}
            for event in chunk:
                positions[event.session_id] = max(positions.get(event.session_id, 0), event.seq)
            batch = EventsPayload(
                batch_id=new_id(),
                events=tuple(public_event(event, mode=self._mode, redactor=self._redactor) for event in chunk),
            )
            if not await self._channel.send(batch):
                return False
            self._in_flight[batch.batch_id] = positions
            for session, seq in positions.items():
                self._sent[session] = max(self._sent.get(session, 0), seq)
        return True

    async def _wait_for_space(self) -> bool:
        """Attend qu'un lot en vol soit acquitté ; faux si la connexion tombe ou que l'attente dure."""
        self._space.clear()
        try:
            async with asyncio.timeout(ACK_WAIT):
                await self._space.wait()
        except TimeoutError:
            return self._channel.connected and len(self._in_flight) < MAX_IN_FLIGHT
        return self._channel.connected

    async def _send_deletions(self) -> None:
        """Dit à la console les sessions effacées, jusqu'à leur acquittement."""
        waiting = set(self._deletions.values())
        for session in sorted(self._deleted - waiting):
            message_id = new_id()
            if not await self._channel.send(SessionDeletedPayload(tenant=self._tenant, session=session), message_id=message_id):
                return
            self._deletions[message_id] = session

    async def _note_deleted(self, sessions: Iterable[SessionId]) -> None:
        """Des sessions du curseur absentes du journal : effacées par un autre process."""
        gone = [session for session in sessions if session not in self._deleted]
        if not gone:
            return
        for session in gone:
            self._forget_locally(session)
            self._deleted.add(session)
        await self._cursor.mark_deleted(self._tenant, gone)

    def _forget_locally(self, session: SessionId) -> None:
        self._acked.pop(session, None)
        self._dirty.pop(session, None)
        self._sent.pop(session, None)
        self._stale.discard(session)

    async def _flush(self, *, force: bool = False) -> None:
        """Écrit les positions acquittées dans le curseur, au plus toutes les ``FLUSH_INTERVAL`` secondes."""
        loop = asyncio.get_running_loop()
        if not self._dirty or (not force and loop.time() - self._last_flush < FLUSH_INTERVAL):
            return
        dirty, self._dirty = self._dirty, {}
        now = datetime.now(UTC)
        try:
            await self._cursor.advance(self._tenant, dirty, flushed_at=now)
        except BaseException:
            for session, seq in dirty.items():
                self._dirty[session] = max(self._dirty.get(session, 0), seq)
            raise
        self._flushed_at = now
        self._last_flush = loop.time()

    async def _reconcile(self) -> None:
        """De temps en temps, cherche les sessions effacées par un autre process (``loom sessions delete``)."""
        loop = asyncio.get_running_loop()
        if loop.time() - self._last_reconcile < RECONCILE_INTERVAL:
            return
        self._last_reconcile = loop.time()
        present = {record.session_id for record in await self._store.sessions(self._tenant)}
        await self._note_deleted(session for session in tuple(self._acked) if session not in present)
