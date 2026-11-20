# SPDX-License-Identifier: Apache-2.0
"""Le curseur de la console : jusqu'où chaque session est acquittée (docs/analyse-exortateur-loom.md, § 6.2).

Le curseur suit le journal : il est rangé **avec lui**, dans le stockage de
``storage.events`` (celui du client de la console s'il a le sien). Restaurer le
journal restaure le curseur qui va avec, et plusieurs process ou machines sur un
même journal partagent un seul curseur.

Il ne porte que des positions : par session, le dernier ``seq`` que la console a
acquitté et un drapeau « effacée » en attente d'acquittement ; pour le client,
``flushed_at``, l'instant de la dernière écriture, d'où part le rattrapage des
sessions qu'un arrêt brutal aurait fait oublier (§ 6.3). Aucun contenu.

Une position ne recule jamais : deux écrivains se fusionnent, session par
session, au plus grand ``seq`` (en fichier comme en SQL).

=========  ====================================================================
Journal    Curseur
=========  ====================================================================
memory     en mémoire, comme le journal
jsonl      ``<path>/<client>/.console-cursor.json``, que le journal ne lit pas
           (il ne lit que ``*.jsonl``) ; verrou ``flock`` sur le dossier
sqlite     table ``console_cursor`` dans le même fichier
postgres   table ``loom_console_cursor`` dans la même base, sous la même
           politique de lignes que le journal
=========  ====================================================================
"""

import asyncio
import fcntl
import json
import os
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, Protocol, cast

from loom_ia.core.model import SessionId, TenantId, new_id

if TYPE_CHECKING:
    import aiosqlite

    from loom_ia.adapters.postgres.pool import PoolLimits, PostgresPool

CURSOR_FILE: Final = ".console-cursor.json"
"""Nom du curseur, dans le dossier d'un client d'un journal JSONL."""

SQLITE_TABLE: Final = "console_cursor"
"""Table du curseur dans un journal SQLite."""

_META: Final = ""
"""Clé de la ligne qui porte ``flushed_at`` : une session n'a jamais d'identifiant vide."""


@dataclass(frozen=True, slots=True)
class CursorState:
    """Ce que le curseur sait d'un client.

    Attributes:
        acked: Dernier ``seq`` acquitté, par session.
        deleted: Sessions effacées chez loom dont l'effacement n'est pas encore
            acquitté par la console.
        flushed_at: Dernière écriture du curseur.
    """

    acked: Mapping[SessionId, int] = field(default_factory=dict[SessionId, int])
    deleted: frozenset[SessionId] = frozenset()
    flushed_at: datetime | None = None


class ConsoleCursor(Protocol):
    """Les positions acquittées par la console, rangées avec le journal."""

    async def load(self, tenant: TenantId) -> CursorState | None:
        """Ce qui est rangé pour ce client ; ``None`` s'il n'a jamais été écrit (premier démarrage)."""
        ...

    async def advance(self, tenant: TenantId, acked: Mapping[SessionId, int], *, flushed_at: datetime) -> None:
        """Avance les positions, sans jamais les faire reculer, et note l'instant de l'écriture."""
        ...

    async def mark_deleted(self, tenant: TenantId, sessions: Iterable[SessionId]) -> None:
        """Note des sessions effacées, à dire à la console jusqu'à son acquittement."""
        ...

    async def forget(self, tenant: TenantId, sessions: Iterable[SessionId]) -> None:
        """Retire des sessions du curseur : leur effacement est acquitté."""
        ...

    async def aclose(self) -> None:
        """Libère les ressources (connexions)."""
        ...


def _merged(state: CursorState | None, acked: Mapping[SessionId, int], flushed_at: datetime) -> CursorState:
    """L'état, avancé au plus grand ``seq`` session par session."""
    base = state or CursorState()
    positions = dict(base.acked)
    for session, seq in acked.items():
        positions[session] = max(positions.get(session, 0), seq)
    return CursorState(acked=positions, deleted=base.deleted, flushed_at=flushed_at)


# --- Mémoire --------------------------------------------------------------------------


class InMemoryCursor:
    """Curseur d'un journal en mémoire : il ne survit pas au process, comme le journal."""

    def __init__(self) -> None:
        self._states: dict[TenantId, CursorState] = {}

    async def load(self, tenant: TenantId) -> CursorState | None:
        """L'état du client, ``None`` s'il n'a jamais été écrit."""
        return self._states.get(tenant)

    async def advance(self, tenant: TenantId, acked: Mapping[SessionId, int], *, flushed_at: datetime) -> None:
        """Avance les positions, au plus grand ``seq``."""
        self._states[tenant] = _merged(self._states.get(tenant), acked, flushed_at)

    async def mark_deleted(self, tenant: TenantId, sessions: Iterable[SessionId]) -> None:
        """Note des sessions effacées."""
        state = self._states.get(tenant) or CursorState(flushed_at=datetime.now(UTC))
        self._states[tenant] = CursorState(acked=state.acked, deleted=state.deleted | frozenset(sessions), flushed_at=state.flushed_at)

    async def forget(self, tenant: TenantId, sessions: Iterable[SessionId]) -> None:
        """Retire des sessions."""
        state = self._states.get(tenant)
        if state is None:
            return
        gone = frozenset(sessions)
        acked = {session: seq for session, seq in state.acked.items() if session not in gone}
        self._states[tenant] = CursorState(acked=acked, deleted=state.deleted - gone, flushed_at=state.flushed_at)

    async def aclose(self) -> None:
        """Rien à fermer."""


# --- JSONL : un fichier par client, à côté de ses journaux -----------------------------


class JsonlCursor:
    """Curseur d'un journal JSONL : ``<racine>/<client>/.console-cursor.json``.

    Chaque écriture relit le fichier sous un verrou ``flock`` posé sur le dossier
    du client, fusionne, puis remplace le fichier d'un coup (fichier temporaire
    et ``os.replace``) : deux process sur le même dossier ne se perdent rien.
    """

    def __init__(self, root: str | os.PathLike[str]) -> None:
        self.root = Path(root)

    def path(self, tenant: TenantId) -> Path:
        """Le fichier du curseur d'un client."""
        return self.root / tenant / CURSOR_FILE

    async def load(self, tenant: TenantId) -> CursorState | None:
        """L'état rangé ; ``None`` sans fichier."""
        return await asyncio.to_thread(self._read, tenant)

    async def advance(self, tenant: TenantId, acked: Mapping[SessionId, int], *, flushed_at: datetime) -> None:
        """Avance les positions, sous verrou."""

        def change(state: CursorState | None) -> CursorState:
            return _merged(state, acked, flushed_at)

        await asyncio.to_thread(self._update, tenant, change)

    async def mark_deleted(self, tenant: TenantId, sessions: Iterable[SessionId]) -> None:
        """Note des sessions effacées, sous verrou."""
        marked = frozenset(sessions)

        def change(state: CursorState | None) -> CursorState:
            base = state or CursorState(flushed_at=datetime.now(UTC))
            return CursorState(acked=base.acked, deleted=base.deleted | marked, flushed_at=base.flushed_at)

        await asyncio.to_thread(self._update, tenant, change)

    async def forget(self, tenant: TenantId, sessions: Iterable[SessionId]) -> None:
        """Retire des sessions, sous verrou."""
        gone = frozenset(sessions)

        def change(state: CursorState | None) -> CursorState:
            base = state or CursorState()
            acked = {session: seq for session, seq in base.acked.items() if session not in gone}
            return CursorState(acked=acked, deleted=base.deleted - gone, flushed_at=base.flushed_at)

        await asyncio.to_thread(self._update, tenant, change)

    async def aclose(self) -> None:
        """Rien à fermer : chaque écriture ouvre et ferme son fichier."""

    def _read(self, tenant: TenantId) -> CursorState | None:
        try:
            raw = json.loads(self.path(tenant).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        data = cast("dict[str, Any]", raw)
        sessions = cast("dict[str, dict[str, Any]]", data.get("sessions", {}))
        flushed = data.get("flushed_at")
        return CursorState(
            acked={SessionId(name): int(entry.get("acked", 0)) for name, entry in sessions.items()},
            deleted=frozenset(SessionId(name) for name, entry in sessions.items() if entry.get("deleted")),
            flushed_at=datetime.fromisoformat(flushed) if isinstance(flushed, str) else None,
        )

    def _update(self, tenant: TenantId, change: Callable[[CursorState | None], CursorState]) -> None:
        folder = self.path(tenant).parent
        folder.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(folder, os.O_RDONLY)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            state = change(self._read(tenant))
            sessions: dict[str, dict[str, Any]] = {session: {"acked": seq} for session, seq in state.acked.items()}
            for session in state.deleted:
                sessions.setdefault(session, {"acked": 0})["deleted"] = True
            content = {"flushed_at": None if state.flushed_at is None else state.flushed_at.isoformat(), "sessions": dict(sorted(sessions.items()))}
            target = self.path(tenant)
            temporary = target.with_name(f".{CURSOR_FILE}.{new_id()}")
            temporary.write_text(json.dumps(content, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
            os.replace(temporary, target)
        finally:
            os.close(descriptor)


# --- SQLite : une table dans le fichier du journal --------------------------------------

_SQLITE_SCHEMA: Final = f"""
CREATE TABLE IF NOT EXISTS {SQLITE_TABLE} (
    tenant_id  TEXT    NOT NULL,
    session_id TEXT    NOT NULL,
    acked      INTEGER NOT NULL DEFAULT 0,
    deleted    INTEGER NOT NULL DEFAULT 0,
    flushed_at TEXT,
    PRIMARY KEY (tenant_id, session_id)
);
"""


class SqliteCursor:
    """Curseur d'un journal SQLite : la table ``console_cursor``, dans le même fichier."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)
        self._connection: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    async def _connect(self) -> aiosqlite.Connection:
        import aiosqlite

        if self._connection is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            connection = await aiosqlite.connect(self.path, isolation_level=None)
            try:
                await connection.execute("PRAGMA busy_timeout = 5000")
                await connection.executescript(_SQLITE_SCHEMA)
            except BaseException:
                await connection.close()
                raise
            self._connection = connection
        return self._connection

    async def load(self, tenant: TenantId) -> CursorState | None:
        """L'état rangé ; ``None`` sans ligne pour ce client."""
        async with self._lock:
            connection = await self._connect()
            async with connection.execute(f"SELECT session_id, acked, deleted, flushed_at FROM {SQLITE_TABLE} WHERE tenant_id = ?", (tenant,)) as rows:
                found = list(await rows.fetchall())
        return _state_of([(str(row[0]), int(row[1]), bool(row[2]), row[3]) for row in found])

    async def advance(self, tenant: TenantId, acked: Mapping[SessionId, int], *, flushed_at: datetime) -> None:
        """Avance les positions, au plus grand ``seq``, dans une transaction."""
        upsert = (
            f"INSERT INTO {SQLITE_TABLE} (tenant_id, session_id, acked) VALUES (?, ?, ?) "
            "ON CONFLICT (tenant_id, session_id) DO UPDATE SET acked = max(acked, excluded.acked)"
        )
        meta = (
            f"INSERT INTO {SQLITE_TABLE} (tenant_id, session_id, flushed_at) VALUES (?, ?, ?) "
            "ON CONFLICT (tenant_id, session_id) DO UPDATE SET flushed_at = excluded.flushed_at"
        )
        await self._write([(upsert, [(tenant, session, seq) for session, seq in acked.items()]), (meta, [(tenant, _META, flushed_at.isoformat())])])

    async def mark_deleted(self, tenant: TenantId, sessions: Iterable[SessionId]) -> None:
        """Note des sessions effacées."""
        upsert = f"INSERT INTO {SQLITE_TABLE} (tenant_id, session_id, deleted) VALUES (?, ?, 1) ON CONFLICT (tenant_id, session_id) DO UPDATE SET deleted = 1"
        meta = f"INSERT INTO {SQLITE_TABLE} (tenant_id, session_id, flushed_at) VALUES (?, ?, ?) ON CONFLICT (tenant_id, session_id) DO NOTHING"
        await self._write([(upsert, [(tenant, session) for session in sessions]), (meta, [(tenant, _META, datetime.now(UTC).isoformat())])])

    async def forget(self, tenant: TenantId, sessions: Iterable[SessionId]) -> None:
        """Retire des sessions."""
        await self._write(
            [(f"DELETE FROM {SQLITE_TABLE} WHERE tenant_id = ? AND session_id = ?", [(tenant, session) for session in sessions if session != _META])]
        )

    async def aclose(self) -> None:
        """Ferme la connexion."""
        async with self._lock:
            if self._connection is not None:
                await self._connection.close()
                self._connection = None

    async def _write(self, statements: list[tuple[str, list[tuple[Any, ...]]]]) -> None:
        async with self._lock:
            connection = await self._connect()
            await connection.execute("BEGIN IMMEDIATE")
            try:
                for sql, rows in statements:
                    if rows:
                        await connection.executemany(sql, rows)
                await connection.commit()
            except BaseException:
                await connection.rollback()
                raise


# --- Postgres : une table dans la base du journal ---------------------------------------


class PostgresCursor:
    """Curseur d'un journal Postgres : la table ``loom_console_cursor``, sous la politique de lignes du journal."""

    def __init__(self, dsn: str, *, role: str | None, limits: PoolLimits | None = None) -> None:
        from loom_ia.adapters.postgres.pool import DEFAULT_LIMITS, PostgresPool
        from loom_ia.adapters.postgres.sql import CONSOLE_CURSOR_SCHEMA_VERSION, CONSOLE_CURSOR_TABLE, ddl

        self._table = CONSOLE_CURSOR_TABLE
        self._pg: PostgresPool = PostgresPool(
            dsn,
            table=CONSOLE_CURSOR_TABLE,
            ddl=ddl(role=role, events=False, idempotency=False, console_cursor=True),
            role=role,
            label="Curseur de la console",
            schema=("console_cursor", CONSOLE_CURSOR_SCHEMA_VERSION),
            limits=limits or DEFAULT_LIMITS,
        )

    async def load(self, tenant: TenantId) -> CursorState | None:
        """L'état rangé ; ``None`` sans ligne pour ce client."""
        async with self._pg.transaction(tenant) as connection:
            rows = await connection.fetch(f"SELECT session_id, acked, deleted, flushed_at FROM {self._table} WHERE tenant_id = $1", tenant)
        return _state_of([(str(row["session_id"]), int(row["acked"]), bool(row["deleted"]), row["flushed_at"]) for row in rows])

    async def advance(self, tenant: TenantId, acked: Mapping[SessionId, int], *, flushed_at: datetime) -> None:
        """Avance les positions, au plus grand ``seq``, dans une transaction."""
        async with self._pg.transaction(tenant) as connection:
            if acked:
                await connection.executemany(
                    f"INSERT INTO {self._table} (tenant_id, session_id, acked) VALUES ($1, $2, $3) "
                    f"ON CONFLICT (tenant_id, session_id) DO UPDATE SET acked = GREATEST({self._table}.acked, excluded.acked)",
                    [(tenant, session, seq) for session, seq in acked.items()],
                )
            await connection.execute(
                f"INSERT INTO {self._table} (tenant_id, session_id, flushed_at) VALUES ($1, $2, $3) "
                "ON CONFLICT (tenant_id, session_id) DO UPDATE SET flushed_at = excluded.flushed_at",
                tenant,
                _META,
                flushed_at,
            )

    async def mark_deleted(self, tenant: TenantId, sessions: Iterable[SessionId]) -> None:
        """Note des sessions effacées."""
        async with self._pg.transaction(tenant) as connection:
            await connection.executemany(
                f"INSERT INTO {self._table} (tenant_id, session_id, deleted) VALUES ($1, $2, true) "
                "ON CONFLICT (tenant_id, session_id) DO UPDATE SET deleted = true",
                [(tenant, session) for session in sessions],
            )
            await connection.execute(
                f"INSERT INTO {self._table} (tenant_id, session_id, flushed_at) VALUES ($1, $2, now()) ON CONFLICT (tenant_id, session_id) DO NOTHING",
                tenant,
                _META,
            )

    async def forget(self, tenant: TenantId, sessions: Iterable[SessionId]) -> None:
        """Retire des sessions."""
        gone = [session for session in sessions if session != _META]
        if not gone:
            return
        async with self._pg.transaction(tenant) as connection:
            await connection.execute(f"DELETE FROM {self._table} WHERE tenant_id = $1 AND session_id = ANY($2::text[])", tenant, gone)

    async def aclose(self) -> None:
        """Ferme le pool."""
        await self._pg.aclose()


def _state_of(rows: list[tuple[str, int, bool, object]]) -> CursorState | None:
    """L'état tiré des lignes d'une table : la ligne ``_META`` porte ``flushed_at`` ; sans elle, jamais écrit."""
    meta = next((row for row in rows if row[0] == _META), None)
    if meta is None:
        return None
    flushed = meta[3]
    if isinstance(flushed, str):
        flushed = datetime.fromisoformat(flushed)
    return CursorState(
        acked={SessionId(row[0]): row[1] for row in rows if row[0] != _META},
        deleted=frozenset(SessionId(row[0]) for row in rows if row[0] != _META and row[2]),
        flushed_at=flushed if isinstance(flushed, datetime) else None,
    )
