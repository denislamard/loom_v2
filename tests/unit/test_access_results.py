# SPDX-License-Identifier: Apache-2.0
"""Résultat d'un run par les trois accès : coûts, ventilation, verdicts, échecs lisibles (J3.6)."""

from collections.abc import AsyncGenerator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import jsonschema
import pytest
from conftest import ANSWER, MODEL, QUESTION, TREE_QUESTION, ConfigFactory, demo_agent

from loom_ia.access import Loom
from loom_ia.access.cli import main
from loom_ia.config.keys import fingerprint, new_api_key
from loom_ia.core.model import RunId, RunStatus, SessionId

pytest.importorskip("fastapi", reason="extra 'http' absent")
pytest.importorskip("httpx2", reason="client HTTP de test absent")
pytest.importorskip("mcp", reason="extra 'mcp' absent")

import httpx2
from mcp import ClientSession
from mcp.shared.memory import create_connected_server_and_client_session as connected
from mcp.types import ContentBlock, TextContent

from loom_ia.access.http import create_app
from loom_ia.access.mcp_server import REPORT_TOOL, STATUS_TOOL, create_server
from loom_ia.access.mcp_server.server import REPORT_OUTPUT, RUN_OUTPUT

CRITERIA: list[dict[str, Any]] = [{"name": "exact", "rule": "Le résultat est juste."}]
REFUSED = "réponse finale refusée par le juge output : exact"


def verdict(exact: float) -> dict[str, Any]:
    scores = [{"name": "exact", "score": exact, "reason": "calcul vérifié"}]
    return {"tool_calls": [{"name": "verdict", "arguments": {"criteria": scores}}]}


def judged(
    demo: ConfigFactory,
    exact: float = 1.0,
    *,
    security: dict[str, Any] | None = None,
    **judge: Any,
) -> Path:
    """``demo`` jugé : note ``exact`` du juge ; sous 0,8, la réponse est refusée sans réparation."""
    model = {
        "id": "JUDGE",
        "sdk": "fake",
        "model": "judge-1",
        "params": {"script": [verdict(exact)]},
        "pricing": {"input": 1.0, "output": 5.0},
    }
    spec = {"model": "JUDGE", "criteria": CRITERIA, "repair": {"max_attempts": 0}, **judge}
    return demo(
        models=[{**MODEL, "pricing": {"input": 2.0, "output": 10.0}}, model],
        storage={"events": {"backend": "jsonl", "path": "data"}},
        agents=[demo_agent(judge=spec)],
        **({"security": security} if security else {}),
    )


def role_judged(demo: ConfigFactory) -> Path:
    """``demo`` appelle deux fois le rôle ``verifier``, jugé : sortie refusée, puis réparée."""
    script: list[dict[str, Any]] = [
        {"tool_calls": [{"name": "verifier", "arguments": {"calcul": "12*7+3"}}]},
        {"tool_calls": [{"name": "verifier", "arguments": {"calcul": "12*7+3"}}]},
        {"text": ANSWER},
    ]
    role: dict[str, Any] = {
        "name": "verifier",
        "description": "Vérifie un calcul.",
        "model": "ROLE",
        "system": "Tu vérifies.",
        "input_schema": {"type": "object", "properties": {"calcul": {"type": "string"}}},
        "judge": {"model": "JUDGE", "criteria": CRITERIA},
    }
    judge = [
        {"with_text": "environ", **verdict(0.2)},
        {"without_text": "environ", **verdict(1.0)},
    ]
    models: list[dict[str, Any]] = [
        {**MODEL, "params": {"script": script}},
        {
            "id": "ROLE",
            "sdk": "fake",
            "model": "role-1",
            "params": {"script": [{"text": "environ 90"}, {"text": "87"}]},
        },
        {"id": "JUDGE", "sdk": "fake", "model": "judge-1", "params": {"script": judge}},
    ]
    return demo(models=models, agents=[demo_agent(roles=[role], tools=[])])


@asynccontextmanager
async def rest(path: Path) -> AsyncGenerator[tuple[Loom, httpx2.AsyncClient]]:
    async with Loom.from_config(path) as loom:
        transport = httpx2.ASGITransport(create_app(loom))
        async with httpx2.AsyncClient(transport=transport, base_url="http://loom.test") as http:
            yield loom, http


@asynccontextmanager
async def mcp(path: Path) -> AsyncGenerator[tuple[Loom, ClientSession]]:
    async with Loom.from_config(path) as loom:
        async with connected(create_server(loom)) as client:
            yield loom, client


# --- Python -------------------------------------------------------------------------


async def test_a_result_carries_its_breakdown_and_verdicts(demo: ConfigFactory) -> None:
    async with Loom.from_config(judged(demo)) as loom:
        result = await loom.run("demo", QUESTION)
        again = await loom.result(result.run_id)

    assert result.ok and result.text == ANSWER and not result.unverified
    assert again == result
    report = result.report
    assert report is not None and report.run_id == result.run_id
    assert report.total.cost == pytest.approx(result.cost_usd) and result.cost_usd > 0
    assert [line.name for line in report.roles] == ["main", "judge:output"]
    assert [line.name for line in report.models] == ["fake-1", "judge-1"]
    [seen] = result.verdicts
    assert (seen.run_id, seen.agent, seen.judge, seen.target) == (
        result.run_id,
        "demo",
        "output",
        "output",
    )
    assert (seen.model_id, seen.passed, seen.blocked, seen.attempt) == ("judge-1", True, False, 1)
    assert seen.call_id is None
    assert [(c.name, c.score) for c in seen.criteria] == [("exact", 1.0)]


async def test_a_failure_has_a_type_and_a_readable_message(demo: ConfigFactory) -> None:
    async with Loom.from_config(judged(demo, 0.2)) as loom:
        result = await loom.run("demo", QUESTION)

    assert result.status is RunStatus.FAILED and result.text == ""
    assert result.error_type == "guard.judge"
    assert result.error is not None and result.error.startswith(REFUSED)
    [seen] = result.verdicts
    assert seen.blocked and not seen.passed
    assert result.report is not None and result.report.runs[0].status == "failed"


async def test_a_kept_answer_is_marked_unverified(demo: ConfigFactory) -> None:
    async with Loom.from_config(judged(demo, 0.2, on_failure="unverified")) as loom:
        result = await loom.run("demo", QUESTION)

    assert result.ok and result.text == ANSWER and result.unverified
    assert [v.blocked for v in result.verdicts] == [True]


async def test_the_breakdown_covers_the_subruns(tree: ConfigFactory) -> None:
    async with Loom.from_config(tree()) as loom:
        result = await loom.run("demo", TREE_QUESTION)

    assert result.report is not None
    assert [(run.agent, run.depth, run.status) for run in result.report.runs] == [
        ("demo", 0, "completed"),
        ("verificateur", 1, "completed"),
    ]
    assert [line.name for line in result.report.roles] == ["demo · main", "verificateur · main"]
    assert result.verdicts == ()


# --- REST ---------------------------------------------------------------------------


async def test_rest_gives_costs_breakdown_and_verdicts(demo: ConfigFactory) -> None:
    async with rest(judged(demo)) as (loom, http):
        posted = await http.post("/v1/agents/demo/runs", json={"message": QUESTION})
        body = posted.json()
        direct = await loom.result(RunId(body["run_id"]))
        again = (await http.get(f"/v1/runs/{body['run_id']}")).json()

    assert posted.status_code == 201 and body == again
    assert body == direct.model_dump(mode="json")
    assert (body["status"], body["unverified"], body["error_type"]) == ("completed", False, None)
    assert body["cost_usd"] == pytest.approx(body["report"]["total"]["cost"])
    assert [line["name"] for line in body["report"]["roles"]] == ["main", "judge:output"]
    assert [(v["judge"], v["passed"]) for v in body["verdicts"]] == [("output", True)]


async def test_rest_answers_a_failed_run_with_201(demo: ConfigFactory) -> None:
    async with rest(judged(demo, 0.2)) as (_, http):
        posted = await http.post("/v1/agents/demo/runs", json={"message": QUESTION})

    body = posted.json()
    assert posted.status_code == 201
    assert (body["status"], body["error_type"]) == ("failed", "guard.judge")
    assert body["error"].startswith(REFUSED)


async def test_rest_unverified_flag(demo: ConfigFactory) -> None:
    async with rest(judged(demo, 0.2, on_failure="unverified")) as (_, http):
        posted = await http.post("/v1/agents/demo/runs", json={"message": QUESTION})

    assert (posted.json()["text"], posted.json()["unverified"]) == (ANSWER, True)


async def test_skipping_the_judges_needs_the_admin_scope(demo: ConfigFactory) -> None:
    runner, admin = new_api_key(), new_api_key()
    security = {
        "api_keys": [
            {"id": "atelier", "hash": fingerprint(runner), "scopes": ["run", "read"]},
            {"id": "admin", "hash": fingerprint(admin), "scopes": ["run", "read", "admin"]},
        ]
    }
    url = "/v1/agents/demo/runs"
    async with rest(judged(demo, 0.2, security=security)) as (_, http):
        refused = await http.post(
            url, json={"message": QUESTION, "judges": "skip"}, headers=_bearer(runner)
        )
        forced = await http.post(
            url, json={"message": QUESTION, "judges": "force"}, headers=_bearer(runner)
        )
        skipped = await http.post(
            url, json={"message": QUESTION, "judges": "skip"}, headers=_bearer(admin)
        )
        form = await http.post(
            url, data={"message": QUESTION, "judges": "skip"}, headers=_bearer(admin)
        )

    assert refused.status_code == 403 and "portée 'admin'" in refused.json()["detail"]
    assert forced.status_code == 201 and forced.json()["status"] == "failed"
    for response in (skipped, form):
        body = response.json()
        assert response.status_code == 201 and (body["text"], body["verdicts"]) == (ANSWER, [])


async def test_an_open_instance_may_skip_the_judges(demo: ConfigFactory) -> None:
    async with rest(judged(demo, 0.2)) as (_, http):
        skipped = await http.post(
            "/v1/agents/demo/runs", json={"message": QUESTION, "judges": "skip"}
        )
        wrong = await http.post(
            "/v1/agents/demo/runs", json={"message": QUESTION, "judges": "jamais"}
        )

    assert skipped.status_code == 201 and skipped.json()["text"] == ANSWER
    assert wrong.status_code == 422


async def test_the_session_report_route(demo: ConfigFactory) -> None:
    lecteur = new_api_key()
    security = {
        "api_keys": [
            {"id": "lecteur", "hash": fingerprint(lecteur), "scopes": ["read"], "agents": ["x"]}
        ]
    }
    async with rest(judged(demo)) as (loom, http):
        body = {"message": QUESTION, "session_id": "atelier"}
        first = (await http.post("/v1/agents/demo/runs", json=body)).json()
        second = (await http.post("/v1/agents/demo/runs", json=body)).json()
        report = await http.get("/v1/sessions/atelier/report")
        unknown = await http.get("/v1/sessions/absente/report")
        direct = await loom.report(session_id=SessionId("atelier"))

    assert report.status_code == 200 and report.json() == direct.model_dump(mode="json")
    assert [run["name"] for run in report.json()["runs"]] == [first["run_id"], second["run_id"]]
    assert report.json()["total"]["cost"] == pytest.approx(first["cost_usd"] + second["cost_usd"])
    assert unknown.status_code == 404 and "absente" in unknown.json()["detail"]

    # Même journal, servi avec une clé limitée à un autre agent.
    async with rest(judged(demo, security=security)) as (_, http):
        hidden = await http.get("/v1/sessions/atelier/report", headers=_bearer(lecteur))
    assert hidden.status_code == 403 and "agent 'demo'" in hidden.json()["detail"]


# --- MCP ----------------------------------------------------------------------------


async def test_mcp_gives_costs_breakdown_and_verdicts(demo: ConfigFactory) -> None:
    async with mcp(judged(demo)) as (loom, client):
        called = await client.call_tool("demo", {"message": QUESTION})
        assert called.structuredContent is not None
        direct = await loom.result(RunId(called.structuredContent["run_id"]))

    data = called.structuredContent
    jsonschema.validate(data, RUN_OUTPUT)
    assert not called.isError and _texts(called.content) == [ANSWER]
    assert (data["cost_usd"], data["unverified"]) == (direct.cost_usd, False)
    assert data["usage"] == direct.usage.model_dump(mode="json")
    assert direct.report is not None and data["report"] == direct.report.model_dump(mode="json")
    assert [(v["judge"], v["passed"]) for v in data["verdicts"]] == [("output", True)]


async def test_mcp_marks_a_failed_run_as_an_error(demo: ConfigFactory) -> None:
    async with mcp(judged(demo, 0.2)) as (_, client):
        called = await client.call_tool("demo", {"message": QUESTION})
        assert called.structuredContent is not None
        status = await client.call_tool(STATUS_TOOL, {"run_id": called.structuredContent["run_id"]})

    data = called.structuredContent
    jsonschema.validate(data, RUN_OUTPUT)
    [text] = _texts(called.content)
    assert called.isError and text.startswith(f"Échec de l'agent demo : {REFUSED}")
    assert "guard." not in text
    assert (data["status"], data["error_type"]) == ("failed", "guard.judge")
    # Relire un run échoué n'est pas une erreur de l'outil.
    assert not status.isError and status.structuredContent == data


async def test_mcp_says_when_an_answer_is_unverified(demo: ConfigFactory) -> None:
    async with mcp(judged(demo, 0.2, on_failure="unverified")) as (_, client):
        called = await client.call_tool("demo", {"message": QUESTION})

    assert called.structuredContent is not None and called.structuredContent["unverified"]
    assert not called.isError
    answer, note = _texts(called.content)
    assert answer == ANSWER and note.startswith("Réponse non vérifiée")


async def test_the_mcp_report_tool(demo: ConfigFactory) -> None:
    async with mcp(judged(demo)) as (loom, client):
        listed = {tool.name: tool for tool in (await client.list_tools()).tools}
        called = await client.call_tool("demo", {"message": QUESTION, "session_id": "atelier"})
        assert called.structuredContent is not None
        run_id = called.structuredContent["run_id"]
        by_run = await client.call_tool(REPORT_TOOL, {"run_id": run_id, "session_id": "atelier"})
        by_session = await client.call_tool(REPORT_TOOL, {"session_id": "atelier"})
        expected = await loom.report(session_id=SessionId("atelier"))
        refusals = [
            await client.call_tool(REPORT_TOOL, arguments)
            for arguments in ({"run_id": "absent"}, {"session_id": "absente"}, {})
        ]

    assert listed[REPORT_TOOL].outputSchema == REPORT_OUTPUT
    assert not by_run.isError and by_run.structuredContent is not None
    assert by_run.structuredContent["run_id"] == run_id
    assert _texts(by_run.content)[0].startswith(f"Consommation — run {run_id}")
    assert by_session.structuredContent == expected.model_dump(mode="json")
    assert _texts(by_session.content)[0].startswith("Consommation — session atelier")
    assert all(refusal.isError for refusal in refusals)
    assert _texts(refusals[0].content) == ["Run absent inconnu"]
    assert _texts(refusals[1].content) == ["Session absente inconnue"]


# --- CLI ----------------------------------------------------------------------------


def test_the_cli_shows_verdicts_and_the_error_type(
    demo: ConfigFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    path = judged(demo, 0.2)
    assert main(["--config", str(path), "run", "demo", QUESTION]) == 1
    err = capsys.readouterr().err
    assert (
        "Juge       : output (réponse finale), tentative 1 — refusée : exact 0,20 (seuil 0,80)"
        in err
    )
    assert f"Erreur     : {REFUSED}" in err and "(guard.judge)" in err

    path = judged(demo, 0.2, on_failure="unverified")
    assert main(["--config", str(path), "run", "demo", QUESTION]) == 0
    assert "Vérifiée   : non" in capsys.readouterr().err


async def test_verdicts_name_the_judged_call(demo: ConfigFactory) -> None:
    async with Loom.from_config(role_judged(demo)) as loom:
        result = await loom.run("demo", QUESTION)

    assert [(v.attempt, v.blocked) for v in result.verdicts] == [
        (1, True),
        (2, False),
        (1, True),
        (2, False),
    ]
    first, repaired, second, again = (v.call_id for v in result.verdicts)
    assert first is not None and second is not None
    assert first == repaired != second == again


def test_the_cli_numbers_the_judged_calls(
    demo: ConfigFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--config", str(role_judged(demo)), "run", "demo", QUESTION]) == 0
    judged_lines = [
        line for line in capsys.readouterr().err.splitlines() if line.startswith("Juge")
    ]
    assert judged_lines == [
        "Juge       : verifier (rôle verifier, appel 1), tentative 1 — refusée : "
        "exact 0,20 (seuil 0,80)",
        "Juge       : verifier (rôle verifier, appel 1), tentative 2 — acceptée : exact 1,00",
        "Juge       : verifier (rôle verifier, appel 2), tentative 1 — refusée : "
        "exact 0,20 (seuil 0,80)",
        "Juge       : verifier (rôle verifier, appel 2), tentative 2 — acceptée : exact 1,00",
    ]


def _texts(content: Sequence[ContentBlock]) -> list[str]:
    return [part.text for part in content if isinstance(part, TextContent)]


def _bearer(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}
