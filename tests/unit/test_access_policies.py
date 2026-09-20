# SPDX-License-Identifier: Apache-2.0
"""Politiques par les trois accès : mêmes décisions au journal, déroulé MCP et CLI (J3.1)."""

from pathlib import Path

import pytest
from conftest import ANSWER, QUESTION, ConfigFactory, demo_agent

from loom_ia.access import Loom
from loom_ia.access.cli import main
from loom_ia.core.events import Event, PolicyDecided
from loom_ia.core.model import RunId

# Politique d'exemple : la réponse finale est signée.
POLITIQUES = """
from loom_ia.policies import Decision, OnOutput, Replace, policy


@policy(points=["on_output"], decisions=["replace"])
def signature(subject: OnOutput) -> Decision:
    return Replace(f"{subject.output.text} (vérifié)", reason="signature ajoutée")
"""
SIGNED = f"{ANSWER} (vérifié)"
DECISIONS = [
    ("loom.require_tool", "before_model", "replace"),
    ("signature", "on_output", "replace"),
]
LINES = [
    "· politique loom.require_tool (before_model) : remplacé — aucun outil appelé dans le run : "
    "appel d'outil imposé",
    "· calculer(expr='12*7+3')",
    "· calculer : fait",
    "· politique signature (on_output) : remplacé — signature ajoutée",
]


@pytest.fixture
def config(demo: ConfigFactory, tmp_path: Path) -> Path:
    (tmp_path / "politiques_acces.py").write_text(POLITIQUES, encoding="utf-8")
    policies = [{"hook": "loom.require_tool"}, {"hook": "signature"}]
    return demo(
        imports=["outils_acces", "politiques_acces"],
        storage={"events": {"backend": "jsonl", "path": "data"}},
        agents=[demo_agent(policies=policies)],
    )


def decisions(events: list[Event]) -> list[tuple[str, str, str]]:
    return [
        (e.payload.policy, e.payload.point, e.payload.decision)
        for e in events
        if isinstance(e.payload, PolicyDecided)
    ]


async def test_the_three_accesses_journal_the_same_decisions(config: Path) -> None:
    pytest.importorskip("fastapi", reason="extra 'http' absent")
    pytest.importorskip("mcp", reason="extra 'mcp' absent")
    httpx2 = pytest.importorskip("httpx2", reason="client HTTP de test absent")
    from mcp.shared.memory import create_connected_server_and_client_session as connected

    from loom_ia.access.http import create_app
    from loom_ia.access.mcp_server import create_server

    notes: list[str | None] = []

    async def on_progress(progress: float, total: float | None, message: str | None) -> None:
        notes.append(message)

    runs: dict[str, RunId] = {}
    async with Loom.from_config(config) as loom:
        result = await loom.run("demo", QUESTION)
        assert result.text == SIGNED
        runs["python"] = result.run_id
        transport = httpx2.ASGITransport(create_app(loom))
        async with httpx2.AsyncClient(transport=transport, base_url="http://loom.test") as http:
            posted = await http.post("/v1/agents/demo/runs", json={"message": QUESTION})
            assert posted.json()["text"] == SIGNED
            runs["rest"] = RunId(posted.json()["run_id"])
        async with connected(create_server(loom)) as client:
            called = await client.call_tool(
                "demo", {"message": QUESTION}, progress_callback=on_progress
            )
            assert called.structuredContent is not None
            assert called.structuredContent["text"] == SIGNED
            runs["mcp"] = RunId(called.structuredContent["run_id"])
        journals = {name: await loom.events(run_id) for name, run_id in runs.items()}

    assert all(decisions(events) == DECISIONS for events in journals.values())
    types = {name: [e.type for e in events] for name, events in journals.items()}
    assert types["python"] == types["rest"] == types["mcp"]
    assert notes == LINES


def test_the_cli_shows_policies_and_decisions(
    config: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--config", str(config), "validate"]) == 0
    out = capsys.readouterr().out
    assert "Outils     : calculer\nPolitiques : signature\n" in out
    assert "    politique loom.require_tool : before_model" in out
    assert "    politique signature : on_output" in out

    assert main(["--config", str(config), "run", "demo", QUESTION, "--stream"]) == 0
    captured = capsys.readouterr()
    # Le déroulé, sans les lignes de log (INFO) qui passent aussi sur la sortie d'erreur.
    shown = [line for line in captured.err.splitlines() if line.startswith("·")]
    assert shown == LINES
    # La réponse diffusée a été remplacée ensuite : la réponse retenue suit.
    assert captured.out.endswith(f"[Réponse retenue]\n{SIGNED}\n")
