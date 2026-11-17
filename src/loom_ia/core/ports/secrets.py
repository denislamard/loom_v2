# SPDX-License-Identifier: Apache-2.0
"""Port des secrets d'un client (fonctions L2 et M3, conception §16.3).

La configuration ne porte jamais un secret : seulement le **nom** de la
variable qui le porte (``api_key_env`` d'un modèle, ``env_from`` et
``headers_env`` d'un serveur MCP). Ce port rend, pour un client, la table
des secrets que ce client peut lire.

Une table plutôt qu'un appel par secret : c'est déjà ce que les adaptateurs
attendent (``Mapping[str, str]``), et l'isolation se lit d'un coup d'œil —
ce qui n'est pas dans la table n'existe pas pour ce client (point 34). La table
est résolue au montage de ses agents, une fois par client.

Implémentation fournie : ``tenancy/secrets.py``, qui lit l'environnement et
applique les redirections ``secrets`` d'un client.
"""

from collections.abc import Mapping
from typing import Protocol

from loom_ia.core.model.ids import TenantId


class SecretProvider(Protocol):
    """Fournisseur des secrets qu'un client peut lire, par nom de variable."""

    def secrets(self, tenant_id: TenantId) -> Mapping[str, str]:
        """Table des secrets d'un client.

        Args:
            tenant_id: Le client.

        Returns:
            Nom de variable → valeur ; vide si le client n'en a aucun. Une
            variable absente de la table n'existe pas pour ce client.
        """
        ...
