# SPDX-License-Identifier: Apache-2.0
"""Accès Python : ``Loom.run``, ``stream``, ``follow`` et ``resume``."""

import asyncio
import logging
from contextlib import aclosing
from pathlib import Path
from typing import Any

import pytest
from conftest import ANSWER, QUESTION, TREE_QUESTION, ConfigFactory, demo_agent

from loom_ia.access import Loom, UnknownRun
from loom_ia.adapters.models.fake import FakeModel
from loom_ia.adapters.stores import InMemoryEventStore
from loom_ia.adapters.usage import InMemoryUsageCounter
from loom_ia.agents import UnknownAgent
from loom_ia.config import load_config
from loom_ia.core.events import Event, EventQuery, ToolCalled
from loom_ia.core.model import (
    DEFAULT_TENANT,
    Message,
    ModelChunk,
    RunId,
    RunStatus,
    SessionId,
    TenantId,
    TextDelta,
    ToolOutput,
    new_run_id,
)
from loom_ia.core.ports import MissingKey
from loom_ia.testing import RunJournal, tool_call_message
from loom_ia.tools import tool

ACME = TenantId("acme")
BETA = TenantId("beta")


def kinds(items: list[Event]) -> list[str]:
    return [event.type for event in items]


async def test_run_answers_and_writes_its_journal(demo: ConfigFactory) -> None:
    async with Loom.from_config(demo()) as loom:
        assert loom.names == ("demo",)
        assert [spec.name for spec in loom.agents] == ["demo"]
        assert loom.exposed("rest") == loom.exposed("mcp") == loom.agents
        assert loom.config.version == 1 and "calculer" in loom.registry.names
        result = await loom.run("demo", QUESTION)

    assert (result.status, result.text, result.ok) == (RunStatus.COMPLETED, ANSWER, True)
    assert result.iterations == 2
    assert result.usage.output_tokens > 0
    assert result.session_id == SessionId(result.run_id)


async def test_run_journal_holds_the_whole_story(demo: ConfigFactory) -> None:
    async with Loom.from_config(demo()) as loom:
        result = await loom.run("demo", QUESTION)
        events = await loom.events(result.run_id)
        state = await loom.state(result.run_id)

    # `run.claimed` : la concession de l'instance qui pilote (#27, 4.2b).
    assert kinds(events)[:4] == ["run.started", "message.user", "run.claimed", "step.started"]
    assert kinds(events)[-1] == "run.completed"
    assert [e.type for e in events if e.type == "tool.called"] == ["tool.called"]
    assert state.finished and state.output is not None
    assert state.output.text == ANSWER


async def test_stream_mixes_events_and_chunks(demo: ConfigFactory) -> None:
    run_id = new_run_id()
    async with Loom.from_config(demo()) as loom:
        items = [item async for item in loom.stream("demo", QUESTION, run_id=run_id)]
        result = await loom.result(run_id)

    events = [item for item in items if isinstance(item, Event)]
    chunks = [item for item in items if not isinstance(item, Event)]
    assert kinds(events)[0] == "run.started"
    assert kinds(events)[-1] == "run.completed"
    assert "".join(chunk.text for chunk in chunks if isinstance(chunk, TextDelta)) == (
        f"Je calcule.{ANSWER}"
    )
    # Les morceaux du modèle précèdent l'événement qui les résume.
    first_text = next(i for i, item in enumerate(items) if isinstance(item, TextDelta))
    first_answer = next(i for i, item in enumerate(items) if _is(item, "model.responded"))
    assert first_text < first_answer
    assert result.text == ANSWER


async def test_stream_cancels_the_run_when_the_caller_leaves(demo: ConfigFactory) -> None:
    run_id = new_run_id()
    async with Loom.from_config(demo()) as loom:
        async with aclosing(loom.stream("demo", QUESTION, run_id=run_id)) as items:
            async for _ in items:
                break
        state = await loom.state(run_id)

    assert not state.finished
    assert state.status is not RunStatus.COMPLETED


async def _two_streams(
    loom: Loom, run_id: RunId, first: dict[str, Any], second: dict[str, Any]
) -> tuple[list[Event], list[Event]]:
    """Deux flux sous un même ``run_id`` ; le premier reste ouvert pendant que le second tourne."""
    async with aclosing(loom.stream("demo", QUESTION, run_id=run_id, **first)) as one:
        opening = await anext(one)
        other = [item async for item in loom.stream("demo", QUESTION, run_id=run_id, **second)]
        rest = [item async for item in one]
    return (
        [item for item in [opening, *rest] if isinstance(item, Event)],
        [item for item in other if isinstance(item, Event)],
    )


async def test_stream_does_not_mix_two_clients_choosing_the_same_run_id(
    demo: ConfigFactory,
) -> None:
    """Chacun ne voit que son journal : ni les événements ni le contenu de l'autre."""
    run_id = new_run_id()
    async with Loom.from_config(demo(tenants=[{"id": ACME}, {"id": BETA}])) as loom:
        acme, beta = await _two_streams(loom, run_id, {"tenant": ACME}, {"tenant": BETA})
        assert acme == await loom.events(run_id, tenant_id=ACME)
        assert beta == await loom.events(run_id, tenant_id=BETA)

    assert kinds(acme)[-1] == kinds(beta)[-1] == "run.completed"
    assert {event.tenant_id for event in acme} == {ACME}
    assert {event.tenant_id for event in beta} == {BETA}


async def test_stream_does_not_mix_two_sessions_choosing_the_same_run_id(
    demo: ConfigFactory,
) -> None:
    run_id = new_run_id()
    first, second = SessionId("s-1"), SessionId("s-2")
    async with Loom.from_config(demo()) as loom:
        one, two = await _two_streams(loom, run_id, {"session_id": first}, {"session_id": second})
        assert one == await loom.events(run_id, session_id=first)
        assert two == await loom.events(run_id, session_id=second)

    assert {event.session_id for event in one} == {first}
    assert {event.session_id for event in two} == {second}


async def test_resume_finishes_without_rerunning_the_tool(demo: ConfigFactory) -> None:
    async with Loom.from_config(demo()) as loom:
        journal = RunJournal(agent="demo")
        journal.start(QUESTION).model_turn(
            tool_call_message(("c1", "calculer", {"expr": "12*7+3"}), text="Je calcule.")
        ).tool_results({"c1": ToolOutput.text("87")})
        interrupted = await loom.store.append(journal.take(), expected_seq=0)

        result = await loom.resume(journal.run_id)
        events = await loom.events(journal.run_id)

    assert (result.status, result.text) == (RunStatus.COMPLETED, ANSWER)
    resumed = events[len(interrupted) :]
    assert not [event for event in resumed if isinstance(event.payload, ToolCalled)]
    assert kinds(resumed)[-1] == "run.completed"


async def test_follow_replays_a_finished_run(demo: ConfigFactory) -> None:
    async with Loom.from_config(demo()) as loom:
        result = await loom.run("demo", QUESTION)
        seen = [event async for event in loom.follow(result.run_id)]
        tail = [event async for event in loom.follow(result.run_id, after_seq=seen[-2].seq)]

    assert kinds(seen)[0] == "run.started" and kinds(seen)[-1] == "run.completed"
    assert kinds(tail) == ["run.completed"]


async def test_follow_and_state_refuse_an_unknown_run(demo: ConfigFactory) -> None:
    async with Loom.from_config(demo()) as loom:
        with pytest.raises(UnknownRun):
            await loom.state(RunId("run-absent"))
        with pytest.raises(UnknownRun):
            async with aclosing(loom.follow(RunId("run-absent"))) as events:
                await anext(events)


async def test_unknown_agent_is_named(demo: ConfigFactory) -> None:
    async with Loom.from_config(demo()) as loom:
        with pytest.raises(UnknownAgent, match="demo"):
            await loom.run("absent", QUESTION)


async def test_register_names_a_tool_without_imports(demo: ConfigFactory) -> None:
    @tool
    def doubler(x: int) -> str:
        """Double un nombre."""
        return str(2 * x)

    path = demo(
        imports=[],
        agents=[demo_agent(tools=[{"python": "doubler"}])],
    )
    async with Loom.from_config(path) as loom:
        loom.register("doubler", doubler)
        assert loom.context("demo").tools.get("doubler") is not None


async def test_two_runs_share_one_session(demo: ConfigFactory) -> None:
    session = SessionId("atelier")
    async with Loom.from_config(demo()) as loom:
        first = await loom.run("demo", QUESTION, session_id=session)
        second = await loom.run("demo", "Et encore ?", session_id=session)
        events = await loom.events(second.run_id, session_id=session)

    assert first.run_id != second.run_id
    assert events[0].seq > 1
    assert kinds(events)[0] == "run.started"


def _is(item: Event | ModelChunk, type_: str) -> bool:
    return isinstance(item, Event) and item.type == type_


# --- Listes et recherche (J5.4a, K5, #32) -------------------------------------


async def test_runs_lists_the_runs_of_every_session(demo: ConfigFactory) -> None:
    async with Loom.from_config(demo()) as loom:
        first = await loom.run("demo", QUESTION, session_id=SessionId("c-1"))
        second = await loom.run("demo", QUESTION, session_id=SessionId("c-2"))
        page = await loom.runs()

    # Du plus récemment écrit au plus ancien, et un run par entrée.
    assert [run.run_id for run in page.runs] == [second.run_id, first.run_id]
    assert page.scanned == 2 and page.truncated is False
    listed = page.runs[0]
    assert (listed.session_id, listed.agent, listed.status) == (
        SessionId("c-2"),
        "demo",
        RunStatus.COMPLETED,
    )
    assert listed.kind == "normal" and listed.parent_run_id is None
    assert listed.iterations == second.iterations and listed.cost_usd == second.cost_usd
    assert listed.usage.output_tokens > 0
    assert listed.started_at <= listed.updated_at
    assert listed.error_type is None


async def test_runs_filters_on_agent_status_and_dates(demo: ConfigFactory) -> None:
    path = demo(agents=[demo_agent(), demo_agent(name="autre")])
    async with Loom.from_config(path) as loom:
        first = await loom.run("demo", QUESTION, session_id=SessionId("c-1"))
        second = await loom.run("autre", QUESTION, session_id=SessionId("c-2"))
        # Dernière écriture du second run : la borne à éprouver.
        border = (await loom.runs()).runs[0].updated_at

        named = await loom.runs(agent="autre")
        done = await loom.runs(status=[RunStatus.COMPLETED])
        none = await loom.runs(status=[RunStatus.FAILED])
        recent = await loom.runs(since=border)
        older = await loom.runs(until=border)

    assert [run.run_id for run in named.runs] == [second.run_id]
    assert len(done.runs) == 2 and none.runs == ()
    # Les bornes se comparent à la dernière écriture du run, ``since`` comprise
    # et ``until`` exclue — comme celles d'une recherche d'événements.
    assert [run.run_id for run in recent.runs] == [second.run_id]
    assert [run.run_id for run in older.runs] == [first.run_id]


async def test_runs_says_when_a_bound_stopped_the_search(demo: ConfigFactory) -> None:
    async with Loom.from_config(demo()) as loom:
        for number in range(3):
            await loom.run("demo", QUESTION, session_id=SessionId(f"c-{number}"))
        bounded = await loom.runs(limit=2)
        shallow = await loom.runs(sessions=1)
        whole = await loom.runs()

    assert len(bounded.runs) == 2 and bounded.truncated is True
    assert len(shallow.runs) == 1 and shallow.scanned == 1 and shallow.truncated is True
    assert len(whole.runs) == 3 and whole.scanned == 3 and whole.truncated is False


async def test_runs_lists_a_subrun_naming_its_delegate(tree: ConfigFactory) -> None:
    async with Loom.from_config(tree()) as loom:
        result = await loom.run("demo", TREE_QUESTION)
        page = await loom.runs()

    assert len(page.runs) == 2 and page.scanned == 1
    parents = {run.agent: run.parent_run_id for run in page.runs}
    assert parents == {"demo": None, "verificateur": result.run_id}


async def test_runs_of_another_tenant_are_not_listed(demo: ConfigFactory) -> None:
    path = demo(tenants=[{"id": "dupont"}, {"id": "martin"}])
    async with Loom.from_config(path) as loom:
        await loom.run("demo", QUESTION, tenant=TenantId("dupont"))
        mine = await loom.runs(tenant_id=TenantId("dupont"))
        theirs = await loom.runs(tenant_id=TenantId("martin"))

    assert len(mine.runs) == 1 and theirs.runs == ()


async def _spoil(loom: Loom, session: SessionId, *, jsonl: Path | None) -> None:
    """Écrit une session que la lecture refuse, dans le journal de ``loom``.

    En mémoire, des événements que la projection rejette (le journal ne commence
    pas par ``run.started``) ; en JSONL (``jsonl`` : son dossier), une ligne du
    milieu qui n'est pas du JSON, la dernière restant lisible pour que la session
    se liste.
    """
    journal = RunJournal(agent="demo", session_id=session)
    journal.start(QUESTION).model_turn(Message.assistant(ANSWER)).complete()
    drafts = journal.take()
    if jsonl is None:
        await loom.store.append(drafts[1:], expected_seq=0)
        return
    await loom.store.append(drafts, expected_seq=0)
    file = jsonl / DEFAULT_TENANT / f"{session}.jsonl"
    lines = file.read_text(encoding="utf-8").splitlines()
    lines[1] = "{pas du json"
    file.write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.mark.parametrize("backend", ["memory", "jsonl"])
async def test_runs_skips_a_session_whose_journal_is_unreadable(
    demo: ConfigFactory, tmp_path: Path, caplog: pytest.LogCaptureFixture, backend: str
) -> None:
    """Un journal illisible ne prive pas des autres : sauté, avec un avertissement qui le nomme."""
    jsonl = tmp_path / "data" if backend == "jsonl" else None
    path = demo(storage={"events": {"backend": "jsonl", "path": "data"}}) if jsonl else demo()
    async with Loom.from_config(path) as loom:
        first = await loom.run("demo", QUESTION, session_id=SessionId("c-1"))
        await _spoil(loom, SessionId("c-2"), jsonl=jsonl)
        last = await loom.run("demo", QUESTION, session_id=SessionId("c-3"))
        with caplog.at_level(logging.WARNING, logger="loom_ia.access.api"):
            page = await loom.runs()

    assert [run.run_id for run in page.runs] == [last.run_id, first.run_id]
    # Le journal illisible a été ouvert : il compte, et la page ne dit rien de plus.
    assert page.scanned == 3 and page.truncated is False
    [warning] = [r.getMessage() for r in caplog.records if "illisible" in r.getMessage()]
    assert "c-2" in warning and DEFAULT_TENANT in warning


class KeyLost(InMemoryEventStore):
    """Journal dont une session est scellée par une clé qu'on a effacée."""

    def __init__(self) -> None:
        super().__init__()
        self.lost: SessionId | None = None

    async def read(
        self,
        tenant_id: TenantId,
        session_id: SessionId,
        *,
        after_seq: int = 0,
        run_id: RunId | None = None,
    ) -> list[Event]:
        if session_id == self.lost:
            raise MissingKey("Aucune clé pour ce client")
        return await super().read(tenant_id, session_id, after_seq=after_seq, run_id=run_id)


async def test_runs_skips_a_session_whose_key_was_erased(demo: ConfigFactory) -> None:
    """Le crypto-shredding est un état voulu : la session effacée ne fait pas échouer la liste."""
    store = KeyLost()
    async with Loom(load_config(demo()), store=store) as loom:
        await loom.run("demo", QUESTION, session_id=SessionId("c-1"))
        kept = await loom.run("demo", QUESTION, session_id=SessionId("c-2"))
        store.lost = SessionId("c-1")
        page = await loom.runs()

    assert [run.run_id for run in page.runs] == [kept.run_id]


async def test_query_searches_the_journal_and_paginates(demo: ConfigFactory) -> None:
    async with Loom.from_config(demo()) as loom:
        await loom.run("demo", QUESTION, session_id=SessionId("c-1"))
        calls = await loom.query(EventQuery(tenant_id=DEFAULT_TENANT, types=("tool.called",)))
        first = await loom.query(EventQuery(tenant_id=DEFAULT_TENANT, limit=1))
        after = await loom.query(
            EventQuery(tenant_id=DEFAULT_TENANT, limit=1, after=first[0].event_id)
        )
        named = await loom.query(EventQuery(tenant_id=DEFAULT_TENANT, tool_name="calculer"))
        elsewhere = await loom.query(EventQuery(tenant_id=TenantId("absent")))

    assert [event.type for event in calls] == ["tool.called"]
    assert first[0].type == "run.started" and after[0].event_id > first[0].event_id
    assert {event.type for event in named} == {"tool.called", "tool.completed"}
    assert elsewhere == []


# --- Fermeture : tout est tenté, même si une étape échoue ----------------------


def _watching(
    monkeypatch: pytest.MonkeyPatch, closed: list[str], cls: Any, label: str, error: Any = None
) -> None:
    """Note la fermeture de ``cls`` dans ``closed`` ; lève ``error``, si on en donne un."""
    original = cls.aclose

    async def aclose(self: Any) -> None:
        closed.append(label)
        if error is not None:
            raise error
        await original(self)

    monkeypatch.setattr(cls, "aclose", aclose)


async def test_aclose_goes_on_after_a_failing_step(
    demo: ConfigFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un client de modèle qui plante à la fermeture ne laisse rien d'autre ouvert."""
    closed: list[str] = []
    _watching(monkeypatch, closed, FakeModel, "modèle", RuntimeError("le modèle ne ferme pas"))
    _watching(monkeypatch, closed, InMemoryEventStore, "journal")
    _watching(monkeypatch, closed, InMemoryUsageCounter, "compteur")
    loom = Loom.from_config(demo())
    await loom.run("demo", QUESTION)

    with pytest.raises(RuntimeError, match="le modèle ne ferme pas"):
        await loom.aclose()

    assert closed == ["modèle", "journal", "compteur"]


async def test_aclose_raises_the_first_failure_and_logs_the_others(
    demo: ConfigFactory, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    closed: list[str] = []
    _watching(monkeypatch, closed, FakeModel, "modèle", RuntimeError("premier"))
    _watching(monkeypatch, closed, InMemoryEventStore, "journal", OSError("second"))
    _watching(monkeypatch, closed, InMemoryUsageCounter, "compteur")
    loom = Loom.from_config(demo())
    await loom.run("demo", QUESTION)

    with caplog.at_level(logging.WARNING), pytest.raises(RuntimeError, match="premier"):
        await loom.aclose()

    assert closed == ["modèle", "journal", "compteur"]
    [logged] = [r for r in caplog.records if r.name == "loom_ia.core.closing"]
    assert logged.exc_info is not None and str(logged.exc_info[1]) == "second"


async def test_aclose_goes_on_after_a_cancellation(
    demo: ConfigFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Annulé en pleine fermeture, l'appelant reste annulé, et le reste est fermé."""
    closed: list[str] = []
    _watching(monkeypatch, closed, FakeModel, "modèle", asyncio.CancelledError())
    _watching(monkeypatch, closed, InMemoryEventStore, "journal", RuntimeError("ordinaire"))
    _watching(monkeypatch, closed, InMemoryUsageCounter, "compteur")
    loom = Loom.from_config(demo())
    await loom.run("demo", QUESTION)

    with pytest.raises(asyncio.CancelledError):
        await loom.aclose()

    assert closed == ["modèle", "journal", "compteur"]
