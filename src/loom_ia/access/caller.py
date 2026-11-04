# SPDX-License-Identifier: Apache-2.0
"""Au nom de qui une requête agit : sa clé, son utilisateur, ce qu'elle peut (#39, D-4).

Deux accès portent une clé d'API — REST depuis J1, et le serveur MCP en HTTP
depuis J5.2b. Ils partagent donc cette description de l'appelant, et avec elle
les mêmes règles : le client vient de l'**utilisateur de la clé** et de nulle
part ailleurs, une permission manquante refuse, et sans ``read_content`` une
relecture est masquée.

Une clé identifie un utilisateur déclaré (D-4) : les permissions sont celles
de ses rôles d'accès, que les ``scopes`` de la clé peuvent restreindre. Le
contrôle complet — rôles bornés à des agents, listes ``access`` des agents,
approbateurs d'une demande — est celui de ``access/authz.py`` ; les accès s'y
branchent aux lots 3 (SDK) et 4 (REST, MCP).

Le MCP en **stdio** n'a pas de clé : rien dans le protocole ne dirait au nom de
qui une requête arrive. Son appelant est donc sans clé, avec le client choisi
au lancement (``loom mcp --tenant``), et tout lui est permis — c'est le process
qui l'a lancé qui décide, pas le protocole.
"""

from dataclasses import dataclass
from typing import Self

from loom_ia.config.models import ApiKey, Permission, SecurityConfig
from loom_ia.core.model import DEFAULT_TENANT, TenantId


@dataclass(frozen=True, slots=True)
class Caller:
    """Une clé reconnue et son utilisateur, ou personne — sur une instance ouverte ou en stdio."""

    key: ApiKey | None = None
    # Utilisateur de la clé ; absent sans clé.
    user: str | None = None
    # Client de l'utilisateur de la clé.
    key_tenant: TenantId = DEFAULT_TENANT
    # Permissions de l'utilisateur, restreintes par la clé (D-4).
    granted: frozenset[Permission] = frozenset()
    # Client d'un appelant sans clé. `default` en REST sur une instance
    # ouverte ; le client choisi au lancement pour un serveur MCP en stdio.
    without_key: TenantId = DEFAULT_TENANT

    @classmethod
    def identified(cls, key: ApiKey, security: SecurityConfig) -> Self:
        """L'appelant d'une clé reconnue : son utilisateur, son client, ses permissions.

        La config garantit que l'utilisateur de la clé est déclaré
        (``LoomConfig.check``).
        """
        user = security.user(key.user)
        granted = security.permissions_of(user.id)
        if key.scopes is not None:
            granted &= frozenset(key.scopes)
        return cls(key=key, user=user.id, key_tenant=user.tenant, granted=granted)

    @property
    def tenant(self) -> TenantId:
        """Client au nom duquel cet appelant agit."""
        return self.key_tenant if self.key is not None else self.without_key

    def may(self, permission: Permission) -> bool:
        return self.key is None or permission in self.granted

    def allows(self, agent: str) -> bool:
        return self.key is None or self.key.allows(agent)

    @property
    def masks(self) -> bool:
        """Vrai si les lectures de cet appelant doivent être privées de contenu."""
        return not self.may("read_content")
