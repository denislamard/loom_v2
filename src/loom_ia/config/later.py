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
# ``capture``, ``redaction`` et ``exporters`` sont arrivés en 6.1a ; ``bus``
# n'est pas « pour plus tard » mais ailleurs (``storage.bus``, 5.3c) : il est
# refusé par ``TelemetryConfig`` avec un message qui le dit.
LATER_TELEMETRY: Final[dict[str, str]] = {}
