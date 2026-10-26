# SPDX-License-Identifier: Apache-2.0
"""Formules et noms d'onglets : ce qu'Excel et LibreOffice évalueront, vérifié sans eux.

Fonctions anglaises d'une liste blanche, séparateur « , », onglets cités qui
existent et bien quotés, références dans les bornes de la feuille. Les
fonctions postérieures à Excel 2007 reçoivent le préfixe ``_xlfn.``.
"""

import re
from collections.abc import Mapping
from typing import Final

from loom_ia.tools_builtin.excel.arguments import (
    MAX_SHEET_COLUMNS,
    MAX_SHEET_ROWS,
    Aggregate,
    Problems,
)

# --- Formules --------------------------------------------------------------------------

# Fonctions permises : elles s'évaluent dans Excel 2010+ et dans LibreOffice.
ALLOWED_FUNCTIONS: Final = frozenset(
    {
        "SUM", "SUMIF", "SUMIFS", "AVERAGE", "AVERAGEIF", "AVERAGEIFS", "COUNT", "COUNTA",
        "COUNTBLANK", "COUNTIF", "COUNTIFS", "MIN", "MAX", "ROUND", "ROUNDUP", "ROUNDDOWN",
        "ABS", "IF", "IFERROR", "AND", "OR", "NOT", "INDEX", "MATCH", "SUMPRODUCT", "TODAY",
        "DATE", "YEAR", "MONTH", "DAY", "EDATE", "EOMONTH", "TEXT", "LEFT", "RIGHT", "MID",
        "LEN", "TRIM", "UPPER", "LOWER", "CONCATENATE", "VLOOKUP",
    }
)  # fmt: skip
# Postérieures à Excel 2007 : le fichier les nomme avec le préfixe _xlfn., ajouté ici.
PREFIXED_FUNCTIONS: Final = frozenset({"TEXTJOIN", "CONCAT", "IFS", "SWITCH", "MAXIFS", "MINIFS"})
# Fonctions à débordement : openpyxl n'écrit pas leurs métadonnées.
SPILL_FUNCTIONS: Final = frozenset({"XLOOKUP", "XMATCH", "FILTER", "SORT", "SORTBY", "UNIQUE",
                                    "SEQUENCE", "RANDARRAY"})  # fmt: skip
FRENCH_FUNCTIONS: Final[Mapping[str, str]] = {
    "SOMME": "SUM", "SOMME.SI": "SUMIF", "SOMME.SI.ENS": "SUMIFS", "MOYENNE": "AVERAGE",
    "NB": "COUNT", "NBVAL": "COUNTA", "NB.SI": "COUNTIF", "NB.SI.ENS": "COUNTIFS",
    "SI": "IF", "SIERREUR": "IFERROR", "ET": "AND", "OU": "OR", "NON": "NOT",
    "RECHERCHEV": "VLOOKUP", "EQUIV": "MATCH", "ARRONDI": "ROUND", "AUJOURDHUI": "TODAY",
    "ANNEE": "YEAR", "MOIS": "MONTH", "JOUR": "DAY", "TEXTE": "TEXT", "GAUCHE": "LEFT",
    "DROITE": "RIGHT", "STXT": "MID", "NBCAR": "LEN", "SOMMEPROD": "SUMPRODUCT",
    "CONCATENER": "CONCATENATE", "JOINDRE.TEXTE": "TEXTJOIN", "MAX.SI.ENS": "MAXIFS",
    "MIN.SI.ENS": "MINIFS",
}  # fmt: skip

AGGREGATES: Final[Mapping[Aggregate, str]] = {
    "somme": "SUM",
    "moyenne": "AVERAGE",
    "nombre": "COUNT",
    "min": "MIN",
    "max": "MAX",
}
_STRING: Final = re.compile(r'"(?:[^"]|"")*"')
_QUOTED_SHEET: Final = re.compile(r"'((?:[^']|'')+)'!")
_BARE_SHEET: Final = re.compile(r"(?<![\w.'])([^\W\d][\w.]*)!", re.UNICODE)
_FUNCTION: Final = re.compile(r"(?<![\w.])([A-Za-z_][A-Za-z0-9_.]*)\s*\(")
_CELL: Final = re.compile(r"(?<![\w.])\$?([A-Za-z]{1,3})\$?(\d+)(?![\w(])")
_NUMBER: Final = re.compile(r"(?<![\w.$:])(\d+(?:\.\d+)?)(?![\w.:])")
_SHEET_FORBIDDEN: Final = re.compile(r"[\[\]:*?/\\]")


def sheet_name_problem(name: str) -> str | None:
    """Pourquoi Excel refuserait ce nom d'onglet, ou ``None``."""
    if not name.strip():
        return "nom d'onglet vide"
    if len(name) > 31:
        return f"onglet {name!r} : 31 caractères au plus ({len(name)})"
    if _SHEET_FORBIDDEN.search(name):
        return f"onglet {name!r} : les caractères [ ] : * ? / \\ sont interdits"
    if name.startswith("'") or name.endswith("'"):
        return f"onglet {name!r} : ne peut ni commencer ni finir par une apostrophe"
    return None


def quoted(name: str) -> str:
    """Le nom d'onglet tel qu'une formule doit l'écrire."""
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*", name) and not _CELL.fullmatch(name):
        return name
    return "'" + name.replace("'", "''") + "'"


def check_formula(formula: str, where: str, sheets: Mapping[str, str], problems: Problems) -> str:
    """La formule telle qu'écrite dans le fichier (préfixes _xlfn. ajoutés), ou une erreur.

    ``sheets`` : nom en minuscules → nom exact, des onglets du classeur.
    """
    body = formula[1:]
    bare = _STRING.sub('""', body)
    if bare.count('"') % 2:
        problems.error(f"{where} : guillemet non fermé dans {formula!r}")
        return formula
    if ";" in bare:
        problems.error(
            f"{where} : séparateur « ; » dans {formula!r} — les formules s'écrivent en anglais, "
            "arguments séparés par « , »"
        )
    depth = 0
    for char in bare:
        depth += (char == "(") - (char == ")")
        if depth < 0:
            break
    if depth:
        problems.error(f"{where} : parenthèses déséquilibrées dans {formula!r}")
    for match in _QUOTED_SHEET.finditer(bare):
        name = match.group(1).replace("''", "'")
        if name.lower() not in sheets:
            problems.error(f"{where} : onglet inconnu {name!r} dans {formula!r}")
    unquoted = _QUOTED_SHEET.sub("", bare)
    for match in _BARE_SHEET.finditer(unquoted):
        name = match.group(1)
        exact = sheets.get(name.lower())
        if exact is None:
            known = ", ".join(quoted(s) for s in sheets.values())
            problems.error(
                f"{where} : onglet inconnu {name!r} dans {formula!r} (onglets : {known}) — un "
                "nom avec espace ou accent s'écrit entre apostrophes : 'Mes Hypothèses'!B2"
            )
        elif quoted(exact) != exact:
            problems.error(f"{where} : écrire {quoted(exact)}! et non {name}! dans {formula!r}")
    for match in _FUNCTION.finditer(unquoted):
        name = match.group(1).upper().removeprefix("_XLFN.")
        if name in FRENCH_FUNCTIONS:
            problems.error(
                f"{where} : fonction française {match.group(1)} dans {formula!r} — écrire "
                f"{FRENCH_FUNCTIONS[name]}"
            )
        elif name in SPILL_FUNCTIONS:
            problems.error(
                f"{where} : {name} n'est pas permise (fonction à débordement) — trier, filtrer "
                "ou dédoublonner les données avant, et INDEX/MATCH pour une recherche"
            )
        elif name not in ALLOWED_FUNCTIONS and name not in PREFIXED_FUNCTIONS:
            problems.error(f"{where} : fonction {match.group(1)} non permise dans {formula!r}")
    no_sheets = _BARE_SHEET.sub("", unquoted)
    for match in _CELL.finditer(no_sheets):
        column = _column_index(match.group(1))
        row = int(match.group(2))
        if column > MAX_SHEET_COLUMNS or not 1 <= row <= MAX_SHEET_ROWS:
            problems.error(f"{where} : référence {match.group(0)} hors de la feuille")
    constants = [
        number
        for number in _NUMBER.findall(_CELL.sub("", _FUNCTION.sub("(", no_sheets)))
        # Un chiffre seul (ROUND(x,2), un indice de colonne) n'est pas une hypothèse cachée.
        if len(number) > 1 and number != "100"
    ]
    if constants:
        problems.warn(
            f"{where} : constante {constants[0]} dans {formula!r} — une hypothèse se met dans "
            "sa propre cellule, citée par la formule"
        )
    return _prefixed(formula)


def _prefixed(formula: str) -> str:
    def add(match: re.Match[str]) -> str:
        name = match.group(1)
        if name.upper() in PREFIXED_FUNCTIONS:
            return f"_xlfn.{name.upper()}("
        return match.group(0)

    # Hors des chaînes : découpe sur les littéraux, préfixe le reste.
    parts: list[str] = []
    last = 0
    for literal in _STRING.finditer(formula):
        parts.append(_FUNCTION.sub(add, formula[last : literal.start()]))
        parts.append(literal.group(0))
        last = literal.end()
    parts.append(_FUNCTION.sub(add, formula[last:]))
    return "".join(parts)


def _column_index(letters: str) -> int:
    index = 0
    for char in letters.upper():
        index = index * 26 + ord(char) - 64
    return index
