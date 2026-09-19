# SPDX-License-Identifier: Apache-2.0
"""Rôles délégués dans la config : déclaration, contrôles, montage, run de bout en bout (J2.1)."""

from pathlib import Path
from typing import Any

import pytest
import yaml

from loom_ia.access.api import Loom
from loom_ia.access.cli import main
from loom_ia.adapters.stores import InMemoryEventStore
from loom_ia.agents import RoleSpec, ToolResultsContext
from loom_ia.config import ConfigError, load_config
from loom_ia.core.events import ModelResponded, RunCompleted, ToolCalled
from loom_ia.core.model import MAIN_ROLE, RunStatus
from loom_ia.engine import RoleTool, ToolResults
from loom_ia.runtime import build_agent

MAIN_SCRIPT: list[dict[str, Any]] = [
    {"tool_calls": [{"name": "calculer", "arguments": {"expr": "12*7+3"}}]},
    {
        "tool_calls": [
            {"name": "rediger", "arguments": {"ton": "poli", "calcul": {"$ref": "result:1"}}}
        ]
    },
]
MODELS: list[dict[str, Any]] = [
    {"id": "MAIN", "sdk": "fake", "model": "main-1", "params": {"script": MAIN_SCRIPT}},
    {
        "id": "ROLE",
        "sdk": "fake",
        "model": "role-1",
        "params": {"script": [{"text": "Cela fait 87."}]},
    },
]
OUTILS = """
from loom_ia.tools import tool


@tool
def calculer(expr: str) -> str:
    '''Calcule une expression.'''
    return str(eval(expr))
"""


def rediger(**changes: Any) -> dict[str, Any]:
    role: dict[str, Any] = {
        "name": "rediger",
        "description": "Rédige la réponse finale.",
        "model": "ROLE",
        "system_file": "rediger.md",
        "input_schema": {
            "type": "object",
            "properties": {"ton": {"type": "string"}, "calcul": {}},
            "required": ["ton"],
        },
        "context": ["user_input", {"tool_results": ["calculer"]}],
        "input_template": (
            "Ton : {{ args.ton }}\nDemande : {{ context.user_input }}\n"
            "Calcul : {{ context.tool_results.calculer }}"
        ),
        "llm": {"max_tokens": 256, "params": {"temperature": 0}},
        "terminal": True,
    }
    return {**role, **changes}


def agent(**changes: Any) -> dict[str, Any]:
    spec: dict[str, Any] = {
        "name": "demo",
        "main": {"model": "MAIN", "system": "Tu orchestres.", "llm": {"max_tokens": 512}},
        "tools": [{"python": "calculer"}],
        "roles": [rediger()],
    }
    return {**spec, **changes}


def write(tmp_path: Path, spec: dict[str, Any], *, models: list[dict[str, Any]] = MODELS) -> Path:
    (tmp_path / "agents").mkdir(exist_ok=True)
    (tmp_path / "prompts").mkdir(exist_ok=True)
    (tmp_path / "prompts" / "rediger.md").write_text("Tu rédiges.", encoding="utf-8")
    (tmp_path / "outils_roles.py").write_text(OUTILS, encoding="utf-8")
    config = {"version": 1, "imports": ["outils_roles"], "models": models}
    (tmp_path / "loom.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    (tmp_path / "agents" / "demo.yaml").write_text(yaml.safe_dump(spec), encoding="utf-8")
    return tmp_path / "loom.yaml"


async def test_roles_are_loaded_and_wired(tmp_path: Path) -> None:
    config = load_config(write(tmp_path, agent()))
    [role] = config.agents[0].roles
    assert isinstance(role, RoleSpec)
    assert role.system_file == tmp_path / "prompts" / "rediger.md"
    assert role.context == ("user_input", ToolResultsContext(tool_results=("calculer",)))
    assert role.tool_results == ("calculer",)

    agent_ = build_agent(config, "demo", InMemoryEventStore())
    context = agent_.context
    assert (context.max_tokens, dict(context.params)) == (512, {})
    tool = context.tools.get("rediger")
    assert isinstance(tool, RoleTool)
    assert (tool.spec.kind, tool.spec.terminal, tool.spec.side_effects) == ("role", True, "none")
    definition = tool.definition
    assert (definition.system, definition.max_tokens, dict(definition.params)) == (
        "Tu rédiges.",
        256,
        {"temperature": 0},
    )
    assert definition.context == ("user_input", ToolResults(tools=("calculer",)))
    assert definition.template is not None
    # Un client par modèle : main et le rôle n'en partagent pas ici.
    assert len(agent_.clients) == 2
    await agent_.aclose()

    shared = load_config(write(tmp_path, agent(roles=[rediger(model="MAIN")])))
    agent_ = build_agent(shared, "demo", InMemoryEventStore())
    assert len(agent_.clients) == 1
    await agent_.aclose()


async def test_run_with_a_terminal_role(tmp_path: Path) -> None:
    async with Loom(load_config(write(tmp_path, agent()))) as loom:
        result = await loom.run("demo", "Combien font 12 fois 7 plus 3 ?")
        events = await loom.events(result.run_id)

    assert result.status is RunStatus.COMPLETED
    assert (result.text, result.iterations) == ("Cela fait 87.", 2)
    calls = [e.payload for e in events if isinstance(e.payload, ToolCalled)]
    assert [(c.tool_name, c.refs) for c in calls] == [("calculer", ()), ("rediger", ("result:1",))]
    responses = [(e.role, e.payload) for e in events if isinstance(e.payload, ModelResponded)]
    assert [(role, p.model_id) for role, p in responses] == [
        (MAIN_ROLE, "main-1"),
        (MAIN_ROLE, "main-1"),
        ("rediger", "role-1"),
    ]
    closing = events[-1].payload
    assert isinstance(closing, RunCompleted) and closing.output_event_id is not None


def test_cli_shows_roles_and_the_terminal_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = str(write(tmp_path, agent()))
    assert main(["--config", path, "validate"]) == 0
    assert "demo : modèle MAIN, 1 outil(s) Python, rôle rediger (ROLE)" in capsys.readouterr().out

    # En direct, la sortie du rôle terminal ne passe pas par le flux du modèle.
    assert main(["--config", path, "run", "demo", "Combien ?", "--stream"]) == 0
    captured = capsys.readouterr()
    assert captured.out.strip() == "Cela fait 87."
    # Arguments tels que le modèle les a écrits (clés triées par le YAML du script).
    assert "· rediger(calcul={'$ref': 'result:1'}, ton='poli')" in captured.err


def broken(**changes: Any) -> dict[str, Any]:
    return agent(roles=[rediger(**changes)])


@pytest.mark.parametrize(
    ("spec", "message"),
    [
        (broken(name="main"), "Le nom 'main' est réservé à l'orchestrateur"),
        (broken(model="ABSENT"), "rôle 'rediger' : modèle 'ABSENT' non déclaré"),
        (broken(system_file="absent.md"), "roles[rediger].system_file — prompt introuvable"),
        (broken(system="x", system_file="rediger.md"), "ne peuvent pas être donnés ensemble"),
        (broken(output={"max_chars": 10}), "'output' : prévu pour le jalon J3.2"),
        (broken(fallbacks=["MAIN"]), "'fallbacks' : prévu pour le jalon J3.5"),
        (
            broken(context=["user_input", {"tool_results": ["calculer"]}, "attachments"]),
            "rôle 'rediger' : il reçoit les pièces jointes, mais le modèle 'ROLE' n'a pas "
            "la capacité vision",
        ),
        (
            broken(context=["attachments"], input_template="{{ context.attachments.x }}"),
            "attachments est un texte",
        ),
        (broken(context=[{"last_turns": 3}]), "'last_turns' : prévu pour le jalon J4.1"),
        (broken(context=["user_input", "user_input"]), "Contexte déclaré deux fois : user_input"),
        (broken(context=[], input_schema={"type": "object"}), "ne recevrait rien"),
        (broken(input_schema={"type": "array"}), "'type: object' attendu"),
        (broken(input_schema={"type": "object", "properties": 3}), "input_schema invalide"),
        (broken(input_template="{{ args.ton"), "input_template : '{{' sans '}}'"),
        (broken(input_template="{{ ctx.x }}"), "variable inconnue {{ ctx.x }}"),
        (broken(input_template="{{ args.montant }}"), "'montant' absent de input_schema"),
        (
            broken(input_template="{{ context.caller_context }}"),
            "contexte 'caller_context' non déclaré",
        ),
        (
            broken(input_template="{{ context.tool_results.autre }}"),
            "'autre' absent de tool_results",
        ),
        (broken(input_template="{{ context.user_input.x }}"), "user_input est un texte"),
        (
            broken(input_template="{{ context.user_input }}"),
            "contexte déclaré mais non utilisé : tool_results.calculer",
        ),
        (agent(roles=[rediger(), rediger()]), "Rôle déclaré deux fois : rediger"),
        (agent(subagents=[{"agent": "x"}]), "'subagents' : prévu pour le jalon J2.4"),
        (agent(main={"model": "MAIN", "fallbacks": ["ROLE"]}), "prévu pour le jalon J3.5"),
    ],
)
def test_role_checks_at_load(tmp_path: Path, spec: dict[str, Any], message: str) -> None:
    with pytest.raises(ConfigError) as caught:
        load_config(write(tmp_path, spec))
    assert message in str(caught.value)


def test_role_checks_when_mounting(tmp_path: Path) -> None:
    store = InMemoryEventStore()
    same_name = load_config(write(tmp_path, agent(roles=[rediger(name="calculer")])))
    with pytest.raises(ConfigError, match="plusieurs outils ou rôles s'appellent calculer"):
        build_agent(same_name, "demo", store)

    unknown = load_config(
        write(
            tmp_path,
            agent(roles=[rediger(context=[{"tool_results": ["absent"]}], input_template=None)]),
        )
    )
    with pytest.raises(ConfigError, match="tool_results désigne 'absent'"):
        build_agent(unknown, "demo", store)
