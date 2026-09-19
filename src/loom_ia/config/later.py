# SPDX-License-Identifier: Apache-2.0
"""Clés du fichier racine prévues pour plus tard (§17).

Le schéma complet de la config est plus large que le sous-ensemble réalisé :
ces clés sont refusées en nommant le jalon qui les apportera.
"""

from typing import Final

LATER_ROOT: Final[dict[str, str]] = {
    "sessions": "J4 (sessions et compaction)",
    "budgets": "J3 (coûts et budgets)",
    "tenants": "J5 (multi-clients)",
    "profile": "J5 (profils dev et prod)",
    "profiles": "J5 (profils dev et prod)",
}
LATER_API_KEY: Final[dict[str, str]] = {
    "tenant": "J5 (multi-clients)",
    "rate_limit": "J5 (quotas)",
    "expires": "J5 (rotation des clés)",
}
LATER_SERVER: Final[dict[str, str]] = {
    "mcp": "J5 (serveur MCP en HTTP)",
}
LATER_STORAGE: Final[dict[str, str]] = {
    "artifacts": "J2.3 (artefacts)",
    "idempotency": "J4.4 (idempotence)",
    "bus": "J4 (bus et observabilité)",
    "queue": "J4 (arrière-plan)",
    "encryption": "J5 (chiffrement par client)",
    "retention": "J5 (rétention)",
}
LATER_TELEMETRY: Final[dict[str, str]] = {
    "capture": "J4 (niveaux de capture)",
    "redaction": "J4 (masquage)",
    "exporters": "J4 (exports)",
    "bus": "J4 (bus)",
}
