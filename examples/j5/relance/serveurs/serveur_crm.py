# SPDX-License-Identifier: Apache-2.0
"""Serveur MCP « crm » de l'exemple J5 : le carnet d'adresses d'un artisan.

Le serveur ne sert **qu'un** artisan : celui dont le jeton ouvre la
connexion. Il le lit dans ``CRM_TOKEN``, que loom-ia lui passe au lancement
(``env_from``) après l'avoir résolu dans les secrets du client — c'est tout
l'intérêt de la portée ``scope: tenant`` (#34) : une connexion par client,
ouverte avec ses identifiants à lui.

Un jeton inconnu donne un serveur qui ne sait rien : il démarre, il répond,
et il n'a aucun client. C'est ce qu'on veut d'un secret manquant — pas les
données du voisin.
"""

import os
from typing import Final

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

CARNETS: Final[dict[str, dict[str, dict[str, str]]]] = {
    "jeton-dupont": {
        "D-2026-042": {
            "client": "Mme Martin",
            "email": "mme.martin@example.com",
            "telephone": "03 80 00 00 42",
        },
    },
    "jeton-martin": {
        "D-2026-117": {
            "client": "M. Leroy",
            "email": "m.leroy@example.com",
            "telephone": "03 80 00 01 17",
        },
    },
}

JETON: Final = os.environ.get("CRM_TOKEN", "")
CARNET: Final = CARNETS.get(JETON, {})

mcp = FastMCP("crm", log_level="WARNING")


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def coordonnees(numero: str) -> dict[str, str]:
    """Coordonnées du client d'un devis (format D-AAAA-NNN)."""
    fiche = CARNET.get(numero)
    if fiche is None:
        connus = ", ".join(CARNET) or "aucun"
        raise ValueError(f"Aucune fiche pour {numero!r} dans ce carnet. Devis connus : {connus}.")
    return fiche


if __name__ == "__main__":
    mcp.run()
