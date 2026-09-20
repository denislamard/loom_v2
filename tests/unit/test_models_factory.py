# SPDX-License-Identifier: Apache-2.0
"""Fabrique des modèles, modèle ``fake`` et outils communs des adaptateurs."""

import sys
from datetime import UTC, datetime
from email.utils import format_datetime

import pytest
from pydantic import ValidationError

from loom_ia.adapters.models import NO_API_KEY, ModelConfigError, create_model_client
from loom_ia.adapters.models._common import classify_error, output_text, retry_after
from loom_ia.adapters.models.fake import FakeModel
from loom_ia.core.model import (
    ArtifactRefBlock,
    InlineDataBlock,
    JsonBlock,
    Message,
    ModelRequest,
    ModelSpec,
    ReasoningBlock,
    TextBlock,
    ToolCallBlock,
    ToolOutput,
    ToolResultBlock,
)
from loom_ia.core.ports import ModelError, complete

SCRIPT = [
    {"tool_calls": [{"name": "calculer", "arguments": {"expr": "12*7+3"}}], "reasoning": "hmm"},
    {"text": "12 * 7 + 3 = 87"},
]


def fake_spec(**params: object) -> ModelSpec:
    return ModelSpec.model_validate(
        {"id": "FAKE", "sdk": "fake", "model": "fake-1", "params": params}
    )


def request(*messages: Message) -> ModelRequest:
    return ModelRequest(model_id="fake-1", messages=messages)


async def test_fake_model_follows_its_script_within_a_run() -> None:
    model = create_model_client(fake_spec(script=SCRIPT))
    assert isinstance(model, FakeModel)
    assert repr(model) == "FakeModel('FAKE', 2 réponse(s))"

    first = await complete(model, request(Message.user("Combien font 12 * 7 + 3 ?")))
    assert first.message.blocks == (
        ReasoningBlock(text="hmm", model_id="fake-1"),
        ToolCallBlock(call_id="fake_0_0", name="calculer", arguments={"expr": "12*7+3"}),
    )
    assert first.stop_reason == "tool_use"
    assert first.provider == "fake"
    assert first.usage.input_tokens > 0 and first.usage.output_tokens > 0

    result = Message(
        role="tool",
        blocks=(ToolResultBlock(call_id="fake_0_0", output=ToolOutput.text("87")),),
    )
    second = await complete(model, request(Message.user("Combien ?"), first.message, result))
    assert second.message == Message.assistant("12 * 7 + 3 = 87")
    assert second.stop_reason == "end"

    # Nouvelle demande dans la même session : le script repart du début.
    again = await complete(
        model,
        request(Message.user("?"), first.message, result, second.message, Message.user("Et ?")),
    )
    assert again.message.tool_calls

    with pytest.raises(ModelError, match="épuisé : réponse n°3 demandée, 2 prévue") as caught:
        await complete(
            model,
            request(Message.user("?"), first.message, result, second.message, result),
        )
    assert caught.value.kind == "invalid_request"
    await model.aclose()


async def test_fake_model_without_script_echoes() -> None:
    model = create_model_client(fake_spec())
    answer = await complete(model, request(Message.user("Bonjour")))
    assert answer.message == Message.assistant("Écho : Bonjour")
    nothing = await complete(model, request())
    assert nothing.message == Message.assistant("Écho : ")


async def test_fake_model_honours_required_tools_and_repairs() -> None:
    from loom_ia.core.model import REPAIR_PREFIX, ToolDefinition

    model = create_model_client(fake_spec(script=[{"text": "Sans outil."}, {"text": "Réparé."}]))
    tools = (ToolDefinition(name="calculer", description="Calcule."),)
    required = ModelRequest(
        model_id="fake-1", messages=(Message.user("?"),), tools=tools, tool_choice="required"
    )
    with pytest.raises(ModelError, match="sans appel d'outil") as caught:
        await complete(model, required)
    assert caught.value.kind == "invalid_request"

    # Un diagnostic de réparation n'est pas une nouvelle demande : le script continue.
    repair = Message.user(f"{REPAIR_PREFIX} (p) : réessaie.")
    again = await complete(
        model, request(Message.user("?"), Message.assistant("Sans outil."), repair)
    )
    assert again.message == Message.assistant("Réparé.")


async def test_fake_model_answers_by_request_and_forced_answer() -> None:
    from loom_ia.core.model import ToolDefinition

    script = [
        {"text": "Version courte.", "without_text": "détaillé"},
        {"text": "Version détaillée.", "with_text": "détaillé"},
    ]
    model = create_model_client(fake_spec(script=script))
    short = await complete(model, request(Message.user("Résume.")))
    detailed = await complete(model, request(Message.user("Un texte détaillé.")))
    assert (short.message.text, detailed.message.text) == ("Version courte.", "Version détaillée.")

    calls: list[dict[str, object]] = [
        {"tool_calls": [{"name": "calculer", "arguments": {}}], "forced": "Je m'arrête là."}
    ]
    model = create_model_client(fake_spec(script=calls))
    tools = (ToolDefinition(name="calculer", description="Calcule."),)
    asked = (Message.user("Avec un outil."),)
    called = await complete(model, ModelRequest(model_id="fake-1", messages=asked, tools=tools))
    assert called.message.tool_calls
    forced = await complete(
        model, ModelRequest(model_id="fake-1", messages=asked, tools=tools, tool_choice="none")
    )
    assert forced.message == Message.assistant("Je m'arrête là.")


def test_fake_script_is_validated() -> None:
    with pytest.raises(ValidationError):
        create_model_client(fake_spec(script=[{"texte": "faute de frappe"}]))


def test_api_keys_come_from_the_environment() -> None:
    pytest.importorskip("anthropic")
    spec = ModelSpec(id="C", sdk="anthropic", model="c", api_key_env="LOOM_TEST_KEY")
    with pytest.raises(ModelConfigError, match="LOOM_TEST_KEY est absente ou vide") as caught:
        create_model_client(spec, environ={"LOOM_TEST_KEY": "  "})
    assert "sk-" not in str(caught.value)
    model = create_model_client(spec, environ={"LOOM_TEST_KEY": "sk-secret"})
    assert "sk-secret" not in repr(model)
    assert NO_API_KEY == "not-needed"


def test_the_responses_api_has_its_adapter() -> None:
    pytest.importorskip("openai")
    from loom_ia.adapters.models.openai_responses import OpenAIResponsesModel

    spec = ModelSpec(id="GPT", sdk="openai", api="responses", model="gpt", api_key_env=None)
    client = create_model_client(spec)
    assert isinstance(client, OpenAIResponsesModel)
    assert repr(client) == "OpenAIResponsesModel('GPT', model='gpt')"


@pytest.mark.parametrize(
    ("sdk", "sdk_module", "adapter"),
    [
        ("anthropic", "anthropic", "loom_ia.adapters.models.anthropic"),
        ("openai", "openai", "loom_ia.adapters.models.openai_chat"),
    ],
)
def test_missing_extra_is_explained(
    sdk: str, sdk_module: str, adapter: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Simule un environnement sans l'extra : le SDK est introuvable.
    for name in list(sys.modules):
        if name in {sdk_module, adapter} or name.startswith(f"{sdk_module}."):
            monkeypatch.delitem(sys.modules, name)
    monkeypatch.setitem(sys.modules, sdk_module, None)
    spec = ModelSpec.model_validate({"id": "X", "sdk": sdk, "model": "x"})
    with pytest.raises(ModelConfigError, match=rf"installer l'extra : loom-ia\[{sdk_module}\]"):
        create_model_client(spec)


@pytest.mark.parametrize(
    ("message", "status", "hints", "kind"),
    [
        ("quota", 402, (), "quota_exhausted"),
        ("quota", 429, ("insufficient_quota",), "quota_exhausted"),
        ("maximum context length is 8192 tokens", 400, (), "context_overflow"),
        ("too many tokens", None, (), "context_overflow"),
        ("trop long", 413, (), "invalid_request"),
        ("filtré", 400, ("content_policy_violation",), "content_filtered"),
        ("interdit", 403, (), "auth"),
        ("plein", None, ("overloaded_error",), "overloaded"),
        ("lent", 429, (), "transient"),
        ("délai", 408, (), "transient"),
        ("conflit", 409, (), "transient"),
        ("serveur", 500, (None,), "transient"),
        ("réseau", None, (), "transient"),
        ("inconnu", 418, ("teapot",), "invalid_request"),
    ],
)
def test_error_classification(
    message: str, status: int | None, hints: tuple[str | None, ...], kind: str
) -> None:
    error = classify_error(message, http_status=status, hints=hints, retry_after=2.0)
    assert (error.kind, error.http_status, error.retry_after, error.message) == (
        kind,
        status,
        2.0,
        message,
    )


def test_retry_after_parsing() -> None:
    now = datetime(2026, 9, 17, 12, 0, tzinfo=UTC)
    later = format_datetime(datetime(2026, 9, 17, 12, 0, 30, tzinfo=UTC), usegmt=True)
    assert retry_after(None) is None
    assert retry_after({}) is None
    assert retry_after({"x-other": "1"}) is None
    assert retry_after({"Retry-After-Ms": "250"}) == 0.25
    assert retry_after({"retry-after-ms": "abc", "retry-after": "4"}) == 4.0
    assert retry_after({"Retry-After": "-3"}) == 0.0
    assert retry_after({"retry-after": later}, now=now.timestamp()) == 30.0
    past = format_datetime(datetime(2020, 1, 1, tzinfo=UTC), usegmt=True)
    assert retry_after({"retry-after": past}) == 0.0
    assert retry_after({"retry-after": "demain"}) is None


def test_output_text() -> None:
    output = ToolOutput(blocks=(TextBlock(text="a"), JsonBlock(data=[1])))
    assert output_text(output) == "a\n[1]"
    assert output_text(ToolOutput(data={"k": "é"})) == '{"k": "é"}'
    assert output_text(ToolOutput()) == ""
    image = ToolOutput(blocks=(ArtifactRefBlock(uri="mem://1", media_type="image/png"),))
    with pytest.raises(ModelError, match="non résolue"):
        output_text(image)
    inline = ToolOutput(blocks=(InlineDataBlock(media_type="image/png", data=b"\x89PNG"),))
    with pytest.raises(ModelError, match="tool_result_media: false"):
        output_text(inline)
