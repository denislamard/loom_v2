# SPDX-License-Identifier: Apache-2.0
"""Modèle de domaine : blocs, messages, usage, identifiants."""

import pytest
from pydantic import ValidationError

from loom_ia.core.model import (
    AnthropicMeta,
    ArtifactRefBlock,
    JsonBlock,
    Message,
    OpenAIMeta,
    ReasoningBlock,
    RunId,
    RunState,
    RunStatus,
    SessionId,
    SpanId,
    TextBlock,
    ToolCallBlock,
    ToolOutput,
    ToolResultBlock,
    Usage,
    new_id,
)


def test_message_round_trip_keeps_every_block_type() -> None:
    assistant = Message(
        role="assistant",
        blocks=(
            ReasoningBlock(
                text="je calcule", provider_meta={"anthropic": AnthropicMeta(signature="s")}
            ),
            TextBlock(text="Je lance le calcul.", cache_breakpoint=True),
            JsonBlock(data={"a": [1, 2]}),
            ArtifactRefBlock(uri="file://devis.pdf", media_type="application/pdf", size=10),
            ToolCallBlock(call_id="c1", name="calculer", arguments={"expr": "1+1"}),
        ),
    )
    tool = Message(
        role="tool",
        blocks=(ToolResultBlock(call_id="c1", output=ToolOutput.text("2")),),
    )
    for message in (assistant, tool):
        assert Message.model_validate_json(message.model_dump_json()) == message


def test_provider_meta_is_typed_and_inferred_from_key() -> None:
    block = ReasoningBlock.model_validate(
        {
            "type": "reasoning",
            "provider_meta": {"anthropic": {"signature": "s"}, "openai": {"item_id": "i"}},
        }
    )
    assert block.provider_meta["anthropic"] == AnthropicMeta(signature="s")
    assert block.provider_meta["openai"] == OpenAIMeta(item_id="i")


def test_provider_meta_key_must_match_provider() -> None:
    with pytest.raises(ValidationError, match="fournisseur 'openai'"):
        TextBlock(text="x", provider_meta={"anthropic": OpenAIMeta(item_id="i")})


@pytest.mark.parametrize(
    ("role", "block"),
    [
        ("user", ToolResultBlock(call_id="c", output=ToolOutput.text("x"))),
        ("tool", TextBlock(text="x")),
        ("user", ToolCallBlock(call_id="c", name="t")),
        ("tool", ReasoningBlock(text="x")),
    ],
)
def test_message_rejects_blocks_in_wrong_role(
    role: str, block: TextBlock | ToolCallBlock | ToolResultBlock | ReasoningBlock
) -> None:
    with pytest.raises(ValidationError):
        Message.model_validate({"role": role, "blocks": [block.model_dump()]})


def test_message_requires_blocks() -> None:
    with pytest.raises(ValidationError, match="au moins un bloc"):
        Message(role="user", blocks=())


def test_message_helpers() -> None:
    message = Message(
        role="assistant",
        blocks=(
            ReasoningBlock(text="..."),
            TextBlock(text="Bonjour"),
            ToolCallBlock(call_id="c1", name="t"),
        ),
    )
    assert message.text == "Bonjour"
    assert [c.call_id for c in message.tool_calls] == ["c1"]
    stripped = message.without_reasoning()
    assert stripped is not None
    assert [b.type for b in stripped.blocks] == ["text", "tool_call"]
    assert Message.user("x").without_reasoning() == Message.user("x")
    only_reasoning = Message(role="assistant", blocks=(ReasoningBlock(text="..."),))
    assert only_reasoning.without_reasoning() is None


def test_models_are_immutable() -> None:
    message = Message.user("x")
    with pytest.raises(ValidationError):
        message.role = "assistant"  # pyright: ignore[reportAttributeAccessIssue]


def test_unknown_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        TextBlock.model_validate({"type": "text", "text": "x", "inconnu": 1})


def test_tool_output_helpers() -> None:
    assert ToolOutput.text("ok").as_text == "ok"
    error = ToolOutput.error("panne")
    assert error.is_error
    assert error.as_text == "panne"


def test_usage_addition_and_total() -> None:
    total = Usage(input_tokens=10, output_tokens=5, cache_read_tokens=2) + Usage(
        input_tokens=1, cache_write_tokens=3, reasoning_tokens=4
    )
    assert total == Usage(
        input_tokens=11,
        output_tokens=5,
        cache_read_tokens=2,
        cache_write_tokens=3,
        reasoning_tokens=4,
    )
    assert total.total_tokens == 21


def test_usage_rejects_negative_values() -> None:
    with pytest.raises(ValidationError):
        Usage(input_tokens=-1)


def test_new_ids_are_time_ordered() -> None:
    ids = [new_id() for _ in range(50)]
    assert ids == sorted(ids)
    assert len(set(ids)) == 50


def test_run_state_defaults() -> None:
    state = RunState(
        run_id=RunId("r"),
        session_id=SessionId("s"),
        root_run_id=RunId("r"),
        span_id=SpanId("sp"),
        agent="demo",
    )
    assert state.status is RunStatus.READY_FOR_MODEL
    assert state.context.tenant_id == "default"
    assert state.pending("inconnu") is None
    assert RunStatus.COMPLETED.is_terminal
    assert not RunStatus.PAUSED.is_terminal
