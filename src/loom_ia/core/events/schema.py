# SPDX-License-Identifier: Apache-2.0
"""JSON Schema des événements : contrat de l'interface de suivi (K2)."""

from typing import Any

from pydantic import TypeAdapter

from loom_ia.core.events.envelope import Event


def event_json_schema() -> dict[str, Any]:
    """Schéma JSON d'un événement écrit, tel qu'il est sérialisé."""
    return TypeAdapter(Event).json_schema(mode="serialization")
