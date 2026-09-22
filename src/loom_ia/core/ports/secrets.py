# SPDX-License-Identifier: Apache-2.0
"""Port des secrets (L2, M3, §16.3).

La configuration ne porte jamais un secret : seulement le **nom** de la
variable qui le porte (``api_key_env`` d'un modèle, ``env_from`` et
``headers_env`` d'un serveur MCP). Ce port rend, pour un client, la table
des secrets que ce client peut lire.

Une table plutôt qu'un appel par secret : c'est déjà ce que les adaptateurs
attendent (``Mapping[str, str]``), et l'isolation se lit d'un coup d'œil —
ce qui n'est pas dans la table n'existe pas pour ce client (#34). La table
est résolue au montage de ses agents, une fois par client.
"""

from collections.abc import Mapping
from typing import Protocol

from loom_ia.core.model.ids import TenantId


class SecretProvider(Protocol):
    """Ce qu'un client peut lire, par nom de variable."""

    def secrets(self, tenant_id: TenantId) -> Mapping[str, str]:
        """Table des secrets de ce client ; vide s'il n'en a aucun."""
        ...
