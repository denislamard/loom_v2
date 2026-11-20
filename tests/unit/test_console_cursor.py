# SPDX-License-Identifier: Apache-2.0
"""Curseur de la console, rangé avec le journal : mémoire, JSONL, SQLite, Postgres (docs/analyse-exortateur-loom.md, lot 2)."""

from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta
from importlib.util import find_spec
from pathlib import Path
from typing import Any

import pytest
from conftest import OWNER, ConfigFactory

from loom_ia.adapters.postgres.sql import CONSOLE_CURSOR_TABLE, ddl
from loom_ia.adapters.stores import JsonlEventStore
from loom_ia.config import load_config
from loom_ia.console.cursor import CURSOR_FILE, ConsoleCursor, InMemoryCursor, JsonlCursor
from loom_ia.core.model import DEFAULT_TENANT, SessionId, TenantId
from loom_ia.runtime import create_console_cursor, postgres_ddl
from loom_ia.testing import RunJournal

type CursorFactory = Callable[[], ConsoleCursor]

S1, S2 = SessionId("s1"), SessionId("s2")
NOW = datetime(2026, 11, 20, 7, 0, tzinfo=UTC)

_SQLITE = pytest.param("sqlite", marks=pytest.mark.skipif(find_spec("aiosqlite") is None, reason="extra 'sqlite' absent"))
_POSTGRES = pytest.param("postgres", marks=pytest.mark.integration)


@pytest.fixture(params=["memory", "jsonl", _SQLITE, _POSTGRES])
def make_cursor(request: pytest.FixtureRequest, tmp_path: Path) -> CursorFactory:
    """Fabrique de curseurs sur un même stockage : deux appels, deux écrivains qui le partagent."""
    if request.param == "memory":
        shared = InMemoryCursor()
        return lambda: shared
    if request.param == "sqlite":
        from loom_ia.console.cursor import SqliteCursor

        return lambda: SqliteCursor(tmp_path / "journal.sqlite3")
    if request.param == "postgres":
        dsn = str(request.getfixturevalue("postgres_dsn"))
        from loom_ia.console.cursor import PostgresCursor

        return lambda: PostgresCursor(dsn, role="loom_app")
    return lambda: JsonlCursor(tmp_path / "journal")


@pytest.fixture
async def cursor(make_cursor: CursorFactory) -> AsyncIterator[ConsoleCursor]:
    instance = make_cursor()
    yield instance
    await instance.aclose()


async def test_never_written_is_a_first_start(cursor: ConsoleCursor) -> None:
    assert await cursor.load(DEFAULT_TENANT) is None


async def test_positions_advance_and_never_go_back(cursor: ConsoleCursor) -> None:
    await cursor.advance(DEFAULT_TENANT, {S1: 5, S2: 2}, flushed_at=NOW)
    await cursor.advance(DEFAULT_TENANT, {S1: 3, S2: 7}, flushed_at=NOW + timedelta(seconds=1))
    state = await cursor.load(DEFAULT_TENANT)
    assert state is not None
    assert dict(state.acked) == {S1: 5, S2: 7}
    assert state.flushed_at == NOW + timedelta(seconds=1)


async def test_an_empty_advance_still_marks_the_first_start_done(cursor: ConsoleCursor) -> None:
    await cursor.advance(DEFAULT_TENANT, {}, flushed_at=NOW)
    state = await cursor.load(DEFAULT_TENANT)
    assert state is not None and not state.acked


async def test_a_deletion_waits_for_its_ack_then_leaves(cursor: ConsoleCursor) -> None:
    await cursor.advance(DEFAULT_TENANT, {S1: 5, S2: 2}, flushed_at=NOW)
    await cursor.mark_deleted(DEFAULT_TENANT, [S1])
    state = await cursor.load(DEFAULT_TENANT)
    assert state is not None and state.deleted == {S1}
    await cursor.forget(DEFAULT_TENANT, [S1])
    state = await cursor.load(DEFAULT_TENANT)
    assert state is not None
    assert dict(state.acked) == {S2: 2}
    assert not state.deleted


async def test_clients_do_not_see_each_other(cursor: ConsoleCursor) -> None:
    await cursor.advance(TenantId("dupont"), {S1: 5}, flushed_at=NOW)
    assert await cursor.load(TenantId("martin")) is None


async def test_two_writers_on_one_storage_merge(make_cursor: CursorFactory) -> None:
    """Deux process sur le même journal : aucun ne fait reculer ce que l'autre a acquitté."""
    first, second = make_cursor(), make_cursor()
    try:
        await first.advance(DEFAULT_TENANT, {S1: 9}, flushed_at=NOW)
        await second.advance(DEFAULT_TENANT, {S1: 4, S2: 3}, flushed_at=NOW)
        state = await first.load(DEFAULT_TENANT)
        assert state is not None and dict(state.acked) == {S1: 9, S2: 3}
    finally:
        await first.aclose()
        await second.aclose()


async def test_the_jsonl_journal_does_not_take_the_cursor_for_a_session(tmp_path: Path) -> None:
    store = JsonlEventStore(tmp_path / "journal")
    journal = RunJournal(agent="demo", session_id=S1)
    await store.append(journal.start("Bonjour").take(), expected_seq=0)
    await JsonlCursor(tmp_path / "journal").advance(DEFAULT_TENANT, {S1: 2}, flushed_at=NOW)
    assert (tmp_path / "journal" / DEFAULT_TENANT / CURSOR_FILE).is_file()
    assert [record.session_id for record in await store.sessions(DEFAULT_TENANT)] == [S1]


def console_config(demo: ConfigFactory, storage: dict[str, Any]) -> Path:
    security: dict[str, Any] = {"roles": [OWNER], "users": [{"id": "dev", "roles": ["owner"]}], "audit": {"path": "audit.jsonl"}}
    return demo(storage=storage, security=security, console={"enable": True, "url": "wss://c.fr/loom", "key_env": "K"})


@pytest.mark.parametrize(
    ("storage", "kind"),
    [
        ({"events": {"backend": "memory"}}, "InMemoryCursor"),
        ({"events": {"backend": "jsonl", "path": "data/journal"}}, "JsonlCursor"),
        pytest.param({"events": {"backend": "sqlite", "path": "data/journal.db"}}, "SqliteCursor", marks=_SQLITE.marks),
    ],
)
def test_the_cursor_follows_the_journal(demo: ConfigFactory, storage: dict[str, Any], kind: str) -> None:
    cursor = create_console_cursor(load_config(console_config(demo, storage)))
    assert type(cursor).__name__ == kind


def test_storage_sql_adds_the_cursor_when_the_console_follows_a_postgres_journal(demo: ConfigFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PG_DSN", "postgresql://localhost/loom")
    storage = {"events": {"backend": "postgres", "dsn_env": "PG_DSN"}, "artifacts": {"backend": "memory"}}
    sql = postgres_ddl(load_config(console_config(demo, storage)))
    assert f"CREATE TABLE IF NOT EXISTS {CONSOLE_CURSOR_TABLE}" in sql
    assert f"GRANT SELECT, INSERT, UPDATE, DELETE ON {CONSOLE_CURSOR_TABLE} TO loom_app;" in sql
    assert f"ALTER TABLE {CONSOLE_CURSOR_TABLE} FORCE ROW LEVEL SECURITY;" in sql
    quiet = load_config(demo(storage=storage, console={"enable": False}))
    assert CONSOLE_CURSOR_TABLE not in postgres_ddl(quiet), "sans console active, pas de curseur"


def test_the_whole_ddl_leaves_the_cursor_out_unless_asked() -> None:
    assert CONSOLE_CURSOR_TABLE not in ddl()
    assert f"CREATE TABLE IF NOT EXISTS {CONSOLE_CURSOR_TABLE}" in ddl(console_cursor=True)
