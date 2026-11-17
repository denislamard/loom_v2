# SPDX-License-Identifier: Apache-2.0
"""Le style des graphiques, commun à ``word``, ``pdf`` et ``excel``.

Un graphique a le même aspect dans les trois formats : même palette, mêmes
formats de nombres, des étiquettes de valeur aux mêmes conditions, le même
bout d'axe. Chaque format l'applique avec ses moyens — le XML DrawingML de
Word, ``reportlab.graphics`` dans le PDF, ``openpyxl.chart`` dans un classeur.

Les couleurs sont celles du style de base (``styles/default.yaml``, réglage ``chart``) :
``word`` prend celles de son style, ``pdf`` et ``excel`` celles de ``default.yaml``.

Module léger — Pydantic et PyYAML, par ``_style_params`` — : un outil l'importe sans
tirer les dépendances d'un autre.
"""

import math
from collections.abc import Sequence
from typing import Final

from loom_ia.tools_builtin._style_params import default_style
from loom_ia.tools_builtin._values import ColumnType

_HOUSE: Final = default_style().chart
PALETTE: Final = tuple(_HOUSE.series if _HOUSE is not None and _HOUSE.series else ())
"""La palette de default.yaml : les accents du thème Office 2023, puis les mêmes plus sombres."""
GRID: Final = _HOUSE.grid_color if _HOUSE is not None and _HOUSE.grid_color else ""
"""Couleur du quadrillage des graphiques, tirée de default.yaml ; vide sans réglage."""
TEXT: Final = _HOUSE.text_color if _HOUSE is not None and _HOUSE.text_color else ""
"""Couleur des textes des graphiques, tirée de default.yaml ; vide sans réglage."""
LABELLED_BARS: Final = 12
"""Au-delà, des étiquettes de valeur sur chaque barre se chevauchent."""
LABEL_ROOM: Final = {"bar": 1.1, "horizontal_bar": 1.22}
"""La place laissée au bout de la plus grande barre pour son étiquette."""

LABEL_FORMATS: Final[dict[ColumnType, str]] = {
    "integer": "#,##0",
    "decimal": "#,##0.00",
    "amount_eur": '#,##0.00 "€"',
    "amount_keur": '#,##0 "k€"',
    "percent": "0.0%",
}
"""Formats des nombres de Word et d'Excel, en syntaxe neutre : le logiciel
applique les séparateurs de la langue du lecteur.
"""
AXIS_FORMATS: Final[dict[ColumnType, str]] = {
    "integer": "#,##0",
    "decimal": "#,##0.##",
    "amount_eur": '#,##0 "€"',
    "amount_keur": '#,##0 "k€"',
    "percent": "0%",
}
"""Formats des nombres d'un axe, par type de colonne : moins de décimales que les étiquettes."""


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
