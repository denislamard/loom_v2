# SPDX-License-Identifier: Apache-2.0
"""Serveur MCP en HTTP : clé par requête, portées, Origin et Host (N5, #39, J5.2b).

Ce que le transport change : la clé arrive à **chaque requête**, là où le stdio
fige un client au lancement. Un seul serveur sert donc tous les clients, publie
à chacun ce que sa clé lui ouvre, et refuse le reste.

Les essais parlent au serveur par un vrai client MCP, mais sans socket : le
client est branché sur l'application ASGI, et le cycle de vie de celle-ci est
déroulé à la main — le gestionnaire de session du SDK en dépend.
"""

import json
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from conftest import QUESTION, ConfigFactory, demo_agent

from loom_ia.access import Loom
from loom_ia.config import ConfigError, load_config
from loom_ia.config.keys import fingerprint, new_api_key
from loom_ia.core.model import SessionId, TenantId

pytest.importorskip("fastapi", reason="extra 'http' absent")
pytest.importorskip("mcp", reason="extra 'mcp' absent")

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from loom_ia.access.http import create_app

DUPONT = TenantId("dupont-plomberie")
MARTIN = TenantId("martin-chauffage")
JOURNAL: dict[str, Any] = {"events": {"backend": "jsonl", "path": "data"}}
MCP_HTTP: dict[str, Any] = {"mcp": {"http": True}}


def cles(**kinds: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    """Une clé par entrée ; rend le bloc `security` et les jetons."""
    jetons = {nom: new_api_key() for nom in kinds}
    keys = [{"id": nom, "hash": fingerprint(jetons[nom]), **spec} for nom, spec in kinds.items()]
    return {"api_keys": keys}, jetons


def branche(app: Any, headers: dict[str, str], host: str) -> httpx.AsyncClient:
    """Client HTTP branché sur l'application, sans socket ni port."""
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app),
        base_url=f"http://{host}",
        headers=headers,
        timeout=30,
    )


@asynccontextmanager
async def parle(
    loom: Loom, jeton: str | None = None, *, origin: str | None = None, host: str = "127.0.0.1"
) -> AsyncGenerator[ClientSession]:
    """Une session MCP ouverte sur l'application de l'instance."""
    app = create_app(loom)
    headers: dict[str, str] = {}
    if jeton:
        headers["Authorization"] = f"Bearer {jeton}"
    if origin:
        headers["Origin"] = origin
    async with app.router.lifespan_context(app):
        async with branche(app, headers, host) as http:
            async with streamable_http_client(f"http://{host}/mcp", http_client=http) as (
                read,
                write,
                _,
            ):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    yield session


async def brut(
    loom: Loom, jeton: str | None = None, *, headers: dict[str, str] | None = None
) -> httpx.Response:
    """Une requête d'initialisation crue, pour voir ce que le transport répond."""
    app = create_app(loom)
    entete = {
        "content-type": "application/json",
        "accept": "application/json, text/event-stream",
        **(headers or {}),
    }
    if jeton:
        entete["authorization"] = f"Bearer {jeton}"
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "essai", "version": "1"},
        },
    }
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app),
            base_url="http://127.0.0.1",
        ) as http:
            # Directement sur le chemin monté : une redirection ferait perdre
            # l'en-tête d'autorisation dès que l'hôte change.
            return await http.post("/mcp/", json=body, headers=entete)


def texte(result: Any) -> str:
    return "\n".join(bloc.text for bloc in result.content if getattr(bloc, "text", None))


# --- Le montage, et ce qu'il exige -------------------------------------------


def test_mcp_in_http_demands_keys(demo: ConfigFactory) -> None:
    # Le MCP publie des outils à un LLM tiers : l'ouvrir sans clé serait une
    # faute de configuration, pas un mode d'usage.
    with pytest.raises(ConfigError, match=r"security\.api_keys"):
        load_config(demo(server=MCP_HTTP))


async def test_nothing_is_mounted_when_it_is_not_asked(demo: ConfigFactory) -> None:
    security, _ = cles(app={"scopes": ["run", "read"]})
    async with Loom.from_config(demo(security=security)) as loom:
        response = await brut(loom)
    assert response.status_code == 404


async def test_a_key_is_required(demo: ConfigFactory) -> None:
    security, _ = cles(app={"scopes": ["run", "read"]})
    async with Loom.from_config(demo(security=security, server=MCP_HTTP)) as loom:
        response = await brut(loom)
    assert response.status_code == 401
    assert "Clé d'API absente" in response.json()["detail"]
    assert response.headers["www-authenticate"] == "Bearer"


async def test_an_expired_key_is_refused_with_its_date(demo: ConfigFactory) -> None:
    security, jetons = cles(
        morte={"scopes": ["run", "read"], "expires": "2020-01-01T00:00:00Z"},
    )
    async with Loom.from_config(demo(security=security, server=MCP_HTTP)) as loom:
        response = await brut(loom, jetons["morte"])
    assert response.status_code == 401
    assert "expirée" in response.json()["detail"] and "morte" in response.json()["detail"]


# --- Protection du transport (spec MCP) --------------------------------------


async def test_an_undeclared_host_is_refused(demo: ConfigFactory) -> None:
    security, jetons = cles(app={"scopes": ["run", "read"]})
    async with Loom.from_config(demo(security=security, server=MCP_HTTP)) as loom:
        response = await brut(loom, jetons["app"], headers={"host": "ailleurs.example"})
    # 421 « Misdirected Request » : ce n'est pas l'hôte où l'on croit parler.
    assert response.status_code == 421


async def test_an_undeclared_origin_is_refused_but_no_origin_passes(
    demo: ConfigFactory,
) -> None:
    security, jetons = cles(app={"scopes": ["run", "read"]})
    async with Loom.from_config(demo(security=security, server=MCP_HTTP)) as loom:
        sans = await brut(loom, jetons["app"])
        avec = await brut(loom, jetons["app"], headers={"origin": "https://ailleurs.example"})
    # Un client natif n'envoie pas d'Origin : il passe. Un Origin non
    # déclaré, lui, est refusé — 403, là où un mauvais Host donne 421.
    assert sans.status_code == 200
    assert avec.status_code == 403


async def test_a_declared_origin_passes(demo: ConfigFactory) -> None:
    security, jetons = cles(app={"scopes": ["run", "read"]})
    server = {"mcp": {"http": True, "allowed_origins": ["https://atelier.example"]}}
    async with Loom.from_config(demo(security=security, server=server)) as loom:
        response = await brut(loom, jetons["app"], headers={"origin": "https://atelier.example"})
    assert response.status_code == 200


# --- La clé dit le client, et ce qu'on peut ----------------------------------


async def test_one_server_serves_every_tenant(demo: ConfigFactory) -> None:
    """Ce que le stdio ne peut pas faire : deux clients sur le même serveur."""
    security, jetons = cles(
        dupont={"scopes": ["run", "read", "read_content"], "tenant": DUPONT},
        martin={"scopes": ["run", "read", "read_content"], "tenant": MARTIN},
    )
    path = demo(
        storage=JOURNAL,
        tenants=[{"id": DUPONT}, {"id": MARTIN}],
        security=security,
        server=MCP_HTTP,
    )
    async with Loom.from_config(path) as loom:
        for nom in ("dupont", "martin"):
            async with parle(loom, jetons[nom]) as session:
                await session.call_tool("demo", {"message": QUESTION, "session_id": f"chez-{nom}"})
        chez_dupont = await loom.export_session(SessionId("chez-dupont"), tenant_id=DUPONT)
        chez_martin = await loom.export_session(SessionId("chez-martin"), tenant_id=MARTIN)
    assert {event.tenant_id for event in chez_dupont} == {DUPONT}
    assert {event.tenant_id for event in chez_martin} == {MARTIN}


async def test_a_key_publishes_only_the_agents_it_may_launch(demo: ConfigFactory) -> None:
    security, jetons = cles(
        tout={"scopes": ["run", "read"]},
        bureau={"scopes": ["run", "read"], "agents": ["autre"]},
    )
    agents = [demo_agent(), demo_agent(name="autre")]
    path = demo(agents=agents, security=security, server=MCP_HTTP)
    async with Loom.from_config(path) as loom:
        async with parle(loom, jetons["tout"]) as session:
            publies = [tool.name for tool in (await session.list_tools()).tools]
        async with parle(loom, jetons["bureau"]) as session:
            limites = [tool.name for tool in (await session.list_tools()).tools]
            refus = await session.call_tool("demo", {"message": QUESTION})
    assert set(publies) == {"demo", "autre", "run_status", "run_report"}
    assert set(limites) == {"autre", "run_status", "run_report"}
    # Et `approve` n'est jamais un outil MCP (#39).
    assert "approve" not in publies
    assert refus.isError and "non autorisée sur l'agent 'demo'" in texte(refus)


async def test_launching_needs_run_and_reading_needs_read(demo: ConfigFactory) -> None:
    security, jetons = cles(
        lecture={"scopes": ["read", "read_content"]},
        lancement={"scopes": ["run"]},
    )
    path = demo(storage=JOURNAL, security=security, server=MCP_HTTP)
    async with Loom.from_config(path) as loom:
        async with parle(loom, jetons["lecture"]) as session:
            sans_run = await session.call_tool("demo", {"message": QUESTION})
        async with parle(loom, jetons["lancement"]) as session:
            lance = await session.call_tool("demo", {"message": QUESTION})
            run_id = (lance.structuredContent or {})["run_id"]
            sans_read = await session.call_tool("run_status", {"run_id": run_id})
    assert sans_run.isError and "portée 'run'" in texte(sans_run)
    assert not lance.isError
    assert sans_read.isError and "portée 'read'" in texte(sans_read)


async def test_a_reread_is_masked_but_what_a_key_launches_it_receives(
    demo: ConfigFactory,
) -> None:
    security, jetons = cles(supervision={"scopes": ["run", "read"]})
    path = demo(storage=JOURNAL, security=security, server=MCP_HTTP)
    async with Loom.from_config(path) as loom:
        async with parle(loom, jetons["supervision"]) as session:
            lance = await session.call_tool("demo", {"message": QUESTION, "session_id": "s1"})
            run_id = (lance.structuredContent or {})["run_id"]
            relu = await session.call_tool("run_status", {"run_id": run_id, "session_id": "s1"})
    lance_json = lance.structuredContent or {}
    relu_json = relu.structuredContent or {}
    # Ce qu'une clé lance, elle le reçoit.
    assert lance_json["text"] and lance_json["status"] == "completed"
    # La relecture, elle, est masquée : le statut et les coûts passent, pas le texte.
    assert relu_json["text"] == ""
    assert relu_json["status"] == "completed"
    assert relu_json["cost_usd"] == lance_json["cost_usd"]
    assert json.dumps(relu_json, ensure_ascii=False).count(lance_json["text"]) == 0
