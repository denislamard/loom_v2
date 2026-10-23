# SPDX-License-Identifier: Apache-2.0
"""Erreurs de configuration, lisibles au démarrage (M1).

Le message nomme le fichier et l'endroit dans le YAML :
``agents/demo.yaml: main.model — modèle 'X' non déclaré``. Quand la
validation trouve plusieurs erreurs, elles viennent une par ligne, et seule la
première porte le nom du fichier.
"""

from collections.abc import Sequence
from pathlib import Path
from typing import cast

from pydantic import ValidationError


class ConfigError(Exception):
    """Configuration inutilisable : le run ne démarre pas."""

    def __init__(self, message: str, *, source: Path | None = None) -> None:
        super().__init__(f"{source}: {message}" if source else message)
        self.reason = message
        self.source = source


def from_validation(
    error: ValidationError,
    *,
    source: Path | None = None,
    root: str | None = "(racine)",
    data: object = None,
) -> ConfigError:
    """Traduit les erreurs Pydantic en une erreur lisible.

    ``root`` nomme l'emplacement d'une erreur sans chemin (un contrôle de tout
    l'objet) ; ``None`` la laisse sans préfixe — pour un agent rapporté à son
    fichier, dont le message nomme déjà l'agent. ``data`` : le YAML validé ; un
    élément de liste y est nommé par son ``id`` ou son ``name`` (``where``).
    """
    lines: list[str] = []
    for item in error.errors(include_url=False):
        location = where(item["loc"], data) or root
        message = item["msg"].removeprefix("Value error, ")
        lines.append(f"{location} — {message}" if location else message)
    return ConfigError("\n".join(lines), source=source)


def where(loc: Sequence[int | str], data: object = None) -> str:
    """L'endroit d'une erreur, un élément de liste nommé par son ``id`` ou son ``name``.

    Au test M1 du 21/10, « models.0.max_token » obligeait à compter les modèles :
    « models[HAIKU].max_token » se retrouve dans le fichier. Sans nom (ou sans
    ``data``), l'indice reste.
    """
    said = ""
    current = data
    for part in loc:
        name: object = None
        if isinstance(part, int) and isinstance(current, list):
            items = cast("list[object]", current)
            current = items[part] if 0 <= part < len(items) else None
            if isinstance(current, dict):
                fields = cast("dict[str, object]", current)
                name = fields.get("id", fields.get("name"))
        else:
            current = (
                cast("dict[str, object]", current).get(str(part))
                if isinstance(current, dict)
                else None
            )
        if isinstance(part, int):
            said += f"[{name}]" if isinstance(name, str) and name else f".{part}"
        else:
            said += f".{part}"
    return said.removeprefix(".")
