# SPDX-License-Identifier: Apache-2.0
"""Contrats de sortie : définition, normalisation, contrôle, décisions du guard (J3.2)."""

import json
import time
from typing import Any

import pytest
from pydantic import JsonValue, ValidationError

from loom_ia.core import bounded_regex
from loom_ia.core.model import (
    CONTINUE,
    AfterTool,
    Fail,
    Message,
    OnOutput,
    OutputContract,
    PendingCall,
    PolicyContext,
    Replace,
    Retry,
    RunId,
    RunState,
    SessionId,
    SpanId,
    ToolOutput,
    ToolSpec,
)
from loom_ia.guards import CONTRACT_POLICY, ContractGuard, check, diagnostic, normalize

SCHEMA: dict[str, JsonValue] = {
    "type": "object",
    "properties": {"objet": {"type": "string"}, "corps": {"type": "string"}},
    "required": ["objet", "corps"],
    "additionalProperties": False,
}
EMAIL = '{"objet": "Votre devis", "corps": "Bonjour"}'
# Un secret qu'un contrat interdit, et le motif qui le reconnaît.
SECRET = "sk-ABCDEF1234567890XYZ"
KEY_PATTERN = r"sk-[A-Za-z0-9]{10,}"
# Un motif qui ne finit pas sur ce texte (``regex`` y met plus de 2 s, ``re`` plus d'une).
SLOW = "^(a|aa)+$"
SLOW_TEXT = "a" * 34 + "!"
STATE = RunState(
    run_id=RunId("r1"),
    session_id=SessionId("r1"),
    root_run_id=RunId("r1"),
    span_id=SpanId("s1"),
    agent="demo",
)


def contract(**fields: Any) -> OutputContract:
    return OutputContract.model_validate(fields)


# --- Définition --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"schema": SCHEMA, "schema_file": "s.json"}, "ensemble"),
        ({"schema": {"type": "objet"}}, "schema invalide"),
        ({"must_match": "("}, "must_match : expression régulière invalide"),
        ({"must_not_match": "["}, "must_not_match : expression régulière invalide"),
        ({"on_failure": "fallback"}, "fallback_message"),
        ({"on_failure": "ignorer"}, "on_failure"),
        ({"repair": {"tools": "parfois"}}, "repair.tools"),
    ],
)
def test_contract_definition_is_checked(fields: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        contract(**fields)


def test_contract_defaults_and_alias() -> None:
    defaults = contract()
    assert (defaults.normalize, defaults.on_failure, defaults.repair.max_attempts) == (
        True,
        "fail",
        1,
    )
    assert not defaults.repair.keeps_tools
    assert contract(schema=SCHEMA).json_schema == SCHEMA
    assert OutputContract.model_validate({"json_schema": SCHEMA}).json_schema == SCHEMA
    assert contract(repair={"tools": "allowed"}).repair.keeps_tools


# --- Normalisation et contrôle -------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "json_expected", "expected"),
    [
        ("  Bonjour  \n", False, "Bonjour"),
        ("```\nBonjour\n```", False, "Bonjour"),
        ("```json\n" + EMAIL + "\n```", True, EMAIL),
        ("Voici l'e-mail :\n```json\n" + EMAIL + "\n```\nBonne journée.", True, EMAIL),
        ("Voici l'e-mail : " + EMAIL + " — fin.", True, EMAIL),
        ("[1, 2] et du texte", True, "[1, 2]"),
        ("Pas de JSON ici.", True, "Pas de JSON ici."),
        ("{ cassé", True, "{ cassé"),
    ],
)
def test_normalize(text: str, json_expected: bool, expected: str) -> None:
    assert normalize(text, json_expected=json_expected) == expected


def test_a_conforming_output_passes_and_gives_its_data() -> None:
    checked = check(contract(schema=SCHEMA), "```json\n" + EMAIL + "\n```")
    assert checked.ok and checked.normalized
    assert checked.text == EMAIL
    assert checked.data == {"objet": "Votre devis", "corps": "Bonjour"}


@pytest.mark.parametrize(
    ("fields", "text", "problems"),
    [
        ({}, "   ", ["la sortie est vide"]),
        ({"schema": SCHEMA}, "Bonjour", ["ce n'est pas un JSON valide"]),
        (
            {"schema": SCHEMA},
            '{"objet": 3, "autre": 1}',
            ["(racine) : Additional properties", "(racine) : 'corps' is a required", "objet : 3"],
        ),
        ({"must_match": r"D-\d{4}-\d{3}"}, "Votre devis", ["motif attendu absent"]),
        (
            {"must_not_match": "```"},
            "a ``` b",
            ["motif interdit présent : ``` (position 2, longueur 3)"],
        ),
        ({"max_chars": 5}, "Bonjour", ["7 caractères, au-delà de 5"]),
        ({"normalize": False, "schema": SCHEMA}, "```json\n" + EMAIL + "\n```", ["JSON"]),
    ],
)
def test_problems_are_listed(fields: dict[str, Any], text: str, problems: list[str]) -> None:
    checked = check(contract(**fields), text)
    assert not checked.ok and checked.data is None
    assert len(checked.problems) == len(problems)
    for expected in problems:
        assert any(expected in found for found in checked.problems), checked.problems


def test_a_forbidden_text_is_not_copied_into_its_problem() -> None:
    forbidden = contract(must_not_match=KEY_PATTERN)
    text = f"Voici la clé : {SECRET}"
    checked = check(forbidden, text)

    assert checked.problems == (
        f"motif interdit présent : {KEY_PATTERN} "
        f"(position {text.index(SECRET)}, longueur {len(SECRET)})",
    )
    assert SECRET not in diagnostic(forbidden, checked.problems)


@pytest.fixture
def short_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bounded_regex, "REGEX_TIMEOUT", 0.2)


@pytest.mark.usefixtures("short_timeout")
@pytest.mark.parametrize(
    ("fields", "text", "problem"),
    [
        ({"must_match": SLOW}, SLOW_TEXT, "must_match : motif trop coûteux"),
        ({"must_not_match": SLOW}, SLOW_TEXT, "must_not_match : motif trop coûteux"),
        (
            {"schema": {"type": "string", "pattern": SLOW}},
            json.dumps(SLOW_TEXT),
            "schéma : motif trop coûteux",
        ),
    ],
)
def test_a_pattern_that_does_not_finish_fails_the_check(
    fields: dict[str, Any], text: str, problem: str
) -> None:
    """Faute de temps, on ne conclut ni « absent » ni « conforme » : le contrôle échoue."""
    checked = check(contract(**fields), text)

    assert not checked.ok and checked.data is None
    [found] = checked.problems
    assert found.startswith(problem) and SLOW in found


def test_a_pattern_of_the_regex_engine_is_accepted() -> None:
    assert check(contract(must_match=r"^\p{Lu}"), "École").ok
    assert not check(contract(must_match=r"^\p{Lu}"), "école").ok


def test_normalize_is_linear_on_a_long_run_of_spaces() -> None:
    text = "```\n" + " " * 40_000 + "x"
    start = time.perf_counter()
    assert normalize(text) == normalize(text, json_expected=True) == text
    assert time.perf_counter() - start < 3


def test_tool_data_is_checked_directly() -> None:
    schema: dict[str, JsonValue] = {"type": "object", "required": ["numero"]}
    assert check(contract(schema=schema), "", {"numero": "D-1"}).ok
    missing = check(contract(schema=schema), "", {"autre": 1})
    assert missing.problems == ("(racine) : 'numero' is a required property",)


def test_tool_data_wins_over_a_text_that_normalization_changes() -> None:
    # Le retour à la ligne final du texte ne doit pas faire écarter les données de l'outil.
    schema: dict[str, JsonValue] = {"type": "object", "required": ["a"]}
    for text in ("Fait", "Fait\n", "  Fait  ", "```\nFait\n```"):
        checked = check(contract(schema=schema), text, {"a": 1})
        assert checked.ok, text
        assert checked.data == {"a": 1}
    # Les données restent contrôlées par le schéma, quel que soit le texte.
    assert not check(contract(schema=schema), '{"a": 1}\n', {"b": 1}).ok


def test_two_consecutive_code_blocks_are_not_one_block() -> None:
    two = "```json\n{\"a\": 1}\n```\n```python\nprint('x')\n```"
    assert normalize(two) == two
    assert normalize(two, json_expected=True) == '{"a": 1}'
    assert check(contract(schema={"type": "object"}), two).data == {"a": 1}
    # Un seul bloc, avec ou sans langage, est toujours retiré.
    assert normalize("```json\n[1]\n```") == "[1]"
    assert normalize("```\nligne 1\nligne 2\n```") == "ligne 1\nligne 2"


def test_a_deeply_nested_output_is_a_problem_not_an_exception() -> None:
    depth = 200_000
    nested = "[" * depth + "]" * depth
    assert normalize(nested, json_expected=True) == nested
    checked = check(contract(schema={"type": "array"}), nested)
    assert not checked.ok
    assert "imbriqué" in checked.problems[0]


def test_diagnostic_gives_the_problems_and_the_schema() -> None:
    text = diagnostic(contract(schema=SCHEMA), ("objet : manquant",))
    assert text.startswith("La sortie ne respecte pas son contrat :\n- objet : manquant\n")
    assert 'Schéma JSON attendu : {"type":"object"' in text
    assert text.endswith("Réponds de nouveau avec la sortie corrigée, et seulement elle.")


# --- Décisions du guard --------------------------------------------------------------


def final(text: str) -> OnOutput:
    return OnOutput(state=STATE, output=Message.assistant(text))


def result(output: ToolOutput, *, kind: str = "role", contract_: OutputContract) -> AfterTool:
    spec = ToolSpec(name="rediger", description="Rédige.", kind=kind, output=contract_)  # pyright: ignore[reportArgumentType]
    call = PendingCall(call_id="c1", name="rediger")
    return AfterTool(state=STATE, call=call, spec=spec, arguments={}, output=output)


async def test_final_answer_passes_or_is_normalized() -> None:
    guard = ContractGuard(contract(schema=SCHEMA))
    assert (guard.name, guard.points) == (CONTRACT_POLICY, frozenset({"after_tool", "on_output"}))
    context = PolicyContext(name=CONTRACT_POLICY)
    assert await guard.decide(final(EMAIL), context) == CONTINUE
    normalized = await guard.decide(final(f"```json\n{EMAIL}\n```"), context)
    assert normalized == Replace(Message.assistant(EMAIL), reason="sortie normalisée")
    [passed, cleaned] = context.checks
    assert (passed.target, passed.outcome, passed.normalized) == ("output", "passed", False)
    assert (cleaned.outcome, cleaned.normalized) == ("passed", True)


async def test_final_answer_is_repaired_then_follows_on_failure() -> None:
    fields: dict[str, Any] = {"schema": SCHEMA, "repair": {"max_attempts": 1}}
    first = PolicyContext(name=CONTRACT_POLICY)
    decision = await ContractGuard(contract(**fields)).decide(final("Bonjour"), first)
    assert isinstance(decision, Retry) and not decision.tools
    assert first.checks[0].resolution == "retry"

    outcomes: list[object] = []
    for on_failure in ("fail", "unverified", "fallback"):
        guard = ContractGuard(
            contract(**fields, on_failure=on_failure, fallback_message="Relance à reprendre.")
        )
        tired = PolicyContext(name=CONTRACT_POLICY, attempt=1)
        outcomes.append((await guard.decide(final("Bonjour"), tired), tired.checks[0].resolution))
    fail, unverified, fallback = outcomes
    assert isinstance(fail, tuple) and isinstance(fail[0], Fail) and fail[1] == "fail"
    assert unverified == (CONTINUE, "unverified")
    assert fallback == (Replace("Relance à reprendre.", reason="message de repli"), "fallback")


async def test_a_forbidden_text_stays_out_of_the_decisions() -> None:
    forbidden = contract(must_not_match=KEY_PATTERN, repair={"max_attempts": 1})
    text = f"Voici la clé : {SECRET}"

    first = PolicyContext(name=CONTRACT_POLICY)
    retry = await ContractGuard(forbidden).decide(final(text), first)
    assert isinstance(retry, Retry) and SECRET not in retry.feedback
    assert first.checks[0].reason and SECRET not in first.checks[0].reason

    tired = PolicyContext(name=CONTRACT_POLICY, attempt=1)
    failure = await ContractGuard(forbidden).decide(final(text), tired)
    assert isinstance(failure, Fail) and SECRET not in failure.error
    assert tired.checks[0].reason and SECRET not in tired.checks[0].reason


async def test_repair_may_keep_the_tools() -> None:
    guard = ContractGuard(contract(must_match="D-", repair={"tools": "allowed"}))
    decision = await guard.decide(final("Bonjour"), PolicyContext(name=CONTRACT_POLICY))
    assert isinstance(decision, Retry) and decision.tools


async def test_role_output_is_structured_repaired_or_refused() -> None:
    guarded = contract(schema=SCHEMA)
    guard = ContractGuard()
    assert guard.points == frozenset({"after_tool"})
    context = PolicyContext(name=CONTRACT_POLICY)

    structured = await guard.decide(result(ToolOutput.text(EMAIL), contract_=guarded), context)
    assert isinstance(structured, Replace) and isinstance(structured.value, ToolOutput)
    assert structured.value.data == {"objet": "Votre devis", "corps": "Bonjour"}
    assert context.checks[0].target == "role:rediger"

    bad = result(ToolOutput.text("Bonjour"), contract_=guarded)
    assert isinstance(await guard.decide(bad, PolicyContext(name=CONTRACT_POLICY)), Retry)
    refused = await guard.decide(bad, PolicyContext(name=CONTRACT_POLICY, attempt=1))
    assert isinstance(refused, Replace) and isinstance(refused.value, ToolOutput)
    assert refused.value.is_error
    assert "Sortie non conforme" in refused.value.as_text
    assert "Sortie reçue :\nBonjour" in refused.value.as_text

    backup = contract(schema=SCHEMA, on_failure="fallback", fallback_message="À reprendre.")
    replaced = await guard.decide(
        result(ToolOutput.text("Bonjour"), contract_=backup), PolicyContext(name="x", attempt=1)
    )
    assert replaced == Replace(ToolOutput.text("À reprendre."), reason="repli")

    kept = contract(schema=SCHEMA, on_failure="unverified")
    marked = await guard.decide(
        result(ToolOutput.text("Bonjour"), contract_=kept), PolicyContext(name="x", attempt=1)
    )
    assert isinstance(marked, Replace) and isinstance(marked.value, ToolOutput)
    assert marked.value.unverified and marked.value.as_text == "Bonjour"


async def test_tools_are_never_repaired_and_errors_are_not_checked() -> None:
    guard = ContractGuard()
    tool_contract = contract(must_match="D-")
    context = PolicyContext(name=CONTRACT_POLICY)
    decision = await guard.decide(
        result(ToolOutput.text("rien"), kind="python", contract_=tool_contract), context
    )
    assert isinstance(decision, Replace) and isinstance(decision.value, ToolOutput)
    assert decision.value.is_error and context.checks[0].target == "tool:rediger"
    errored = result(ToolOutput.error("panne"), kind="python", contract_=tool_contract)
    assert await guard.decide(errored, PolicyContext(name=CONTRACT_POLICY)) == CONTINUE
    json_tool = ToolOutput(data={"objet": "a", "corps": "b"})
    assert (
        await guard.decide(
            result(json_tool, kind="python", contract_=contract(schema=SCHEMA)),
            PolicyContext(name=CONTRACT_POLICY),
        )
        == CONTINUE
    )


async def test_nothing_to_check_without_a_contract() -> None:
    guard = ContractGuard()
    context = PolicyContext(name=CONTRACT_POLICY)
    assert await guard.decide(final("x"), context) == CONTINUE
    spec = ToolSpec(name="calculer", description="Calcule.", kind="python")
    plain = AfterTool(
        state=STATE,
        call=PendingCall(call_id="c1", name="calculer"),
        spec=spec,
        arguments={},
        output=ToolOutput.text("2"),
    )
    assert await guard.decide(plain, context) == CONTINUE
    assert context.checks == []
    assert "sortie finale : False" in repr(guard)
