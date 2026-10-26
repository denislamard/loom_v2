# SPDX-License-Identifier: Apache-2.0
"""Les valeurs de la base, rendues au modèle en JSON.

- ``numeric`` (``Decimal``) en **chaîne** : un montant reste exact (« 1250.50 ») ;
- une date-heure avec fuseau passe dans le fuseau de l'outil, en ISO 8601 ;
  sans fuseau, elle est rendue telle quelle ; dates et heures en ISO 8601 ;
- une durée en ISO 8601 (« P1DT2H ») ;
- un binaire n'est jamais rendu : « <bytes: 1234> » ;
- un texte de plus de ``MAX_CELL_CHARS`` caractères est coupé, et le dit ;
- ``json`` et ``jsonb`` arrivent décodés (codec posé sur la connexion) ;
- le reste (UUID, adresses, intervalles de valeurs…) en texte.
"""

import math
from collections.abc import Mapping, Sequence
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Final, cast
from zoneinfo import ZoneInfo

from pydantic import JsonValue

MAX_CELL_CHARS: Final = 1000


def cell(value: object, zone: ZoneInfo) -> JsonValue:
    """Une valeur rendue au modèle."""
    match value:
        case None | bool() | int():
            return value
        case float():
            return value if math.isfinite(value) else str(value)
        case str():
            return _cut(value)
        case Decimal():
            return str(value)
        case datetime():
            if value.tzinfo is not None:
                return value.astimezone(zone).isoformat()
            return value.isoformat()
        case date() | time():
            return value.isoformat()
        case timedelta():
            return duration(value)
        case bytes() | bytearray():
            return f"<bytes: {len(value)}>"
        case memoryview():
            return f"<bytes: {cast(memoryview[int], value).nbytes}>"
        case Mapping():
            items = cast(Mapping[object, object], value)
            return {str(key): cell(item, zone) for key, item in items.items()}
        case list() | tuple():
            return [cell(item, zone) for item in cast(Sequence[object], value)]
        case _:
            return _cut(str(value))


def row(values: Sequence[object], zone: ZoneInfo) -> list[JsonValue]:
    return [cell(value, zone) for value in values]


def duration(value: timedelta) -> str:
    """ISO 8601 : « P1DT2H30M », « PT0.5S », « -P1D »."""
    total = value.total_seconds()
    sign = "-" if total < 0 else ""
    seconds = abs(total)
    days, rest = divmod(seconds, 86_400)
    hours, rest = divmod(rest, 3600)
    minutes, secs = divmod(rest, 60)
    out = f"{sign}P{int(days)}D" if days else f"{sign}P"
    clock = ""
    if hours:
        clock += f"{int(hours)}H"
    if minutes:
        clock += f"{int(minutes)}M"
    if secs or not (days or hours or minutes):
        clock += f"{secs:g}S"
    return out + ("T" + clock if clock else "")


def _cut(text: str) -> str:
    if len(text) <= MAX_CELL_CHARS:
        return text
    return f"{text[:MAX_CELL_CHARS]}…[+{len(text) - MAX_CELL_CHARS} caractères]"
