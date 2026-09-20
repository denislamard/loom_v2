# SPDX-License-Identifier: Apache-2.0
"""Sessions (J4.1a) : marqueurs d'historique, écrivain partagé, lister et supprimer."""

import asyncio
import json
from pathlib import Path

import pytest
from conftest import ANSWER, QUESTION, ConfigFactory

from loom_ia.access.api import Loom, UnknownSession
from loom_ia.access.cli import main
from loom_ia.adapters.stores import InMemoryEventStore
from loom_ia.core.events import Event, SessionSnapshot
from loom_ia.core.model import (
    DEFAULT_TENANT,
    Message,
    RunId,
    SessionId,
    ToolOutput,
    artifact_uri,
    new_run_id,
)
from loom_ia.core.ports import ArtifactNotFound, EventStore
from loom_ia.core.projections import fold, history
from loom_ia.engine import SessionWriter, SessionWriters
from loom_ia.sessions import boundary, due, estimate_tokens, marked, snapshot
from loom_ia.testing import RunJournal, tool_call_message

SESSION = SessionId("atelier")


def conversation(session: SessionId, question: str, answer: str) -> RunJournal:
    """Un run complet : demande, appel d'outil, réponse."""
    journal = RunJournal(session_id=session)
    journal.start(question)
    journal.model_turn(tool_call_message(("c1", "calculer", {"expr": "2+2"})))
    journal.tool_results({"c1": ToolOutput.text("4")})
    journal.model_turn(Message.assistant(answer))
    journal.complete()
    return journal


async def written(store: EventStore, journal: RunJournal) -> list[Event]:
    last = await store.last_seq(DEFAULT_TENANT, journal.scope.session_id)
    return await store.append(journal.take(), expected_seq=last)


# --- Historique repris d'un marqueur -----------------------------------------


async def test_history_from_a_snapshot_matches_a_full_replay() -> None:
    store = InMemoryEventStore()
    await written(store, conversation(SESSION, "Premier ?", "Un."))
    without = await store.read(DEFAULT_TENANT, SESSION)
    scope = conversation(SESSION, "", "").scope

    await store.append([scope.draft(snapshot(without))], expected_seq=without[-1].seq)
    await written(store, conversation(SESSION, "Second ?", "Deux."))
    complete = await store.read(DEFAULT_TENANT, SESSION)

    # Le marqueur remplace le rejeu du premier run, sans rien y changer.
    assert history(complete) == history([e for e in complete if e.category != "session"])
    assert len([e for e in complete if e.type == "session.snapshot"]) == 1


async def test_the_marker_with_the_widest_reach_wins() -> None:
    store = InMemoryEventStore()
    await written(store, conversation(SESSION, "Premier ?", "Un."))
    events = await store.read(DEFAULT_TENANT, SESSION)
    scope = conversation(SESSION, "", "").scope
    early = SessionSnapshot(up_to_seq=1, messages=(Message.user("perdu"),))

    await store.append(
        [scope.draft(early), scope.draft(snapshot(events))], expected_seq=events[-1].seq
    )

    assert history(await store.read(DEFAULT_TENANT, SESSION)) == history(events)


async def test_a_snapshot_stops_before_a_run_still_going() -> None:
    store = InMemoryEventStore()
    done = conversation(SESSION, "Premier ?", "Un.")
    await written(store, done)
    ongoing = RunJournal(session_id=SESSION)
    ongoing.start("Second ?")
    started = await written(store, ongoing)
    events = await store.read(DEFAULT_TENANT, SESSION)

    # La position s'arrête juste avant le run.started du second run.
    assert boundary(events) == started[0].seq - 1
    assert snapshot(events).messages == tuple(history(events))


def test_the_boundary_of_an_empty_journal_is_zero() -> None:
    assert boundary([]) == 0


async def test_a_snapshot_is_written_only_when_it_pays() -> None:
    store = InMemoryEventStore()
    await written(store, conversation(SESSION, "Premier ?", "Un."))
    events = await store.read(DEFAULT_TENANT, SESSION)

    assert due(events, every=1)
    assert not due(events, every=len(events) + 1)
    assert marked(events) == 0


async def test_a_written_snapshot_moves_the_mark() -> None:
    store = InMemoryEventStore()
    await written(store, conversation(SESSION, "Premier ?", "Un."))
    events = await store.read(DEFAULT_TENANT, SESSION)
    scope = conversation(SESSION, "", "").scope

    await store.append([scope.draft(snapshot(events))], expected_seq=events[-1].seq)
    complete = await store.read(DEFAULT_TENANT, SESSION)

    assert marked(complete) == events[-1].seq
    assert not due(complete, every=1)


def test_tokens_are_estimated_from_the_messages() -> None:
    assert estimate_tokens([]) == 0
    assert estimate_tokens([Message.user("a" * 400)]) > 90


# --- Projection ---------------------------------------------------------------


async def test_a_marker_written_after_the_run_is_ignored_by_the_projection() -> None:
    store = InMemoryEventStore()
    journal = conversation(SESSION, "Premier ?", "Un.")
    events = await written(store, journal)
    scope = journal.scope

    marker = await store.append([scope.draft(snapshot(events))], expected_seq=events[-1].seq)

    state = fold([*events, *marker], journal.run_id)
    assert state.finished
    # Le marqueur ne fait pas partie du run : il n'avance pas sa position.
    assert state.last_seq == events[-1].seq


# --- Écrivain de session --------------------------------------------------------


async def test_a_conflicting_write_is_retried() -> None:
    store = InMemoryEventStore()
    first = await SessionWriter.open(store, DEFAULT_TENANT, SESSION)
    second = await SessionWriter.open(store, DEFAULT_TENANT, SESSION)

    await first.append(conversation(SESSION, "Premier ?", "Un.").take())
    # ``second`` croit encore le journal vide : il relit et réécrit.
    written_by_second = await second.append(RunJournal(session_id=SESSION).start("Second ?").take())

    assert written_by_second[0].seq == first.last_seq + 1
    assert await store.last_seq(DEFAULT_TENANT, SESSION) == written_by_second[-1].seq


async def test_the_registry_shares_one_writer_per_session() -> None:
    store = InMemoryEventStore()
    writers = SessionWriters()

    mine = await writers.open(store, DEFAULT_TENANT, SESSION)
    again = await writers.open(store, DEFAULT_TENANT, SESSION)
    other = await writers.open(store, DEFAULT_TENANT, SessionId("autre"))

    assert mine is again
    assert other is not mine
    assert len(writers) == 2
    writers.forget(DEFAULT_TENANT, SESSION)
    assert len(writers) == 1


async def test_the_registry_forgets_the_oldest_writer() -> None:
    store = InMemoryEventStore()
    writers = SessionWriters(max_writers=2)

    kept = await writers.open(store, DEFAULT_TENANT, SessionId("a"))
    await writers.open(store, DEFAULT_TENANT, SessionId("b"))
    await writers.open(store, DEFAULT_TENANT, SessionId("a"))
    await writers.open(store, DEFAULT_TENANT, SessionId("c"))

    assert len(writers) == 2
    # « a » vient d'être utilisée : c'est « b » qui part.
    assert await writers.open(store, DEFAULT_TENANT, SessionId("a")) is kept


# --- Deux runs dans une même session ---------------------------------------------


def test_two_runs_of_a_session_share_its_journal(demo: ConfigFactory) -> None:
    path = demo(storage={"events": {"backend": "jsonl", "path": "data"}})

    async def go() -> list[Event]:
        async with Loom.from_config(path) as loom:
            await asyncio.gather(
                loom.run("demo", QUESTION, session_id=SESSION),
                loom.run("demo", QUESTION, session_id=SESSION),
            )
            return await loom.export_session(SESSION)

    events = asyncio.run(go())
    assert [event.seq for event in events] == list(range(1, len(events) + 1))
    assert len({event.run_id for event in events}) == 2
    assert len([e for e in events if e.type == "run.completed"]) == 2


def test_a_second_run_reads_the_first_from_the_snapshot(demo: ConfigFactory) -> None:
    path = demo(
        storage={"events": {"backend": "jsonl", "path": "data"}},
        sessions={"snapshot_every": 1},
    )

    async def go() -> tuple[str, list[Event]]:
        async with Loom.from_config(path) as loom:
            await loom.run("demo", QUESTION, session_id=SESSION)
            second = await loom.run("demo", QUESTION, session_id=SESSION)
            return second.text, await loom.export_session(SESSION)

    answer, events = asyncio.run(go())
    markers = [e for e in events if e.type == "session.snapshot"]
    assert answer == ANSWER
    # Un snapshot par run, chacun couvrant tout ce qui le précède.
    assert len(markers) == 2
    payload = markers[-1].payload
    assert isinstance(payload, SessionSnapshot)
    covered = [e for e in events if e.seq <= payload.up_to_seq]
    assert list(payload.messages) == history(covered)
    assert payload.tokens > 0


def test_an_anonymous_run_gets_no_snapshot(demo: ConfigFactory) -> None:
    path = demo(
        storage={"events": {"backend": "jsonl", "path": "data"}},
        sessions={"snapshot_every": 1},
    )

    async def go() -> list[Event]:
        async with Loom.from_config(path) as loom:
            result = await loom.run("demo", QUESTION)
            return await loom.export_session(result.session_id)

    assert not [e for e in asyncio.run(go()) if e.category == "session"]


def test_a_snapshot_stays_out_of_the_run_events(demo: ConfigFactory) -> None:
    path = demo(
        storage={"events": {"backend": "jsonl", "path": "data"}},
        sessions={"snapshot_every": 1},
    )

    async def go() -> tuple[list[Event], list[Event]]:
        async with Loom.from_config(path) as loom:
            result = await loom.run("demo", QUESTION, session_id=SESSION)
            tree = await loom.events(result.run_id, session_id=SESSION)
            alone = await loom.events(result.run_id, session_id=SESSION, subruns=False)
            return tree, alone

    tree, alone = asyncio.run(go())
    assert not [e for e in tree if e.category == "session"]
    assert not [e for e in alone if e.category == "session"]


# --- Lister, exporter, supprimer (F7) ---------------------------------------------


def test_sessions_lists_what_the_instance_wrote(demo: ConfigFactory) -> None:
    path = demo(storage={"events": {"backend": "jsonl", "path": "data"}})

    async def go() -> list[str]:
        async with Loom.from_config(path) as loom:
            await loom.run("demo", QUESTION, session_id=SESSION)
            await loom.run("demo", QUESTION, session_id=SessionId("autre"))
            return [record.session_id for record in await loom.sessions()]

    assert sorted(asyncio.run(go())) == ["atelier", "autre"]


def test_exporting_an_unknown_session_is_refused(demo: ConfigFactory) -> None:
    path = demo(storage={"events": {"backend": "jsonl", "path": "data"}})

    async def go() -> None:
        async with Loom.from_config(path) as loom:
            await loom.export_session(SessionId("jamais-vue"))

    with pytest.raises(UnknownSession):
        asyncio.run(go())


def test_deleting_a_session_removes_its_journal_and_its_files(demo: ConfigFactory) -> None:
    path = demo(storage={"events": {"backend": "jsonl", "path": "data"}})
    uri = artifact_uri(DEFAULT_TENANT, SESSION, b"des octets", "image/png")

    async def go() -> tuple[int, int, list[SessionId], bool]:
        async with Loom.from_config(path) as loom:
            await loom.run("demo", QUESTION, session_id=SESSION)
            await loom.artifacts.put(uri, b"des octets")
            removed = await loom.delete_session(SESSION)
            left = [record.session_id for record in await loom.sessions()]
            try:
                await loom.artifact(uri)
            except ArtifactNotFound:
                gone = True
            else:
                gone = False
            return removed.events, removed.artifacts, left, gone

    events, artifacts, left, gone = asyncio.run(go())
    assert events > 0
    assert artifacts == 1
    assert left == []
    assert gone


def test_deleting_an_unknown_session_removes_nothing(demo: ConfigFactory) -> None:
    path = demo(storage={"events": {"backend": "jsonl", "path": "data"}})

    async def go() -> tuple[int, int]:
        async with Loom.from_config(path) as loom:
            removed = await loom.delete_session(SessionId("jamais-vue"))
            return removed.events, removed.artifacts

    assert asyncio.run(go()) == (0, 0)


# --- CLI ---------------------------------------------------------------------------


def test_cli_lists_exports_and_deletes_a_session(
    demo: ConfigFactory, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = demo(storage={"events": {"backend": "jsonl", "path": "data"}})
    assert main(["--config", str(path), "run", "demo", QUESTION, "--session", "atelier"]) == 0
    capsys.readouterr()

    assert main(["--config", str(path), "sessions", "list"]) == 0
    listed = capsys.readouterr().out
    assert "atelier" in listed

    out = tmp_path / "atelier.jsonl"
    assert main(["--config", str(path), "sessions", "export", "atelier", "--out", str(out)]) == 0
    capsys.readouterr()
    lines = out.read_text(encoding="utf-8").splitlines()
    assert json.loads(lines[0])["type"] == "run.started"

    assert main(["--config", str(path), "sessions", "delete", "atelier", "--yes"]) == 0
    assert "supprimée" in capsys.readouterr().out
    assert main(["--config", str(path), "sessions", "list"]) == 0
    assert "Aucune session." in capsys.readouterr().out


def test_cli_refuses_to_delete_an_unknown_session(
    demo: ConfigFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    path = demo(storage={"events": {"backend": "jsonl", "path": "data"}})
    assert main(["--config", str(path), "sessions", "delete", "jamais-vue", "--yes"]) == 1
    assert "rien à supprimer" in capsys.readouterr().err


def test_cli_exports_an_unknown_session_with_an_error(
    demo: ConfigFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    path = demo(storage={"events": {"backend": "jsonl", "path": "data"}})
    assert main(["--config", str(path), "sessions", "export", "jamais-vue"]) == 1
    assert "inconnue" in capsys.readouterr().err


def test_an_unnamed_run_is_its_own_session() -> None:
    run_id = new_run_id()
    assert SessionId(run_id) == SessionId(RunId(run_id))
