# SPDX-License-Identifier: Apache-2.0
"""Déclencheurs : une porte déclarée, sa charge, sa clé, ses doublons (H6, J5.4c)."""

import json
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from conftest import ConfigFactory, demo_agent

from loom_ia.access import DeliveryRefused, Loom, Triggered, UnknownTrigger
from loom_ia.config import ConfigError, load_config
from loom_ia.config.keys import fingerprint, new_api_key
from loom_ia.core.model import RunId, SessionId, TenantId

if TYPE_CHECKING:
    import httpx2

JOURNAL: dict[str, Any] = {"events": {"backend": "jsonl", "path": "data"}}
RELANCE: dict[str, Any] = {
    "name": "relance-quotidienne",
    "agent": "demo",
    "message": "Relance le devis {{ payload.devis.numero }} de {{ payload.client }}.",
}
CHARGE: dict[str, Any] = {"devis": {"numero": "D-2026-042"}, "client": "Mme Martin"}


def cles(**kinds: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    jetons = {nom: new_api_key() for nom in kinds}
    keys = [{"id": nom, "hash": fingerprint(jetons[nom]), **spec} for nom, spec in kinds.items()]
    return {"api_keys": keys}, jetons


# --- La configuration d'une porte ---------------------------------------------


def test_a_trigger_names_an_agent_that_exists(demo: ConfigFactory) -> None:
    absent = {**RELANCE, "agent": "ailleurs"}
    with pytest.raises(ConfigError, match="agent 'ailleurs' non déclaré"):
        load_config(demo(triggers=[absent]))


def test_two_triggers_of_the_same_name_are_refused(demo: ConfigFactory) -> None:
    with pytest.raises(ConfigError, match="Déclencheur"):
        load_config(demo(triggers=[RELANCE, RELANCE]))


def test_a_broken_template_is_refused_at_load(demo: ConfigFactory) -> None:
    casse = {**RELANCE, "message": "Relance {{ devis numero }}."}
    with pytest.raises(ConfigError, match="Variable invalide"):
        load_config(demo(triggers=[casse]))


def test_a_name_that_is_not_a_path_segment_is_refused(demo: ConfigFactory) -> None:
    with pytest.raises(ConfigError, match="pattern"):
        load_config(demo(triggers=[{**RELANCE, "name": "relance/quotidienne"}]))


def test_a_trigger_may_open_an_agent_that_rest_does_not_publish(demo: ConfigFactory) -> None:
    # Une porte déclarée n'est pas l'API ouverte : c'est même la façon de
    # n'ouvrir un agent qu'à un planificateur.
    agents = [demo_agent(name="interne", expose={"rest": False})]
    config = load_config(demo(agents=agents, triggers=[{**RELANCE, "agent": "interne"}]))
    assert [spec.agent for spec in config.triggers] == ["interne"]


# --- Ce qu'une livraison ouvre ------------------------------------------------


async def test_a_delivery_renders_its_message_and_opens_a_run(demo: ConfigFactory) -> None:
    async with Loom.from_config(demo(storage=JOURNAL, triggers=[RELANCE])) as loom:
        opened = await loom.trigger("relance-quotidienne", CHARGE)
        await loom.drain()
        events = await loom.events(opened.run_id, session_id=opened.session_id)
        result = await loom.result(opened.run_id, session_id=opened.session_id)

    assert opened.trigger == "relance-quotidienne" and opened.repeated is False
    demande = [event.payload for event in events if event.type == "message.user"]
    assert "Relance le devis D-2026-042 de Mme Martin." in str(demande[0])
    # La porte qui a ouvert le run est une facette du journal, pas du contenu.
    [started] = [event for event in events if event.type == "run.started"]
    assert started.facets["trigger"] == "relance-quotidienne"
    assert result.status == "completed"


async def test_a_missing_variable_makes_a_poorer_message_not_a_lost_delivery(
    demo: ConfigFactory,
) -> None:
    async with Loom.from_config(demo(storage=JOURNAL, triggers=[RELANCE])) as loom:
        opened = await loom.trigger("relance-quotidienne", {"client": "Mme Martin"})
        events = await loom.events(opened.run_id, session_id=opened.session_id)

    demande = [event.payload for event in events if event.type == "message.user"]
    assert "Relance le devis  de Mme Martin." in str(demande[0])


async def test_a_trigger_can_gather_its_deliveries_in_one_session(demo: ConfigFactory) -> None:
    avec = {**RELANCE, "session": "devis-{{ payload.devis.numero }}"}
    async with Loom.from_config(demo(storage=JOURNAL, triggers=[avec])) as loom:
        first = await loom.trigger("relance-quotidienne", CHARGE)
        second = await loom.trigger("relance-quotidienne", CHARGE)
        fiche = await loom.session(SessionId("devis-D-2026-042"))

    assert first.session_id == second.session_id == SessionId("devis-D-2026-042")
    assert first.run_id != second.run_id and len(fiche.runs) == 2


async def test_an_unknown_trigger_names_the_declared_ones(demo: ConfigFactory) -> None:
    async with Loom.from_config(demo(triggers=[RELANCE])) as loom:
        with pytest.raises(UnknownTrigger, match="relance-quotidienne"):
            await loom.trigger("absent")


# --- Les doublons de livraison ------------------------------------------------


async def test_the_same_delivery_twice_opens_one_run(demo: ConfigFactory) -> None:
    async with Loom.from_config(demo(storage=JOURNAL, triggers=[RELANCE])) as loom:
        first = await loom.trigger("relance-quotidienne", CHARGE, delivery_id="evt-1")
        second = await loom.trigger("relance-quotidienne", CHARGE, delivery_id="evt-1")
        autre = await loom.trigger("relance-quotidienne", CHARGE, delivery_id="evt-2")
        await loom.drain()
        journaux = await loom.sessions()

    assert first.run_id == second.run_id == "evt-1"
    assert first.repeated is False and second.repeated is True
    assert autre.run_id == "evt-2" and autre.repeated is False
    # Deux livraisons, deux journaux : la troisième n'a rien rouvert.
    assert sorted(record.session_id for record in journaux) == ["evt-1", "evt-2"]


async def test_a_delivery_id_that_is_not_usable_is_refused(demo: ConfigFactory) -> None:
    async with Loom.from_config(demo(storage=JOURNAL, triggers=[RELANCE])) as loom:
        with pytest.raises(DeliveryRefused, match="Identifiant de livraison"):
            await loom.trigger("relance-quotidienne", CHARGE, delivery_id="../ailleurs")


async def test_a_session_template_that_renders_nothing_is_refused(demo: ConfigFactory) -> None:
    # Une variable absente fait un message plus pauvre ; un nom de journal
    # vide, lui, n'est pas un nom.
    avec = {**RELANCE, "session": "{{ payload.absente }}"}
    async with Loom.from_config(demo(storage=JOURNAL, triggers=[avec])) as loom:
        with pytest.raises(DeliveryRefused, match="Session du déclencheur"):
            await loom.trigger("relance-quotidienne", CHARGE)


# --- La porte en REST ---------------------------------------------------------


@asynccontextmanager
async def servie(path: Path) -> AsyncGenerator[tuple[Loom, httpx2.AsyncClient]]:
    """Une instance et un client parlant à son application ASGI.

    L'extra est demandé ici, et non en tête de module : les essais de
    configuration ci-dessus valent sans lui.
    """
    pytest.importorskip("fastapi", reason="extra 'http' absent")
    pytest.importorskip("httpx2", reason="client HTTP de test absent")
    import httpx2

    from loom_ia.access.http import create_app

    async with Loom.from_config(path) as instance:
        transport = httpx2.ASGITransport(create_app(instance))
        async with httpx2.AsyncClient(transport=transport, base_url="http://loom.test") as client:
            yield instance, client


async def test_a_hook_answers_202_then_200_on_a_repeat(demo: ConfigFactory) -> None:
    avec = {**RELANCE, "delivery_header": "X-Delivery-Id"}
    security, jetons = cles(planificateur={"scopes": ["run", "read"]})
    path = demo(storage=JOURNAL, security=security, triggers=[avec])
    entete = {"Authorization": f"Bearer {jetons['planificateur']}", "X-Delivery-Id": "evt-7"}
    async with servie(path) as (loom, http):
        first = await http.post("/v1/hooks/relance-quotidienne", json=CHARGE, headers=entete)
        second = await http.post("/v1/hooks/relance-quotidienne", json=CHARGE, headers=entete)
        absent = await http.post("/v1/hooks/ailleurs", json=CHARGE, headers=entete)
        casse = await http.post(
            "/v1/hooks/relance-quotidienne", content=b"{pas du json", headers=entete
        )
        await loom.drain()
        events = await loom.events(RunId("evt-7"))

    assert first.status_code == 202 and first.json()["run_id"] == "evt-7"
    assert first.json()["repeated"] is False
    assert second.status_code == 200 and second.json()["repeated"] is True
    assert absent.status_code == 404 and "non déclaré" in absent.json()["detail"]
    assert casse.status_code == 422 and "Charge illisible" in casse.json()["detail"]
    [started] = [event for event in events if event.type == "run.started"]
    assert started.facets["trigger"] == "relance-quotidienne"


async def test_a_hook_needs_run_on_the_agent_of_its_trigger(demo: ConfigFactory) -> None:
    security, jetons = cles(
        lecture={"scopes": ["read"]},
        bureau={"scopes": ["run", "read"], "agents": ["autre"]},
        tout={"scopes": ["run", "read"]},
    )
    agents = [demo_agent(), demo_agent(name="autre")]
    path = demo(agents=agents, storage=JOURNAL, security=security, triggers=[RELANCE])
    async with servie(path) as (loom, http):
        codes = {}
        for nom in ("lecture", "bureau", "tout"):
            response = await http.post(
                "/v1/hooks/relance-quotidienne",
                json=CHARGE,
                headers={"Authorization": f"Bearer {jetons[nom]}"},
            )
            codes[nom] = response.status_code
        await loom.drain()

    # Le déclencheur nomme son agent : c'est sur lui que porte le droit.
    assert codes == {"lecture": 403, "bureau": 403, "tout": 202}


async def test_a_delivery_stays_with_the_tenant_of_its_key(demo: ConfigFactory) -> None:
    security, jetons = cles(
        dupont={"scopes": ["run", "read"], "tenant": "dupont"},
        martin={"scopes": ["run", "read"], "tenant": "martin"},
    )
    path = demo(
        storage=JOURNAL,
        tenants=[{"id": "dupont"}, {"id": "martin"}],
        security=security,
        triggers=[RELANCE],
    )
    async with servie(path) as (loom, http):
        for nom in ("dupont", "martin"):
            await http.post(
                "/v1/hooks/relance-quotidienne",
                json=CHARGE,
                headers={"Authorization": f"Bearer {jetons[nom]}"},
            )
        await loom.drain()
        chez_dupont = await loom.runs(tenant_id=TenantId("dupont"))
        chez_martin = await loom.runs(tenant_id=TenantId("martin"))

    # Rien dans l'URL ni dans le corps ne nomme un client : la clé le dit.
    assert len(chez_dupont.runs) == 1 and len(chez_martin.runs) == 1
    assert chez_dupont.runs[0].run_id != chez_martin.runs[0].run_id


async def test_an_empty_body_is_a_legitimate_delivery(demo: ConfigFactory) -> None:
    fixe = {**RELANCE, "message": "C'est l'heure : relance les devis en attente."}
    security, jetons = cles(planificateur={"scopes": ["run", "read"]})
    path = demo(storage=JOURNAL, security=security, triggers=[fixe])
    async with servie(path) as (loom, http):
        # Un planificateur n'a rien à dire d'autre que « c'est l'heure ».
        response = await http.post(
            "/v1/hooks/relance-quotidienne",
            headers={"Authorization": f"Bearer {jetons['planificateur']}"},
        )
        await loom.drain()
        opened = Triggered.model_validate(response.json())
        events = await loom.events(opened.run_id, session_id=opened.session_id)

    assert response.status_code == 202
    demande = [event.payload for event in events if event.type == "message.user"]
    assert "C'est l'heure" in str(demande[0])


def test_validate_shows_the_doors(demo: ConfigFactory, capsys: pytest.CaptureFixture[str]) -> None:
    from loom_ia.access.cli import main

    sans = {**RELANCE, "name": "sans-entete"}
    avec = {**RELANCE, "name": "avec-entete", "delivery_header": "X-Delivery-Id"}
    assert main(["--config", str(demo(triggers=[sans, avec])), "validate"]) == 0
    out = capsys.readouterr().out
    assert "Portes     : sans-entete, avec-entete" in out
    assert "POST /v1/hooks/avec-entete → agent demo" in out
    assert "livraison sur X-Delivery-Id" in out
    # Le piège dit avant la panne : sans en-tête, une relivraison rouvre un run.
    assert "aucun en-tête de livraison : une relivraison rouvre un run" in out


def test_the_hook_route_is_documented(demo: ConfigFactory) -> None:
    pytest.importorskip("fastapi", reason="extra 'http' absent")

    from loom_ia.access.http import create_app

    document = create_app(Loom(load_config(demo(triggers=[RELANCE])))).openapi()
    route = document["paths"]["/v1/hooks/{name}"]["post"]
    assert route["tags"] == ["hooks"] and route["summary"]
    assert set(route["responses"]) >= {"200", "202"}
    assert json.dumps(route["responses"]["200"]).count("Triggered") >= 1
