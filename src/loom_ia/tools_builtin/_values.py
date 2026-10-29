# SPDX-License-Identifier: Apache-2.0
"""Valeurs typées : le vocabulaire des colonnes, partagé par ``excel``, ``word`` et ``pdf``.

Le modèle dit ce qu'est une valeur — un montant en euros, une date, un
pourcentage — et l'outil décide de son apparence. Ce module ne fait que la
conversion, tolérante aux exports français (``1 250,50``, ``02/10/2026``,
``15%``, ``oui``) ; une date-heure qui porte un fuseau passe dans celui de
l'outil, sans fuseau, car ni Excel ni un document n'en affichent.

Module léger — la bibliothèque standard seulement — : un outil l'importe sans
tirer les dépendances d'un autre.
"""

import datetime as dt
import re
from typing import Literal, Protocol
from zoneinfo import ZoneInfo

type ColumnType = Literal[
    "texte",
    "entier",
    "decimal",
    "montant_eur",
    "montant_keur",
    "pourcentage",
    "date",
    "date_heure",
    "annee",
    "booleen",
]
type Aggregate = Literal["somme", "moyenne", "nombre", "min", "max"]
type Scalar = str | int | float | bool | None

# Ce qu'une cellule reçoit : valeur convertie selon son type de colonne.
type Stored = str | int | float | dt.date | dt.datetime | None


class Reports(Protocol):
    """Où dire une faute ou un avertissement : les ``Problems`` de chaque outil."""

    def error(self, said: str) -> None: ...

    def warn(self, said: str) -> None: ...


def convert(
    value: Scalar, kind: ColumnType, where: str, problems: Reports, zone: ZoneInfo
) -> Stored:
    """La valeur à écrire pour une colonne de ce type ; ``None`` et une erreur si impossible.

    ``zone`` : le fuseau où passe une date-heure qui en porte un.
    """
    if value is None or value == "":
        return None
    try:
        return _convert(value, kind, where, problems, zone)
    except ValueError, OverflowError:
        problems.error(f"{where} : {value!r} n'est pas un(e) {kind.replace('_', ' ')}")
        return None


def _convert(
    value: Scalar, kind: ColumnType, where: str, problems: Reports, zone: ZoneInfo
) -> Stored:
    match kind:
        case "texte":
            return str(value)
        case "annee":
            year = str(value).strip()
            if isinstance(value, float) and value.is_integer():
                year = str(int(value))
            if not re.fullmatch(r"\d{4}", year):
                raise ValueError(year)
            return year
        case "booleen":
            return "Oui" if _boolean(value) else "Non"
        case "entier":
            number = _number(value)
            if not float(number).is_integer():
                raise ValueError(value)
            return int(number)
        case "decimal" | "montant_eur" | "montant_keur":
            return _number(value)
        case "pourcentage":
            number = _number(value, percent=True)
            if abs(number) > 1:
                problems.warn(
                    f"{where} : {value!r} affiché {number:.0%} — un pourcentage se donne en "
                    "fraction (0.15 pour 15 %)"
                )
            return number
        case "date":
            return _date(value)
        case "date_heure":
            return _datetime(value, zone)


def _boolean(value: Scalar) -> bool:
    if isinstance(value, bool):
        return value
    said = str(value).strip().lower()
    if said in {"oui", "o", "vrai", "true", "1", "yes", "y"}:
        return True
    if said in {"non", "n", "faux", "false", "0", "no"}:
        return False
    raise ValueError(value)


def _number(value: Scalar, *, percent: bool = False) -> float | int:
    if isinstance(value, bool):
        raise ValueError(value)
    if isinstance(value, int | float):
        return value
    said = str(value).strip()
    for space in (" ", "\u00a0", "\u202f"):
        said = said.replace(space, "")
    said = said.removesuffix("k€").removesuffix("€")
    divide = 1
    if percent and said.endswith("%"):
        said, divide = said[:-1], 100
    if "," in said:
        # Français : virgule décimale, point des milliers éventuel.
        said = said.replace(".", "").replace(",", ".")
    number = float(said)
    if number != number or number in (float("inf"), float("-inf")):
        raise ValueError(value)
    return number / divide if divide != 1 else (int(number) if said.isdigit() else number)


def _date(value: Scalar) -> dt.date:
    said = str(value).strip()
    french = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", said)
    if french:
        day, month, year = (int(part) for part in french.groups())
        return dt.date(year, month, day)
    return dt.date.fromisoformat(said[:10])


def _datetime(value: Scalar, zone: ZoneInfo) -> dt.datetime:
    said = str(value).strip()
    french = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})[ T](\d{1,2}):(\d{2})(?::(\d{2}))?", said)
    if french:
        day, month, year, hour, minute = (int(part) for part in french.groups()[:5])
        return dt.datetime(year, month, day, hour, minute, int(french.group(6) or 0))
    moment = dt.datetime.fromisoformat(said)
    if moment.tzinfo is not None:
        # Excel ne connaît pas les fuseaux : heure locale, sans fuseau.
        moment = moment.astimezone(zone).replace(tzinfo=None)
    return moment
