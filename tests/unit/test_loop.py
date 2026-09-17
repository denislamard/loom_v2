# SPDX-License-Identifier: Apache-2.0
"""Boucle d'exécution : déroulé d'un run, plafond, échec, reprise, session."""

import asyncio
import logging
from collections.abc import AsyncGenerator, AsyncIterator, Sequence
from dataclasses import replace
from pathlib import Path

import pytest

from loom_ia.adapters.stores import InMemoryEventStore, JsonlEventStore
from loom_ia.core.events import (
    Event,
    EventDraft,
    EventQuery,
    ModelResponded,
    RunCompleted,
    RunFailed,
    RunTransitioned,
    StepCompleted,
    StepStarted,
    ToolCalled,
    ToolCompleted,
)
from loom_ia.core.model import (
    DEFAULT_TENANT,
    CallerContext,
    Message,
    ModelChunk,
    ModelSpec,
    Pricing,
    RetryPolicy,
    RunId,
    RunState,
    RunStatus,
    SessionId,
    TenantId,
    TextBlock,
    ToolOutput,
    ToolResultBlock,
    Usage,
)
from loom_ia.core.ports import EventStore
from loom_ia.core.projections import ProjectionError
from loom_ia.engine import UNKNOWN_STATE, RunContext, ToolExecutor, begin_run, drive, step
from loom_ia.testing import RunJournal, ScriptedModel, tool_call_message
from loom_ia.tools import tool

USAGE = Usage(input_tokens=1_000, output_tokens=100)
SPEC = ModelSpec(
    id="FAKE",
    sdk="fake",
    model="fake-1",
    pricing=Pricing(input=1.0, output=5.0),
    retry=RetryPolicy(initial_delay=0),
)


@tool
def calculer(expr: str) -> str:
    """Évalue une expression arithmétique."""
    return str(eval(expr, {"__builtins__": {}}))


@pytest.fixture(params=["memory", "jsonl"])
async def store(request: pytest.FixtureRequest, tmp_path: Path) -> AsyncIterator[EventStore]:
    instance: EventStore = (
        InMemoryEventStore() if request.param == "memory" else JsonlEventStore(tmp_path)
    )
    yield instance
    await instance.aclose()


def context(store: EventStore, model: ScriptedModel, **options: object) -> RunContext:
    defaults: dict[str, object] = {
        "tools": ToolExecutor([calculer]),
        "system": "Tu calcules.",
        "model_spec": SPEC,
    }
    return RunContext(
        agent="demo",
        store=store,
        model=model,
        **(defaults | options),  # pyright: ignore[reportArgumentType]
    )


def scripted(*replies: Message) -> ScriptedModel:
    return ScriptedModel(*replies, usage=USAGE)


async def journal(store: EventStore, state: RunState) -> list[Event]:
    return await store.read(state.context.tenant_id, state.session_id)


def kinds(events: list[Event]) -> list[str]:
    """Type de chaque événement, précisé par l'effet ou la transition."""
    labels: list[str] = []
    for event in events:
        match event.payload:
            case StepStarted(effect=effect):
                labels.append(f"step:{effect}")
            case RunTransitioned(to_state=target):
                labels.append(f"→{target}")
            case _:
                labels.append(event.type)
    return labels


class WatchedStore:
    """Store qui signale l'écriture d'un résultat d'outil."""

    def __init__(self, inner: EventStore) -> None:
        self.inner = inner
        self.tool_completed = asyncio.Event()

    async def append(
        self, drafts: Sequence[EventDraft], *, expected_seq: int | None
    ) -> list[Event]:
        events = await self.inner.append(drafts, expected_seq=expected_seq)
        if any(isinstance(e.payload, ToolCompleted) for e in events):
            self.tool_completed.set()
        return events

    async def read(
        self,
        tenant_id: TenantId,
        session_id: SessionId,
        *,
        after_seq: int = 0,
        run_id: RunId | None = None,
    ) -> list[Event]:
        return await self.inner.read(tenant_id, session_id, after_seq=after_seq, run_id=run_id)

    async def query(self, query: EventQuery) -> list[Event]:
        return await self.inner.query(query)

    async def last_seq(self, tenant_id: TenantId, session_id: SessionId) -> int:
        return await self.inner.last_seq(tenant_id, session_id)

    async def aclose(self) -> None:
        await self.inner.aclose()


async def append_all(store: EventStore, drafts: list[EventDraft]) -> list[Event]:
    return await store.append(drafts, expected_seq=None)


async def test_direct_answer(store: EventStore) -> None:
    model = scripted(Message.assistant("Bonjour !"))
    chunks: list[ModelChunk] = []

    async def on_chunk(chunk: ModelChunk) -> None:
        chunks.append(chunk)

    ctx = context(
        store,
        model,
        on_chunk=on_chunk,
        model_spec=SPEC.model_copy(update={"max_tokens": 500, "params": {"top_p": 1}}),
        params={"temperature": 0},
    )
    started = await begin_run(ctx, "Bonjour")
    assert started.session_id == started.run_id
    state = await drive(ctx, started.run_id)

    assert state.status is RunStatus.COMPLETED and state.finished
    assert state.output == Message.assistant("Bonjour !")
    assert (state.iterations, state.usage) == (1, USAGE)
    assert state.cost_usd == pytest.approx(0.0015)
    assert chunks[-1].type == "stopped"

    [request] = model.requests
    assert request.messages == (Message.user("Bonjour"),)
    assert (request.system, request.tool_choice, request.max_tokens) == (
        "Tu calcules.",
        "auto",
        500,
    )
    assert request.tools == (calculer.spec.definition(),)
    assert request.params == {"top_p": 1, "temperature": 0}

    events = await journal(store, state)
    assert kinds(events) == [
        "run.started",
        "message.user",
        "step:model_call",
        "model.responded",
        "step.completed",
        "→completed",
        "run.completed",
    ]
    step_started, responded, step_done, transition, closing = events[2:]
    assert isinstance(responded.payload, ModelResponded)
    assert responded.payload.request_hash == request.request_hash()
    assert responded.payload.cost_usd == pytest.approx(0.0015)
    assert responded.span_id == step_started.span_id == step_done.span_id
    assert responded.parent_span_id == events[0].span_id
    assert isinstance(step_done.payload, StepCompleted)
    assert (step_done.payload.step_no, step_done.payload.events_emitted) == (1, 1)
    assert isinstance(transition.payload, RunTransitioned)
    assert transition.payload.cause_type == "model.responded"
    assert transition.payload.cause_event_id == responded.event_id
    assert transition.span_id == events[0].span_id
    assert isinstance(closing.payload, RunCompleted)
    assert closing.payload.output == Message.assistant("Bonjour !")
    assert closing.payload.iterations == 1


async def test_tool_round_trip(store: EventStore) -> None:
    model = scripted(
        tool_call_message(("c1", "calculer", {"expr": "12*7+3"}), text="Je calcule."),
        Message.assistant("12 * 7 + 3 = 87"),
    )
    ctx = context(store, model)
    state = await drive(ctx, (await begin_run(ctx, "Combien font 12 * 7 + 3 ?")).run_id)

    assert state.status is RunStatus.COMPLETED
    assert state.output is not None and state.output.text == "12 * 7 + 3 = 87"
    assert (state.iterations, state.step) == (2, 3)
    assert state.usage == USAGE + USAGE

    events = await journal(store, state)
    assert kinds(events) == [
        "run.started",
        "message.user",
        "step:model_call",
        "model.responded",
        "step.completed",
        "→awaiting_tools",
        "step:tool_batch",
        "tool.called",
        "tool.completed",
        "step.completed",
        "→ready_for_model",
        "step:model_call",
        "model.responded",
        "step.completed",
        "→completed",
        "run.completed",
    ]
    tool_step, called, done, step_done, back = events[6:11]
    assert called.span_id == done.span_id != tool_step.span_id
    assert called.parent_span_id == tool_step.span_id
    assert isinstance(done.payload, ToolCompleted)
    assert done.payload.output == ToolOutput.text("87")
    assert isinstance(step_done.payload, StepCompleted)
    assert step_done.payload.events_emitted == 2
    assert isinstance(back.payload, RunTransitioned)
    assert back.payload.cause_event_id == done.event_id

    second = model.requests[1]
    assert [m.role for m in second.messages] == ["user", "assistant", "tool"]
    closing = events[-1].payload
    assert isinstance(closing, RunCompleted)
    assert (closing.iterations, closing.usage) == (2, USAGE + USAGE)


async def test_iteration_limit_forces_an_answer_without_tools(store: EventStore) -> None:
    model = scripted(
        tool_call_message(("c1", "calculer", {"expr": "1+1"})),
        tool_call_message(("c2", "calculer", {"expr": "2+2"}), text="Réponse : 2"),
    )
    ctx = context(store, model, max_iterations=1)
    state = await drive(ctx, (await begin_run(ctx, "1+1 ?")).run_id)

    assert state.status is RunStatus.COMPLETED
    assert state.output == Message.assistant("Réponse : 2")
    assert state.iterations == 2
    forced = model.requests[1]
    assert forced.tool_choice == "none"
    assert forced.tools == (calculer.spec.definition(),)
    events = await journal(store, state)
    assert kinds(events)[10:] == [
        "→finalizing",
        "step:finalize",
        "model.responded",
        "step.completed",
        "→completed",
        "run.completed",
    ]


async def test_forced_answer_made_only_of_tool_calls(store: EventStore) -> None:
    model = scripted(
        tool_call_message(("c1", "calculer", {"expr": "1+1"})),
        tool_call_message(("c2", "calculer", {"expr": "2+2"})),
    )
    ctx = context(store, model, max_iterations=1)
    state = await drive(ctx, (await begin_run(ctx, "1+1 ?")).run_id)
    assert state.output == Message(role="assistant", blocks=(TextBlock(text=""),))
    assert state.pending_calls == ()


async def test_model_failure_fails_the_run(
    store: EventStore, caplog: pytest.LogCaptureFixture
) -> None:
    ctx = context(store, ScriptedModel(RuntimeError("panne réseau")))
    with caplog.at_level(logging.ERROR, logger="loom_ia.engine.loop"):
        state = await drive(ctx, (await begin_run(ctx, "?")).run_id)

    assert state.status is RunStatus.FAILED and state.finished
    assert state.error == "RuntimeError: panne réseau"
    events = await journal(store, state)
    assert kinds(events)[2:] == ["step:model_call", "step.completed", "→failed", "run.failed"]
    step_done, transition, failure = (e.payload for e in events[3:])
    assert isinstance(step_done, StepCompleted) and step_done.outcome == "error"
    assert events[3].status == "error"
    assert isinstance(transition, RunTransitioned)
    assert (transition.cause_type, transition.cause_event_id) == ("RuntimeError", None)
    assert isinstance(failure, RunFailed) and failure.iterations == 0
    assert "Échec de l'appel au modèle FAKE" in caplog.text


async def test_resume_after_a_crash_during_the_tool_batch(store: EventStore) -> None:
    runs = {"rapide": 0, "lent": 0}
    slow_started = asyncio.Event()

    @tool(side_effects="irreversible")
    def rapide(x: int) -> str:
        """Outil rapide, à effet de bord."""
        runs["rapide"] += 1
        return f"rapide {x}"

    @tool
    async def lent(x: int) -> str:
        """Outil lent, sans effet de bord."""
        runs["lent"] += 1
        slow_started.set()
        await asyncio.sleep(0 if runs["lent"] > 1 else 10)
        return f"lent {x}"

    tools = ToolExecutor([rapide, lent])
    first = context(
        store,
        scripted(tool_call_message(("c1", "rapide", {"x": 1}), ("c2", "lent", {"x": 2}))),
        tools=tools,
    )
    run = await begin_run(first, "Lance les deux outils")

    watched = WatchedStore(store)
    crashed = asyncio.create_task(drive(replace(first, store=watched), run.run_id))
    async with asyncio.timeout(2):
        await slow_started.wait()
        await watched.tool_completed.wait()
    crashed.cancel()
    with pytest.raises(asyncio.CancelledError):
        await crashed

    interrupted = await journal(store, run)
    assert kinds(interrupted)[-4:] == [
        "step:tool_batch",
        "tool.called",
        "tool.called",
        "tool.completed",
    ]

    model = scripted(Message.assistant("Fait."))
    state = await drive(context(store, model, tools=tools), run.run_id)

    assert state.status is RunStatus.COMPLETED
    assert runs == {"rapide": 1, "lent": 2}
    events = await journal(store, state)
    resumed = events[len(interrupted) :]
    assert kinds(resumed)[:4] == [
        "step:tool_batch",
        "tool.called",
        "tool.completed",
        "step.completed",
    ]
    assert isinstance(resumed[0].payload, StepStarted) and resumed[0].payload.step_no == 3
    assert isinstance(resumed[1].payload, ToolCalled)
    assert (resumed[1].payload.call_id, resumed[1].payload.resumed) == ("c2", True)
    [request] = model.requests
    results = [b for m in request.messages for b in m.blocks if isinstance(b, ToolResultBlock)]
    assert [block.call_id for block in results] == ["c1", "c2"]


async def test_interrupted_tool_with_side_effects_is_not_rerun(store: EventStore) -> None:
    sent: list[str] = []

    @tool(side_effects="irreversible")
    def envoyer(texte: str) -> str:
        """Envoie un message."""
        sent.append(texte)
        return "envoyé"

    history = RunJournal(agent="demo")
    history.start("Envoie bonjour")
    history.model_turn(tool_call_message(("c1", "envoyer", {"texte": "bonjour"})))
    drafts = history.take()
    drafts += [
        history.scope.draft(
            StepStarted(step_no=2, state=RunStatus.AWAITING_TOOLS, effect="tool_batch")
        ),
        history.scope.draft(
            ToolCalled(
                call_id="c1",
                tool_name="envoyer",
                tool_kind="python",
                arguments={"texte": "bonjour"},
            )
        ),
    ]
    await append_all(store, drafts)

    model = scripted(Message.assistant("Je vérifie avant de renvoyer."))
    state = await drive(context(store, model, tools=ToolExecutor([envoyer])), history.run_id)

    assert sent == []
    assert state.status is RunStatus.COMPLETED
    result = model.requests[0].messages[-1].blocks[0]
    assert isinstance(result, ToolResultBlock)
    assert result.output == ToolOutput.error(UNKNOWN_STATE)


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        (Message.assistant("Déjà répondu"), ["→completed", "run.completed"]),
        (
            tool_call_message(("c1", "calculer", {"expr": "2*3"})),
            ["→awaiting_tools", "step:tool_batch", "tool.called", "tool.completed"],
        ),
    ],
)
async def test_recorded_model_response_is_not_requested_again(
    store: EventStore, answer: Message, expected: list[str]
) -> None:
    journal_ = RunJournal(agent="demo")
    journal_.start("?")
    drafts = journal_.take()
    drafts += [
        journal_.scope.draft(
            StepStarted(step_no=1, state=RunStatus.READY_FOR_MODEL, effect="model_call")
        ),
        journal_.scope.draft(
            ModelResponded(model_id="fake-1", provider="fake", message=answer, request_hash="h")
        ),
    ]
    written = await append_all(store, drafts)

    model = scripted(Message.assistant("Suite"))
    state = await drive(context(store, model), journal_.run_id)

    assert state.status is RunStatus.COMPLETED
    events = await journal(store, state)
    new = events[len(written) :]
    assert kinds(new)[: len(expected)] == expected
    transition = new[0].payload
    assert isinstance(transition, RunTransitioned)
    assert transition.cause_event_id == written[-1].event_id
    assert len(model.requests) == (0 if not answer.tool_calls else 1)


@pytest.mark.parametrize("target", [RunStatus.COMPLETED, RunStatus.FAILED])
async def test_missing_closing_event_is_written(store: EventStore, target: RunStatus) -> None:
    journal_ = RunJournal(agent="demo")
    journal_.start("?").model_turn(Message.assistant("Réponse"))
    journal_.transition(target, cause="model.responded")
    await append_all(store, journal_.take())

    state = await drive(context(store, scripted()), journal_.run_id)

    assert state.finished and state.status is target
    closing = (await journal(store, state))[-1].payload
    if target is RunStatus.COMPLETED:
        assert isinstance(closing, RunCompleted)
        assert closing.output == Message.assistant("Réponse")
        assert closing.iterations == 1
    else:
        assert isinstance(closing, RunFailed)
        assert closing.error_type == "Interrupted"


async def test_finished_run_is_left_untouched(store: EventStore) -> None:
    ctx = context(store, scripted(Message.assistant("ok")))
    state = await drive(ctx, (await begin_run(ctx, "?")).run_id)
    before = await journal(store, state)
    again = await drive(ctx, state.run_id)
    assert again == state
    assert await journal(store, state) == before


async def test_drive_checks_the_agent(store: EventStore) -> None:
    ctx = context(store, scripted())
    run = await begin_run(ctx, "?")
    other = RunContext(agent="autre", store=store, model=scripted(), model_spec=SPEC)
    with pytest.raises(ValueError, match="appartient à l'agent 'demo'"):
        await drive(other, run.run_id)


async def test_session_history_precedes_the_run(store: EventStore) -> None:
    session = SessionId("conversation")
    tenant = TenantId("acme")
    caller = CallerContext(tenant_id=tenant, user_id="u1")
    model = scripted(Message.assistant("Salut !"), Message.assistant("Très bien."))
    ctx = context(store, model)

    first = await begin_run(ctx, "Bonjour", session_id=session, context=caller)
    await drive(ctx, first.run_id, session_id=session, tenant_id=tenant)

    failed = RunJournal(agent="demo", tenant_id=tenant, session_id=session)
    failed.start("Plantage").fail("E", "boom")
    last = await store.last_seq(tenant, session)
    await store.append(failed.take(), expected_seq=last)

    second = await begin_run(
        ctx, Message.user("Et toi ?"), session_id=session, context=caller, run_id=RunId("r-2")
    )
    state = await drive(ctx, RunId("r-2"), session_id=session, tenant_id=tenant)

    assert second.run_id == "r-2"
    assert state.status is RunStatus.COMPLETED
    assert state.messages == (Message.user("Et toi ?"), Message.assistant("Très bien."))
    assert model.requests[1].messages == (
        Message.user("Bonjour"),
        Message.assistant("Salut !"),
        Message.user("Et toi ?"),
    )
    events = await store.read(tenant, session)
    assert {e.tenant_id for e in events} == {tenant}
    assert await store.read(DEFAULT_TENANT, session) == []


async def test_begin_run_requires_a_user_message(store: EventStore) -> None:
    with pytest.raises(ValueError, match="message 'user'"):
        await begin_run(context(store, scripted()), Message.assistant("non"))


async def test_run_identifiers(store: EventStore) -> None:
    ctx = context(store, scripted())
    run = await begin_run(ctx, "?", run_id=RunId("r-1"))
    with pytest.raises(ValueError, match="r-1 existe déjà"):
        await begin_run(ctx, "encore", run_id=RunId("r-1"))
    assert len(await journal(store, run)) == 2
    with pytest.raises(ProjectionError, match="Aucun événement"):
        await drive(ctx, RunId("inconnu"))


async def test_transitions_are_logged(store: EventStore, caplog: pytest.LogCaptureFixture) -> None:
    ctx = context(store, scripted(Message.assistant("ok")))
    with caplog.at_level(logging.INFO, logger="loom_ia.engine.loop"):
        state = await drive(ctx, (await begin_run(ctx, "?")).run_id)
    [record] = [r for r in caplog.records if r.message.startswith("Transition")]
    assert record.message == "Transition ready_for_model → completed (agent demo)"
    assert record.__dict__["run_id"] == state.run_id


async def test_step_does_nothing_in_a_waiting_state(store: EventStore) -> None:
    ctx = context(store, scripted())
    state = await begin_run(ctx, "?")
    paused = state.model_copy(update={"status": RunStatus.PAUSED})
    assert [d async for d in step(paused, ctx)] == []


async def test_drive_refuses_a_step_without_progress(
    store: EventStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def stuck(*args: object, **kwargs: object) -> AsyncGenerator[EventDraft]:
        return
        yield  # pragma: no cover

    monkeypatch.setattr("loom_ia.engine.loop.step", stuck)
    ctx = context(store, scripted())
    run = await begin_run(ctx, "?")
    with pytest.raises(RuntimeError, match="aucune progression"):
        await drive(ctx, run.run_id)
