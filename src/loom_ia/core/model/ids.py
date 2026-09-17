# SPDX-License-Identifier: Apache-2.0
"""Identifiants du domaine.

Les identifiants générés par loom-ia sont des UUIDv7 : triés dans l'ordre
chronologique, ils servent aussi de curseur de pagination (#22, #43).
"""

import uuid
from typing import Final, NewType

RunId = NewType("RunId", str)
SessionId = NewType("SessionId", str)
TenantId = NewType("TenantId", str)
SpanId = NewType("SpanId", str)
EventId = NewType("EventId", str)

# Client implicite du mode librairie (#33).
DEFAULT_TENANT: Final = TenantId("default")


def new_id() -> str:
    """Identifiant unique, triable dans le temps (UUIDv7)."""
    return str(uuid.uuid7())


def new_run_id() -> RunId:
    return RunId(new_id())


def new_span_id() -> SpanId:
    return SpanId(new_id())


def new_event_id() -> EventId:
    return EventId(new_id())
