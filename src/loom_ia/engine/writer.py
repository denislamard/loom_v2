# SPDX-License-Identifier: Apache-2.0
"""Écrivain d'une session : tous ses runs écrivent par lui (#4, #22).

Chaque écriture annonce le dernier ``seq`` qu'elle connaît (écriture
optimiste) : deux écrivains indépendants sur la même session se
contrediraient. Or un sous-agent écrit son run dans le journal de son parent,
pendant que le parent écrit le sien, et plusieurs sous-agents peuvent tourner
en parallèle. Ils partagent donc un seul écrivain : un verrou ordonne les
écritures, et le dernier ``seq`` est tenu à jour pour tous.

Deux runs d'une même session lancés en parallèle le partagent aussi, par le
registre ``SessionWriters`` de l'instance. Un autre process — un worker, un
second serveur — reste hors de portée du registre : son écriture fait avancer
le journal et la nôtre est refusée. L'écrivain lit alors ce qui a été écrit
depuis sa position. Si c'est la fin d'un run dont il écrit — arrêté, fini ou en
échec ailleurs —, il ne réécrit pas : ses brouillons parleraient d'un run que
le journal a clos, et il lève ``RunMoved`` (``drive`` s'arrête alors sur ce que
dit le journal ; un arrêt demandé trop tard rend « déjà fini »). Sinon — un
autre run de la session, ou notre run qui avance ailleurs pendant qu'on
l'arrête —, le conflit n'invalide rien : l'écrivain relit la position et
réécrit, un nombre borné de fois.

Ouvrir un run sous un identifiant choisi (``opens``) ne tolère pas la
concurrence : deux ``run.started`` rendraient le journal illisible. L'écrivain
vérifie sous son verrou que le run n'a aucun événement — un écrivain partagé
suit le journal, il ne verrait aucun conflit —, puis à chaque reprise : le lot
qui perd la course lève ``RunExists`` au lieu de se rejouer derrière le gagnant.

Une écriture peut aussi se donner une garde (``guard``) : à chaque reprise,
elle examine ce qui s'est écrit depuis notre position et peut renoncer en
levant. C'est ce que fait la concession d'un run : si un autre worker l'a prise
entre-temps, la rejouer derrière lui ferait deux pilotes pour un run.

Le pilote d'un run qui tient sa concession écrit par une vue de l'écrivain
(``held``), qui partage sa position et son verrou (#27). Toute écriture
reprise sur la session — la sienne, celle d'un autre run, celle de la
compaction — fait passer sous les yeux des pilotes ce qui s'est écrit depuis.
S'ils y voient un autre worker prendre la concession de leur run, ils l'ont
perdue : leur bail a expiré sans être renouvelé (store injoignable, process
gelé), et l'autre a repris le run. La vue refuse dès lors toute écriture
(``ClaimConflict``), sans rien écrire et même sans conflit de séquence : ses
brouillons partent d'un état que le journal a dépassé, et les écrire ferait
deux pilotes pour un run. De même s'ils y voient leur run clos ailleurs
(``RunMoved``) : sans cela, une écriture d'un autre run de la session, qui
avance la position commune par-dessus l'arrêt, laisserait le pilote écrire
après la clôture. Ce qui ne vient pas du pilote — un arrêt, une décision
d'approbation, la compaction — passe par l'écrivain lui-même et n'est pas
concerné.

La compaction écrit sans contrôle de séquence (``checked=False``, #23) : son
événement ne couvre que des événements anciens, et son worker ne partage aucun
écrivain avec les runs en cours.
"""

import asyncio
import logging
from collections.abc import Callable, Generator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Final, Self

from loom_ia.core.events import (
    Event,
    EventDraft,
    RunCancelled,
    RunClaimed,
    RunCompleted,
    RunFailed,
)
from loom_ia.core.model import RunClaim, RunId, SessionId, TenantId
from loom_ia.core.ports import EventStore, SequenceConflict

logger = logging.getLogger(__name__)

# Écritures refusées d'affilée avant d'abandonner : au-delà, ce n'est plus un
# croisement mais un journal écrit sans répit par ailleurs.
MAX_ATTEMPTS: Final = 5
# Écrivains gardés par le registre ; au-delà, le moins récemment utilisé est
# oublié (un run qui le tient encore continue de s'en servir).
MAX_WRITERS: Final = 256


# Ce qui clôt un run : écrit ailleurs, il rend nos brouillons sur ce run caducs.
_CLOSING: Final = (RunCompleted, RunFailed, RunCancelled)


class RunMoved(RuntimeError):
    """Un autre process a clos un run que cet écrivain écrivait : rien n'a été écrit."""

    def __init__(self, run_ids: frozenset[RunId], events: Sequence[Event]) -> None:
        said = ", ".join(f"{e.run_id} ({e.type}, seq {e.seq})" for e in events)
        super().__init__(f"Écriture abandonnée : clos ailleurs entre-temps — {said}")
        self.run_ids = run_ids
        self.events = tuple(events)


class ClaimConflict(RuntimeError):
    """Un autre pilote tient la concession de ce run (#27).

    Levée avant de piloter, si sa concession est vivante. Levée aussi en plein
    pilotage, par la vue du pilote (``SessionWriter.held``), dès qu'un autre
    worker l'a prise : il a pu la rendre depuis, ``lease_until`` est alors passé.
    """

    def __init__(self, run_id: RunId, worker_id: str, until: datetime) -> None:
        self.run_id = run_id
        self.worker_id = worker_id
        self.lease_until = until
        super().__init__(
            f"Run {run_id} : piloté par {worker_id}, concession valable jusqu'à "
            f"{until.isoformat(timespec='seconds')}"
        )


@dataclass(eq=False, slots=True)
class _Tenure:
    """Concession qu'un pilote de ce process tient sur un run, et ce qu'il en reste."""

    run_id: RunId
    worker_id: str
    # Dernière concession prise par un autre worker depuis la nôtre : la perte.
    taken: RunClaim | None = None
    # Clôture du run écrite ailleurs.
    closed: tuple[Event, ...] = ()

    @property
    def lost(self) -> bool:
        return self.taken is not None or bool(self.closed)

    def witness(self, landed: Sequence[Event]) -> None:
        """Note la prise du run par un autre worker (vivante ou rendue), ou sa clôture."""
        for event in landed:
            if event.run_id != self.run_id:
                continue
            payload = event.payload
            if isinstance(payload, RunClaimed) and payload.worker_id != self.worker_id:
                self.taken = RunClaim(worker_id=payload.worker_id, lease_until=payload.lease_until)
            elif isinstance(payload, _CLOSING):
                self.closed = (*self.closed, event)

    def check(self) -> None:
        """Lève ``RunMoved`` si le run a été clos ailleurs, ``ClaimConflict`` s'il a été pris."""
        if self.closed:
            raise RunMoved(frozenset({self.run_id}), self.closed)
        if self.taken is not None:
            raise ClaimConflict(self.run_id, self.taken.worker_id, self.taken.lease_until)


@dataclass(eq=False, slots=True)
class _Shared:
    """Ce que l'écrivain d'une session partage avec les vues de ses pilotes."""

    last_seq: int
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    # Concessions tenues par les pilotes en cours : chaque reprise les leur montre.
    tenures: set[_Tenure] = field(default_factory=set[_Tenure])


class RunExists(ValueError):
    """Le run à ouvrir a déjà des événements dans le journal : rien n'a été écrit.

    C'est une ``ValueError``, comme l'a toujours été « le run existe déjà ».
    ``session_id`` nomme la session qui le détient quand ce n'est pas celle
    demandée : un ``run_id`` choisi par l'appelant est unique chez son client.
    """

    def __init__(self, run_id: RunId, session_id: SessionId | None = None) -> None:
        where = "" if session_id is None else f" dans la session {session_id}"
        super().__init__(f"Le run {run_id} existe déjà{where}")
        self.run_id = run_id
        self.session_id = session_id


class SessionWriter:
    """Écritures ordonnées dans le journal d'une session."""

    def __init__(
        self, store: EventStore, tenant_id: TenantId, session_id: SessionId, last_seq: int
    ) -> None:
        self.store = store
        self.tenant_id = tenant_id
        self.session_id = session_id
        self._shared = _Shared(last_seq)
        # Concession du pilote pour une vue rendue par ``held`` ; ``None`` pour l'écrivain.
        self._tenure: _Tenure | None = None

    @property
    def last_seq(self) -> int:
        """Dernier ``seq`` de la session connu de l'écrivain et de ses vues."""
        return self._shared.last_seq

    @last_seq.setter
    def last_seq(self, seq: int) -> None:
        self._shared.last_seq = seq

    @classmethod
    async def open(cls, store: EventStore, tenant_id: TenantId, session_id: SessionId) -> Self:
        """Écrivain qui part du dernier événement de la session."""
        return cls(store, tenant_id, session_id, await store.last_seq(tenant_id, session_id))

    @contextmanager
    def held(self, run_id: RunId, worker_id: str) -> Generator[SessionWriter]:
        """Vue du pilote qui vient d'écrire la concession de ``run_id`` (#27).

        À ouvrir aussitôt la concession écrite, sans attente entre les deux :
        tout ce qui s'écrit ensuite sur la session passe alors sous ses yeux. La
        vue écrit comme l'écrivain, dont elle partage la position et le verrou.
        Mais dès qu'un autre worker a pris la concession — vivante ou déjà
        rendue —, elle refuse toute écriture en levant ``ClaimConflict``, sans
        rien écrire, et cela jusqu'au bout ; dès que le run a été clos ailleurs,
        de même avec ``RunMoved``. Le pilote, ses sous-runs et ses traces
        d'idempotence écrivent par elle.
        """
        tenure = _Tenure(run_id, worker_id)
        view = SessionWriter(self.store, self.tenant_id, self.session_id, self.last_seq)
        view._shared = self._shared
        view._tenure = tenure
        self._shared.tenures.add(tenure)
        try:
            yield view
        finally:
            self._shared.tenures.discard(tenure)

    async def append(
        self,
        drafts: Sequence[EventDraft],
        *,
        checked: bool = True,
        opens: RunId | None = None,
        guard: Callable[[Sequence[Event]], None] | None = None,
    ) -> list[Event]:
        """Écrit les brouillons à la suite, dans l'ordre, et les renvoie numérotés.

        Un conflit de séquence est repris : la position est relue chez le
        store, puis l'écriture rejouée telle quelle — sauf si un run dont on
        écrit a été clos entre-temps, ce que ``RunMoved`` dit.

        ``opens`` désigne le run que le lot ouvre : s'il a déjà des événements,
        avant l'écriture ou entre deux reprises, ``RunExists`` est levée et
        rien n'est écrit.

        ``guard`` reçoit, avant chaque reprise, les événements écrits depuis
        la position de l'écrivain ; ce qu'elle lève sort tel quel, et rien
        n'est écrit.

        Une vue de pilote (``held``) dont la concession a été prise lève
        ``ClaimConflict``, et dont le run a été clos ailleurs ``RunMoved`` :
        elle n'écrit rien.
        """
        batch = list(drafts)
        async with self._shared.lock:
            self._still_held()
            if opens is not None and await self.store.read(
                self.tenant_id, self.session_id, run_id=opens
            ):
                raise RunExists(opens)
            attempt = 0
            while True:
                attempt += 1
                try:
                    events = await self.store.append(
                        batch, expected_seq=self.last_seq if checked else None
                    )
                except SequenceConflict as conflict:
                    if attempt >= MAX_ATTEMPTS:
                        raise
                    await self._still_open(batch, conflict.actual, opens, guard)
                    logger.debug(
                        "Journal %s : écriture reprise (seq %d → %d, tentative %d)",
                        self.session_id,
                        self.last_seq,
                        conflict.actual,
                        attempt,
                    )
                    self.last_seq = conflict.actual
                else:
                    if events:
                        await self._passed_over(events[0].seq)
                        self.last_seq = events[-1].seq
                    return events

    async def _still_open(
        self,
        batch: Sequence[EventDraft],
        actual: int,
        opens: RunId | None,
        guard: Callable[[Sequence[Event]], None] | None,
    ) -> None:
        """Lève ``RunExists`` si le run à ouvrir l'est déjà, ``RunMoved`` si un run est clos.

        Puis, pour une vue de pilote, ``RunMoved`` si son run a été clos ailleurs
        et ``ClaimConflict`` s'il a été pris ; sinon ``guard``, s'il y en a une,
        examine ce qui s'est écrit depuis. Ce qui s'est écrit passe d'abord sous
        les yeux des pilotes de la session.
        """
        ours = frozenset(draft.run_id for draft in batch)
        landed = await self.store.read(self.tenant_id, self.session_id, after_seq=self.last_seq)
        self._witness(landed)
        seen = max([actual, *(e.seq for e in landed)])
        if opens is not None and any(e.run_id == opens for e in landed):
            self.last_seq = seen
            raise RunExists(opens)
        closed = [e for e in landed if e.run_id in ours and isinstance(e.payload, _CLOSING)]
        if closed:
            self.last_seq = seen
            raise RunMoved(ours, closed)
        if self._tenure is not None and self._tenure.lost:
            self.last_seq = seen
            self._tenure.check()
        if guard is not None:
            try:
                guard(landed)
            except Exception:
                self.last_seq = seen
                raise

    async def _passed_over(self, first: int) -> None:
        """Ce qu'une écriture sans contrôle de séquence a sauté passe sous les yeux des pilotes.

        Sans cela, la position avancerait par-dessus la prise d'un run par un
        autre worker, ou sa clôture, et le pilote de ce run écrirait sans conflit.
        """
        if first <= self.last_seq + 1 or not self._shared.tenures:
            return
        landed = await self.store.read(self.tenant_id, self.session_id, after_seq=self.last_seq)
        self._witness([e for e in landed if e.seq < first])

    def _witness(self, landed: Sequence[Event]) -> None:
        for tenure in self._shared.tenures:
            tenure.witness(landed)

    def _still_held(self) -> None:
        """Lève ``RunMoved`` ou ``ClaimConflict`` si cette vue de pilote a perdu son run."""
        if self._tenure is not None:
            self._tenure.check()

    def __repr__(self) -> str:
        return f"SessionWriter({self.tenant_id}/{self.session_id}, seq {self.last_seq})"


class SessionWriters:
    """Registre des écrivains d'une instance : un par session."""

    def __init__(self, max_writers: int = MAX_WRITERS) -> None:
        self._writers: dict[tuple[TenantId, SessionId], SessionWriter] = {}
        self._lock = asyncio.Lock()
        self._max = max_writers

    async def open(
        self, store: EventStore, tenant_id: TenantId, session_id: SessionId
    ) -> SessionWriter:
        """Écrivain de cette session, créé au premier appel."""
        key = (tenant_id, session_id)
        async with self._lock:
            writer = self._writers.pop(key, None)
            if writer is None or writer.store is not store:
                writer = await SessionWriter.open(store, tenant_id, session_id)
            self._writers[key] = writer
            while len(self._writers) > self._max:
                self._writers.pop(next(iter(self._writers)))
            return writer

    def forget(self, tenant_id: TenantId, session_id: SessionId) -> None:
        """Oublie l'écrivain d'une session supprimée (F7)."""
        self._writers.pop((tenant_id, session_id), None)

    def clear(self) -> None:
        self._writers.clear()

    def __len__(self) -> int:
        return len(self._writers)

    def __repr__(self) -> str:
        return f"SessionWriters({len(self._writers)} session(s))"
