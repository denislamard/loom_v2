# SPDX-License-Identifier: Apache-2.0
"""Multi-clients : liste fermée, surcharges, secrets, isolation (L1 à L3, #33, #34).

Ce qu'un client change, il le change **pour lui** : ses modèles, ses secrets,
ses outils, ses connexions MCP, et parfois son stockage. Ce que ces essais
cherchent avant tout, c'est la fuite — un client qui verrait ce qui n'est pas
à lui —, puis la cohérence : une surcharge incohérente doit tomber au
démarrage, pas au premier run du client concerné.
"""

from pathlib import Path
from typing import Any, cast

import pytest
from conftest import ANSWER, QUESTION, ConfigFactory, demo_agent

from loom_ia.access import AgentNotAllowed, Loom, UnknownTenant
from loom_ia.config import ConfigError, load_config
from loom_ia.core.model import DEFAULT_TENANT, SessionId, TenantId
from loom_ia.runtime import build_agent, load_registry
from loom_ia.tenancy import EnvironmentSecrets, Tenants

DUPONT = TenantId("dupont-plomberie")
MARTIN = TenantId("martin-chauffage")


def two(**extra: Any) -> list[dict[str, Any]]:
    """Deux clients, le second sans rien surcharger."""
    return [{"id": DUPONT, **extra}, {"id": MARTIN}]


# --- La liste des clients est fermée dès qu'elle existe (#33) ------------------


async def test_without_tenants_only_default_exists(demo: ConfigFactory) -> None:
    async with Loom.from_config(demo()) as loom:
        assert loom.tenants == (DEFAULT_TENANT,)
        result = await loom.run("demo", QUESTION)
        assert result.text == ANSWER


async def test_declaring_tenants_closes_the_list(demo: ConfigFactory) -> None:
    async with Loom.from_config(demo(tenants=two())) as loom:
        assert loom.tenants == (DUPONT, MARTIN)
        # `default` n'est plus un client : il n'est pas déclaré.
        with pytest.raises(UnknownTenant, match="default"):
            await loom.run("demo", QUESTION)
        with pytest.raises(UnknownTenant, match="inconnu"):
            await loom.run("demo", QUESTION, tenant=TenantId("inconnu"))
        result = await loom.run("demo", QUESTION, tenant=DUPONT)
        assert result.text == ANSWER


async def test_default_is_a_tenant_like_any_other_when_declared(demo: ConfigFactory) -> None:
    async with Loom.from_config(demo(tenants=[{"id": DEFAULT_TENANT}, {"id": DUPONT}])) as loom:
        assert loom.tenants == (DEFAULT_TENANT, DUPONT)
        assert (await loom.run("demo", QUESTION)).text == ANSWER


async def test_a_run_carries_its_tenant_to_the_journal(demo: ConfigFactory) -> None:
    async with Loom.from_config(demo(tenants=two())) as loom:
        result = await loom.run("demo", QUESTION, tenant=DUPONT)
        events = await loom.events(result.run_id, tenant_id=DUPONT)
        assert {event.tenant_id for event in events} == {DUPONT}
        # Le même run, demandé au nom de l'autre client : rien.
        assert await loom.events(result.run_id, tenant_id=MARTIN) == []


async def test_tenant_and_context_must_agree(demo: ConfigFactory) -> None:
    from loom_ia.core.model import CallerContext

    async with Loom.from_config(demo(tenants=two())) as loom:
        with pytest.raises(ValueError, match="contradictoire"):
            await loom.run("demo", QUESTION, tenant=DUPONT, context=CallerContext(tenant_id=MARTIN))


# --- Agents ouverts à un client (L1) ------------------------------------------


async def test_a_tenant_only_launches_the_agents_it_is_given(demo: ConfigFactory) -> None:
    config = demo(tenants=[{"id": DUPONT, "agents": ["demo"]}, {"id": MARTIN, "agents": []}])
    async with Loom.from_config(config) as loom:
        assert await loom.run("demo", QUESTION, tenant=DUPONT)
        assert [spec.name for spec in loom.exposed("rest", DUPONT)] == ["demo"]


async def test_an_agent_closed_to_a_tenant_is_refused_and_unpublished(
    demo: ConfigFactory,
) -> None:
    agents = [demo_agent(), demo_agent(name="autre")]
    config = demo(agents=agents, tenants=[{"id": DUPONT, "agents": ["autre"]}])
    async with Loom.from_config(config) as loom:
        with pytest.raises(AgentNotAllowed, match="demo"):
            await loom.run("demo", QUESTION, tenant=DUPONT)
        assert [spec.name for spec in loom.exposed("rest", DUPONT)] == ["autre"]
        assert [spec.name for spec in loom.exposed("mcp", DUPONT)] == ["autre"]


def test_an_unknown_agent_in_a_tenant_is_a_startup_error(demo: ConfigFactory) -> None:
    with pytest.raises(ConfigError, match="agent 'fantome' non déclaré"):
        load_config(demo(tenants=[{"id": DUPONT, "agents": ["fantome"]}]))


# --- Correspondance des modèles (#34) -----------------------------------------


def other_model(**changes: Any) -> dict[str, Any]:
    model: dict[str, Any] = {
        "id": "AUTRE",
        "sdk": "fake",
        "model": "fake-2",
        "params": {"script": [{"text": "Réponse du second modèle."}]},
    }
    return {**model, **changes}


def mapped(demo: ConfigFactory, **changes: Any) -> Path:
    from conftest import MODEL

    return demo(
        models=[MODEL, other_model(**changes)],
        tenants=[{"id": DUPONT, "models": {"FAKE": "AUTRE"}}, {"id": MARTIN}],
    )


async def test_a_tenant_model_replaces_the_definition_not_the_reference(
    demo: ConfigFactory,
) -> None:
    async with Loom.from_config(mapped(demo)) as loom:
        # L'identifiant ne bouge pas : c'est ce qu'il désigne qui change.
        assert loom.tenant(DUPONT).config.model_spec("FAKE").model == "fake-2"
        assert loom.tenant(MARTIN).config.model_spec("FAKE").model == "fake-1"
        assert loom.config.model_spec("FAKE").model == "fake-1"
        assert loom.context("demo", DUPONT).model_spec.model == "fake-2"
        assert loom.context("demo", MARTIN).model_spec.model == "fake-1"


async def test_two_tenants_of_one_agent_are_mounted_apart(demo: ConfigFactory) -> None:
    async with Loom.from_config(mapped(demo)) as loom:
        assert loom.context("demo", DUPONT) is not loom.context("demo", MARTIN)
        # Le second modèle répond sans appeler d'outil : la réponse le montre.
        assert (await loom.run("demo", QUESTION, tenant=DUPONT)).text == "Réponse du second modèle."
        assert (await loom.run("demo", QUESTION, tenant=MARTIN)).text == ANSWER


def test_a_model_replacement_passes_the_same_checks(demo: ConfigFactory) -> None:
    # L'agent a un outil : son modèle doit savoir en appeler (M5).
    config = load_config(mapped(demo, capabilities={"tools": False}))
    with pytest.raises(ConfigError, match="ne sait pas le faire"):
        Tenants(config)


def test_an_unknown_replacement_is_a_startup_error(demo: ConfigFactory) -> None:
    with pytest.raises(ConfigError, match="modèle de remplacement 'ABSENT' non déclaré"):
        load_config(demo(tenants=[{"id": DUPONT, "models": {"FAKE": "ABSENT"}}]))


def test_a_model_cannot_replace_itself(demo: ConfigFactory) -> None:
    with pytest.raises(ConfigError, match="se remplace par lui-même"):
        load_config(demo(tenants=[{"id": DUPONT, "models": {"FAKE": "FAKE"}}]))


# --- Outils retirés et approbations imposées ----------------------------------


async def test_a_denied_tool_is_never_offered(demo: ConfigFactory) -> None:
    config = demo(tenants=[{"id": DUPONT, "tools_deny": ["calculer"]}, {"id": MARTIN}])
    async with Loom.from_config(config) as loom:
        assert loom.context("demo", DUPONT).tools.get("calculer") is None
        assert loom.context("demo", MARTIN).tools.get("calculer") is not None


async def test_a_tenant_can_impose_an_approval(atelier: Any) -> None:
    # L'outil est déclaré `approval: always` : le client le lève pour lui.
    config = atelier(tenants=[{"id": DUPONT, "approvals": {"envoyer_email": "never"}}])
    async with Loom.from_config(config) as loom:
        result = await loom.run("demo", "Relance Mme Martin.", tenant=DUPONT)
        assert result.ok and not result.pending_approvals


async def test_an_imposed_approval_still_needs_a_durable_journal(demo: ConfigFactory) -> None:
    # Journal en mémoire : imposer une approbation est refusé comme si
    # l'outil la déclarait lui-même (#28).
    config = demo(tenants=[{"id": DUPONT, "approvals": {"calculer": "always"}}])
    async with Loom.from_config(config) as loom:
        with pytest.raises(ConfigError, match="journal durable"):
            loom.context("demo", DUPONT)


# --- Secrets (L2) --------------------------------------------------------------


def test_a_tenant_reads_its_own_variables(demo: ConfigFactory) -> None:
    config = load_config(demo(tenants=two(secrets={"CRM_TOKEN": "DUPONT_CRM_TOKEN"})))
    environ = {"CRM_TOKEN": "commun", "DUPONT_CRM_TOKEN": "à-dupont"}
    secrets = EnvironmentSecrets(config, environ)
    assert secrets.secrets(DUPONT)["CRM_TOKEN"] == "à-dupont"
    # Le client qui ne redirige rien lit l'environnement commun.
    assert secrets.secrets(MARTIN)["CRM_TOKEN"] == "commun"


def test_a_missing_secret_does_not_fall_back_on_the_common_one(demo: ConfigFactory) -> None:
    config = load_config(demo(tenants=two(secrets={"CRM_TOKEN": "DUPONT_CRM_TOKEN"})))
    secrets = EnvironmentSecrets(config, {"CRM_TOKEN": "commun"})
    # Sans sa variable à lui, le client n'a rien — surtout pas le secret des autres.
    assert secrets.secrets(DUPONT)["CRM_TOKEN"] == ""


def test_the_agent_of_a_tenant_is_mounted_with_its_secrets(demo: ConfigFactory) -> None:
    config = load_config(demo(tenants=two(secrets={"X": "DUPONT_X"})))
    tenants = Tenants(config, environ={"X": "commun", "DUPONT_X": "à-dupont"})
    assert tenants.get(DUPONT).secrets["X"] == "à-dupont"
    assert tenants.get(MARTIN).secrets["X"] == "commun"


# --- Variables des prompts (§6) ------------------------------------------------


def with_variable(demo: ConfigFactory, prompt: str, **root: Any) -> Path:
    path = demo(**root)
    (path.parent / "prompts" / "demo.md").write_text(prompt, encoding="utf-8")
    return path


async def test_a_prompt_variable_takes_the_value_of_its_tenant(demo: ConfigFactory) -> None:
    config = with_variable(
        demo,
        "Tu réponds pour {{ entreprise }}.",
        tenants=[
            {"id": DUPONT, "variables": {"entreprise": "Dupont Plomberie"}},
            {"id": MARTIN, "variables": {"entreprise": "Martin Chauffage"}},
        ],
    )
    async with Loom.from_config(config) as loom:
        assert loom.context("demo", DUPONT).system == "Tu réponds pour Dupont Plomberie."
        assert loom.context("demo", MARTIN).system == "Tu réponds pour Martin Chauffage."


def test_a_variable_missing_for_one_tenant_stops_the_start(demo: ConfigFactory) -> None:
    config = with_variable(
        demo,
        "Tu réponds pour {{ entreprise }}.",
        tenants=[{"id": DUPONT, "variables": {"entreprise": "Dupont"}}, {"id": MARTIN}],
    )
    with pytest.raises(ConfigError, match="variable non définie pour le client 'martin"):
        load_config(config)


def test_a_variable_without_any_tenant_stops_the_start(demo: ConfigFactory) -> None:
    with pytest.raises(ConfigError, match="variable non définie pour le client 'default'"):
        load_config(with_variable(demo, "Tu réponds pour {{ entreprise }}."))


def test_a_malformed_prompt_template_stops_the_start(demo: ConfigFactory) -> None:
    with pytest.raises(ConfigError, match="sans '}}' correspondant"):
        load_config(with_variable(demo, "Tu calcules. {{ mal fermé", tenants=two()))


async def test_a_prompt_without_variables_is_left_alone(demo: ConfigFactory) -> None:
    # Pas de `{{` : le prompt ne passe même pas par le rendu.
    async with Loom.from_config(with_variable(demo, "Tu calcules.", tenants=two())) as loom:
        assert loom.context("demo", DUPONT).system == "Tu calcules."


# --- Clés d'API et client (#34) ------------------------------------------------


def test_a_key_names_the_tenant_it_acts_for(demo: ConfigFactory) -> None:
    keys = [{"id": "app-dupont", "hash": "sha256:" + "0" * 64, "tenant": DUPONT}]
    config = load_config(demo(tenants=two(), security={"api_keys": keys}))
    assert config.security.api_keys[0].tenant == DUPONT


def test_a_key_on_an_undeclared_tenant_is_refused(demo: ConfigFactory) -> None:
    keys = [{"id": "app", "hash": "sha256:" + "0" * 64, "tenant": "fantome"}]
    with pytest.raises(ConfigError, match="client 'fantome' non déclaré"):
        load_config(demo(tenants=two(), security={"api_keys": keys}))


# --- Ce qui attend les phases suivantes ---------------------------------------


def test_a_tenant_storage_cannot_carry_its_own_idempotency(demo: ConfigFactory) -> None:
    # Le port n'a le client que sur ``reserve`` : un magasin par client attend J5.3.
    with pytest.raises(ConfigError, match=r"J5\.3"):
        load_config(
            demo(tenants=[{"id": DUPONT, "storage": {"idempotency": {"backend": "memory"}}}])
        )


# --- Serveurs MCP de portée ``tenant`` (#19, #34) ------------------------------

CRM = """
from loom_ia.tools import tool


@tool
def noop() -> str:
    '''Ne fait rien.'''
    return "ok"
"""


def with_mcp(demo: ConfigFactory, scope: str) -> Path:
    server = {"name": "crm", "transport": "stdio", "command": "true", "scope": scope}
    agent = demo_agent(tools=[{"python": "calculer"}, {"mcp": "crm"}])
    return demo(agents=[agent], mcp_servers=[server], tenants=two())


def pool_keys(demo: ConfigFactory, scope: str) -> list[str]:
    config = load_config(with_mcp(demo, scope))
    registry = load_registry(config)
    tenants = Tenants(config)
    keys: list[str] = []
    for tenant_id in (DUPONT, MARTIN):
        from loom_ia.adapters.stores import InMemoryEventStore

        agent = build_agent(
            config,
            "demo",
            InMemoryEventStore(),
            registry=registry,
            tenant=tenants.get(tenant_id),
        )
        source = cast(Any, agent.context.tools.sources[0])
        keys.append(str(source.pool_key))
    return keys


def test_a_tenant_scoped_server_gets_one_connection_per_tenant(demo: ConfigFactory) -> None:
    pytest.importorskip("mcp", reason="extra 'mcp' absent")
    assert pool_keys(demo, "tenant") == [f"crm#{DUPONT}", f"crm#{MARTIN}"]


def test_a_shared_server_stays_one_connection_for_everyone(demo: ConfigFactory) -> None:
    pytest.importorskip("mcp", reason="extra 'mcp' absent")
    assert pool_keys(demo, "shared") == ["crm", "crm"]


def test_the_pool_keeps_one_server_per_key() -> None:
    pytest.importorskip("mcp", reason="extra 'mcp' absent")
    from loom_ia.adapters.mcp import McpPool
    from loom_ia.core.model import McpServerSpec

    spec = McpServerSpec(name="crm", transport="stdio", command="true", scope="tenant")
    pool = McpPool(lambda _: _unused)
    dupont = pool.server(spec, f"crm#{DUPONT}", _unused)
    martin = pool.server(spec, f"crm#{MARTIN}", _unused)
    assert dupont is not martin
    assert pool.server(spec, f"crm#{DUPONT}", _unused) is dupont


def _unused(*args: Any, **kwargs: Any) -> Any:
    raise AssertionError("aucune connexion n'est ouverte par ces essais")


# --- Isolation physique : TenantRouter (#34, §7.3) ----------------------------


def routed(demo: ConfigFactory) -> Path:
    return demo(
        storage={"events": {"backend": "jsonl", "path": "commun"}},
        tenants=[
            {"id": DUPONT, "storage": {"events": {"backend": "jsonl", "path": "chez-dupont"}}},
            {"id": MARTIN},
        ],
    )


async def test_a_tenant_with_its_own_storage_writes_elsewhere(demo: ConfigFactory) -> None:
    path = routed(demo)
    async with Loom.from_config(path) as loom:
        chez_dupont = await loom.run("demo", QUESTION, tenant=DUPONT)
        chez_martin = await loom.run("demo", QUESTION, tenant=MARTIN)
        # Chacun relit le sien, et seulement le sien.
        assert [r.session_id for r in await loom.sessions(tenant_id=DUPONT)] == [
            SessionId(chez_dupont.run_id)
        ]
        assert [r.session_id for r in await loom.sessions(tenant_id=MARTIN)] == [
            SessionId(chez_martin.run_id)
        ]
        assert await loom.events(chez_dupont.run_id, tenant_id=MARTIN) == []

    base = path.parent
    assert (base / "chez-dupont" / DUPONT).is_dir()
    assert (base / "commun" / MARTIN).is_dir()
    assert not (base / "commun" / DUPONT).exists()


async def test_without_its_own_storage_a_tenant_shares_the_common_journal(
    demo: ConfigFactory,
) -> None:
    path = demo(storage={"events": {"backend": "jsonl", "path": "commun"}}, tenants=two())
    async with Loom.from_config(path) as loom:
        await loom.run("demo", QUESTION, tenant=DUPONT)
        await loom.run("demo", QUESTION, tenant=MARTIN)
    # Un dossier par client sous le même journal : l'isolation reste logique.
    assert {p.name for p in (path.parent / "commun").iterdir()} == {DUPONT, MARTIN}


# --- Isolation vue de l'API REST : la clé dit le client (#34, N2) -------------


async def test_a_key_only_ever_reads_its_own_tenant(demo: ConfigFactory) -> None:
    pytest.importorskip("fastapi", reason="extra 'http' absent")
    pytest.importorskip("httpx2", reason="client HTTP de test absent")
    import httpx2

    from loom_ia.access.http import create_app
    from loom_ia.config.keys import fingerprint, new_api_key

    cle_dupont, cle_martin = new_api_key(), new_api_key()
    security = {
        "api_keys": [
            {"id": "app-dupont", "hash": fingerprint(cle_dupont), "tenant": DUPONT},
            {"id": "app-martin", "hash": fingerprint(cle_martin), "tenant": MARTIN},
        ]
    }
    path = demo(
        storage={"events": {"backend": "jsonl", "path": "data"}},
        tenants=two(),
        security=security,
    )
    async with Loom.from_config(path) as loom:
        transport = httpx2.ASGITransport(create_app(loom))
        async with httpx2.AsyncClient(transport=transport, base_url="http://loom.test") as http:
            chez_dupont = {"Authorization": f"Bearer {cle_dupont}"}
            chez_martin = {"Authorization": f"Bearer {cle_martin}"}
            lance = await http.post(
                "/v1/agents/demo/runs", json={"message": QUESTION}, headers=chez_dupont
            )
            assert lance.status_code == 201
            run_id = lance.json()["run_id"]
            session_id = lance.json()["session_id"]

            # Le client qui l'a lancé relit son run et sa session.
            assert (await http.get(f"/v1/runs/{run_id}", headers=chez_dupont)).status_code == 200
            mienne = await http.get(f"/v1/sessions/{session_id}", headers=chez_dupont)
            assert mienne.status_code == 200
            assert [r["agent"] for r in mienne.json()["runs"]] == ["demo"]
            assert [
                s["session_id"]
                for s in (await http.get("/v1/sessions", headers=chez_dupont)).json()
            ] == [session_id]

            # L'autre ne le trouve pas : il ne peut même pas savoir qu'il existe.
            assert (await http.get(f"/v1/runs/{run_id}", headers=chez_martin)).status_code == 404
            autre = await http.get(f"/v1/sessions/{session_id}", headers=chez_martin)
            assert autre.status_code == 404
            assert (await http.get("/v1/sessions", headers=chez_martin)).json() == []


async def test_an_open_instance_with_declared_tenants_is_refused(demo: ConfigFactory) -> None:
    pytest.importorskip("fastapi", reason="extra 'http' absent")
    pytest.importorskip("httpx2", reason="client HTTP de test absent")
    import httpx2

    from loom_ia.access.http import create_app

    # Sans clé, rien ne dit au nom de qui agir : `default` n'étant pas
    # déclaré, la demande est refusée plutôt que servie au hasard.
    async with Loom.from_config(demo(tenants=two())) as loom:
        transport = httpx2.ASGITransport(create_app(loom))
        async with httpx2.AsyncClient(transport=transport, base_url="http://loom.test") as http:
            response = await http.post("/v1/agents/demo/runs", json={"message": QUESTION})
            assert response.status_code == 403
            assert "default" in response.json()["detail"]


async def test_an_agent_closed_to_a_tenant_is_refused_over_rest(demo: ConfigFactory) -> None:
    pytest.importorskip("fastapi", reason="extra 'http' absent")
    pytest.importorskip("httpx2", reason="client HTTP de test absent")
    import httpx2

    from loom_ia.access.http import create_app
    from loom_ia.config.keys import fingerprint, new_api_key

    cle = new_api_key()
    agents = [demo_agent(), demo_agent(name="autre")]
    path = demo(
        agents=agents,
        tenants=[{"id": DUPONT, "agents": ["autre"]}],
        security={"api_keys": [{"id": "app", "hash": fingerprint(cle), "tenant": DUPONT}]},
    )
    async with Loom.from_config(path) as loom:
        transport = httpx2.ASGITransport(create_app(loom))
        async with httpx2.AsyncClient(transport=transport, base_url="http://loom.test") as http:
            headers = {"Authorization": f"Bearer {cle}"}
            # L'agent existe et il est publié : ce n'est pas un 404, c'est un refus.
            refus = await http.post(
                "/v1/agents/demo/runs", json={"message": QUESTION}, headers=headers
            )
            assert refus.status_code == 403
            assert "non ouvert au client" in refus.json()["detail"]
            listed = await http.get("/v1/agents", headers=headers)
            assert [a["name"] for a in listed.json()] == ["autre"]


# --- Ligne de commande : --tenant, et ce que `loom validate` en dit ------------


def test_validate_shows_each_tenant_and_what_it_overrides(
    demo: ConfigFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    from loom_ia.access.cli import main

    config = demo(
        tenants=[
            {
                "id": DUPONT,
                "agents": ["demo"],
                "tools_deny": ["calculer"],
                "variables": {"entreprise": "Dupont"},
            },
            {"id": MARTIN},
        ]
    )
    assert main(["--config", str(config), "validate"]) == 0
    out = capsys.readouterr().out
    assert f"Clients    : {DUPONT}, {MARTIN}" in out
    assert f"  client {DUPONT}" in out
    assert "    agents : demo" in out
    assert "    outils retirés : calculer" in out
    assert "    variables : entreprise" in out
    assert f"  client {MARTIN}" in out
    assert "    rien de surchargé" in out
    # Un agent monté par client.
    assert "2 agent(s) monté(s) sans erreur." in out


def test_run_acts_for_the_tenant_given_on_the_command_line(
    demo: ConfigFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    from loom_ia.access.cli import main

    config = demo(storage={"events": {"backend": "jsonl", "path": "data"}}, tenants=two())
    assert main(["--config", str(config), "run", "demo", QUESTION, "--tenant", DUPONT]) == 0
    assert capsys.readouterr().out.strip() == ANSWER
    assert (config.parent / "data" / DUPONT).is_dir()
    assert not (config.parent / "data" / MARTIN).exists()


def test_run_without_tenant_is_refused_when_the_list_is_closed(
    demo: ConfigFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    from loom_ia.access.cli import main

    assert main(["--config", str(demo(tenants=two())), "run", "demo", QUESTION]) == 2
    assert "Client 'default' non déclaré" in capsys.readouterr().err
