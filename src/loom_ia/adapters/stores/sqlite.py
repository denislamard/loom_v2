# SPDX-License-Identifier: Apache-2.0
"""Journal d'événements en SQLite : mode service sur une machine (#22).

Une table ``events`` par base : colonnes pour l'enveloppe et index composés,
dont ``(tenant_id, session_id, seq)`` qui est la clé primaire. L'événement
entier est rangé en JSON dans ``event`` : le relire le reconstruit tel quel,
sans avoir à recomposer l'enveloppe. Les facettes vivent à part, en JSON, et
se filtrent par ``json_extract``.

Le contrôle de séquence et l'écriture tiennent dans une transaction
``BEGIN IMMEDIATE`` : deux process qui écrivent dans la même base ne peuvent
pas se croiser. Le journal SQLite est en WAL et les commits sont synchrones,
car chaque événement sert de point de reprise.

Une requête est d'abord réduite en SQL sur les colonnes indexées et les
facettes, puis repassée par ``EventQuery.select`` : c'est lui qui fait foi.
"""

import asyncio
import json
import os
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, Final

import aiosqlite

from loom_ia.adapters.stores.codec import PLAIN, JournalCodec
from loom_ia.core.events import Event, EventDraft, EventQuery
from loom_ia.core.model import RunId, SessionId, TenantId
from loom_ia.core.ports import SequenceConflict, SessionRecord, journal_key

SCHEMA: Final = """
CREATE TABLE IF NOT EXISTS events (
    tenant_id   TEXT    NOT NULL,
    session_id  TEXT    NOT NULL,
    seq         INTEGER NOT NULL,
    event_id    TEXT    NOT NULL,
    ts          TEXT    NOT NULL,
    run_id      TEXT    NOT NULL,
    root_run_id TEXT    NOT NULL,
    type        TEXT    NOT NULL,
    category    TEXT    NOT NULL,
    status      TEXT    NOT NULL,
    agent       TEXT,
    role        TEXT,
    facets      TEXT    NOT NULL,
    event       TEXT    NOT NULL,
    PRIMARY KEY (tenant_id, session_id, seq)
);
CREATE INDEX IF NOT EXISTS events_run ON events (tenant_id, run_id, seq);
CREATE INDEX IF NOT EXISTS events_event_id ON events (tenant_id, event_id);
CREATE INDEX IF NOT EXISTS events_type ON events (tenant_id, type, ts);
"""

_INSERT: Final = (
    "INSERT INTO events (tenant_id, session_id, seq, event_id, ts, run_id, root_run_id,"
    " type, category, status, agent, role, facets, event)"
    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)


def _row(event: Event, codec: JournalCodec) -> tuple[Any, ...]:
    return (
        event.tenant_id,
        event.session_id,
        event.seq,
        event.event_id,
        event.ts.isoformat(),
        event.run_id,
        event.root_run_id,
        event.type,
        event.category,
        event.status,
        event.agent,
        event.role,
        json.dumps(event.facets, ensure_ascii=False),
        codec.dumps(event),
    )


def _conditions(query: EventQuery) -> tuple[str, list[Any]]:
    """Clauses SQL qui réduisent la recherche ; ``select`` tranche ensuite."""
    clauses = ["tenant_id = ?"]
    values: list[Any] = [query.tenant_id]
    simple: list[tuple[str, Any]] = [
        ("session_id", query.session_id),
        ("run_id", query.run_id),
        ("agent", query.agent),
        ("role", query.role),
    ]
    for column, value in simple:
        if value is not None:
            clauses.append(f"{column} = ?")
            values.append(value)
    for column, choices in (
        ("type", query.types),
        ("category", query.categories),
        ("status", query.status),
    ):
        if choices:
            clauses.append(f"{column} IN ({', '.join('?' * len(choices))})")
            values.extend(choices)
    facets = dict(query.facets)
    if query.tool_name is not None:
        facets["tool_name"] = query.tool_name
    if query.model_id is not None:
        facets["model_id"] = query.model_id
    for name, value in facets.items():
        clauses.append("json_extract(facets, ?) IS ?")
        values.extend([f"$.{name}", value])
    if query.since is not None:
        clauses.append("ts >= ?")
        values.append(query.since.isoformat())
    if query.until is not None:
        clauses.append("ts < ?")
        values.append(query.until.isoformat())
    if query.after is not None:
        clauses.append("event_id > ?")
        values.append(query.after)
    return " AND ".join(clauses), values


class SqliteEventStore:
    """Journal durable dans un fichier SQLite."""

    def __init__(self, path: str | os.PathLike[str], *, codec: JournalCodec = PLAIN) -> None:
        self.path = Path(path)
        self._codec = codec
        self._connection: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    async def _connect(self) -> aiosqlite.Connection:
        if self._connection is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # ``isolation_level=None`` : les transactions sont ouvertes à la main.
            connection = await aiosqlite.connect(self.path, isolation_level=None)
            await connection.execute("PRAGMA journal_mode = WAL")
            await connection.execute("PRAGMA synchronous = FULL")
            await connection.execute("PRAGMA busy_timeout = 5000")
            await connection.executescript(SCHEMA)
            self._connection = connection
        return self._connection

    # --- Écriture ---------------------------------------------------------

    async def append(
        self, drafts: Sequence[EventDraft], *, expected_seq: int | None
    ) -> list[Event]:
        if not drafts:
            return []
        tenant_id, session_id = journal_key(drafts)
        async with self._lock:
            connection = await self._connect()
            await connection.execute("BEGIN IMMEDIATE")
            try:
                last = await self._last_seq(connection, tenant_id, session_id)
                if expected_seq is not None and expected_seq != last:
                    raise SequenceConflict(session_id, expected_seq, last)
                events = [draft.to_event(last + i) for i, draft in enumerate(drafts, start=1)]
                await connection.executemany(
                    _INSERT, [_row(event, self._codec) for event in events]
                )
            except BaseException:
                await connection.execute("ROLLBACK")
                raise
            await connection.execute("COMMIT")
            return events

    # --- Lecture ----------------------------------------------------------

    async def read(
        self,
        tenant_id: TenantId,
        session_id: SessionId,
        *,
        after_seq: int = 0,
        run_id: RunId | None = None,
    ) -> list[Event]:
        sql = (
            "SELECT event FROM events WHERE tenant_id = ? AND session_id = ? AND seq > ?"
            f"{' AND run_id = ?' if run_id is not None else ''} ORDER BY seq"
        )
        values: list[Any] = [tenant_id, session_id, after_seq]
        if run_id is not None:
            values.append(run_id)
        return await self._events(sql, values)

    async def query(self, query: EventQuery) -> list[Event]:
        where, values = _conditions(query)
        sql = f"SELECT event FROM events WHERE {where} ORDER BY event_id LIMIT ?"
        return query.select(await self._events(sql, [*values, query.limit]))

    async def _events(self, sql: str, values: Sequence[Any]) -> list[Event]:
        async with self._lock:
            connection = await self._connect()
            cursor = await connection.execute(sql, tuple(values))
            try:
                rows = await cursor.fetchall()
            finally:
                await cursor.close()
        return [self._codec.loads(str(row[0])) for row in rows]

    async def last_seq(self, tenant_id: TenantId, session_id: SessionId) -> int:
        async with self._lock:
            connection = await self._connect()
            return await self._last_seq(connection, tenant_id, session_id)

    async def _last_seq(
        self, connection: aiosqlite.Connection, tenant_id: TenantId, session_id: SessionId
    ) -> int:
        cursor = await connection.execute(
            "SELECT COALESCE(MAX(seq), 0) FROM events WHERE tenant_id = ? AND session_id = ?",
            (tenant_id, session_id),
        )
        try:
            row = await cursor.fetchone()
        finally:
            await cursor.close()
        return int(row[0]) if row is not None else 0

    # --- Sessions (F7) ----------------------------------------------------

    async def sessions(self, tenant_id: TenantId) -> list[SessionRecord]:
        async with self._lock:
            connection = await self._connect()
            cursor = await connection.execute(
                "SELECT session_id, MAX(seq), MAX(ts) FROM events WHERE tenant_id = ?"
                " GROUP BY session_id ORDER BY MAX(ts) DESC",
                (tenant_id,),
            )
            try:
                rows = await cursor.fetchall()
            finally:
                await cursor.close()
        return [
            SessionRecord(
                session_id=SessionId(str(row[0])),
                last_seq=int(row[1]),
                updated_at=datetime.fromisoformat(str(row[2])),
            )
            for row in rows
        ]

    async def delete(self, tenant_id: TenantId, session_id: SessionId) -> int:
        async with self._lock:
            connection = await self._connect()
            cursor = await connection.execute(
                "DELETE FROM events WHERE tenant_id = ? AND session_id = ?",
                (tenant_id, session_id),
            )
            removed = cursor.rowcount
            await cursor.close()
        return max(removed, 0)

    async def aclose(self) -> None:
        async with self._lock:
            if self._connection is not None:
                await self._connection.close()
                self._connection = None

    def __repr__(self) -> str:
        return f"SqliteEventStore({str(self.path)!r})"
