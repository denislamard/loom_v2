# SPDX-License-Identifier: Apache-2.0
"""Politiques dans la boucle : effet de chaque décision à chaque point, journal, reprise (J3.1)."""

from collections.abc import AsyncIterator, Callable, Sequence
from pathlib import Path

import pytest

from loom_ia.adapters.stores import InMemoryEventStore, JsonlEventStore
from loom_ia.core.events import (
    Event,
    EventDraft,
    EventQuery,
    PolicyDecided,
    RunCompleted,
    RunFailed,
    RunTransitioned,
    StepStarted,
    ToolCalled,
    ToolCompleted,
    UserMessage,
)
from loom_ia.core.model import (
    CONTINUE,
    REPAIR_PREFIX,
    AfterModel,
    AfterTool,
    BeforeModel,
    BeforeTool,
    Decision,
    Deny,
    Fail,
    Message,
    ModelSpec,
    OnOutput,
    PolicyContext,
    Pricing,
    Replace,
    Retry,
    RetryPolicy,
    RunId,
    RunState,
    RunStatus,
    SessionId,
    Stop,
    TenantId,
    ToolOutput,
    Usage,
)
from loom_ia.core.ports import EventStore
from loom_ia.core.projections import history
from loom_ia.engine import BoundPolicy, Policies, RunContext, ToolExecutor, begin_run, drive
from loom_ia.policies import FunctionPolicy, policy, require_tool
from loom_ia.testing import ScriptedModel, tool_call_message
from loom_ia.tools import ConfiguredTool, tool

USAGE = Usage(input_tokens=100, output_tokens=10)
SPEC = ModelSpec(
    id="FAKE",
    sdk="fake",
    model="fake-1",
    pricing=Pricing(input=1.0, output=5.0),
    retry=RetryPolicy(initial_delay=0),
)
executed: list[str] = []


@tool
def calculer(expr: str) -> str:
    """Évalue une expression arithmétique."""
    executed.append(expr)
    return str(eval(expr, {"__builtins__": {}}))


@tool
def rediger(sujet: str) -> str:
    """Rédige un texte."""
    return f"Texte sur {sujet}"


# Sortie transmise telle quelle comme réponse finale (#13).
REDIGER = ConfiguredTool(tool=rediger, spec=rediger.spec.model_copy(update={"terminal": True}))


@pytest.fixture(autouse=True)
def reset() -> None:
    executed.clear()


@pytest.fixture(params=["memory", "jsonl"])
async def store(request: pytest.FixtureRequest, tmp_path: Path) -> AsyncIterator[EventStore]:
    instance: EventStore = (
        InMemoryEventStore() if request.param == "memory" else JsonlEventStore(tmp_path)
    )
    yield instance
    await instance.aclose()


def bind(*policies: FunctionPolicy, max_attempts: int = 1) -> Policies:
    return Policies(
        BoundPolicy(policy=p, name=p.name, points=p.points, max_attempts=max_attempts)
        for p in policies
    )


def context(
    store: EventStore, model: ScriptedModel, policies: Policies, **options: object
) -> RunContext:
    defaults: dict[str, object] = {
        "tools": ToolExecutor([calculer, REDIGER]),
        "system": "Tu calcules.",
        "model_spec": SPEC,
        "max_iterations": 4,
    }
    return RunContext(
        agent="demo",
        store=store,
        model=model,
        policies=policies,
        **(defaults | options),  # pyright: ignore[reportArgumentType]
    )


def scripted(*replies: Message) -> ScriptedModel:
    return ScriptedModel(*replies, usage=USAGE)


async def run(ctx: RunContext, prompt: str = "Combien font 1 + 1 ?") -> RunState:
    return await drive(ctx, (await begin_run(ctx, prompt)).run_id)


async def journal(store: EventStore, state: RunState) -> list[Event]:
    return await store.read(state.context.tenant_id, state.session_id)


def kinds(events: Sequence[Event]) -> list[str]:
    labels: list[str] = []
    for event in events:
        match event.payload:
            case StepStarted(effect=effect):
                labels.append(f"step:{effect}")
            case RunTransitioned(to_state=target):
                labels.append(f"→{target}")
            case PolicyDecided(policy=name, decision=decision):
                labels.append(f"policy:{name}:{decision}")
            case UserMessage(kind="repair"):
                labels.append("repair")
            case _:
                labels.append(event.type)
    return labels


def decided(events: Sequence[Event]) -> list[PolicyDecided]:
    return [e.payload for e in events if isinstance(e.payload, PolicyDecided)]


# --- before_model ----------------------------------------------------------------


async def test_require_tool_replaces_the_first_request(store: EventStore) -> None:
    model = scripted(
        tool_call_message(("c1", "calculer", {"expr": "1+1"})),
        Message.assistant("2"),
    )
    policies = Policies(
        [BoundPolicy(policy=require_tool, name=require_tool.name, points=require_tool.points)]
    )
    state = await run(context(store, model, policies))

    assert state.status is RunStatus.COMPLETED
    assert [r.tool_choice for r in model.requests] == ["required", "auto"]
    events = await journal(store, state)
    assert kinds(events)[2:5] == [
        "step:model_call",
        "policy:loom.require_tool:replace",
        "model.responded",
    ]
    step, replace, responded = events[2:5]
    assert replace.span_id == step.span_id == responded.span_id
    assert replace.category == "policy" and replace.status == "ok"
    assert replace.facets == {
        "policy": "loom.require_tool",
        "point": "before_model",
        "decision": "replace",
    }


async def test_stop_before_the_model_forces_an_answer(store: EventStore) -> None:
    @policy(points=["before_model"], decisions=["stop"])
    def plafond(subject: BeforeModel) -> Decision:
        if subject.state.iterations >= 1:
            return Stop("plafond d'un appel atteint")
        return CONTINUE

    model = scripted(
        tool_call_message(("c1", "calculer", {"expr": "1+1"})),
        Message.assistant("Réponse : 2"),
    )
    state = await run(context(store, model, bind(plafond)))

    assert state.status is RunStatus.COMPLETED
    assert state.output == Message.assistant("Réponse : 2")
    assert [r.tool_choice for r in model.requests] == ["auto", "none"]
    events = await journal(store, state)
    assert kinds(events)[11:] == [
        "step:model_call",
        "policy:plafond:stop",
        "step.completed",
        "→finalizing",
        "step:finalize",
        "model.responded",
        "step.completed",
        "→completed",
        "run.completed",
    ]
    transition = events[14].payload
    assert isinstance(transition, RunTransitioned)
    assert (transition.cause_type, transition.cause_event_id) == (
        "policy.decided",
        events[12].event_id,
    )


async def test_fail_before_the_model_fails_the_run(store: EventStore) -> None:
    @policy(points=["before_model"], decisions=["fail"])
    def interdit(subject: BeforeModel) -> Decision:
        return Fail("agent suspendu")

    model = scripted()
    state = await run(context(store, model, bind(interdit)))

    assert state.status is RunStatus.FAILED
    assert (state.error_type, state.error) == ("policy.interdit", "agent suspendu")
    assert model.requests == []
    events = await journal(store, state)
    assert kinds(events)[2:] == [
        "step:model_call",
        "policy:interdit:fail",
        "step.completed",
        "→failed",
        "run.failed",
    ]
    assert events[3].status == "error"


async def test_finalizing_keeps_its_answer_without_tools(store: EventStore) -> None:
    @policy(points=["before_model"], decisions=["stop", "replace"])
    def insistante(subject: BeforeModel) -> Decision:
        if subject.finalizing:
            return Replace(subject.request.model_copy(update={"tool_choice": "required"}))
        return Stop("tout de suite")

    model = scripted(Message.assistant("Rien à faire."))
    state = await run(context(store, model, bind(insistante)))
    assert state.status is RunStatus.COMPLETED
    assert [r.tool_choice for r in model.requests] == ["none"]


# --- after_model et on_output : réparation de l'orchestrateur ----------------------------


async def test_retry_after_the_final_answer_asks_for_a_repair(store: EventStore) -> None:
    @policy(points=["on_output"], decisions=["retry"])
    def cite_le_resultat(subject: OnOutput) -> Decision:
        if "2" not in subject.output.text:
            return Retry("Donne le résultat du calcul.", tools=False)
        return CONTINUE

    model = scripted(Message.assistant("C'est facile."), Message.assistant("1 + 1 = 2"))
    state = await run(context(store, model, bind(cite_le_resultat)))

    assert state.status is RunStatus.COMPLETED
    assert state.output == Message.assistant("1 + 1 = 2")
    assert (state.iterations, state.retries) == (2, {"cite_le_resultat": 1})
    repair = model.requests[1]
    assert repair.tool_choice == "none"
    assert [m.role for m in repair.messages] == ["user", "assistant", "user"]
    assert repair.messages[-1].text == (
        f"{REPAIR_PREFIX} (cite_le_resultat) : Donne le résultat du calcul."
    )
    events = await journal(store, state)
    assert kinds(events)[5:] == [
        "policy:cite_le_resultat:retry",
        "repair",
        "step:model_call",
        "model.responded",
        "step.completed",
        "→completed",
        "run.completed",
    ]
    retry = events[5].payload
    assert isinstance(retry, PolicyDecided)
    assert (retry.point, retry.attempt, retry.tools) == ("on_output", 1, False)
    message = events[6]
    assert message.facets == {"kind": "repair"}
    assert isinstance(message.payload, UserMessage) and message.payload.policy == "cite_le_resultat"
    # La tentative refusée et le diagnostic restent au journal, pas dans l'historique.
    assert history(events) == [Message.user("Combien font 1 + 1 ?"), Message.assistant("1 + 1 = 2")]


async def test_retry_after_a_response_with_tool_calls_closes_them(store: EventStore) -> None:
    @policy(points=["after_model"], decisions=["retry"])
    def pas_d_eval(subject: AfterModel) -> Decision:
        if any("*" in str(c.arguments.get("expr")) for c in subject.response.tool_calls):
            return Retry("Pas de multiplication : additionne.")
        return CONTINUE

    model = scripted(
        tool_call_message(("c1", "calculer", {"expr": "2*3"}), text="Je calcule."),
        tool_call_message(("c2", "calculer", {"expr": "3+3"})),
        Message.assistant("6"),
    )
    state = await run(context(store, model, bind(pas_d_eval)))

    assert state.status is RunStatus.COMPLETED and executed == ["3+3"]
    assert model.requests[1].tool_choice == "auto"
    assert [m.role for m in model.requests[1].messages] == ["user", "assistant", "tool", "user"]
    events = await journal(store, state)
    assert kinds(events)[2:9] == [
        "step:model_call",
        "model.responded",
        "step.completed",
        "policy:pas_d_eval:retry",
        "tool.completed",
        "repair",
        "step:model_call",
    ]
    closed = events[6].payload
    assert isinstance(closed, ToolCompleted) and closed.output.is_error
    assert "réponse refusée par le contrôle pas_d_eval" in closed.output.as_text
    assert not any(isinstance(e.payload, ToolCalled) and e.payload.call_id == "c1" for e in events)
    kept = history(events)
    assert [m.role for m in kept] == ["user", "assistant", "tool", "assistant"]
    assert kept[1] == tool_call_message(("c2", "calculer", {"expr": "3+3"}))


async def test_stop_after_the_model_skips_its_tool_calls(store: EventStore) -> None:
    @policy(points=["after_model"], decisions=["stop"])
    def assez(subject: AfterModel) -> Decision:
        return Stop("assez d'outils") if subject.response.tool_calls else CONTINUE

    model = scripted(
        tool_call_message(("c1", "calculer", {"expr": "1+1"})),
        Message.assistant("Je ne peux pas calculer."),
    )
    state = await run(context(store, model, bind(assez)))

    assert state.status is RunStatus.COMPLETED and executed == []
    assert state.output == Message.assistant("Je ne peux pas calculer.")
    events = await journal(store, state)
    assert kinds(events)[5:9] == [
        "policy:assez:stop",
        "tool.completed",
        "→finalizing",
        "step:finalize",
    ]
    closed = events[6].payload
    assert isinstance(closed, ToolCompleted)
    assert closed.output.as_text == "Non exécuté : run arrêté (assez d'outils)."


async def test_exhausted_repairs_fail_the_run(store: EventStore) -> None:
    @policy(points=["on_output"], decisions=["retry"])
    def jamais_content(subject: OnOutput) -> Decision:
        return Retry("Encore.")

    model = scripted(Message.assistant("a"), Message.assistant("b"), Message.assistant("c"))
    ctx = context(store, model, bind(jamais_content, max_attempts=2))
    state = await run(ctx)

    assert state.status is RunStatus.FAILED
    assert state.error is not None and "2 réparation(s)" in state.error
    assert len(model.requests) == 3
    events = await journal(store, state)
    assert [d.decision for d in decided(events)] == ["retry", "retry", "fail"]
    assert kinds(events)[-2:] == ["→failed", "run.failed"]


async def test_on_output_replaces_the_final_answer(store: EventStore) -> None:
    @policy(points=["on_output"], decisions=["replace"])
    def signe(subject: OnOutput) -> Decision:
        return Replace(f"{subject.output.text}\n-- loom")

    model = scripted(Message.assistant("2"))
    state = await run(context(store, model, bind(signe)))

    assert state.output == Message.assistant("2\n-- loom")
    events = await journal(store, state)
    closing = events[-1].payload
    assert isinstance(closing, RunCompleted) and closing.output == Message.assistant("2\n-- loom")
    assert history(events)[-1] == Message.assistant("2\n-- loom")


async def test_on_output_on_a_terminal_output(store: EventStore) -> None:
    @policy(points=["on_output"], decisions=["replace", "retry"])
    def relecture(subject: OnOutput) -> Decision:
        assert (subject.source, subject.tool) == ("terminal", "rediger")
        if "devis" not in subject.output.text:
            return Retry("Parle du devis.")
        return Replace(subject.output.text.upper())

    model = scripted(
        tool_call_message(("c1", "rediger", {"sujet": "la pluie"})),
        tool_call_message(("c2", "rediger", {"sujet": "le devis"})),
    )
    state = await run(context(store, model, bind(relecture)))

    assert state.status is RunStatus.COMPLETED
    assert state.terminal_call_id == "c2"
    assert state.output == Message.assistant("TEXTE SUR LE DEVIS")
    events = await journal(store, state)
    assert kinds(events)[10:14] == [
        "policy:relecture:retry",
        "repair",
        "→ready_for_model",
        "step:model_call",
    ]
    closing = events[-1].payload
    assert isinstance(closing, RunCompleted)
    assert closing.output == Message.assistant("TEXTE SUR LE DEVIS")
    assert closing.output_event_id is not None
    assert history(events)[-1] == Message.assistant("TEXTE SUR LE DEVIS")


async def test_terminal_called_with_other_tools_is_journaled(store: EventStore) -> None:
    model = scripted(
        tool_call_message(("c1", "rediger", {"sujet": "x"}), ("c2", "calculer", {"expr": "1"})),
        Message.assistant("Fini."),
    )
    state = await run(context(store, model, Policies()))
    assert state.output == Message.assistant("Fini.")
    [notice] = decided(await journal(store, state))
    assert (notice.policy, notice.point, notice.decision, notice.call_id) == (
        "loom.terminal",
        "after_tool",
        "continue",
        "c1",
    )
    assert "appelé avec d'autres outils" in notice.reason
    assert notice.event_status == "warning"


# --- before_tool et after_tool ------------------------------------------------------


async def test_before_tool_replaces_or_denies_calls(store: EventStore) -> None:
    @policy(points=["before_tool"], decisions=["replace", "deny"])
    def expressions(subject: BeforeTool) -> Decision:
        expr = str(subject.arguments.get("expr", ""))
        if "import" in expr:
            return Deny("expression interdite")
        return Replace({"expr": expr.replace(" ", "")}, reason="espaces retirés")

    model = scripted(
        tool_call_message(
            ("c1", "calculer", {"expr": "1 + 1"}), ("c2", "calculer", {"expr": "import os"})
        ),
        Message.assistant("2"),
    )
    state = await run(context(store, model, bind(expressions)))

    assert executed == ["1+1"]
    events = await journal(store, state)
    batch = kinds(events)[7:13]
    assert batch == [
        "policy:expressions:replace",
        "policy:expressions:deny",
        "tool.completed",
        "tool.called",
        "tool.completed",
        "step.completed",
    ]
    replaced, _, refusal, called = (e.payload for e in events[7:11])
    assert isinstance(replaced, PolicyDecided) and replaced.arguments == {"expr": "1+1"}
    assert isinstance(called, ToolCalled) and called.arguments == {"expr": "1 + 1"}
    assert isinstance(refusal, ToolCompleted) and refusal.call_id == "c2"
    assert refusal.output == ToolOutput.error("Appel refusé (expressions) : expression interdite")
    assert events[7].span_id == events[10].span_id
    assert events[8].span_id == events[9].span_id


async def test_invalid_replaced_arguments_fail_the_run(store: EventStore) -> None:
    @policy(points=["before_tool"], decisions=["replace"])
    def casse(subject: BeforeTool) -> Decision:
        return Replace({"expression": "1"})

    model = scripted(tool_call_message(("c1", "calculer", {"expr": "1"})))
    state = await run(context(store, model, bind(casse)))
    assert state.status is RunStatus.FAILED and executed == []
    assert state.error is not None and "Replace : arguments refusés" in state.error


async def test_fail_before_a_tool_launches_nothing(store: EventStore) -> None:
    @policy(points=["before_tool"], decisions=["fail"])
    def coupe(subject: BeforeTool) -> Decision:
        return Fail("budget épuisé") if subject.call.call_id == "c2" else CONTINUE

    model = scripted(
        tool_call_message(("c1", "calculer", {"expr": "1"}), ("c2", "calculer", {"expr": "2"}))
    )
    state = await run(context(store, model, bind(coupe)))

    assert state.status is RunStatus.FAILED and executed == []
    assert (state.error_type, state.error) == ("policy.coupe", "budget épuisé")
    events = await journal(store, state)
    assert kinds(events)[6:] == [
        "step:tool_batch",
        "policy:coupe:fail",
        "step.completed",
        "→failed",
        "run.failed",
    ]


async def test_after_tool_replaces_or_refuses_results(store: EventStore) -> None:
    @policy(points=["after_tool"], decisions=["replace", "retry"])
    def verifie(subject: AfterTool) -> Decision:
        if subject.output.as_text == "4":
            return Retry("Résultat suspect.")
        return Replace(ToolOutput.text(f"= {subject.output.as_text}"))

    model = scripted(
        tool_call_message(("c1", "calculer", {"expr": "1+1"}), ("c2", "calculer", {"expr": "2+2"})),
        Message.assistant("Fini."),
    )
    state = await run(context(store, model, bind(verifie)))

    assert state.status is RunStatus.COMPLETED
    outputs = {
        e.payload.call_id: e.payload.output
        for e in await journal(store, state)
        if isinstance(e.payload, ToolCompleted)
    }
    assert outputs["c1"] == ToolOutput.text("= 2")
    refused = outputs["c2"]
    assert refused.is_error
    assert refused.as_text == "Résultat refusé (verifie) : Résultat suspect.\n4"
    # Seules les réparations de l'orchestrateur se comptent dans le run.
    assert state.retries == {}


async def test_fail_after_a_tool_fails_the_run_after_the_batch(store: EventStore) -> None:
    @policy(points=["after_tool"], decisions=["fail"])
    def fatal(subject: AfterTool) -> Decision:
        return Fail("résultat inacceptable")

    model = scripted(tool_call_message(("c1", "calculer", {"expr": "1+1"})))
    state = await run(context(store, model, bind(fatal)))

    assert state.status is RunStatus.FAILED and executed == ["1+1"]
    events = await journal(store, state)
    assert kinds(events)[6:] == [
        "step:tool_batch",
        "tool.called",
        "policy:fatal:fail",
        "tool.completed",
        "step.completed",
        "→failed",
        "run.failed",
    ]
    failure = events[-1].payload
    assert isinstance(failure, RunFailed) and failure.error_type == "policy.fatal"


# --- Reprise ----------------------------------------------------------------------


class Crash(Exception):
    """Plantage simulé après l'écriture d'un événement."""


class CrashingStore:
    """Store qui « plante » une fois, juste après avoir écrit l'événement visé."""

    def __init__(self, inner: EventStore, after: Callable[[Event], bool]) -> None:
        self.inner = inner
        self.after = after
        self.crashed = False

    async def append(
        self, drafts: Sequence[EventDraft], *, expected_seq: int | None
    ) -> list[Event]:
        events = await self.inner.append(drafts, expected_seq=expected_seq)
        if not self.crashed and any(self.after(e) for e in events):
            self.crashed = True
            raise Crash
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


async def test_a_decided_repair_survives_a_crash(store: EventStore) -> None:
    calls: list[int] = []

    @policy(points=["on_output"], decisions=["retry"])
    def une_fois(subject: OnOutput, context: PolicyContext) -> Decision:
        calls.append(context.attempt)
        return Retry("Précise.") if context.attempt == 0 else CONTINUE

    crashing = CrashingStore(store, lambda e: isinstance(e.payload, PolicyDecided))
    model = scripted(Message.assistant("Vague."), Message.assistant("Précis : 2."))
    ctx = context(crashing, model, bind(une_fois))  # pyright: ignore[reportArgumentType]
    started = await begin_run(ctx, "1 + 1 ?")
    with pytest.raises(Crash):
        await drive(ctx, started.run_id)

    state = await drive(context(store, model, bind(une_fois)), started.run_id)
    assert state.status is RunStatus.COMPLETED
    assert state.output == Message.assistant("Précis : 2.")
    # La réparation décidée avant le plantage n'est pas réévaluée à la reprise.
    assert calls == [0, 1]
    events = await journal(store, state)
    assert kinds(events).count("repair") == 1


async def test_replaced_arguments_are_reused_on_resume(store: EventStore) -> None:
    calls: list[str] = []

    @policy(points=["before_tool"], decisions=["replace"])
    def sans_espaces(subject: BeforeTool) -> Decision:
        calls.append(subject.call.call_id)
        return Replace({"expr": str(subject.arguments["expr"]).replace(" ", "")})

    crashing = CrashingStore(store, lambda e: isinstance(e.payload, ToolCalled))
    model = scripted(
        tool_call_message(("c1", "calculer", {"expr": "3 + 4"})), Message.assistant("7")
    )
    ctx = context(crashing, model, bind(sans_espaces))  # pyright: ignore[reportArgumentType]
    started = await begin_run(ctx, "3 + 4 ?")
    with pytest.raises(Crash):
        await drive(ctx, started.run_id)

    state = await drive(context(store, model, bind(sans_espaces)), started.run_id)
    assert state.status is RunStatus.COMPLETED
    assert calls == ["c1"] and executed == ["3+4"]


async def test_fail_after_the_model_fails_the_run(store: EventStore) -> None:
    @policy(points=["after_model"], decisions=["fail"])
    def hors_sujet(subject: AfterModel) -> Decision:
        return Fail("réponse hors sujet")

    model = scripted(tool_call_message(("c1", "calculer", {"expr": "1+1"})))
    state = await run(context(store, model, bind(hors_sujet)))
    assert state.status is RunStatus.FAILED and executed == []
    assert (state.error_type, state.error) == ("policy.hors_sujet", "réponse hors sujet")
    events = await journal(store, state)
    assert kinds(events)[5:] == ["policy:hors_sujet:fail", "→failed", "run.failed"]
