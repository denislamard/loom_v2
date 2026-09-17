# SPDX-License-Identifier: Apache-2.0
"""Adaptateur ``sdk: openai``, ``api: chat`` : API Chat Completions (B1, B8).

Vise les fournisseurs compatibles (Together, vLLM, Ollama…) :

- le prompt système devient un message ``system`` ;
- chaque résultat d'outil devient un message ``tool`` ; l'API n'ayant pas
  d'indicateur d'erreur, le contenu d'un résultat en erreur est préfixé ;
- le raisonnement est lu dans les champs ``reasoning_content`` ou
  ``reasoning`` que ces fournisseurs ajoutent, mais n'est jamais renvoyé ;
- la limite de sortie passe par ``max_tokens``, qu'ils reconnaissent tous ;
- un identifiant d'appel d'outil manquant est généré.

Les retries du SDK sont désactivés : la politique de loom-ia s'applique.
"""

import logging
from collections.abc import AsyncGenerator, Sequence
from typing import Final

import httpx2
import openai
from openai.types import CompletionUsage
from openai.types.chat import (
    ChatCompletion,
    ChatCompletionAssistantMessageParam,
    ChatCompletionChunk,
    ChatCompletionFunctionToolParam,
    ChatCompletionMessageFunctionToolCallParam,
    ChatCompletionMessageParam,
    ChatCompletionToolChoiceOptionParam,
)
from pydantic import BaseModel

from loom_ia.adapters.models._common import (
    ERROR_PREFIX,
    classify_error,
    json_text,
    output_text,
    retry_after,
    unsupported,
)
from loom_ia.core.model import (
    ArtifactRefBlock,
    JsonBlock,
    Message,
    ModelChunk,
    ModelRequest,
    ModelSpec,
    ReasoningDelta,
    Stopped,
    StopReason,
    TextBlock,
    TextDelta,
    ToolArgsDelta,
    ToolCallEnded,
    ToolCallStarted,
    ToolResultBlock,
    Usage,
    UsageDelta,
    new_id,
)
from loom_ia.core.ports import ModelError

logger = logging.getLogger(__name__)

PROVIDER: Final = "openai"
# Champs de raisonnement ajoutés par les fournisseurs compatibles.
REASONING_FIELDS: Final = ("reasoning_content", "reasoning")

_FINISH_REASONS: Final[dict[str, StopReason]] = {
    "stop": "end",
    "length": "max_tokens",
    "tool_calls": "tool_use",
    "function_call": "tool_use",
    "content_filter": "refusal",
}


class OpenAIChatModel:
    """Client ``ModelClient`` pour l'API Chat Completions."""

    def __init__(
        self,
        spec: ModelSpec,
        *,
        api_key: str,
        http_client: httpx2.AsyncClient | None = None,
    ) -> None:
        self.spec = spec
        self._client = openai.AsyncOpenAI(
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
        return f"OpenAIChatModel({self.spec.id!r}, model={self.spec.model!r})"

    async def stream(self, request: ModelRequest) -> AsyncGenerator[ModelChunk]:
        messages = to_openai_messages(request.system, request.messages)
        tools = to_openai_tools(request)
        tool_choice: ChatCompletionToolChoiceOptionParam | openai.Omit = openai.omit
        if tools:
            tool_choice = request.tool_choice
        max_tokens = request.max_tokens or self.spec.max_tokens or openai.omit
        extra_body = dict(request.params) or None
        completions = self._client.chat.completions
        try:
            if not self.spec.capabilities.streaming:
                completion = await completions.create(
                    model=request.model_id,
                    messages=messages,
                    tools=tools or openai.omit,
                    tool_choice=tool_choice,
                    max_tokens=max_tokens,
                    extra_body=extra_body,
                )
                chunks = completion_to_chunks(completion)
            else:
                parser = StreamParser()
                events = await completions.create(
                    model=request.model_id,
                    messages=messages,
                    tools=tools or openai.omit,
                    tool_choice=tool_choice,
                    max_tokens=max_tokens,
                    extra_body=extra_body,
                    stream=True,
                    stream_options={"include_usage": True},
                )
                async with events:
                    async for event in events:
                        for chunk in parser.feed(event):
                            yield chunk
                for chunk in parser.finish():
                    yield chunk
                return
        except openai.APIError as exc:
            raise to_model_error(exc) from exc
        for chunk in chunks:
            yield chunk


# --- Requête -------------------------------------------------------------------


def to_openai_messages(
    system: str, messages: Sequence[Message]
) -> list[ChatCompletionMessageParam]:
    result: list[ChatCompletionMessageParam] = []
    if system:
        result.append({"role": "system", "content": system})
    for message in messages:
        match message.role:
            case "user":
                result.append({"role": "user", "content": _text_of(message)})
            case "assistant":
                result.append(_assistant(message))
            case "tool":
                for block in message.blocks:
                    if isinstance(block, ToolResultBlock):
                        text = output_text(block.output)
                        if block.output.is_error:
                            text = ERROR_PREFIX + text
                        result.append(
                            {"role": "tool", "tool_call_id": block.call_id, "content": text}
                        )
    return result


def to_openai_tools(request: ModelRequest) -> list[ChatCompletionFunctionToolParam]:
    tools: list[ChatCompletionFunctionToolParam] = []
    for tool in request.tools:
        parameters: dict[str, object] = {**tool.input_schema}
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": parameters,
                },
            }
        )
    return tools


def _text_of(message: Message) -> str:
    parts: list[str] = []
    for block in message.blocks:
        match block:
            case TextBlock(text=text):
                parts.append(text)
            case JsonBlock(data=data):
                parts.append(json_text(data))
            case ArtifactRefBlock():
                raise unsupported(block)
            case _:
                pass
    return "\n\n".join(part for part in parts if part)


def _assistant(message: Message) -> ChatCompletionAssistantMessageParam:
    param: ChatCompletionAssistantMessageParam = {"role": "assistant"}
    calls: list[ChatCompletionMessageFunctionToolCallParam] = [
        {
            "id": call.call_id,
            "type": "function",
            "function": {"name": call.name, "arguments": json_text(call.arguments)},
        }
        for call in message.tool_calls
    ]
    text = _text_of(message)
    if calls:
        param["tool_calls"] = calls
        param["content"] = text or None
    else:
        param["content"] = text
    return param


# --- Réponse -------------------------------------------------------------------


class StreamParser:
    """Morceaux ``ChatCompletionChunk`` → morceaux neutres."""

    def __init__(self) -> None:
        self._calls: set[int] = set()
        self._finish: str | None = None
        self._usage: Usage | None = None
        self._model: str | None = None
        self._reasoning = False
        self._refused = False

    def feed(self, event: ChatCompletionChunk) -> list[ModelChunk]:
        self._model = event.model or self._model
        if event.usage is not None:
            self._usage = to_usage(event.usage)
        chunks: list[ModelChunk] = []
        for choice in event.choices:
            if choice.index != 0:
                continue
            delta = choice.delta
            reasoning = _reasoning(delta)
            if reasoning:
                chunks.append(ReasoningDelta(text=reasoning, start=not self._reasoning))
                self._reasoning = True
            text = (delta.content or "") + (delta.refusal or "")
            if text:
                chunks.append(TextDelta(text=text))
                self._reasoning = False
            for call in delta.tool_calls or ():
                self._reasoning = False
                function = call.function
                if call.index not in self._calls:
                    self._calls.add(call.index)
                    chunks.append(
                        ToolCallStarted(
                            index=call.index,
                            call_id=call.id or f"call_{new_id()}",
                            name=(function.name if function else None) or "",
                        )
                    )
                if function is not None and function.arguments:
                    chunks.append(ToolArgsDelta(index=call.index, json_fragment=function.arguments))
            if choice.finish_reason is not None:
                self._finish = choice.finish_reason
            self._refused = self._refused or bool(delta.refusal)
        return chunks

    def feed_completion(self, completion: ChatCompletion) -> list[ModelChunk]:
        """Réponse complète, lue comme un flux d'un seul tenant."""
        self._model = completion.model
        if completion.usage is not None:
            self._usage = to_usage(completion.usage)
        if not completion.choices:
            return []
        choice = completion.choices[0]
        message = choice.message
        chunks: list[ModelChunk] = []
        reasoning = _reasoning(message)
        if reasoning:
            chunks.append(ReasoningDelta(text=reasoning, start=True))
        text = (message.content or "") + (message.refusal or "")
        if text:
            chunks.append(TextDelta(text=text))
        for index, call in enumerate(message.tool_calls or ()):
            if call.type != "function":
                logger.debug("Appel d'outil OpenAI ignoré : %s", call.type)
                continue
            self._calls.add(index)
            call_id = call.id or f"call_{new_id()}"
            chunks.append(ToolCallStarted(index=index, call_id=call_id, name=call.function.name))
            chunks.append(ToolArgsDelta(index=index, json_fragment=call.function.arguments))
        self._finish = choice.finish_reason
        self._refused = bool(message.refusal)
        return chunks

    def finish(self) -> list[ModelChunk]:
        """Morceaux de fin : l'API ne signale ni la fin des appels ni l'arrêt séparément."""
        chunks: list[ModelChunk] = [ToolCallEnded(index=index) for index in sorted(self._calls)]
        if self._usage is not None:
            chunks.append(UsageDelta(usage=self._usage))
        chunks.append(Stopped(reason=self._stop_reason(), model_id=self._model))
        return chunks

    def _stop_reason(self) -> StopReason:
        if self._refused:
            return "refusal"
        reason = _FINISH_REASONS.get(self._finish or "stop", "end")
        # Certains fournisseurs répondent « stop » alors qu'ils demandent des outils.
        if reason == "end" and self._calls:
            return "tool_use"
        return reason


def completion_to_chunks(completion: ChatCompletion) -> list[ModelChunk]:
    """Réponse non streamée → flux simulé."""
    parser = StreamParser()
    return parser.feed_completion(completion) + parser.finish()


def to_usage(usage: CompletionUsage) -> Usage:
    """Les tokens en cache sont compris dans ``prompt_tokens`` : ils en sont retirés."""
    prompt = usage.prompt_tokens_details
    cached = (prompt.cached_tokens or 0) if prompt else 0
    written = (prompt.cache_write_tokens or 0) if prompt else 0
    completion = usage.completion_tokens_details
    return Usage(
        input_tokens=max(0, usage.prompt_tokens - cached - written),
        output_tokens=usage.completion_tokens,
        cache_read_tokens=cached,
        cache_write_tokens=written,
        reasoning_tokens=(completion.reasoning_tokens or 0) if completion else 0,
    )


def _reasoning(part: BaseModel) -> str:
    extra = part.model_extra or {}
    for field in REASONING_FIELDS:
        value = extra.get(field)
        if isinstance(value, str) and value:
            return value
    return ""


# --- Erreurs -------------------------------------------------------------------


def to_model_error(exc: openai.APIError) -> ModelError:
    hints = (exc.code, exc.type)
    if isinstance(exc, openai.APIStatusError):
        return classify_error(
            exc.message,
            http_status=exc.status_code,
            hints=hints,
            retry_after=retry_after(exc.response.headers),
        )
    return classify_error(exc.message, http_status=None, hints=hints)
