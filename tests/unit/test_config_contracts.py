# SPDX-License-Identifier: Apache-2.0
"""Contrats de sortie dans la config, et par les accès : schémas, diffusion, résultats (J3.2)."""

import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from conftest import QUESTION, ConfigFactory, demo_agent

from loom_ia.access import Loom
from loom_ia.access.cli import main
from loom_ia.access.progress import Progress
from loom_ia.adapters.stores import InMemoryEventStore
from loom_ia.agents import AgentSpec
from loom_ia.config import ConfigError, Registry, load_config
from loom_ia.core.events import GuardChecked, PolicyDecided, RunScope, RunStarted
from loom_ia.core.model import (
    DEFAULT_TENANT,
    OutputContract,
    RunId,
    SessionId,
    ToolOverrides,
    ToolSpec,
)
from loom_ia.guards import CONTRACT_POLICY
from loom_ia.runtime import build_agent, build_policies, stream_output

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"resultat": {"type": "integer"}},
    "required": ["resultat"],
}
# Le faux modèle de la démo répond en texte : un contrat JSON l'oblige à réparer.
SCRIPT: list[dict[str, Any]] = [
    {"text": "Je calcule.", "tool_calls": [{"name": "calculer", "arguments": {"expr": "12*7+3"}}]},
    {"text": "12 fois 7, plus 3, font 87."},
    {"text": '```json\n{"resultat": 87}\n```'},
]


def structured(demo: ConfigFactory, **output: Any) -> Path:
    model = {"id": "FAKE", "sdk": "fake", "model": "fake-1", "params": {"script": SCRIPT}}
    return demo(
        models=[model],
        storage={"events": {"backend": "jsonl", "path": "data"}},
        agents=[demo_agent(output={"schema": SCHEMA, **output})],
    )


async def test_a_structured_answer_by_the_python_and_rest_accesses(demo: ConfigFactory) -> None:
    path = structured(demo)
    async with Loom.from_config(path) as loom:
        result = await loom.run("demo", QUESTION)
        assert loom.context("demo").stream_output == "after_guards"
        events = await loom.events(result.run_id)
        pytest.importorskip("fastapi", reason="extra 'http' absent")
        httpx2 = pytest.importorskip("httpx2", reason="client HTTP de test absent")
        from loom_ia.access.http import create_app

        transport = httpx2.ASGITransport(create_app(loom))
        async with httpx2.AsyncClient(transport=transport, base_url="http://loom.test") as http:
            posted = (await http.post("/v1/agents/demo/runs", json={"message": QUESTION})).json()

    assert (result.text, result.data, result.unverified) == (
        '{"resultat": 87}',
        {"resultat": 87},
        False,
    )
    assert (posted["data"], posted["unverified"]) == ({"resultat": 87}, False)
    checked = [e.payload for e in events if isinstance(e.payload, GuardChecked)]
    assert [(c.outcome, c.resolution, c.normalized) for c in checked] == [
        ("failed", "retry", False),
        ("passed", None, True),
    ]
    progress = Progress()
    lines = [line for e in events if (line := progress.line(e)) is not None]
    assert lines[2].startswith(
        "· contrôle contract output : non conforme, réparation demandée — ce n'est pas un JSON"
    )
    assert lines[3] == "· contrôle contract output : conforme (après normalisation)"


def test_the_cli_streams_the_checked_answer(
    demo: ConfigFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    path = structured(demo, on_failure="unverified")
    assert main(["--config", str(path), "validate"]) == 0
    assert f"    politique {CONTRACT_POLICY} : after_tool, on_output" in capsys.readouterr().out
    assert main(["--config", str(path), "run", "demo", QUESTION, "--stream"]) == 0
    captured = capsys.readouterr()
    # after_guards : la réponse refusée n'est jamais affichée.
    assert captured.out.splitlines() == ["Je calcule.", '{"resultat": 87}']


def test_schema_files_are_read_at_load(tmp_path: Path) -> None:
    (tmp_path / "agents").mkdir()
    (tmp_path / "schemas").mkdir()
    (tmp_path / "schemas" / "relance.yaml").write_text(yaml.safe_dump(SCHEMA), encoding="utf-8")
    (tmp_path / "schemas" / "faux.json").write_text(
        json.dumps({"type": "entier"}), encoding="utf-8"
    )
    config = {
        "version": 1,
        "models": [{"id": "FAKE", "sdk": "fake", "model": "fake-1"}],
        "mcp_servers": [
            {
                "name": "crm",
                "transport": "stdio",
                "command": "python",
                "tools": {"fiche": {"output": {"schema_file": "schemas/relance.yaml"}}},
            }
        ],
    }
    (tmp_path / "loom.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    contract = {"schema_file": "schemas/relance.yaml", "max_chars": 500}
    agent = {
        "name": "demo",
        "main": {"model": "FAKE", "system": "Tu réponds."},
        "output": contract,
        "tools": [{"mcp": "crm", "tools": {"fiche": {"output": contract}}}],
        "roles": [
            {
                "name": "rediger",
                "description": "Rédige.",
                "model": "FAKE",
                "context": ["user_input"],
                "output": contract,
            }
        ],
    }
    (tmp_path / "agents" / "demo.yaml").write_text(yaml.safe_dump(agent), encoding="utf-8")
    loaded = load_config(tmp_path / "loom.yaml")
    spec = loaded.agents[0]
    assert spec.output is not None and spec.output.json_schema == SCHEMA
    assert spec.output.schema_file is None and spec.output.max_chars == 500
    assert spec.roles[0].output is not None and spec.roles[0].output.json_schema == SCHEMA
    mcp_ref = spec.mcp_tools[0].tools["fiche"].output
    assert mcp_ref is not None and mcp_ref.json_schema == SCHEMA
    server = loaded.mcp_servers[0].tools["fiche"].output
    assert server is not None and server.json_schema == SCHEMA

    agent["output"] = {"schema_file": "schemas/faux.json"}
    (tmp_path / "agents" / "demo.yaml").write_text(yaml.safe_dump(agent), encoding="utf-8")
    with pytest.raises(ConfigError, match="schema invalide"):
        load_config(tmp_path / "loom.yaml")


def test_contracts_reach_the_tools_and_the_stream_mode() -> None:
    contract = OutputContract(must_match="D-")
    spec = ToolSpec(name="fiche", description="Fiche.", kind="mcp")
    overridden = spec.overridden(ToolOverrides(output=contract, approval="always"))
    assert overridden.output is contract and overridden.approval == "always"

    base: dict[str, Any] = {"name": "demo", "main": {"model": "FAKE"}}
    plain = AgentSpec.model_validate(base)
    assert not plain.contracts
    assert build_policies(plain, Registry()).bound == ()
    assert stream_output(plain, build_policies(plain, Registry())) == "live"

    guarded = AgentSpec.model_validate({**base, "tools": [{"python": "x", "output": {}}]})
    policies = build_policies(guarded, Registry())
    assert [(b.name, sorted(b.points)) for b in policies.bound] == [
        (CONTRACT_POLICY, ["after_tool"])
    ]
    # Un outil sous contrat ne contrôle pas la réponse finale : diffusion live.
    assert stream_output(guarded, policies) == "live"
    role: dict[str, Any] = {
        "name": "rediger",
        "description": "Rédige.",
        "model": "FAKE",
        "context": ["user_input"],
        "terminal": True,
        "output": {},
    }
    terminal = AgentSpec.model_validate({**base, "roles": [role]})
    assert stream_output(terminal, build_policies(terminal, Registry())) == "after_guards"
    forced = AgentSpec.model_validate({**base, "output": {}, "stream_output": "live"})
    assert stream_output(forced, build_policies(forced, Registry())) == "live"


async def test_the_agent_carries_its_contract(demo: ConfigFactory) -> None:
    config = load_config(structured(demo))
    agent = build_agent(config, "demo", InMemoryEventStore())
    assert agent.context.output is not None and agent.context.output.json_schema == SCHEMA
    assert agent.context.stream_output == "after_guards"
    await agent.aclose()


def test_progress_lines_of_the_other_resolutions() -> None:
    scope = RunScope(
        tenant_id=DEFAULT_TENANT,
        session_id=SessionId("s"),
        run_id=RunId("r"),
        root_run_id=RunId("r"),
        agent="demo",
    )
    progress = Progress()
    payloads = [
        RunStarted(),
        GuardChecked(
            guard="contract",
            target="role:x",
            outcome="failed",
            reason="vide",
            resolution="fallback",
        ),
        GuardChecked(guard="judge:x", target="output", outcome="skipped", reason="sampled_out"),
        PolicyDecided(policy=CONTRACT_POLICY, point="after_tool", decision="replace"),
    ]
    lines = [progress.line(scope.draft(p).to_event(n)) for n, p in enumerate(payloads, start=1)]
    assert lines == [
        None,
        "· contrôle contract role:x : non conforme, remplacée par le message de repli — vide",
        "· contrôle judge:x output : ignoré — sampled_out",
        None,
    ]
