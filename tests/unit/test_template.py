# SPDX-License-Identifier: Apache-2.0
"""Templates ``{{ var }}`` : analyse, variables, rendu (#50)."""

import pytest
from pydantic import JsonValue

from loom_ia.core.template import Template, TemplateError, lookup, render_value


def test_parse_separates_text_and_variables() -> None:
    template = Template.parse("Devis : {{ args.devis }}\nDemande : {{context.user_input}}")
    assert template.parts == (
        "Devis : ",
        ("args", "devis"),
        "\nDemande : ",
        ("context", "user_input"),
    )
    assert template.variables == (("args", "devis"), ("context", "user_input"))


def test_render_inserts_strings_as_is_and_the_rest_as_json() -> None:
    template = Template.parse(
        "{{ a }}|{{ b }}|{{ c }}|{{ d.0 }}|{{ d.9 }}|{{ e.f }}|{{ absent.x }}"
    )
    values: dict[str, JsonValue] = {
        "a": "texte",
        "b": 12,
        "c": {"k": "é"},
        "d": ["x", "y"],
        "e": {"f": None},
    }
    assert template.render(values) == 'texte|12|{"k": "é"}|x|||'


def test_text_without_variables() -> None:
    template = Template.parse("Rien à remplacer } ni }}")
    assert template.variables == ()
    assert template.render({}) == "Rien à remplacer } ni }}"


@pytest.mark.parametrize(
    ("source", "message"),
    [
        ("{{ }}", "Variable invalide"),
        ("{{ args. }}", "Variable invalide"),
        ("{{ a b }}", "Variable invalide"),
        ("{{ a {{ b }}", "Variable invalide"),
        ("début {{ args.x", "sans '}}'"),
    ],
)
def test_malformed_templates(source: str, message: str) -> None:
    with pytest.raises(TemplateError, match=message):
        Template.parse(source)


def test_lookup_and_render_value() -> None:
    values: dict[str, JsonValue] = {"liste": [1, {"n": "deux"}], "texte": "abc"}
    assert lookup(values, ("liste", "1", "n")) == "deux"
    assert lookup(values, ("liste", "x")) is None
    assert lookup(values, ("texte", "0")) is None
    assert render_value(None) == ""
    assert render_value([1, "a"]) == '[1, "a"]'
