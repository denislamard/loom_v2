# SPDX-License-Identifier: Apache-2.0
"""Clés du fichier racine prévues pour plus tard (§17).

Le schéma complet de la config est plus large que le sous-ensemble réalisé :
ces clés sont refusées en nommant le jalon qui les apportera.
"""

from typing import Final

LATER_ROOT: Final[dict[str, str]] = {
    "profile": "J5.5 (profils dev et prod)",
    "profiles": "J5.5 (profils dev et prod)",
}
LATER_API_KEY: Final[dict[str, str]] = {
    "rate_limit": "J5.1b (quotas et débit)",
    "expires": "J5.2 (rotation des clés)",
}
# Surcharges d'un client prévues pour la phase suivante (§17.8).
LATER_TENANT: Final[dict[str, str]] = {
    "budgets": "J5.1b (budgets par client et par période)",
    "quotas": "J5.1b (quotas et débit)",
}
LATER_MCP_ACCESS: Final[dict[str, str]] = {
    "http": "J5.2 (serveur MCP en HTTP)",
    "allowed_origins": "J5.2 (serveur MCP en HTTP)",
}

LATER_STORAGE: Final[dict[str, str]] = {
    "bus": "J4 (bus et observabilité)",
    "queue": "J4 (arrière-plan)",
    "encryption": "J5.5 (chiffrement par client)",
    "retention": "J5.5 (rétention)",
}
LATER_TELEMETRY: Final[dict[str, str]] = {
    "capture": "J4 (niveaux de capture)",
    "redaction": "J4 (masquage)",
    "exporters": "J4 (exports)",
    "bus": "J4 (bus)",
}
