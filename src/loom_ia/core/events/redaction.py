# SPDX-License-Identifier: Apache-2.0
"""Masquage du contenu d'un événement (J5.2a, §14.2).

Le journal garde tout, toujours : c'est lui qui fait foi, et un run ne se
rejoue pas sans ce que le modèle a lu et répondu. Ce qui se règle, c'est **ce
qu'on en montre** — une clé d'API qui a `read` mais pas `read_content` voit
passer les runs, leurs coûts, leurs durées et leurs verdicts, sans lire la
correspondance d'un artisan avec ses clients.

Le masquage rend un **dictionnaire**, pas un événement : un champ obligatoire
retiré ne repasserait pas la validation, et c'est voulu — un journal masqué
n'est pas un journal qu'on peut rejouer. Ce qui a été retiré est nommé à côté
(`redacted`), pour qu'un lecteur ne prenne pas une absence pour un vide.

Ce que chaque charge déclare `content_fields` (payloads.py) ; ici, on ne fait
que suivre ces chemins.
"""

from collections.abc import Sequence
from typing import Any, Final, cast

from pydantic import JsonValue

from loom_ia.core.events.envelope import Event

# Clé ajoutée à la charge masquée : ce qui a été retiré, dans l'ordre déclaré.
MARK: Final = "redacted"


def redacted(event: Event) -> dict[str, JsonValue]:
    """Événement en JSON, privé de ce que sa charge déclare comme contenu."""
    dumped: dict[str, Any] = event.model_dump(mode="json")
    payload: object = dumped.get("payload")
    if not isinstance(payload, dict):
        return dumped
    charge = cast("dict[str, Any]", payload)
    removed = [path for path in event.payload.content_fields if _remove(charge, path)]
    if removed:
        charge[MARK] = removed
    return dumped


def redacted_all(events: Sequence[Event]) -> list[dict[str, JsonValue]]:
    return [redacted(event) for event in events]


def contents(event: Event) -> dict[str, JsonValue]:
    """Le contenu d'un événement, et lui seul : ``{chemin déclaré: valeur}`` (J6.1a).

    C'est l'inverse de ``redacted`` — ce que le masquage retire, ici rendu.
    Les exports en ``content`` s'en servent pour joindre le contenu à part,
    sous son chemin, au lieu de recopier toute la charge. Un chemin dans une
    liste (``criteria[].reason``) rend la liste de ses valeurs.
    """
    charge: object = event.payload.model_dump(mode="json")
    found: dict[str, JsonValue] = {}
    for path in event.payload.content_fields:
        value = _pick(charge, path)
        if _carries(value):
            found[path] = cast("JsonValue", value)
    return found


def _pick(where: object, path: str) -> object:
    """Ce que désigne le chemin, ou l'absence ; même grammaire que ``_remove``."""
    if not isinstance(where, dict):
        return _ABSENT
    table = cast("dict[str, Any]", where)
    head, _, rest = path.partition(".")
    if not rest:
        return table.get(head, _ABSENT)
    if head.endswith("[]"):
        items = table.get(head[:-2])
        if not isinstance(items, list):
            return _ABSENT
        picked = [_pick(item, rest) for item in cast("list[object]", items)]
        return [value for value in picked if _carries(value)]
    return _pick(table.get(head), rest)


def _remove(where: object, path: str) -> bool:
    """Retire ce que désigne le chemin ; vrai si quelque chose est parti.

    Un seul cran de descente suffit à ce que portent les charges :
    ``arguments`` (ici même), ``context.metadata`` (un cran), et
    ``criteria[].reason`` (un cran, dans chaque élément d'une liste).
    """
    if not isinstance(where, dict):
        return False
    table = cast("dict[str, Any]", where)
    head, _, rest = path.partition(".")
    if not rest:
        if not _carries(table.get(head, _ABSENT)):
            # Un champ absent ou vide ne cache rien : le retirer ferait dire au
            # masquage qu'il a caché quelque chose, et laisserait croire à un
            # lecteur que le run a écrit là où il n'a rien écrit.
            return False
        del table[head]
        return True
    if head.endswith("[]"):
        items = table.get(head[:-2])
        if not isinstance(items, list):
            return False
        # Liste construite d'abord : `any` s'arrêterait au premier retrait,
        # et les éléments suivants garderaient leur contenu.
        return any([_remove(item, rest) for item in cast("list[object]", items)])
    return _remove(table.get(head), rest)


def _carries(value: object) -> bool:
    """Vrai si la valeur porte vraiment quelque chose à cacher."""
    return (
        value is not _ABSENT and value is not None and value != "" and value != [] and value != {}
    )


class _Absent:
    """Sentinelle : `None` est une valeur possible, l'absence n'en est pas une."""


_ABSENT: Final = _Absent()
