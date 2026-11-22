# SPDX-License-Identifier: Apache-2.0
"""La sécurité poussée vers la console en contrat 2 (docs/analyse-securite-hello.md, lot 1)."""

import json
from datetime import UTC, datetime

from conftest import OWNER, ConfigFactory, demo_agent

from loom_ia.access import Loom
from loom_ia.config.console import ConsoleCommands, ConsoleConfig
from loom_ia.config.models import AccessRole, ApiKey, SecurityConfig, UserSpec
from loom_ia.console.contract import SecurityAgent, SecurityGateway, SecurityKey, SecurityRole, SecurityUser
from loom_ia.console.security import fingerprint, security_payload
from loom_ia.core.model import TenantId

HASH = "sha256:" + "ab" * 32
EXPIRES = datetime(2027, 1, 1, tzinfo=UTC)


def security(**changes: object) -> SecurityConfig:
    values: dict[str, object] = {
        "roles": (
            AccessRole(id="owner", permissions=("run", "read", "approve")),
            AccessRole(id="lecteur", description="Lit seulement", permissions=("read",), agents=("devis",)),
            AccessRole(id="console", permissions=("act_as",), act_as_roles=("owner", "lecteur")),
            AccessRole(id="client-acme", permissions=("run",)),
        ),
        "users": (
            UserSpec(id="dev", name="Denis", roles=("owner",)),
            UserSpec(id="paul", roles=("lecteur",)),
            UserSpec(id="console-gateway", roles=("console",)),
            UserSpec(id="marc", roles=("client-acme",), tenant=TenantId("acme")),
        ),
        "api_keys": (
            ApiKey(id="k-dev", hash=HASH, user="dev", scopes=("read",), agents=("devis",), expires=EXPIRES),
            ApiKey(id="k-marc", hash=HASH, user="marc"),
        ),
        **changes,
    }
    return SecurityConfig.model_validate(values)


def console(user: str | None = "console-gateway") -> ConsoleConfig:
    return ConsoleConfig(enable=True, url="wss://console.exemple.fr/loom", key_env="K", commands=ConsoleCommands(user=user, rate=30, max_ttl=120))


def test_only_the_console_tenant_leaves() -> None:
    sent = security_payload(security(), (), console())
    assert sent.tenant == "default"
    assert sent.users == (
        SecurityUser(id="dev", name="Denis", roles=("owner",)),
        SecurityUser(id="paul", roles=("lecteur",)),
        SecurityUser(id="console-gateway", roles=("console",)),
    )
    assert [role.id for role in sent.roles] == ["owner", "lecteur", "console"]
    assert sent.roles[1] == SecurityRole(id="lecteur", description="Lit seulement", permissions=("read",), agents=("devis",))
    assert sent.api_keys == (SecurityKey(id="k-dev", user="dev", scopes=("read",), agents=("devis",), expires=EXPIRES),)


def test_no_key_fingerprint_ever_leaves() -> None:
    text = security_payload(security(), (), console()).model_dump_json()
    assert HASH not in text and "hash" not in text


def test_the_gateway_says_whom_it_endorses() -> None:
    sent = security_payload(security(), (), console())
    assert sent.gateway == SecurityGateway(user="console-gateway", act_as_roles=("owner", "lecteur"), rate=30, max_ttl=120)


def test_without_a_gateway_there_is_none() -> None:
    assert security_payload(security(), (), console(user=None)).gateway is None


def test_an_endorsable_role_nobody_carries_still_leaves() -> None:
    roles = (
        AccessRole(id="owner", permissions=("run",)),
        AccessRole(id="assistant", permissions=("read",)),
        AccessRole(id="console", permissions=("act_as",), act_as_roles=("assistant",)),
    )
    users = (UserSpec(id="dev", roles=("owner",)), UserSpec(id="console-gateway", roles=("console",)))
    sent = security_payload(security(roles=roles, users=users, api_keys=()), (), console())
    assert [role.id for role in sent.roles] == ["owner", "assistant", "console"]


def test_the_fingerprint_is_the_digest_of_the_rest() -> None:
    sent = security_payload(security(), (), console())
    document = sent.model_dump(mode="json", exclude={"fingerprint"})
    assert sent.fingerprint == fingerprint(document)
    canonical = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    assert canonical.startswith('{"agents":[],"api_keys":')


def test_the_fingerprint_is_stable_and_follows_any_change() -> None:
    first = security_payload(security(), (), console()).fingerprint
    assert security_payload(security(), (), console()).fingerprint == first
    changed = security(roles=(*security().roles[:1], AccessRole(id="lecteur", permissions=("read", "read_content")), *security().roles[2:]))
    assert security_payload(changed, (), console()).fingerprint != first
    assert security_payload(security(), (), console(user=None)).fingerprint != first


async def test_the_agents_give_their_access_block(demo: ConfigFactory) -> None:
    access = {"run": ["owner"], "approve": {"default": ["owner"], "tools": {"calculer": ["owner"]}}, "self_approval": False}
    owners = {"roles": [OWNER], "users": [{"id": "dev", "roles": ["owner"]}], "audit": {"path": "audit.jsonl"}}
    config = demo(agents=[demo_agent(access=access), demo_agent(name="libre")], security=owners)
    async with Loom.from_config(config) as loom:
        sent = security_payload(loom.config.security, loom.agents, console(user=None))
    by_name = {agent.name: agent for agent in sent.agents}
    assert by_name["demo"] == SecurityAgent(
        name="demo", run=("owner",), approve_default=("owner",), approve_tools={"calculer": ("owner",)}, self_approval=False
    )
    assert by_name["libre"] == SecurityAgent(name="libre")
