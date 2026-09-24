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
from loom_ia.core.events import Event
from loom_ia.core.model import SessionId, new_run_id

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


# --- Approbations, annulation et sessions (J4.5) ------------------------------

APPROBATEUR = new_api_key()
ADMIN = new_api_key()
LECTEUR = new_api_key()
CLES = {
    "api_keys": [
        {"id": "atelier", "hash": fingerprint(CLE), "scopes": ["run", "read"]},
        {
            "id": "mme-durand",
            "hash": fingerprint(APPROBATEUR),
            "scopes": ["run", "read", "approve"],
        },
        {"id": "console", "hash": fingerprint(ADMIN), "scopes": ["read", "admin"]},
        {"id": "lecture", "hash": fingerprint(LECTEUR), "scopes": ["read"]},
    ]
}


async def test_a_run_can_be_left_in_the_background(demo: ConfigFactory) -> None:
    async with serving(demo()) as (loom, http):
        started = await http.post(
            "/v1/agents/demo/runs", json={"message": QUESTION, "background": True}
        )
        assert started.status_code == 202
        accepted = started.json()
        # Le run est inscrit au journal avant la réponse : il se lit aussitôt.
        assert (await http.get(f"/v1/runs/{accepted['run_id']}")).status_code == 200

        await loom.drain()
        finished = await http.get(f"/v1/runs/{accepted['run_id']}")

    assert set(accepted) == {"run_id", "session_id", "status"}
    assert finished.json()["text"] == ANSWER


async def test_an_approval_is_granted_over_rest(atelier: ConfigFactory) -> None:
    async with serving(atelier(security=CLES)) as (loom, http):
        entete = {"Authorization": f"Bearer {APPROBATEUR}"}
        started = await http.post(
            "/v1/agents/demo/runs", json={"message": "Relance."}, headers=entete
        )
        paused = started.json()
        attente = paused["pending_approvals"]

        decided = await http.post(f"/v1/runs/{paused['run_id']}/approve", json={}, headers=entete)
        await loom.drain()
        finished = await http.get(f"/v1/runs/{paused['run_id']}", headers=entete)
        events = await loom.export_session(SessionId(paused["session_id"]))

    assert paused["status"] == "paused"
    assert [a["tool_name"] for a in attente] == ["envoyer_email"]
    assert decided.status_code == 200
    assert decided.json() == {"run_id": paused["run_id"], "calls": [attente[0]["call_id"]]}
    assert finished.json()["status"] == "completed"
    # Sans ``by`` dans le corps, c'est la clé d'API qui signe l'accord.
    assert _by(events, "approval.granted") == ["mme-durand"]


async def test_the_body_names_the_human_behind_the_key(
    atelier: ConfigFactory,
) -> None:
    async with serving(atelier(security=CLES)) as (loom, http):
        entete = {"Authorization": f"Bearer {APPROBATEUR}"}
        started = await http.post(
            "/v1/agents/demo/runs", json={"message": "Relance."}, headers=entete
        )
        run = started.json()
        await http.post(
            f"/v1/runs/{run['run_id']}/approve",
            json={"by": "denis", "reason": "devis vérifié"},
            headers=entete,
        )
        await loom.drain()
        events = await loom.export_session(SessionId(run["session_id"]))

    assert _by(events, "approval.granted") == ["denis"]


async def test_an_approval_can_be_rejected_over_rest(atelier: ConfigFactory) -> None:
    async with serving(atelier(security=CLES)) as (loom, http):
        entete = {"Authorization": f"Bearer {APPROBATEUR}"}
        started = await http.post(
            "/v1/agents/demo/runs", json={"message": "Relance."}, headers=entete
        )
        run = started.json()
        refus = await http.post(
            f"/v1/runs/{run['run_id']}/reject",
            json={"reason": "mauvais destinataire"},
            headers=entete,
        )
        await loom.drain()
        finished = await http.get(f"/v1/runs/{run['run_id']}", headers=entete)
        events = await loom.export_session(SessionId(run["session_id"]))

    assert refus.json()["calls"] == [run["pending_approvals"][0]["call_id"]]
    # Le run ne s'arrête pas sur un refus : le modèle en fait ce qu'il peut.
    assert finished.json()["status"] == "completed"
    assert _by(events, "approval.rejected") == ["mme-durand"]
    assert "tool.called" not in [event.type for event in events]


async def test_deciding_needs_the_approve_scope(atelier: ConfigFactory) -> None:
    async with serving(atelier(security=CLES)) as (_, http):
        started = await http.post(
            "/v1/agents/demo/runs",
            json={"message": "Relance."},
            headers={"Authorization": f"Bearer {CLE}"},
        )
        run_id = started.json()["run_id"]
        for route in ("approve", "reject"):
            refused = await http.post(
                f"/v1/runs/{run_id}/{route}",
                json={},
                headers={"Authorization": f"Bearer {CLE}"},
            )
            assert refused.status_code == 403 and "approve" in refused.json()["detail"]


async def test_a_paused_run_can_be_cancelled_over_rest(
    atelier: ConfigFactory,
) -> None:
    async with serving(atelier(security=CLES)) as (_, http):
        entete = {"Authorization": f"Bearer {CLE}"}
        started = await http.post(
            "/v1/agents/demo/runs", json={"message": "Relance."}, headers=entete
        )
        run_id = started.json()["run_id"]
        stopped = await http.post(f"/v1/runs/{run_id}/cancel", json={}, headers=entete)
        again = await http.post(f"/v1/runs/{run_id}/cancel", json={}, headers=entete)
        finished = await http.get(f"/v1/runs/{run_id}", headers=entete)

    assert stopped.json() == {"run_id": run_id, "cancelled": True}
    # Un run annulé est terminal : la seconde demande ne trouve plus rien à arrêter.
    assert again.json()["cancelled"] is False
    assert finished.json()["status"] == "cancelled"


async def test_sessions_are_listed_read_and_exported(demo: ConfigFactory) -> None:
    path = demo(storage={"events": {"backend": "jsonl", "path": "data"}})
    async with serving(path) as (_, http):
        await http.post("/v1/agents/demo/runs", json={"message": QUESTION, "session_id": "c-42"})
        listed = await http.get("/v1/sessions")
        fiche = await http.get("/v1/sessions/c-42")
        export = await http.get("/v1/sessions/c-42/events")
        absent = await http.get("/v1/sessions/c-99")

    assert [record["session_id"] for record in listed.json()] == ["c-42"]
    assert [run["agent"] for run in fiche.json()["runs"]] == ["demo"]
    assert fiche.json()["last_seq"] > 0 and fiche.json()["pending_approvals"] == []
    assert export.headers["content-type"].startswith("application/x-ndjson")
    assert len(export.text.splitlines()) == fiche.json()["last_seq"]
    assert absent.status_code == 404


async def test_a_session_shows_what_awaits_a_human(atelier: ConfigFactory) -> None:
    async with serving(atelier()) as (_, http):
        await http.post("/v1/agents/demo/runs", json={"message": "Relance.", "session_id": "c-7"})
        fiche = (await http.get("/v1/sessions/c-7")).json()

    assert [run["status"] for run in fiche["runs"]] == ["paused"]
    assert [a["tool_name"] for a in fiche["pending_approvals"]] == ["envoyer_email"]


async def test_deleting_a_session_needs_admin(demo: ConfigFactory) -> None:
    path = demo(
        storage={"events": {"backend": "jsonl", "path": "data"}},
        security=CLES,
    )
    async with serving(path) as (_, http):
        await http.post(
            "/v1/agents/demo/runs",
            json={"message": QUESTION, "session_id": "c-42"},
            headers={"Authorization": f"Bearer {CLE}"},
        )
        refused = await http.delete("/v1/sessions/c-42", headers={"Authorization": f"Bearer {CLE}"})
        removed = await http.delete(
            "/v1/sessions/c-42", headers={"Authorization": f"Bearer {ADMIN}"}
        )
        gone = await http.get("/v1/sessions/c-42", headers={"Authorization": f"Bearer {ADMIN}"})
        twice = await http.delete("/v1/sessions/c-42", headers={"Authorization": f"Bearer {ADMIN}"})

    assert refused.status_code == 403
    assert removed.json()["session_id"] == "c-42" and removed.json()["events"] > 0
    assert gone.status_code == 404 and twice.status_code == 404


async def test_a_key_limited_to_agents_cannot_list_sessions(demo: ConfigFactory) -> None:
    limitee = new_api_key()
    security = {
        "api_keys": [
            {
                "id": "limitee",
                "hash": fingerprint(limitee),
                "scopes": ["run", "read"],
                "agents": ["ailleurs"],
            }
        ]
    }
    path = demo(storage={"events": {"backend": "jsonl", "path": "data"}}, security=security)
    async with serving(path) as (_, http):
        refused = await http.get("/v1/sessions", headers={"Authorization": f"Bearer {limitee}"})

    assert refused.status_code == 403 and "filtrée" in refused.json()["detail"]


# --- Document OpenAPI (J5.4a, N2) ---------------------------------------------


async def test_the_openapi_document_describes_every_route(demo: ConfigFactory) -> None:
    async with serving(demo(security=SECURITY)) as (_, http):
        document = (await http.get("/openapi.json")).json()
        page = await http.get("/docs")

    assert document["info"]["title"] == "loom-ia" and document["info"]["version"]
    declared = {tag["name"] for tag in document["tags"]}
    assert declared == {"agents", "runs", "sessions", "journal", "hooks"}
    operations = [
        (path, method, spec)
        for path, methods in document["paths"].items()
        for method, spec in methods.items()
    ]
    assert operations
    for path, method, spec in operations:
        assert spec.get("summary"), f"{method} {path} sans résumé"
        # Une famille, et une qui soit décrite : sinon la route n'est nulle part
        # dans la page de l'API.
        assert set(spec.get("tags", [])) <= declared, f"{method} {path} mal rangée"
        assert spec.get("tags"), f"{method} {path} sans famille"
        assert spec.get("operationId")
    # Les deux façons de présenter une clé sont déclarées, et chaque route les porte.
    schemes = document["components"]["securitySchemes"]
    assert set(schemes) == {"HTTPBearer", "APIKeyHeader"}
    assert schemes["APIKeyHeader"] == {
        "type": "apiKey",
        "in": "header",
        "name": "x-api-key",
        "description": "Clé d'API de l'instance",
    }
    for path, method, spec in operations:
        names = {name for entry in spec.get("security", []) for name in entry}
        assert names == set(schemes), f"{method} {path} sans schéma d'authentification"
    assert page.status_code == 200


async def test_the_listing_routes_are_documented(demo: ConfigFactory) -> None:
    async with serving(demo()) as (_, http):
        paths = (await http.get("/openapi.json")).json()["paths"]

    runs = paths["/v1/runs"]["get"]
    assert runs["tags"] == ["journal"]
    assert {param["name"] for param in runs["parameters"]} == {
        "agent",
        "status",
        "since",
        "until",
        "limit",
        "sessions",
    }
    events = paths["/v1/events"]["get"]
    assert events["tags"] == ["journal"]
    assert {param["name"] for param in events["parameters"]} == {
        "session_id",
        "run_id",
        "type",
        "category",
        "status",
        "agent",
        "role",
        "tool_name",
        "model_id",
        "since",
        "until",
        "after",
        "limit",
    }


# --- Listes et recherche (J5.4a, K5, #32) -------------------------------------


async def test_runs_are_listed_and_filtered(demo: ConfigFactory) -> None:
    path = demo(agents=[demo_agent(), demo_agent(name="autre")])
    async with serving(path) as (_, http):
        first = await http.post(
            "/v1/agents/demo/runs", json={"message": QUESTION, "session_id": "c-1"}
        )
        second = await http.post(
            "/v1/agents/autre/runs", json={"message": QUESTION, "session_id": "c-2"}
        )
        page = (await http.get("/v1/runs")).json()
        named = (await http.get("/v1/runs?agent=autre")).json()
        done = (await http.get("/v1/runs?status=completed&status=failed")).json()
        none = (await http.get("/v1/runs?status=failed")).json()
        bounded = (await http.get("/v1/runs?limit=1")).json()

    ordered = [run["run_id"] for run in page["runs"]]
    assert ordered == [second.json()["run_id"], first.json()["run_id"]]
    assert page["scanned"] == 2 and page["truncated"] is False
    listed = page["runs"][0]
    assert listed["agent"] == "autre" and listed["status"] == "completed"
    assert listed["session_id"] == "c-2" and listed["parent_run_id"] is None
    assert listed["started_at"] <= listed["updated_at"] and listed["cost_usd"] >= 0
    # Une liste ne porte aucun contenu : ni la réponse, ni le message d'erreur.
    assert "text" not in listed and "error" not in listed and listed["error_type"] is None
    assert [run["agent"] for run in named["runs"]] == ["autre"]
    assert len(done["runs"]) == 2 and none["runs"] == []
    assert len(bounded["runs"]) == 1 and bounded["truncated"] is True


async def test_a_bad_bound_on_a_list_is_refused(demo: ConfigFactory) -> None:
    async with serving(demo()) as (_, http):
        zero = await http.get("/v1/runs?limit=0")
        huge = await http.get("/v1/runs?sessions=100000")
        unknown = await http.get("/v1/runs?status=parti")

    assert zero.status_code == 422 and huge.status_code == 422 and unknown.status_code == 422


async def test_a_key_limited_to_agents_gets_only_its_runs(demo: ConfigFactory) -> None:
    limitee = new_api_key()
    security = {
        "api_keys": [
            {"id": "atelier", "hash": fingerprint(CLE), "scopes": ["run", "read"]},
            {
                "id": "limitee",
                "hash": fingerprint(limitee),
                "scopes": ["run", "read"],
                "agents": ["autre"],
            },
        ]
    }
    path = demo(agents=[demo_agent(), demo_agent(name="autre")], security=security)
    async with serving(path) as (_, http):
        ouverte = {"Authorization": f"Bearer {CLE}"}
        bornee = {"Authorization": f"Bearer {limitee}"}
        await http.post("/v1/agents/demo/runs", json={"message": QUESTION}, headers=ouverte)
        await http.post("/v1/agents/autre/runs", json={"message": QUESTION}, headers=ouverte)
        page = (await http.get("/v1/runs", headers=bornee)).json()
        refused = await http.get("/v1/runs?agent=demo", headers=bornee)

    # Contrairement aux sessions, la liste des runs se filtre honnêtement :
    # chaque run dit de quel agent il est.
    assert [run["agent"] for run in page["runs"]] == ["autre"]
    # Nommer un agent qu'on n'a pas le droit de lire est un refus, pas un vide.
    assert refused.status_code == 403


async def test_events_are_searched_in_the_journal(demo: ConfigFactory) -> None:
    async with serving(demo()) as (_, http):
        started = await http.post(
            "/v1/agents/demo/runs", json={"message": QUESTION, "session_id": "c-1"}
        )
        run_id = started.json()["run_id"]
        calls = (await http.get("/v1/events?type=tool.called")).json()
        named = (await http.get("/v1/events?tool_name=calculer")).json()
        owned = (await http.get(f"/v1/events?run_id={run_id}&category=run")).json()
        first = (await http.get("/v1/events?limit=1")).json()
        after = (await http.get(f"/v1/events?limit=1&after={first[0]['event_id']}")).json()
        elsewhere = (await http.get("/v1/events?session_id=c-99")).json()

    assert [event["type"] for event in calls] == ["tool.called"]
    assert {event["type"] for event in named} == {"tool.called", "tool.completed"}
    assert {event["run_id"] for event in owned} == {run_id}
    assert first[0]["type"] == "run.started"
    assert after[0]["event_id"] > first[0]["event_id"]
    assert elsewhere == []


async def test_a_search_never_leaves_the_client_of_the_key(demo: ConfigFactory) -> None:
    autre = new_api_key()
    security = {
        "api_keys": [
            {"id": "atelier", "hash": fingerprint(CLE), "scopes": ["run", "read"]},
            {
                "id": "cabinet",
                "hash": fingerprint(autre),
                "scopes": ["run", "read"],
                "tenant": "martin",
            },
        ]
    }
    path = demo(tenants=[{"id": "default"}, {"id": "martin"}], security=security)
    async with serving(path) as (_, http):
        await http.post(
            "/v1/agents/demo/runs",
            json={"message": QUESTION},
            headers={"Authorization": f"Bearer {CLE}"},
        )
        mine = (await http.get("/v1/events", headers={"Authorization": f"Bearer {CLE}"})).json()
        theirs = (await http.get("/v1/events", headers={"Authorization": f"Bearer {autre}"})).json()
        runs = (await http.get("/v1/runs", headers={"Authorization": f"Bearer {autre}"})).json()

    # Rien dans l'URL ne nomme un client : la clé le dit, et elle seule.
    assert mine and theirs == []
    assert runs["runs"] == []


async def test_a_bad_search_is_refused(demo: ConfigFactory) -> None:
    async with serving(demo()) as (_, http):
        category = await http.get("/v1/events?category=inconnue")
        limit = await http.get("/v1/events?limit=0")

    assert category.status_code == 422 and limit.status_code == 422


def _by(events: list[Event], type_: str) -> list[str | None]:
    """Auteurs inscrits au journal pour un type d'événement."""
    found = [event.facets.get("by") for event in events if event.type == type_]
    return [value if value is None or isinstance(value, str) else str(value) for value in found]


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
