# SPDX-License-Identifier: Apache-2.0
"""Suite de contrat du port EventStore, exécutée sur chaque adaptateur."""

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from pathlib import Path

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

SESSION = SessionId("c-42")

type StoreFactory = Callable[[], EventStore]


@pytest.fixture(params=["memory", "jsonl"])
def make_store(request: pytest.FixtureRequest, tmp_path: Path) -> StoreFactory:
    """Fabrique de stores partageant le même stockage (pour la réouverture)."""
    if request.param == "memory":
        shared = InMemoryEventStore()
        return lambda: shared
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


async def test_concurrent_appends_get_distinct_seqs(store: EventStore) -> None:
    _, drafts = run_drafts()
    results = await asyncio.gather(*(store.append([draft], expected_seq=None) for draft in drafts))
    seqs = sorted(events[0].seq for events in results)
    assert seqs == list(range(1, len(drafts) + 1))


async def test_journal_survives_reopening(make_store: StoreFactory) -> None:
    journal, drafts = run_drafts()
    first = make_store()
    await first.append(drafts, expected_seq=0)
    await first.aclose()

    second = make_store()
    events = await second.read(DEFAULT_TENANT, SESSION)
    assert fold(events, journal.run_id).output == Message.assistant("4")
    assert await second.last_seq(DEFAULT_TENANT, SESSION) == len(drafts)


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


async def test_closing_the_notifying_store_closes_the_one_it_wraps(store: EventStore) -> None:
    notifying = NotifyingEventStore(store)
    with notifying.listen(lambda _: None):
        await notifying.aclose()
    assert notifying.listeners == 0
