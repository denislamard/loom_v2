# SPDX-License-Identifier: Apache-2.0
"""Un événement du journal, tel qu'il part vers la console : le format des événements du REST (décision D-11).

C'est la couche qui isole le contrat du format interne : aujourd'hui elle rend
exactement ce que sert ``GET /v1/events`` ; le jour où le format interne
changera, c'est ici qu'il sera ramené au contrat, et la console ne verra rien.

Deux modes (``console.export.mode``, décision D-5) :

- ``on_demand`` : l'événement privé de ce que sa charge déclare comme contenu
  (``content_fields``), qui porte ``redacted``, la liste des chemins retirés —
  ce que rend le REST à un appelant sans ``read_content`` ;
- ``push`` : l'événement entier, son contenu masqué par les motifs de
  ``telemetry.redaction``. Une charge qui ne doit jamais sortir son contenu
  (``export_content`` faux : les échanges bruts avec un fournisseur) part
  comme en ``on_demand``.

Le journal n'est jamais modifié : tout se fait sur une copie sérialisée.
"""

from typing import Any, cast

from pydantic import JsonValue

from loom_ia.config.console import ExportMode
from loom_ia.core.events import Event
from loom_ia.core.events.redaction import redacted
from loom_ia.telemetry.redaction import Redactor


def public_event(event: Event, *, mode: ExportMode, redactor: Redactor | None = None) -> dict[str, JsonValue]:
    """L'événement au format du contrat.

    Args:
        event: L'événement écrit.
        mode: ``on_demand`` (sans contenu) ou ``push`` (avec, masqué).
        redactor: Le masqueur du contenu, en ``push``.

    Returns:
        L'événement sérialisé en JSON.
    """
    if mode == "on_demand" or not event.payload.export_content:
        return redacted(event)
    dumped = event.model_dump(mode="json")
    if redactor is not None:
        charge = dumped.get("payload")
        for path in event.payload.content_fields:
            _mask(charge, path, redactor)
    return dumped


def _mask(where: object, path: str, redactor: Redactor) -> None:
    """Masque en place ce que désigne le chemin (grammaire des ``content_fields`` : ``a``, ``a.b``, ``a[].b``)."""
    if not isinstance(where, dict):
        return
    table = cast("dict[str, Any]", where)
    head, _, rest = path.partition(".")
    if not rest:
        if head in table:
            table[head] = redactor.json(cast("JsonValue", table[head]))
        return
    if head.endswith("[]"):
        items = table.get(head[:-2])
        if isinstance(items, list):
            for item in cast("list[object]", items):
                _mask(item, rest, redactor)
        return
    _mask(table.get(head), rest, redactor)
