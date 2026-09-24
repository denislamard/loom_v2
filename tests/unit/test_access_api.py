# SPDX-License-Identifier: Apache-2.0
"""Accès Python : ``Loom.run``, ``stream``, ``follow`` et ``resume``."""

from contextlib import aclosing

import pytest
from conftest import ANSWER, QUESTION, TREE_QUESTION, ConfigFactory, demo_agent

from loom_ia.access import Loom, UnknownRun
from loom_ia.agents import UnknownAgent
from loom_ia.core.events import Event, EventQuery, ToolCalled
from loom_ia.core.model import (
    DEFAULT_TENANT,
    ModelChunk,
    RunId,
    RunStatus,
    SessionId,
    TenantId,
    TextDelta,
    ToolOutput,
    new_run_id,
)
from loom_ia.testing import RunJournal, tool_call_message
from loom_ia.tools import tool


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
