# SPDX-License-Identifier: Apache-2.0
"""Au nom de qui une requête agit : sa clé, son client, ce qu'elle peut (#39).

Deux accès portent une clé d'API — REST depuis J1, et le serveur MCP en HTTP
depuis J5.2b. Ils partagent donc cette description de l'appelant, et avec elle
les mêmes règles : le client vient de la **clé** et de nulle part ailleurs, une
portée manquante refuse, et sans ``read_content`` une relecture est masquée.

Le MCP en **stdio** n'a pas de clé : rien dans le protocole ne dirait au nom de
qui une requête arrive. Son appelant est donc sans clé, avec le client choisi
au lancement (``loom mcp --tenant``), et tout lui est permis — c'est le process
qui l'a lancé qui décide, pas le protocole.
"""

from dataclasses import dataclass

from loom_ia.config.models import ApiKey, Scope
from loom_ia.core.model import DEFAULT_TENANT, TenantId


@dataclass(frozen=True, slots=True)
class Caller:
    """Une clé reconnue, ou personne — sur une instance ouverte ou en stdio."""

    key: ApiKey | None = None
    # Client d'un appelant sans clé. `default` en REST sur une instance
    # ouverte ; le client choisi au lancement pour un serveur MCP en stdio.
    without_key: TenantId = DEFAULT_TENANT

    @property
    def anonymous(self) -> bool:
        return self.key is None

    @property
    def tenant(self) -> TenantId:
        """Client au nom duquel cet appelant agit."""
        return self.key.tenant if self.key is not None else self.without_key

    def may(self, scope: Scope) -> bool:
        return self.key is None or scope in self.key.scopes

    def allows(self, agent: str) -> bool:
        return self.key is None or self.key.allows(agent)

    @property
    def masks(self) -> bool:
        """Vrai si les lectures de cet appelant doivent être privées de contenu."""
        return not self.may("read_content")
