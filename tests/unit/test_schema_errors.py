# SPDX-License-Identifier: Apache-2.0
"""Un message d'erreur de schéma ne recopie jamais la valeur fautive (RES-2)."""

import pytest
from pydantic import JsonValue

from loom_ia.core.bounded_regex import bounded_validator
from loom_ia.core.schema_errors import schema_problem

SECRET = "sk-SECRET-123"


@pytest.mark.parametrize(
    ("schema", "value", "said"),
    [
        ({"type": "integer"}, SECRET, "doit être un entier (reçu : du texte)"),
        ({"enum": ["a", "b"]}, SECRET, "doit valoir l'une des valeurs ['a', 'b']"),
        ({"const": "ok"}, SECRET, "doit valoir 'ok'"),
        ({"maxLength": 4}, SECRET, "13 caractères ; attendu au plus 4"),
        ({"minLength": 20}, SECRET, "13 caractères ; attendu au moins 20"),
        ({"pattern": "^[0-9]+$"}, SECRET, "ne respecte pas le motif ^[0-9]+$"),
        ({"maximum": 10}, 4242, "doit être inférieur ou égal à 10"),
        ({"minimum": 10}, 4, "doit être supérieur ou égal à 10"),
        ({"minItems": 2}, [SECRET], "1 éléments ; attendu au moins 2"),
        ({"uniqueItems": True}, [SECRET, SECRET], "les éléments de la liste doivent être uniques"),
        (
            {"anyOf": [{"type": "integer"}, {"type": "null"}]},
            SECRET,
            "ne correspond à aucune des variantes autorisées",
        ),
        ({"type": "object", "required": ["a"]}, {SECRET: 1}, "propriété requise absente : a"),
        (
            {"type": "object", "properties": {"a": {}}, "additionalProperties": False},
            {"a": SECRET, "b": SECRET},
            "propriété non prévue par le schéma : b",
        ),
    ],
)
def test_the_faulty_value_is_never_quoted(
    schema: dict[str, JsonValue], value: JsonValue, said: str
) -> None:
    errors = list(bounded_validator(schema).iter_errors(value))
    assert errors
    problem = schema_problem(errors[0])
    assert problem == said
    assert SECRET not in problem and "4242" not in problem


def test_a_keyword_without_its_own_wording_is_named_without_the_value() -> None:
    schema: dict[str, JsonValue] = {"minContains": 2, "contains": {"type": "integer"}}
    errors = list(bounded_validator(schema).iter_errors([SECRET]))
    assert errors and SECRET not in schema_problem(errors[0])
