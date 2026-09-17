# SPDX-License-Identifier: Apache-2.0
"""Outils Python : schéma déduit, appel, conversion du résultat."""

import asyncio
from typing import Annotated

import pytest
from pydantic import BaseModel, Field

from loom_ia.core.model import (
    DEFAULT_TENANT,
    JsonBlock,
    RunId,
    SessionId,
    TextBlock,
    ToolOutput,
)
from loom_ia.core.ports import ToolContext, ToolError, idempotency_key
from loom_ia.tools import FunctionTool, to_output, tool

CONTEXT = ToolContext(
    tenant_id=DEFAULT_TENANT,
    session_id=SessionId("s"),
    run_id=RunId("r"),
    call_id="c1",
    agent="demo",
)


class Adresse(BaseModel):
    ville: str
    code_postal: str | None = None


@tool
def calculer(expr: str) -> str:
    """Évalue une expression arithmétique."""
    return str(eval(expr, {"__builtins__": {}}))


@tool(name="meteo_ville", side_effects="none", idempotent=True, timeout=5)
async def meteo(
    adresse: Adresse,
    jours: Annotated[int, Field(ge=1, le=7, description="Nombre de jours")] = 1,
) -> dict[str, object]:
    """Prévisions météo.

    Renvoie une prévision par jour.
    """
    await asyncio.sleep(0)
    return {"ville": adresse.ville, "jours": jours}


@tool(side_effects="irreversible")
async def envoyer(message: str, ctx: ToolContext) -> None:
    """Envoie un message."""
    assert ctx.call_id == "c1"


def test_schema_and_spec_come_from_the_function() -> None:
    spec = calculer.spec
    assert spec.name == "calculer"
    assert spec.description == "Évalue une expression arithmétique."
    assert spec.kind == "python"
    assert spec.input_schema == {
        "type": "object",
        "properties": {"expr": {"type": "string", "title": "Expr"}},
        "required": ["expr"],
        "additionalProperties": False,
    }
    assert repr(calculer) == "FunctionTool('calculer')"


def test_options_nested_models_and_descriptions() -> None:
    spec = meteo.spec
    assert spec.name == "meteo_ville"
    assert spec.description == "Prévisions météo.\n\nRenvoie une prévision par jour."
    assert (spec.idempotent, spec.timeout, spec.safe_to_retry) == (True, 5, True)
    properties = spec.input_schema["properties"]
    assert isinstance(properties, dict)
    assert properties["jours"] == {
        "type": "integer",
        "minimum": 1,
        "maximum": 7,
        "default": 1,
        "description": "Nombre de jours",
        "title": "Jours",
    }
    assert "$defs" in spec.input_schema
    assert spec.input_schema["required"] == ["adresse"]


def test_context_parameter_is_hidden_from_the_model() -> None:
    assert envoyer.spec.input_schema["required"] == ["message"]
    properties = envoyer.spec.input_schema["properties"]
    assert isinstance(properties, dict)
    assert set(properties) == {"message"}
    assert not envoyer.spec.safe_to_retry


def test_decorated_function_stays_callable() -> None:
    assert calculer("2+2") == "4"
    assert isinstance(calculer, FunctionTool)


async def test_invoke_sync_and_async_functions() -> None:
    assert await calculer.invoke({"expr": "12*7+3"}, CONTEXT) == ToolOutput.text("87")
    output = await meteo.invoke({"adresse": {"ville": "Lyon"}, "jours": 2}, CONTEXT)
    assert output.data == {"ville": "Lyon", "jours": 2}
    assert output.blocks == (JsonBlock(data={"ville": "Lyon", "jours": 2}),)
    assert await envoyer.invoke({"message": "Bonjour"}, CONTEXT) == ToolOutput()


async def test_invalid_arguments_raise_a_tool_error() -> None:
    with pytest.raises(ToolError) as caught:
        await meteo.invoke({"adresse": {}, "jours": 9, "autre": 1}, CONTEXT)
    message = caught.value.message
    assert message.startswith("Arguments invalides :")
    assert "- adresse.ville : Field required" in message
    assert "- jours :" in message
    assert "- autre : Extra inputs are not permitted" in message


def test_result_conversion() -> None:
    explicit = ToolOutput.error("non")
    assert to_output(explicit) is explicit
    assert to_output("texte").blocks == (TextBlock(text="texte"),)
    assert to_output(None) == ToolOutput()
    assert to_output(42).data == 42
    assert to_output(Adresse(ville="Paris")).data == {"ville": "Paris", "code_postal": None}
    assert to_output([1, "a"]).blocks == (JsonBlock(data=[1, "a"]),)
    with pytest.raises(Exception, match="serialize"):
        to_output(object())


def test_invalid_declarations() -> None:
    def sans_doc(x: int) -> int:
        return x

    def variadique(*args: int) -> int:
        """Somme."""
        return sum(args)

    with pytest.raises(ValueError, match="description manquante"):
        tool(sans_doc)
    assert tool(description="Identité.")(sans_doc).spec.description == "Identité."
    with pytest.raises(TypeError, match="args"):
        tool(variadique)
    with pytest.raises(ValueError, match="name"):
        tool(name="nom invalide", description="x")(sans_doc)


def test_idempotency_key_is_stable() -> None:
    assert CONTEXT.idempotency_key == idempotency_key(RunId("r"), "c1")
    assert CONTEXT.idempotency_key != idempotency_key(RunId("r"), "c2")
    assert len(CONTEXT.idempotency_key) == 64
