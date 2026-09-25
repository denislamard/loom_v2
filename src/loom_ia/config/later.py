# SPDX-License-Identifier: Apache-2.0
"""Clés du fichier racine prévues pour plus tard (§17).

Le schéma complet de la config est plus large que le sous-ensemble réalisé :
ces clés sont refusées en nommant le jalon qui les apportera.
"""

from typing import Final

LATER_ROOT: Final[dict[str, str]] = {}
LATER_API_KEY: Final[dict[str, str]] = {}
# Surcharges d'un client prévues pour plus tard (§17.8).
LATER_TENANT: Final[dict[str, str]] = {}
LATER_MCP_ACCESS: Final[dict[str, str]] = {}

LATER_STORAGE: Final[dict[str, str]] = {}
LATER_TELEMETRY: Final[dict[str, str]] = {
    "capture": "J4 (niveaux de capture)",
    "redaction": "J4 (masquage)",
    "exporters": "J4 (exports)",
    "bus": "J4 (bus)",
}
