# SPDX-License-Identifier: Apache-2.0
"""Identifiants du domaine.

Les identifiants générés par loom-ia sont des UUIDv7 : triés dans l'ordre
chronologique, ils servent aussi de curseur de pagination (points 22 et 43).

Les types ``RunId``, ``SessionId``, ``TenantId``, ``SpanId`` et ``EventId``
sont des ``str`` nommés (``NewType``) : ils ne coûtent rien à l'exécution et
évitent de passer un identifiant de session là où on attend un run.
"""

import re
import uuid
from typing import Final, NewType

RunId = NewType("RunId", str)
SessionId = NewType("SessionId", str)
TenantId = NewType("TenantId", str)
SpanId = NewType("SpanId", str)
EventId = NewType("EventId", str)

DEFAULT_TENANT: Final = TenantId("default")
"""Client implicite du mode librairie (point 33), quand la config ne déclare pas de clients."""

SAFE_ID: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
"""Forme d'un identifiant qui peut servir de nom de fichier.

C'est ce que le journal JSONL accepte pour un client ou une session (pas de
chemin, pas de « .. ») : une lettre ou un chiffre, puis jusqu'à 127 lettres,
chiffres, points, tirets ou soulignés. Un déclencheur l'exige aussi d'un
appelant pour en tirer une session.
"""


def new_id() -> str:
    """Identifiant unique, triable dans le temps : un UUIDv7 en minuscules, avec ses tirets."""
    return str(uuid.uuid7())


def new_run_id() -> RunId:
    """Nouvel identifiant de run (UUIDv7)."""
    return RunId(new_id())


def new_span_id() -> SpanId:
    """Nouvel identifiant de span (UUIDv7)."""
    return SpanId(new_id())


def new_event_id() -> EventId:
    """Nouvel identifiant d'événement (UUIDv7), posé à la création du brouillon."""
    return EventId(new_id())
