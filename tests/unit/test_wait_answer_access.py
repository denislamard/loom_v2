# SPDX-License-Identifier: Apache-2.0
"""``loom.ask`` par la CLI et le REST (wait_answer, lot 3)."""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from test_wait_answer import config

from loom_ia.access import Loom
from loom_ia.access.cli import main
from loom_ia.config.keys import fingerprint, new_api_key

pytest.importorskip("fastapi", reason="extra 'http' absent")
pytest.importorskip("httpx2", reason="client HTTP de test absent")

import httpx2
import yaml

from loom_ia.access.http import create_app

# --- CLI -------------------------------------------------------------------------------


def test_cli_shows_the_question_and_a_message_answers_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = config(tmp_path)
    code = main(["--config", str(path), "run", "demo", "Prépare un devis.", "--session", "s1"])
    out, err = capsys.readouterr()
    # Un run qui attend la réponse de l'utilisateur n'a pas échoué.
    assert code == 0
    assert "Pour quel client" in out and "1. Dupont" in out and "2. Martin" in out
    assert "Statut     : paused" in err and 'loom run demo "<réponse>" --session s1' in err
    code = main(["--config", str(path), "run", "demo", "Dupont", "--session", "s1"])
    out, _ = capsys.readouterr()
    assert code == 0 and "Devis préparé." in out


def test_cli_answer_command(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = config(tmp_path)
    main(["--config", str(path), "run", "demo", "Prépare un devis.", "--json"])
    paused = capsys.readouterr().out
    import json

    run_id = json.loads(paused)["run_id"]
    assert main(["--config", str(path), "answer", run_id, "--answer", "Dupont;Martin"]) == 2
    assert "un seul choix" in capsys.readouterr().err
    assert main(["--config", str(path), "answer", run_id, "--answer", "Martin"]) == 0
    out, err = capsys.readouterr()
    assert "Répondu" in err and "Devis préparé" in out


def test_cli_stream_routes_the_answer(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = config(tmp_path)
    main(["--config", str(path), "run", "demo", "Prépare un devis.", "--session", "s1", "--stream"])
    _, err = capsys.readouterr()
    assert "question à l'utilisateur : Pour quel client" in err
    code = main(["--config", str(path), "run", "demo", "Dupont", "--session", "s1", "--stream"])
    out, err = capsys.readouterr()
    assert code == 0 and "Devis préparé." in out
    assert "réponse de l'utilisateur reçue" in err


# --- REST ------------------------------------------------------------------------------

LANCEUR = new_api_key()
LECTEUR = new_api_key()


def keyed(tmp_path: Path) -> Path:
    path = config(tmp_path)
    root = yaml.safe_load(path.read_text(encoding="utf-8"))
    root["security"] = {
        "api_keys": [
            {"id": "appli", "hash": fingerprint(LANCEUR), "scopes": ["run", "read"]},
            {"id": "lecture", "hash": fingerprint(LECTEUR), "scopes": ["read"]},
        ]
    }
    path.write_text(yaml.safe_dump(root), encoding="utf-8")
    return path


@asynccontextmanager
async def http_serving(path: Path) -> AsyncGenerator[tuple[Loom, httpx2.AsyncClient]]:
    async with Loom.from_config(path) as loom:
        transport = httpx2.ASGITransport(create_app(loom))
        async with httpx2.AsyncClient(transport=transport, base_url="http://loom.test") as http:
            yield loom, http


def test_rest_answer_route_needs_run_not_approve(tmp_path: Path) -> None:
    async def go() -> None:
        async with http_serving(keyed(tmp_path)) as (loom, http):
            appli = {"Authorization": f"Bearer {LANCEUR}"}
            started = await http.post(
                "/v1/agents/demo/runs",
                json={"message": "Prépare un devis.", "session_id": "s1"},
                headers=appli,
            )
            paused = started.json()
            assert started.status_code == 201 and paused["status"] == "paused"
            assert paused["pending_questions"][0]["questions"][0]["header"] == "Client"
            url = f"/v1/runs/{paused['run_id']}/answer?session_id=s1"
            lecture = {"Authorization": f"Bearer {LECTEUR}"}
            refused = await http.post(url, json={"text": "Dupont"}, headers=lecture)
            assert refused.status_code == 403
            bad = await http.post(url, json={"answers": [["a"], ["b"]]}, headers=appli)
            assert bad.status_code == 422 and "2 réponse" in bad.json()["detail"]
            done = await http.post(url, json={"answers": [["Dupont"]]}, headers=appli)
            assert done.status_code == 200
            assert done.json() == {
                "run_id": paused["run_id"],
                "call_id": paused["pending_questions"][0]["call_id"],
            }
            await loom.drain()
            finished = await http.get(f"/v1/runs/{paused['run_id']}?session_id=s1", headers=appli)
            assert finished.json()["status"] == "completed"
            unknown = await http.post(url, json={"text": "x"}, headers=appli)
            assert unknown.status_code == 404

    asyncio.run(go())


def test_rest_a_new_message_answers_and_resumes(tmp_path: Path) -> None:
    async def go() -> None:
        async with http_serving(config(tmp_path)) as (_, http):
            first = await http.post(
                "/v1/agents/demo/runs", json={"message": "Prépare un devis.", "session_id": "s1"}
            )
            again = await http.post(
                "/v1/agents/demo/runs", json={"message": "Dupont", "session_id": "s1"}
            )
            assert again.status_code == 201
            assert again.json()["run_id"] == first.json()["run_id"]
            assert again.json()["status"] == "completed"

    asyncio.run(go())


def test_rest_reading_without_read_content_hides_the_question(tmp_path: Path) -> None:
    async def go() -> None:
        async with http_serving(keyed(tmp_path)) as (_, http):
            appli = {"Authorization": f"Bearer {LANCEUR}"}
            started = await http.post(
                "/v1/agents/demo/runs",
                json={"message": "Prépare un devis.", "session_id": "s1"},
                headers=appli,
            )
            run_id = started.json()["run_id"]
            read = await http.get(f"/v1/runs/{run_id}?session_id=s1", headers=appli)
            body = read.json()
            assert body["pending_questions"][0]["questions"] == []
            assert "pending_questions[].questions" in body["redacted"]

    asyncio.run(go())


def test_cli_sessions_show_lists_the_question(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = config(tmp_path)
    main(["--config", str(path), "run", "demo", "Prépare un devis.", "--session", "s1"])
    capsys.readouterr()
    assert main(["--config", str(path), "sessions", "show", "s1"]) == 0
    out = capsys.readouterr().out
    assert "question (fake_0_0)" in out and "sans échéance : Pour quel client" in out
    assert 'loom run demo "<réponse>" --session s1' in out


def test_rest_a_background_message_answers_too(tmp_path: Path) -> None:
    async def go() -> None:
        async with http_serving(config(tmp_path)) as (loom, http):
            first = await http.post(
                "/v1/agents/demo/runs", json={"message": "Prépare un devis.", "session_id": "s1"}
            )
            again = await http.post(
                "/v1/agents/demo/runs",
                json={"message": "Dupont", "session_id": "s1", "background": True},
            )
            assert again.status_code == 202
            assert again.json()["run_id"] == first.json()["run_id"]
            await loom.drain()
            finished = await http.get(f"/v1/runs/{first.json()['run_id']}?session_id=s1")
            assert finished.json()["status"] == "completed"

    asyncio.run(go())


def test_cli_inspect_tells_a_question_from_a_refusal(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import json

    path = config(tmp_path)
    main(["--config", str(path), "run", "demo", "Prépare un devis.", "--session", "s1", "--json"])
    run_id = json.loads(capsys.readouterr().out)["run_id"]
    assert main(["--config", str(path), "inspect", run_id, "--session", "s1"]) == 0
    out = capsys.readouterr().out
    assert "question à l'utilisateur — en attente" in out
    assert "question  : Pour quel client faut-il préparer le devis ?" in out
    main(["--config", str(path), "run", "demo", "Dupont", "--session", "s1"])
    capsys.readouterr()
    assert main(["--config", str(path), "inspect", run_id, "--session", "s1"]) == 0
    out = capsys.readouterr().out
    assert "question à l'utilisateur — répondue" in out
    assert "question ask — réponse rendue au modèle" in out
    assert "refusé" not in out
    assert "1 question(s) à l'utilisateur" in out
