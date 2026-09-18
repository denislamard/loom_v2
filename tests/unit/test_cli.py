# SPDX-License-Identifier: Apache-2.0
"""Ligne de commande : ``validate``, ``run``, ``resume``, ``keys`` et ``schema``."""

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from conftest import ANSWER, QUESTION, ConfigFactory

from loom_ia.access.cli import main
from loom_ia.adapters.stores import JsonlEventStore
from loom_ia.config.keys import matches
from loom_ia.core.model import ToolOutput
from loom_ia.testing import RunJournal, tool_call_message

JOURNAL: dict[str, Any] = {"events": {"backend": "jsonl", "path": "journaux"}}


def test_validate_shows_what_the_config_declares(
    demo: ConfigFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--config", str(demo()), "validate"]) == 0
    out = capsys.readouterr().out
    assert "Modèles    : FAKE" in out
    assert "Agents     : demo" in out
    assert "Outils     : calculer" in out
    assert "Clés d'API : aucune" in out
    assert "1 agent(s) monté(s) sans erreur." in out


def test_run_prints_the_answer(demo: ConfigFactory, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--config", str(demo()), "run", "demo", QUESTION]) == 0
    captured = capsys.readouterr()
    assert captured.out.strip() == ANSWER
    assert "Statut     : completed" in captured.err


def test_run_in_json_gives_the_whole_result(
    demo: ConfigFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--config", str(demo()), "run", "demo", QUESTION, "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert (result["status"], result["text"], result["iterations"]) == ("completed", ANSWER, 2)


def test_run_in_stream_shows_the_answer_as_it_comes(
    demo: ConfigFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--config", str(demo()), "run", "demo", QUESTION, "--stream"]) == 0
    captured = capsys.readouterr()
    assert captured.out.startswith("Je calcule.")
    assert ANSWER in captured.out
    assert "· calculer(expr='12*7+3')" in captured.err


def test_resume_finishes_an_interrupted_run(
    demo: ConfigFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    path = demo(storage=JOURNAL)
    journal = RunJournal(agent="demo")
    journal.start(QUESTION).model_turn(
        tool_call_message(("c1", "calculer", {"expr": "12*7+3"}), text="Je calcule.")
    ).tool_results({"c1": ToolOutput.text("87")})
    _write(path.parent / "journaux", journal)

    assert main(["--config", str(path), "resume", journal.run_id]) == 0
    assert capsys.readouterr().out.strip() == ANSWER


def test_the_same_run_id_twice_is_refused(
    demo: ConfigFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    path = demo(storage=JOURNAL)
    command = ["--config", str(path), "run", "demo", QUESTION, "--run-id", "run-unique"]
    assert main(command) == 0
    capsys.readouterr()
    assert main(command) == 2
    assert "existe déjà" in capsys.readouterr().err


def test_an_unknown_agent_is_refused(
    demo: ConfigFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--config", str(demo()), "run", "absent", QUESTION]) == 2
    assert "Agent 'absent' inconnu" in capsys.readouterr().err


def test_a_missing_config_is_refused(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--config", str(tmp_path / "rien.yaml"), "validate"]) == 2
    assert "Configuration :" in capsys.readouterr().err


def test_keys_create_gives_a_key_and_its_fingerprint(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["keys", "create", "atelier", "--scope", "run", "--agent", "demo"]) == 0
    out = capsys.readouterr().out
    key = _field(out, "Clé        :")
    assert key.startswith("lk_")
    assert matches(key, _field(out, "hash:"))
    assert "- id: atelier" in out
    assert "scopes: [run]" in out and "agents: [demo]" in out


def test_schema_is_valid_json(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["schema"]) == 0
    schema = json.loads(capsys.readouterr().out)
    assert "AgentSpec" in schema["$defs"]
    assert schema["properties"]["version"]


def test_serve_starts_the_rest_access(
    demo: ConfigFactory, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    http = pytest.importorskip("loom_ia.access.http", reason="extra 'http' absent")
    served: dict[str, Any] = {}

    def fake_serve(loom: Any, *, host: str | None = None, port: int | None = None) -> None:
        served["agents"] = loom.names
        served["host"], served["port"] = host, port

    monkeypatch.setattr(http, "serve", fake_serve)
    assert main(["--config", str(demo()), "serve", "--port", "9100"]) == 0
    assert served == {"agents": ("demo",), "host": None, "port": 9100}
    assert "http://127.0.0.1:9100/v1" in capsys.readouterr().out


def test_mcp_serves_on_stdio(
    demo: ConfigFactory, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    server = pytest.importorskip("loom_ia.access.mcp_server", reason="extra 'mcp' absent")
    served: dict[str, Any] = {}

    async def fake_stdio(loom: Any) -> None:
        served["agents"] = loom.names

    monkeypatch.setattr(server, "run_stdio", fake_stdio)
    assert main(["--config", str(demo()), "mcp"]) == 0
    assert served == {"agents": ("demo",)}
    # Rien sur stdout : le protocole y passe.
    assert capsys.readouterr().out == ""


def _write(directory: Path, journal: RunJournal) -> None:
    store = JsonlEventStore(directory)

    async def go() -> None:
        await store.append(journal.take(), expected_seq=0)
        await store.aclose()

    asyncio.run(go())


def _field(out: str, label: str) -> str:
    line = next(line for line in out.splitlines() if label in line)
    return line.split(label, 1)[1].strip()
