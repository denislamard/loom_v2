# SPDX-License-Identifier: Apache-2.0
"""Expressions régulières à délai : recherche, substitution, validateur JSON Schema borné."""

import types
from typing import Any

import pytest
from jsonschema import (
    Draft3Validator,
    Draft4Validator,
    Draft6Validator,
    Draft7Validator,
    Draft201909Validator,
    Draft202012Validator,
    SchemaError,
    _keywords,  # pyright: ignore[reportPrivateUsage]
    _legacy_keywords,  # pyright: ignore[reportPrivateUsage]
    _utils,  # pyright: ignore[reportPrivateUsage]
)
from jsonschema.validators import validator_for

from loom_ia.core import bounded_regex
from loom_ia.core.bounded_regex import (
    PatternError,
    RegexTimeout,
    bounded_validator,
    check_schema,
    compile_pattern,
    search,
    substitute,
)

# Catastrophique pour ``regex`` comme pour ``re`` : sur ce texte, elle ne finit pas.
SLOW = "^(a|aa)+$"
SLOW_TEXT = "a" * 64 + "!"
D7 = "http://json-schema.org/draft-07/schema#"
D2019 = "https://json-schema.org/draft/2019-09/schema"
D2020 = "https://json-schema.org/draft/2020-12/schema"


@pytest.fixture(autouse=True)
def short_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """Un délai court : un motif lent s'arrête vite, un motif sain finit bien avant."""
    monkeypatch.setattr(bounded_regex, "REGEX_TIMEOUT", 0.2)


# --- Recherche et substitution ---------------------------------------------------------


def test_a_healthy_pattern_searches_and_substitutes() -> None:
    found = search(r"D-\d{4}", "Devis D-2026 et D-2027")
    assert found is not None and (found.start(), found.group()) == (6, "D-2026")
    assert search(r"D-\d{4}", "rien") is None
    assert search(compile_pattern("^$"), "") is not None
    assert substitute(compile_pattern(r"\d+"), "#", "a1 b22") == "a# b#"


def test_a_pattern_that_does_not_finish_raises_regex_timeout() -> None:
    with pytest.raises(
        RegexTimeout, match=r"motif trop coûteux : '\^\(a\|aa\)\+\$' \(délai de 0.2 s"
    ) as raised:
        search(SLOW, SLOW_TEXT)
    assert raised.value.pattern == SLOW
    with pytest.raises(RegexTimeout):
        search(compile_pattern(SLOW), SLOW_TEXT)
    with pytest.raises(RegexTimeout):
        substitute(compile_pattern(SLOW), "#", SLOW_TEXT)


def test_the_engine_is_regex_and_refuses_what_it_cannot_compile() -> None:
    with pytest.raises(PatternError):
        compile_pattern("(")
    # Au-delà de ``re`` : propriétés Unicode, quantificateur possessif.
    assert search(r"^\p{Lu}+$", "ÉCOLE") is not None
    assert search(r"^a++b", "aaab") is not None


# --- JSON Schema : chaque mot-clé qui cherche un motif a son délai -----------------------

KEYWORDS: list[tuple[dict[str, Any], Any]] = [
    ({"type": "object", "properties": {"s": {"pattern": SLOW}}}, {"s": SLOW_TEXT}),
    ({"$schema": D7, "properties": {"s": {"pattern": SLOW}}}, {"s": SLOW_TEXT}),
    ({"patternProperties": {SLOW: {"type": "integer"}}}, {SLOW_TEXT: 1}),
    ({"patternProperties": {SLOW: {}}, "additionalProperties": False}, {SLOW_TEXT: 1}),
    ({"patternProperties": {SLOW: {}}, "unevaluatedProperties": False}, {SLOW_TEXT: 1}),
    # Dans l'ordre inverse, le mot-clé qui cherche passe avant ``patternProperties``.
    ({"unevaluatedProperties": False, "patternProperties": {SLOW: {}}}, {SLOW_TEXT: 1}),
    (
        {"allOf": [{"patternProperties": {SLOW: {}}}], "unevaluatedProperties": False},
        {SLOW_TEXT: 1},
    ),
    (
        {
            "$defs": {"ext": {"patternProperties": {SLOW: {}}}},
            "$ref": "#/$defs/ext",
            "unevaluatedProperties": False,
        },
        {SLOW_TEXT: 1},
    ),
    (
        {"$schema": D2019, "patternProperties": {SLOW: {}}, "unevaluatedProperties": False},
        {SLOW_TEXT: 1},
    ),
]


@pytest.mark.parametrize(("schema", "instance"), KEYWORDS)
def test_every_keyword_that_searches_a_pattern_has_a_delay(
    schema: dict[str, Any], instance: Any
) -> None:
    with pytest.raises(RegexTimeout, match="motif trop coûteux"):
        bounded_validator(schema).is_valid(instance)


def test_the_timeout_comes_out_of_every_way_to_validate() -> None:
    schema, instance = KEYWORDS[0]
    validator = bounded_validator(schema)
    with pytest.raises(RegexTimeout):
        validator.validate(instance)
    with pytest.raises(RegexTimeout):
        list(validator.iter_errors(instance))


@pytest.mark.parametrize(
    "schema",
    [
        {"not": {"pattern": SLOW}},
        {"anyOf": [{"pattern": SLOW}, {"type": "string"}]},
        {"oneOf": [{"pattern": SLOW}, {"type": "integer"}]},
        {"if": {"pattern": SLOW}, "else": {"type": "string"}},
        {"contains": {"pattern": SLOW}},
    ],
)
def test_a_timeout_is_never_read_as_a_verdict(schema: dict[str, Any]) -> None:
    instance = [SLOW_TEXT] if "contains" in schema else SLOW_TEXT
    with pytest.raises(RegexTimeout):
        bounded_validator(schema).is_valid(instance)


HEALTHY: list[tuple[dict[str, Any], list[Any]]] = [
    (
        {"type": "object", "properties": {"s": {"pattern": r"^D-\d{4}$"}}},
        [{"s": "D-2026"}, {"s": "x"}, {"s": 3}],
    ),
    (
        {"patternProperties": {"^x-": {"type": "integer"}}, "additionalProperties": False},
        [{"x-a": 1}, {"x-a": "non", "y": 1}, {}],
    ),
    (
        {"patternProperties": {"^x-": {}, "^y-": {}}, "additionalProperties": {"type": "string"}},
        [{"x-a": 1, "y-b": 2, "z": "ok"}, {"z": 3}],
    ),
    (
        {"properties": {"a": {}}, "patternProperties": {"^x-": {}}, "unevaluatedProperties": False},
        [{"a": 1, "x-b": 2}, {"a": 1, "z": 2}],
    ),
    (
        {
            "allOf": [{"patternProperties": {"^x-": {}}}],
            "unevaluatedProperties": {"type": "integer"},
        },
        [{"x-a": "texte", "b": 1}, {"b": "texte"}],
    ),
    (
        {
            "$schema": D7,
            "patternProperties": {"^x-": {"type": "integer"}},
            "additionalProperties": False,
        },
        [{"x-a": 1}, {"x-a": "non", "y": 1}],
    ),
    (
        {"$schema": D2019, "patternProperties": {"^x-": {}}, "unevaluatedProperties": False},
        [{"x-a": 1}, {"y": 1}],
    ),
]


@pytest.mark.parametrize(("schema", "instances"), HEALTHY)
def test_nothing_changes_when_no_pattern_is_slow(
    schema: dict[str, Any], instances: list[Any]
) -> None:
    """Mêmes erreurs, même chemin, mêmes messages que ``jsonschema`` seul."""
    stock = validator_for(schema, default=Draft202012Validator)(schema)
    bounded = bounded_validator(schema)
    for instance in instances:
        wanted = [(e.message, list(e.absolute_path)) for e in stock.iter_errors(instance)]
        got = [(e.message, list(e.absolute_path)) for e in bounded.iter_errors(instance)]
        assert got == wanted


def test_jsonschema_has_no_other_function_that_searches_a_pattern() -> None:
    """Si une version de jsonschema cherche un motif ailleurs, ce garde-fou le dit."""
    searching = {
        f"{module.__name__.rsplit('.', 1)[1]}.{name}"
        for module in (_keywords, _legacy_keywords, _utils)
        for name, function in vars(module).items()
        if isinstance(function, types.FunctionType)
        and function.__module__ == module.__name__
        and "re" in function.__code__.co_names
    }
    assert searching == {
        "_keywords.pattern",
        "_keywords.patternProperties",
        "_legacy_keywords.find_evaluated_property_keys_by_schema",
        "_utils.find_additional_properties",
        "_utils.find_evaluated_property_keys_by_schema",
    }
    # Et chaque mot-clé remplacé existe toujours dans une classe de validation.
    drafts = (
        Draft3Validator,
        Draft4Validator,
        Draft6Validator,
        Draft7Validator,
        Draft201909Validator,
        Draft202012Validator,
    )
    declared = {function for draft in drafts for function in draft.VALIDATORS.values()}
    assert set(bounded_regex._CLONES) <= declared  # pyright: ignore[reportPrivateUsage]


# --- Contrôle d'un schéma ------------------------------------------------------------------


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "object", "properties": {"s": {"pattern": r"^\p{L}+$"}}},
        {"type": "object", "patternProperties": {r"^\p{Lu}": {}}},
        # Un nom de propriété n'est pas un dialecte.
        {"$schema": D2020, "type": "object", "properties": {"$schema": {"type": "string"}}},
        # Des références qui se résolvent : pointeur local, racine, ancre, ``$id`` d'un
        # sous-schéma, méta-schéma. Un nom de propriété « $ref » n'est pas une référence.
        {"$defs": {"n": {"type": "integer"}}, "properties": {"a": {"$ref": "#/$defs/n"}}},
        {"type": "object", "properties": {"suite": {"$ref": "#"}}},
        {"$defs": {"n": {"$anchor": "nombre", "type": "integer"}}, "$ref": "#nombre"},
        {
            "$id": "https://loom.test/racine",
            "properties": {
                "a": {"$id": "https://loom.test/autre", "$defs": {"x": {}}, "$ref": "#/$defs/x"},
                "b": {"$ref": "https://loom.test/autre#/$defs/x"},
            },
        },
        {"$ref": "https://json-schema.org/draft/2020-12/schema"},
        {"type": "object", "properties": {"$ref": {"type": "string"}}},
    ],
)
def test_check_schema_accepts_what_the_engine_runs(schema: dict[str, Any]) -> None:
    check_schema(schema)


@pytest.mark.parametrize(
    ("schema", "said"),
    [
        ({"type": "objet"}, "not valid under any of the given schemas"),
        ({"properties": {"s": {"pattern": "("}}}, "'(' is not a 'regex'"),
        ({"patternProperties": {"[": {}}}, "'[' is not a 'regex'"),
        # Un sous-schéma qui déclare son dialecte échapperait au délai.
        (
            {"properties": {"s": {"$schema": D2020, "pattern": SLOW}}},
            "ne se déclare qu'à la racine",
        ),
        ({"allOf": [{"$schema": D7}]}, "ne se déclare qu'à la racine"),
        # Une référence qui ne se résout pas casserait à la validation, une fois le modèle payé
        # (GAR-6) ; rien ne va chercher un fichier ni une adresse.
        ({"$ref": "#/$defs/absent"}, "'#/$defs/absent'"),
        ({"$ref": "autre.json"}, "'autre.json'"),
        ({"$ref": "https://loom.test/schema.json"}, "'https://loom.test/schema.json'"),
        ({"properties": {"a": {"items": {"$ref": "#/$defs/absent"}}}}, "'#/$defs/absent'"),
        (
            {"$defs": {"n": {}}, "properties": {"a": {"$ref": "#/$defs/n/$defs/m"}}},
            "'#/$defs/n/$defs",
        ),
        ({"$ref": "#manquante"}, "'#manquante'"),
        (
            {"allOf": [{"$ref": "#/$defs/a"}, {"$ref": "#/$defs/b"}], "$defs": {"a": {}}},
            "'#/$defs/b'",
        ),
    ],
)
def test_check_schema_refuses_what_it_could_not_bound(schema: dict[str, Any], said: str) -> None:
    with pytest.raises(SchemaError) as refused:
        check_schema(schema)
    assert said in refused.value.message
