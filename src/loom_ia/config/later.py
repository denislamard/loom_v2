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
LATER_API_KEY: Final[dict[str, str]] = {}
# Surcharges d'un client prévues pour plus tard (§17.8).
LATER_TENANT: Final[dict[str, str]] = {}
LATER_MCP_ACCESS: Final[dict[str, str]] = {}

LATER_STORAGE: Final[dict[str, str]] = {
    "bus": "J5.3c (bus Postgres et Redis)",
    "encryption": "J5.5 (chiffrement par client)",
    "retention": "J5.5 (rétention)",
}
LATER_TELEMETRY: Final[dict[str, str]] = {
    "capture": "J4 (niveaux de capture)",
    "redaction": "J4 (masquage)",
    "exporters": "J4 (exports)",
    "bus": "J4 (bus)",
}
