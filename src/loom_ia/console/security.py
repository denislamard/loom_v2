# SPDX-License-Identifier: Apache-2.0
"""La sécurité de l'instance, telle qu'elle part vers la console (message ``security``, docs/analyse-securite-hello.md).

En sécurité locale, loom reste le référentiel des utilisateurs et des rôles :
la console la lit, et n'en renvoie rien. En sécurité partagée
(``security.source: console``), le message confirme la part appliquée : il
porte sa version et son empreinte. Seul le client que la console sert part — ses utilisateurs,
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
from loom_ia.config.models import SecurityConfig
from loom_ia.console.contract import (
    SecurityAgent,
    SecurityGateway,
    SecurityKey,
    SecurityPayload,
    SecurityRole,
    SecurityUser,
)


def security_payload(
    security: SecurityConfig,
    agents: Sequence[AgentSpec],
    console: ConsoleConfig,
    *,
    version: int | None = None,
    part_fingerprint: str | None = None,
) -> SecurityPayload:
    """Le message ``security`` pour le client de la console : tous les rôles déclarés, ses utilisateurs et leurs clés.

    Args:
        security: La section ``security`` de la config.
        agents: Les agents du client de la console.
        console: La section ``console`` : son client et sa passerelle.
        version: Version de la part appliquée, en sécurité partagée.
        part_fingerprint: Empreinte de la part appliquée, en sécurité partagée.

    Returns:
        Le document, avec son empreinte, calculée sans ``version`` ni
        ``part_fingerprint``.
    """
    users = [user for user in security.users if user.tenant == console.tenant]
    # Tous les rôles déclarés, portés ou non : le bloc access d'un agent peut citer
    # un rôle que personne ne porte, et la console les reprend tous dans son référentiel.
    roles = security.roles
    names = {user.id for user in users}
    document: dict[str, Any] = {
        "tenant": console.tenant,
        "roles": [SecurityRole.of(role).model_dump(mode="json") for role in roles],
        "users": [SecurityUser(id=user.id, name=user.name, roles=user.roles).model_dump(mode="json") for user in users],
        "api_keys": [
            SecurityKey(id=key.id, user=key.user, scopes=key.scopes, agents=key.agents, expires=key.expires).model_dump(mode="json")
            for key in security.api_keys
            if key.user in names
        ],
        "agents": [SecurityAgent.of(spec.name, spec.access).model_dump(mode="json") for spec in agents],
        "gateway": _gateway(security, console),
    }
    confirmed = {"version": version, "part_fingerprint": part_fingerprint} if version is not None else {}
    return SecurityPayload.model_validate({**document, "fingerprint": fingerprint(document), **confirmed})


def fingerprint(document: Mapping[str, Any]) -> str:
    """L'empreinte d'un document : ``sha256:`` et le condensé de son JSON canonique (clés triées, sans espaces).

    Args:
        document: Le document, sans son empreinte.

    Returns:
        L'empreinte.
    """
    canonical = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _gateway(security: SecurityConfig, console: ConsoleConfig) -> dict[str, Any] | None:
    """La passerelle des commandes, ou ``None`` sans ``console.commands.user``."""
    user = console.commands.user
    if user is None:
        return None
    endorsable = dict.fromkeys(endorsed for role in security.roles_of(user) if "act_as" in role.permissions for endorsed in role.act_as_roles)
    gateway = SecurityGateway(user=user, act_as_roles=tuple(endorsable), rate=console.commands.rate, max_ttl=console.commands.max_ttl)
    return gateway.model_dump(mode="json")
