# SPDX-License-Identifier: Apache-2.0
"""Références ``$ref`` aux résultats d'outils, et leur marquage dans les requêtes (#12)."""

import pytest
from pydantic import JsonValue

from loom_ia.core.model import JsonBlock, Message, TextBlock, ToolOutput, ToolResultBlock
from loom_ia.engine import REFS_HINT, RefError, ResultIndex, mark_results
from loom_ia.testing import tool_call_message


def result(call_id: str, output: ToolOutput) -> Message:
    return Message(role="tool", blocks=(ToolResultBlock(call_id=call_id, output=output),))


DATA: JsonValue = {"devis": "D-42", "montant": 1200}

# Deux tours : chercher (JSON) et calculer (texte), puis un tour dont
# « lent » n'a pas encore de résultat, à côté d'un appel en erreur.
MESSAGES = (
    Message.user("Relance le devis"),
    tool_call_message(("c1", "chercher", {}), ("c2", "calculer", {"expr": "1+1"})),
    result("c1", ToolOutput(blocks=(JsonBlock(data=DATA),), data=DATA)),
    result("c2", ToolOutput.text("2")),
    tool_call_message(("c3", "lent", {}), ("c4", "casse", {})),
    result("c4", ToolOutput.error("boum")),
)


def test_calls_are_numbered_in_request_order() -> None:
    index = ResultIndex(MESSAGES)
    assert [(r.ref, r.call_id, r.name) for r in index.records] == [
        ("result:1", "c1", "chercher"),
        ("result:2", "c2", "calculer"),
        ("result:3", "c3", "lent"),
        ("result:4", "c4", "casse"),
    ]
    assert index.ref_of("c2") == "result:2"
    assert index.ref_of("inconnu") is None
    assert [r.call_id for r in index.results_of("chercher")] == ["c1"]
    assert index.results_of("casse") == index.results_of("lent") == ()


def test_references_are_replaced_at_any_depth() -> None:
    index = ResultIndex(MESSAGES)
    arguments: dict[str, JsonValue] = {
        "devis": {"$ref": "result:1"},
        "lignes": [{"total": {"$ref": "result:2"}}, "fixe"],
        "autre": {"$ref": "#/definitions/x"},
        "double": {"$ref": "result:2", "note": "pas une référence"},
    }
    resolved, refs = index.resolve(arguments)
    assert resolved == {
        "devis": DATA,
        "lignes": [{"total": "2"}, "fixe"],
        "autre": {"$ref": "#/definitions/x"},
        "double": {"$ref": "result:2", "note": "pas une référence"},
    }
    assert refs == ("result:1", "result:2")
    assert index.resolve({"x": 1}) == ({"x": 1}, ())


def test_serialized_references_are_resolved_too() -> None:
    # Ce qu'a écrit MiniMax-M3 : la référence sérialisée en chaîne.
    index = ResultIndex(MESSAGES)
    arguments: dict[str, JsonValue] = {
        "devis": '{"$ref": "result:1"}',
        "total": ' {"$ref":"result:2"} ',
        "liste": ['{"$ref": "result:2"}'],
        "texte": 'Voir {"$ref": "result:1"} plus haut',
        "casse": '{"$ref": "result:1"',
        "invalide": '{"$ref": result:1}',
        "autre": '{"$ref": "#/definitions/x"}',
        "deux": '{"$ref": "result:1", "note": 1}',
    }
    resolved, refs = index.resolve(arguments)
    assert resolved == {
        **arguments,
        "devis": DATA,
        "total": "2",
        "liste": ["2"],
    }
    assert refs == ("result:1", "result:2", "result:2")
    with pytest.raises(RefError, match="Référence inconnue : result:7"):
        index.resolve({"x": '{"$ref": "result:7"}'})


@pytest.mark.parametrize(
    ("ref", "message"),
    [
        (
            "result:9",
            "Référence inconnue : result:9. Références disponibles : "
            "result:1 (chercher), result:2 (calculer).",
        ),
        ("result:c1", "Référence invalide : 'result:c1'"),
        ("result:3", "result:3 (lent) n'a pas encore de résultat"),
        ("result:4", "result:4 (casse) est une erreur"),
    ],
)
def test_unresolvable_references(ref: str, message: str) -> None:
    with pytest.raises(RefError) as caught:
        ResultIndex(MESSAGES).resolve({"x": {"$ref": ref}})
    assert caught.value.message.startswith(message)


def test_empty_result_and_empty_run() -> None:
    messages = (tool_call_message(("c1", "vide", {})), result("c1", ToolOutput()))
    with pytest.raises(RefError, match="est vide"):
        ResultIndex(messages).resolve({"x": {"$ref": "result:1"}})
    with pytest.raises(RefError, match="Aucun résultat à référencer"):
        ResultIndex(()).resolve({"x": {"$ref": "result:1"}})


def test_results_shown_to_the_model_carry_their_reference() -> None:
    only_data = ToolOutput(data={"n": 1})
    messages = (*MESSAGES[:4], tool_call_message(("c5", "brut", {})), result("c5", only_data))
    marked = mark_results(messages, ResultIndex(messages))

    assert marked[0] == messages[0] and marked[1] == messages[1]
    blocks = [b for m in marked for b in m.blocks if isinstance(b, ToolResultBlock)]
    assert [b.output.blocks[0] for b in blocks] == [
        TextBlock(text="[result:1]"),
        TextBlock(text="[result:2]"),
        TextBlock(text="[result:3]"),
    ]
    # Sans blocs, ``data`` reste visible derrière la référence.
    assert blocks[2].output.blocks[1:] == (JsonBlock(data={"n": 1}),)
    # Les erreurs ne sont pas référençables : pas de marque.
    errors = mark_results(MESSAGES, ResultIndex(MESSAGES))[-1]
    assert errors == MESSAGES[-1]
    assert "$ref" in REFS_HINT
