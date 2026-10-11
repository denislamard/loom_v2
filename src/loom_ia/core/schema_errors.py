# SPDX-License-Identifier: Apache-2.0
"""Dire une erreur de schéma sans citer la valeur fautive.

Les messages de ``jsonschema`` recopient la valeur qui ne convient pas :
``'sk-SECRET-123' is not of type 'integer'``. Or ce texte part dans le journal
(``guard.checked.reason``, ``run.failed.error``, ``tool.completed.output``) et
dans les journaux de l'hôte, qui ne doivent pas porter ce que l'utilisateur ou
l'outil a écrit. ``schema_problem`` ne dit que ce que le schéma exige — des mots du
code ou du schéma, que son auteur a écrits — et, de la valeur, son genre ou sa
taille : jamais son contenu (seuls les noms de propriétés en trop sont cités). Le
chemin de la valeur est dit à part par l'appelant.
"""

from collections.abc import Sequence
from typing import Final, cast

from jsonschema import ValidationError

# Un mot-clé qui porte une liste de valeurs ou de variantes : coupé, pour qu'un
# schéma long ne remplisse pas le journal.
_LIMIT: Final = 120

_TYPES: Final = {
    "string": "du texte",
    "object": "un objet JSON",
    "array": "une liste",
    "integer": "un entier",
    "number": "un nombre",
    "boolean": "un booléen",
    "null": "null",
}


def _kind(value: object) -> str:
    """Le genre d'une valeur, jamais son contenu."""
    match value:
        case None:
            return "null"
        case bool():
            return _TYPES["boolean"]
        case str():
            return _TYPES["string"]
        case dict():
            return _TYPES["object"]
        case list():
            return _TYPES["array"]
        case int() | float():
            return _TYPES["number"]
        case _:
            return type(value).__name__


def _cut(text: str) -> str:
    return text if len(text) <= _LIMIT else text[: _LIMIT - 1] + "…"


def _size(value: object) -> int:
    return len(cast("Sequence[object]", value)) if isinstance(value, str | list | dict) else 0


def _missing(instance: object, wanted: list[str]) -> list[str]:
    if not isinstance(instance, dict):
        return wanted
    present = cast("dict[str, object]", instance)
    return [name for name in wanted if name not in present] or wanted


def _extra(error: ValidationError) -> str:
    """Les propriétés en trop, par leur nom : un nom est de la structure, pas une valeur.

    Sans ``patternProperties`` (qui en admet d'autres par leur forme), le calcul
    est exact ; sinon, le message reste général.
    """
    schema = cast("dict[str, object]", error.schema)
    instance = error.instance
    if not isinstance(instance, dict) or "patternProperties" in schema:
        return "porte des propriétés que le schéma ne prévoit pas"
    known = schema.get("properties")
    allowed: set[str] = set(cast("dict[str, object]", known)) if isinstance(known, dict) else set()
    names = [str(name) for name in cast("dict[str, object]", instance) if name not in allowed]
    return _cut("propriété non prévue par le schéma : " + ", ".join(names))


def schema_problem(error: ValidationError) -> str:
    """Ce que le schéma exige de la valeur, sans la recopier."""
    keyword = str(error.validator)
    expected = error.validator_value
    instance = error.instance
    match keyword:
        case "type":
            kinds = [expected] if isinstance(expected, str) else list(cast("list[str]", expected))
            wanted = " ou ".join(_TYPES.get(kind, kind) for kind in kinds)
            return f"doit être {wanted} (reçu : {_kind(instance)})"
        case "enum":
            return _cut(f"doit valoir l'une des valeurs {expected!r}")
        case "const":
            return _cut(f"doit valoir {expected!r}")
        case "required":
            names = _missing(instance, cast("list[str]", expected))
            return "propriété requise absente : " + ", ".join(names)
        case "minLength" | "maxLength":
            side = "au moins" if keyword == "minLength" else "au plus"
            return f"{_size(instance)} caractères ; attendu {side} {expected}"
        case "minItems" | "maxItems":
            side = "au moins" if keyword == "minItems" else "au plus"
            return f"{_size(instance)} éléments ; attendu {side} {expected}"
        case "minProperties" | "maxProperties":
            side = "au moins" if keyword == "minProperties" else "au plus"
            return f"{_size(instance)} propriétés ; attendu {side} {expected}"
        case "minimum":
            return f"doit être supérieur ou égal à {expected}"
        case "maximum":
            return f"doit être inférieur ou égal à {expected}"
        case "exclusiveMinimum":
            return f"doit être strictement supérieur à {expected}"
        case "exclusiveMaximum":
            return f"doit être strictement inférieur à {expected}"
        case "multipleOf":
            return f"doit être un multiple de {expected}"
        case "pattern":
            return _cut(f"ne respecte pas le motif {expected}")
        case "format":
            return f"n'a pas le format {expected}"
        case "uniqueItems":
            return "les éléments de la liste doivent être uniques"
        case "additionalProperties" | "unevaluatedProperties":
            return _extra(error)
        case "additionalItems" | "unevaluatedItems":
            return "porte des éléments que le schéma ne prévoit pas"
        case "anyOf":
            return "ne correspond à aucune des variantes autorisées"
        case "oneOf":
            return "doit correspondre à exactement une des variantes autorisées"
        case "not":
            return "correspond à un schéma interdit"
        case "contains" | "minContains" | "maxContains":
            return "le nombre d'éléments attendus n'y est pas"
        case "dependentRequired" | "dependencies":
            return "une propriété qui en appelle une autre est seule"
        case "propertyNames":
            return "un nom de propriété ne convient pas"
        case _:
            return f"ne respecte pas la contrainte « {keyword} » du schéma"
