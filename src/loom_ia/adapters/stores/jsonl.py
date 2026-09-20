# SPDX-License-Identifier: Apache-2.0
"""Journal d'événements en fichiers JSONL : mode librairie et dev (#22).

Un fichier par session : ``<racine>/<client>/<session>.jsonl``, un événement
par ligne. Plusieurs process peuvent écrire dans le même journal : chaque
écriture prend un verrou exclusif ``flock`` (POSIX : Linux, macOS) et se
termine par un ``fsync``, car chaque événement sert de point de reprise.

Plantage en cours d'écriture : la dernière ligne peut rester incomplète.
Elle est ignorée à la lecture ; à l'écriture suivante, elle est déplacée
dans ``<session>.jsonl.corrupt`` puis retirée du journal. Une ligne
complète illisible lève ``JournalCorrupted``.
"""

import asyncio
import fcntl
import logging
import os
import re
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import BinaryIO, Final

from pydantic import ValidationError

from loom_ia.core.events import Event, EventDraft, EventQuery
from loom_ia.core.model import RunId, SessionId, TenantId
from loom_ia.core.ports import JournalCorrupted, SequenceConflict, SessionRecord, journal_key

logger = logging.getLogger(__name__)

# Identifiants utilisables comme nom de fichier (pas de chemin, pas de « .. »).
_SAFE_COMPONENT: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
SUFFIX: Final = ".jsonl"
CORRUPT_SUFFIX: Final = ".corrupt"
# Fin de fichier lue pour connaître le dernier événement sans relire tout le
# journal ; au-delà (un événement plus gros que ça), on relit le fichier.
TAIL_BYTES: Final = 64 * 1024


def _component(value: str, kind: str) -> str:
    if not _SAFE_COMPONENT.fullmatch(value):
        raise ValueError(f"{kind} inutilisable comme nom de fichier : {value!r}")
    return value


def _parse(path: Path, content: bytes) -> list[Event]:
    """Événements des lignes complètes ; la ligne finale incomplète est ignorée."""
    lines = content.split(b"\n")
    if lines[-1]:
        logger.warning("%s : ligne finale incomplète ignorée (écriture interrompue)", path)
    events: list[Event] = []
    for number, line in enumerate(lines[:-1], start=1):
        try:
            events.append(Event.model_validate_json(line))
        except ValidationError as exc:
            raise JournalCorrupted(f"{path}, ligne {number} : {exc}") from exc
    return events


def _tail(path: Path, size: int) -> bytes:
    """Fin du fichier, sous verrou partagé."""
    try:
        with path.open("rb") as fh:
            fcntl.flock(fh, fcntl.LOCK_SH)
            try:
                fh.seek(max(0, os.fstat(fh.fileno()).st_size - size))
                return fh.read()
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)
    except FileNotFoundError:
        return b""


def _last_parsed(content: bytes) -> Event | None:
    """Dernier événement lisible : la ligne finale peut être incomplète, la
    première tronquée par une lecture partielle."""
    for line in reversed([line for line in content.split(b"\n") if line]):
        try:
            return Event.model_validate_json(line)
        except ValidationError:
            continue
    return None


def _locked_read(path: Path) -> bytes:
    try:
        with path.open("rb") as fh:
            fcntl.flock(fh, fcntl.LOCK_SH)
            try:
                return fh.read()
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)
    except FileNotFoundError:
        return b""


class JsonlEventStore:
    def __init__(self, root: str | os.PathLike[str]) -> None:
        self._root = Path(root)
        # Dernier seq connu par fichier, valable tant que la taille n'a pas changé.
        self._tails: dict[Path, tuple[int, int]] = {}
        self._tails_lock = threading.Lock()

    def path(self, tenant_id: TenantId, session_id: SessionId) -> Path:
        tenant = _component(tenant_id, "Client")
        session = _component(session_id, "Session")
        return self._root / tenant / f"{session}{SUFFIX}"

    # --- Écriture ---------------------------------------------------------

    async def append(
        self, drafts: Sequence[EventDraft], *, expected_seq: int | None
    ) -> list[Event]:
        if not drafts:
            return []
        tenant_id, session_id = journal_key(drafts)
        path = self.path(tenant_id, session_id)
        return await asyncio.to_thread(
            self._append_sync, path, session_id, list(drafts), expected_seq
        )

    def _append_sync(
        self,
        path: Path,
        session_id: SessionId,
        drafts: list[EventDraft],
        expected_seq: int | None,
    ) -> list[Event]:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a+b") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                last = self._last_seq_locked(path, fh)
                if expected_seq is not None and expected_seq != last:
                    raise SequenceConflict(session_id, expected_seq, last)
                events = [draft.to_event(last + i) for i, draft in enumerate(drafts, start=1)]
                fh.write(b"".join(e.model_dump_json().encode() + b"\n" for e in events))
                fh.flush()
                os.fsync(fh.fileno())
                self._remember(path, os.fstat(fh.fileno()).st_size, events[-1].seq)
                return events
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)

    def _last_seq_locked(self, path: Path, fh: BinaryIO) -> int:
        """Dernier seq, sous verrou exclusif ; répare une ligne finale incomplète."""
        size = os.fstat(fh.fileno()).st_size
        with self._tails_lock:
            cached = self._tails.get(path)
        if cached is not None and cached[0] == size:
            return cached[1]

        fh.seek(0)
        content = fh.read()
        complete = content.rfind(b"\n") + 1
        if complete < len(content):
            corrupt = path.with_name(path.name + CORRUPT_SUFFIX)
            with corrupt.open("ab") as out:
                out.write(content[complete:] + b"\n")
            os.ftruncate(fh.fileno(), complete)
            logger.warning("%s : ligne finale incomplète déplacée dans %s", path, corrupt.name)
            content = content[:complete]

        events = _parse(path, content[content.rfind(b"\n", 0, -1) + 1 :]) if content else []
        last = events[-1].seq if events else 0
        self._remember(path, len(content), last)
        return last

    def _remember(self, path: Path, size: int, last_seq: int) -> None:
        with self._tails_lock:
            self._tails[path] = (size, last_seq)

    # --- Lecture ----------------------------------------------------------

    async def read(
        self,
        tenant_id: TenantId,
        session_id: SessionId,
        *,
        after_seq: int = 0,
        run_id: RunId | None = None,
    ) -> list[Event]:
        path = self.path(tenant_id, session_id)
        events = await asyncio.to_thread(self._read_sync, path)
        return [e for e in events if e.seq > after_seq and (run_id is None or e.run_id == run_id)]

    def _read_sync(self, path: Path) -> list[Event]:
        return _parse(path, _locked_read(path))

    async def query(self, query: EventQuery) -> list[Event]:
        return await asyncio.to_thread(self._query_sync, query)

    def _query_sync(self, query: EventQuery) -> list[Event]:
        if query.session_id is not None:
            paths = [self.path(query.tenant_id, query.session_id)]
        else:
            tenant_dir = self._root / _component(query.tenant_id, "Client")
            paths = sorted(tenant_dir.glob(f"*{SUFFIX}")) if tenant_dir.is_dir() else []
        candidates = [event for path in paths for event in self._read_sync(path)]
        return query.select(candidates)

    async def last_seq(self, tenant_id: TenantId, session_id: SessionId) -> int:
        events = await self.read(tenant_id, session_id)
        return events[-1].seq if events else 0

    # --- Sessions (F7) ----------------------------------------------------

    async def sessions(self, tenant_id: TenantId) -> list[SessionRecord]:
        return await asyncio.to_thread(self._sessions_sync, tenant_id)

    def _sessions_sync(self, tenant_id: TenantId) -> list[SessionRecord]:
        tenant_dir = self._root / _component(tenant_id, "Client")
        if not tenant_dir.is_dir():
            return []
        records: list[SessionRecord] = []
        for path in sorted(tenant_dir.glob(f"*{SUFFIX}")):
            last = self._last_event(path)
            if last is not None:
                records.append(
                    SessionRecord(session_id=last.session_id, last_seq=last.seq, updated_at=last.ts)
                )
        records.sort(key=lambda record: record.updated_at, reverse=True)
        return records

    def _last_event(self, path: Path) -> Event | None:
        """Dernier événement du journal, lu par la fin du fichier."""
        tail = _tail(path, TAIL_BYTES)
        found = _last_parsed(tail)
        if found is None and len(tail) >= TAIL_BYTES:
            # Un événement plus gros que la fenêtre : on relit tout le journal.
            found = _last_parsed(_locked_read(path))
        return found

    async def delete(self, tenant_id: TenantId, session_id: SessionId) -> int:
        path = self.path(tenant_id, session_id)
        return await asyncio.to_thread(self._delete_sync, path)

    def _delete_sync(self, path: Path) -> int:
        """Supprime le fichier de la session, ligne incomplète mise de côté comprise."""
        count = _locked_read(path).count(b"\n")
        path.unlink(missing_ok=True)
        path.with_name(path.name + CORRUPT_SUFFIX).unlink(missing_ok=True)
        with self._tails_lock:
            self._tails.pop(path, None)
        return count

    async def aclose(self) -> None:
        return None
