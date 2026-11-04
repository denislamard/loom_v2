# SPDX-License-Identifier: Apache-2.0
"""Au nom de qui une requête agit : sa clé, son utilisateur, ce qu'elle peut (#39, D-4).

Deux accès portent une clé d'API — REST depuis J1, et le serveur MCP en HTTP
depuis J5.2b. Ils partagent donc cette description de l'appelant, et avec elle
les mêmes règles : le client vient de l'**utilisateur de la clé** et de nulle
part ailleurs, une permission manquante refuse, et sans ``read_content`` une
relecture est masquée.

Une clé identifie un utilisateur déclaré (D-4) : son ``actor``, dont les
permissions sont celles de ses rôles d'accès, que les ``scopes`` et les
``agents`` de la clé restreignent. Les actions (lancer, trancher, répondre,
arrêter) passent cet acteur au SDK, qui applique toute la matrice
(``access/authz.py``) ; ``may`` et ``allows`` ne servent qu'aux contrôles de
forme d'un accès — refuser tôt, filtrer une liste.

Sans clé, deux cas. Un utilisateur désigné — ``server.http.user`` pour une
instance locale (D-1b), ``loom mcp --user`` en stdio — est l'acteur, et ses
droits valent. Sinon l'appelant n'est personne : instance ouverte, ou MCP en
stdio lancé sans utilisateur, où tout est permis — c'est le process qui l'a
lancé qui décide, pas le protocole. Cette ouverture disparaît quand un
utilisateur devient obligatoire (lot 5).
"""

from dataclasses import dataclass
from typing import Self

from loom_ia.access.authz import Actor
from loom_ia.config.models import ApiKey, Permission, SecurityConfig
from loom_ia.core.model import DEFAULT_TENANT, TenantId


@dataclass(frozen=True, slots=True)
class Caller:
    """Une clé reconnue et son utilisateur, un utilisateur désigné, ou personne."""

    key: ApiKey | None = None
    # L'utilisateur au nom duquel la requête agit : celui de la clé, ou celui
    # que l'instance désigne sans clé ; absent sur une instance ouverte.
    actor: Actor | None = None
    # Client d'un appelant sans acteur. `default` en REST sur une instance
    # ouverte ; le client choisi au lancement pour un serveur MCP en stdio.
    without_key: TenantId = DEFAULT_TENANT

    @classmethod
    def identified(cls, key: ApiKey, security: SecurityConfig) -> Self:
        """L'appelant d'une clé reconnue : son utilisateur, avec la clé qui le restreint.

        La config garantit que l'utilisateur de la clé est déclaré
        (``LoomConfig.check``).
        """
        return cls(key=key, actor=Actor.of(security, key.user, key=key))

    @classmethod
    def acting(cls, security: SecurityConfig, user: str) -> Self:
        """L'appelant sans clé d'une instance qui désigne son utilisateur (D-1b, stdio)."""
        return cls(actor=Actor.of(security, user))

    @property
    def user(self) -> str | None:
        """Identifiant de l'utilisateur au nom duquel la requête agit, s'il y en a un."""
        return None if self.actor is None else self.actor.user

    @property
    def tenant(self) -> TenantId:
        """Client au nom duquel cet appelant agit."""
        return self.actor.tenant if self.actor is not None else self.without_key

    @property
    def granted(self) -> frozenset[Permission]:
        """Permissions de l'utilisateur, tous agents confondus, restreintes par la clé (D-4)."""
        if self.actor is None:
            return frozenset()
        mine: frozenset[Permission] = frozenset(
            permission for role in self.actor.roles for permission in role.permissions
        )
        if self.key is not None and self.key.scopes is not None:
            mine &= frozenset(self.key.scopes)
        return mine

    def may(self, permission: Permission) -> bool:
        """L'un des rôles porte la permission, et la clé la laisse ; sans acteur, tout."""
        return self.actor is None or permission in self.granted

    def allows(self, agent: str, permission: Permission = "read") -> bool:
        """La clé laisse cet agent, et l'un des rôles y a la permission ; sans acteur, tout.

        Les listes ``access`` d'un agent ne comptent pas ici : elles ne bornent
        que ``run``, ``answer`` et ``cancel``, que le SDK vérifie en agissant.
        """
        if self.actor is None:
            return True
        if self.key is not None and not self.key.allows(agent):
            return False
        return any(
            permission in role.permissions and role.allows(agent) for role in self.actor.roles
        )

    @property
    def masks(self) -> bool:
        """Vrai si les lectures de cet appelant doivent être privées de contenu."""
        return not self.may("read_content")
