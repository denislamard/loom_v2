# SPDX-License-Identifier: Apache-2.0
"""Juges dans la config, au montage et par les accès (J3.3)."""

import logging
from pathlib import Path
from typing import Any

import pytest
from conftest import ANSWER, MODEL, QUESTION, ConfigFactory, demo_agent

from loom_ia.access import Loom
from loom_ia.access.cli import main
from loom_ia.access.progress import Progress
from loom_ia.agents import AgentSpec
from loom_ia.config import ConfigError, load_config
from loom_ia.core.events import GuardChecked, JudgeEvaluated, ModelResponded
from loom_ia.guards import JudgeGuard

CRITERIA: list[dict[str, Any]] = [
    {"name": "exact", "rule": "Le résultat est juste."},
    {"name": "bref", "rule": "La réponse tient en une phrase.", "blocking": False},
]


def verdict(exact: float, bref: float = 1.0) -> dict[str, Any]:
    scores = [
        {"name": "exact", "score": exact, "reason": "calcul vérifié"},
        {"name": "bref", "score": bref, "reason": "une phrase"},
    ]
    return {"tool_calls": [{"name": "verdict", "arguments": {"criteria": scores}}]}


JUDGE_MODEL: dict[str, Any] = {
    "id": "JUDGE",
    "sdk": "fake",
    "model": "judge-1",
    "params": {"script": [verdict(1.0)]},
    "pricing": {"input": 1.0, "output": 5.0},
}
CONDITION = """
def calcul(sortie):
    return "87" in sortie.output


pas_une_fonction = 42
"""


def judged(demo: ConfigFactory, *, judge: dict[str, Any] | None = None, **root: Any) -> Path:
    spec = {"model": "JUDGE", "criteria": CRITERIA, "context": ["user_input"], **(judge or {})}
    return demo(
        models=[MODEL, JUDGE_MODEL],
        storage={"events": {"backend": "jsonl", "path": "data"}},
        agents=[demo_agent(judge=spec)],
        **root,
    )


async def test_a_judged_answer_by_the_python_access(demo: ConfigFactory) -> None:
    path = judged(demo)
    async with Loom.from_config(path) as loom:
        context = loom.context("demo")
        assert [b.name for b in context.policies.bound] == ["loom.judge.output"]
        assert context.stream_output == "after_guards"
        [guard] = [b.policy for b in context.policies.bound]
        assert isinstance(guard, JudgeGuard) and guard.model_spec.id == "JUDGE"
        result = await loom.run("demo", QUESTION)
        events = await loom.events(result.run_id)

    assert result.text == ANSWER and result.cost_usd > 0
    [evaluated] = [e.payload for e in events if isinstance(e.payload, JudgeEvaluated)]
    assert evaluated.passed and evaluated.model_id == "judge-1"
    [responded] = [e for e in events if isinstance(e.payload, ModelResponded) and e.payload.judge]
    assert responded.role == "judge:output"
    assert result.cost_usd == pytest.approx(responded.payload.cost_usd)  # type: ignore[union-attr]
    progress = Progress()
    lines = [line for e in events if (line := progress.line(e)) is not None]
    assert lines[-2:] == [
        "· juge output (JUDGE) : exact 1,00, bref 1,00",
        "· contrôle judge output : conforme",
    ]


def test_the_cli_lists_skips_and_forces_the_judges(
    demo: ConfigFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    path = judged(demo, judge={"when": {"sample": 0.0}, "criteria": CRITERIA[1:]})
    assert main(["--config", str(path), "validate"]) == 0
    out = capsys.readouterr().out
    assert "    politique loom.judge.output : on_output" in out
    assert "    juge output (réponse finale) : modèle JUDGE, 1 critère(s), sample 0" in out

    assert main(["--config", str(path), "run", "demo", QUESTION, "--stream"]) == 0
    err = capsys.readouterr().err
    assert "· contrôle judge output : ignoré — hors échantillon (when.sample)" in err
    assert (
        "Juge       : output (réponse finale) — non jugée : hors échantillon (when.sample)" in err
    )
    assert (
        main(["--config", str(path), "run", "demo", QUESTION, "--stream", "--judges", "skip"]) == 0
    )
    err = capsys.readouterr().err
    assert "ignoré — sauté à la demande de l'appelant (judges: skip)" in err
    assert (
        "Juge       : output (réponse finale) — non jugée : "
        "sauté à la demande de l'appelant (judges: skip)" in err
    )


async def test_a_role_judge_with_a_condition(demo: ConfigFactory, tmp_path: Path) -> None:
    (tmp_path / "conditions.py").write_text(CONDITION, encoding="utf-8")
    script: list[dict[str, Any]] = [
        {"tool_calls": [{"name": "verifier", "arguments": {"calcul": "12*7+3"}}]},
        {"text": ANSWER},
    ]
    role: dict[str, Any] = {
        "name": "verifier",
        "description": "Vérifie un calcul.",
        "model": "ROLE",
        "system": "Tu vérifies.",
        "input_schema": {"type": "object", "properties": {"calcul": {"type": "string"}}},
        "judge": {
            "model": "JUDGE",
            "criteria": CRITERIA,
            "when": {"condition": "conditions:calcul"},
        },
    }
    role_model = {"id": "ROLE", "sdk": "fake", "model": "role-1"}
    models = [
        {**MODEL, "params": {"script": script}},
        {**role_model, "params": {"script": [{"text": "87"}]}},
        JUDGE_MODEL,
    ]
    path = demo(models=models, agents=[demo_agent(roles=[role], tools=[])])
    async with Loom.from_config(path) as loom:
        result = await loom.run("demo", QUESTION)
        events = await loom.events(result.run_id)
    checks = [e.payload for e in events if isinstance(e.payload, GuardChecked)]
    assert [(c.target, c.outcome) for c in checks] == [("role:verifier", "passed")]

    role["judge"] = {**role["judge"], "when": {"condition": "conditions:pas_une_fonction"}}
    path = demo(models=models, agents=[demo_agent(roles=[role], tools=[])])
    with pytest.raises(ConfigError, match="n'est pas une fonction"):
        Loom.from_config(path).context("demo")


def test_judge_warnings_at_startup(demo: ConfigFactory, caplog: pytest.LogCaptureFixture) -> None:
    path = judged(demo, judge={"model": "FAKE", "when": {"sample": 0.5}})
    with caplog.at_level(logging.WARNING, logger="loom_ia.runtime.wiring"):
        Loom.from_config(path).context("demo")
    messages = [r.getMessage() for r in caplog.records]
    assert (
        "Agent 'demo', juge 'output' : même modèle que la sortie qu'il évalue (FAKE)"
        in (messages[0])
    )
    assert messages[1] == (
        "Agent 'demo', juge 'output' : bloquant, mais ne juge qu'une partie des runs (sample 0.5)"
    )


@pytest.mark.parametrize(
    ("judge", "message"),
    [
        ({"model": "ABSENT"}, "juge 'output' : modèle 'ABSENT' non déclaré"),
        ({"on_failure": "fallback"}, "on_failure: fallback demande un 'fallback_message'"),
        ({"criteria": [CRITERIA[0], CRITERIA[0]]}, "Critère déclaré deux fois : exact"),
        ({"criteria": []}, "criteria"),
        ({"context": ["session_summary", "session_summary"]}, "Contexte déclaré deux fois"),
        ({"context": ["attachments"]}, "n'a pas la capacité vision"),
        # D11 : refusé avec un message clair, plutôt qu'accepté puis ignoré.
        (
            {"context": ["user_input", "session_state"]},
            "contexte 'session_state' : un juge ne le reçoit pas dans cette version",
        ),
        (
            {"context": [{"session_state": ["client"]}]},
            "contexte 'session_state' : un juge ne le reçoit pas",
        ),
    ],
)
def test_judge_config_errors(demo: ConfigFactory, judge: dict[str, Any], message: str) -> None:
    path = judged(demo, judge=judge)
    with pytest.raises(ConfigError, match=message):
        load_config(path)


JUDGE = {"model": "JUDGE", "criteria": CRITERIA}


@pytest.mark.parametrize(
    ("agent", "where", "what"),
    [
        (
            demo_agent(tools=[{"python": "calculer", "judge": JUDGE}]),
            "tools.0.python",
            "un outil Python",
        ),
        (demo_agent(tools=[{"mcp": "crm", "judge": JUDGE}]), "tools.0.mcp", "un serveur MCP"),
        (
            demo_agent(tools=[{"source": "forge", "judge": JUDGE}]),
            "tools.0.source",
            "une source d'outils",
        ),
        (
            demo_agent(subagents=[{"agent": "autre", "judge": JUDGE}]),
            "subagents.0",
            "la référence d'un sous-agent",
        ),
    ],
)
def test_a_judge_on_a_tool_is_refused_with_its_reason(
    demo: ConfigFactory, agent: dict[str, Any], where: str, what: str
) -> None:
    # Test E5 du 19/10 : « tools.1.python.judge — Extra inputs are not permitted ».
    path = demo(models=[MODEL, JUDGE_MODEL], agents=[agent])
    with pytest.raises(ConfigError) as caught:
        load_config(path)
    message = str(caught.value)
    assert where in message and f"{what} n'a pas de juge" in message
    assert "(roles[].judge)" in message and "Extra inputs" not in message


def test_a_judge_correlated_for_one_client_names_it(
    demo: ConfigFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    # Test E6 du 19/10 : « (OPUS et HAIKU) », sans le client, pour un client chez
    # qui OPUS devient HAIKU.
    path = judged(
        demo,
        judge={"when": {"sample": 0.5}},
        tenants=[{"id": "default"}, {"id": "dupont", "models": {"JUDGE": "FAKE"}}],
    )
    assert main(["--config", str(path), "validate"]) == 0
    err = capsys.readouterr().err
    assert (
        "Client 'dupont', agent 'demo', juge 'output' : même modèle que la sortie qu'il évalue "
        "(juge JUDGE → FAKE, sortie FAKE) : juge corrélé" in err
    )
    assert (
        "Client 'default', agent 'demo', juge 'output' : bloquant, mais ne juge qu'une partie "
        "des runs (sample 0.5)" in err
    )
    assert "Client 'default', agent 'demo', juge 'output' : même modèle" not in err
    # En prod, l'erreur nomme le client.
    assert main(["--config", str(path), "--profile", "prod", "validate"]) == 2
    assert "Profil prod : Client 'default', agent 'demo', juge 'output'" in capsys.readouterr().err


def test_two_judges_cannot_share_a_name() -> None:
    role: dict[str, Any] = {
        "name": "output",
        "description": "Rôle mal nommé.",
        "model": "FAKE",
        "input_schema": {"type": "object", "properties": {"x": {"type": "string"}}},
        "judge": {"model": "FAKE", "criteria": CRITERIA},
    }
    with pytest.raises(ValueError, match="Juge déclaré deux fois : output"):
        AgentSpec.model_validate(
            {
                "name": "demo",
                "main": {"model": "FAKE"},
                "judge": {"model": "FAKE", "criteria": CRITERIA},
                "roles": [role],
            }
        )
    role["judge"] = {"model": "FAKE", "criteria": CRITERIA, "name": "redaction"}
    spec = AgentSpec.model_validate(
        {
            "name": "demo",
            "main": {"model": "FAKE"},
            "judge": {"model": "FAKE", "criteria": CRITERIA},
            "roles": [role],
        }
    )
    assert [(name, r.name if r else None) for name, r, _ in spec.judges] == [
        ("output", None),
        ("redaction", "output"),
    ]
