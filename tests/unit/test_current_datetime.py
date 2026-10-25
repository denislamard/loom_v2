# SPDX-License-Identifier: Apache-2.0
"""Outil fourni ``loom.current_datetime`` : la date et/ou l'heure, dans un fuseau horaire.

L'horloge est figée : ``datetime`` est remplacé, dans le module de l'outil, par une
sous-classe dont ``now`` rend un instant choisi, converti dans le fuseau demandé.
"""

from datetime import UTC, datetime, tzinfo
from typing import Final, Self

import pytest

import loom_ia.tools_builtin.current_datetime as module
from loom_ia.core.model import DEFAULT_TENANT, RunId, SessionId
from loom_ia.core.ports import ToolContext, ToolError
from loom_ia.tools_builtin.current_datetime import current_datetime

CONTEXT: Final = ToolContext(
    tenant_id=DEFAULT_TENANT,
    session_id=SessionId("s"),
    run_id=RunId("r"),
    call_id="c1",
    agent="demo",
)
# Dimanche 25 octobre 2026, 10:43:02 UTC : 11:43:02 à Paris, passée à l'heure d'hiver la nuit même.
INSTANT: Final = datetime(2026, 10, 25, 10, 43, 2, tzinfo=UTC)


@pytest.fixture(autouse=True)
def clock(monkeypatch: pytest.MonkeyPatch) -> None:
    class Fixed(datetime):
        @classmethod
        def now(cls, tz: tzinfo | None = None) -> Self:
            moment = INSTANT.astimezone(tz)
            return cls.combine(moment.date(), moment.timetz())

    monkeypatch.setattr(module, "datetime", Fixed)


def test_both_is_the_default() -> None:
    assert current_datetime() == {
        "timezone": "Europe/Paris",
        "utc_offset": "+01:00",
        "date": "2026-10-25",
        "weekday": "dimanche",
        "time": "11:43:02",
    }


def test_date_only() -> None:
    assert current_datetime(part="date") == {
        "timezone": "Europe/Paris",
        "utc_offset": "+01:00",
        "date": "2026-10-25",
        "weekday": "dimanche",
    }


def test_time_only_in_another_timezone() -> None:
    assert current_datetime(part="time", timezone="America/Montreal") == {
        "timezone": "America/Montreal",
        "utc_offset": "-04:00",
        "time": "06:43:02",
    }


def test_the_date_follows_the_timezone() -> None:
    """À 10:43 UTC, il est déjà lundi à Kiritimati (UTC+14)."""
    said = current_datetime(part="date", timezone="Pacific/Kiritimati")
    assert (said["date"], said["weekday"], said["utc_offset"]) == ("2026-10-26", "lundi", "+14:00")


def test_utc_and_surrounding_spaces() -> None:
    said = current_datetime(timezone="  UTC ")
    assert (said["timezone"], said["utc_offset"], said["time"]) == ("UTC", "+00:00", "10:43:02")


@pytest.mark.parametrize("timezone", ["Mars/Olympus", "../etc/passwd", "", "Europe/"])
def test_unknown_timezone_is_a_tool_error(timezone: str) -> None:
    with pytest.raises(ToolError, match="Fuseau horaire inconnu"):
        current_datetime(timezone=timezone)


def test_schema_seen_by_the_model() -> None:
    spec = current_datetime.spec
    assert spec.input_schema == {
        "type": "object",
        "properties": {
            "part": {
                "default": "both",
                "description": "Ce qu'il faut rendre : la date, l'heure, ou les deux.",
                "enum": ["date", "time", "both"],
                "title": "Part",
                "type": "string",
            },
            "timezone": {
                "default": "Europe/Paris",
                "description": "Fuseau horaire IANA, par exemple Europe/Paris ou America/Montreal.",
                "title": "Timezone",
                "type": "string",
            },
        },
        "additionalProperties": False,
    }
    assert (spec.side_effects, spec.approval) == ("none", "never")


async def test_an_unknown_part_is_refused_before_the_call() -> None:
    with pytest.raises(ToolError, match="part"):
        await current_datetime.invoke({"part": "week"}, CONTEXT)
