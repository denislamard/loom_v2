# SPDX-License-Identifier: Apache-2.0
"""Exécuteur d'outils : lot parallèle, contrôles, timeout, reprise."""

import asyncio
import logging
from contextlib import aclosing
from dataclasses import dataclass, field

import pytest
from jsonschema.exceptions import SchemaError
from pydantic import JsonValue

from loom_ia.core.events import ToolCalled, ToolCompleted
from loom_ia.core.model import (
    INVALID_JSON_KEY,
    CallerContext,
    PendingCall,
    RunState,
    RunStatus,
    TenantId,
    ToolOutput,
    ToolSpec,
)
from loom_ia.core.ports import ToolContext, ToolError
from loom_ia.core.projections import fold
from loom_ia.engine import UNKNOWN_STATE, ToolExecutor
from loom_ia.engine.executor import ToolEvent
from loom_ia.testing import RunJournal
from loom_ia.tools import tool


@dataclass
class RecordingTool:
    """Outil minimal qui note ses appels et exécute ``action``."""

    spec: ToolSpec
    result: ToolOutput = field(default_factory=lambda: ToolOutput.text("ok"))
    error: Exception | None = None
    delay: float = 0.0
    calls: list[tuple[dict[str, JsonValue], ToolContext]] = field(
        default_factory=list[tuple[dict[str, JsonValue], ToolContext]]
    )
    cancelled: bool = False

    async def invoke(self, arguments: dict[str, JsonValue], context: ToolContext) -> ToolOutput:
        self.calls.append((arguments, context))
        try:
            await asyncio.sleep(self.delay)
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        if self.error is not None:
            raise self.error
        return self.result


def spec(name: str, **options: object) -> ToolSpec:
    schema: dict[str, JsonValue] = {
        "type": "object",
        "properties": {"x": {"type": "integer"}},
        "additionalProperties": False,
    }
    return ToolSpec.model_validate(
        {"name": name, "description": name, "kind": "python", "input_schema": schema, **options}
    )


def awaiting(*calls: PendingCall, tenant: str = "default") -> RunState:
    journal = RunJournal(agent="demo", tenant_id=TenantId(tenant))
    journal.start("?", context=CallerContext(tenant_id=TenantId(tenant), user_id="u1"))
    events = [draft.to_event(seq) for seq, draft in enumerate(journal.take(), start=1)]
    return fold(events, journal.run_id).model_copy(
        update={"status": RunStatus.AWAITING_TOOLS, "pending_calls": calls}
    )


def call(call_id: str, name: str, *, started: bool = False, **arguments: JsonValue) -> PendingCall:
    return PendingCall(call_id=call_id, name=name, arguments=arguments, started=started)


async def collect(executor: ToolExecutor, state: RunState) -> list[ToolEvent]:
    return [event async for event in executor.run_batch(state)]


def completed(events: list[ToolEvent]) -> dict[str, ToolOutput]:
    return {e.call_id: e.output for e in events if isinstance(e, ToolCompleted)}


async def test_batch_runs_in_parallel_and_reports_as_soon_as_possible() -> None:
    both_started = asyncio.Event()
    started: list[str] = []

    @tool
    async def lent(x: int) -> str:
        """Lent."""
        started.append("lent")
        if len(started) == 2:
            both_started.set()
        await both_started.wait()
        await asyncio.sleep(0.02)
        return "lent"

    @tool
    async def rapide(x: int) -> str:
        """Rapide."""
        started.append("rapide")
        if len(started) == 2:
            both_started.set()
        await both_started.wait()
        return "rapide"

    executor = ToolExecutor([lent, rapide])
    async with asyncio.timeout(2):
        events = await collect(
            executor, awaiting(call("c1", "lent", x=1), call("c2", "rapide", x=2))
        )

    assert [(type(e).__name__, e.call_id) for e in events] == [
        ("ToolCalled", "c1"),
        ("ToolCalled", "c2"),
        ("ToolCompleted", "c2"),
        ("ToolCompleted", "c1"),
    ]
    first = events[0]
    assert isinstance(first, ToolCalled)
    assert (first.tool_name, first.tool_kind, first.arguments, first.resumed) == (
        "lent",
        "python",
        {"x": 1},
        False,
    )
    last = events[-1]
    assert isinstance(last, ToolCompleted)
    assert last.latency_ms >= 20
    assert last.size == len(ToolOutput.text("lent").model_dump_json())


async def test_rejected_calls_are_not_started() -> None:
    target = RecordingTool(spec("cible"))
    executor = ToolExecutor([target])
    events = await collect(
        executor,
        awaiting(
            call("c1", "inconnu"),
            PendingCall(call_id="c2", name="cible", arguments={INVALID_JSON_KEY: '{"x": '}),
            call("c3", "cible", x="deux", y=1),
        ),
    )
    assert all(isinstance(e, ToolCompleted) for e in events)
    assert target.calls == []
    outputs = completed(events)
    assert all(o.is_error for o in outputs.values())
    assert outputs["c1"].as_text == "Outil inconnu : 'inconnu'. Outils disponibles : cible."
    assert outputs["c2"].as_text.startswith("Arguments illisibles")
    assert outputs["c3"].as_text == (
        "Arguments non conformes au schéma de l'outil :\n"
        "- (racine) : Additional properties are not allowed ('y' was unexpected)\n"
        "- x : 'deux' is not of type 'integer'"
    )
    assert all(isinstance(e, ToolCompleted) and e.latency_ms == 0 for e in events)


async def test_validation_can_be_disabled() -> None:
    target = RecordingTool(spec("cible"))
    events = await collect(
        ToolExecutor([target], validate_arguments=False), awaiting(call("c1", "cible", x="a"))
    )
    assert target.calls[0][0] == {"x": "a"}
    assert not completed(events)["c1"].is_error


async def test_unknown_tool_without_any_tool() -> None:
    outputs = completed(await collect(ToolExecutor(), awaiting(call("c1", "t"))))
    assert outputs["c1"].as_text.endswith("Outils disponibles : aucun.")


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (ToolError("x doit être positif"), "x doit être positif"),
        (ValueError("mauvais"), "Erreur de l'outil cible : ValueError: mauvais"),
        (TimeoutError("api lente"), "Erreur de l'outil cible : TimeoutError: api lente"),
    ],
)
async def test_failures_become_error_results(
    error: Exception, expected: str, caplog: pytest.LogCaptureFixture
) -> None:
    target = RecordingTool(spec("cible"), error=error)
    with caplog.at_level(logging.WARNING, logger="loom_ia.engine.executor"):
        outputs = completed(await collect(ToolExecutor([target]), awaiting(call("c1", "cible"))))
    assert outputs["c1"] == ToolOutput.error(expected)
    logged = [r for r in caplog.records if r.message == "Échec de l'outil cible"]
    assert len(logged) == (0 if isinstance(error, ToolError) else 1)


async def test_timeouts() -> None:
    own = RecordingTool(spec("propre", timeout=0.01), delay=1)
    default = RecordingTool(spec("defaut"), delay=1)
    unlimited = RecordingTool(spec("libre"), delay=0.03)
    executor = ToolExecutor([own, default, unlimited], default_timeout=0.02)
    outputs = completed(
        await collect(executor, awaiting(call("c1", "propre"), call("c2", "defaut")))
    )
    assert outputs["c1"] == ToolOutput.error("Délai dépassé : pas de réponse en 0.01 s.")
    assert outputs["c2"] == ToolOutput.error("Délai dépassé : pas de réponse en 0.02 s.")
    assert own.cancelled and default.cancelled

    executor.default_timeout = None
    outputs = completed(await collect(executor, awaiting(call("c3", "libre"))))
    assert not outputs["c3"].is_error


async def test_interrupted_calls_follow_the_resume_rule() -> None:
    reader = RecordingTool(spec("lire"))
    idempotent = RecordingTool(spec("payer", side_effects="irreversible", idempotent=True))
    sender = RecordingTool(spec("envoyer", side_effects="irreversible"))
    executor = ToolExecutor([reader, idempotent, sender])
    events = await collect(
        executor,
        awaiting(
            call("c1", "lire", started=True),
            call("c2", "payer", started=True),
            call("c3", "envoyer", started=True),
            call("c4", "envoyer"),
        ),
    )
    called = {e.call_id: e.resumed for e in events if isinstance(e, ToolCalled)}
    assert called == {"c1": True, "c2": True, "c4": False}
    assert completed(events)["c3"] == ToolOutput.error(UNKNOWN_STATE)
    assert [c[1].call_id for c in sender.calls] == ["c4"]
    assert len(reader.calls) == len(idempotent.calls) == 1


async def test_tool_context() -> None:
    target = RecordingTool(spec("cible"))
    state = awaiting(call("c1", "cible", x=1), tenant="acme")
    await collect(ToolExecutor([target]), state)
    arguments, context = target.calls[0]
    assert arguments == {"x": 1}
    assert (context.tenant_id, context.session_id, context.run_id) == (
        "acme",
        state.session_id,
        state.run_id,
    )
    assert (context.call_id, context.agent, context.caller.user_id) == ("c1", "demo", "u1")


async def test_closing_the_batch_cancels_running_tools() -> None:
    fast = RecordingTool(spec("rapide"))
    slow = RecordingTool(spec("lent"), delay=5)
    executor = ToolExecutor([fast, slow])
    seen: list[ToolEvent] = []
    async with aclosing(
        executor.run_batch(awaiting(call("c1", "rapide"), call("c2", "lent")))
    ) as events:
        async for event in events:
            seen.append(event)
            if isinstance(event, ToolCompleted):
                break
    assert [type(e).__name__ for e in seen] == ["ToolCalled", "ToolCalled", "ToolCompleted"]
    assert slow.cancelled


def test_registration() -> None:
    first, second = RecordingTool(spec("a")), RecordingTool(spec("b"))
    executor = ToolExecutor([first])
    executor.add(second)
    assert executor.get("b") is second
    assert executor.get("c") is None
    assert [d.name for d in executor.definitions()] == ["a", "b"]
    assert executor.specs == (first.spec, second.spec)
    with pytest.raises(ValueError, match="déjà déclaré"):
        executor.add(RecordingTool(spec("a")))
    broken = ToolSpec(name="casse", description="x", kind="python", input_schema={"type": "objet"})
    with pytest.raises(SchemaError):
        executor.add(RecordingTool(broken))
    assert executor.get("casse") is None


class Fatal(BaseException):
    """Exception hors de ``Exception`` : elle ne devient pas un résultat d'erreur."""


@dataclass
class FatalTool:
    spec: ToolSpec

    async def invoke(self, arguments: dict[str, JsonValue], context: ToolContext) -> ToolOutput:
        raise Fatal("arrêt")


async def test_fatal_error_interrupts_the_batch() -> None:
    executor = ToolExecutor([FatalTool(spec("fatal"))])
    with pytest.raises(Fatal, match="arrêt"):
        await collect(executor, awaiting(call("c1", "fatal")))
