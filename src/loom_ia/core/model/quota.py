# SPDX-License-Identifier: Apache-2.0
"""Débit : quotas d'un client, limitation d'une clé d'API (L3, #39, §17.8).

Un budget dit **combien** on peut dépenser, un quota **à quelle vitesse** on
peut demander. Les deux sont indépendants : un client peut avoir de l'argent
et pas le droit de lancer trente runs dans la minute.

Deux notions distinctes, malgré leur ressemblance :

- ``Quotas`` appartient au **client**, et vaut quel que soit le chemin
  emprunté — API Python, REST, MCP, ligne de commande. C'est une règle
  métier : ce que cet artisan a payé le droit de faire.
- ``RateLimit`` appartient à une **clé d'API**, et n'a de sens qu'en HTTP :
  c'est la protection du serveur contre un appelant qui s'emballe, et elle
  se répond par un 429 et un ``Retry-After``.
"""

from pydantic import PositiveInt

from loom_ia.core.model.base import DomainModel

# Fenêtre des deux limites, en secondes : elles se disent « par minute ».
WINDOW: float = 60.0


class Quotas(DomainModel):
    """Débit accordé à un client (L3) ; ``None`` : pas de limite."""

    # Runs qu'un client peut lancer par minute, tous accès confondus. Un
    # sous-run n'en est pas un : il est lancé par son parent, pas par le client.
    runs_per_minute: PositiveInt | None = None

    @property
    def limited(self) -> bool:
        return self.runs_per_minute is not None


class RateLimit(DomainModel):
    """Débit d'une clé d'API, vérifié par l'accès HTTP (#39)."""

    per_minute: PositiveInt
