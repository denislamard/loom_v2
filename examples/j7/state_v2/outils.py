# SPDX-License-Identifier: Apache-2.0
"""Outil de l'exemple du state v2, chargé par `imports` dans loom.yaml.

``chercher_clients`` rend un tableau ``{columns, rows}``, comme ``loom.sql`` :
le modèle ne peut pas y viser une valeur par un chemin fixe. C'est lui qui
désigne le résultat à ``loom.state`` (``{"set": "client", "result": "result:1"}``),
et loom en tire l'``id`` et le ``code`` d'après ``extract:`` (agents/clients.yaml).
"""

from loom_ia.tools import tool

CLIENTS: list[tuple[int, str, str, str]] = [
    (2, "C-002", "Renault", "Dijon"),
    (261, "C-261", "Renard", "Longvic"),
    (312, "C-312", "Renard", "Dijon"),
    (8, "C-008", "Dupuy", "Plombières-lès-Dijon"),
]


@tool
def chercher_clients(nom: str = "", ville: str = "") -> dict[str, object]:
    """Cherche des clients par nom ou par ville ; rend id, code, nom et ville de chacun."""
    rows = [
        list(client)
        for client in CLIENTS
        if (not nom or client[2] == nom) and (not ville or client[3] == ville)
    ]
    return {"columns": ["id", "code", "nom", "ville"], "rows": rows, "truncated": False}
