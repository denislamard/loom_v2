# SPDX-License-Identifier: Apache-2.0
"""Bascule en WAL d'une base SQLite : un verrou pris par un autre process se reprend."""

import sqlite3
from importlib.util import find_spec
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(find_spec("aiosqlite") is None, reason="extra 'sqlite' absent")


class _Cursor:
    def __init__(self, row: tuple[str] | None) -> None:
        self._row = row

    async def fetchone(self) -> tuple[str] | None:
        return self._row

    async def close(self) -> None:
        return None


class _Connection:
    """Une base que ``locked`` bascules refusent, puis qui accepte (ou garde son mode)."""

    def __init__(self, *, locked: int, mode: str = "delete", then_wal: bool = False) -> None:
        self.locked = locked
        self.mode = mode
        self.then_wal = then_wal  # un autre process a posé le WAL pendant l'attente
        self.sets = 0

    async def execute(self, sql: str) -> _Cursor:
        if sql == "PRAGMA journal_mode":
            return _Cursor((self.mode,))
        assert sql == "PRAGMA journal_mode = WAL"
        self.sets += 1
        if self.locked > 0:
            self.locked -= 1
            if self.then_wal:
                self.mode = "wal"
            raise sqlite3.OperationalError("database is locked")
        self.mode = "wal"
        return _Cursor(("wal",))


async def test_enable_wal_retries_while_the_database_is_locked() -> None:
    from loom_ia.adapters._sqlite import enable_wal

    connection = _Connection(locked=3)
    await enable_wal(connection)  # pyright: ignore[reportArgumentType]
    assert connection.mode == "wal"
    assert connection.sets == 4


async def test_enable_wal_does_not_set_what_another_process_already_set() -> None:
    from loom_ia.adapters._sqlite import enable_wal

    # Le premier essai échoue parce qu'un autre process pose le WAL : le second
    # trouve le mode en place et ne le repose pas.
    connection = _Connection(locked=1, then_wal=True)
    await enable_wal(connection)  # pyright: ignore[reportArgumentType]
    assert connection.sets == 1
    already = _Connection(locked=0, mode="wal")
    await enable_wal(already)  # pyright: ignore[reportArgumentType]
    assert already.sets == 0


async def test_enable_wal_gives_up_after_its_patience(monkeypatch: pytest.MonkeyPatch) -> None:
    from loom_ia.adapters import _sqlite

    monkeypatch.setattr(_sqlite, "WAL_PATIENCE", 0.05)
    connection = _Connection(locked=10**6)
    with pytest.raises(sqlite3.OperationalError, match="locked"):
        await _sqlite.enable_wal(connection)  # pyright: ignore[reportArgumentType]
    assert connection.sets >= 2


async def test_enable_wal_does_not_hide_other_errors(tmp_path: Path) -> None:
    import aiosqlite

    from loom_ia.adapters._sqlite import enable_wal

    path = tmp_path / "pas-une-base.db"
    path.write_bytes(b"pas une base SQLite. " * 64)
    connection = await aiosqlite.connect(path, isolation_level=None)
    try:
        with pytest.raises(sqlite3.DatabaseError, match="not a database"):
            await enable_wal(connection)
    finally:
        await connection.close()


async def test_enable_wal_on_a_real_database(tmp_path: Path) -> None:
    import aiosqlite

    from loom_ia.adapters._sqlite import enable_wal

    connection = await aiosqlite.connect(tmp_path / "base.db", isolation_level=None)
    try:
        await enable_wal(connection)
        await enable_wal(connection)  # idempotent
        async with connection.execute("PRAGMA journal_mode") as cursor:
            row = await cursor.fetchone()
        assert row is not None and row[0] == "wal"
    finally:
        await connection.close()


@pytest.mark.parametrize("kind", ["journal", "idempotence"])
async def test_first_open_waits_for_a_writer_instead_of_failing(kind: str, tmp_path: Path) -> None:
    """Une base neuve dont un autre process tient l'écriture : l'ouverture attend.

    Sous un verrou d'écriture pris par un autre process, ``PRAGMA journal_mode = WAL``
    rend « database is locked » **aussitôt**, sans attendre le ``busy_timeout``
    (reste connu du 05/10). Le verrou est ici tenu 0,3 s par une autre connexion.
    """
    import threading

    from loom_ia.adapters.idempotency.sqlite import SqliteIdempotency
    from loom_ia.adapters.stores.sqlite import SqliteEventStore

    path = tmp_path / "base.db"
    writer = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
    writer.execute("CREATE TABLE autre (x)")  # la base existe, pas encore en WAL
    writer.execute("BEGIN IMMEDIATE")
    release = threading.Timer(0.3, lambda: writer.execute("COMMIT"))
    release.start()
    store = SqliteEventStore(path) if kind == "journal" else SqliteIdempotency(path)
    try:
        await store._connect()  # pyright: ignore[reportPrivateUsage]
    finally:
        release.join()
        await store.aclose()
        writer.close()
    check = sqlite3.connect(path)
    try:
        assert check.execute("PRAGMA journal_mode").fetchone() == ("wal",)
    finally:
        check.close()
