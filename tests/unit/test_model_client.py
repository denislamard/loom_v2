# SPDX-License-Identifier: Apache-2.0
"""Port des modèles : requête, accumulation du flux, tarifs, faux modèle."""

import pytest
from pydantic import ValidationError

from loom_ia.core.model import (
    INVALID_JSON_KEY,
    AnthropicMeta,
    Message,
    ModelChunk,
    ModelRequest,
    Pricing,
    ProviderMeta,
    ReasoningBlock,
    ReasoningDelta,
    ResponseAccumulator,
    Stopped,
    TextBlock,
    TextDelta,
    ToolArgsDelta,
    ToolCallBlock,
    ToolCallEnded,
    ToolCallStarted,
    ToolDefinition,
    ToolSpec,
    Usage,
    UsageDelta,
)
from loom_ia.core.ports import complete
from loom_ia.testing import ScriptedModel, ScriptExhausted, message_to_chunks, tool_call_message


def accumulate(*chunks: ModelChunk) -> ResponseAccumulator:
    accumulator = ResponseAccumulator()
    for chunk in chunks:
        accumulator.add(chunk)
    return accumulator


def test_request_hash_is_stable_and_sensitive() -> None:
    request = ModelRequest(
        model_id="m", messages=(Message.user("Bonjour"),), params={"b": 1, "a": 2}
    )
    same = ModelRequest(model_id="m", messages=(Message.user("Bonjour"),), params={"a": 2, "b": 1})
    other = request.model_copy(update={"tool_choice": "none"})
    assert request.request_hash() == same.request_hash()
    assert request.request_hash() != other.request_hash()
    assert len(request.request_hash()) == 64


def test_accumulator_keeps_block_order() -> None:
    meta: dict[str, ProviderMeta] = {"anthropic": AnthropicMeta(signature="sig")}
    response = accumulate(
        ReasoningDelta(text="je "),
        ReasoningDelta(text="calcule", provider_meta=meta),
        TextDelta(text="Je "),
        TextDelta(text="lance"),
        ToolCallStarted(index=0, call_id="c1", name="calculer"),
        ToolCallStarted(index=1, call_id="c2", name="meteo"),
        ToolArgsDelta(index=1, json_fragment='{"ville"'),
        ToolArgsDelta(index=0, json_fragment='{"expr": "1+1"}'),
        ToolArgsDelta(index=1, json_fragment=': "Lyon"}'),
        ToolCallEnded(index=0),
        ToolCallEnded(index=1),
        TextDelta(text="Fin"),
    ).result(model_id="m", provider="fake")

    assert response.message.blocks == (
        ReasoningBlock(text="je calcule", provider_meta=meta),
        TextBlock(text="Je lance"),
        ToolCallBlock(call_id="c1", name="calculer", arguments={"expr": "1+1"}),
        ToolCallBlock(call_id="c2", name="meteo", arguments={"ville": "Lyon"}),
        TextBlock(text="Fin"),
    )
    assert response.stop_reason == "tool_use"
    assert response.model_id == "m"
    assert response.provider == "fake"


def test_accumulator_sums_usage_and_reads_stop() -> None:
    response = accumulate(
        TextDelta(text="ok"),
        UsageDelta(usage=Usage(input_tokens=10)),
        UsageDelta(usage=Usage(output_tokens=3, reasoning_tokens=1)),
        Stopped(reason="max_tokens", model_id="m-2024"),
    ).result(model_id="m", provider="fake")
    assert response.usage == Usage(input_tokens=10, output_tokens=3, reasoning_tokens=1)
    assert response.stop_reason == "max_tokens"
    assert response.model_id == "m-2024"


@pytest.mark.parametrize("raw", ['{"expr":', "[1, 2]"])
def test_unreadable_arguments_are_kept_raw(raw: str) -> None:
    response = accumulate(
        ToolCallStarted(index=0, call_id="c1", name="calculer"),
        ToolArgsDelta(index=0, json_fragment=raw),
    ).result(model_id="m", provider="fake")
    assert response.message.tool_calls[0].arguments == {INVALID_JSON_KEY: raw}


def test_missing_arguments_mean_an_empty_object() -> None:
    response = accumulate(ToolCallStarted(index=0, call_id="c1", name="heure")).result(
        model_id="m", provider="fake"
    )
    assert response.message.tool_calls[0].arguments == {}


def test_empty_stream_gives_an_empty_text() -> None:
    response = accumulate().result(model_id="m", provider="fake")
    assert response.message == Message(role="assistant", blocks=(TextBlock(text=""),))
    assert response.stop_reason == "end"


def test_pricing_cost() -> None:
    pricing = Pricing(input=1.0, output=5.0, cache_read=0.1, cache_write=1.25)
    usage = Usage(
        input_tokens=1_000,
        output_tokens=200,
        cache_read_tokens=10_000,
        cache_write_tokens=2_000,
        reasoning_tokens=150,
    )
    assert pricing.cost(usage) == pytest.approx((1_000 + 1_000 + 1_000 + 2_500) / 1e6)
    assert Pricing().cost(usage) == 0.0


def test_tool_definitions() -> None:
    spec = ToolSpec(name="envoyer", description="Envoie.", kind="python", side_effects="reversible")
    assert not spec.safe_to_retry
    assert spec.model_copy(update={"idempotent": True}).safe_to_retry
    assert ToolSpec(name="lire", description="Lit.", kind="mcp").safe_to_retry
    assert spec.definition() == ToolDefinition(
        name="envoyer",
        description="Envoie.",
        input_schema={"type": "object", "properties": {}},
    )
    with pytest.raises(ValidationError):
        ToolDefinition(name="nom invalide", description="x")


async def test_complete_rebuilds_the_scripted_message() -> None:
    meta: dict[str, ProviderMeta] = {"anthropic": AnthropicMeta(signature="s")}
    message = Message(
        role="assistant",
        blocks=(
            ReasoningBlock(text="un raisonnement assez long", provider_meta=meta),
            TextBlock(text="Je calcule, puis je réponds."),
            ToolCallBlock(call_id="c1", name="calculer", arguments={"expr": "12*7+3"}),
            ToolCallBlock(call_id="c2", name="heure"),
        ),
    )
    model = ScriptedModel(message, fragment_size=3)
    seen: list[ModelChunk] = []

    async def on_chunk(chunk: ModelChunk) -> None:
        seen.append(chunk)

    request = ModelRequest(model_id="fake-1", messages=(Message.user("?"),))
    response = await complete(model, request, on_chunk=on_chunk)

    assert response.message == message
    assert response.usage == model.usage
    assert response.provider == "fake"
    assert response.model_id == "fake-1"
    assert seen == message_to_chunks(message, usage=model.usage, fragment_size=3)
    assert model.requests == [request]
    assert model.remaining == 0


async def test_scripted_model_errors_and_callables() -> None:
    model = ScriptedModel(
        RuntimeError("panne"),
        lambda request: Message.assistant(f"{len(request.messages)} message(s)"),
        delay=0.001,
    )
    request = ModelRequest(model_id="m", messages=(Message.user("?"),))
    with pytest.raises(RuntimeError, match="panne"):
        await complete(model, request)
    assert (await complete(model, request)).message.text == "1 message(s)"
    with pytest.raises(ScriptExhausted):
        await complete(model, request)
    model.add(tool_call_message(("c1", "t", {})))
    assert (await complete(model, request)).stop_reason == "tool_use"


def test_message_to_chunks_rejects_foreign_blocks() -> None:
    message = Message.model_validate(
        {"role": "assistant", "blocks": [{"type": "json", "data": {"a": 1}}]}
    )
    with pytest.raises(ValueError, match="json"):
        message_to_chunks(message)
    chunks = message_to_chunks(Message.assistant(""), stop_reason="refusal")
    assert chunks == [TextDelta(text=""), Stopped(reason="refusal")]
