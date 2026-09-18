# SPDX-License-Identifier: Apache-2.0
"""Templates ``{{ var }}`` (#12, #50).

Moteur interne minimal, sans Jinja : une variable est un chemin pointé
(``args.devis``, ``context.user_input``, ``args.lignes.0``), sans boucle,
condition ni filtre. Au rendu, une chaîne est insérée telle quelle, toute
autre valeur en JSON, et une valeur absente donne une chaîne vide.

L'analyse est séparée du rendu : la config vérifie les variables au
chargement, et le rendu ne peut plus échouer.
"""

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final, Self, cast

from pydantic import JsonValue

_VARIABLE: Final = re.compile(r"\{\{(.*?)\}\}", re.DOTALL)
_PATH: Final = re.compile(r"\w[\w-]*(?:\.\w[\w-]*)*")

type Path = tuple[str, ...]


class TemplateError(ValueError):
    """Template mal formé."""


@dataclass(frozen=True, slots=True)
class Template:
    source: str
    # Texte littéral, ou chemin d'une variable.
    parts: tuple[str | Path, ...]

    @classmethod
    def parse(cls, source: str) -> Self:
        parts: list[str | Path] = []
        position = 0
        for match in _VARIABLE.finditer(source):
            if match.start() > position:
                parts.append(source[position : match.start()])
            expression = match.group(1).strip()
            if not _PATH.fullmatch(expression):
                raise TemplateError(
                    f"Variable invalide : {match.group(0)!r} "
                    "(attendu un chemin pointé, par exemple {{ args.devis }})"
                )
            parts.append(tuple(expression.split(".")))
            position = match.end()
        rest = source[position:]
        if "{{" in rest:
            raise TemplateError("'{{' sans '}}' correspondant")
        if rest:
            parts.append(rest)
        return cls(source=source, parts=tuple(parts))

    @property
    def variables(self) -> tuple[Path, ...]:
        """Chemins des variables, dans l'ordre du texte."""
        return tuple(part for part in self.parts if isinstance(part, tuple))

    def render(self, values: Mapping[str, JsonValue]) -> str:
        return "".join(
            part if isinstance(part, str) else render_value(lookup(values, part))
            for part in self.parts
        )


def lookup(values: Mapping[str, JsonValue], path: Path) -> JsonValue:
    """Valeur au bout du chemin, ou None si une étape manque."""
    current: object = values
    for key in path:
        if isinstance(current, Mapping):
            current = cast(Mapping[str, object], current).get(key)
        elif isinstance(current, Sequence) and not isinstance(current, str) and key.isdigit():
            items = cast(Sequence[object], current)
            index = int(key)
            current = items[index] if index < len(items) else None
        else:
            return None
    return cast(JsonValue, current)


def render_value(value: JsonValue) -> str:
    """Texte inséré pour une valeur : la chaîne telle quelle, sinon du JSON."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)
