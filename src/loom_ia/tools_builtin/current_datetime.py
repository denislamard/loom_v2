# SPDX-License-Identifier: Apache-2.0
"""Outil current_datetime : la date et/ou l'heure actuelles, dans le fuseau horaire demandé."""

from datetime import datetime
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field

from loom_ia.core.ports import ToolError
from loom_ia.tools import tool

_JOURS = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")


@tool
def current_datetime(
    part: Annotated[
        Literal["date", "time", "both"],
        Field(description="Ce qu'il faut rendre : la date, l'heure, ou les deux."),
    ] = "both",
    timezone: Annotated[
        str,
        Field(description="Fuseau horaire IANA, par exemple Europe/Paris ou America/Montreal."),
    ] = "Europe/Paris",
) -> dict[str, str]:
    """Donne la date et/ou l'heure actuelles dans un fuseau horaire.

    À utiliser dès qu'une réponse dépend du jour ou de l'heure (aujourd'hui, demain,
    une échéance, un délai) : le modèle ne connaît pas la date du jour.
    """
    try:
        zone = ZoneInfo(timezone.strip())
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ToolError(
            f"Fuseau horaire inconnu : {timezone!r}. Donner un nom IANA, comme Europe/Paris."
        ) from exc
    now = datetime.now(zone)
    result = {"timezone": zone.key, "utc_offset": now.isoformat()[-6:]}
    if part in ("date", "both"):
        result["date"] = now.date().isoformat()
        result["weekday"] = _JOURS[now.weekday()]
    if part in ("time", "both"):
        result["time"] = now.strftime("%H:%M:%S")
    return result
