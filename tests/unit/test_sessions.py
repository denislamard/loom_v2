# SPDX-License-Identifier: Apache-2.0
"""Sessions (J4.1a) : marqueurs d'historique, écrivain partagé, lister et supprimer."""

import asyncio
import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import pytest
from conftest import ANSWER, QUESTION, ConfigFactory

from loom_ia.access.api import Loom, UnknownSession
from loom_ia.access.cli import main
from loom_ia.adapters.stores import InMemoryEventStore
from loom_ia.core.events import (
    Event,
    RunCancelled,
    RunClaimed,
    RunScope,
    SessionSnapshot,
    SessionTrimmed,
)
from loom_ia.core.model import (
    DEFAULT_TENANT,
    Message,
    RunId,
    RunStatus,
    SessionId,
    TenantId,
    ToolOutput,
    artifact_uri,
    new_run_id,
)
from loom_ia.core.ports import ArtifactNotFound, EventStore
from loom_ia.core.projections import fold, fold_all, history
from loom_ia.engine import RunExists, RunMoved, SessionWriter, SessionWriters, cancellation
from loom_ia.sessions import boundary, cut, due, estimate_tokens, marked, snapshot
from loom_ia.sessions.snapshot import Cuts
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


def folded_boundary(events: Sequence[Event]) -> int:
    """Définition d'origine de ``boundary`` : projeter chaque run (sans chevauchement)."""
    first: dict[RunId, int] = {}
    for event in events:
        first.setdefault(event.run_id, event.seq)
    running = [first[run_id] for run_id, state in fold_all(events).items() if not state.finished]
    if running:
        return min(running) - 1
    return max((event.seq for event in events if event.category != "session"), default=0)


async def test_the_boundary_matches_a_full_fold_when_runs_do_not_overlap() -> None:
    store = InMemoryEventStore()
    done = conversation(SESSION, "Un ?", "Un.")
    events = await written(store, done)
    # Marqueurs : un snapshot au nom du run, une coupe de sécurité hors de tout run.
    synthetic = RunId(SESSION)
    outside = RunScope(
        tenant_id=DEFAULT_TENANT,
        session_id=SESSION,
        run_id=synthetic,
        root_run_id=synthetic,
        agent="",
    )
    await store.append(
        [done.scope.draft(snapshot(events)), outside.draft(SessionTrimmed(up_to_seq=1))],
        expected_seq=events[-1].seq,
    )
    failed = RunJournal(session_id=SESSION)
    failed.start("Deux ?").fail("boom", "panne")
    await written(store, failed)
    stopped = RunJournal(session_id=SESSION)
    stopped.start("Trois ?").transition(RunStatus.CANCELLED, cause="cancel")
    await store.append(
        [*stopped.take(), stopped.scope.draft(RunCancelled())],
        expected_seq=await store.last_seq(DEFAULT_TENANT, SESSION),
    )
    # Un run qui délègue : l'enfant naît et finit alors que le parent tourne.
    parent = RunJournal(session_id=SESSION)
    parent.start("Quatre ?").model_turn(tool_call_message(("c1", "deleguer", {})))
    await written(store, parent)
    child = RunJournal(session_id=SESSION, root_run_id=parent.run_id, agent="enfant")
    child.start("Sous-tâche ?", parent_run_id=parent.run_id, parent_call_id="c1", depth=1)
    child.model_turn(Message.assistant("Fait.")).complete()
    await written(store, child)
    parent.tool_results({"c1": ToolOutput.text("Fait.")})
    parent.model_turn(Message.assistant("Quatre.")).complete()
    await written(store, parent)

    journal = await store.read(DEFAULT_TENANT, SESSION)

    # Chaque préfixe est un journal valide : un run y est tantôt en cours, tantôt clos.
    seen = {boundary(journal[:size]) for size in range(len(journal) + 1)}
    for size in range(len(journal) + 1):
        assert boundary(journal[:size]) == folded_boundary(journal[:size]), size
    assert len(seen) > 5
    # Le marqueur hors run ne laisse pas un run « en cours » derrière lui.
    outside_at = next(e.seq for e in journal if e.run_id == synthetic)
    assert boundary(journal[:outside_at]) == boundary(journal[: outside_at - 1])
    assert boundary(journal) == max(e.seq for e in journal if e.category != "session")


async def overlapping_runs(store: InMemoryEventStore) -> list[Event]:
    """Un tour fini, puis deux runs qui se chevauchent : le premier finit avant le second."""
    await written(store, conversation(SESSION, "Premier ?", "Un."))
    early = RunJournal(session_id=SESSION)
    early.start("Deuxième ?").model_turn(tool_call_message(("c1", "calculer", {})))
    await written(store, early)
    late = RunJournal(session_id=SESSION)
    late.start("Troisième ?").model_turn(tool_call_message(("c2", "calculer", {})))
    await written(store, late)
    early.tool_results({"c1": ToolOutput.text("4")})
    early.model_turn(Message.assistant("Deux.")).complete()
    await written(store, early)
    late.tool_results({"c2": ToolOutput.text("4")})
    late.model_turn(Message.assistant("Trois.")).complete()
    await written(store, late)
    return await store.read(DEFAULT_TENANT, SESSION)


async def test_the_boundary_does_not_cut_through_a_run_that_overlaps_a_running_one() -> None:
    events = await overlapping_runs(InMemoryEventStore())
    started = [e.seq for e in events if e.type == "run.started"]
    completed = [e.seq for e in events if e.type == "run.completed"]

    # Le deuxième run a fini, le troisième tourne : couper juste avant ce
    # dernier laisserait la fin du deuxième sans son début. On coupe avant lui.
    meanwhile = [e for e in events if e.seq <= completed[1]]
    assert started[1] < started[2] < completed[1]
    assert boundary(meanwhile) == completed[0]
    # Tous ont fini : tout le journal peut être couvert.
    assert boundary(events) == events[-1].seq


async def test_a_cut_between_overlapping_runs_falls_before_both() -> None:
    events = await overlapping_runs(InMemoryEventStore())
    started = [e.seq for e in events if e.type == "run.started"]
    completed = [e.seq for e in events if e.type == "run.completed"]
    cuts = Cuts(events)

    # Avant le deuxième run : une coupe nette, il n'y a rien à reculer.
    assert cuts.at_most(started[1] - 1) == started[1] - 1
    # Avant le troisième : il commence au milieu du deuxième, la coupe recule.
    assert cuts.at_most(started[2] - 1) == completed[0]
    # Garder « le dernier tour » ne peut pas séparer les deux : on garde les deux.
    assert cut(events, keep_last=1) == completed[0]
    assert cut(events, keep_last=2) == completed[0]
    assert cut(events, keep_last=3) == 0


async def test_a_snapshot_taken_at_any_point_matches_a_full_replay() -> None:
    events = await overlapping_runs(InMemoryEventStore())
    scope = conversation(SESSION, "", "").scope
    scratch = InMemoryEventStore()
    for size in range(1, len(events) + 1):
        [marker] = await scratch.append(
            [scope.draft(snapshot(events[:size]))],
            expected_seq=await scratch.last_seq(DEFAULT_TENANT, SESSION),
        )
        # Le marqueur, écrit après tout le reste, ne doit faire perdre aucun tour.
        written_last = marker.model_copy(update={"seq": events[-1].seq + 1})
        assert history([*events, written_last]) == history(events), size


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


async def test_a_write_about_a_run_closed_elsewhere_is_refused() -> None:
    """Un autre process a clos le run : réécrire après sa fin le ferait repartir (6.4)."""
    store = InMemoryEventStore()
    started = RunJournal(session_id=SESSION).start("Calcule.")
    await written(store, started)
    pilote = await SessionWriter.open(store, DEFAULT_TENANT, SESSION)
    ailleurs = await SessionWriter.open(store, DEFAULT_TENANT, SESSION)
    state = fold(await store.read(DEFAULT_TENANT, SESSION), started.run_id)
    arret = await ailleurs.append(cancellation(state, by="ailleurs"))

    # La concession que le pilote croyait prendre sur un run encore ouvert.
    concession = RunClaimed(worker_id="worker-pilote", lease_until=datetime.now(UTC))
    with pytest.raises(RunMoved) as refus:
        await pilote.append([started.scope.draft(concession)])
    assert refus.value.run_ids == {started.run_id}
    assert [e.type for e in refus.value.events] == ["run.cancelled"]
    # Rien d'écrit après l'arrêt, et l'écrivain sait où en est le journal.
    assert await store.last_seq(DEFAULT_TENANT, SESSION) == arret[-1].seq == pilote.last_seq
    fold(await store.read(DEFAULT_TENANT, SESSION), started.run_id)
    # Un autre run de la session s'écrit toujours.
    autre = await pilote.append(RunJournal(session_id=SESSION).start("Autre ?").take())
    assert autre[0].seq == arret[-1].seq + 1


async def test_a_run_is_not_opened_twice_through_one_writer() -> None:
    """L'écrivain partagé suit le journal : sans conflit à voir, seule sa vérification refuse."""
    store = InMemoryEventStore()
    writer = await SessionWriter.open(store, DEFAULT_TENANT, SESSION)
    run = RunId("r-1")
    first = RunJournal(session_id=SESSION, run_id=run).start("Premier ?").take()
    second = RunJournal(session_id=SESSION, run_id=run).start("Second ?").take()

    await writer.append(first, opens=run)
    with pytest.raises(RunExists, match="r-1 existe déjà"):
        await writer.append(second, opens=run)

    events = await store.read(DEFAULT_TENANT, SESSION)
    assert [e.type for e in events] == ["run.started", "message.user"]
    assert writer.last_seq == events[-1].seq


async def test_a_write_opening_a_run_lost_to_another_is_refused_not_replayed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """L'autre ouverture passe entre la vérification et l'écriture : le lot n'est pas rejoué."""
    store = InMemoryEventStore()
    run = RunId("r-1")
    ours = await SessionWriter.open(store, DEFAULT_TENANT, SESSION)
    rival = await SessionWriter.open(store, DEFAULT_TENANT, SESSION)
    rival_drafts = [RunJournal(session_id=SESSION, run_id=run).start("Rivale ?").take()]
    reading = store.read

    async def read(
        tenant_id: TenantId,
        session_id: SessionId,
        *,
        after_seq: int = 0,
        run_id: RunId | None = None,
    ) -> list[Event]:
        found = await reading(tenant_id, session_id, after_seq=after_seq, run_id=run_id)
        if run_id is not None and rival_drafts:
            # La vérification vient de ne rien voir ; l'autre écrit avant nous.
            await rival.append(rival_drafts.pop(), opens=run)
        return found

    monkeypatch.setattr(store, "read", read)
    mine = RunJournal(session_id=SESSION, run_id=run).start("Nôtre ?").take()
    with pytest.raises(RunExists):
        await ours.append(mine, opens=run)

    events = await store.read(DEFAULT_TENANT, SESSION)
    assert [e.type for e in events] == ["run.started", "message.user"]
    assert ours.last_seq == events[-1].seq


async def test_a_run_moving_on_elsewhere_does_not_refuse_its_stop() -> None:
    """Le run avance ailleurs pendant qu'on l'arrête : l'arrêt passe quand même."""
    store = InMemoryEventStore()
    journal = RunJournal(session_id=SESSION)
    journal.start("Calcule.")
    await written(store, journal)
    arreteur = await SessionWriter.open(store, DEFAULT_TENANT, SESSION)
    state = fold(await store.read(DEFAULT_TENANT, SESSION), journal.run_id)
    journal.model_turn(Message.assistant("Je calcule."))
    await written(store, journal)
    arret = await arreteur.append(cancellation(state, by="ailleurs"))
    assert arret[-1].type == "run.cancelled"
    assert fold(await store.read(DEFAULT_TENANT, SESSION), journal.run_id).finished


async def test_a_stop_after_the_end_is_refused() -> None:
    """Fini ailleurs entre la lecture et l'écriture de l'arrêt : rien après la fin."""
    store = InMemoryEventStore()
    journal = RunJournal(session_id=SESSION)
    journal.start("Calcule.")
    await written(store, journal)
    arreteur = await SessionWriter.open(store, DEFAULT_TENANT, SESSION)
    state = fold(await store.read(DEFAULT_TENANT, SESSION), journal.run_id)
    journal.model_turn(Message.assistant("4."))
    journal.complete()
    await written(store, journal)
    with pytest.raises(RunMoved, match=r"run\.completed"):
        await arreteur.append(cancellation(state, by="ailleurs"))
    assert fold(await store.read(DEFAULT_TENANT, SESSION), journal.run_id).status.value == (
        "completed"
    )


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
