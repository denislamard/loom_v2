# SPDX-License-Identifier: Apache-2.0
"""Accès Python : ``Loom.run``, ``stream``, ``follow`` et ``resume``."""

from contextlib import aclosing

import pytest
from conftest import ANSWER, QUESTION, ConfigFactory, demo_agent

from loom_ia.access import Loom, UnknownRun
from loom_ia.agents import UnknownAgent
from loom_ia.core.events import Event, ToolCalled
from loom_ia.core.model import (
    ModelChunk,
    RunId,
    RunStatus,
    SessionId,
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

    assert kinds(events)[:3] == ["run.started", "message.user", "step.started"]
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
