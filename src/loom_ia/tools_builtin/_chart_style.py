# SPDX-License-Identifier: Apache-2.0
"""Le style des graphiques, commun à ``word``, ``pdf`` et ``excel``.

Un graphique a le même aspect dans les trois formats : même palette, mêmes
formats de nombres, des étiquettes de valeur aux mêmes conditions, le même
bout d'axe. Chaque format l'applique avec ses moyens — le XML DrawingML de
Word, ``reportlab.graphics`` dans le PDF, ``openpyxl.chart`` dans un classeur.

Module léger — la bibliothèque standard seulement — : un outil l'importe sans
tirer les dépendances d'un autre.
"""

import math
from collections.abc import Sequence
from typing import Final

from loom_ia.tools_builtin._values import ColumnType

# La palette de la maison : bleu nuit, puis des couleurs qui s'en distinguent bien.
PALETTE: Final = ("1F3864", "C55A11", "8EA9DB", "548235", "7F7F7F", "BF9000",
                  "2F5496", "843C0C", "A5A5A5", "4472C4", "70AD47", "FFC000")  # fmt: skip
GRID: Final = "D9D9D9"
TEXT: Final = "404040"
# Au-delà, des étiquettes de valeur sur chaque barre se chevauchent.
LABELLED_BARS: Final = 12
# La place laissée au bout de la plus grande barre pour son étiquette.
LABEL_ROOM: Final = {"barres": 1.1, "barres_horizontales": 1.22}

# Formats des nombres de Word et d'Excel, en syntaxe neutre : le logiciel applique
# les séparateurs de la langue du lecteur.
LABEL_FORMATS: Final[dict[ColumnType, str]] = {
    "entier": "#,##0",
    "decimal": "#,##0.00",
    "montant_eur": '#,##0.00 "€"',
    "montant_keur": '#,##0 "k€"',
    "pourcentage": "0.0%",
}
AXIS_FORMATS: Final[dict[ColumnType, str]] = {
    "entier": "#,##0",
    "decimal": "#,##0.##",
    "montant_eur": '#,##0 "€"',
    "montant_keur": '#,##0 "k€"',
    "pourcentage": "0%",
}


def axis_end(values: Sequence[float], room: float) -> float | None:
    """Le bout de l'axe des valeurs, quand une étiquette doit tenir au bout des barres.

    Sans lui, Word et Excel arrêtent l'axe juste après la plus grande barre, et
    son étiquette déborde du graphique. Le bout reste un nombre rond. ``None``
    quand il n'y a rien à régler : aucune valeur, une valeur négative, ou rien
    au-dessus de zéro.
    """
    if not values or min(values) < 0:
        return None
    top = max(values) * room
    if top <= 0:
        return None
    step = 10 ** math.floor(math.log10(top)) / 5
    return math.ceil(top / step) * step
