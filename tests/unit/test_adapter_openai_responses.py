# SPDX-License-Identifier: Apache-2.0
"""Contrat de l'adaptateur ``openai`` (``responses``) : requête envoyée, lecture du flux, erreurs.

Les réponses HTTP sont simulées par ``httpx2.MockTransport``, au format SSE de
l'API Responses (événements ``response.*``), et en JSON pour un modèle sans
streaming.
"""

import json
from typing import Any

import pytest

pytest.importorskip("openai")

import httpx2

from loom_ia.adapters.models import create_model_client
from loom_ia.adapters.models._common import is_strict
from loom_ia.adapters.models.openai_responses import OpenAIResponsesModel
from loom_ia.core.model import (
    InlineDataBlock,
    JsonBlock,
    Message,
    ModelRequest,
    ModelSpec,
    OpenAIMeta,
    ProviderMeta,
    ReasoningBlock,
    TextBlock,
    ToolCallBlock,
    ToolDefinition,
    ToolOutput,
    ToolResultBlock,
    Usage,
)
from loom_ia.core.ports import ModelError, complete

SPEC = ModelSpec(
    id="GPT",
    sdk="openai",
    api="responses",
    model="gpt-test",
    base_url="https://openai.test/v1",
    api_key_env="TEST_OPENAI_KEY",
    max_tokens=800,
)
TOOL = ToolDefinition(
    name="calculer",
    description="Calcule.",
    input_schema={"type": "object", "properties": {"expr": {"type": "string"}}},
)
STRICT_TOOL = ToolDefinition(
    name="heure",
    description="Donne l'heure.",
    input_schema={
        "type": "object",
        "properties": {"ville": {"type": "string"}},
        "required": ["ville"],
        "additionalProperties": False,
    },
)
SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"objet": {"type": "string"}, "corps": {"type": "string", "minLength": 20}},
    "required": ["objet", "corps"],
    "additionalProperties": False,
}
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8


def event(kind: str, **fields: Any) -> dict[str, Any]:
    return {"type": kind, "sequence_number": 0, **fields}


def response(
    status: str = "completed",
    *,
    output: list[dict[str, Any]] | None = None,
    usage: dict[str, Any] | None = None,
    incomplete: str | None = None,
    error: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": "resp_1",
        "object": "response",
        "created_at": 1,
        "model": "gpt-test-2026",
        "status": status,
        "output": output or [],
        "usage": usage,
        "incomplete_details": {"reason": incomplete} if incomplete else None,
        "error": error,
    }


def usage(
    input_tokens: int, output_tokens: int, cached: int = 0, reasoning: int = 0
) -> dict[str, Any]:
    return {
        "input_tokens": input_tokens,
        "input_tokens_details": {"cached_tokens": cached, "cache_write_tokens": 0},
        "output_tokens": output_tokens,
        "output_tokens_details": {"reasoning_tokens": reasoning},
        "total_tokens": input_tokens + output_tokens,
    }


def streamed(*events: dict[str, Any]) -> httpx2.Response:
    text = "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events)
    return httpx2.Response(200, text=text, headers={"content-type": "text/event-stream"})


class Server:
    def __init__(self, *responses: httpx2.Response) -> None:
        self.responses = list(responses)
        self.requests: list[httpx2.Request] = []

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        return self.responses.pop(0)

    @property
    def body(self) -> dict[str, Any]:
        return json.loads(self.requests[-1].content)

    def model(self, spec: ModelSpec = SPEC) -> OpenAIResponsesModel:
        client = create_model_client(
            spec,
            environ={"TEST_OPENAI_KEY": "sk-test"},
            http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(self.handle)),
        )
        assert isinstance(client, OpenAIResponsesModel)
        return client


def request(*messages: Message, **options: Any) -> ModelRequest:
    return ModelRequest.model_validate(
        {"model_id": "gpt-test", "messages": messages or (Message.user("?"),), **options}
    )


def encrypted(item_id: str, content: str) -> dict[str, ProviderMeta]:
    return {"openai": OpenAIMeta(item_id=item_id, encrypted_content=content)}


REASONING_ITEM: dict[str, Any] = {"type": "reasoning", "id": "rs_1", "summary": []}
MESSAGE_ITEM: dict[str, Any] = {
    "type": "message",
    "id": "msg_1",
    "role": "assistant",
    "status": "completed",
    "content": [],
}
CALL_ITEM: dict[str, Any] = {
    "type": "function_call",
    "id": "fc_1",
    "call_id": "call_1",
    "name": "calculer",
    "arguments": "",
    "status": "in_progress",
}


def tool_turn() -> httpx2.Response:
    """Raisonnement résumé et chiffré, texte, appel d'outil en deux morceaux, puis fin."""
    return streamed(
        event("response.created", response=response("in_progress")),
        event("response.output_item.added", output_index=0, item=REASONING_ITEM),
        event(
            "response.reasoning_summary_part.added",
            item_id="rs_1",
            output_index=0,
            summary_index=0,
            part={"type": "summary_text", "text": ""},
        ),
        event(
            "response.reasoning_summary_text.delta",
            item_id="rs_1",
            output_index=0,
            summary_index=0,
            delta="Je pose ",
        ),
        event(
            "response.reasoning_summary_text.delta",
            item_id="rs_1",
            output_index=0,
            summary_index=0,
            delta="le calcul.",
        ),
        event(
            "response.reasoning_summary_part.added",
            item_id="rs_1",
            output_index=0,
            summary_index=1,
            part={"type": "summary_text", "text": ""},
        ),
        event(
            "response.reasoning_summary_text.delta",
            item_id="rs_1",
            output_index=0,
            summary_index=1,
            delta="Puis je vérifie.",
        ),
        event(
            "response.output_item.done",
            output_index=0,
            item={**REASONING_ITEM, "encrypted_content": "chiffré-1"},
        ),
        event("response.output_item.added", output_index=1, item=MESSAGE_ITEM),
        event(
            "response.output_text.delta",
            item_id="msg_1",
            output_index=1,
            content_index=0,
            delta="Je calcule.",
            logprobs=[],
        ),
        event(
            "response.output_item.done",
            output_index=1,
            item={
                **MESSAGE_ITEM,
                "content": [{"type": "output_text", "text": "Je calcule.", "annotations": []}],
            },
        ),
        event("response.output_item.added", output_index=2, item=CALL_ITEM),
        event(
            "response.function_call_arguments.delta",
            item_id="fc_1",
            output_index=2,
            delta='{"expr": ',
        ),
        event(
            "response.function_call_arguments.delta",
            item_id="fc_1",
            output_index=2,
            delta='"12*7+3"}',
        ),
        event(
            "response.output_item.done",
            output_index=2,
            item={**CALL_ITEM, "arguments": '{"expr": "12*7+3"}', "status": "completed"},
        ),
        event(
            "response.completed",
            response=response(usage=usage(1500, 120, cached=1024, reasoning=64)),
        ),
    )


# --- Lecture -------------------------------------------------------------------------------


async def test_stream_is_read_in_order() -> None:
    server = Server(tool_turn())
    answer = await complete(server.model(), request(tools=(TOOL,)))

    assert answer.message.blocks == (
        ReasoningBlock(
            text="Je pose le calcul.\n\nPuis je vérifie.",
            provider_meta=encrypted("rs_1", "chiffré-1"),
            model_id="gpt-test",
        ),
        TextBlock(text="Je calcule."),
        ToolCallBlock(call_id="call_1", name="calculer", arguments={"expr": "12*7+3"}),
    )
    assert answer.stop_reason == "tool_use"
    assert answer.model_id == "gpt-test-2026"
    # Les tokens en cache sont retirés des tokens d'entrée.
    assert answer.usage == Usage(
        input_tokens=476, output_tokens=120, cache_read_tokens=1024, reasoning_tokens=64
    )


async def test_items_without_deltas_are_read_when_done() -> None:
    """Un fournisseur compatible peut ne donner que les éléments complets."""
    server = Server(
        streamed(
            event(
                "response.output_item.added",
                output_index=0,
                item={**CALL_ITEM, "call_id": "call_9"},
            ),
            event(
                "response.output_item.done",
                output_index=0,
                item={**CALL_ITEM, "call_id": "call_9", "arguments": '{"expr": "1"}'},
            ),
            event(
                "response.output_item.done",
                output_index=1,
                item={
                    **MESSAGE_ITEM,
                    "content": [{"type": "output_text", "text": "Fait.", "annotations": []}],
                },
            ),
            event("response.completed", response=response(usage=usage(10, 5))),
        )
    )
    answer = await complete(server.model(), request(tools=(TOOL,)))
    assert answer.message.blocks == (
        ToolCallBlock(call_id="call_9", name="calculer", arguments={"expr": "1"}),
        TextBlock(text="Fait."),
    )


@pytest.mark.parametrize(
    ("status", "reason", "stop"),
    [
        ("incomplete", "max_output_tokens", "max_tokens"),
        ("incomplete", "content_filter", "refusal"),
    ],
)
async def test_incomplete_answers(status: str, reason: str, stop: str) -> None:
    server = Server(
        streamed(
            event(
                "response.output_text.delta",
                item_id="msg_1",
                output_index=0,
                content_index=0,
                delta="Début",
                logprobs=[],
            ),
            event("response.incomplete", response=response(status, incomplete=reason)),
        )
    )
    answer = await complete(server.model(), request())
    assert (answer.message.text, answer.stop_reason) == ("Début", stop)


async def test_refusal() -> None:
    server = Server(
        streamed(
            event(
                "response.refusal.delta",
                item_id="msg_1",
                output_index=0,
                content_index=0,
                delta="Je ne peux pas.",
            ),
            event("response.completed", response=response()),
        )
    )
    answer = await complete(server.model(), request())
    assert (answer.message.text, answer.stop_reason) == ("Je ne peux pas.", "refusal")


async def test_raw_reasoning_of_an_open_model() -> None:
    """gpt-oss servi par l'API Responses : raisonnement en clair, sans chiffrement."""
    server = Server(
        streamed(
            event("response.output_item.added", output_index=0, item=REASONING_ITEM),
            event(
                "response.reasoning_text.delta",
                item_id="rs_1",
                output_index=0,
                content_index=0,
                delta="analyse",
            ),
            event("response.output_item.done", output_index=0, item=REASONING_ITEM),
            event("response.completed", response=response()),
        )
    )
    answer = await complete(server.model(), request())
    [block] = answer.message.blocks
    assert isinstance(block, ReasoningBlock) and block.text == "analyse"
    assert block.provider_meta == {"openai": OpenAIMeta(item_id="rs_1")}


async def test_non_streaming_models() -> None:
    body = response(
        output=[
            {
                **REASONING_ITEM,
                "summary": [{"type": "summary_text", "text": "Je pose le calcul."}],
                "encrypted_content": "chiffré-2",
            },
            {
                **MESSAGE_ITEM,
                "content": [{"type": "output_text", "text": "Je calcule.", "annotations": []}],
            },
            {**CALL_ITEM, "arguments": '{"expr": "3"}', "status": "completed"},
            {"type": "web_search_call", "id": "ws_1", "status": "completed"},
        ],
        usage=usage(10, 5),
    )
    server = Server(httpx2.Response(200, json=body))
    spec = SPEC.model_copy(
        update={"capabilities": SPEC.capabilities.model_copy(update={"streaming": False})}
    )
    answer = await complete(server.model(spec), request(tools=(TOOL,)))
    assert "stream" not in server.body
    assert answer.message.blocks == (
        ReasoningBlock(
            text="Je pose le calcul.",
            provider_meta=encrypted("rs_1", "chiffré-2"),
            model_id="gpt-test",
        ),
        TextBlock(text="Je calcule."),
        ToolCallBlock(call_id="call_1", name="calculer", arguments={"expr": "3"}),
    )
    assert (answer.stop_reason, answer.usage) == (
        "tool_use",
        Usage(input_tokens=10, output_tokens=5),
    )


# --- Requête -------------------------------------------------------------------------------


async def test_request_is_stateless_and_sends_encrypted_reasoning_back() -> None:
    server = Server(tool_turn())
    history = (
        Message.user("Combien font 12 * 7 + 3 ?"),
        Message(
            role="assistant",
            blocks=(
                ReasoningBlock(
                    text="Je pose le calcul.", provider_meta=encrypted("rs_0", "chiffré-0")
                ),
                TextBlock(text="Je calcule."),
                ToolCallBlock(call_id="call_0", name="calculer", arguments={"expr": "12*7+3"}),
            ),
        ),
        Message(
            role="tool",
            blocks=(
                ToolResultBlock(call_id="call_0", output=ToolOutput(blocks=(JsonBlock(data=87),))),
            ),
        ),
    )
    await complete(
        server.model(),
        request(*history, system="Tu calcules.", tools=(TOOL, STRICT_TOOL), tool_choice="required"),
    )
    body = server.body
    assert (body["model"], body["instructions"], body["store"]) == (
        "gpt-test",
        "Tu calcules.",
        False,
    )
    assert body["include"] == ["reasoning.encrypted_content"]
    assert body["max_output_tokens"] == 800
    assert body["stream"] is True
    assert body["tool_choice"] == "required"
    assert body["input"] == [
        {"role": "user", "content": "Combien font 12 * 7 + 3 ?"},
        # Sans identifiant : rien n'est gardé chez le fournisseur (store: false).
        {
            "type": "reasoning",
            "summary": [{"type": "summary_text", "text": "Je pose le calcul."}],
            "encrypted_content": "chiffré-0",
        },
        {"role": "assistant", "content": "Je calcule."},
        {
            "type": "function_call",
            "call_id": "call_0",
            "name": "calculer",
            "arguments": '{"expr": "12*7+3"}',
        },
        {"type": "function_call_output", "call_id": "call_0", "output": "87"},
    ]
    assert body["tools"] == [
        {
            "type": "function",
            "name": "calculer",
            "description": "Calcule.",
            "parameters": {"type": "object", "properties": {"expr": {"type": "string"}}},
            "strict": False,
        },
        {
            "type": "function",
            "name": "heure",
            "description": "Donne l'heure.",
            "parameters": STRICT_TOOL.input_schema,
            "strict": True,
        },
    ]
    assert "text" not in body


async def test_images_errors_and_other_models_reasoning() -> None:
    server = Server(tool_turn())
    history = (
        Message(
            role="user",
            blocks=(TextBlock(text="Regarde."), InlineDataBlock(media_type="image/png", data=PNG)),
        ),
        Message(
            role="assistant",
            blocks=(
                # Raisonnement en clair sans thinking : non renvoyé.
                ReasoningBlock(text="à part moi"),
                ToolCallBlock(call_id="c1", name="capture"),
                ToolCallBlock(call_id="c2", name="calculer"),
            ),
        ),
        Message(
            role="tool",
            blocks=(
                ToolResultBlock(
                    call_id="c1",
                    output=ToolOutput(
                        blocks=(
                            TextBlock(text="Écran"),
                            InlineDataBlock(media_type="image/png", data=PNG),
                        )
                    ),
                ),
                ToolResultBlock(call_id="c2", output=ToolOutput.error("division par zéro")),
            ),
        ),
    )
    await complete(server.model(), request(*history))
    items = server.body["input"]
    image = "data:image/png;base64,iVBORw0KGgoAAAAAAAAAAA=="
    assert items[0] == {
        "role": "user",
        "content": [
            {"type": "input_text", "text": "Regarde."},
            {"type": "input_image", "image_url": image, "detail": "auto"},
        ],
    }
    assert [item.get("type") for item in items[1:]] == [
        "function_call",
        "function_call",
        "function_call_output",
        "function_call_output",
    ]
    assert items[3]["output"] == [
        {"type": "input_text", "text": "Écran"},
        {"type": "input_image", "image_url": image, "detail": "auto"},
    ]
    assert items[4]["output"] == "[erreur] division par zéro"


async def test_clear_reasoning_goes_back_to_a_thinking_model_in_the_open_loop() -> None:
    spec = SPEC.model_copy(
        update={"capabilities": SPEC.capabilities.model_copy(update={"thinking": True})}
    )
    server = Server(tool_turn())
    history = (
        Message.user("Premier calcul"),
        Message(role="assistant", blocks=(ReasoningBlock(text="ancien"), TextBlock(text="4"))),
        Message.user("Second calcul"),
        Message(
            role="assistant",
            blocks=(ReasoningBlock(text="en cours"), ToolCallBlock(call_id="c1", name="calculer")),
        ),
        Message(role="tool", blocks=(ToolResultBlock(call_id="c1", output=ToolOutput.text("9")),)),
    )
    await complete(server.model(spec), request(*history))
    reasoning = [item for item in server.body["input"] if item.get("type") == "reasoning"]
    # Seule la boucle en cours : le tour conclu (« 4 ») a perdu le sien.
    assert reasoning == [
        {
            "type": "reasoning",
            "summary": [],
            "content": [{"type": "reasoning_text", "text": "en cours"}],
        }
    ]


async def test_output_schema_goes_to_text_format_with_native_json() -> None:
    server = Server(tool_turn(), tool_turn())
    await complete(server.model(), request(output_schema=SCHEMA))
    assert "text" not in server.body
    native = SPEC.model_copy(
        update={"capabilities": SPEC.capabilities.model_copy(update={"native_json": True})}
    )
    await complete(server.model(native), request(output_schema=SCHEMA))
    # minLength : hors du mode strict ; le contrat de sortie le vérifie.
    assert server.body["text"] == {
        "format": {"type": "json_schema", "name": "output", "schema": SCHEMA, "strict": False}
    }


# --- Erreurs -------------------------------------------------------------------------------


async def test_errors_are_classified() -> None:
    server = Server(
        httpx2.Response(
            429,
            json={
                "error": {
                    "message": "Trop de requêtes",
                    "type": "requests",
                    "code": "rate_limit_exceeded",
                }
            },
            headers={"retry-after": "3"},
        ),
        streamed(
            event(
                "response.failed",
                response=response("failed", error={"code": "server_error", "message": "panne"}),
            )
        ),
        streamed(event("error", code="context_length_exceeded", message="trop long", param=None)),
        httpx2.Response(
            400,
            json={"error": {"message": "invalide", "type": "invalid_request_error", "code": None}},
        ),
    )
    model = server.model()
    kinds: list[tuple[str, float | None]] = []
    for _ in range(4):
        with pytest.raises(ModelError) as caught:
            await complete(model, request())
        kinds.append((caught.value.kind, caught.value.retry_after))
    assert kinds == [
        ("transient", 3.0),
        ("transient", None),
        ("context_overflow", None),
        ("invalid_request", None),
    ]


def test_strict_schemas() -> None:
    item: dict[str, Any] = {
        "type": "object",
        "properties": {"n": {"type": "integer", "minimum": 0}},
        "required": ["n"],
        "additionalProperties": False,
    }
    strict: dict[str, Any] = {
        "type": "object",
        "properties": {
            "liste": {"type": "array", "items": {"$ref": "#/$defs/item"}},
            "choix": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        },
        "required": ["liste", "choix"],
        "additionalProperties": False,
        "$defs": {"item": item},
    }
    assert is_strict(strict)
    assert not is_strict({**strict, "required": ["liste"]})
    assert not is_strict({**strict, "additionalProperties": True})
    assert not is_strict({**strict, "$defs": {"item": {**item, "additionalProperties": True}}})
    assert not is_strict(
        {**strict, "properties": {**strict["properties"], "x": {"type": "string", "minLength": 1}}}
    )
    assert not is_strict(
        {"type": "object", "properties": {}, "required": "n", "additionalProperties": False}
    )


async def test_failure_without_details_and_an_image_in_an_error_result() -> None:
    server = Server(streamed(event("response.failed", response=response("failed"))), tool_turn())
    with pytest.raises(ModelError) as caught:
        await complete(server.model(), request())
    assert caught.value.kind == "transient"
    failed = ToolOutput(
        blocks=(TextBlock(text="Capture ratée"), InlineDataBlock(media_type="image/png", data=PNG)),
        is_error=True,
    )
    await complete(
        server.model(),
        request(
            Message(role="assistant", blocks=(ToolCallBlock(call_id="c1", name="capture"),)),
            Message(role="tool", blocks=(ToolResultBlock(call_id="c1", output=failed),)),
        ),
    )
    [output] = [
        item["output"]
        for item in server.body["input"]
        if item.get("type") == "function_call_output"
    ]
    assert output[0] == {"type": "input_text", "text": "[erreur]"}
