# SPDX-License-Identifier: Apache-2.0
"""Juges : déclenchement, verdict, réparation, échecs, journal et coût (J3.3)."""

import json
from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from typing import Any

import pytest
from pydantic import JsonValue, ValidationError

from loom_ia.access.progress import describe
from loom_ia.adapters.stores import InMemoryEventStore, JsonlEventStore
from loom_ia.core.events import (
    Event,
    GuardChecked,
    JudgeEvaluated,
    ModelResponded,
    ModelRetried,
    PolicyDecided,
    RunCompleted,
    RunFailed,
    RunStarted,
    RunTransitioned,
    StepStarted,
    ToolCompleted,
    UserMessage,
)
from loom_ia.core.model import (
    CallerContext,
    Criterion,
    CriterionScore,
    JudgeInput,
    JudgesMode,
    JudgeWhen,
    Message,
    ModelSpec,
    OnFailure,
    OnOutput,
    OutputContract,
    PolicyContext,
    RepairSettings,
    Retry,
    RetryPolicy,
    RunId,
    RunState,
    RunStatus,
    SessionId,
    SpanId,
    TenantId,
    ToolResultBlock,
    Usage,
    sampled,
)
from loom_ia.core.ports import EventStore, ModelError
from loom_ia.core.projections import history
from loom_ia.engine import (
    BoundPolicy,
    Policies,
    RoleDefinition,
    RoleTool,
    RunContext,
    ToolExecutor,
    ToolResults,
    begin_run,
    drive,
)
from loom_ia.guards import (
    CONTRACT_POLICY,
    VERDICT_TOOL,
    Condition,
    ContractGuard,
    JudgeDefinition,
    JudgeGuard,
    correlated,
    judge_policy_name,
    verdict_tool,
)
from loom_ia.testing import ScriptedModel, tool_call_message
from loom_ia.tools import tool

SPEC = ModelSpec(id="MAIN", sdk="fake", model="main-1", retry=RetryPolicy(initial_delay=0))
ROLE_SPEC = ModelSpec(id="ROLE", sdk="fake", model="role-1", retry=RetryPolicy(initial_delay=0))
JUDGE_SPEC = ModelSpec.model_validate(
    {
        "id": "JUDGE",
        "sdk": "fake",
        "model": "judge-1",
        "retry": {"initial_delay": 0},
        "pricing": {"input": 1.0, "output": 5.0},
    }
)
USAGE = Usage(input_tokens=100, output_tokens=10)
# 100 tokens à 1 $ + 10 tokens à 5 $ le million.
JUDGE_COST = 0.00015
EXACT = Criterion(name="exact", rule="Aucun montant ni délai absent du devis.")
TONE = Criterion(name="ton", rule="Le ton est cordial.", min_score=0.5, blocking=False)
PROMPT = "Relance le devis D-2026-042."


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


def verdict(**scores: float) -> Message:
    """Réponse du juge : une note par critère, avec un motif."""
    criteria: list[JsonValue] = [
        {"name": name, "score": score, "reason": f"motif {name}"} for name, score in scores.items()
    ]
    return tool_call_message(("v1", VERDICT_TOOL, {"criteria": criteria}))


def scripted(*replies: Any) -> ScriptedModel:
    return ScriptedModel(*replies, usage=USAGE)


def judge(
    model: ScriptedModel,
    *criteria: Criterion,
    name: str = "output",
    role: str | None = None,
    **fields: Any,
) -> JudgeGuard:
    definition = JudgeDefinition(name=name, role=role, criteria=criteria or (EXACT,), **fields)
    return JudgeGuard(definition, model, JUDGE_SPEC)


def bound(guard: JudgeGuard) -> BoundPolicy:
    definition = guard.definition
    return BoundPolicy(
        policy=guard,
        name=guard.name,
        points=guard.points,
        timeout=definition.timeout,
        on_error=definition.on_error,
        max_attempts=None,
    )


def writer_role(model: ScriptedModel, *, terminal: bool = False) -> RoleTool:
    definition = RoleDefinition(
        name="rediger",
        description="Rédige la relance.",
        system="Tu rédiges.",
        input_schema={"type": "object", "properties": {"ton": {"type": "string"}}},
        terminal=terminal,
        output=OutputContract(must_match="Bonjour"),
    )
    return RoleTool(definition, model, ROLE_SPEC)


def context(
    store: EventStore,
    model: ScriptedModel,
    *guards: JudgeGuard,
    tools: Sequence[object] = (),
    contract: bool = False,
) -> RunContext:
    policies: list[BoundPolicy] = []
    if contract:
        guard = ContractGuard()
        policies.append(
            BoundPolicy(policy=guard, name=CONTRACT_POLICY, points=guard.points, max_attempts=None)
        )
    policies += [bound(g) for g in guards]
    return RunContext(
        agent="demo",
        store=store,
        model=model,
        model_spec=SPEC,
        tools=ToolExecutor([chercher, *tools]),  # pyright: ignore[reportArgumentType]
        system="Tu relances.",
        policies=Policies(policies),
    )


async def run(
    ctx: RunContext,
    *,
    judges: JudgesMode = "auto",
    caller: CallerContext | None = None,
) -> RunState:
    state = await begin_run(ctx, PROMPT, judges=judges, context=caller)
    return await drive(ctx, state.run_id, tenant_id=state.context.tenant_id)


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
            case GuardChecked(guard=guard, outcome=outcome, resolution=resolution):
                labels.append(f"{guard}:{outcome}{f':{resolution}' if resolution else ''}")
            case PolicyDecided(decision=decision):
                labels.append(f"policy:{decision}")
            case UserMessage(kind="repair"):
                labels.append("repair")
            case ModelResponded(judge=str()):
                labels.append("judge.responded")
            case ModelResponded(call_id=str()):
                labels.append("role.responded")
            case JudgeEvaluated(passed=passed):
                labels.append(f"judge.evaluated:{'passed' if passed else 'failed'}")
            case _:
                labels.append(event.type)
    return labels


def checks(events: Sequence[Event]) -> list[GuardChecked]:
    return [e.payload for e in events if isinstance(e.payload, GuardChecked)]


def evaluations(events: Sequence[Event]) -> list[JudgeEvaluated]:
    return [e.payload for e in events if isinstance(e.payload, JudgeEvaluated)]


# --- Modèles -------------------------------------------------------------------------------


def test_sampling_is_deterministic_and_proportional() -> None:
    runs = [f"run-{n}" for n in range(4000)]
    picked = [r for r in runs if sampled(r, "output", 0.25)]
    assert 0.22 < len(picked) / len(runs) < 0.28
    assert picked == [r for r in runs if sampled(r, "output", 0.25)]
    # Deux juges ne tirent pas les mêmes runs.
    assert picked != [r for r in runs if sampled(r, "autre", 0.25)]
    assert all(sampled(r, "output", 1.0) for r in runs[:10])
    assert not any(sampled(r, "output", 0.0) for r in runs[:10])


def test_when_refuses_what_it_cannot_mean() -> None:
    # `profiles` est débloqué depuis 5.5a ; une liste vide, elle, ne dit rien.
    assert JudgeWhen.model_validate({"profiles": ["prod"]}).profiles == ("prod",)
    with pytest.raises(ValidationError):
        JudgeWhen.model_validate({"profiles": []})
    with pytest.raises(ValidationError):
        JudgeWhen(sample=1.5)
    with pytest.raises(ValidationError):
        Criterion(name="espace interdit", rule="…")


def test_verdict_tool_lists_the_criteria() -> None:
    definition = verdict_tool((EXACT, TONE))
    schema = definition.input_schema
    assert definition.name == VERDICT_TOOL
    items = schema["properties"]["criteria"]  # type: ignore[index]
    assert items["minItems"] == items["maxItems"] == 2  # type: ignore[index]
    assert items["items"]["properties"]["name"]["enum"] == ["exact", "ton"]  # type: ignore[index]


def test_correlated_judges_share_the_provider_model() -> None:
    same = SPEC.model_copy(update={"id": "AUTRE"})
    assert correlated(same, SPEC)
    assert not correlated(JUDGE_SPEC, SPEC)
    assert not correlated(SPEC.model_copy(update={"base_url": "https://ailleurs"}), SPEC)


def test_judge_evaluated_checks_its_verdict() -> None:
    low = CriterionScore(name="exact", score=0.2, min_score=0.8, blocking=True, reason="…")
    event = JudgeEvaluated(
        judge="output", target="output", model_id="m", criteria=(low,), passed=False, blocked=True
    )
    assert event.event_status == "warning"
    assert event.facets() == {
        "judge": "output",
        "target": "output",
        "model_id": "m",
        "passed": False,
        "blocked": True,
    }
    with pytest.raises(ValidationError, match="incohérent"):
        JudgeEvaluated(
            judge="o", target="output", model_id="m", criteria=(low,), passed=True, blocked=True
        )


def test_judge_events_keep_older_facets() -> None:
    assert (
        "judge"
        not in ModelRetried(
            model_id="m", provider="p", attempt=1, error_kind="transient", error="…", delay_s=0
        ).facets()
    )
    assert RunStarted().facets() == {"kind": "normal"}
    assert RunStarted(judges="force").facets() == {"kind": "normal", "judges": "force"}


# --- Réponse finale ---------------------------------------------------------------------


async def test_a_passing_judge_is_journaled_and_paid(store: EventStore) -> None:
    main = scripted(Message.assistant("Bonjour Madame Martin, 1 840 €."))
    jury = scripted(verdict(exact=0.9))
    state = await run(context(store, main, judge(jury, context=("user_input",))))

    assert state.status is RunStatus.COMPLETED and state.iterations == 1
    assert state.output == Message.assistant("Bonjour Madame Martin, 1 840 €.")
    [request] = jury.requests
    assert request.tool_choice == "required"
    assert [t.name for t in request.tools] == [VERDICT_TOOL]
    text = request.messages[0].text
    assert "<criteria>\n- exact : Aucun montant ni délai absent du devis.\n</criteria>" in text
    assert f"<user_input>\n{PROMPT}\n</user_input>" in text
    assert text.endswith("<output>\nBonjour Madame Martin, 1 840 €.\n</output>")
    events = await journal(store, state)
    assert kinds(events)[3:] == [
        "model.responded",
        "step.completed",
        "judge.responded",
        "judge.evaluated:passed",
        "judge:passed",
        "→completed",
        "run.completed",
    ]
    called, evaluated = events[5], events[6]
    assert called.role == "judge:output" and called.facets["judge"] == "output"
    assert called.parent_span_id == state.span_id and called.span_id != state.span_id
    assert evaluated.span_id == state.span_id and evaluated.category == "guard"
    payload = evaluated.payload
    assert isinstance(payload, JudgeEvaluated)
    assert (payload.judge, payload.target, payload.model_id) == ("output", "output", "judge-1")
    assert payload.policy == judge_policy_name("output") == "loom.judge.output"
    closing = events[-1].payload
    assert isinstance(closing, RunCompleted)
    assert closing.cost_usd == pytest.approx(JUDGE_COST) == state.cost_usd
    assert closing.usage == Usage(input_tokens=200, output_tokens=20) == state.usage
    assert history(events) == [Message.user(PROMPT), state.output]


async def test_a_blocking_criterion_asks_the_orchestrator_to_repair(store: EventStore) -> None:
    main = scripted(
        Message.assistant("Bonjour, remise de 10 % offerte."),
        Message.assistant("Bonjour, votre devis de 1 840 €."),
    )
    jury = scripted(verdict(exact=0.1, ton=0.2), verdict(exact=1.0, ton=0.9))
    state = await run(context(store, main, judge(jury, EXACT, TONE)))

    assert state.status is RunStatus.COMPLETED
    assert state.output == Message.assistant("Bonjour, votre devis de 1 840 €.")
    repair = main.requests[1]
    # Un échec de fond : l'orchestrateur garde ses outils (repair.tools: auto).
    assert repair.tool_choice == "auto" and repair.tools
    diagnostic = repair.messages[-1].text
    assert diagnostic.startswith("Réponse refusée par un contrôle (loom.judge.output) : ")
    assert "- exact (note 0,10, seuil 0,80) : motif exact" in diagnostic
    assert "ton" not in diagnostic
    events = await journal(store, state)
    assert kinds(events)[5:10] == [
        "judge.responded",
        "judge.evaluated:failed",
        "judge:failed:retry",
        "policy:retry",
        "repair",
    ]
    first, second = evaluations(events)
    assert (first.blocked, first.attempt, second.passed, second.attempt) == (True, 1, True, 2)
    failed = checks(events)[0]
    assert failed.reason == "exact (0,10 < 0,80) : motif exact"
    assert state.cost_usd == pytest.approx(2 * JUDGE_COST)
    assert history(events)[-1] == state.output


async def test_repair_tools_none_repairs_without_tools(store: EventStore) -> None:
    main = scripted(Message.assistant("Remise."), Message.assistant("Bonjour."))
    jury = scripted(verdict(exact=0.0), verdict(exact=1.0))
    guard = judge(jury, repair=RepairSettings(tools="none"))
    await run(context(store, main, guard))
    assert main.requests[1].tool_choice == "none"


@pytest.mark.parametrize(
    ("on_failure", "status", "text", "unverified"),
    [
        ("fail", RunStatus.FAILED, "", False),
        ("unverified", RunStatus.COMPLETED, "Remise.", True),
        ("fallback", RunStatus.COMPLETED, "À relire.", False),
    ],
)
async def test_exhausted_repairs_follow_on_failure(
    store: EventStore, on_failure: OnFailure, status: RunStatus, text: str, unverified: bool
) -> None:
    main = scripted(Message.assistant("Remise."), Message.assistant("Remise."))
    jury = scripted(verdict(exact=0.0), verdict(exact=0.0))
    guard = judge(jury, on_failure=on_failure, fallback_message="À relire.")
    state = await run(context(store, main, guard))

    assert state.status is status and state.unverified is unverified
    assert (state.output.text if state.output else "") == text
    events = await journal(store, state)
    last = checks(events)[-1]
    assert (last.outcome, last.resolution, last.attempt) == ("failed", on_failure, 2)
    closing = events[-1].payload
    assert isinstance(closing, RunCompleted | RunFailed)
    # Les deux appels du juge sont comptés, même dans un échec.
    assert closing.cost_usd == pytest.approx(2 * JUDGE_COST)
    if isinstance(closing, RunFailed):
        assert closing.error_type == "guard.judge"
        assert closing.error.startswith("réponse finale refusée par le juge output : exact")


async def test_a_non_blocking_failure_is_only_reported(store: EventStore) -> None:
    main = scripted(Message.assistant("Bonjour."))
    jury = scripted(verdict(exact=0.9, ton=0.1))
    state = await run(context(store, main, judge(jury, EXACT, TONE)))

    assert state.status is RunStatus.COMPLETED and len(main.requests) == 1
    events = await journal(store, state)
    [evaluated] = evaluations(events)
    assert (evaluated.passed, evaluated.blocked) == (False, False)
    [passed] = checks(events)
    assert passed.outcome == "passed"
    assert passed.reason == "non bloquant : ton (0,10 < 0,50) : motif ton"
    line = describe(next(e for e in events if isinstance(e.payload, JudgeEvaluated)))
    assert line == "juge output (judge-1) : exact 0,90, ton 0,10 (seuil 0,50)"


# --- Déclenchement ----------------------------------------------------------------------


async def test_the_caller_can_skip_or_force_the_judges(store: EventStore) -> None:
    main = scripted(Message.assistant("Bonjour."), Message.assistant("Bonjour."))
    jury = scripted(verdict(exact=1.0))
    ctx = context(store, main, judge(jury, sample=0.0))

    skipped = await run(ctx, judges="skip")
    [check] = checks(await journal(store, skipped))
    assert (check.outcome, check.reason) == ("skipped", "caller_skip")
    assert not jury.requests

    forced = await run(ctx, judges="force")
    assert forced.judges == "force" and len(jury.requests) == 1
    events = await journal(store, forced)
    assert events[0].facets["judges"] == "force"
    assert checks(events)[0].outcome == "passed"


async def test_sampled_out_and_filtered_runs_are_skipped(store: EventStore) -> None:
    main = scripted(*(Message.assistant("Bonjour.") for _ in range(3)))
    jury = scripted(verdict(exact=1.0))

    state = await run(context(store, main, judge(jury, sample=0.0)))
    assert [(c.outcome, c.reason) for c in checks(await journal(store, state))] == [
        ("skipped", "sampled_out")
    ]
    guard = judge(jury, tenants=frozenset({"dupont"}))
    state = await run(
        context(store, main, guard), caller=CallerContext(tenant_id=TenantId("martin"))
    )
    assert checks(await journal(store, state))[0].reason == "filtered"
    state = await run(
        context(store, main, guard), caller=CallerContext(tenant_id=TenantId("dupont"))
    )
    assert checks(await journal(store, state))[0].outcome == "passed"


@pytest.mark.parametrize("asynchronous", [False, True])
async def test_a_condition_decides_from_the_output(store: EventStore, asynchronous: bool) -> None:
    seen: list[JudgeInput] = []

    def big(given: JudgeInput) -> bool:
        seen.append(given)
        return "€" in given.output

    async def big_async(given: JudgeInput) -> bool:
        return big(given)

    condition: Condition = big_async if asynchronous else big
    main = scripted(Message.assistant("Bonjour."), Message.assistant("Bonjour, 1 840 €."))
    jury = scripted(verdict(exact=1.0))
    ctx = context(store, main, judge(jury, condition=condition))

    state = await run(ctx)
    assert checks(await journal(store, state))[0].reason == "condition_false"
    state = await run(ctx)
    assert checks(await journal(store, state))[0].outcome == "passed"
    first = seen[0]
    assert (first.target, first.role, first.agent, first.request) == (
        "output",
        None,
        "demo",
        PROMPT,
    )


async def test_a_condition_that_is_not_boolean_is_a_judge_error(store: EventStore) -> None:
    def odd(given: JudgeInput) -> Any:
        return "oui"

    main = scripted(Message.assistant("Bonjour."))
    state = await run(context(store, main, judge(scripted(), condition=odd)))
    assert state.status is RunStatus.FAILED
    assert state.error is not None and "la condition doit rendre un booléen" in state.error


# --- Erreurs du juge --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("reply", "message"),
    [
        (Message.assistant("Tout va bien."), "réponse sans verdict"),
        (verdict(autre=1.0), "verdict invalide — criteria.0.name"),
        (
            tool_call_message(("v1", VERDICT_TOOL, {"criteria": [{"name": "exact"}]})),
            "verdict invalide",
        ),
        (ModelError("auth", "clé refusée"), "juge output : model.auth — clé refusée"),
    ],
)
async def test_judge_errors_block_by_default(store: EventStore, reply: Any, message: str) -> None:
    main = scripted(Message.assistant("Bonjour."))
    state = await run(context(store, main, judge(scripted(reply))))
    assert state.status is RunStatus.FAILED
    assert state.error is not None and message in state.error
    assert state.error_type == "policy.loom.judge.output"


async def test_on_error_allow_lets_the_output_pass(store: EventStore) -> None:
    main = scripted(Message.assistant("Bonjour."))
    guard = judge(scripted(Message.assistant("Pas d'outil.")), on_error="allow")
    state = await run(context(store, main, guard))
    assert state.status is RunStatus.COMPLETED
    events = await journal(store, state)
    [decided] = [e for e in events if isinstance(e.payload, PolicyDecided)]
    assert decided.status == "warning"
    # L'appel raté du juge reste compté.
    assert state.cost_usd == pytest.approx(JUDGE_COST)


# --- Rôles --------------------------------------------------------------------------


async def test_a_role_is_judged_after_its_contract_and_repairs_itself(
    store: EventStore,
) -> None:
    writer = scripted(
        Message.assistant("Bonjour, remise de 10 %."),
        Message.assistant("Bonjour, votre devis de 1 840 €."),
    )
    main = scripted(
        tool_call_message(("c0", "chercher", {"numero": "D-2026-042"})),
        tool_call_message(("c1", "rediger", {"ton": "cordial"})),
        Message.assistant("Relance rédigée."),
    )
    jury = scripted(verdict(exact=0.1), verdict(exact=1.0))
    guard = judge(jury, name="rediger", role="rediger", context=(ToolResults(tools=("chercher",)),))
    state = await run(context(store, main, guard, tools=[writer_role(writer)], contract=True))

    assert state.status is RunStatus.COMPLETED
    _, repair = writer.requests
    assert repair.messages[1] == Message.assistant("Bonjour, remise de 10 %.")
    assert repair.messages[2].text.startswith(
        "Réponse refusée par un contrôle (loom.judge.rediger) : Le juge rediger refuse"
    )
    asked = jury.requests[0].messages[0].text
    assert '<tool_result tool="chercher" ref="result:1">\nDevis D-2026-042 : 1 840 €' in asked
    assert '<arguments>\n{"ton": "cordial"}\n</arguments>' in asked
    events = await journal(store, state)
    batch = [k for k in kinds(events) if k != "step.completed"]
    start = batch.index("role.responded")
    assert batch[start : start + 12] == [
        "role.responded",
        "contract:passed",
        "judge.responded",
        "judge.evaluated:failed",
        "judge:failed:retry",
        "policy:retry",
        "role.responded",
        "contract:passed",
        "judge.responded",
        "judge.evaluated:passed",
        "judge:passed",
        "tool.completed",
    ]
    call = next(e for e in events if e.type == "tool.called" and e.payload.call_id == "c1")  # type: ignore[union-attr]
    judged = [e for e in events if isinstance(e.payload, ModelResponded) and e.payload.judge]
    assert [e.role for e in judged] == ["judge:rediger", "judge:rediger"]
    assert all(e.parent_span_id == call.span_id for e in judged)
    assert judged[0].span_id != judged[1].span_id
    assert all(e.payload.call_id == "c1" for e in judged)  # type: ignore[union-attr]
    assert [(c.target, c.attempt) for c in checks(events) if c.guard == "judge"] == [
        ("role:rediger", 1),
        ("role:rediger", 2),
    ]
    assert state.cost_usd == pytest.approx(2 * JUDGE_COST)


async def test_an_exhausted_role_returns_the_verdict_to_the_orchestrator(
    store: EventStore,
) -> None:
    writer = scripted(Message.assistant("Bonjour, remise."), Message.assistant("Bonjour, remise."))
    main = scripted(
        tool_call_message(("c1", "rediger", {"ton": "cordial"})),
        Message.assistant("Relance à reprendre."),
    )
    jury = scripted(verdict(exact=0.0), verdict(exact=0.0))
    guard = judge(jury, name="rediger", role="rediger")
    state = await run(context(store, main, guard, tools=[writer_role(writer)]))

    assert state.status is RunStatus.COMPLETED
    [completed] = [
        e.payload for e in await journal(store, state) if isinstance(e.payload, ToolCompleted)
    ]
    assert completed.output.is_error
    text = completed.output.as_text
    assert text.startswith("Sortie refusée par le juge rediger.\nLe juge rediger refuse")
    assert text.endswith("Sortie reçue :\nBonjour, remise.")
    result = main.requests[1].messages[-1].blocks[0]
    assert isinstance(result, ToolResultBlock) and result.output.is_error


async def test_a_judged_terminal_role_kept_unverified_marks_the_run(store: EventStore) -> None:
    writer = scripted(Message.assistant("Bonjour, remise."))
    main = scripted(tool_call_message(("c1", "rediger", {"ton": "cordial"})))
    guard = judge(
        scripted(verdict(exact=0.0)),
        name="rediger",
        role="rediger",
        on_failure="unverified",
        repair=RepairSettings(max_attempts=0),
    )
    state = await run(context(store, main, guard, tools=[writer_role(writer, terminal=True)]))
    assert state.status is RunStatus.COMPLETED
    assert state.unverified and state.output == Message.assistant("Bonjour, remise.")


async def test_a_role_judge_ignores_other_tools_and_errors(store: EventStore) -> None:
    main = scripted(
        tool_call_message(("c1", "chercher", {"numero": "D-1"})),
        Message.assistant("Trouvé."),
    )
    jury = scripted()
    guard = judge(jury, name="rediger", role="rediger")
    state = await run(context(store, main, guard))
    assert state.status is RunStatus.COMPLETED and not jury.requests
    assert not checks(await journal(store, state))


async def test_a_subrun_inherits_the_judges_choice(store: EventStore) -> None:
    main = scripted(Message.assistant("Bonjour."))
    ctx = context(store, main)
    state = await begin_run(ctx, PROMPT, judges="skip")
    assert state.judges == "skip"
    events = await store.read(state.context.tenant_id, state.session_id)
    started = events[0].payload
    assert isinstance(started, RunStarted) and started.judges == "skip"


def test_a_judge_request_is_a_plain_model_request() -> None:
    guard = judge(scripted(), max_tokens=300, params={"temperature": 0})
    assert guard.points == frozenset({"on_output"})
    assert judge(scripted(), role="r").points == frozenset({"after_tool"})
    assert guard.decisions == frozenset({"replace", "retry", "fail"})
    assert "JudgeGuard('output', output" in repr(guard)
    assert isinstance(json.dumps(guard.tool.input_schema), str)


# --- Cas limites ------------------------------------------------------------------------


async def test_judge_retries_are_journaled_in_its_span(store: EventStore) -> None:
    main = scripted(Message.assistant("Bonjour."))
    jury = scripted(ModelError("transient", "surcharge"), verdict(exact=1.0))
    state = await run(context(store, main, judge(jury)))
    assert state.status is RunStatus.COMPLETED
    events = await journal(store, state)
    retried, responded = [e for e in events if e.role == "judge:output"]
    assert isinstance(retried.payload, ModelRetried) and retried.payload.judge == "output"
    assert retried.facets["judge"] == "output"
    assert retried.span_id == responded.span_id != state.span_id
    assert isinstance(responded.payload, ModelResponded) and responded.payload.attempts == 2


async def test_the_judge_sees_the_declared_context(store: EventStore) -> None:
    main = scripted(Message.assistant('{"objet": "Relance"}'))
    jury = scripted(verdict(exact=1.0))
    seen: list[JudgeInput] = []

    def keep(given: JudgeInput) -> bool:
        seen.append(given)
        return True

    guard = judge(
        jury,
        context=("caller_context", "attachments", ToolResults(tools=("chercher",))),
        condition=keep,
    )
    caller = CallerContext(user_id="artisan-1")
    state = await run(context(store, main, guard), caller=caller)
    assert state.status is RunStatus.COMPLETED
    text = jury.requests[0].messages[0].text
    assert '"user_id": "artisan-1"' in text
    assert "<attachments>\n(aucune pièce jointe)\n</attachments>" in text
    assert '<tool_result tool="chercher">\n(aucun résultat dans ce run)\n</tool_result>' in text
    assert "<arguments>" not in text
    assert seen[0].data == {"objet": "Relance"} and seen[0].caller.user_id == "artisan-1"


async def test_a_verdict_cannot_score_a_criterion_twice(store: EventStore) -> None:
    twice = tool_call_message(
        (
            "v1",
            VERDICT_TOOL,
            {
                "criteria": [
                    {"name": "exact", "score": 1, "reason": "…"},
                    {"name": "exact", "score": 0, "reason": "…"},
                ]
            },
        )
    )
    main = scripted(Message.assistant("Bonjour."))
    state = await run(context(store, main, judge(scripted(twice), EXACT, TONE)))
    assert state.error is not None and "critère noté deux fois" in state.error


async def test_a_role_fallback_replaces_the_refused_output(store: EventStore) -> None:
    writer = scripted(Message.assistant("Bonjour, remise."))
    main = scripted(
        tool_call_message(("c1", "rediger", {"ton": "cordial"})),
        Message.assistant("Fait."),
    )
    guard = judge(
        scripted(verdict(exact=0.0)),
        name="rediger",
        role="rediger",
        on_failure="fallback",
        fallback_message="Bonjour, relance à relire.",
        repair=RepairSettings(max_attempts=0),
    )
    state = await run(context(store, main, guard, tools=[writer_role(writer)]))
    [completed] = [
        e.payload for e in await journal(store, state) if isinstance(e.payload, ToolCompleted)
    ]
    assert completed.output.as_text == "Bonjour, relance à relire."
    assert not completed.output.is_error


async def test_a_judge_decides_outside_the_engine() -> None:
    guard = judge(scripted(verdict(exact=0.1)))
    assert guard.definition.blocking
    state = RunState(
        run_id=RunId("r"),
        session_id=SessionId("s"),
        root_run_id=RunId("r"),
        span_id=SpanId("p"),
        agent="demo",
    )
    decision = await guard.decide(
        OnOutput(state=state, output=Message.assistant("Bonjour.")),
        PolicyContext(name=guard.name),
    )
    assert isinstance(decision, Retry) and "exact" in decision.feedback


async def test_a_failed_batch_counts_what_it_spent(store: EventStore) -> None:
    writer = scripted(Message.assistant("Bonjour."))
    main = scripted(tool_call_message(("c1", "rediger", {"ton": "cordial"})))
    guard = judge(scripted(Message.assistant("Sans verdict.")), name="rediger", role="rediger")
    state = await run(context(store, main, guard, tools=[writer_role(writer)]))
    assert state.status is RunStatus.FAILED
    failed = (await journal(store, state))[-1].payload
    assert isinstance(failed, RunFailed) and failed.error_type == "policy.loom.judge.rediger"
    # Orchestrateur, rôle et juge : trois appels de modèle.
    assert failed.usage == Usage(input_tokens=300, output_tokens=30) == state.usage
    assert failed.cost_usd == pytest.approx(JUDGE_COST) == state.cost_usd
