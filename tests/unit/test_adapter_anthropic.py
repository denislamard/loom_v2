# SPDX-License-Identifier: Apache-2.0
"""Contrat de l'adaptateur ``anthropic`` : requête envoyée, lecture du flux, erreurs.

Les réponses HTTP sont simulées par ``httpx2.MockTransport``, au format SSE
documenté de l'API Messages.
"""

import base64
import json
from collections.abc import Callable
from typing import Any

import pytest

pytest.importorskip("anthropic")

import httpx2

from loom_ia.adapters.models import create_model_client
from loom_ia.adapters.models.anthropic import AnthropicModel
from loom_ia.core.model import (
    AnthropicMeta,
    ArtifactRefBlock,
    InlineDataBlock,
    JsonBlock,
    Message,
    ModelChunk,
    ModelRequest,
    ModelSpec,
    OpenAIMeta,
    PromptCache,
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

type Handler = Callable[[httpx2.Request], httpx2.Response]

SPEC = ModelSpec(
    id="CLAUDE",
    sdk="anthropic",
    model="claude-test",
    base_url="https://anthropic.test",
    api_key_env="TEST_ANTHROPIC_KEY",
)
TOOL = ToolDefinition(
    name="calculer",
    description="Calcule.",
    input_schema={"type": "object", "properties": {"expr": {"type": "string"}}},
)


CITATION = {
    "type": "char_location",
    "cited_text": "x",
    "document_index": 0,
    "document_title": None,
    "start_char_index": 0,
    "end_char_index": 1,
}


def sse(*events: dict[str, Any]) -> str:
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events)


def start(**usage: int) -> dict[str, Any]:
    return {
        "type": "message_start",
        "message": {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "model": "claude-test-20260101",
            "content": [],
            "stop_reason": None,
            "stop_sequence": None,
            "usage": {"input_tokens": 0, "output_tokens": 0, **usage},
        },
    }


def block_start(index: int, block: dict[str, Any]) -> dict[str, Any]:
    return {"type": "content_block_start", "index": index, "content_block": block}


def delta(index: int, value: dict[str, Any]) -> dict[str, Any]:
    return {"type": "content_block_delta", "index": index, "delta": value}


def stop(index: int) -> dict[str, Any]:
    return {"type": "content_block_stop", "index": index}


def end(reason: str, **usage: int) -> list[dict[str, Any]]:
    return [
        {
            "type": "message_delta",
            "delta": {"stop_reason": reason, "stop_sequence": None},
            "usage": {"output_tokens": 0, **usage},
        },
        {"type": "message_stop"},
    ]


class Server:
    """Transport simulé qui enregistre les requêtes reçues."""

    def __init__(self, *responses: httpx2.Response | Handler) -> None:
        self.responses = list(responses)
        self.requests: list[httpx2.Request] = []

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        response = self.responses.pop(0)
        return response(request) if callable(response) else response

    @property
    def body(self) -> dict[str, Any]:
        return json.loads(self.requests[-1].content)

    def model(self, spec: ModelSpec = SPEC) -> AnthropicModel:
        client = create_model_client(
            spec,
            environ={"TEST_ANTHROPIC_KEY": "sk-test"},
            http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(self.handle)),
        )
        assert isinstance(client, AnthropicModel)
        return client


def streamed(*events: dict[str, Any]) -> httpx2.Response:
    return httpx2.Response(200, text=sse(*events), headers={"content-type": "text/event-stream"})


def request(*messages: Message, **options: Any) -> ModelRequest:
    return ModelRequest.model_validate(
        {"model_id": "claude-test", "messages": messages or (Message.user("?"),), **options}
    )


async def test_stream_is_read_in_order() -> None:
    server = Server(
        streamed(
            start(input_tokens=120, cache_read_input_tokens=1000, cache_creation_input_tokens=50),
            block_start(0, {"type": "thinking", "thinking": "", "signature": ""}),
            delta(0, {"type": "thinking_delta", "thinking": "Je pose "}),
            delta(0, {"type": "thinking_delta", "thinking": "le calcul."}),
            delta(0, {"type": "signature_delta", "signature": "sig-1"}),
            stop(0),
            block_start(1, {"type": "redacted_thinking", "data": "chiffré"}),
            stop(1),
            block_start(2, {"type": "text", "text": ""}),
            delta(2, {"type": "text_delta", "text": "Je "}),
            delta(2, {"type": "text_delta", "text": "calcule."}),
            {"type": "ping"},
            stop(2),
            block_start(3, {"type": "tool_use", "id": "toolu_1", "name": "calculer", "input": {}}),
            delta(3, {"type": "input_json_delta", "partial_json": '{"expr": '}),
            delta(3, {"type": "input_json_delta", "partial_json": '"12*7+3"}'}),
            stop(3),
            *end("tool_use", output_tokens=85),
        )
    )
    chunks: list[ModelChunk] = []

    async def on_chunk(chunk: ModelChunk) -> None:
        chunks.append(chunk)

    model = server.model()
    response = await complete(model, request(), on_chunk=on_chunk)

    signed: dict[str, ProviderMeta] = {"anthropic": AnthropicMeta(signature="sig-1")}
    redacted: dict[str, ProviderMeta] = {"anthropic": AnthropicMeta(redacted_data="chiffré")}
    assert response.message.blocks == (
        # Le raisonnement est marqué du modèle qui l'a produit (#7).
        ReasoningBlock(text="Je pose le calcul.", provider_meta=signed, model_id="claude-test"),
        ReasoningBlock(provider_meta=redacted, model_id="claude-test"),
        TextBlock(text="Je calcule."),
        ToolCallBlock(call_id="toolu_1", name="calculer", arguments={"expr": "12*7+3"}),
    )
    assert response.stop_reason == "tool_use"
    assert response.model_id == "claude-test-20260101"
    assert response.provider == "anthropic"
    assert response.usage == Usage(
        input_tokens=120, output_tokens=85, cache_read_tokens=1000, cache_write_tokens=50
    )
    assert [c.type for c in chunks][-2:] == ["usage", "stopped"]
    assert repr(model) == "AnthropicModel('CLAUDE', model='claude-test')"
    sent = server.requests[0]
    assert sent.url == "https://anthropic.test/v1/messages"
    assert sent.headers["x-api-key"] == "sk-test"
    await model.aclose()


async def test_request_translation() -> None:
    server = Server(streamed(start(), *end("end_turn")))
    signed: dict[str, ProviderMeta] = {"anthropic": AnthropicMeta(signature="sig")}
    hidden: dict[str, ProviderMeta] = {"anthropic": AnthropicMeta(redacted_data="xyz")}
    unsigned: dict[str, ProviderMeta] = {"anthropic": AnthropicMeta()}
    foreign: dict[str, ProviderMeta] = {"openai": OpenAIMeta(encrypted_content="x")}
    history = (
        Message.user("Combien ?"),
        Message(role="assistant", blocks=(TextBlock(text=""),)),
        Message(
            role="assistant",
            blocks=(
                ReasoningBlock(text="calcul", provider_meta=signed),
                ReasoningBlock(provider_meta=hidden),
                ReasoningBlock(text="incomplet", provider_meta=unsigned),
                ReasoningBlock(text="autre fournisseur", provider_meta=foreign),
                ReasoningBlock(text="sans signature"),
                TextBlock(text=""),
                ToolCallBlock(call_id="t1", name="calculer", arguments={"expr": "1+1"}),
                ToolCallBlock(call_id="t2", name="calculer", arguments={"expr": "x"}),
            ),
        ),
        Message(
            role="tool",
            blocks=(
                ToolResultBlock(call_id="t1", output=ToolOutput.text("2")),
                ToolResultBlock(call_id="t2", output=ToolOutput.error("expression invalide")),
            ),
        ),
        Message(role="user", blocks=(JsonBlock(data={"suite": True}),)),
        Message(role="tool", blocks=(ToolResultBlock(call_id="t3", output=ToolOutput()),)),
    )
    await complete(
        server.model(),
        request(
            *history,
            system="Tu calcules.",
            tools=(TOOL,),
            max_tokens=256,
            params={"temperature": 0, "thinking": {"type": "enabled", "budget_tokens": 1024}},
        ),
    )

    body = server.body
    assert body["model"] == "claude-test"
    assert body["system"] == "Tu calcules."
    assert body["max_tokens"] == 256
    assert body["stream"] is True
    assert body["temperature"] == 0
    assert body["thinking"] == {"type": "enabled", "budget_tokens": 1024}
    assert body["tools"] == [TOOL.model_dump()]
    assert body["tool_choice"] == {"type": "auto"}
    assert body["messages"] == [
        {"role": "user", "content": [{"type": "text", "text": "Combien ?"}]},
        {
            "role": "assistant",
            "content": [
                {"type": "thinking", "thinking": "calcul", "signature": "sig"},
                {"type": "redacted_thinking", "data": "xyz"},
                {"type": "tool_use", "id": "t1", "name": "calculer", "input": {"expr": "1+1"}},
                {"type": "tool_use", "id": "t2", "name": "calculer", "input": {"expr": "x"}},
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "t1",
                    "is_error": False,
                    "content": [{"type": "text", "text": "2"}],
                },
                {
                    "type": "tool_result",
                    "tool_use_id": "t2",
                    "is_error": True,
                    "content": [{"type": "text", "text": "expression invalide"}],
                },
                {"type": "text", "text": '{"suite": true}'},
                {"type": "tool_result", "tool_use_id": "t3", "is_error": False},
            ],
        },
    ]


async def test_defaults_without_tools_and_forced_answer() -> None:
    server = Server(
        streamed(start(), *end("end_turn")),
        streamed(start(), *end("max_tokens")),
    )
    model = server.model()
    response = await complete(model, request())
    body = server.body
    assert body["max_tokens"] == 4096
    assert not {"system", "tools", "tool_choice"} & body.keys()
    assert response.message == Message.assistant("")
    assert response.stop_reason == "end"

    spec = SPEC.model_copy(update={"max_tokens": 1000})
    server.responses.append(streamed(start(), *end("refusal")))
    forced = await complete(server.model(spec), request(tools=(TOOL,), tool_choice="none"))
    assert server.body["tool_choice"] == {"type": "none"}
    assert server.body["max_tokens"] == 1000
    assert forced.stop_reason == "max_tokens"


async def test_required_tool_choice_becomes_any() -> None:
    server = Server(streamed(start(), *end("tool_use")))
    await complete(server.model(), request(tools=(TOOL,), tool_choice="required"))
    assert server.body["tool_choice"] == {"type": "any"}


async def test_unknown_blocks_are_ignored() -> None:
    server = Server(
        streamed(
            start(),
            block_start(0, {"type": "server_tool_use", "id": "s1", "name": "web", "input": {}}),
            delta(0, {"type": "input_json_delta", "partial_json": "{}"}),
            stop(0),
            block_start(1, {"type": "text", "text": "Voilà"}),
            delta(1, {"type": "citations_delta", "citation": CITATION}),
            stop(1),
            *end("pause_turn"),
        )
    )
    response = await complete(server.model(), request())
    assert response.message == Message.assistant("Voilà")
    assert response.stop_reason == "end"


async def test_non_streaming_models() -> None:
    body: dict[str, Any] = {
        "id": "msg_2",
        "type": "message",
        "role": "assistant",
        "model": "claude-test-20260101",
        "content": [
            {"type": "thinking", "thinking": "réflexion", "signature": "s2"},
            {"type": "redacted_thinking", "data": "masqué"},
            {"type": "text", "text": "Réponse"},
            {"type": "tool_use", "id": "toolu_9", "name": "calculer", "input": {"expr": "2"}},
            {"type": "server_tool_use", "id": "s1", "name": "web_search", "input": {}},
        ],
        "stop_reason": "tool_use",
        "stop_sequence": None,
        "usage": {"input_tokens": 10, "output_tokens": 20},
    }
    server = Server(httpx2.Response(200, json=body))
    spec = ModelSpec.model_validate({**SPEC.model_dump(), "capabilities": {"streaming": False}})
    response = await complete(server.model(spec), request())
    assert "stream" not in server.body
    assert [b.type for b in response.message.blocks] == [
        "reasoning",
        "reasoning",
        "text",
        "tool_call",
    ]
    assert response.message.tool_calls[0].arguments == {"expr": "2"}
    assert response.usage == Usage(input_tokens=10, output_tokens=20)
    assert response.stop_reason == "tool_use"
    assert response.model_id == "claude-test-20260101"


def api_error(status: int, error_type: str, message: str, **headers: str) -> httpx2.Response:
    return httpx2.Response(
        status,
        json={"type": "error", "error": {"type": error_type, "message": message}},
        headers=headers,
    )


@pytest.mark.parametrize(
    ("response", "kind", "status", "retry"),
    [
        (api_error(529, "overloaded_error", "Overloaded"), "overloaded", 529, None),
        (api_error(429, "rate_limit_error", "Slow", **{"retry-after": "3"}), "transient", 429, 3.0),
        (api_error(500, "api_error", "Boom"), "transient", 500, None),
        (api_error(401, "authentication_error", "invalid x-api-key"), "auth", 401, None),
        (
            api_error(400, "invalid_request_error", "prompt is too long: 250000 tokens"),
            "context_overflow",
            400,
            None,
        ),
        (
            api_error(400, "billing_error", "credit balance is too low"),
            "quota_exhausted",
            400,
            None,
        ),
        (api_error(404, "not_found_error", "model: inconnu"), "invalid_request", 404, None),
        (
            streamed(
                start(),
                block_start(0, {"type": "text", "text": "Déb"}),
                {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}},
            ),
            "overloaded",
            None,
            None,
        ),
    ],
)
async def test_errors_are_classified(
    response: httpx2.Response, kind: str, status: int | None, retry: float | None
) -> None:
    server = Server(response)
    with pytest.raises(ModelError) as caught:
        await complete(server.model(), request())
    error = caught.value
    assert (error.kind, error.http_status, error.retry_after) == (kind, status, retry)
    assert error.__cause__ is not None


async def test_network_errors_are_transient() -> None:
    def fail(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("connexion refusée", request=request)

    with pytest.raises(ModelError) as caught:
        await complete(Server(fail).model(), request())
    assert (caught.value.kind, caught.value.http_status) == ("transient", None)


async def test_attachments_are_refused_before_calling() -> None:
    server = Server()
    image = ArtifactRefBlock(uri="file://photo.png", media_type="image/png")
    with pytest.raises(ModelError, match=r"photo\.png") as caught:
        await complete(server.model(), request(Message(role="user", blocks=(image,))))
    assert caught.value.kind == "invalid_request"
    assert server.requests == []


async def test_official_address_without_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    # La config décide seule où partent les requêtes : la variable du SDK est ignorée.
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://ailleurs.test")
    server = Server(streamed(start(), *end("end_turn")))
    await complete(server.model(SPEC.model_copy(update={"base_url": None})), request())
    assert str(server.requests[0].url) == "https://api.anthropic.com/v1/messages"


async def test_images_are_sent_in_base64() -> None:
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8
    encoded = base64.b64encode(png).decode()
    image = InlineDataBlock(media_type="image/png", data=png, name="photo.png")
    result = ToolResultBlock(
        call_id="t1",
        output=ToolOutput(blocks=(TextBlock(text="[result:1]"), TextBlock(text="voici"), image)),
    )
    server = Server(streamed(start(), *end("end_turn")))
    await complete(
        server.model(),
        request(
            Message(role="user", blocks=(TextBlock(text="Regarde"), image)),
            Message(role="assistant", blocks=(ToolCallBlock(call_id="t1", name="tracer"),)),
            Message(role="tool", blocks=(result,)),
        ),
    )
    source = {"type": "base64", "media_type": "image/png", "data": encoded}
    user, _, tool = server.body["messages"]
    assert user["content"] == [
        {"type": "text", "text": "Regarde"},
        {"type": "image", "source": source},
    ]
    # Dans un résultat d'outil : les textes réunis, puis l'image à sa place.
    assert tool["content"][0]["content"] == [
        {"type": "text", "text": "[result:1]\nvoici"},
        {"type": "image", "source": source},
    ]

    pdf = InlineDataBlock(media_type="application/pdf", data=b"%PDF")
    with pytest.raises(ModelError, match="seules les images"):
        await complete(server.model(), request(Message(role="user", blocks=(pdf,))))


# --- Cache de prompt (B5) et schéma natif (B9) -----------------------------------------------


def cached_spec(**cache: Any) -> ModelSpec:
    return SPEC.model_copy(update={"cache": PromptCache.model_validate(cache)})


def answered() -> httpx2.Response:
    return streamed(
        start(), block_start(0, {"type": "text", "text": ""}), stop(0), *end("end_turn")
    )


EPHEMERAL: dict[str, Any] = {"type": "ephemeral"}


async def test_cache_points_on_tools_system_and_last_message() -> None:
    server = Server(answered(), answered())
    history = (
        Message.user("Bonjour"),
        Message(
            role="assistant",
            blocks=(
                ReasoningBlock(
                    text="calcul", provider_meta={"anthropic": AnthropicMeta(signature="s")}
                ),
                ToolCallBlock(call_id="t1", name="calculer", arguments={"expr": "1"}),
            ),
        ),
        Message(role="tool", blocks=(ToolResultBlock(call_id="t1", output=ToolOutput.text("1")),)),
    )
    await complete(
        server.model(cached_spec(system=True, tools=True, messages=True)),
        request(*history, system="Tu calcules.", tools=(TOOL,)),
    )
    body = server.body
    assert body["system"] == [{"type": "text", "text": "Tu calcules.", "cache_control": EPHEMERAL}]
    assert body["tools"][-1]["cache_control"] == EPHEMERAL
    # Le point de la conversation avance : sur le dernier bloc.
    last = body["messages"][-1]["content"][-1]
    assert (last["type"], last["cache_control"]) == ("tool_result", EPHEMERAL)
    others = [b for m in body["messages"][:-1] for b in m["content"]]
    assert all("cache_control" not in b for b in others)

    # Une heure ; sans réglage « messages », rien sur la conversation.
    await complete(server.model(cached_spec(system=True, ttl="1h")), request(*history, system="S"))
    body = server.body
    assert body["system"][0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    assert all("cache_control" not in b for m in body["messages"] for b in m["content"])


async def test_marked_blocks_and_the_four_points_limit() -> None:
    server = Server(answered(), answered())
    marked = [
        Message(role="user", blocks=(TextBlock(text=f"partie {n}", cache_breakpoint=True),))
        for n in range(3)
    ]
    thinking = Message(
        role="assistant",
        blocks=(
            ReasoningBlock(
                text="x",
                provider_meta={"anthropic": AnthropicMeta(signature="s")},
                cache_breakpoint=True,
            ),
            TextBlock(text="suite"),
        ),
    )
    # Sans réglage cache, les marques suffisent ; jamais sur un raisonnement.
    await complete(server.model(), request(marked[0], thinking, Message.user("?")))
    body = server.body
    assert body["messages"][0]["content"][0]["cache_control"] == EPHEMERAL
    assert all("cache_control" not in b for b in body["messages"][1]["content"])

    await complete(
        server.model(cached_spec(system=True, tools=True, messages=True)),
        request(*marked, Message.user("fin"), system="S", tools=(TOOL,)),
    )
    body = server.body
    placed = [
        n
        for n, message in enumerate(body["messages"])
        for b in message["content"]
        if "cache_control" in b
    ]
    # Système, outils, dernier message, puis la marque la plus récente : quatre au plus.
    assert "cache_control" in body["system"][0] and "cache_control" in body["tools"][0]
    assert len(body["messages"]) == 1
    contents = body["messages"][0]["content"]
    assert [("cache_control" in b) for b in contents] == [False, False, True, True]
    assert placed == [0, 0]


async def test_output_schema_goes_to_output_config_with_native_json() -> None:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {"objet": {"type": "string", "minLength": 5}},
        "required": ["objet"],
    }
    server = Server(answered(), answered(), answered())
    await complete(server.model(), request(output_schema=schema))
    assert "output_config" not in server.body
    native = SPEC.model_copy(
        update={"capabilities": SPEC.capabilities.model_copy(update={"native_json": True})}
    )
    await complete(
        server.model(native),
        request(output_schema=schema, params={"output_config": {"effort": "low"}}),
    )
    config = server.body["output_config"]
    assert config["effort"] == "low"
    # Adapté par le SDK : objet fermé, contrainte non prise en charge reportée en description.
    assert config["format"] == {
        "type": "json_schema",
        "schema": {
            "type": "object",
            "properties": {"objet": {"type": "string", "description": "{minLength: 5}"}},
            "additionalProperties": False,
            "required": ["objet"],
        },
    }
    # Schéma que le SDK ne sait pas adapter : non transmis, l'appel part quand même.
    await complete(server.model(native), request(output_schema={"properties": {}}))
    assert "output_config" not in server.body
