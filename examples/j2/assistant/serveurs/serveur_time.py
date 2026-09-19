# SPDX-License-Identifier: Apache-2.0
"""Serveur MCP « time » de l'exemple : date et heure, écart entre deux dates.

Lancé par loom-ia en stdio (voir ``mcp_servers`` dans loom.yaml). Ses outils
sont annotés en lecture seule : loom-ia les traite comme sans effet de bord.
"""

from datetime import date, datetime
from zoneinfo import ZoneInfo

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

LECTURE = ToolAnnotations(readOnlyHint=True)

mcp = FastMCP("time", log_level="WARNING")


@mcp.tool(annotations=LECTURE)
def maintenant(fuseau: str = "Europe/Paris") -> str:
    """Date et heure actuelles dans un fuseau horaire (par exemple Europe/Paris)."""
    return datetime.now(ZoneInfo(fuseau)).isoformat(timespec="minutes")


@mcp.tool(annotations=LECTURE)
def jours_entre(debut: str, fin: str) -> int:
    """Nombre de jours entre deux dates au format AAAA-MM-JJ."""
    return (date.fromisoformat(fin) - date.fromisoformat(debut)).days


if __name__ == "__main__":
    mcp.run()
