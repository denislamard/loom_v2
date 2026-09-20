# SPDX-License-Identifier: Apache-2.0
"""Adaptateur ``sdk: openai``, ``api: chat`` : API Chat Completions (B1, B8).

Vise les fournisseurs compatibles (Together, vLLM, Ollama…) ; sans
``base_url``, l'adresse officielle d'OpenAI, quelle que soit
``OPENAI_BASE_URL``.

- le prompt système devient un message ``system`` ;
- chaque résultat d'outil devient un message ``tool`` ; l'API n'ayant pas
  d'indicateur d'erreur, le contenu d'un résultat en erreur est préfixé ;
- une image (``inline_data``) d'un message utilisateur devient une partie
  ``image_url`` en URL ``data:`` ; l'API n'accepte pas d'image dans un
  résultat d'outil (``tool_result_media: false``) ;
- le raisonnement est lu dans les champs ``reasoning_content`` ou
  ``reasoning`` que ces fournisseurs ajoutent ; il n'est renvoyé qu'à un
  modèle déclaré ``thinking: true``, pour la boucle d'outils en cours, dans le
  champ où il est arrivé (#7, backlog #015 : gpt-oss l'attend) — certains
  fournisseurs refusent ce champ ;
- la limite de sortie passe par ``max_tokens``, qu'ils reconnaissent tous ;
- ``tool_choice`` passe tel quel (``auto``, ``none``, ``required``) ;
- un identifiant d'appel d'outil manquant est généré ;
- le schéma de sortie (``output_schema``) devient ``response_format``
  (``json_schema``) pour un modèle déclaré ``native_json: true`` ; ``strict``
  seulement si le schéma en suit les règles (B9).

Les retries du SDK sont désactivés : la politique de loom-ia s'applique.
"""

import logging
from collections.abc import AsyncGenerator, Sequence
from typing import Final, cast

import httpx2
import openai
from openai.types import CompletionUsage
from openai.types.chat import (
    ChatCompletion,
    ChatCompletionAssistantMessageParam,
    ChatCompletionChunk,
    ChatCompletionContentPartParam,
    ChatCompletionFunctionToolParam,
    ChatCompletionMessageFunctionToolCallParam,
    ChatCompletionMessageParam,
    ChatCompletionToolChoiceOptionParam,
)
from openai.types.chat.completion_create_params import ResponseFormat
from pydantic import BaseModel, JsonValue

from loom_ia.adapters.models._common import (
    ERROR_PREFIX,
    OUTPUT_SCHEMA_NAME,
    classify_error,
    image_data,
    is_strict,
    json_text,
    output_text,
    reasoning_loop,
    retry_after,
    unresolved,
)
from loom_ia.core.model import (
    ArtifactRefBlock,
    ContentBlock,
    InlineDataBlock,
    JsonBlock,
    Message,
    ModelChunk,
    ModelRequest,
    ModelSpec,
    OpenAIMeta,
    ProviderMeta,
    ReasoningBlock,
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
# Adresse sans ``base_url`` : passée au SDK pour qu'il ne lise pas OPENAI_BASE_URL.
DEFAULT_BASE_URL: Final = "https://api.openai.com/v1"
# Champs de raisonnement ajoutés par les fournisseurs compatibles.
REASONING_FIELDS: Final = ("reasoning_content", "reasoning")
# Champ de renvoi d'un raisonnement dont l'origine est inconnue (convention d'OpenAI pour gpt-oss).
DEFAULT_REASONING_FIELD: Final = "reasoning"

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
            base_url=spec.base_url or DEFAULT_BASE_URL,
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
        capabilities = self.spec.capabilities
        messages = to_openai_messages(
            request.system, request.messages, reasoning=capabilities.thinking
        )
        tools = to_openai_tools(request)
        tool_choice: ChatCompletionToolChoiceOptionParam | openai.Omit = openai.omit
        if tools:
            tool_choice = request.tool_choice
        max_tokens = request.max_tokens or self.spec.max_tokens or openai.omit
        response_format: ResponseFormat | openai.Omit = openai.omit
        if request.output_schema is not None and capabilities.native_json:
            response_format = to_response_format(request.output_schema)
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
                    response_format=response_format,
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
                    response_format=response_format,
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
    system: str, messages: Sequence[Message], *, reasoning: bool = False
) -> list[ChatCompletionMessageParam]:
    """Messages neutres → messages de l'API.

    ``reasoning`` : le raisonnement des réponses de la boucle d'outils en
    cours est renvoyé (modèle ``thinking: true``).
    """
    result: list[ChatCompletionMessageParam] = []
    if system:
        result.append({"role": "system", "content": system})
    echoed = reasoning_loop(messages) if reasoning else set[int]()
    for position, message in enumerate(messages):
        match message.role:
            case "user":
                result.append({"role": "user", "content": _user_content(message)})
            case "assistant":
                result.append(_assistant(message, reasoning=position in echoed))
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


def to_response_format(schema: dict[str, JsonValue]) -> ResponseFormat:
    """Schéma de sortie du contrat → ``response_format`` (B9)."""
    definition: dict[str, object] = {"name": OUTPUT_SCHEMA_NAME, "schema": {**schema}}
    if is_strict(schema):
        definition["strict"] = True
    return cast(ResponseFormat, {"type": "json_schema", "json_schema": definition})


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


def _user_content(message: Message) -> str | list[ChatCompletionContentPartParam]:
    """Texte seul, ou parties texte et image quand le message porte des images."""
    if not any(isinstance(block, InlineDataBlock) for block in message.blocks):
        return _text_of(message)
    parts: list[ChatCompletionContentPartParam] = []
    for block in message.blocks:
        match block:
            case InlineDataBlock(media_type=media_type):
                url = f"data:{media_type};base64,{image_data(block)}"
                parts.append({"type": "image_url", "image_url": {"url": url}})
            case _:
                if text := _text(block):
                    parts.append({"type": "text", "text": text})
    return parts


def _text_of(message: Message) -> str:
    return "\n\n".join(text for block in message.blocks if (text := _text(block)))


def _text(block: ContentBlock) -> str:
    match block:
        case TextBlock(text=text):
            return text
        case JsonBlock(data=data):
            return json_text(data)
        case ArtifactRefBlock():
            raise unresolved(block)
        case InlineDataBlock():
            raise ModelError(
                "invalid_request", "Image hors d'un message utilisateur : non transmissible"
            )
        case _:
            return ""


def _assistant(message: Message, *, reasoning: bool = False) -> ChatCompletionAssistantMessageParam:
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
    if reasoning and (echo := _reasoning_echo(message)) is not None:
        field, thought = echo
        return cast(ChatCompletionAssistantMessageParam, {**param, field: thought})
    return param


def _reasoning_echo(message: Message) -> tuple[str, str] | None:
    """Champ et texte du raisonnement d'une réponse, pour le lui renvoyer."""
    blocks = [b for b in message.blocks if isinstance(b, ReasoningBlock) and b.text]
    if not blocks:
        return None
    field = DEFAULT_REASONING_FIELD
    for block in blocks:
        meta = block.provider_meta.get(PROVIDER)
        if isinstance(meta, OpenAIMeta) and meta.reasoning_field in REASONING_FIELDS:
            field = meta.reasoning_field
    return field, "\n\n".join(block.text for block in blocks)


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
            field, reasoning = _reasoning(delta)
            if reasoning:
                start = not self._reasoning
                chunks.append(
                    ReasoningDelta(text=reasoning, start=start, provider_meta=_origin(field))
                    if start
                    else ReasoningDelta(text=reasoning)
                )
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
        field, reasoning = _reasoning(message)
        if reasoning:
            chunks.append(ReasoningDelta(text=reasoning, start=True, provider_meta=_origin(field)))
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


def _reasoning(part: BaseModel) -> tuple[str, str]:
    """Champ et texte du raisonnement d'un morceau ou d'une réponse (texte vide sans lui)."""
    extra = part.model_extra or {}
    for field in REASONING_FIELDS:
        value = extra.get(field)
        if isinstance(value, str) and value:
            return field, value
    return DEFAULT_REASONING_FIELD, ""


def _origin(field: str) -> dict[str, ProviderMeta]:
    return {PROVIDER: OpenAIMeta(reasoning_field=field)}


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
