# SPDX-License-Identifier: Apache-2.0
"""Templates ``{{ var }}`` des prompts et des entrées de rôles (points 12 et 50, fonction M7).

Moteur interne minimal, sans Jinja : une variable est un chemin pointé
(``args.devis``, ``context.user_input``, ``args.lignes.0``), sans boucle,
condition ni filtre. Un segment numérique indexe une liste. Au rendu, une
chaîne est insérée telle quelle, toute autre valeur en JSON (sans échappement
ASCII), et une valeur absente donne une chaîne vide.

L'analyse est séparée du rendu : la config analyse chaque template au
chargement (``Template.parse``) et vérifie que ses variables existent ; une
variable inconnue est une erreur de config. Le rendu ne peut donc plus
échouer.

Contenu : ``Template`` (un template analysé), ``TemplateError`` (template mal
formé), ``lookup`` (valeur au bout d'un chemin) et ``render_value`` (texte
inséré pour une valeur).
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
    """Template mal formé, levé par ``Template.parse``.

    Deux cas : une variable dont l'expression n'est pas un chemin pointé
    (``{{ a + b }}``, ``{{ }}``), ou un ``{{`` sans ``}}`` correspondant. Le
    message cite la variable fautive et rappelle la forme attendue.
    """


@dataclass(frozen=True, slots=True)
class Template:
    """Un template ``{{ var }}`` analysé, prêt à rendre.

    Attributes:
        source: Texte du template, tel qu'écrit dans la config.
        parts: Le template découpé, dans l'ordre : une chaîne est du texte
            littéral, un tuple est le chemin d'une variable
            (``("args", "devis")``).
    """

    source: str
    parts: tuple[str | Path, ...]

    @classmethod
    def parse(cls, source: str) -> Self:
        """Analyse un template.

        Args:
            source: Texte du template. Une variable s'écrit ``{{ chemin }}``,
                espaces permis autour du chemin ; le chemin est fait de noms
                (lettres, chiffres, ``_``, ``-``) séparés par des points.

        Returns:
            Le template, découpé en texte et en chemins.

        Raises:
            TemplateError: Une variable n'est pas un chemin pointé, ou un
                ``{{`` n'est pas fermé.
        """
        parts: list[str | Path] = []
        position = 0
        for match in _VARIABLE.finditer(source):
            if match.start() > position:
                parts.append(source[position : match.start()])
            expression = match.group(1).strip()
            if not _PATH.fullmatch(expression):
                raise TemplateError(f"Variable invalide : {match.group(0)!r} (attendu un chemin pointé, par exemple {{{{ args.devis }}}})")
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
        """Chemins des variables, dans l'ordre du texte ; la config les vérifie au chargement."""
        return tuple(part for part in self.parts if isinstance(part, tuple))

    def render(self, values: Mapping[str, JsonValue]) -> str:
        """Rend le template avec des valeurs ; ne lève jamais.

        Args:
            values: Valeurs de premier niveau (``args``, ``context``…), où
                chaque chemin est cherché par ``lookup``.

        Returns:
            Le texte, où chaque variable est remplacée par ``render_value`` de
            sa valeur : une chaîne telle quelle, ``None`` ou une valeur absente
            par une chaîne vide, le reste en JSON.
        """
        return "".join(part if isinstance(part, str) else render_value(lookup(values, part)) for part in self.parts)


def lookup(values: Mapping[str, JsonValue], path: Path) -> JsonValue:
    """Valeur au bout d'un chemin, ou ``None`` si une étape manque.

    Chaque segment est une clé d'objet, ou l'indice d'une liste s'il est fait
    de chiffres (18 au plus). Un indice hors de la liste, une clé absente ou une
    étape qui tombe sur une valeur simple donnent ``None``.

    Args:
        values: Valeurs de premier niveau.
        path: Chemin découpé, par exemple ``("args", "lignes", "0")``.

    Returns:
        La valeur trouvée, ou ``None``.
    """
    current: object = values
    for key in path:
        if isinstance(current, Mapping):
            current = cast(Mapping[str, object], current).get(key)
        elif isinstance(current, Sequence) and not isinstance(current, str) and re.fullmatch(r"[0-9]{1,18}", key):
            items = cast(Sequence[object], current)
            index = int(key)
            current = items[index] if index < len(items) else None
        else:
            return None
    return cast(JsonValue, current)


def render_value(value: JsonValue) -> str:
    """Texte inséré dans un template pour une valeur.

    Args:
        value: Valeur trouvée par ``lookup``.

    Returns:
        Une chaîne vide pour ``None``, la chaîne telle quelle pour du texte, et
        sinon le JSON de la valeur (caractères non ASCII gardés tels quels).
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)
