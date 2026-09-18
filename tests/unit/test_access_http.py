# SPDX-License-Identifier: Apache-2.0
"""Accès REST : routes, flux SSE et clés d'API.

Le client parle à l'application ASGI en direct : pas de serveur, pas de port.
"""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path as FilePath

import pytest
from conftest import ANSWER, QUESTION, ConfigFactory, demo_agent

from loom_ia.access import Loom
from loom_ia.config.keys import fingerprint, new_api_key
from loom_ia.core.model import new_run_id

pytest.importorskip("fastapi", reason="extra 'http' absent")
pytest.importorskip("httpx2", reason="client HTTP de test absent")

import httpx2

from loom_ia.access.http import create_app

CLE = new_api_key()
SECURITY = {
    "api_keys": [
        {"id": "atelier", "hash": fingerprint(CLE), "scopes": ["run", "read"]},
    ]
}


@asynccontextmanager
async def serving(path: FilePath) -> AsyncGenerator[tuple[Loom, httpx2.AsyncClient]]:
    """Une instance et un client parlant à son application ASGI."""
    async with Loom.from_config(path) as loom:
        transport = httpx2.ASGITransport(create_app(loom))
        async with httpx2.AsyncClient(transport=transport, base_url="http://loom.test") as http:
            yield loom, http


async def test_agents_are_listed(demo: ConfigFactory) -> None:
    path = demo(agents=[demo_agent(), demo_agent(name="cache", expose={"rest": False})])
    async with serving(path) as (_, http):
        response = await http.get("/v1/agents")
        assert response.status_code == 200
        assert response.json() == [
            {"name": "demo", "description": "Répond aux questions de calcul."}
        ]


async def test_a_run_answers_and_is_readable_afterwards(demo: ConfigFactory) -> None:
    async with serving(demo()) as (_, http):
        started = await http.post("/v1/agents/demo/runs", json={"message": QUESTION})
        assert started.status_code == 201
        result = started.json()
        assert (result["status"], result["text"]) == ("completed", ANSWER)

        again = await http.get(f"/v1/runs/{result['run_id']}")
        assert again.status_code == 200
        assert again.json()["text"] == ANSWER


async def test_events_are_replayed_in_sse(demo: ConfigFactory) -> None:
    async with serving(demo()) as (_, http):
        started = await http.post("/v1/agents/demo/runs", json={"message": QUESTION})
        run_id = started.json()["run_id"]
        events = await _sse(http, f"/v1/runs/{run_id}/events")

    assert [name for name, _ in events][:2] == ["run.started", "message.user"]
    assert events[-1][0] == "run.completed"
    assert all(int(seq) > 0 for _, seq in events)


async def test_events_follow_a_run_in_flight(demo: ConfigFactory) -> None:
    run_id = new_run_id()
    started = asyncio.Event()
    async with serving(demo()) as (loom, http):
        # Le journal prévient dès l'écriture : le flux s'ouvre sur un run en cours.
        with loom.store.listen(lambda _: started.set(), run_id):
            run = asyncio.create_task(
                http.post("/v1/agents/demo/runs", json={"message": QUESTION, "run_id": run_id})
            )
            async with asyncio.timeout(5):
                await started.wait()
            events = await _sse(http, f"/v1/runs/{run_id}/events")
        assert (await run).status_code == 201

    assert events[0][0] == "run.started"
    assert events[-1][0] == "run.completed"


async def test_a_replay_can_start_after_a_given_seq(demo: ConfigFactory) -> None:
    async with serving(demo()) as (_, http):
        started = await http.post("/v1/agents/demo/runs", json={"message": QUESTION})
        run_id = started.json()["run_id"]
        whole = await _sse(http, f"/v1/runs/{run_id}/events")
        tail = await _sse(http, f"/v1/runs/{run_id}/events?after_seq={whole[-2][1]}")
        resumed = await _sse(
            http, f"/v1/runs/{run_id}/events", headers={"Last-Event-ID": whole[-2][1]}
        )

    assert [name for name, _ in tail] == ["run.completed"]
    assert tail == resumed


async def test_the_same_run_id_twice_is_refused(demo: ConfigFactory) -> None:
    async with serving(demo()) as (_, http):
        body = {"message": QUESTION, "run_id": "run-unique"}
        assert (await http.post("/v1/agents/demo/runs", json=body)).status_code == 201
        again = await http.post("/v1/agents/demo/runs", json=body)

    assert again.status_code == 409
    assert "existe déjà" in again.json()["detail"]


async def test_unknown_agent_and_unknown_run_give_404(demo: ConfigFactory) -> None:
    path = demo(agents=[demo_agent(), demo_agent(name="cache", expose={"rest": False})])
    async with serving(path) as (_, http):
        absent = await http.post("/v1/agents/absent/runs", json={"message": QUESTION})
        hidden = await http.post("/v1/agents/cache/runs", json={"message": QUESTION})
        run = await http.get("/v1/runs/run-absent")
        events = await http.get("/v1/runs/run-absent/events")

    assert absent.status_code == 404 and "absent" in absent.json()["detail"]
    assert hidden.status_code == 404
    assert run.status_code == 404 and events.status_code == 404


async def test_a_bad_body_is_refused(demo: ConfigFactory) -> None:
    async with serving(demo()) as (_, http):
        empty = await http.post("/v1/agents/demo/runs", json={"message": ""})
        extra = await http.post("/v1/agents/demo/runs", json={"message": QUESTION, "x": 1})

    assert empty.status_code == 422 and extra.status_code == 422


async def test_a_key_is_required_when_one_is_declared(demo: ConfigFactory) -> None:
    async with serving(demo(security=SECURITY)) as (_, http):
        without = await http.get("/v1/agents")
        wrong = await http.get("/v1/agents", headers={"X-API-Key": "lk_faux"})
        bearer = await http.get("/v1/agents", headers={"Authorization": f"Bearer {CLE}"})
        header = await http.get("/v1/agents", headers={"X-API-Key": CLE})

    assert without.status_code == 401 and "Clé d'API absente" in without.json()["detail"]
    assert wrong.status_code == 401
    assert bearer.status_code == 200 and header.status_code == 200


async def test_scopes_and_agents_limit_a_key(demo: ConfigFactory) -> None:
    lecture = new_api_key()
    autre = new_api_key()
    security = {
        "api_keys": [
            {"id": "lecture", "hash": fingerprint(lecture), "scopes": ["read"]},
            {
                "id": "autre",
                "hash": fingerprint(autre),
                "scopes": ["run", "read"],
                "agents": ["ailleurs"],
            },
        ]
    }
    async with serving(demo(security=security)) as (_, http):
        run = await http.post(
            "/v1/agents/demo/runs",
            json={"message": QUESTION},
            headers={"Authorization": f"Bearer {lecture}"},
        )
        read = await http.get("/v1/agents", headers={"Authorization": f"Bearer {lecture}"})
        elsewhere = await http.post(
            "/v1/agents/demo/runs",
            json={"message": QUESTION},
            headers={"Authorization": f"Bearer {autre}"},
        )
        listed = await http.get("/v1/agents", headers={"Authorization": f"Bearer {autre}"})

    assert run.status_code == 403 and "portée 'run'" in run.json()["detail"]
    assert read.status_code == 200
    assert elsewhere.status_code == 403 and "agent 'demo'" in elsewhere.json()["detail"]
    assert listed.json() == []


async def test_a_base_path_moves_every_route(demo: ConfigFactory) -> None:
    path = demo(server={"http": {"base_path": "/loom"}})
    async with serving(path) as (_, http):
        moved = await http.get("/loom/v1/agents")
        gone = await http.get("/v1/agents")

    assert moved.status_code == 200 and gone.status_code == 404


async def test_an_open_instance_off_localhost_warns(
    demo: ConfigFactory, caplog: pytest.LogCaptureFixture
) -> None:
    path = demo(server={"http": {"host": "0.0.0.0"}})
    async with Loom.from_config(path) as loom:
        with caplog.at_level("WARNING", logger="loom_ia.access.http.app"):
            create_app(loom)

    assert "sans clé déclarée" in caplog.text


async def test_an_owned_instance_is_closed_at_shutdown(demo: ConfigFactory) -> None:
    loom = Loom.from_config(demo())
    app = create_app(loom, own=True)
    before = loom.context("demo")
    async with app.router.lifespan_context(app):
        pass
    # L'arrêt du serveur a fermé l'instance : l'agent est remonté au suivant.
    assert loom.context("demo") is not before


async def _sse(
    http: httpx2.AsyncClient, url: str, headers: dict[str, str] | None = None
) -> list[tuple[str, str]]:
    """Couples ``(event, id)`` lus sur un flux SSE, jusqu'à sa fin."""
    seen: list[tuple[str, str]] = []
    block: dict[str, str] = {}
    async with http.stream("GET", url, headers=headers, timeout=5) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        async for line in response.aiter_lines():
            if line.strip():
                field, _, value = line.partition(":")
                block.setdefault(field.strip(), value.strip())
            elif block:
                seen.append((block.get("event", ""), block.get("id", "")))
                block = {}
    return seen
