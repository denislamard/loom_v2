# SPDX-License-Identifier: Apache-2.0
"""Suite de contrat du port EventStore, exécutée sur chaque adaptateur."""

import asyncio
import errno
import logging
import os
import threading
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime, timedelta, timezone, tzinfo
from importlib.util import find_spec
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from loom_ia.adapters.stores import (
    InMemoryEventStore,
    JsonlEventStore,
    NotifyingEventStore,
)
from loom_ia.core.events import EventDraft, EventQuery
from loom_ia.core.model import (
    DEFAULT_TENANT,
    Message,
    SessionId,
    TenantId,
    ToolOutput,
)
from loom_ia.core.ports import EventStore, JournalCorrupted, SequenceConflict
from loom_ia.core.projections import fold
from loom_ia.testing import RunJournal, tool_call_message

if TYPE_CHECKING:
    from loom_ia.adapters.stores.sqlite import SqliteEventStore

SESSION = SessionId("c-42")

type StoreFactory = Callable[[], EventStore]


_SQLITE = pytest.param(
    "sqlite",
    marks=pytest.mark.skipif(find_spec("aiosqlite") is None, reason="extra 'sqlite' absent"),
)
# Postgres demande un service : la fixture ``postgres_dsn`` saute l'essai s'il
# n'y en a pas, et vide les tables avant chacun.
_POSTGRES = pytest.param("postgres", marks=pytest.mark.integration)


@pytest.fixture(params=["memory", "jsonl", _SQLITE, _POSTGRES])
def make_store(request: pytest.FixtureRequest, tmp_path: Path) -> StoreFactory:
    """Fabrique de stores partageant le même stockage (pour la réouverture)."""
    if request.param == "memory":
        shared = InMemoryEventStore()
        return lambda: shared
    if request.param == "sqlite":
        from loom_ia.adapters.stores.sqlite import SqliteEventStore

        return lambda: SqliteEventStore(tmp_path / "journal.sqlite3")
    if request.param == "postgres":
        # Le DSN d'abord : la fixture saute l'essai sans base ni extra, et
        # l'import du pilote ne doit pas précéder ce saut.
        dsn = str(request.getfixturevalue("postgres_dsn"))
        from loom_ia.adapters.stores.postgres import PostgresEventStore

        return lambda: PostgresEventStore(dsn)
    return lambda: JsonlEventStore(tmp_path / "journal")


@pytest.fixture
async def store(make_store: StoreFactory) -> AsyncIterator[EventStore]:
    instance = make_store()
    yield instance
    await instance.aclose()


def run_drafts(
    session: SessionId = SESSION, tenant: TenantId = DEFAULT_TENANT, tool: str = "calculer"
) -> tuple[RunJournal, list[EventDraft]]:
    journal = RunJournal(session_id=session, tenant_id=tenant)
    journal.start("Combien font 2 + 2 ?")
    journal.model_turn(tool_call_message(("c1", tool, {"expr": "2+2"})))
    journal.tool_results({"c1": ToolOutput.text("4")})
    journal.model_turn(Message.assistant("4"))
    journal.complete()
    return journal, journal.take()


async def test_append_numbers_events_from_one(store: EventStore) -> None:
    _, drafts = run_drafts()
    events = await store.append(drafts[:3], expected_seq=0)
    assert [e.seq for e in events] == [1, 2, 3]
    assert [e.event_id for e in events] == [d.event_id for d in drafts[:3]]
    more = await store.append(drafts[3:5], expected_seq=3)
    assert [e.seq for e in more] == [4, 5]
    assert await store.last_seq(DEFAULT_TENANT, SESSION) == 5


async def test_empty_batch_writes_nothing(store: EventStore) -> None:
    assert await store.append([], expected_seq=0) == []
    assert await store.last_seq(DEFAULT_TENANT, SESSION) == 0


async def test_expected_seq_conflict_writes_nothing(store: EventStore) -> None:
    _, drafts = run_drafts()
    await store.append(drafts[:2], expected_seq=0)
    with pytest.raises(SequenceConflict) as error:
        await store.append(drafts[2:4], expected_seq=1)
    assert (error.value.expected, error.value.actual) == (1, 2)
    assert await store.last_seq(DEFAULT_TENANT, SESSION) == 2


async def test_expected_seq_none_skips_the_check(store: EventStore) -> None:
    _, drafts = run_drafts()
    await store.append(drafts[:2], expected_seq=0)
    events = await store.append(drafts[2:3], expected_seq=None)
    assert events[0].seq == 3


async def test_batch_must_target_one_journal(store: EventStore) -> None:
    _, first = run_drafts(session=SessionId("a"))
    _, second = run_drafts(session=SessionId("b"))
    with pytest.raises(ValueError, match="un seul journal"):
        await store.append([first[0], second[0]], expected_seq=None)


async def test_read_returns_the_journal_in_order(store: EventStore) -> None:
    journal, drafts = run_drafts()
    await store.append(drafts, expected_seq=0)
    events = await store.read(DEFAULT_TENANT, SESSION)
    assert [e.seq for e in events] == list(range(1, len(drafts) + 1))
    assert fold(events, journal.run_id).output == Message.assistant("4")
    tail = await store.read(DEFAULT_TENANT, SESSION, after_seq=len(drafts) - 2)
    assert [e.type for e in tail] == ["run.transitioned", "run.completed"]


async def test_read_can_filter_one_run(store: EventStore) -> None:
    first, first_drafts = run_drafts()
    _, second_drafts = run_drafts()
    await store.append(first_drafts, expected_seq=0)
    await store.append(second_drafts, expected_seq=len(first_drafts))
    events = await store.read(DEFAULT_TENANT, SESSION, run_id=first.run_id)
    assert len(events) == len(first_drafts)
    assert {e.run_id for e in events} == {first.run_id}


async def test_unknown_journal_is_empty(store: EventStore) -> None:
    assert await store.read(DEFAULT_TENANT, SessionId("absente")) == []
    assert await store.last_seq(DEFAULT_TENANT, SessionId("absente")) == 0


async def test_tenants_are_isolated(store: EventStore) -> None:
    other = TenantId("dupont-plomberie")
    _, mine = run_drafts()
    _, theirs = run_drafts(tenant=other)
    await store.append(mine, expected_seq=0)
    await store.append(theirs, expected_seq=0)

    assert {e.tenant_id for e in await store.read(other, SESSION)} == {other}
    found = await store.query(EventQuery(tenant_id=other, limit=1000))
    assert len(found) == len(theirs)
    assert {e.tenant_id for e in found} == {other}


async def test_query_filters_and_paginates(store: EventStore) -> None:
    _, first = run_drafts(session=SessionId("a"), tool="calculer")
    _, second = run_drafts(session=SessionId("b"), tool="meteo")
    await store.append(first, expected_seq=0)
    await store.append(second, expected_seq=0)

    tool_events = await store.query(EventQuery(tenant_id=DEFAULT_TENANT, tool_name="meteo"))
    assert [e.type for e in tool_events] == ["tool.called", "tool.completed"]
    assert {e.session_id for e in tool_events} == {"b"}

    in_session = await store.query(
        EventQuery(tenant_id=DEFAULT_TENANT, session_id=SessionId("a"), types=("run.completed",))
    )
    assert len(in_session) == 1

    page = await store.query(EventQuery(tenant_id=DEFAULT_TENANT, limit=4))
    rest = await store.query(
        EventQuery(tenant_id=DEFAULT_TENANT, limit=1000, after=page[-1].event_id)
    )
    assert len(page) + len(rest) == len(first) + len(second)
    assert [e.event_id for e in page + rest] == sorted(e.event_id for e in page + rest)


@pytest.mark.parametrize(
    "zone", [UTC, timezone(timedelta(hours=2)), timezone(timedelta(hours=-5))], ids=str
)
async def test_query_bounds_are_instants_whatever_their_offset(
    store: EventStore, zone: tzinfo
) -> None:
    """``since`` / ``until`` se comparent comme des instants, dans le décalage qu'on veut.

    Un client REST français envoie ``+02:00``. Le SQLite d'avant comparait en texte
    la borne (en ``+02:00``) aux ``ts`` rangés (en UTC) : 0 événement au lieu de 8.
    Les événements sont ici datés avec des décalages et des microsecondes
    différents ; chaque magasin rend ce que ``EventQuery.select`` rend.
    """
    paris = timezone(timedelta(hours=2))
    base = datetime(2026, 3, 1, 10, 0, tzinfo=UTC)
    moments = [
        base,
        base + timedelta(microseconds=1),
        (base + timedelta(minutes=30)).astimezone(paris),
        base + timedelta(hours=14),
    ]
    _, drafts = run_drafts()
    stamped = [
        draft.model_copy(update={"ts": at}) for draft, at in zip(drafts, moments, strict=False)
    ]
    await store.append(stamped, expected_seq=0)
    stored = await store.read(DEFAULT_TENANT, SESSION)
    assert len(stored) == len(moments)

    for at in moments:
        for query in (
            EventQuery(tenant_id=DEFAULT_TENANT, since=at.astimezone(zone)),
            EventQuery(tenant_id=DEFAULT_TENANT, until=at.astimezone(zone)),
        ):
            found = [e.event_id for e in await store.query(query)]
            assert found == [e.event_id for e in query.select(stored)], (query, zone)

    after_first = EventQuery(
        tenant_id=DEFAULT_TENANT, since=(base + timedelta(microseconds=1)).astimezone(zone)
    )
    assert sorted(e.seq for e in await store.query(after_first)) == [2, 3, 4]
    before_last = EventQuery(
        tenant_id=DEFAULT_TENANT, until=(base + timedelta(hours=14)).astimezone(zone)
    )
    assert sorted(e.seq for e in await store.query(before_last)) == [1, 2, 3]


async def test_concurrent_appends_get_distinct_seqs(store: EventStore) -> None:
    _, drafts = run_drafts()
    results = await asyncio.gather(*(store.append([draft], expected_seq=None) for draft in drafts))
    seqs = sorted(events[0].seq for events in results)
    assert seqs == list(range(1, len(drafts) + 1))


async def test_concurrent_checked_appends_let_only_one_through(store: EventStore) -> None:
    """Deux écritures qui annoncent le même dernier ``seq`` : une passe, l'autre n'écrit rien.

    Ouvrir un run sous un identifiant choisi (``SessionWriter.append(opens=…)``)
    repose sur ce contrat : c'est lui qui départage deux ouvertures simultanées.
    """
    _, drafts = run_drafts()
    outcomes = await asyncio.gather(
        *(store.append([draft], expected_seq=0) for draft in drafts[:2]), return_exceptions=True
    )
    written = [outcome for outcome in outcomes if isinstance(outcome, list)]
    refused = [outcome for outcome in outcomes if isinstance(outcome, SequenceConflict)]
    assert len(written) == 1 and len(refused) == 1
    assert await store.last_seq(DEFAULT_TENANT, SESSION) == 1


async def test_journal_survives_reopening(make_store: StoreFactory) -> None:
    journal, drafts = run_drafts()
    first = make_store()
    await first.append(drafts, expected_seq=0)
    await first.aclose()

    second = make_store()
    events = await second.read(DEFAULT_TENANT, SESSION)
    assert fold(events, journal.run_id).output == Message.assistant("4")
    assert await second.last_seq(DEFAULT_TENANT, SESSION) == len(drafts)


# --- Sessions : lister et supprimer (F7) -------------------------------------


async def test_sessions_lists_journals_newest_first(store: EventStore) -> None:
    written = 0
    for session in (SessionId("c-1"), SessionId("c-2")):
        _, drafts = run_drafts(session)
        written = len(drafts)
        await store.append(drafts, expected_seq=0)

    records = await store.sessions(DEFAULT_TENANT)

    assert {record.session_id for record in records} == {"c-1", "c-2"}
    assert all(record.last_seq == written for record in records)
    assert [record.updated_at for record in records] == sorted(
        (record.updated_at for record in records), reverse=True
    )


async def test_sessions_ignores_another_tenant(store: EventStore) -> None:
    _, mine = run_drafts(SESSION)
    _, theirs = run_drafts(SessionId("c-eux"), TenantId("autre"))
    await store.append(mine, expected_seq=0)
    await store.append(theirs, expected_seq=0)

    assert [record.session_id for record in await store.sessions(DEFAULT_TENANT)] == [SESSION]
    assert [record.session_id for record in await store.sessions(TenantId("autre"))] == ["c-eux"]


async def test_delete_removes_a_journal_and_counts_it(store: EventStore) -> None:
    _, drafts = run_drafts()
    await store.append(drafts, expected_seq=0)

    assert await store.delete(DEFAULT_TENANT, SESSION) == len(drafts)

    assert await store.read(DEFAULT_TENANT, SESSION) == []
    assert await store.last_seq(DEFAULT_TENANT, SESSION) == 0
    assert await store.sessions(DEFAULT_TENANT) == []
    # Le journal repart de 1 : rien ne reste de l'ancien.
    again = await store.append(drafts[:1], expected_seq=0)
    assert [event.seq for event in again] == [1]


async def test_delete_of_an_unknown_session_removes_nothing(store: EventStore) -> None:
    assert await store.delete(DEFAULT_TENANT, SessionId("jamais-vue")) == 0


# --- Spécifique au JSONL ---------------------------------------------------


@pytest.fixture
def jsonl(tmp_path: Path) -> JsonlEventStore:
    return JsonlEventStore(tmp_path / "journal")


async def test_jsonl_layout(jsonl: JsonlEventStore, tmp_path: Path) -> None:
    _, drafts = run_drafts()
    await jsonl.append(drafts, expected_seq=0)
    path = tmp_path / "journal" / "default" / "c-42.jsonl"
    assert jsonl.path(DEFAULT_TENANT, SESSION) == path
    assert len(path.read_text(encoding="utf-8").splitlines()) == len(drafts)


@pytest.mark.parametrize("bad", ["../evil", ".cache", "a/b", "", "é"])
async def test_jsonl_rejects_unsafe_ids(jsonl: JsonlEventStore, bad: str) -> None:
    with pytest.raises(ValueError, match="inutilisable"):
        await jsonl.read(DEFAULT_TENANT, SessionId(bad))
    with pytest.raises(ValueError, match="inutilisable"):
        await jsonl.query(EventQuery(tenant_id=TenantId(bad)))


async def test_jsonl_partial_last_line_is_ignored_then_quarantined(
    jsonl: JsonlEventStore, caplog: pytest.LogCaptureFixture
) -> None:
    _, drafts = run_drafts()
    await jsonl.append(drafts[:2], expected_seq=0)
    path = jsonl.path(DEFAULT_TENANT, SESSION)
    with path.open("ab") as fh:
        fh.write(b'{"event_id": "interrompu"')

    # Un autre process a écrit : le store relit le fichier au lieu du cache.
    reopened = JsonlEventStore(path.parents[1])
    with caplog.at_level(logging.WARNING):
        events = await reopened.read(DEFAULT_TENANT, SESSION)
    assert [e.seq for e in events] == [1, 2]
    assert "incomplète ignorée" in caplog.text

    written = await reopened.append(drafts[2:3], expected_seq=2)
    assert written[0].seq == 3
    assert "déplacée" in caplog.text
    corrupt = path.with_name(path.name + ".corrupt")
    assert corrupt.read_bytes() == b'{"event_id": "interrompu"\n'
    assert [e.seq for e in await reopened.read(DEFAULT_TENANT, SESSION)] == [1, 2, 3]


async def test_jsonl_last_line_without_newline_is_one_rule_everywhere(
    jsonl: JsonlEventStore,
) -> None:
    """Une dernière ligne en JSON entier mais sans saut de ligne est incomplète, partout.

    Avant : ``last_seq`` et ``sessions`` la comptaient (lecture par la marque), ``read``
    l'ignorait et l'``append`` suivant la déplaçait dans ``.corrupt`` : ``append``
    avec le ``expected_seq`` que ``last_seq`` venait d'annoncer levait ``SequenceConflict``.
    """
    _, drafts = run_drafts()
    await jsonl.append(drafts[:3], expected_seq=0)
    path = jsonl.path(DEFAULT_TENANT, SESSION)
    whole = path.read_bytes()
    path.write_bytes(whole.removesuffix(b"\n"))

    reopened = JsonlEventStore(path.parents[1])  # un autre process : pas de cache
    assert await reopened.last_seq(DEFAULT_TENANT, SESSION) == 2
    assert [(r.session_id, r.last_seq) for r in await reopened.sessions(DEFAULT_TENANT)] == [
        (SESSION, 2)
    ]
    assert [e.seq for e in await reopened.read(DEFAULT_TENANT, SESSION)] == [1, 2]
    query = EventQuery(tenant_id=DEFAULT_TENANT, session_id=SESSION)
    assert [e.seq for e in await reopened.query(query)] == [1, 2]

    written = await reopened.append(drafts[3:4], expected_seq=2)
    assert [e.seq for e in written] == [3]
    corrupt = path.with_name(path.name + ".corrupt")
    assert corrupt.read_bytes() == whole.splitlines(keepends=True)[-1]
    assert await reopened.last_seq(DEFAULT_TENANT, SESSION) == 3
    assert [e.seq for e in await reopened.read(DEFAULT_TENANT, SESSION)] == [1, 2, 3]


@pytest.mark.parametrize("failing", ["write", "fsync"])
async def test_jsonl_a_failed_write_leaves_no_part_of_the_batch(
    jsonl: JsonlEventStore, monkeypatch: pytest.MonkeyPatch, failing: str
) -> None:
    """Un lot dont l'écriture échoue (disque plein, erreur d'E/S) n'en laisse aucune ligne.

    Avant : ses premières lignes restaient dans le journal alors que l'appelant
    avait reçu l'erreur, et le lot qu'il rejouait faisait des doublons.
    """
    _, drafts = run_drafts()
    await jsonl.append(drafts[:2], expected_seq=0)
    path = jsonl.path(DEFAULT_TENANT, SESSION)
    before = path.read_bytes()
    journal_inode = path.stat().st_ino
    real_write, real_fsync = os.write, os.fsync
    writes: list[int] = []

    def write(fd: int, data: Any) -> int:
        if failing != "write" or os.fstat(fd).st_ino != journal_inode:
            return real_write(fd, data)
        writes.append(fd)
        if len(writes) > 1:
            raise OSError(errno.ENOSPC, "No space left on device")
        # Le disque se remplit à la moitié du lot : la moitié part, puis plus rien.
        return real_write(fd, bytes(data)[: len(data) // 2])

    def fsync(fd: int) -> None:
        if failing == "fsync" and os.fstat(fd).st_ino == journal_inode:
            raise OSError(errno.EIO, "Input/output error")
        real_fsync(fd)

    monkeypatch.setattr(os, "write", write)
    monkeypatch.setattr(os, "fsync", fsync)
    with pytest.raises(OSError):
        await jsonl.append(drafts[2:6], expected_seq=2)
    monkeypatch.undo()

    assert path.read_bytes() == before
    assert not path.with_name(path.name + ".corrupt").exists()
    assert [e.seq for e in await jsonl.read(DEFAULT_TENANT, SESSION)] == [1, 2]
    again = await jsonl.append(drafts[2:6], expected_seq=2)
    assert [e.seq for e in again] == [3, 4, 5, 6]


async def test_jsonl_corrupted_complete_line_raises(jsonl: JsonlEventStore) -> None:
    _, drafts = run_drafts()
    await jsonl.append(drafts[:2], expected_seq=0)
    path = jsonl.path(DEFAULT_TENANT, SESSION)
    lines = path.read_bytes().splitlines(keepends=True)
    path.write_bytes(lines[0] + b"pas du json\n")
    with pytest.raises(JournalCorrupted, match="ligne 2"):
        await jsonl.read(DEFAULT_TENANT, SESSION)


async def test_jsonl_two_instances_share_the_sequence(tmp_path: Path) -> None:
    root = tmp_path / "journal"
    first, second = JsonlEventStore(root), JsonlEventStore(root)
    _, drafts = run_drafts()
    await first.append(drafts[:2], expected_seq=0)
    await second.append(drafts[2:4], expected_seq=2)
    with pytest.raises(SequenceConflict):
        await first.append(drafts[4:5], expected_seq=2)
    results = await asyncio.gather(
        *(
            (first if i % 2 else second).append([draft], expected_seq=None)
            for i, draft in enumerate(drafts[4:])
        )
    )
    seqs = sorted(events[0].seq for events in results)
    assert seqs == list(range(5, len(drafts) + 1))


# --- Journal qui prévient ses abonnés ----------------------------------------


async def test_notifying_store_delegates_to_the_one_it_wraps(store: EventStore) -> None:
    notifying = NotifyingEventStore(store)
    _, drafts = run_drafts()
    events = await notifying.append(drafts, expected_seq=0)

    assert notifying.inner is store
    assert await notifying.read(DEFAULT_TENANT, SESSION) == events
    assert await notifying.read(DEFAULT_TENANT, SESSION, after_seq=events[-2].seq) == events[-1:]
    assert await notifying.last_seq(DEFAULT_TENANT, SESSION) == len(events)
    query = EventQuery(tenant_id=DEFAULT_TENANT, session_id=SESSION, categories=("tool",))
    assert [e.type for e in await notifying.query(query)] == ["tool.called", "tool.completed"]


async def test_a_sink_is_called_at_each_write(store: EventStore) -> None:
    notifying = NotifyingEventStore(store)
    seen: list[str] = []
    _, drafts = run_drafts()

    with notifying.listen(lambda event: seen.append(event.type)):
        assert notifying.listeners == 1
        await notifying.append(drafts[:2], expected_seq=0)
    await notifying.append(drafts[2:4], expected_seq=2)

    assert seen == ["run.started", "message.user"]
    assert notifying.listeners == 0


async def test_a_sink_can_watch_one_run(store: EventStore) -> None:
    notifying = NotifyingEventStore(store)
    journal, drafts = run_drafts()
    other = RunJournal(session_id=SESSION)
    other.start("Autre run")
    seen: list[str] = []

    with notifying.listen(lambda event: seen.append(event.run_id), journal.run_id):
        await notifying.append(drafts[:2], expected_seq=0)
        await notifying.append(other.take(), expected_seq=2)

    assert seen == [journal.run_id, journal.run_id]


async def test_a_subscription_iterates_until_it_closes(store: EventStore) -> None:
    notifying = NotifyingEventStore(store)
    _, drafts = run_drafts()

    async with notifying.subscribe() as subscription:
        await notifying.append(drafts[:3], expected_seq=0)
        assert subscription.pending == 3
        seen = [await anext(aiter(subscription)) for _ in range(3)]
        subscription.close()
        rest = [event async for event in subscription]

    assert [event.type for event in seen] == ["run.started", "message.user", "step.started"]
    assert rest == []
    # Fermée, elle n'accepte plus rien.
    await notifying.append(drafts[3:4], expected_seq=3)
    assert subscription.pending == 0


@pytest.mark.skipif(find_spec("aiosqlite") is None, reason="extra 'sqlite' absent")
async def test_sqlite_closes_a_connection_it_could_not_set_up(tmp_path: Path) -> None:
    """Une connexion qu'il n'a pas pu mettre en place, le journal la ferme.

    Sinon son fil survit et retient le process à la sortie (voir l'essai du
    même nom pour le magasin d'idempotence). Le fichier n'est pas une base :
    la mise en place échoue sans attendre l'échéance d'un verrou.
    """
    import sqlite3

    from loom_ia.adapters.stores.sqlite import SqliteEventStore

    path = tmp_path / "journal.sqlite3"
    path.write_bytes(b"pas une base SQLite. " * 64)
    store = SqliteEventStore(path)
    avant = set(threading.enumerate())
    with pytest.raises(sqlite3.DatabaseError) as echec:
        await store.last_seq(DEFAULT_TENANT, SESSION)
    nouveaux = [fil for fil in threading.enumerate() if fil not in avant]
    for fil in nouveaux:
        fil.join(timeout=2)
    assert not [fil.name for fil in nouveaux if fil.is_alive()], echec.value
    # Le journal n'en reste pas bloqué : la base remise en état, il s'ouvre.
    path.unlink()
    assert await store.last_seq(DEFAULT_TENANT, SESSION) == 0
    await store.aclose()


# --- Spécifique à SQLite ----------------------------------------------------

_NEEDS_SQLITE = pytest.mark.skipif(find_spec("aiosqlite") is None, reason="extra 'sqlite' absent")


@pytest.fixture
async def sqlite_store(tmp_path: Path) -> AsyncIterator[SqliteEventStore]:
    from loom_ia.adapters.stores.sqlite import SqliteEventStore

    instance = SqliteEventStore(tmp_path / "journal.sqlite3")
    yield instance
    await instance.aclose()


async def _in_flight(sent: asyncio.Event, call: Awaitable[Any]) -> Any:
    """Lance ``call`` dans le fil de la connexion, prévient, puis l'attend.

    L'annulation qu'un essai envoie après ``sent`` tombe pendant que
    l'instruction tourne, ce qui est le cas réel : le fil la termine même si
    l'appelant n'est plus là.
    """
    pending = asyncio.ensure_future(call)
    await asyncio.sleep(0)
    sent.set()
    return await pending


@_NEEDS_SQLITE
async def test_sqlite_append_cancelled_while_waiting_for_the_write_lock(
    sqlite_store: SqliteEventStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un ``append`` annulé pendant le ``BEGIN IMMEDIATE`` ne garde pas le verrou d'écriture.

    Un autre process tient l'écriture : le ``BEGIN`` du magasin attend dans le fil
    de sa connexion. L'appel est annulé, l'autre relâche, le ``BEGIN`` aboutit quand
    même — et plus personne ne le conclut. Avant : la connexion restait « en
    transaction », le verrou d'écriture gardé, et tous les ``append`` suivants
    échouaient (« cannot start a transaction within a transaction »).
    """
    import sqlite3

    _, drafts = run_drafts()
    await sqlite_store.append(drafts[:1], expected_seq=0)
    connection = await sqlite_store._connect()  # pyright: ignore[reportPrivateUsage]
    real = connection.execute
    sent = asyncio.Event()

    async def execute(sql: str, *args: Any) -> Any:
        call = real(sql, *args)
        return await (_in_flight(sent, call) if sql == "BEGIN IMMEDIATE" else call)

    monkeypatch.setattr(connection, "execute", execute)
    other = sqlite3.connect(sqlite_store.path, isolation_level=None, check_same_thread=False)
    try:
        other.execute("PRAGMA busy_timeout = 200")
        other.execute("BEGIN IMMEDIATE")
        task = asyncio.create_task(sqlite_store.append(drafts[1:2], expected_seq=1))
        await sent.wait()
        task.cancel()
        other.execute("COMMIT")
        with pytest.raises(asyncio.CancelledError):
            await task

        assert not connection.in_transaction
        other.execute("BEGIN IMMEDIATE")  # le magasin ne garde plus le verrou d'écriture
        other.execute("COMMIT")
        assert [e.seq for e in await sqlite_store.append(drafts[1:2], expected_seq=1)] == [2]
    finally:
        other.close()


@_NEEDS_SQLITE
@pytest.mark.parametrize(("step", "last"), [("insert", 1), ("commit", 2)])
async def test_sqlite_append_cancelled_midway_leaves_the_connection_usable(
    sqlite_store: SqliteEventStore, monkeypatch: pytest.MonkeyPatch, step: str, last: int
) -> None:
    """Annulé pendant l'insertion, le lot n'est pas écrit ; la connexion reste libre.

    Annulé pendant le ``COMMIT`` : celui-ci est déjà parti dans le fil de la
    connexion et s'achève, le lot est écrit et l'appelant reçoit l'annulation (comme
    pour tout commit interrompu) ; la connexion, elle, est libre dans les deux cas.
    """
    _, drafts = run_drafts()
    await sqlite_store.append(drafts[:1], expected_seq=0)
    connection = await sqlite_store._connect()  # pyright: ignore[reportPrivateUsage]
    real_execute, real_many = connection.execute, connection.executemany
    sent = asyncio.Event()

    async def execute(sql: str, *args: Any) -> Any:
        call = real_execute(sql, *args)
        return await (_in_flight(sent, call) if step == "commit" and sql == "COMMIT" else call)

    async def executemany(sql: str, rows: Any) -> Any:
        call = real_many(sql, rows)
        return await (_in_flight(sent, call) if step == "insert" else call)

    monkeypatch.setattr(connection, "execute", execute)
    monkeypatch.setattr(connection, "executemany", executemany)
    task = asyncio.create_task(sqlite_store.append(drafts[1:2], expected_seq=1))
    await sent.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert not connection.in_transaction
    assert await sqlite_store.last_seq(DEFAULT_TENANT, SESSION) == last
    again = await sqlite_store.append(drafts[2:3], expected_seq=last)
    assert [e.seq for e in again] == [last + 1]


@_NEEDS_SQLITE
@pytest.mark.parametrize("step", ["insert", "commit"])
async def test_sqlite_append_failing_midway_leaves_no_transaction_open(
    sqlite_store: SqliteEventStore, monkeypatch: pytest.MonkeyPatch, step: str
) -> None:
    """Une erreur pendant l'insertion ou au ``COMMIT`` défait la transaction.

    Le ``append`` suivant réussit, et rien du lot en échec n'est écrit.
    """
    import sqlite3

    _, drafts = run_drafts()
    connection = await sqlite_store._connect()  # pyright: ignore[reportPrivateUsage]
    real_execute, real_many = connection.execute, connection.executemany
    failures = ["disk I/O error"]

    async def execute(sql: str, *args: Any) -> Any:
        if step == "commit" and sql == "COMMIT" and failures:
            raise sqlite3.OperationalError(failures.pop())
        return await real_execute(sql, *args)

    async def executemany(sql: str, rows: Any) -> Any:
        if step == "insert" and failures:
            await real_many(sql, rows)  # une partie du lot est déjà dans la transaction
            raise sqlite3.OperationalError(failures.pop())
        return await real_many(sql, rows)

    monkeypatch.setattr(connection, "execute", execute)
    monkeypatch.setattr(connection, "executemany", executemany)
    with pytest.raises(sqlite3.OperationalError, match="disk I/O"):
        await sqlite_store.append(drafts[:2], expected_seq=0)

    assert not connection.in_transaction
    assert await sqlite_store.last_seq(DEFAULT_TENANT, SESSION) == 0
    assert [e.seq for e in await sqlite_store.append(drafts[:2], expected_seq=0)] == [1, 2]


@_NEEDS_SQLITE
async def test_sqlite_failing_rollback_neither_hides_the_error_nor_wedges_the_store(
    sqlite_store: SqliteEventStore,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Un ``ROLLBACK`` qui échoue ne masque pas l'erreur d'origine, et la connexion est jetée.

    Avant : l'erreur du ``ROLLBACK`` remplaçait ``SequenceConflict`` et la
    transaction restait ouverte. Maintenant : la connexion dont on ignore l'état
    est fermée (ce qui défait la transaction) et la suivante en ouvre une neuve.
    """
    import sqlite3

    _, drafts = run_drafts()
    await sqlite_store.append(drafts[:1], expected_seq=0)
    connection = await sqlite_store._connect()  # pyright: ignore[reportPrivateUsage]
    real_execute, real_rollback = connection.execute, connection.rollback

    async def rollback() -> None:
        if connection.in_transaction:  # pas celui d'avant le BEGIN : celui qui suit l'échec
            raise sqlite3.OperationalError("disk I/O error")
        await real_rollback()

    async def execute(sql: str, *args: Any) -> Any:
        if sql == "ROLLBACK":  # la forme que l'ancien code employait
            await rollback()
        return await real_execute(sql, *args)

    monkeypatch.setattr(connection, "rollback", rollback)
    monkeypatch.setattr(connection, "execute", execute)
    with caplog.at_level(logging.ERROR), pytest.raises(SequenceConflict):
        await sqlite_store.append(drafts[1:2], expected_seq=0)  # le journal est déjà à 1
    assert "connexion abandonnée" in caplog.text

    assert await sqlite_store.last_seq(DEFAULT_TENANT, SESSION) == 1
    assert [e.seq for e in await sqlite_store.append(drafts[1:2], expected_seq=1)] == [2]


@_NEEDS_SQLITE
async def test_sqlite_append_cleans_a_transaction_left_open(sqlite_store: SqliteEventStore) -> None:
    """Un état « en transaction » hérité est défait avant le ``BEGIN IMMEDIATE`` suivant."""
    _, drafts = run_drafts()
    connection = await sqlite_store._connect()  # pyright: ignore[reportPrivateUsage]
    await connection.execute("BEGIN IMMEDIATE")
    assert connection.in_transaction

    assert [e.seq for e in await sqlite_store.append(drafts[:2], expected_seq=0)] == [1, 2]

    assert not connection.in_transaction
    assert [e.seq for e in await sqlite_store.read(DEFAULT_TENANT, SESSION)] == [1, 2]


@_NEEDS_SQLITE
async def test_sqlite_stores_ts_in_utc(sqlite_store: SqliteEventStore) -> None:
    """La colonne ``ts`` est rangée en UTC, quel que soit le décalage de l'événement.

    C'est ce qui permet de comparer en texte une borne ramenée en UTC. Les
    lignes écrites avant cette correction avec un ``ts`` d'un autre décalage (un
    appelant qui datait ses événements en ``+02:00``) ne sont pas réécrites.
    """
    import sqlite3

    paris = datetime(2026, 3, 1, 12, 30, tzinfo=timezone(timedelta(hours=2)))
    _, drafts = run_drafts()
    await sqlite_store.append([drafts[0].model_copy(update={"ts": paris})], expected_seq=0)

    check = sqlite3.connect(sqlite_store.path)
    try:
        assert check.execute("SELECT ts FROM events").fetchall() == [("2026-03-01T10:30:00+00:00",)]
    finally:
        check.close()


SECRET = "JEAN-DUPONT-0612345678"


def bytes_of(path: Path) -> bytes:
    """Octets d'un fichier, vides s'il n'existe pas (le ``-wal`` d'une base fermée)."""
    return path.read_bytes() if path.exists() else b""


def secret_drafts() -> list[EventDraft]:
    journal = RunJournal(session_id=SESSION, tenant_id=DEFAULT_TENANT)
    journal.start(f"Mon numéro est {SECRET}")
    journal.model_turn(Message.assistant("Noté."))
    journal.complete()
    return journal.take()


@_NEEDS_SQLITE
@pytest.mark.parametrize("where", ["base", "wal"])
async def test_sqlite_delete_purges_the_database_and_the_wal(tmp_path: Path, where: str) -> None:
    """Après ``delete``, la chaîne supprimée n'est plus dans la base ni dans le ``-wal`` (RGPD).

    ``base`` : les événements ont été reportés dans le fichier de la base (la
    fermeture d'un journal fait ce point de reprise). ``wal`` : ils sont encore
    dans le ``-wal``, qui garde aussi l'ancienne version des pages après la
    suppression. Avant : la chaîne restait dans l'un ou l'autre.
    """
    from loom_ia.adapters.stores.sqlite import SqliteEventStore

    path = tmp_path / "journal.sqlite3"
    wal = Path(f"{path}-wal")
    store = SqliteEventStore(path)
    drafts = secret_drafts()
    try:
        await store.append(drafts, expected_seq=0)
        if where == "base":
            await store.aclose()
            store = SqliteEventStore(path)
        assert SECRET.encode() in bytes_of(path if where == "base" else wal)

        assert await store.delete(DEFAULT_TENANT, SESSION) == len(drafts)

        assert SECRET.encode() not in bytes_of(path)
        assert SECRET.encode() not in bytes_of(wal)
        assert await store.read(DEFAULT_TENANT, SESSION) == []
        # ``secure_delete`` se pose par connexion : celle-ci doit l'avoir.
        connection = await store._connect()  # pyright: ignore[reportPrivateUsage]
        async with connection.execute("PRAGMA secure_delete") as cursor:
            assert await cursor.fetchone() == (1,)
    finally:
        await store.aclose()


@_NEEDS_SQLITE
async def test_sqlite_delete_still_succeeds_when_a_reader_keeps_the_wal(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Un lecteur ouvert dans un autre process empêche le point de reprise d'aboutir.

    SQLite répond par un drapeau « occupé », pas par une erreur : la suppression
    réussit et le dit par un avertissement, puisque le WAL n'est pas purgé.
    """
    import sqlite3

    from loom_ia.adapters.stores.sqlite import SqliteEventStore

    store = SqliteEventStore(tmp_path / "journal.sqlite3")
    drafts = secret_drafts()
    await store.append(drafts, expected_seq=0)
    connection = await store._connect()  # pyright: ignore[reportPrivateUsage]
    await connection.execute("PRAGMA busy_timeout = 50")  # l'essai n'attend pas les 5 s d'usage
    reader = sqlite3.connect(store.path, isolation_level=None)
    try:
        reader.execute("BEGIN")
        reader.execute("SELECT count(*) FROM events").fetchone()  # un instantané reste ouvert
        with caplog.at_level(logging.WARNING):
            assert await store.delete(DEFAULT_TENANT, SESSION) == len(drafts)
        assert await store.read(DEFAULT_TENANT, SESSION) == []
    finally:
        reader.close()
        await store.aclose()
    assert "le WAL n'est pas purgé" in caplog.text


async def test_closing_the_notifying_store_closes_the_one_it_wraps(store: EventStore) -> None:
    notifying = NotifyingEventStore(store)
    with notifying.listen(lambda _: None):
        await notifying.aclose()
    assert notifying.listeners == 0
