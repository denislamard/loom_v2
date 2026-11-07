# SPDX-License-Identifier: Apache-2.0
"""Outils de l'exemple J5, chargés par `imports` dans loom.yaml.

Deux artisans, deux carnets de devis. ``chercher_devis`` prend celui de son
client : un outil reçoit le contexte de l'appel (``ToolContext``), et le
``tenant_id`` y est depuis le premier jalon — c'est ce qui permet à un outil
métier de ne jamais servir la donnée d'un autre client (L1, #34).

Un vrai artisan aurait son CRM derrière un serveur MCP en ``scope: tenant``,
ouvert avec ses identifiants à lui (voir ``mcp_servers`` dans loom.yaml) ;
ici, un dictionnaire suffit à montrer la cloison.
"""

from loom_ia.core.ports import ToolContext, ToolError
from loom_ia.tools import tool

type Devis = dict[str, str | float]

CARNETS: dict[str, dict[str, Devis]] = {
    "dupont-plomberie": {
        "D-2026-042": {
            "numero": "D-2026-042",
            "entreprise": "Plomberie Dupont",
            "client": "Mme Martin",
            "objet": "Remplacement d'un chauffe-eau de 200 L",
            "montant_ttc": 1840.0,
            "envoye_le": "2026-09-02",
            "statut": "en attente",
        },
    },
    "martin-chauffage": {
        "D-2026-117": {
            "numero": "D-2026-117",
            "entreprise": "Chauffage Martin",
            "client": "M. Leroy",
            "objet": "Entretien annuel d'une chaudière gaz",
            "montant_ttc": 210.0,
            "envoye_le": "2026-09-05",
            "statut": "en attente",
        },
    },
}


@tool
def chercher_devis(numero: str, context: ToolContext) -> Devis:
    """Renvoie un devis par son numéro (format D-AAAA-NNN, par exemple D-2026-042)."""
    carnet = CARNETS.get(context.tenant_id, {})
    devis = carnet.get(numero)
    if devis is None:
        connus = ", ".join(carnet) or "aucun"
        raise ToolError(f"Aucun devis {numero!r} chez ce client. Devis connus : {connus}.")
    return devis
