# SPDX-License-Identifier: Apache-2.0
"""Sécurité partagée : ``security.source: console``, part ``security.sync``, application à chaud et cache (référentiel de Heddlo, lot 1)."""

import asyncio
import json
import stat
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("websockets", reason="extra 'console' absent")
pytest.importorskip("cryptography", reason="extra 'console' absent")

from conftest import DEV, EVERYTHING, ConfigFactory, demo_agent

from loom_ia.access import Loom
from loom_ia.access.cli import console as console_cli
from loom_ia.access.cli.main import main
from loom_ia.agents.spec import AgentAccess
from loom_ia.config import ConfigError, LoomConfig, load_config
from loom_ia.config.keys import fingerprint, new_api_key
from loom_ia.config.models import AccessRole, SecurityConfig, UserSpec, check_security_references
from loom_ia.console.contract import (
    ErrorPayload,
    HelloPayload,
    SecurityAgent,
    SecurityPayload,
    SecurityRole,
    SecuritySyncKey,
    SecuritySyncPayload,
    SecurityUser,
)
from loom_ia.console.shared import (
    CACHE_FILE,
    PartRefused,
    SecurityCache,
    access_of,
    applied,
    check_part,
    compared,
    make_part,
    part_fingerprint,
)
from loom_ia.testing.console_server import FakeConsole

KEY = "s3cret"
ENV = {"LOOM_CONSOLE_KEY": KEY}
SHARED: dict[str, Any] = {"source": "console"}
API_KEY = new_api_key()

ROLES = (
    SecurityRole(id="owner", permissions=tuple(EVERYTHING)),  # type: ignore[arg-type]
    SecurityRole(id="lecteur", permissions=("read",)),
    SecurityRole(id="console", permissions=("act_as",), act_as_roles=("owner", "lecteur")),
)


def part(version: int = 1, *, users: tuple[SecurityUser, ...] | None = None, keys: tuple[SecuritySyncKey, ...] = (), **more: Any) -> SecuritySyncPayload:
    """Une part : ``dev`` (owner), ``paul`` (lecteur) et la passerelle, par défaut."""
    chosen = (
        users
        if users is not None
        else (
            SecurityUser(id=DEV, roles=("owner",)),
            SecurityUser(id="paul", roles=("lecteur",)),
            SecurityUser(id="console-gateway", roles=("console",)),
        )
    )
    return make_part(version=version, tenant=more.get("tenant", "default"), roles=more.get("roles", ROLES), users=chosen, api_keys=keys)


def _strict(_config: LoomConfig) -> bool:
    """Le profil ``prod`` pour l'attente de la première part seulement."""
    return True


def section(url: str, **more: Any) -> dict[str, Any]:
    return {"enable": True, "url": url, "key_env": "LOOM_CONSOLE_KEY", "instance_id": "atelier", "commands": {"user": "console-gateway"}, **more}


# --- Configuration ------------------------------------------------------------------


def test_a_shared_config_loads_without_roles_nor_users(demo: ConfigFactory) -> None:
    config = load_config(demo(profile="dev", security=SHARED, console=section("ws://127.0.0.1:9/loom")))
    assert config.security.source == "console"
    assert not config.security.received
    assert config.security.audit is not None
    assert config.workpath is not None
    assert config.security.audit.path == config.workpath / "data" / "audit.jsonl"


@pytest.mark.parametrize("declared", ["roles", "users", "api_keys"])
def test_a_shared_config_refuses_what_the_console_sends(declared: str) -> None:
    values: dict[str, Any] = {
        "roles": [{"id": "owner", "permissions": ["read"]}],
        "users": [{"id": DEV, "roles": ["owner"]}],
        "api_keys": [{"id": "k", "user": DEV, "hash": fingerprint(API_KEY)}],
    }
    with pytest.raises(ValueError, match=f"security.source: console : {declared} viennent de la console"):
        SecurityConfig.model_validate({"source": "console", declared: values[declared]})


def test_a_shared_config_needs_an_enabled_console(demo: ConfigFactory) -> None:
    with pytest.raises(ConfigError, match="déclarer une section 'console' avec 'enable: true'"):
        load_config(demo(profile="dev", security=SHARED))


def test_a_shared_config_refuses_several_clients(demo: ConfigFactory) -> None:
    tenants = [{"id": "dupont"}, {"id": "martin"}]
    with pytest.raises(ConfigError, match="2 clients déclarés"):
        load_config(demo(profile="dev", security=SHARED, tenants=tenants, console=section("ws://127.0.0.1:9/loom", tenant="dupont")))


def test_a_shared_config_refuses_a_local_user(demo: ConfigFactory) -> None:
    with pytest.raises(ConfigError, match=r"'server\.http\.user' est refusé"):
        load_config(demo(profile="dev", security=SHARED, server={"http": {"user": DEV}}, console=section("ws://127.0.0.1:9/loom")))


def test_a_local_config_still_needs_roles_and_users() -> None:
    access = {"demo": AgentAccess(run=("absent",))}
    security = SecurityConfig(roles=(AccessRole(id="owner", permissions=("run",)),), users=(UserSpec(id=DEV, roles=("owner",)),))
    with pytest.raises(ValueError, match="rôle d'accès 'absent' non déclaré"):
        check_security_references(security, access)


def test_a_shared_role_may_be_bound_to_an_agent_the_instance_lacks() -> None:
    security = SecurityConfig(roles=(AccessRole(id="devis", permissions=("read",), agents=("devis",)),), users=(UserSpec(id=DEV, roles=("devis",)),))
    with pytest.raises(ValueError, match="agent 'devis' non déclaré"):
        check_security_references(security, {"demo": AgentAccess()})
    check_security_references(security, {"demo": AgentAccess()}, shared=True)


# --- Part, contrôles ------------------------------------------------------------------


def test_the_fingerprint_of_a_part_leaves_its_version_out() -> None:
    first, second = part(1), part(2)
    assert first.fingerprint == second.fingerprint == part_fingerprint(first)
    assert part(1, users=()).fingerprint != first.fingerprint


@pytest.mark.parametrize(
    ("previous", "received", "expected"),
    [(None, 3, "new"), (3, 4, "new"), (3, 3, "same"), (4, 3, "older")],
)
def test_a_part_is_compared_with_the_previous(previous: int | None, received: int, expected: str) -> None:
    assert compared(None if previous is None else part(previous), part(received)) == expected


def test_a_rewritten_part_keeps_its_version_and_changes_its_content() -> None:
    assert compared(part(3), part(3, users=())) == "rewritten"


def test_check_part_passes_what_loom_would_apply() -> None:
    agents = access_of((SecurityAgent(name="devis", run=("owner",), approve_default=("owner",)),))
    security = check_part(part(), tenant="default", agents=agents, gateway_user="console-gateway")
    assert [user.id for user in security.users] == [DEV, "paul", "console-gateway"]
    assert security.source == "console"


def test_check_part_refuses_a_role_an_agent_cites_and_the_part_lacks() -> None:
    agents = access_of((SecurityAgent(name="devis", run=("assistante",)),))
    with pytest.raises(PartRefused, match="rôle d'accès 'assistante' non déclaré"):
        check_part(part(), tenant="default", agents=agents)


def test_check_part_refuses_a_part_without_the_gateway() -> None:
    users = (SecurityUser(id=DEV, roles=("owner",)),)
    with pytest.raises(PartRefused, match=r"console\.commands\.user \('console-gateway'\) : utilisateur non déclaré"):
        check_part(part(users=users), tenant="default", agents={}, gateway_user="console-gateway")


def test_check_part_wants_a_key_for_mcp_over_http() -> None:
    with pytest.raises(PartRefused, match="au moins une clé"):
        check_part(part(), tenant="default", agents={}, keys_required=True)
    check_part(part(users=()), tenant="default", agents={}, keys_required=True)


def test_check_part_refuses_another_client_or_a_false_fingerprint() -> None:
    with pytest.raises(PartRefused, match="pour le client 'martin'"):
        check_part(part(tenant="martin"), tenant="default", agents={})
    forged = part().model_copy(update={"users": ()})
    with pytest.raises(PartRefused, match="empreinte ne correspond pas"):
        check_part(forged, tenant="default", agents={})


def test_a_local_instance_applies_no_part(demo: ConfigFactory) -> None:
    config = load_config(demo(profile="dev", console=section("ws://127.0.0.1:9/loom", commands={})))
    with pytest.raises(PartRefused, match="sécurité de cette instance est locale"):
        applied(config, part())


def test_applied_runs_the_checks_of_the_config(demo: ConfigFactory) -> None:
    config = load_config(demo(profile="dev", security=SHARED, console=section("ws://127.0.0.1:9/loom"), agents=[demo_agent(access={"run": ["owner"]})]))
    done = applied(config, part())
    assert done.security.received
    assert done.security.audit == config.security.audit
    assert config.security.users == (), "la config d'origine ne change pas"
    roles = (SecurityRole(id="lecteur", permissions=("read",)), SecurityRole(id="console", permissions=("act_as",), act_as_roles=("lecteur",)))
    with pytest.raises(PartRefused, match="Agent 'demo', access : rôle d'accès 'owner' non déclaré"):
        applied(config, part(users=(SecurityUser(id="console-gateway", roles=("console",)),), roles=roles))


# --- Cache ----------------------------------------------------------------------------


def test_the_cache_reads_back_what_it_wrote(tmp_path: Path) -> None:
    cache = SecurityCache.of(tmp_path, "atelier", KEY)
    assert cache.read() is None
    cache.write(part(7))
    assert cache.read() == part(7)
    assert stat.S_IMODE((tmp_path / CACHE_FILE).stat().st_mode) == 0o600
    header = json.loads((tmp_path / CACHE_FILE).read_text(encoding="utf-8"))
    assert (header["contract"], header["instance"], header["version"]) == (1, "atelier", 7)
    assert "paul" not in (tmp_path / CACHE_FILE).read_text(encoding="utf-8"), "le contenu est scellé"


@pytest.mark.parametrize(
    ("instance", "key", "said"),
    [("atelier", "autre-clé", "scellé par une autre clé d'instance"), ("bureau", KEY, "il est de l'instance 'atelier'")],
)
def test_a_cache_of_another_key_or_instance_is_refused(tmp_path: Path, instance: str, key: str, said: str, caplog: pytest.LogCaptureFixture) -> None:
    SecurityCache.of(tmp_path, "atelier", KEY).write(part())
    assert SecurityCache.of(tmp_path, instance, key).read() is None
    assert said in caplog.text


def test_a_tampered_cache_is_refused(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    cache = SecurityCache.of(tmp_path, "atelier", KEY)
    cache.write(part(2))
    header = json.loads(cache.path.read_text(encoding="utf-8"))
    for change in ({"version": 3}, {"contract": 2}, {"sealed": header["sealed"][:-8] + "AAAAAAA="}):
        cache.path.write_text(json.dumps({**header, **change}), encoding="utf-8")
        assert cache.read() is None
    cache.path.write_text("pas du json", encoding="utf-8")
    assert cache.read() is None
    assert caplog.text.count("traité comme absent") == 4


# --- Instance -----------------------------------------------------------------------


async def test_a_shared_instance_announces_itself_and_applies_the_part(demo: ConfigFactory) -> None:
    async with FakeConsole(key=KEY, security=part(4)) as fake:
        config = demo(profile="dev", security=SHARED, console=section(fake.url))
        async with Loom.from_config(config, environ=ENV) as loom:
            assert loom.security_part is None
            assert await loom.start_console()
            assert loom.security_part == part(4)
            assert [user.id for user in loom.config.security.users] == [DEV, "paul", "console-gateway"]
            async with asyncio.timeout(5):
                (item,) = await fake.wait_for("security")
            hello = fake.of("hello")[0].payload
        assert isinstance(hello, HelloPayload)
        assert (hello.security_source, hello.gateway_user) == ("console", "console-gateway")
        confirmed = item.payload
        assert isinstance(confirmed, SecurityPayload)
        assert (confirmed.version, confirmed.part_fingerprint) == (4, part(4).fingerprint)
        assert [user.id for user in confirmed.users] == [DEV, "paul", "console-gateway"]


async def test_a_new_part_applies_at_once_and_a_refused_one_keeps_the_previous(demo: ConfigFactory) -> None:
    async with FakeConsole(key=KEY, security=part(1)) as fake:
        config = demo(profile="dev", security=SHARED, console=section(fake.url))
        async with Loom.from_config(config, environ=ENV) as loom:
            await loom.start_console()
            async with asyncio.timeout(5):
                await fake.wait_for("security")
            without_paul = (SecurityUser(id=DEV, roles=("owner",)), SecurityUser(id="console-gateway", roles=("console",)))
            await fake.sync(part(2, users=without_paul))
            async with asyncio.timeout(5):
                await fake.wait_for("security", 2)
            assert "paul" not in {user.id for user in loom.config.security.users}
            await fake.sync(part(3, users=(SecurityUser(id=DEV, roles=("owner",)),)))
            async with asyncio.timeout(5):
                (error,) = await fake.wait_for("error")
            assert loom.security_part == part(2, users=without_paul)
        refused = error.payload
        assert isinstance(refused, ErrorPayload)
        assert refused.code == "invalid_message"
        assert "console-gateway" in refused.message
        assert refused.about is not None


async def test_the_same_part_is_confirmed_without_rewriting_the_cache(demo: ConfigFactory, tmp_path: Path) -> None:
    async with FakeConsole(key=KEY, security=part(5)) as fake:
        config = demo(profile="dev", security=SHARED, console=section(fake.url))
        async with Loom.from_config(config, environ=ENV) as loom:
            await loom.start_console()
            async with asyncio.timeout(5):
                await fake.wait_for("security")
            written = (tmp_path / CACHE_FILE).stat().st_mtime_ns
            assert loom.apply_security(part(5)) == "same"
            assert (tmp_path / CACHE_FILE).stat().st_mtime_ns == written


async def test_an_older_part_is_applied_with_a_warning(demo: ConfigFactory, caplog: pytest.LogCaptureFixture) -> None:
    async with FakeConsole(key=KEY, security=part(9)) as fake:
        config = demo(profile="dev", security=SHARED, console=section(fake.url))
        async with Loom.from_config(config, environ=ENV) as loom:
            await loom.start_console()
            assert loom.apply_security(part(8)) == "older"
            assert loom.security_part == part(8)
    assert "version 8 reçue, plus basse que la 9 appliquée (contenu identique)" in caplog.text


async def test_an_instance_restarts_on_its_cache_without_the_console(demo: ConfigFactory, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("INFO")
    async with FakeConsole(key=KEY, security=part(6)) as fake:
        async with Loom.from_config(demo(profile="dev", security=SHARED, console=section(fake.url)), environ=ENV) as loom:
            await loom.start_console()
    config = demo(profile="dev", security=SHARED, console=section("ws://127.0.0.1:9/loom"))
    async with Loom.from_config(config, environ=ENV) as loom:
        assert loom.security_part == part(6)
        assert "paul" in {user.id for user in loom.config.security.users}
        async with asyncio.timeout(2):
            assert await loom.start_console(grace=0.1), "le cache suffit : pas d'attente de la console"
    assert "version 6 relue du cache" in caplog.text


async def test_without_cache_nor_console_production_refuses_to_start(demo: ConfigFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("loom_ia.console.shared.FIRST_PART_TIMEOUT", 0.2)
    async with FakeConsole(key=KEY) as fake:
        config = demo(profile="dev", security=SHARED, console=section(fake.url))
        async with Loom.from_config(config, environ=ENV) as loom:
            # La production exige wss:// : l'essai garde la fausse console en clair, et ne durcit que l'attente.
            monkeypatch.setattr(LoomConfig, "strict", property(_strict))
            with pytest.raises(ConfigError, match="ni cache, ni part reçue de la console"):
                await loom.start_console()


async def test_without_cache_nor_console_dev_starts_with_nobody(demo: ConfigFactory, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.setattr("loom_ia.console.shared.FIRST_PART_TIMEOUT", 0.2)
    async with FakeConsole(key=KEY) as fake:
        async with Loom.from_config(demo(profile="dev", security=SHARED, console=section(fake.url)), environ=ENV) as loom:
            assert await loom.start_console()
            assert not loom.config.security.received
    assert "toute action est refusée jusqu'à la première part" in caplog.text


async def test_a_key_removed_from_the_part_is_refused_at_the_next_request(demo: ConfigFactory) -> None:
    pytest.importorskip("fastapi", reason="extra 'http' absent")
    httpx2 = pytest.importorskip("httpx2", reason="client HTTP de test absent")
    from loom_ia.access.http import create_app

    key = SecuritySyncKey(id="k-dev", user=DEV, hash=fingerprint(API_KEY))
    async with FakeConsole(key=KEY, security=part(1, keys=(key,))) as fake:
        async with Loom.from_config(demo(profile="dev", security=SHARED, console=section(fake.url)), environ=ENV) as loom:
            await loom.start_console()
            transport = httpx2.ASGITransport(create_app(loom))
            async with httpx2.AsyncClient(transport=transport, base_url="http://loom.test") as http:
                headers = {"Authorization": f"Bearer {API_KEY}"}
                assert (await http.get("/v1/sessions", headers=headers)).status_code == 200
                await fake.sync(part(2))
                async with asyncio.timeout(5):
                    await fake.wait_for("security", 2)
                assert (await http.get("/v1/sessions", headers=headers)).status_code == 401


# --- Ligne de commande ----------------------------------------------------------------


def test_validate_says_where_the_security_comes_from(demo: ConfigFactory, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOOM_CONSOLE_KEY", KEY)
    path = demo(profile="dev", security=SHARED, console=section("ws://127.0.0.1:9/loom"))
    assert main(["--config", str(path), "validate", "--no-keys"]) == 0
    out = capsys.readouterr().out
    assert "  Sécurité     partagée, reçue de la console (security.source: console)\n" in out
    assert "aucun cache valide : la console est attendue au démarrage (10 s)" in out
    SecurityCache.of(path.parent, "atelier", KEY).write(part(3))
    assert main(["--config", str(path), "validate", "--no-keys"]) == 0
    out = capsys.readouterr().out
    assert "cache : version 3" in out
    assert "paul" in out


def test_keys_create_is_refused_for_a_shared_instance(demo: ConfigFactory, capsys: pytest.CaptureFixture[str]) -> None:
    path = demo(profile="dev", security=SHARED, console=section("ws://127.0.0.1:9/loom"))
    assert main(["--config", str(path), "keys", "create", "k1", "--user", DEV]) == 2
    assert "les clés d'API se créent dans la console" in capsys.readouterr().err


def test_a_command_acts_from_the_cache(demo: ConfigFactory, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOOM_CONSOLE_KEY", KEY)
    path = demo(profile="dev", security=SHARED, console=section("ws://127.0.0.1:9/loom"))
    SecurityCache.of(path.parent, "atelier", KEY).write(part(1))
    assert main(["--config", str(path), "run", "demo", "Combien font 2 + 2 ?", "--user", "inconnu"]) == 2
    assert "Utilisateur 'inconnu' inconnu (utilisateurs : console-gateway, dev, paul)" in capsys.readouterr().err


def test_listen_reads_a_part_from_yaml(tmp_path: Path) -> None:
    path = tmp_path / "part.yaml"
    path.write_text(
        "tenant: default\n"
        "roles: [{id: owner, permissions: [run, read]}]\n"
        f"users: [{{id: {DEV}, roles: [owner]}}]\n"
        f'api_keys: [{{id: k1, user: {DEV}, hash: "{fingerprint(API_KEY)}"}}]\n',
        encoding="utf-8",
    )
    first = console_cli.read_part(path)
    assert (first.version, [user.id for user in first.users], first.api_keys[0].hash) == (1, [DEV], fingerprint(API_KEY))
    assert console_cli.read_part(path, previous=first.version).version == 2
    path.write_text("version: 7\ntenant: default\n", encoding="utf-8")
    assert console_cli.read_part(path).version == 7
    path.write_text("roles: [{id: owner}]\n", encoding="utf-8")
    with pytest.raises(ValueError, match="permissions"):
        console_cli.read_part(path)
    path.write_text("inconnue: 1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="clé\\(s\\) inconnue\\(s\\) : inconnue"):
        console_cli.read_part(path)


async def test_a_command_without_cache_waits_for_the_first_part(
    demo: ConfigFactory, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LOOM_CONSOLE_KEY", KEY)
    async with FakeConsole(key=KEY, security=part(2)) as fake:
        path = demo(profile="dev", security=SHARED, console=section(fake.url))
        code = await asyncio.to_thread(main, ["--config", str(path), "run", "demo", "Combien font 2 + 2 ?", "--user", "inconnu"])
    assert code == 2
    assert "Utilisateur 'inconnu' inconnu (utilisateurs : console-gateway, dev, paul)" in capsys.readouterr().err
    assert (path.parent / CACHE_FILE).is_file(), "la part reçue est gardée pour la commande suivante"


async def test_a_local_instance_never_receives_a_part(demo: ConfigFactory) -> None:
    async with FakeConsole(key=KEY, security=part(1)) as fake:
        config = demo(profile="dev", console=section(fake.url, commands={}))
        async with Loom.from_config(config, environ=ENV) as loom:
            await loom.start_console()
            async with asyncio.timeout(5):
                (item,) = await fake.wait_for("security")
            await fake.sync(part(2))
            await asyncio.sleep(0.1)
            assert loom.security_part is None
        hello = fake.of("hello")[0].payload
        assert isinstance(hello, HelloPayload)
        assert (hello.security_source, hello.gateway_user) == ("file", None)
        assert isinstance(item.payload, SecurityPayload)
        assert item.payload.version is None
        assert not fake.of("error")


async def test_with_shared_security_gives_the_cache_or_leaves_a_local_config(demo: ConfigFactory) -> None:
    from loom_ia.access import with_shared_security

    local = load_config(demo(profile="dev"))
    assert await with_shared_security(local) is local
    path = demo(profile="dev", security=SHARED, console=section("ws://127.0.0.1:9/loom"))
    SecurityCache.of(path.parent, "atelier", KEY).write(part(4))
    config = await with_shared_security(load_config(path), ENV)
    assert [user.id for user in config.security.users] == [DEV, "paul", "console-gateway"]
