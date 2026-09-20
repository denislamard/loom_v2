# SPDX-License-Identifier: Apache-2.0
"""Coûts et budgets : paliers, ledger, rapport, loom.budget, part des sous-agents (J3.4)."""

from collections.abc import AsyncIterator, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from loom_ia.access.progress import describe as line_of
from loom_ia.adapters.stores import InMemoryEventStore, JsonlEventStore
from loom_ia.core.events import (
    BudgetExceeded,
    Event,
    PolicyDecided,
    RunCompleted,
    RunStarted,
    RunTransitioned,
    StepStarted,
    ToolCompleted,
)
from loom_ia.core.model import (
    Budgets,
    Message,
    ModelSpec,
    PriceTier,
    Pricing,
    RetryPolicy,
    RunBudget,
    RunState,
    RunStatus,
    SessionId,
    Spent,
    Usage,
)
from loom_ia.core.ports import EventStore
from loom_ia.core.projections import ledger, spent
from loom_ia.engine import (
    FINALIZE_HINT,
    AgentTool,
    BoundPolicy,
    Policies,
    RunContext,
    SubAgentDefinition,
    ToolExecutor,
    begin_run,
    drive,
)
from loom_ia.policies import CONTINUE, Decision, OnOutput, policy
from loom_ia.testing import ScriptedModel, tool_call_message
from loom_ia.tools import tool
from loom_ia.usage import BUDGET_POLICY, BudgetGuard, amount, render, usage_report

USAGE = Usage(input_tokens=1_000, output_tokens=100)
# 1 000 tokens à 1 $ + 100 tokens à 5 $ le million.
CALL_COST = 0.0015
SPEC = ModelSpec(
    id="MAIN",
    sdk="fake",
    model="main-1",
    pricing=Pricing(input=1.0, output=5.0),
    retry=RetryPolicy(initial_delay=0),
)
CHILD_SPEC = SPEC.model_copy(update={"id": "CHILD", "model": "child-1"})


@tool
def chercher(numero: str) -> str:
    """Cherche un devis."""
    return f"Devis {numero} : 1 840 €"


@pytest.fixture(params=["memory", "jsonl"])
async def store(request: pytest.FixtureRequest, tmp_path: Path) -> AsyncIterator[EventStore]:
    instance: EventStore = (
        InMemoryEventStore() if request.param == "memory" else JsonlEventStore(tmp_path)
    )
    yield instance
    await instance.aclose()


def scripted(*replies: Message) -> ScriptedModel:
    return ScriptedModel(*replies, usage=USAGE)


def searching(times: int, answer: str = "Trouvé.") -> list[Message]:
    calls = [tool_call_message((f"c{n}", "chercher", {"numero": f"D-{n}"})) for n in range(times)]
    return [*calls, Message.assistant(answer)]


def context(
    store: EventStore, model: ScriptedModel, budgets: Budgets | None, *tools: object
) -> RunContext:
    bound: list[BoundPolicy] = []
    if budgets is not None:
        guard = BudgetGuard(budgets)
        bound.append(BoundPolicy(policy=guard, name=guard.name, points=guard.points))
    return RunContext(
        agent="demo",
        store=store,
        model=model,
        model_spec=SPEC,
        tools=ToolExecutor([chercher, *tools]),  # pyright: ignore[reportArgumentType]
        system="Tu cherches.",
        policies=Policies(bound),
    )


def budgets(**fields: Any) -> Budgets:
    return Budgets.model_validate(fields)


async def run(
    ctx: RunContext, prompt: str = "Cherche.", session: SessionId | None = None
) -> RunState:
    state = await begin_run(ctx, prompt, session_id=session)
    return await drive(ctx, state.run_id, session_id=state.session_id)


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
            case BudgetExceeded(scope=scope, limit=limit, action=action):
                labels.append(f"budget:{scope}.{limit}:{action}")
            case PolicyDecided(decision=decision):
                labels.append(f"policy:{decision}")
            case _:
                labels.append(event.type)
    return labels


def exceeded(events: Sequence[Event]) -> list[BudgetExceeded]:
    return [e.payload for e in events if isinstance(e.payload, BudgetExceeded)]


# --- Tarifs --------------------------------------------------------------------------


def test_pricing_tiers_apply_to_the_whole_call() -> None:
    pricing = Pricing(
        input=0.30,
        output=1.20,
        cache_read=0.06,
        tiers=(PriceTier(above=512_000, input=0.60, output=2.40),),
    )
    small = Usage(input_tokens=500_000, output_tokens=1_000)
    big = Usage(input_tokens=500_000, cache_read_tokens=20_000, output_tokens=1_000)
    assert pricing.cost(small) == pytest.approx((500_000 * 0.30 + 1_000 * 1.20) / 1e6)
    # 520 000 tokens d'entrée, cache compris : le palier s'applique (cache_read : tarif de base).
    assert pricing.cost(big) == pytest.approx((500_000 * 0.60 + 20_000 * 0.06 + 1_000 * 2.40) / 1e6)
    assert pricing.priced and not Pricing().priced
    with pytest.raises(ValidationError, match="strictement croissants"):
        Pricing(tiers=(PriceTier(above=2), PriceTier(above=1)))


# --- Budgets -------------------------------------------------------------------------


def test_agent_budgets_override_the_defaults_key_by_key() -> None:
    root = budgets(run={"max_cost": 0.05, "max_calls": 20}, session={"max_cost": 1.0})
    agent = budgets(run={"max_cost": 0.01, "max_tokens": None}, on_exceed="warn")
    merged = root.merged(agent)
    assert merged.run == RunBudget(max_cost=0.01, max_calls=20)
    assert merged.session.max_cost == 1.0 and merged.on_exceed == "warn"
    assert root.merged(budgets(run={"max_calls": None})).run == RunBudget(max_cost=0.05)
    assert root.merged(None) is root
    assert merged.limited and merged.in_dollars and not Budgets().limited
    with pytest.raises(ValidationError, match="prévu pour le jalon J5"):
        budgets(tenant={"max_cost_per_day": 5})


def test_a_share_is_part_of_what_is_left() -> None:
    limits = RunBudget(max_cost=0.10, max_tokens=10_000, max_calls=5)
    used = Spent(Usage(input_tokens=4_000), 0.04, 2)
    share = limits.share(0.5, used)
    assert share is not None
    assert (share.max_cost, share.max_tokens, share.max_calls) == (pytest.approx(0.03), 3_000, 1)
    assert limits.share(0.5, Spent(Usage(), 0.10, 0)) is None
    assert RunBudget(max_cost=0.1).tightest(RunBudget(max_cost=0.05, max_calls=3)) == RunBudget(
        max_cost=0.05, max_calls=3
    )
    assert RunBudget(max_calls=3).tightest(RunBudget(max_cost=1.0)) == RunBudget(
        max_cost=1.0, max_calls=3
    )
    assert RunBudget(max_calls=3).tightest(None) == RunBudget(max_calls=3)


async def test_a_run_over_its_calls_budget_is_stopped(store: EventStore) -> None:
    model = scripted(*searching(2)[:2], Message.assistant("Arrêté : budget atteint."))
    state = await run(context(store, model, budgets(run={"max_calls": 2})))

    assert state.status is RunStatus.COMPLETED
    assert state.output == Message.assistant("Arrêté : budget atteint.")
    assert state.model_calls == 3 and state.exceeded == ("run.max_calls",)
    # La réponse forcée part sans outils.
    assert model.requests[-1].tool_choice == "none"
    events = await journal(store, state)
    labels = kinds(events)
    start = labels.index("budget:run.max_calls:stop")
    assert labels[start - 1 : start + 4] == [
        "step:model_call",
        "budget:run.max_calls:stop",
        "policy:stop",
        "step.completed",
        "→finalizing",
    ]
    [reached] = exceeded(events)
    assert (reached.value, reached.spent, reached.policy) == (2, 2, BUDGET_POLICY)
    stop = next(e.payload for e in events if isinstance(e.payload, PolicyDecided))
    assert stop.reason == "budget du run atteint : 2 appels (plafond 2 appels)"
    assert line_of(events[start]) == "budget du run atteint : 2 appels (plafond 2 appels) — arrêt"
    # La réponse forcée reçoit la consigne ; la transition porte le numéro de son étape.
    forced = model.requests[-1]
    assert forced.messages[-1] == Message.user(FINALIZE_HINT)
    assert forced.system == model.requests[0].system
    assert all(m.text != FINALIZE_HINT for m in model.requests[0].messages)
    # La consigne n'est jamais journalisée.
    assert all(FINALIZE_HINT not in e.model_dump_json() for e in events)
    step = events[start - 1].payload
    transition = events[start + 3].payload
    assert isinstance(step, StepStarted) and isinstance(transition, RunTransitioned)
    assert transition.step_no == step.step_no == 5


async def test_on_output_policies_know_the_answer_is_forced(store: EventStore) -> None:
    seen: list[bool] = []

    @policy(points=["on_output"], decisions=["retry"])
    def watch(subject: OnOutput) -> Decision:
        seen.append(subject.finalizing)
        return CONTINUE

    model = scripted(*searching(1)[:1], Message.assistant("Arrêté."))
    ctx = context(store, model, budgets(run={"max_calls": 1}))
    guard = BoundPolicy(policy=watch, name="watch", points=frozenset({"on_output"}))
    ctx = replace(ctx, policies=Policies([*ctx.policies.bound, guard]))
    state = await run(ctx)
    assert state.status is RunStatus.COMPLETED and seen == [True]


async def test_warn_reports_each_limit_once(store: EventStore) -> None:
    model = scripted(*searching(3))
    state = await run(
        context(
            store, model, budgets(run={"max_cost": 0.002, "max_tokens": 2_000}, on_exceed="warn")
        )
    )
    assert state.status is RunStatus.COMPLETED and state.output == Message.assistant("Trouvé.")
    events = await journal(store, state)
    assert [(b.limit, b.action) for b in exceeded(events)] == [
        ("max_cost", "warn"),
        ("max_tokens", "warn"),
    ]
    assert not [e for e in events if isinstance(e.payload, PolicyDecided)]
    assert all(e.status == "warning" for e in events if isinstance(e.payload, BudgetExceeded))
    assert line_of(events[kinds(events).index("budget:run.max_cost:warn")]) == (
        "budget du run atteint : 0,00300 $ (plafond 0,00200 $) — avertissement"
    )


async def test_the_session_budget_counts_earlier_runs(store: EventStore) -> None:
    session = SessionId("s-1")
    model = scripted(*searching(1), Message.assistant("Session épuisée."))
    ctx = context(store, model, budgets(session={"max_cost": 0.003}))
    first = await run(ctx, session=session)
    assert first.status is RunStatus.COMPLETED and not first.exceeded
    second = await run(ctx, session=session)
    assert second.output == Message.assistant("Session épuisée.")
    events = await store.read(second.context.tenant_id, session)
    [reached] = exceeded(events)
    assert (reached.scope, reached.limit) == ("session", "max_cost")
    assert reached.spent == pytest.approx(first.cost_usd) == pytest.approx(2 * CALL_COST)
    # Le run en cours n'avait encore rien dépensé : seule la session est atteinte.
    assert second.exceeded == ("session.max_cost",)
    assert len(model.requests) == 3


# --- Ledger et rapport ------------------------------------------------------------------


def child_context(store: EventStore, model: ScriptedModel, budgets: Budgets | None) -> RunContext:
    ctx = context(store, model, budgets)
    return RunContext(
        agent="verificateur",
        store=store,
        model=model,
        model_spec=CHILD_SPEC,
        system="Tu vérifies.",
        policies=ctx.policies,
    )


def verifier(
    agents: dict[str, RunContext], *, share: float | None = None, parent: RunBudget | None = None
) -> AgentTool:
    definition = SubAgentDefinition(
        name="verifier",
        agent="verificateur",
        description="Vérifie.",
        budget_share=share,
        parent_budget=parent,
    )
    return AgentTool(definition, agents.__getitem__)


async def test_the_ledger_counts_each_call_once(store: EventStore) -> None:
    child = scripted(Message.assistant("Vérifié."))
    agents = {"verificateur": child_context(store, child, None)}
    main = scripted(
        tool_call_message(("c1", "verifier", {"message": "Vérifie 2 + 2."})),
        Message.assistant("C'est vérifié."),
    )
    state = await run(context(store, main, None, verifier(agents)))
    events = await journal(store, state)

    entries = ledger(events)
    assert [(e.agent, e.role, e.model_id) for e in entries] == [
        ("demo", "main", "main-1"),
        ("verificateur", "main", "child-1"),
        ("demo", "main", "main-1"),
    ]
    total = spent(events)
    assert (total.calls, total.cost) == (3, pytest.approx(3 * CALL_COST))
    # run.completed du parent compte l'enfant (tool.completed) : les deux totaux concordent.
    assert state.cost_usd == pytest.approx(total.cost) and state.model_calls == 2

    report = usage_report(events, state.session_id, state.run_id)
    assert (report.total.calls, report.total.cost) == (3, pytest.approx(3 * CALL_COST))
    assert [(r.agent, r.depth, r.calls, r.status) for r in report.runs] == [
        ("demo", 0, 2, "completed"),
        ("verificateur", 1, 1, "completed"),
    ]
    assert [line.name for line in report.roles] == ["demo · main", "verificateur · main"]
    assert [(m.name, m.calls) for m in report.models] == [("main-1", 2), ("child-1", 1)]
    lines = render(report)
    assert lines[0] == f"Consommation — run {state.run_id}"
    assert lines[1].startswith("  Total") and lines[1].endswith("0,00450 $")
    assert "  Par run :" in lines and "  Par modèle :" in lines


async def test_a_subagent_gets_a_share_of_what_is_left(store: EventStore) -> None:
    child = scripted(*searching(3, "Vérifié."), Message.assistant("Arrêté."))
    agents = {"verificateur": child_context(store, child, Budgets())}
    parent = RunBudget(max_cost=0.01)
    main = scripted(
        tool_call_message(("c1", "verifier", {"message": "Vérifie."})),
        Message.assistant("Fini."),
    )
    ctx = context(
        store, main, budgets(run={"max_cost": 0.01}), verifier(agents, share=0.5, parent=parent)
    )
    state = await run(ctx)

    events = await journal(store, state)
    started = [e.payload for e in events if isinstance(e.payload, RunStarted) and e.payload.depth]
    [child_started] = started
    # Le parent a dépensé un appel (0,0015 $) : l'enfant reçoit la moitié du reste.
    assert child_started.budget is not None
    assert child_started.budget.max_cost == pytest.approx((0.01 - CALL_COST) / 2)
    child_events = [e for e in events if e.run_id != state.run_id]
    assert [b.limit for b in exceeded(child_events)] == ["max_cost"]
    assert state.status is RunStatus.COMPLETED


async def test_no_subagent_once_the_budget_is_spent(store: EventStore) -> None:
    child = scripted()
    agents = {"verificateur": child_context(store, child, None)}
    main = scripted(
        tool_call_message(("c1", "verifier", {"message": "Vérifie."})),
        Message.assistant("Sans vérification."),
    )
    tool_ = verifier(agents, share=0.5, parent=RunBudget(max_calls=1))
    state = await run(context(store, main, None, tool_))
    events = await journal(store, state)
    [completed] = [e.payload for e in events if isinstance(e.payload, ToolCompleted)]
    assert completed.output.is_error
    assert (
        completed.output.as_text
        == "Budget du run atteint : le sous-agent verifier n'est pas lancé."
    )
    assert not child.requests
    assert not [e for e in events if isinstance(e.payload, RunStarted) and e.payload.depth]


def test_amounts_are_readable() -> None:
    assert amount("max_cost", 0.0123) == "0,01230 $"
    assert amount("max_tokens", 1500.0) == "1500 tokens"
    assert amount("max_calls", 1) == "1 appel"


async def test_the_completion_keeps_its_total(store: EventStore) -> None:
    model = scripted(*searching(1))
    state = await run(context(store, model, budgets(run={"max_cost": 1.0})))
    closing = (await journal(store, state))[-1].payload
    assert isinstance(closing, RunCompleted)
    assert closing.cost_usd == pytest.approx(2 * CALL_COST) == state.spent.cost
