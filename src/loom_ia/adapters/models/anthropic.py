# SPDX-License-Identifier: Apache-2.0
"""Adaptateur ``sdk: anthropic`` : API Messages, en streaming (B1, B8).

Sert aussi les fournisseurs compatibles (MiniMax…) via ``base_url``.

Traduction des messages :

- le rôle ``tool`` devient ``user`` avec des blocs ``tool_result`` ;
- les messages consécutifs de même rôle sont fusionnés ;
- les blocs de texte vides sont omis (l'API les refuse) ;
- le raisonnement n'est renvoyé que s'il porte des données Anthropic
  (signature ou contenu masqué) ; les autres blocs de raisonnement sont omis.

Les retries du SDK sont désactivés : la politique de loom-ia s'applique.
"""

import logging
from collections.abc import AsyncGenerator, Sequence
from typing import Final, Literal, cast

import anthropic
import anthropic.types as sdk
import httpx2
from pydantic import JsonValue

from loom_ia.adapters.models._common import (
    classify_error,
    json_text,
    output_text,
    retry_after,
    unsupported,
)
from loom_ia.core.model import (
    AnthropicMeta,
    ArtifactRefBlock,
    ContentBlock,
    JsonBlock,
    Message,
    ModelChunk,
    ModelRequest,
    ModelSpec,
    ProviderMeta,
    ReasoningBlock,
    ReasoningDelta,
    Stopped,
    StopReason,
    TextBlock,
    TextDelta,
    ToolArgsDelta,
    ToolCallBlock,
    ToolCallEnded,
    ToolCallStarted,
    ToolResultBlock,
    Usage,
    UsageDelta,
    message_to_chunks,
)
from loom_ia.core.ports import ModelError

logger = logging.getLogger(__name__)

PROVIDER: Final = "anthropic"
# L'API exige max_tokens.
DEFAULT_MAX_TOKENS: Final = 4096

_STOP_REASONS: Final[dict[str, StopReason]] = {
    "end_turn": "end",
    "stop_sequence": "end",
    "pause_turn": "end",
    "tool_use": "tool_use",
    "max_tokens": "max_tokens",
    "model_context_window_exceeded": "max_tokens",
    "refusal": "refusal",
}

type _Role = Literal["user", "assistant"]


class AnthropicModel:
    """Client ``ModelClient`` pour l'API Messages."""

    def __init__(
        self,
        spec: ModelSpec,
        *,
        api_key: str,
        http_client: httpx2.AsyncClient | None = None,
    ) -> None:
        self.spec = spec
        self._client = anthropic.AsyncAnthropic(
            api_key=api_key,
            base_url=spec.base_url,
            max_retries=0,
            timeout=spec.timeouts.total,
            http_client=http_client,
        )

    @property
    def provider(self) -> str:
        return PROVIDER

    async def aclose(self) -> None:
        await self._client.close()

    def __repr__(self) -> str:
        return f"AnthropicModel({self.spec.id!r}, model={self.spec.model!r})"

    async def stream(self, request: ModelRequest) -> AsyncGenerator[ModelChunk]:
        messages = to_anthropic_messages(request.messages)
        tools = to_anthropic_tools(request)
        tool_choice: sdk.ToolChoiceParam | anthropic.Omit = anthropic.omit
        if tools:
            tool_choice = {"type": "none"} if request.tool_choice == "none" else {"type": "auto"}
        max_tokens = request.max_tokens or self.spec.max_tokens or DEFAULT_MAX_TOKENS
        system = request.system or anthropic.omit
        extra_body = dict(request.params) or None
        messages_api = self._client.messages
        try:
            if not self.spec.capabilities.streaming:
                message = await messages_api.create(
                    model=request.model_id,
                    max_tokens=max_tokens,
                    messages=messages,
                    system=system,
                    tools=tools or anthropic.omit,
                    tool_choice=tool_choice,
                    extra_body=extra_body,
                )
                chunks = response_to_chunks(message)
            else:
                parser = StreamParser()
                events = await messages_api.create(
                    model=request.model_id,
                    max_tokens=max_tokens,
                    messages=messages,
                    system=system,
                    tools=tools or anthropic.omit,
                    tool_choice=tool_choice,
                    extra_body=extra_body,
                    stream=True,
                )
                async with events:
                    async for event in events:
                        for chunk in parser.feed(event):
                            yield chunk
                return
        except anthropic.APIError as exc:
            raise to_model_error(exc) from exc
        for chunk in chunks:
            yield chunk


# --- Requête -------------------------------------------------------------------


def to_anthropic_messages(messages: Sequence[Message]) -> list[sdk.MessageParam]:
    """Messages neutres → messages de l'API, fusionnés par rôle."""
    turns: list[tuple[_Role, list[sdk.ContentBlockParam]]] = []
    for message in messages:
        role: _Role = "assistant" if message.role == "assistant" else "user"
        content = [block for b in message.blocks if (block := _to_block(b)) is not None]
        if not content:
            continue
        if turns and turns[-1][0] == role:
            turns[-1][1].extend(content)
        else:
            turns.append((role, content))
    return [{"role": role, "content": content} for role, content in turns]


def to_anthropic_tools(request: ModelRequest) -> list[sdk.ToolParam]:
    tools: list[sdk.ToolParam] = []
    for tool in request.tools:
        schema: dict[str, object] = {**tool.input_schema}
        tools.append({"name": tool.name, "description": tool.description, "input_schema": schema})
    return tools


def _to_block(block: ContentBlock) -> sdk.ContentBlockParam | None:
    match block:
        case TextBlock(text=text):
            return {"type": "text", "text": text} if text else None
        case JsonBlock(data=data):
            return {"type": "text", "text": json_text(data)}
        case ArtifactRefBlock():
            raise unsupported(block)
        case ReasoningBlock(text=text, provider_meta=meta):
            own = meta.get(PROVIDER)
            if not isinstance(own, AnthropicMeta):
                return None
            if own.redacted_data is not None:
                return {"type": "redacted_thinking", "data": own.redacted_data}
            if own.signature is not None:
                return {"type": "thinking", "thinking": text, "signature": own.signature}
            return None
        case ToolCallBlock(call_id=call_id, name=name, arguments=arguments):
            arguments_object: dict[str, object] = {**arguments}
            return {"type": "tool_use", "id": call_id, "name": name, "input": arguments_object}
        case ToolResultBlock(call_id=call_id, output=output):
            result: sdk.ToolResultBlockParam = {
                "type": "tool_result",
                "tool_use_id": call_id,
                "is_error": output.is_error,
            }
            text = output_text(output)
            if text:
                result["content"] = [{"type": "text", "text": text}]
            return result


# --- Réponse -------------------------------------------------------------------


def _meta(**fields: str) -> dict[str, ProviderMeta]:
    return {PROVIDER: AnthropicMeta.model_validate(fields)}


class StreamParser:
    """Événements bruts du flux → morceaux neutres."""

    def __init__(self) -> None:
        self._tools: dict[int, int] = {}
        self._usage: dict[str, int] = {}
        self._stop: StopReason | None = None
        self._model: str | None = None

    def feed(self, event: sdk.RawMessageStreamEvent) -> list[ModelChunk]:
        match event:
            case sdk.RawMessageStartEvent(message=message):
                self._model = message.model
                self.record_usage(message.usage)
                return []
            case sdk.RawContentBlockStartEvent(index=index, content_block=block):
                return self._block_start(index, block)
            case sdk.RawContentBlockDeltaEvent(index=index, delta=delta):
                return self._block_delta(index, delta)
            case sdk.RawContentBlockStopEvent(index=index):
                if index in self._tools:
                    return [ToolCallEnded(index=self._tools[index])]
                return []
            case sdk.RawMessageDeltaEvent(delta=delta, usage=usage):
                if delta.stop_reason is not None:
                    self._stop = _STOP_REASONS.get(delta.stop_reason, "end")
                self.record_usage(usage)
                return []
            case sdk.RawMessageStopEvent():
                return [
                    UsageDelta(usage=self.usage()),
                    Stopped(reason=self._stop or "end", model_id=self._model),
                ]

    def record_usage(self, usage: sdk.Usage | sdk.MessageDeltaUsage) -> None:
        """Retient les compteurs ; ceux d'un ``message_delta`` sont cumulés."""
        details = usage.output_tokens_details
        values = {
            "input": usage.input_tokens,
            "output": usage.output_tokens,
            "cache_read": usage.cache_read_input_tokens,
            "cache_write": usage.cache_creation_input_tokens,
            "reasoning": details.thinking_tokens if details is not None else None,
        }
        self._usage.update({key: value for key, value in values.items() if value is not None})

    def usage(self) -> Usage:
        return Usage(
            input_tokens=self._usage.get("input", 0),
            output_tokens=self._usage.get("output", 0),
            cache_read_tokens=self._usage.get("cache_read", 0),
            cache_write_tokens=self._usage.get("cache_write", 0),
            reasoning_tokens=self._usage.get("reasoning", 0),
        )

    def _block_start(self, index: int, block: sdk.ContentBlock) -> list[ModelChunk]:
        match block:
            case sdk.TextBlock(text=text):
                return [TextDelta(text=text)] if text else []
            case sdk.ThinkingBlock(thinking=thinking, signature=signature):
                meta = _meta(signature=signature) if signature else {}
                return [ReasoningDelta(text=thinking, start=True, provider_meta=meta)]
            case sdk.RedactedThinkingBlock(data=data):
                return [ReasoningDelta(start=True, provider_meta=_meta(redacted_data=data))]
            case sdk.ToolUseBlock(id=call_id, name=name):
                position = len(self._tools)
                self._tools[index] = position
                return [ToolCallStarted(index=position, call_id=call_id, name=name)]
            case _:
                logger.debug("Bloc Anthropic ignoré : %s", block.type)
                return []

    def _block_delta(self, index: int, delta: sdk.RawContentBlockDelta) -> list[ModelChunk]:
        match delta:
            case sdk.TextDelta(text=text):
                return [TextDelta(text=text)]
            case sdk.InputJSONDelta(partial_json=fragment) if index in self._tools:
                return [ToolArgsDelta(index=self._tools[index], json_fragment=fragment)]
            case sdk.ThinkingDelta(thinking=thinking):
                return [ReasoningDelta(text=thinking)]
            case sdk.SignatureDelta(signature=signature):
                return [ReasoningDelta(provider_meta=_meta(signature=signature))]
            case _:
                return []


def response_to_chunks(message: sdk.Message) -> list[ModelChunk]:
    """Réponse non streamée → flux simulé."""
    blocks: list[ContentBlock] = []
    for block in message.content:
        match block:
            case sdk.TextBlock(text=text):
                blocks.append(TextBlock(text=text))
            case sdk.ThinkingBlock(thinking=thinking, signature=signature):
                blocks.append(
                    ReasoningBlock(text=thinking, provider_meta=_meta(signature=signature))
                )
            case sdk.RedactedThinkingBlock(data=data):
                blocks.append(ReasoningBlock(provider_meta=_meta(redacted_data=data)))
            case sdk.ToolUseBlock(id=call_id, name=name, input=arguments):
                json_arguments = cast(dict[str, JsonValue], arguments)
                blocks.append(ToolCallBlock(call_id=call_id, name=name, arguments=json_arguments))
            case _:
                logger.debug("Bloc Anthropic ignoré : %s", block.type)
    parser = StreamParser()
    parser.record_usage(message.usage)
    return message_to_chunks(
        Message(role="assistant", blocks=tuple(blocks) or (TextBlock(text=""),)),
        usage=parser.usage(),
        stop_reason=_STOP_REASONS.get(message.stop_reason or "end_turn", "end"),
        model_id=message.model,
    )


# --- Erreurs -------------------------------------------------------------------


def to_model_error(exc: anthropic.APIError) -> ModelError:
    if isinstance(exc, anthropic.APIStatusError):
        status = exc.status_code
        return classify_error(
            exc.message,
            # Une erreur reçue dans un flux ouvert porte le statut 200 de ce flux.
            http_status=status if status >= 400 else None,
            hints=(exc.type,),
            retry_after=retry_after(exc.response.headers),
        )
    return classify_error(exc.message, http_status=None)
