# SPDX-License-Identifier: Apache-2.0
"""Sources d'outils ouvertes au début de chaque run (#19) : moteur seul, sans SDK MCP."""

import logging
from collections.abc import AsyncGenerator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

import pytest
from pydantic import JsonValue

from loom_ia.adapters.stores import InMemoryEventStore
from loom_ia.core.events import (
    Event,
    RunFailed,
    RunTransitioned,
    ToolCompleted,
    ToolSourceUnavailable,
)
from loom_ia.core.model import (
    DEFAULT_TENANT,
    Message,
    ModelSpec,
    RunId,
    RunStatus,
    SessionId,
    ToolOutput,
    ToolSpec,
)
from loom_ia.core.ports import SourceContext, SourceUnavailable, Tool, ToolContext
from loom_ia.engine import RunContext, ToolExecutor, begin_run, drive
from loom_ia.testing import ScriptedModel, tool_call_message
from loom_ia.tools import tool

SPEC = ModelSpec(id="FAKE", sdk="fake", model="fake-1")


@tool
def calculer(expr: str) -> str:
    """Évalue une expression arithmétique."""
    return str(eval(expr, {"__builtins__": {}}))


@dataclass
class Echo:
    """Outil fourni par une source : renvoie son argument."""

    spec: ToolSpec

    async def invoke(self, arguments: dict[str, JsonValue], context: ToolContext) -> ToolOutput:
        return ToolOutput.text(f"{self.spec.name} : {arguments.get('x')}")


def echo(name: str, schema: dict[str, JsonValue] | None = None) -> Echo:
    return Echo(
        ToolSpec(
            name=name,
            description=f"Outil {name}",
            kind="mcp",
            input_schema=schema or {"type": "object", "properties": {"x": {"type": "string"}}},
        )
    )


@dataclass
class FakeSource:
    """Source d'outils scriptée : ses outils, ou une panne."""

    name: str
    tools: list[Tool] = field(default_factory=list[Tool])
    failure: Exception | None = None
    required: bool = False
    opened: list[SourceContext] = field(default_factory=list[SourceContext])
    closed: int = 0

    @asynccontextmanager
    async def open(self, context: SourceContext) -> AsyncGenerator[Sequence[Tool]]:
        self.opened.append(context)
        if self.failure is not None:
            raise self.failure
        try:
            yield list(self.tools)
        finally:
            self.closed += 1


def context(model: ScriptedModel, *sources: FakeSource) -> RunContext:
    return RunContext(
        agent="demo",
        store=InMemoryEventStore(),
        model=model,
        model_spec=SPEC,
        tools=ToolExecutor([calculer], sources=sources),
    )


async def events_of(ctx: RunContext, run_id: str) -> list[Event]:
    return await ctx.store.read(DEFAULT_TENANT, SessionId(run_id))


async def test_source_tools_join_the_run_and_the_source_is_closed() -> None:
    time = FakeSource("time", [echo("time__maintenant")])
    model = ScriptedModel(
        tool_call_message(("c1", "time__maintenant", {"x": "Paris"})),
        Message.assistant("Il est midi."),
    )
    ctx = context(model, time)
    run = await begin_run(ctx, "Quelle heure est-il ?")
    state = await drive(ctx, run.run_id)

    assert state.status is RunStatus.COMPLETED
    assert [d.name for d in model.requests[0].tools] == ["calculer", "time__maintenant"]
    [opened] = time.opened
    assert (opened.run_id, opened.agent, opened.session_id) == (run.run_id, "demo", run.session_id)
    assert time.closed == 1
    [done] = [
        e.payload for e in await events_of(ctx, run.run_id) if isinstance(e.payload, ToolCompleted)
    ]
    assert done.output == ToolOutput.text("time__maintenant : Paris")
    # L'exécuteur de l'agent n'a pas bougé : les outils de source valent pour un run.
    assert [s.name for s in ctx.tools.specs] == ["calculer"]


async def test_unavailable_source_is_journaled_and_the_run_goes_on(
    caplog: pytest.LogCaptureFixture,
) -> None:
    down = FakeSource("crm", failure=SourceUnavailable("crm", "pas de réponse en 10 s"))
    broken = FakeSource("bug", failure=RuntimeError("bogue"))
    model = ScriptedModel(Message.assistant("Je fais sans."))
    ctx = context(model, down, broken)
    run = await begin_run(ctx, "Bonjour")
    with caplog.at_level(logging.WARNING, logger="loom_ia.engine.executor"):
        state = await drive(ctx, run.run_id)

    assert state.status is RunStatus.COMPLETED
    assert [d.name for d in model.requests[0].tools] == ["calculer"]
    events = await events_of(ctx, run.run_id)
    missing = [
        (e.payload.source, e.payload.error, e.status)
        for e in events
        if isinstance(e.payload, ToolSourceUnavailable)
    ]
    assert missing == [
        ("crm", "pas de réponse en 10 s", "warning"),
        ("bug", "RuntimeError('bogue')", "warning"),
    ]
    # Écrits avant la première étape, dans le span racine du run.
    assert [e.type for e in events[2:5]] == [
        "tool.source_unavailable",
        "tool.source_unavailable",
        "step.started",
    ]
    assert events[2].span_id == events[0].span_id
    assert "Source d'outils crm indisponible" in caplog.text


async def test_required_source_unavailable_fails_the_run() -> None:
    down = FakeSource("crm", failure=SourceUnavailable("crm", "refusé"), required=True)
    model = ScriptedModel()
    ctx = context(model, down)
    run = await begin_run(ctx, "Bonjour")
    state = await drive(ctx, run.run_id)

    assert state.status is RunStatus.FAILED
    assert state.error_type == "tool.source_unavailable"
    assert state.error == "source crm requise et indisponible : refusé"
    assert model.requests == []
    events = await events_of(ctx, run.run_id)
    assert [e.type for e in events[2:]] == [
        "tool.source_unavailable",
        "run.transitioned",
        "run.failed",
    ]
    transition = events[3].payload
    assert isinstance(transition, RunTransitioned)
    assert (transition.to_state, transition.cause_event_id) == (
        RunStatus.FAILED,
        events[2].event_id,
    )
    assert isinstance(events[4].payload, RunFailed)
    assert events[2].status == "error"


async def test_each_drive_opens_its_own_tools() -> None:
    changing = FakeSource("time", [echo("time__maintenant")])
    model = ScriptedModel(Message.assistant("Un."), Message.assistant("Deux."))
    ctx = context(model, changing)
    first = await begin_run(ctx, "Premier")
    await drive(ctx, first.run_id)
    changing.tools = [echo("time__maintenant"), echo("time__fuseau")]
    second = await begin_run(ctx, "Second")
    await drive(ctx, second.run_id)

    assert [len(r.tools) for r in model.requests] == [2, 3]
    assert changing.closed == 2
    # Un run terminé n'ouvre plus rien.
    await drive(ctx, first.run_id)
    assert len(changing.opened) == 2


async def test_unusable_source_tools_are_skipped(caplog: pytest.LogCaptureFixture) -> None:
    clash = FakeSource(
        "clash",
        [echo("calculer"), echo("clash__casse", {"type": "objet"}), echo("clash__bon")],
    )
    model = ScriptedModel(Message.assistant("Ok."))
    ctx = context(model, clash)
    run = await begin_run(ctx, "Bonjour")
    with caplog.at_level(logging.WARNING, logger="loom_ia.engine.executor"):
        await drive(ctx, run.run_id)
    assert [d.name for d in model.requests[0].tools] == ["calculer", "clash__bon"]
    assert "Outil calculer de la source clash écarté" in caplog.text
    assert "Outil clash__casse de la source clash écarté" in caplog.text


async def test_executor_without_sources_is_used_as_is() -> None:
    tools = ToolExecutor([calculer])
    context_ = SourceContext(
        tenant_id=DEFAULT_TENANT, session_id=SessionId("s"), run_id=RunId("r"), agent="demo"
    )
    async with tools.opened(context_) as opened:
        assert opened.tools is tools
        assert opened.unavailable == ()
