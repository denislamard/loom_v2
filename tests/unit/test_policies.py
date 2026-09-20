# SPDX-License-Identifier: Apache-2.0
"""Politiques : décorateur, exécution d'une chaîne, garde-fous, politiques fournies, config."""

import asyncio
from pathlib import Path
from typing import Any

import pytest
import yaml

from loom_ia.adapters.stores import InMemoryEventStore
from loom_ia.agents import AgentSpec
from loom_ia.config import ConfigError, Registry, import_modules, load_config
from loom_ia.core.events import PolicyDecided
from loom_ia.core.model import (
    CONTINUE,
    AfterModel,
    AfterTool,
    BeforeModel,
    BeforeTool,
    Decision,
    Deny,
    Fail,
    Message,
    ModelRequest,
    OnOutput,
    PendingCall,
    PolicyContext,
    Replace,
    Retry,
    RunId,
    RunState,
    SessionId,
    SpanId,
    Stop,
    ToolDefinition,
    ToolOutput,
    ToolSpec,
)
from loom_ia.engine import BoundPolicy, Policies
from loom_ia.policies import FunctionPolicy, policy, require_tool
from loom_ia.runtime import build_agent, build_policies
from loom_ia.testing import tool_call_message

STATE = RunState(
    run_id=RunId("r1"),
    session_id=SessionId("r1"),
    root_run_id=RunId("r1"),
    span_id=SpanId("s1"),
    agent="demo",
    messages=(Message.user("Bonjour"),),
)
REQUEST = ModelRequest(
    model_id="fake-1",
    messages=(Message.user("Bonjour"),),
    tools=(ToolDefinition(name="calculer", description="Calcule."),),
)
SPEC = ToolSpec(
    name="calculer",
    description="Calcule.",
    kind="python",
    input_schema={
        "type": "object",
        "properties": {"expr": {"type": "string"}},
        "required": ["expr"],
        "additionalProperties": False,
    },
)
CALL = PendingCall(call_id="c1", name="calculer", arguments={"expr": "1+1"})


def before_tool(arguments: dict[str, Any] | None = None) -> BeforeTool:
    return BeforeTool(state=STATE, call=CALL, spec=SPEC, arguments=arguments or {"expr": "1+1"})


def bound(fn: FunctionPolicy, **options: Any) -> BoundPolicy:
    return BoundPolicy(policy=fn, name=options.pop("name", fn.name), points=fn.points, **options)


# --- Décorateur ---------------------------------------------------------------


async def test_decorator_accepts_sync_and_async_functions() -> None:
    @policy(points=["before_tool"], decisions=["deny", "continue"])
    def sync(subject: BeforeTool) -> Decision:
        return Deny("non")

    @policy(points=["on_output"], decisions=["replace"], name="majuscules")
    async def with_context(subject: OnOutput, context: PolicyContext) -> Decision:
        return Replace(subject.output.text.upper() + str(context.params["fin"]))

    assert (sync.name, sync.points, sync.decisions) == (
        "sync",
        frozenset({"before_tool"}),
        frozenset({"deny"}),
    )
    assert with_context.name == "majuscules"
    decision = await sync.decide(before_tool(), PolicyContext(name="sync"))
    assert decision == Deny("non")
    output = OnOutput(state=STATE, output=Message.assistant("oui"))
    replaced = await with_context.decide(output, PolicyContext(name="m", params={"fin": "!"}))
    assert replaced == Replace("OUI!")
    assert sync(before_tool()) == Deny("non")
    assert "sync" in repr(sync)


@pytest.mark.parametrize(
    ("options", "message"),
    [
        ({"points": ["avant"], "decisions": []}, "point d'accroche inconnu"),
        ({"points": ["on_output"], "decisions": ["accepter"]}, "décision inconnu"),
        ({"points": [], "decisions": []}, "aucun point"),
        ({"points": ["on_output"], "decisions": [], "name": "loom.x"}, "réservé"),
        ({"points": ["on_output"], "decisions": [], "name": "a b"}, "nom invalide"),
    ],
)
def test_decorator_rejects_bad_declarations(options: dict[str, Any], message: str) -> None:
    def fn(subject: OnOutput) -> Decision:
        return CONTINUE

    with pytest.raises(ValueError, match=message):
        policy(**options)(fn)


def test_decorator_checks_the_signature() -> None:
    def three(a: object, b: object, c: object) -> Decision:
        return CONTINUE

    with pytest.raises(TypeError, match="3 paramètre"):
        policy(points=["on_output"], decisions=[])(three)


# --- Chaîne des politiques --------------------------------------------------------


async def test_replacements_are_chained_then_a_decision_stops_the_chain() -> None:
    seen: list[dict[str, Any]] = []

    @policy(points=["before_tool"], decisions=["replace"])
    def majuscules(subject: BeforeTool) -> Decision:
        return Replace({"expr": str(subject.arguments["expr"]).upper()}, reason="casse")

    @policy(points=["before_tool"], decisions=["deny"])
    def refus(subject: BeforeTool) -> Decision:
        seen.append(dict(subject.arguments))
        return Deny("pas aujourd'hui")

    @policy(points=["before_tool"], decisions=["deny"])
    def jamais(subject: BeforeTool) -> Decision:
        raise AssertionError("la chaîne aurait dû s'arrêter")

    policies = Policies([bound(majuscules), bound(refus), bound(jamais)])
    verdict = await policies.run(before_tool({"expr": "a+b"}), call_id="c1")

    assert seen == [{"expr": "A+B"}]
    assert verdict.decision == Deny("pas aujourd'hui")
    assert verdict.by == "refus"
    assert verdict.replaced
    replace, deny = verdict.decided
    assert (replace.decision, replace.reason, replace.arguments) == (
        "replace",
        "casse",
        {"expr": "A+B"},
    )
    assert (deny.decision, deny.reason, deny.call_id) == ("deny", "pas aujourd'hui", "c1")
    assert isinstance(verdict.subject, BeforeTool)
    assert verdict.subject.arguments == {"expr": "A+B"}


async def test_only_policies_of_the_point_run() -> None:
    @policy(points=["after_tool"], decisions=["fail"])
    def ailleurs(subject: AfterTool) -> Decision:
        return Fail("non")

    verdict = await Policies([bound(ailleurs)]).run(before_tool())
    assert verdict.decision == CONTINUE and not verdict.decided
    assert not Policies() and Policies([bound(ailleurs)])
    assert "ailleurs" in repr(Policies([bound(ailleurs)]))


@pytest.mark.parametrize(
    ("behaviour", "message"),
    [
        ("exception", "RuntimeError: panne"),
        ("undeclared", "non déclarée"),
        ("not_allowed", "non permise au point before_tool"),
        ("not_a_decision", "décision attendue"),
        ("bad_replace", "objet JSON attendu"),
        ("invalid_arguments", "refusés par le schéma"),
        ("timeout", "délai de 0.01 s dépassé"),
        ("own_timeout", "TimeoutError"),
    ],
)
async def test_policy_errors_block_by_default(behaviour: str, message: str) -> None:
    @policy(points=["before_tool"], decisions=["replace", "deny", "stop"])
    async def fautive(subject: BeforeTool) -> Decision:
        match behaviour:
            case "exception":
                raise RuntimeError("panne")
            case "undeclared":
                return Fail("x")
            case "not_allowed":
                return Stop("x")
            case "not_a_decision":
                return "oui"  # type: ignore[return-value]
            case "bad_replace":
                return Replace(["pas", "un", "objet"])
            case "invalid_arguments":
                return Replace({"autre": 1})
            case "timeout":
                await asyncio.sleep(1)
            case _:
                raise TimeoutError
        return CONTINUE

    policies = Policies([bound(fautive, timeout=0.01)])
    verdict = await policies.run(
        before_tool(),
        call_id="c1",
        check_arguments=lambda arguments: None if "expr" in arguments else "expr manquant",
    )
    assert isinstance(verdict.decision, Fail)
    assert message in verdict.decision.error
    [event] = verdict.decided
    assert (event.decision, event.error) == ("fail", True)
    assert PolicyDecided.model_validate(event.model_dump()).event_status == "error"


async def test_allowed_errors_are_journaled_and_skipped() -> None:
    @policy(points=["before_tool"], decisions=["deny"])
    def fautive(subject: BeforeTool) -> Decision:
        raise ValueError("oups")

    @policy(points=["before_tool"], decisions=["deny"])
    def suivante(subject: BeforeTool) -> Decision:
        return Deny("refusé")

    verdict = await Policies([bound(fautive, on_error="allow"), bound(suivante)]).run(
        before_tool(), call_id="c1"
    )
    assert verdict.decision == Deny("refusé")
    allowed, denied = verdict.decided
    assert (allowed.decision, allowed.error, allowed.event_status) == ("continue", True, "warning")
    assert "ValueError: oups" in allowed.reason
    assert denied.event_status == "ok"


async def test_retry_is_bounded_by_max_attempts() -> None:
    attempts: list[int] = []

    @policy(points=["on_output"], decisions=["retry"])
    def exigeante(subject: OnOutput, context: PolicyContext) -> Decision:
        attempts.append(context.attempt)
        return Retry("Cite le devis.", tools=False)

    policies = Policies([bound(exigeante, max_attempts=2)])
    subject = OnOutput(state=STATE, output=Message.assistant("Bonjour"))
    first = await policies.run(subject)
    assert first.decision == Retry("Cite le devis.", tools=False)
    [event] = first.decided
    assert (event.attempt, event.tools, event.reason) == (1, False, "Cite le devis.")

    tired = OnOutput(
        state=STATE.model_copy(update={"retries": {"exigeante": 2}}), output=subject.output
    )
    last = await policies.run(tired)
    assert isinstance(last.decision, Fail)
    assert "2 réparation(s)" in last.decision.error and "Cite le devis." in last.decision.error
    assert attempts == [0, 2]


async def test_ignored_decisions_are_neither_applied_nor_journaled() -> None:
    @policy(points=["before_model"], decisions=["stop"])
    def plafond(subject: BeforeModel) -> Decision:
        return Stop("plafond")

    verdict = await Policies([bound(plafond)]).run(
        BeforeModel(state=STATE, request=REQUEST, finalizing=True), ignore=frozenset({"stop"})
    )
    assert verdict.decision == CONTINUE and not verdict.decided


@pytest.mark.parametrize(
    ("subject", "value", "expected"),
    [
        (
            BeforeModel(state=STATE, request=REQUEST),
            REQUEST.model_copy(update={"system": "x"}),
            None,
        ),
        (BeforeModel(state=STATE, request=REQUEST), "requête", "ModelRequest attendue"),
        (
            AfterTool(state=STATE, call=CALL, spec=SPEC, arguments={}, output=ToolOutput.text("1")),
            ToolOutput.text("2"),
            None,
        ),
        (
            AfterTool(state=STATE, call=CALL, spec=SPEC, arguments={}, output=ToolOutput.text("1")),
            "2",
            "ToolOutput attendu",
        ),
        (OnOutput(state=STATE, output=Message.assistant("a")), Message.assistant("b"), None),
        (OnOutput(state=STATE, output=Message.assistant("a")), Message.user("b"), "message de"),
        (
            AfterModel(state=STATE, response=Message.assistant("a")),
            "b",
            "non permise au point after_model",
        ),
    ],
)
async def test_replacement_values_are_checked(
    subject: BeforeModel | AfterTool | OnOutput | AfterModel, value: object, expected: str | None
) -> None:
    point = subject.point

    @policy(points=[point], decisions=["replace"])
    def remplace(subject: object) -> Decision:
        return Replace(value)

    policies = Policies([bound(remplace)])
    verdict = await policies.run(subject)
    if expected is None:
        assert verdict.decision == CONTINUE and verdict.replaced
    else:
        assert isinstance(verdict.decision, Fail) and expected in verdict.decision.error


async def test_replaced_output_is_journaled() -> None:
    @policy(points=["on_output"], decisions=["replace"])
    def signe(subject: OnOutput) -> Decision:
        return Replace(f"{subject.output.text}\n-- Dupont")

    verdict = await Policies([bound(signe)]).run(
        OnOutput(state=STATE, output=Message.assistant("Bonjour"))
    )
    [event] = verdict.decided
    assert event.output == Message.assistant("Bonjour\n-- Dupont")


# --- Politique fournie : loom.require_tool --------------------------------------


async def test_require_tool_until_a_tool_is_called() -> None:
    context = PolicyContext(name="loom.require_tool")
    first = await require_tool.decide(BeforeModel(state=STATE, request=REQUEST), context)
    assert isinstance(first, Replace)
    assert isinstance(first.value, ModelRequest) and first.value.tool_choice == "required"

    called = STATE.model_copy(
        update={"messages": (*STATE.messages, tool_call_message(("c1", "calculer", {})))}
    )
    quiet = [
        BeforeModel(state=called, request=REQUEST),
        BeforeModel(state=STATE, request=REQUEST, finalizing=True),
        BeforeModel(state=STATE, request=REQUEST.model_copy(update={"tools": ()})),
        BeforeModel(state=STATE, request=REQUEST.model_copy(update={"tool_choice": "none"})),
    ]
    for subject in quiet:
        assert await require_tool.decide(subject, context) == CONTINUE


# --- Config ------------------------------------------------------------------------

POLITIQUES = """
from loom_ia.policies import CONTINUE, BeforeTool, Decision, Deny, OnOutput, Pause, policy


@policy(points=["before_tool"], decisions=["deny"])
def prudente(subject: BeforeTool) -> Decision:
    return CONTINUE


@policy(points=["before_tool", "on_output"], decisions=["replace"])
def partout(subject: object) -> Decision:
    return CONTINUE


@policy(points=["before_tool"], decisions=["pause"])
def attente(subject: BeforeTool) -> Decision:
    return Pause("validation")


def pas_une_politique(subject: object) -> Decision:
    return CONTINUE
"""


def write_config(tmp_path: Path, policies: list[dict[str, Any]]) -> Path:
    (tmp_path / "agents").mkdir(exist_ok=True)
    (tmp_path / "politiques_test.py").write_text(POLITIQUES, encoding="utf-8")
    config = {
        "version": 1,
        "imports": ["politiques_test"],
        "models": [{"id": "FAKE", "sdk": "fake", "model": "fake-1"}],
    }
    (tmp_path / "loom.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    agent = {
        "name": "demo",
        "main": {"model": "FAKE", "system": "Tu réponds."},
        "policies": policies,
    }
    (tmp_path / "agents" / "demo.yaml").write_text(yaml.safe_dump(agent), encoding="utf-8")
    return tmp_path / "loom.yaml"


async def test_policies_from_the_config(tmp_path: Path) -> None:
    config = load_config(
        write_config(
            tmp_path,
            [
                {"hook": "loom.require_tool"},
                {"hook": "prudente", "params": {"max": 5000}, "timeout": 2, "on_error": "allow"},
                {"hook": "politiques_test:partout", "points": ["on_output"], "name": "sortie"},
                {"hook": "partout", "max_attempts": 3},
            ],
        )
    )
    agent = build_agent(config, "demo", InMemoryEventStore())
    policies = agent.context.policies
    assert [(b.name, sorted(b.points)) for b in policies.bound] == [
        ("loom.require_tool", ["before_model"]),
        ("prudente", ["before_tool"]),
        ("sortie", ["on_output"]),
        ("partout", ["before_tool", "on_output"]),
    ]
    prudente = policies.bound[1]
    assert (prudente.params, prudente.timeout, prudente.on_error) == ({"max": 5000}, 2, "allow")
    assert policies.bound[3].max_attempts == 3
    await agent.aclose()


@pytest.mark.parametrize(
    ("declared", "message"),
    [
        ({"hook": "inconnue"}, "introuvable"),
        ({"hook": "loom.inconnue"}, "politique fournie 'loom.inconnue' inconnue"),
        ({"hook": "pas_une_politique"}, "introuvable"),
        ({"hook": "politiques_test:pas_une_politique"}, "n'est pas une politique"),
        ({"hook": "prudente", "points": ["on_output"]}, "ne s'applique pas à on_output"),
        ({"hook": "attente"}, "prévue pour le jalon J4.3"),
    ],
)
def test_policy_reference_errors(tmp_path: Path, declared: dict[str, Any], message: str) -> None:
    config = load_config(write_config(tmp_path, [declared]))
    with pytest.raises(ConfigError, match=message):
        build_agent(config, "demo", InMemoryEventStore())


def test_decisions_must_be_allowed_at_each_point() -> None:
    @policy(points=["before_tool", "after_model"], decisions=["deny"])
    def mal_placee(subject: object) -> Decision:
        return CONTINUE

    spec = AgentSpec.model_validate(
        {"name": "demo", "main": {"model": "FAKE"}, "policies": [{"hook": "mal_placee"}]}
    )
    with pytest.raises(ConfigError, match="deny non permise\\(s\\) au point after_model"):
        build_policies(spec, Registry({"mal_placee": mal_placee}))
    placed = spec.model_copy(
        update={"policies": (spec.policies[0].model_copy(update={"points": ("before_tool",)}),)}
    )
    assert [b.name for b in build_policies(placed, Registry({"mal_placee": mal_placee})).bound] == [
        "mal_placee"
    ]


def test_policy_names_are_unique_and_not_reserved(tmp_path: Path) -> None:
    config = load_config(write_config(tmp_path, [{"hook": "prudente"}, {"hook": "prudente"}]))
    with pytest.raises(ConfigError, match="déclarée deux fois"):
        build_agent(config, "demo", InMemoryEventStore())
    with pytest.raises(ValueError, match="réservé"):
        AgentSpec.model_validate(
            {
                "name": "demo",
                "main": {"model": "FAKE"},
                "policies": [{"hook": "x", "name": "loom.x"}],
            }
        )


def test_imports_register_policies_but_not_classes(tmp_path: Path) -> None:
    (tmp_path / "politiques_import.py").write_text(
        POLITIQUES + "\nfrom loom_ia.policies import FunctionPolicy\n", encoding="utf-8"
    )
    registry = import_modules(["politiques_import"], base_dir=tmp_path)
    assert {"prudente", "partout", "attente"} <= set(registry.names)
    assert "FunctionPolicy" not in registry.names
