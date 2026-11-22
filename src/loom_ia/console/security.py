# SPDX-License-Identifier: Apache-2.0
"""La sécurité de l'instance, telle qu'elle part vers la console (message ``security``, docs/analyse-securite-hello.md).

loom reste le référentiel des utilisateurs et des rôles : la console la lit, et
n'en renvoie rien. Seul le client que la console sert part — ses utilisateurs,
les rôles qu'ils portent ou endossent, leurs clés, ses agents —, et jamais un
secret : ni l'empreinte d'une clé d'API, ni la clé de la console.

L'empreinte (``fingerprint``) est celle du JSON canonique du document : elle ne
change que si ce qui part change, et deux process d'une même instance aux
configs différentes en ont deux.
"""

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

from loom_ia.agents.spec import AgentSpec
from loom_ia.config.console import ConsoleConfig
from loom_ia.config.models import AccessRole, SecurityConfig
from loom_ia.console.contract import (
    SecurityAgent,
    SecurityGateway,
    SecurityKey,
    SecurityPayload,
    SecurityRole,
    SecurityUser,
)


def security_payload(security: SecurityConfig, agents: Sequence[AgentSpec], console: ConsoleConfig) -> SecurityPayload:
    """Le message ``security`` pour le client de la console.

    Args:
        security: La section ``security`` de la config.
        agents: Les agents du client de la console.
        console: La section ``console`` : son client et sa passerelle.

    Returns:
        Le document, avec son empreinte.
    """
    users = [user for user in security.users if user.tenant == console.tenant]
    carried = {role for user in users for role in user.roles}
    endorsed = {endorsable for role in security.roles if role.id in carried for endorsable in role.act_as_roles}
    roles = [role for role in security.roles if role.id in carried | endorsed]
    names = {user.id for user in users}
    document: dict[str, Any] = {
        "tenant": console.tenant,
        "roles": [_role(role).model_dump(mode="json") for role in roles],
        "users": [SecurityUser(id=user.id, name=user.name, roles=user.roles).model_dump(mode="json") for user in users],
        "api_keys": [
            SecurityKey(id=key.id, user=key.user, scopes=key.scopes, agents=key.agents, expires=key.expires).model_dump(mode="json")
            for key in security.api_keys
            if key.user in names
        ],
        "agents": [_agent(spec).model_dump(mode="json") for spec in agents],
        "gateway": _gateway(security, console),
    }
    return SecurityPayload.model_validate({**document, "fingerprint": fingerprint(document)})


def fingerprint(document: Mapping[str, Any]) -> str:
    """L'empreinte d'un document : ``sha256:`` et le condensé de son JSON canonique (clés triées, sans espaces).

    Args:
        document: Le document, sans son empreinte.

    Returns:
        L'empreinte.
    """
    canonical = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _role(role: AccessRole) -> SecurityRole:
    """Un rôle d'accès, tel qu'il part."""
    return SecurityRole(id=role.id, description=role.description, permissions=role.permissions, agents=role.agents, act_as_roles=role.act_as_roles)


def _agent(spec: AgentSpec) -> SecurityAgent:
    """Le bloc ``access`` d'un agent, tel qu'il part."""
    access = spec.access
    return SecurityAgent(
        name=spec.name,
        run=access.run,
        answer=access.answer,
        cancel=access.cancel,
        approve_default=access.approve.default,
        approve_tools=dict(access.approve.tools),
        self_approval=access.self_approval,
    )


def _gateway(security: SecurityConfig, console: ConsoleConfig) -> dict[str, Any] | None:
    """La passerelle des commandes, ou ``None`` sans ``console.commands.user``."""
    user = console.commands.user
    if user is None:
        return None
    endorsable = dict.fromkeys(endorsed for role in security.roles_of(user) if "act_as" in role.permissions for endorsed in role.act_as_roles)
    gateway = SecurityGateway(user=user, act_as_roles=tuple(endorsable), rate=console.commands.rate, max_ttl=console.commands.max_ttl)
    return gateway.model_dump(mode="json")
