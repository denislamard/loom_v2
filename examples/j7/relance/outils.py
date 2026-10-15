# SPDX-License-Identifier: Apache-2.0
"""Outil de l'exemple du state de session, chargé par `imports` dans loom.yaml.

``chercher_devis`` rend le devis entier et ne sait rien du state : ce sont les
sources ``from`` de l'agent (``agents/relance.yaml``) qui tirent de son
résultat le numéro et le client, et les posent dans le state de la session.
L'outil n'a pas à changer.
"""

from loom_ia.core.ports import ToolError
from loom_ia.tools import tool

type Devis = dict[str, str | float]

CARNET: dict[str, Devis] = {
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
def chercher_devis(numero: str) -> Devis:
    """Renvoie un devis par son numéro (format D-AAAA-NNN, par exemple D-2026-042)."""
    devis = CARNET.get(numero)
    if devis is None:
        raise ToolError(f"Aucun devis {numero!r}. Devis connus : {', '.join(CARNET)}.")
    return devis
