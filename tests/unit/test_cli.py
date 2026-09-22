# SPDX-License-Identifier: Apache-2.0
"""Ligne de commande : ``validate``, ``run``, ``resume``, ``approve``, ``keys``, ``schema``."""

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from conftest import ANSWER, QUESTION, ConfigFactory

from loom_ia.access.cli import main
from loom_ia.adapters.stores import JsonlEventStore
from loom_ia.config.keys import matches
from loom_ia.core.events import Event
from loom_ia.core.model import DEFAULT_TENANT, ToolOutput
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


# --- Trancher une approbation depuis un autre terminal (J4.5) -----------------


def _paused(path: Path, capsys: pytest.CaptureFixture[str]) -> str:
    """Lance le run de l'atelier, qui s'arrête sur son approbation ; rend son identifiant."""
    # Le run n'a pas répondu : la commande le dit par son code de sortie.
    assert main(["--config", str(path), "run", "demo", "Relance.", "--json"]) == 1
    started = json.loads(capsys.readouterr().out)
    assert started["status"] == "paused"
    return str(started["run_id"])


def test_run_says_what_a_paused_run_is_waiting_for(
    atelier: ConfigFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--config", str(atelier()), "run", "demo", "Relance."]) == 1
    erreur = capsys.readouterr().err
    assert "Statut     : paused" in erreur
    assert "En attente : envoyer_email" in erreur and "loom approve" in erreur


def test_approve_finishes_the_run_in_this_terminal(
    atelier: ConfigFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    path = atelier()
    run_id = _paused(path, capsys)

    assert main(["--config", str(path), "approve", run_id, "--by", "denis"]) == 0
    fini = capsys.readouterr()
    assert fini.out.strip() == "Relance envoyée."
    assert "Accordé : " in fini.err
    assert _decisions(path, "approval.granted") == ["denis"]


def test_reject_hands_the_reason_back_to_the_model(
    atelier: ConfigFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    path = atelier()
    run_id = _paused(path, capsys)

    # Un refus n'arrête pas le run : l'orchestrateur en fait ce qu'il peut.
    assert main(["--config", str(path), "reject", run_id, "--reason", "mauvais devis"]) == 0
    assert "Refusé : " in capsys.readouterr().err
    assert _decisions(path, "approval.rejected") == [None]


def test_no_wait_writes_the_decision_and_leaves_the_run(
    atelier: ConfigFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    path = atelier()
    run_id = _paused(path, capsys)

    assert main(["--config", str(path), "approve", run_id, "--no-wait"]) == 0
    capsys.readouterr()
    # Personne n'a piloté la reprise : le run attend toujours d'être repris.
    assert main(["--config", str(path), "resume", run_id]) == 0
    assert capsys.readouterr().out.strip() == "Relance envoyée."


def test_approving_a_run_that_awaits_nothing_is_refused(
    demo: ConfigFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    path = demo(storage=JOURNAL)
    assert main(["--config", str(path), "run", "demo", QUESTION, "--json"]) == 0
    run_id = json.loads(capsys.readouterr().out)["run_id"]

    assert main(["--config", str(path), "approve", run_id]) == 2
    assert "rien n'attend de décision" in capsys.readouterr().err


def test_correcting_arguments_needs_a_designated_call(
    atelier: ConfigFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    path = atelier()
    run_id = _paused(path, capsys)

    sans_call = main(["--config", str(path), "approve", run_id, "--arguments", "{}"])
    assert sans_call == 2 and "--call" in capsys.readouterr().err


def test_a_corrected_argument_is_the_one_that_goes(
    atelier: ConfigFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    path = atelier()
    run_id = _paused(path, capsys)
    call_id = _awaited(path, run_id)

    code = main(
        [
            "--config",
            str(path),
            "approve",
            run_id,
            "--call",
            call_id,
            "--arguments",
            '{"destinataire": "compta@example.com"}',
        ]
    )

    assert code == 0
    events = _events(path)
    # Le journal ne réécrit pas l'appel du modèle : ``tool.called`` garde ce
    # qu'il avait demandé, et c'est le résultat qui montre ce qui est parti.
    [called] = [e.payload for e in events if e.type == "tool.called"]
    [done] = [e.payload for e in events if e.type == "tool.completed"]
    assert getattr(called, "arguments", {})["destinataire"] == "mme.martin@example.com"
    assert "compta@example.com" in str(getattr(done, "output", ""))


def test_an_unknown_call_id_is_refused(
    atelier: ConfigFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    path = atelier()
    run_id = _paused(path, capsys)

    assert main(["--config", str(path), "approve", run_id, "--call", "absent"]) == 2
    assert "absent" in capsys.readouterr().err


def _events(path: Path) -> list[Event]:
    """Journal de l'unique session écrite par l'atelier."""
    store = JsonlEventStore(path.parent / "data")

    async def go() -> list[Event]:
        [record] = await store.sessions(DEFAULT_TENANT)
        events = await store.read(DEFAULT_TENANT, record.session_id)
        await store.aclose()
        return list(events)

    return asyncio.run(go())


def _decisions(path: Path, type_: str) -> list[str | None]:
    """Auteurs des décisions d'un type, tels que le journal les garde."""
    return [
        None if (by := event.facets.get("by")) is None else str(by)
        for event in _events(path)
        if event.type == type_
    ]


def _awaited(path: Path, run_id: str) -> str:
    """Identifiant du seul appel que le run attend."""
    [asked] = [e.payload for e in _events(path) if e.type == "approval.requested"]
    return str(getattr(asked, "call_id", ""))


def _write(directory: Path, journal: RunJournal) -> None:
    store = JsonlEventStore(directory)

    async def go() -> None:
        await store.append(journal.take(), expected_seq=0)
        await store.aclose()

    asyncio.run(go())


def _field(out: str, label: str) -> str:
    line = next(line for line in out.splitlines() if label in line)
    return line.split(label, 1)[1].strip()
