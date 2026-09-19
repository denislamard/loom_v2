# SPDX-License-Identifier: Apache-2.0
"""Contrat de l'adaptateur ``openai`` (``chat``) : requête envoyée, lecture du flux, erreurs.

Les réponses HTTP sont simulées par ``httpx2.MockTransport``, au format SSE de
l'API Chat Completions, avec les champs ajoutés par les fournisseurs compatibles.
"""

import base64
import json
from typing import Any

import pytest

pytest.importorskip("openai")

import httpx2

from loom_ia.adapters.models import NO_API_KEY, create_model_client
from loom_ia.adapters.models.openai_chat import OpenAIChatModel
from loom_ia.core.model import (
    INVALID_JSON_KEY,
    AnthropicMeta,
    ArtifactRefBlock,
    InlineDataBlock,
    JsonBlock,
    Message,
    ModelRequest,
    ModelSpec,
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
    id="OSS",
    sdk="openai",
    model="oss-120b",
    base_url="https://together.test/v1",
    api_key_env="TEST_OPENAI_KEY",
)
TOOL = ToolDefinition(
    name="calculer",
    description="Calcule.",
    input_schema={"type": "object", "properties": {"expr": {"type": "string"}}},
)


def chunk(delta: dict[str, Any] | None = None, finish: str | None = None, **extra: Any) -> str:
    choices = [] if delta is None else [{"index": 0, "delta": delta, "finish_reason": finish}]
    body = {
        "id": "c1",
        "object": "chat.completion.chunk",
        "created": 1,
        "model": "oss-120b-v2",
        "choices": choices,
        **extra,
    }
    return f"data: {json.dumps(body)}\n\n"


def streamed(*parts: str) -> httpx2.Response:
    text = "".join(parts) + "data: [DONE]\n\n"
    return httpx2.Response(200, text=text, headers={"content-type": "text/event-stream"})


def usage_chunk(prompt: int, completion: int, cached: int = 0, reasoning: int = 0) -> str:
    usage = {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
        "prompt_tokens_details": {"cached_tokens": cached},
        "completion_tokens_details": {"reasoning_tokens": reasoning},
    }
    return chunk(usage=usage)


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

    def model(self, spec: ModelSpec = SPEC) -> OpenAIChatModel:
        client = create_model_client(
            spec,
            environ={"TEST_OPENAI_KEY": "sk-test"},
            http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(self.handle)),
        )
        assert isinstance(client, OpenAIChatModel)
        return client


def request(*messages: Message, **options: Any) -> ModelRequest:
    return ModelRequest.model_validate(
        {"model_id": "oss-120b", "messages": messages or (Message.user("?"),), **options}
    )


async def test_stream_is_read_in_order() -> None:
    server = Server(
        streamed(
            chunk({"role": "assistant", "content": ""}),
            chunk({"reasoning_content": "Je pose "}),
            chunk({"reasoning_content": "le calcul."}),
            chunk({"content": "Je "}),
            chunk({"content": "calcule."}),
            chunk(
                {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "call_a",
                            "type": "function",
                            "function": {"name": "calculer", "arguments": ""},
                        }
                    ]
                }
            ),
            chunk({"tool_calls": [{"index": 0, "function": {"arguments": '{"expr": '}}]}),
            chunk({"tool_calls": [{"index": 1, "function": {"name": "calculer"}}]}),
            chunk({"tool_calls": [{"index": 0, "function": {"arguments": '"1+1"}'}}]}),
            chunk({"tool_calls": [{"index": 1, "function": {"arguments": "{}"}}]}),
            chunk({}, finish="stop"),
            usage_chunk(1200, 90, cached=1000, reasoning=40),
        )
    )
    model = server.model()
    response = await complete(model, request())

    blocks = response.message.blocks
    assert blocks[:3] == (
        ReasoningBlock(text="Je pose le calcul."),
        TextBlock(text="Je calcule."),
        ToolCallBlock(call_id="call_a", name="calculer", arguments={"expr": "1+1"}),
    )
    generated = blocks[3]
    assert isinstance(generated, ToolCallBlock)
    assert generated.call_id.startswith("call_") and generated.name == "calculer"
    # « stop » avec des appels d'outils : le modèle demande bien des outils.
    assert response.stop_reason == "tool_use"
    assert response.model_id == "oss-120b-v2"
    assert response.provider == "openai"
    assert response.usage == Usage(
        input_tokens=200, output_tokens=90, cache_read_tokens=1000, reasoning_tokens=40
    )
    sent = server.requests[0]
    assert sent.url == "https://together.test/v1/chat/completions"
    assert sent.headers["authorization"] == "Bearer sk-test"
    assert repr(model) == "OpenAIChatModel('OSS', model='oss-120b')"
    await model.aclose()


async def test_request_translation() -> None:
    server = Server(streamed(chunk({"content": "ok"}, finish="stop")))
    signed: dict[str, ProviderMeta] = {"anthropic": AnthropicMeta(signature="sig")}
    history = (
        Message(role="user", blocks=(TextBlock(text="Combien ?"), JsonBlock(data={"n": 2}))),
        Message(
            role="assistant",
            blocks=(
                ReasoningBlock(text="jamais renvoyé", provider_meta=signed),
                ToolCallBlock(call_id="t1", name="calculer", arguments={"expr": "1+1"}),
                ToolCallBlock(call_id="t2", name="calculer", arguments={INVALID_JSON_KEY: "{"}),
            ),
        ),
        Message(
            role="tool",
            blocks=(
                ToolResultBlock(call_id="t1", output=ToolOutput(data={"valeur": 2})),
                ToolResultBlock(call_id="t2", output=ToolOutput.error("JSON illisible")),
            ),
        ),
        Message(role="assistant", blocks=(ReasoningBlock(text="..."), TextBlock(text="2"))),
    )
    await complete(
        server.model(),
        request(
            *history,
            system="Tu calcules.",
            tools=(TOOL,),
            max_tokens=300,
            params={"temperature": 0.2, "top_k": 20},
        ),
    )

    body = server.body
    assert body["model"] == "oss-120b"
    assert body["stream"] is True
    assert body["stream_options"] == {"include_usage": True}
    assert body["max_tokens"] == 300
    assert (body["temperature"], body["top_k"]) == (0.2, 20)
    assert body["tool_choice"] == "auto"
    assert body["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "calculer",
                "description": "Calcule.",
                "parameters": TOOL.input_schema,
            },
        }
    ]
    assert body["messages"] == [
        {"role": "system", "content": "Tu calcules."},
        {"role": "user", "content": 'Combien ?\n\n{"n": 2}'},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "t1",
                    "type": "function",
                    "function": {"name": "calculer", "arguments": '{"expr": "1+1"}'},
                },
                {
                    "id": "t2",
                    "type": "function",
                    "function": {"name": "calculer", "arguments": '{"_loom_invalid_json": "{"}'},
                },
            ],
        },
        {"role": "tool", "tool_call_id": "t1", "content": '{"valeur": 2}'},
        {"role": "tool", "tool_call_id": "t2", "content": "[erreur] JSON illisible"},
        {"role": "assistant", "content": "2"},
    ]


async def test_defaults_and_forced_answer() -> None:
    server = Server(
        streamed(chunk({"content": "tronqué"}, finish="length")),
        streamed(chunk({"refusal": "Je ne peux pas."}), chunk({}, finish="stop")),
    )
    model = server.model()
    truncated = await complete(model, request())
    assert not {"tools", "tool_choice", "max_tokens"} & server.body.keys()
    assert truncated.stop_reason == "max_tokens"
    assert truncated.usage == Usage()

    refused = await complete(model, request(tools=(TOOL,), tool_choice="none"))
    assert server.body["tool_choice"] == "none"
    assert refused.stop_reason == "refusal"
    assert refused.message == Message.assistant("Je ne peux pas.")


async def test_other_choices_and_reasoning_field() -> None:
    server = Server(
        streamed(
            chunk({"reasoning": "pensée"}),
            'data: {"id":"c1","object":"chat.completion.chunk","created":1,"model":"m",'
            '"choices":[{"index":1,"delta":{"content":"ignoré"},"finish_reason":null}]}\n\n',
            chunk({"content": "Réponse"}, finish="stop"),
        )
    )
    response = await complete(server.model(), request())
    assert response.message.blocks == (ReasoningBlock(text="pensée"), TextBlock(text="Réponse"))
    assert response.stop_reason == "end"


async def test_non_streaming_models() -> None:
    completion: dict[str, Any] = {
        "id": "c2",
        "object": "chat.completion",
        "created": 1,
        "model": "oss-120b-v2",
        "choices": [
            {
                "index": 0,
                "finish_reason": "tool_calls",
                "message": {
                    "role": "assistant",
                    "content": "Je calcule.",
                    "reasoning_content": "réflexion",
                    "tool_calls": [
                        {
                            "id": "call_z",
                            "type": "function",
                            "function": {"name": "calculer", "arguments": '{"expr": "3"}'},
                        },
                        {"id": "call_c", "type": "custom", "custom": {"name": "x", "input": "y"}},
                    ],
                },
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }
    server = Server(httpx2.Response(200, json=completion))
    spec = ModelSpec.model_validate({**SPEC.model_dump(), "capabilities": {"streaming": False}})
    response = await complete(server.model(spec), request())
    assert "stream" not in server.body
    assert response.message.blocks == (
        ReasoningBlock(text="réflexion"),
        TextBlock(text="Je calcule."),
        ToolCallBlock(call_id="call_z", name="calculer", arguments={"expr": "3"}),
    )
    assert response.stop_reason == "tool_use"
    assert response.usage == Usage(input_tokens=10, output_tokens=5)

    empty: dict[str, Any] = {**completion, "choices": [], "usage": None}
    server.responses.append(httpx2.Response(200, json=empty))
    nothing = await complete(server.model(spec), request())
    assert nothing.message == Message.assistant("")


def api_error(status: int, code: str | None, message: str, **headers: str) -> httpx2.Response:
    error = {"message": message, "type": "invalid_request_error", "code": code}
    return httpx2.Response(status, json={"error": error}, headers=headers)


@pytest.mark.parametrize(
    ("response", "kind", "status", "retry"),
    [
        (api_error(429, "insufficient_quota", "quota"), "quota_exhausted", 429, None),
        (
            api_error(429, "rate_limit_exceeded", "lent", **{"retry-after-ms": "1500"}),
            "transient",
            429,
            1.5,
        ),
        (api_error(400, "context_length_exceeded", "trop long"), "context_overflow", 400, None),
        (api_error(400, "content_filter", "filtré"), "content_filtered", 400, None),
        (api_error(401, "invalid_api_key", "clé"), "auth", 401, None),
        (api_error(503, None, "surchargé"), "overloaded", 503, None),
        (api_error(502, None, "passerelle"), "transient", 502, None),
        (api_error(422, None, "schéma"), "invalid_request", 422, None),
        (
            streamed(
                chunk({"content": "Déb"}),
                'data: {"error": {"message": "backend down", "type": "server_error"}}\n\n',
            ),
            "transient",
            None,
            None,
        ),
    ],
)
async def test_errors_are_classified(
    response: httpx2.Response, kind: str, status: int | None, retry: float | None
) -> None:
    with pytest.raises(ModelError) as caught:
        await complete(Server(response).model(), request())
    error = caught.value
    assert (error.kind, error.http_status, error.retry_after) == (kind, status, retry)


async def test_servers_without_key() -> None:
    server = Server(streamed(chunk({"content": "ok"}, finish="stop")))
    local = SPEC.model_copy(update={"api_key_env": None, "base_url": "http://vllm.test/v1"})
    await complete(server.model(local), request())
    assert server.requests[0].headers["authorization"] == f"Bearer {NO_API_KEY}"


async def test_unresolved_references_are_refused_before_calling() -> None:
    server = Server()
    image = ArtifactRefBlock(uri="file://photo.png", media_type="image/png")
    with pytest.raises(ModelError, match="non résolue"):
        await complete(server.model(), request(Message(role="user", blocks=(image,))))
    assert server.requests == []


async def test_official_address_without_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    # La config décide seule où partent les requêtes : la variable du SDK est ignorée.
    monkeypatch.setenv("OPENAI_BASE_URL", "https://ailleurs.test/v1")
    server = Server(streamed(chunk({"content": "ok"}, finish="stop")))
    await complete(server.model(SPEC.model_copy(update={"base_url": None})), request())
    assert str(server.requests[0].url) == "https://api.openai.com/v1/chat/completions"


async def test_images_go_in_user_content_parts() -> None:
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8
    image = InlineDataBlock(media_type="image/png", data=png)
    server = Server(streamed(chunk({"content": "ok"}, finish="stop")))
    await complete(
        server.model(),
        request(
            Message(role="user", blocks=(TextBlock(text="Regarde"), TextBlock(text=""), image))
        ),
    )
    url = f"data:image/png;base64,{base64.b64encode(png).decode()}"
    assert server.body["messages"] == [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Regarde"},
                {"type": "image_url", "image_url": {"url": url}},
            ],
        }
    ]
    # L'API n'accepte pas d'image dans un résultat d'outil.
    result = ToolResultBlock(call_id="t1", output=ToolOutput(blocks=(image,)))
    with pytest.raises(ModelError, match="tool_result_media: false"):
        await complete(
            server.model(),
            request(
                Message(role="assistant", blocks=(ToolCallBlock(call_id="t1", name="tracer"),)),
                Message(role="tool", blocks=(result,)),
            ),
        )
