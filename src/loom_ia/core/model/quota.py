# SPDX-License-Identifier: Apache-2.0
"""Débit : quotas d'un client, limitation d'une clé d'API (fonction L3, point 39, conception §17.8).

Un budget dit **combien** on peut dépenser, un quota **à quelle vitesse** on
peut demander. Les deux sont indépendants : un client peut avoir de l'argent
et pas le droit de lancer trente runs dans la minute.

Deux notions distinctes, malgré leur ressemblance :

- ``Quotas`` appartient au **client**, et vaut quel que soit le chemin
  emprunté — API Python, REST, MCP — **dans une même instance** ``Loom`` :
  sa fenêtre est tenue en mémoire, si bien qu'une commande ``loom run``
  (un process par run) ne l'atteint jamais, et que N process (workers,
  ``loom serve``) accordent chacun le quota entier. C'est une règle métier :
  ce que cet artisan a payé le droit de faire.
- ``RateLimit`` appartient à une **clé d'API**, et n'a de sens qu'en HTTP :
  c'est la protection du serveur contre un appelant qui s'emballe, et elle
  se répond par un 429 et un ``Retry-After``.
"""

from pydantic import PositiveInt

from loom_ia.core.model.base import DomainModel

WINDOW: float = 60.0
"""Fenêtre des deux limites, en secondes : elles se disent « par minute »."""


class Quotas(DomainModel):
    """Débit accordé à un client (fonction L3), déclaré sous ``tenants[].quotas``.

    Un run refusé lève ``QuotaExceeded`` ; l'API REST le rend en 429 avec
    ``Retry-After``.

    Attributes:
        runs_per_minute: Runs qu'un client peut lancer par minute, tous accès
            confondus, par instance ``Loom`` (fenêtre en mémoire, voir le
            module). Un sous-run n'en est pas un : il est lancé par son
            parent, pas par le client. ``None`` (défaut) : pas de limite.
    """

    runs_per_minute: PositiveInt | None = None

    @property
    def limited(self) -> bool:
        """Vrai si une limite est posée."""
        return self.runs_per_minute is not None


class RateLimit(DomainModel):
    """Débit d'une clé d'API, vérifié par l'accès HTTP (point 39) ; au-delà, 429 et ``Retry-After``.

    Se pose à la création de la clé (``loom keys create --rate-limit``).

    Attributes:
        per_minute: Requêtes permises par minute à cette clé.
    """

    per_minute: PositiveInt
