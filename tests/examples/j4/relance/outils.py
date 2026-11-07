# SPDX-License-Identifier: Apache-2.0
"""Outils de l'exemple de relance, chargés par `imports` dans loom.yaml."""

from loom_ia.core.ports import ToolError
from loom_ia.tools import tool

DEVIS: dict[str, dict[str, str | float]] = {
    "D-2026-042": {
        "numero": "D-2026-042",
        "entreprise": "Plomberie Dupont",
        "client": "Mme Martin",
        "objet": "Remplacement d'un chauffe-eau de 200 L",
        "montant_ttc": 1840.0,
        "envoye_le": "2026-09-02",
        "statut": "en attente",
    },
}


@tool
def chercher_devis(numero: str) -> dict[str, str | float]:
    """Renvoie un devis par son numéro (format D-AAAA-NNN, par exemple D-2026-042)."""
    devis = DEVIS.get(numero)
    if devis is None:
        raise ToolError(f"Aucun devis {numero!r}. Devis connus : {', '.join(DEVIS)}.")
    return devis
