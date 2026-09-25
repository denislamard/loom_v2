# SPDX-License-Identifier: Apache-2.0
"""Journal d'événements en Postgres : mode service, plusieurs process (F5, #5).

Même table, mêmes colonnes et mêmes index qu'en SQLite — l'événement entier
rangé en texte dans ``event``, les facettes à part en ``jsonb`` — à trois
différences près, qui sont tout l'intérêt de Postgres ici.

**La politique de lignes fait barrière.** Chaque transaction pose le client
courant ; la politique du DDL ne laisse voir et n'accepte d'écrire que ses
lignes. Le ``WHERE tenant_id = $1`` de chaque requête reste écrit, mais il
n'est plus ce qui protège : s'il était oublié, la requête ne rendrait pas le
journal d'un autre, elle rendrait le même résultat. C'est ce que vérifie
``test_postgres.py`` en l'oubliant exprès.

**Le journal ne se modifie pas.** Le rôle applicatif n'a pas ``UPDATE`` sur
la table : un événement écrit ne peut plus changer, même par une faute de
code. ``DELETE`` reste accordé, c'est l'effacement RGPD d'une session.

**L'ordre d'écriture est arbitré par la base.** Le contrôle de séquence et
l'insertion tiennent dans une transaction ouverte par un verrou consultatif
sur le journal visé : deux process qui écrivent dans la même session
attendent leur tour plutôt que de s'entrelacer. La clé primaire
``(tenant_id, session_id, seq)`` reste le dernier mot — une violation
d'unicité devient un ``SequenceConflict``, comme un ``expected_seq`` périmé.
"""

import json
from collections.abc import Sequence
from typing import Any, Final

import asyncpg

from loom_ia.adapters.postgres.pool import Held, PostgresPool, rows_touched
from loom_ia.adapters.postgres.sql import DEFAULT_ROLE, EVENTS_TABLE, ddl
from loom_ia.adapters.stores.codec import PLAIN, JournalCodec
from loom_ia.core.events import Event, EventDraft, EventQuery
from loom_ia.core.model import RunId, SessionId, TenantId
from loom_ia.core.ports import SequenceConflict, SessionRecord, journal_key

_COLUMNS: Final = (
    "tenant_id, session_id, seq, event_id, ts, run_id, root_run_id,"
    " type, category, status, agent, role, facets, event"
)
_INSERT: Final = (
    f"INSERT INTO {EVENTS_TABLE} ({_COLUMNS})"
    " VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13::jsonb, $14)"
)
# Un verrou par journal, tenu jusqu'à la fin de la transaction. ``hashtext``
# peut faire se rencontrer deux journaux : ils s'attendent, c'est tout.
_LOCK: Final = "SELECT pg_advisory_xact_lock(hashtext($1)::bigint)"
_LAST_SEQ: Final = (
    f"SELECT COALESCE(MAX(seq), 0) FROM {EVENTS_TABLE} WHERE tenant_id = $1 AND session_id = $2"
)


def _row(event: Event, codec: JournalCodec) -> tuple[Any, ...]:
    return (
        event.tenant_id,
        event.session_id,
        event.seq,
        event.event_id,
        event.ts,
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


class _Conditions:
    """Clauses SQL numérotées ; ``EventQuery.select`` tranche ensuite."""

    def __init__(self) -> None:
        self.clauses: list[str] = []
        self.values: list[Any] = []

    def add(self, clause: str, value: Any) -> None:
        self.values.append(value)
        self.clauses.append(clause.format(n=len(self.values)))

    @property
    def where(self) -> str:
        return " AND ".join(self.clauses)


def _conditions(query: EventQuery) -> _Conditions:
    found = _Conditions()
    found.add("tenant_id = ${n}", query.tenant_id)
    simple: list[tuple[str, Any]] = [
        ("session_id", query.session_id),
        ("run_id", query.run_id),
        ("agent", query.agent),
        ("role", query.role),
    ]
    for column, value in simple:
        if value is not None:
            found.add(f"{column} = ${{n}}", value)
    for column, choices in (
        ("type", query.types),
        ("category", query.categories),
        ("status", query.status),
    ):
        if choices:
            found.add(f"{column} = ANY(${{n}}::text[])", list(choices))
    facets: dict[str, Any] = dict(query.facets)
    if query.tool_name is not None:
        facets["tool_name"] = query.tool_name
    if query.model_id is not None:
        facets["model_id"] = query.model_id
    if facets:
        # Contenance : la facette est présente **et** vaut cette valeur.
        found.add("facets @> ${n}::jsonb", json.dumps(facets, ensure_ascii=False))
    if query.since is not None:
        found.add("ts >= ${n}", query.since)
    if query.until is not None:
        found.add("ts < ${n}", query.until)
    if query.after is not None:
        found.add("event_id > ${n}", query.after)
    return found


class PostgresEventStore:
    """Journal durable dans une base Postgres, sous politique de lignes."""

    def __init__(
        self,
        dsn: str,
        *,
        role: str | None = DEFAULT_ROLE,
        codec: JournalCodec = PLAIN,
    ) -> None:
        self._codec = codec
        self._pg = PostgresPool(
            dsn,
            table=EVENTS_TABLE,
            ddl=ddl(role=role, idempotency=False),
            role=role,
        )

    def __repr__(self) -> str:
        return f"PostgresEventStore({EVENTS_TABLE!r})"

    # --- Écriture ---------------------------------------------------------

    async def append(
        self, drafts: Sequence[EventDraft], *, expected_seq: int | None
    ) -> list[Event]:
        if not drafts:
            return []
        tenant_id, session_id = journal_key(drafts)
        async with self._pg.transaction(tenant_id) as connection:
            await connection.execute(_LOCK, f"{tenant_id}/{session_id}")
            last = await self._last_seq(connection, tenant_id, session_id)
            if expected_seq is not None and expected_seq != last:
                raise SequenceConflict(session_id, expected_seq, last)
            events = [draft.to_event(last + i) for i, draft in enumerate(drafts, start=1)]
            try:
                await connection.executemany(
                    _INSERT, [_row(event, self._codec) for event in events]
                )
            except asyncpg.UniqueViolationError as exc:
                # Deux écrivains ont franchi le verrou : la clé primaire a
                # tranché. L'appelant relit et rejoue, comme sur conflit.
                raise SequenceConflict(session_id, last, last + len(events)) from exc
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
            f"SELECT event FROM {EVENTS_TABLE}"
            " WHERE tenant_id = $1 AND session_id = $2 AND seq > $3"
            f"{' AND run_id = $4' if run_id is not None else ''} ORDER BY seq"
        )
        values: list[Any] = [tenant_id, session_id, after_seq]
        if run_id is not None:
            values.append(run_id)
        return await self._events(tenant_id, sql, values)

    async def query(self, query: EventQuery) -> list[Event]:
        found = _conditions(query)
        sql = (
            f"SELECT event FROM {EVENTS_TABLE} WHERE {found.where}"
            f" ORDER BY event_id LIMIT ${len(found.values) + 1}"
        )
        events = await self._events(query.tenant_id, sql, [*found.values, query.limit])
        return query.select(events)

    async def _events(self, tenant_id: TenantId, sql: str, values: Sequence[Any]) -> list[Event]:
        async with self._pg.transaction(tenant_id) as connection:
            rows = await connection.fetch(sql, *values)
        return [self._codec.loads(str(row["event"])) for row in rows]

    async def last_seq(self, tenant_id: TenantId, session_id: SessionId) -> int:
        async with self._pg.transaction(tenant_id) as connection:
            return await self._last_seq(connection, tenant_id, session_id)

    async def _last_seq(self, connection: Held, tenant_id: TenantId, session_id: SessionId) -> int:
        found = await connection.fetchval(_LAST_SEQ, tenant_id, session_id)
        return int(found or 0)

    # --- Sessions (F7) ----------------------------------------------------

    async def sessions(self, tenant_id: TenantId) -> list[SessionRecord]:
        async with self._pg.transaction(tenant_id) as connection:
            rows = await connection.fetch(
                f"SELECT session_id, MAX(seq) AS last_seq, MAX(ts) AS updated_at"
                f" FROM {EVENTS_TABLE} WHERE tenant_id = $1"
                " GROUP BY session_id ORDER BY MAX(ts) DESC",
                tenant_id,
            )
        return [
            SessionRecord(
                session_id=SessionId(str(row["session_id"])),
                last_seq=int(row["last_seq"]),
                updated_at=row["updated_at"],
            )
            for row in rows
        ]

    async def delete(self, tenant_id: TenantId, session_id: SessionId) -> int:
        async with self._pg.transaction(tenant_id) as connection:
            status = await connection.execute(
                f"DELETE FROM {EVENTS_TABLE} WHERE tenant_id = $1 AND session_id = $2",
                tenant_id,
                session_id,
            )
        return rows_touched(status)

    async def aclose(self) -> None:
        await self._pg.aclose()
