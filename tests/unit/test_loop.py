# SPDX-License-Identifier: Apache-2.0
"""Boucle d'exécution : déroulé d'un run, plafond, échec, reprise, session."""

import asyncio
import inspect
import logging
from collections.abc import AsyncGenerator, AsyncIterator, Sequence
from contextlib import asynccontextmanager, suppress
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
    ApprovalOutcome,
    CallerContext,
    Message,
    ModelChunk,
    ModelSpec,
    PendingApproval,
    Pricing,
    RetryPolicy,
    RunId,
    RunState,
    RunStatus,
    SessionId,
    TenantId,
    TextBlock,
    TextDelta,
    ToolCallBlock,
    ToolOutput,
    ToolResultBlock,
    Usage,
)
from loom_ia.core.ports import EventStore, ModelError, SourceContext, SourceUnavailable, Tool
from loom_ia.core.projections import ProjectionError, fold
from loom_ia.engine import (
    UNKNOWN_STATE,
    RunContext,
    RunExists,
    SessionWriter,
    ToolExecutor,
    begin_run,
    drive,
    in_call_order,
    step,
)
from loom_ia.testing import RunJournal, ScriptedModel, tool_call_message
from loom_ia.tools import ConfiguredTool, tool

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


async def test_two_tool_calls_with_the_same_call_id_stay_distinct(store: EventStore) -> None:
    """Un fournisseur qui répète un identifiant ne casse pas la projection du run.

    Sans cela, le second ``tool.completed`` visait un appel déjà réglé : le run
    levait ``ProjectionError`` et la session devenait illisible.
    """
    model = scripted(
        tool_call_message(("c1", "calculer", {"expr": "1+1"}), ("c1", "calculer", {"expr": "2+2"})),
        Message.assistant("2 et 4"),
    )
    ctx = context(store, model)
    state = await drive(ctx, (await begin_run(ctx, "Calcule.")).run_id)

    assert state.status is RunStatus.COMPLETED
    events = await journal(store, state)
    called = [e.payload for e in events if isinstance(e.payload, ToolCalled)]
    done = [e.payload for e in events if isinstance(e.payload, ToolCompleted)]
    assert [c.call_id for c in called] == ["c1", "c1_2"]
    assert {d.call_id: d.output for d in done} == {
        "c1": ToolOutput.text("2"),
        "c1_2": ToolOutput.text("4"),
    }
    # Le journal se relit, et le modèle voit un résultat sous l'identifiant de chaque appel.
    assert fold(events, state.run_id).status is RunStatus.COMPLETED
    seen = [b for m in model.requests[1].messages for b in m.blocks]
    assert [b.call_id for b in seen if isinstance(b, ToolCallBlock)] == ["c1", "c1_2"]
    assert [b.call_id for b in seen if isinstance(b, ToolResultBlock)] == ["c1", "c1_2"]


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
    assert (state.error_type, state.error) == ("RuntimeError", "panne réseau")
    events = await journal(store, state)
    assert kinds(events)[2:] == ["step:model_call", "step.completed", "→failed", "run.failed"]
    step_done, transition, failure = (e.payload for e in events[3:])
    assert isinstance(step_done, StepCompleted) and step_done.outcome == "error"
    assert events[3].status == "error"
    assert isinstance(transition, RunTransitioned)
    assert (transition.cause_type, transition.cause_event_id) == ("RuntimeError", None)
    assert isinstance(failure, RunFailed) and failure.iterations == 0
    assert "Échec de l'appel au modèle FAKE : RuntimeError — panne réseau" in caplog.text
    # Une exception inattendue garde sa pile d'appels.
    assert caplog.records[-1].exc_info is not None


async def test_model_errors_are_logged_on_one_line(
    store: EventStore, caplog: pytest.LogCaptureFixture
) -> None:
    ctx = context(store, ScriptedModel(ModelError("auth", "clé refusée", http_status=401)))
    with caplog.at_level(logging.ERROR, logger="loom_ia.engine.loop"):
        state = await drive(ctx, (await begin_run(ctx, "?")).run_id)
    assert (state.error_type, state.error) == ("model.auth", "clé refusée")
    [record] = [r for r in caplog.records if r.name == "loom_ia.engine.loop"]
    assert record.getMessage() == "Échec de l'appel au modèle FAKE : model.auth — clé refusée"
    assert record.exc_info is None


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


# --- Clôture à moitié écrite : plantage, échéance --------------------------------


class Crash(Exception):
    """Plantage simulé du process."""


class FlakyStore(InMemoryEventStore):
    """Journal en mémoire qui plante avant sa k-ième écriture (``crash_at``)."""

    def __init__(self) -> None:
        super().__init__()
        self.crash_at: int | None = None
        self.writes = 0

    async def append(
        self, drafts: Sequence[EventDraft], *, expected_seq: int | None
    ) -> list[Event]:
        self.writes += 1
        if self.writes == self.crash_at:
            raise Crash(f"plantage à l'écriture {self.writes}")
        return await super().append(drafts, expected_seq=expected_seq)


class Erp:
    """Source d'outils requise, que l'on peut faire tomber."""

    name = "erp"
    required = True

    def __init__(self, *, up: bool) -> None:
        self.up = up

    @asynccontextmanager
    async def open(self, context: SourceContext) -> AsyncGenerator[Sequence[Tool]]:
        if not self.up:
            raise SourceUnavailable(self.name, "serveur injoignable")
        yield []


@tool
def rediger(sujet: str) -> str:
    """Rédige un texte."""
    return f"Texte sur {sujet}"


REDIGER = ConfiguredTool(tool=rediger, spec=rediger.spec.model_copy(update={"terminal": True}))


def crash_plan(scenario: str) -> tuple[list[Message], ToolExecutor, Erp | None]:
    """Réponses du modèle, outils et source de chaque scénario du balayage."""
    match scenario:
        case "tool":
            script = [
                tool_call_message(("c1", "calculer", {"expr": "1+1"})),
                Message.assistant("2"),
            ]
            return script, ToolExecutor([calculer]), None
        case "terminal_tool":
            script = [tool_call_message(("c1", "rediger", {"sujet": "la pluie"}))]
            return script, ToolExecutor([REDIGER]), None
        case "required_source_down":
            erp = Erp(up=False)
            return [Message.assistant("Bonjour")], ToolExecutor(sources=[erp]), erp
        case "source_lost_on_resume":
            erp = Erp(up=True)
            return [Message.assistant("Bonjour")], ToolExecutor(sources=[erp]), erp
        case _:
            return [Message.assistant("Bonjour")], ToolExecutor(), None


@pytest.mark.parametrize("worker", [None, "worker-1"])
@pytest.mark.parametrize(
    "scenario",
    ["answer", "tool", "terminal_tool", "required_source_down", "source_lost_on_resume"],
)
async def test_a_crash_at_any_write_leaves_a_journal_that_resumes_cleanly(
    scenario: str, worker: str | None
) -> None:
    """Balayage de plantage : le process meurt à la k-ième écriture, un autre reprend.

    Quel que soit le point — dont celui qui sépare la transition finale de sa
    clôture —, la reprise ne lève pas, le journal se relit (``fold``) et le run
    est clos. ``source_lost_on_resume`` : la source requise tombe entre-temps ;
    elle ne compte que si le run n'est pas déjà dans son état final.
    """
    crashes = 0
    for k in range(1, 60):
        store = FlakyStore()
        script, tools, erp = crash_plan(scenario)
        first = context(store, scripted(*script), tools=tools, worker_id=worker)
        run = await begin_run(first, "Salut")
        store.writes, store.crash_at = 0, k
        try:
            await drive(first, run.run_id)
        except Crash:
            crashes += 1
        else:
            break
        # Reprise : un nouveau pilote, dont le modèle reprend le script où il en était.
        store.crash_at = None
        if scenario == "source_lost_on_resume" and erp is not None:
            erp.up = False
        done = sum(1 for e in await journal(store, run) if isinstance(e.payload, ModelResponded))
        second = context(store, scripted(*script[done:]), tools=tools, worker_id=worker)
        state = await drive(second, run.run_id)

        events = await journal(store, run)
        assert fold(events, run.run_id).finished, f"écriture {k} : {kinds(events)[-3:]}"
        assert state.finished, f"écriture {k} : {state.status}"
        if scenario == "required_source_down":
            assert state.status is RunStatus.FAILED
        elif scenario != "source_lost_on_resume":
            assert state.status is RunStatus.COMPLETED
    # Le balayage a bien traversé la clôture : au moins sa transition et sa fin.
    assert crashes >= 3


async def test_a_half_closed_run_out_of_time_is_closed_not_expired(store: EventStore) -> None:
    """Le délai borne le travail à faire : une clôture à finir n'en a plus."""
    journal_ = RunJournal(agent="demo", step_ms=2_000.0)
    journal_.start("?").model_turn(Message.assistant("Réponse"))
    journal_.transition(RunStatus.COMPLETED, cause="model.responded")
    await append_all(store, journal_.take())

    state = await drive(context(store, scripted(), timeout=1.0), journal_.run_id)

    assert state.status is RunStatus.COMPLETED and state.finished
    events = await journal(store, state)
    assert kinds(events)[-2:] == ["→completed", "run.completed"]
    fold(events, state.run_id)


class Deadlines:
    """Délais que la boucle ouvre pour ses étapes : de quoi en faire tomber un à l'instant voulu."""

    def __init__(self) -> None:
        self.opened: list[asyncio.Timeout] = []

    def expire(self) -> None:
        """Fait tomber maintenant le dernier délai ouvert, s'il court encore."""
        if self.opened:
            with suppress(RuntimeError):
                self.opened[-1].reschedule(asyncio.get_running_loop().time())


@pytest.fixture
def deadlines(monkeypatch: pytest.MonkeyPatch) -> Deadlines:
    seen = Deadlines()
    real = asyncio.timeout

    def spy(delay: float | None) -> asyncio.Timeout:
        timeout = real(delay)
        # Les autres délais (appel du modèle, outils) ne sont pas celui du run.
        frame = inspect.currentframe()
        if frame is not None and frame.f_back is not None:
            if frame.f_back.f_globals["__name__"] == "loom_ia.engine.loop":
                seen.opened.append(timeout)
        return timeout

    monkeypatch.setattr(asyncio, "timeout", spy)
    return seen


async def test_the_deadline_cannot_fall_while_the_final_answer_is_released(
    deadlines: Deadlines,
) -> None:
    """``after_guards`` : la réponse part une fois le run clos, hors du délai."""
    delivered: list[str] = []

    async def slow(chunk: ModelChunk) -> None:
        deadlines.expire()
        await asyncio.sleep(0)  # un point où l'échéance couperait, si elle courait encore
        if isinstance(chunk, TextDelta):
            delivered.append(chunk.text)

    ctx = context(
        InMemoryEventStore(),
        scripted(Message.assistant("Bonjour")),
        timeout=30.0,
        stream_output="after_guards",
        on_chunk=slow,
    )
    run = await begin_run(ctx, "?")

    state = await drive(ctx, run.run_id)

    assert state.status is RunStatus.COMPLETED and state.finished
    assert delivered == ["Bonjour"]
    fold(await journal(ctx.store, run), run.run_id)


async def test_a_timeout_error_that_is_not_the_deadline_is_not_a_timeout_of_the_run() -> None:
    """Un consommateur de flux qui lève ``TimeoutError`` n'est pas l'échéance du run."""

    async def full(chunk: ModelChunk) -> None:
        raise TimeoutError("file de diffusion pleine")

    ctx = context(
        InMemoryEventStore(),
        scripted(Message.assistant("Bonjour")),
        stream_output="after_guards",
        on_chunk=full,
    )
    run = await begin_run(ctx, "?")

    with pytest.raises(TimeoutError, match="file de diffusion pleine"):
        await drive(ctx, run.run_id)

    events = await journal(ctx.store, run)
    # La clôture est intacte : le run est terminé, et rien n'a été écrit après.
    assert kinds(events)[-2:] == ["→completed", "run.completed"]
    assert fold(events, run.run_id).status is RunStatus.COMPLETED


class AckLost(InMemoryEventStore):
    """Persiste une transition, puis laisse tomber le délai avant d'en rendre l'accusé."""

    def __init__(self, deadlines: Deadlines, to_state: RunStatus) -> None:
        super().__init__()
        self.deadlines = deadlines
        self.to_state = to_state
        self.armed = True

    async def append(
        self, drafts: Sequence[EventDraft], *, expected_seq: int | None
    ) -> list[Event]:
        events = await super().append(drafts, expected_seq=expected_seq)
        if self.armed and any(
            isinstance(d.payload, RunTransitioned) and d.payload.to_state is self.to_state
            for d in drafts
        ):
            self.armed = False
            self.deadlines.expire()
            await asyncio.sleep(0)  # l'échéance coupe ici : l'écriture est déjà au journal
        return events


@pytest.mark.parametrize(
    ("to_state", "replies", "expected"),
    [
        # Le délai tombe sur la transition finale : le run est mené à son terme.
        (RunStatus.COMPLETED, [Message.assistant("Bonjour")], RunStatus.COMPLETED),
        # Il tombe sur un état en cours : l'échec part de l'état réel, pas de l'ancien.
        (
            RunStatus.AWAITING_TOOLS,
            [tool_call_message(("c1", "calculer", {"expr": "1+1"})), Message.assistant("2")],
            RunStatus.FAILED,
        ),
    ],
)
async def test_a_deadline_falling_on_a_persisted_write_reads_the_journal_again(
    deadlines: Deadlines, to_state: RunStatus, replies: list[Message], expected: RunStatus
) -> None:
    store = AckLost(deadlines, to_state)
    ctx = context(store, scripted(*replies), timeout=30.0)
    run = await begin_run(ctx, "?")

    state = await drive(ctx, run.run_id)

    assert state.status is expected and state.finished
    events = await journal(store, run)
    assert fold(events, run.run_id).status is expected
    closing = events[-1].payload
    if expected is RunStatus.FAILED:
        assert isinstance(closing, RunFailed) and closing.error_type == "timeout"
    else:
        assert isinstance(closing, RunCompleted)


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


@pytest.mark.parametrize("shared", [False, True], ids=["own_writer", "shared_writer"])
async def test_two_simultaneous_openings_of_a_run_write_it_once(
    store: EventStore, monkeypatch: pytest.MonkeyPatch, shared: bool
) -> None:
    """Deux ``begin_run`` du même ``run_id`` : le perdant n'écrit rien, le journal reste lisible."""
    reading = store.read
    meeting = asyncio.Barrier(2)
    checks = 0

    async def read(
        tenant_id: TenantId,
        session_id: SessionId,
        *,
        after_seq: int = 0,
        run_id: RunId | None = None,
    ) -> list[Event]:
        nonlocal checks
        found = await reading(tenant_id, session_id, after_seq=after_seq, run_id=run_id)
        if run_id is not None and checks < 2:
            checks += 1
            # Les deux ouvertures ont vu « personne » avant que l'une n'écrive.
            async with asyncio.timeout(10):
                await meeting.wait()
        return found

    monkeypatch.setattr(store, "read", read)
    ctx = context(store, scripted(Message.assistant("ok")))
    session = SessionId("s-1") if shared else None
    writer = await SessionWriter.open(store, DEFAULT_TENANT, session) if session else None

    outcomes = await asyncio.gather(
        *(
            begin_run(ctx, "?", session_id=session, run_id=RunId("r-1"), writer=writer)
            for _ in range(2)
        ),
        return_exceptions=True,
    )

    refused = [o for o in outcomes if isinstance(o, RunExists)]
    opened = [o for o in outcomes if isinstance(o, RunState)]
    assert len(opened) == 1 and len(refused) == 1
    assert isinstance(refused[0], ValueError) and "r-1 existe déjà" in str(refused[0])
    events = await store.read(DEFAULT_TENANT, session or SessionId("r-1"))
    assert [e.type for e in events] == ["run.started", "message.user"]
    final = await drive(ctx, RunId("r-1"), session_id=session)
    assert final.status is RunStatus.COMPLETED


async def test_transitions_are_logged(store: EventStore, caplog: pytest.LogCaptureFixture) -> None:
    ctx = context(store, scripted(Message.assistant("ok")))
    with caplog.at_level(logging.INFO, logger="loom_ia.engine.loop"):
        state = await drive(ctx, (await begin_run(ctx, "?")).run_id)
    [record] = [r for r in caplog.records if r.message.startswith("Transition")]
    assert record.message == "Transition ready_for_model → completed (agent demo)"
    assert record.__dict__["run_id"] == state.run_id


async def test_step_does_nothing_while_an_approval_is_awaited(store: EventStore) -> None:
    """Un run qui attend un humain n'avance pas tout seul."""
    ctx = context(store, scripted())
    state = await begin_run(ctx, "?")
    en_attente = state.model_copy(
        update={
            "status": RunStatus.PAUSED,
            "approvals": (PendingApproval(call_id="c1", tool_name="envoyer_email"),),
        }
    )
    assert [d async for d in step(en_attente, ctx)] == []


async def test_a_parent_whose_child_can_go_on_replays_its_call(store: EventStore) -> None:
    """``step`` n'est atteint que si l'enfant peut repartir : le parent rejoue."""
    ctx = context(store, scripted())
    state = await begin_run(ctx, "?")
    parent = state.model_copy(update={"status": RunStatus.WAITING_CHILD})
    drafts = [d async for d in step(parent, ctx)]
    assert [d.payload.type for d in drafts] == ["run.transitioned"]
    transition = drafts[0].payload
    assert isinstance(transition, RunTransitioned)
    assert transition.to_state is RunStatus.AWAITING_TOOLS


async def test_a_paused_run_whose_approvals_are_settled_goes_back_to_its_tools(
    store: EventStore,
) -> None:
    """Plus rien n'attend : le run repart où il s'était arrêté."""
    ctx = context(store, scripted())
    state = await begin_run(ctx, "?")
    tranchee = PendingApproval(
        call_id="c1",
        tool_name="envoyer_email",
        outcome=ApprovalOutcome(verdict="granted", by="denis"),
    )
    repris = state.model_copy(update={"status": RunStatus.PAUSED, "approvals": (tranchee,)})
    drafts = [d async for d in step(repris, ctx)]
    assert [d.payload.type for d in drafts] == ["run.transitioned"]
    transition = drafts[0].payload
    assert isinstance(transition, RunTransitioned)
    assert transition.to_state is RunStatus.AWAITING_TOOLS


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


async def test_results_follow_the_call_order_in_requests(store: EventStore) -> None:
    released = asyncio.Event()

    @tool
    async def lent() -> str:
        """Répond après rapide."""
        await released.wait()
        return "lent"

    @tool
    async def rapide() -> str:
        """Répond tout de suite."""
        released.set()
        return "rapide"

    model = ScriptedModel(
        tool_call_message(("c1", "lent", {}), ("c2", "rapide", {})),
        Message.assistant("Fini."),
    )
    ctx = context(store, model, tools=ToolExecutor([lent, rapide]))
    state = await drive(ctx, (await begin_run(ctx, "?")).run_id)

    def order(messages: Sequence[Message]) -> list[str]:
        return [b.call_id for m in messages for b in m.blocks if isinstance(b, ToolResultBlock)]

    # Le journal garde l'ordre d'arrivée ; la requête suit l'ordre des appels.
    assert order(state.messages) == ["c2", "c1"]
    sent = model.requests[1]
    assert order(sent.messages) == ["c1", "c2"]
    responded = [
        e.payload for e in await journal(store, state) if isinstance(e.payload, ModelResponded)
    ]
    assert responded[1].request_hash == sent.request_hash()


def test_in_call_order_keeps_other_messages_in_place() -> None:
    def result(call_id: str) -> Message:
        return Message(
            role="tool", blocks=(ToolResultBlock(call_id=call_id, output=ToolOutput.text(call_id)),)
        )

    first = tool_call_message(("a", "x", {}), ("b", "x", {}))
    second = tool_call_message(("c", "x", {}), ("d", "x", {}))
    messages = (
        Message.user("?"),
        first,
        result("b"),
        result("a"),
        second,
        result("inconnu"),
        result("d"),
        result("c"),
    )
    assert in_call_order(messages) == (
        Message.user("?"),
        first,
        result("a"),
        result("b"),
        second,
        result("c"),
        result("d"),
        result("inconnu"),
    )
    assert in_call_order(()) == ()
