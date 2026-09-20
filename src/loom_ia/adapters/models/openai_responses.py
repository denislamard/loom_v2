# SPDX-License-Identifier: Apache-2.0
"""Adaptateur ``sdk: openai``, ``api: responses`` : API Responses d'OpenAI (#7, #9, B1).

Sans état chez le fournisseur : ``store: false``, toute la conversation part
à chaque appel, et le raisonnement revient chiffré
(``include: reasoning.encrypted_content``) pour être renvoyé au tour suivant.

- le prompt système devient ``instructions`` ;
- un message utilisateur devient un message ``user`` (``input_text`` et, pour
  une image, ``input_image`` en URL ``data:``) ;
- une réponse du modèle redevient, dans l'ordre de ses blocs : ses éléments
  de raisonnement, son texte (message ``assistant``), ses appels d'outils
  (éléments ``function_call``) ;
- un résultat d'outil devient un ``function_call_output`` : du texte, ou du
  texte et des images ; l'API n'ayant pas d'indicateur d'erreur, le contenu
  d'un résultat en erreur est préfixé ;
- raisonnement renvoyé : chiffré, toujours, sans son identifiant (rien n'est
  gardé chez le fournisseur, un identifiant y serait cherché en vain) ; en
  clair (gpt-oss servi par l'API Responses), seulement à un modèle déclaré
  ``thinking: true`` et pour la boucle d'outils en cours ;
- la limite de sortie passe par ``max_output_tokens`` ; ``tool_choice`` tel
  quel ; un outil est ``strict`` si son schéma en suit les règles ;
- le schéma de sortie (``output_schema``) devient ``text.format``
  (``json_schema``) pour un modèle déclaré ``native_json: true`` (B9).

Les retries du SDK sont désactivés : la politique de loom-ia s'applique.
Adaptateur testé sur des réponses HTTP enregistrées.
"""

import logging
from collections.abc import AsyncGenerator, Sequence
from typing import Final, cast

import httpx2
import openai
from openai.types.responses import (
    FunctionToolParam,
    Response,
    ResponseCompletedEvent,
    ResponseErrorEvent,
    ResponseFailedEvent,
    ResponseFunctionCallArgumentsDeltaEvent,
    ResponseFunctionToolCall,
    ResponseIncludable,
    ResponseIncompleteEvent,
    ResponseInputItemParam,
    ResponseOutputItem,
    ResponseOutputItemAddedEvent,
    ResponseOutputItemDoneEvent,
    ResponseOutputMessage,
    ResponseReasoningItem,
    ResponseReasoningSummaryPartAddedEvent,
    ResponseReasoningSummaryTextDeltaEvent,
    ResponseReasoningTextDeltaEvent,
    ResponseRefusalDeltaEvent,
    ResponseStreamEvent,
    ResponseTextConfigParam,
    ResponseTextDeltaEvent,
    ResponseUsage,
)
from openai.types.responses.response_create_params import ToolChoice
from openai.types.responses.response_error import ResponseError
from pydantic import JsonValue

from loom_ia.adapters.models._common import (
    ERROR_PREFIX,
    OUTPUT_SCHEMA_NAME,
    classify_error,
    image_data,
    is_strict,
    json_text,
    reasoning_loop,
    unresolved,
)
from loom_ia.adapters.models.openai_chat import DEFAULT_BASE_URL, to_model_error
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
    ToolOutput,
    ToolResultBlock,
    Usage,
    UsageDelta,
)
from loom_ia.core.ports import ModelError

logger = logging.getLogger(__name__)

PROVIDER: Final = "openai"
# Le raisonnement revient chiffré, pour être renvoyé sans rien garder chez le fournisseur.
INCLUDE: Final[list[ResponseIncludable]] = ["reasoning.encrypted_content"]

type _Item = dict[str, object]


class OpenAIResponsesModel:
    """Client ``ModelClient`` pour l'API Responses."""

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
        return f"OpenAIResponsesModel({self.spec.id!r}, model={self.spec.model!r})"

    async def stream(self, request: ModelRequest) -> AsyncGenerator[ModelChunk]:
        capabilities = self.spec.capabilities
        items = to_input_items(request.messages, thinking=capabilities.thinking)
        tools = to_response_tools(request)
        tool_choice: ToolChoice | openai.Omit = request.tool_choice if tools else openai.omit
        max_tokens = request.max_tokens or self.spec.max_tokens or openai.omit
        text: ResponseTextConfigParam | openai.Omit = openai.omit
        if request.output_schema is not None and capabilities.native_json:
            text = to_text_config(request.output_schema)
        instructions = request.system or openai.omit
        extra_body = dict(request.params) or None
        responses = self._client.responses
        try:
            if not capabilities.streaming:
                response = await responses.create(
                    model=request.model_id,
                    input=items,
                    instructions=instructions,
                    tools=tools or openai.omit,
                    tool_choice=tool_choice,
                    max_output_tokens=max_tokens,
                    text=text,
                    store=False,
                    include=INCLUDE,
                    extra_body=extra_body,
                )
                chunks = response_to_chunks(response)
            else:
                parser = StreamParser()
                events = await responses.create(
                    model=request.model_id,
                    input=items,
                    instructions=instructions,
                    tools=tools or openai.omit,
                    tool_choice=tool_choice,
                    max_output_tokens=max_tokens,
                    text=text,
                    store=False,
                    include=INCLUDE,
                    extra_body=extra_body,
                    stream=True,
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


def to_input_items(
    messages: Sequence[Message], *, thinking: bool = False
) -> list[ResponseInputItemParam]:
    """Messages neutres → éléments d'entrée de l'API, dans l'ordre."""
    items: list[_Item] = []
    echoed = reasoning_loop(messages) if thinking else set[int]()
    for position, message in enumerate(messages):
        match message.role:
            case "user":
                user: _Item = {"role": "user", "content": _user_content(message)}
                items.append(user)
            case "assistant":
                items += _assistant_items(message, clear=position in echoed)
            case "tool":
                for block in message.blocks:
                    if isinstance(block, ToolResultBlock):
                        result: _Item = {
                            "type": "function_call_output",
                            "call_id": block.call_id,
                            "output": _tool_output(block.output),
                        }
                        items.append(result)
    return cast(list[ResponseInputItemParam], items)


def to_response_tools(request: ModelRequest) -> list[FunctionToolParam]:
    tools: list[FunctionToolParam] = []
    for tool in request.tools:
        parameters: dict[str, object] = {**tool.input_schema}
        tools.append(
            {
                "type": "function",
                "name": tool.name,
                "description": tool.description,
                "parameters": parameters,
                "strict": is_strict(tool.input_schema),
            }
        )
    return tools


def to_text_config(schema: dict[str, JsonValue]) -> ResponseTextConfigParam:
    """Schéma de sortie du contrat → ``text.format`` (B9)."""
    return {
        "format": {
            "type": "json_schema",
            "name": OUTPUT_SCHEMA_NAME,
            "schema": {**schema},
            "strict": is_strict(schema),
        }
    }


def _user_content(message: Message) -> str | list[_Item]:
    """Texte seul, ou parties texte et image quand le message porte des images."""
    if not any(isinstance(block, InlineDataBlock) for block in message.blocks):
        return "\n\n".join(text for block in message.blocks if (text := _text(block)))
    return _parts(message.blocks)


def _parts(blocks: Sequence[ContentBlock]) -> list[_Item]:
    parts: list[_Item] = []
    for block in blocks:
        match block:
            case InlineDataBlock(media_type=media_type):
                url = f"data:{media_type};base64,{image_data(block)}"
                parts.append({"type": "input_image", "image_url": url, "detail": "auto"})
            case _:
                if text := _text(block):
                    parts.append({"type": "input_text", "text": text})
    return parts


def _text(block: ContentBlock) -> str:
    match block:
        case TextBlock(text=text):
            return text
        case JsonBlock(data=data):
            return json_text(data)
        case ArtifactRefBlock():
            raise unresolved(block)
        case _:
            return ""


def _assistant_items(message: Message, *, clear: bool) -> list[_Item]:
    """Une réponse du modèle, élément par élément, dans l'ordre de ses blocs.

    ``clear`` : son raisonnement en clair est renvoyé (boucle en cours, ``thinking``).
    """
    items: list[_Item] = []
    texts: list[str] = []

    def flush() -> None:
        text = "\n\n".join(t for t in texts if t)
        if text:
            items.append({"role": "assistant", "content": text})
        texts.clear()

    for block in message.blocks:
        match block:
            case ReasoningBlock():
                reasoning = _reasoning_item(block, clear=clear)
                if reasoning is not None:
                    flush()
                    items.append(reasoning)
            case ToolCallBlock(call_id=call_id, name=name, arguments=arguments):
                flush()
                items.append(
                    {
                        "type": "function_call",
                        "call_id": call_id,
                        "name": name,
                        "arguments": json_text(arguments),
                    }
                )
            case _:
                texts.append(_text(block))
    flush()
    return items


def _reasoning_item(block: ReasoningBlock, *, clear: bool) -> _Item | None:
    """Élément ``reasoning`` à renvoyer, ou None."""
    meta = block.provider_meta.get(PROVIDER)
    encrypted = meta.encrypted_content if isinstance(meta, OpenAIMeta) else None
    if encrypted is not None:
        summary = [{"type": "summary_text", "text": block.text}] if block.text else []
        return {"type": "reasoning", "summary": summary, "encrypted_content": encrypted}
    if clear and block.text:
        return {
            "type": "reasoning",
            "summary": [],
            "content": [{"type": "reasoning_text", "text": block.text}],
        }
    return None


def _tool_output(output: ToolOutput) -> str | list[_Item]:
    """Résultat d'un outil : son texte, ou ses parties quand il porte des images."""
    prefix = ERROR_PREFIX if output.is_error else ""
    if any(isinstance(block, InlineDataBlock) for block in output.blocks):
        parts = _parts(output.blocks)
        if prefix:
            parts.insert(0, {"type": "input_text", "text": prefix.strip()})
        return parts
    texts = [_text(block) for block in output.blocks]
    if not any(texts) and output.data is not None:
        texts = [json_text(output.data)]
    return prefix + "\n".join(texts)


# --- Réponse -------------------------------------------------------------------


class StreamParser:
    """Événements de l'API Responses → morceaux neutres."""

    def __init__(self) -> None:
        self._calls: set[int] = set()
        self._with_args: set[int] = set()
        self._with_text: set[int] = set()
        self._usage: Usage | None = None
        self._model: str | None = None
        self._incomplete: str | None = None
        self._refused = False

    def feed(self, event: ResponseStreamEvent) -> list[ModelChunk]:
        match event:
            case ResponseOutputItemAddedEvent(item=ResponseFunctionToolCall() as call):
                return [self.call_started(call, event.output_index)]
            case ResponseOutputItemAddedEvent(item=ResponseReasoningItem()):
                # Un bloc par élément de raisonnement, même sans texte (chiffré seulement).
                return [ReasoningDelta(start=True)]
            case ResponseReasoningSummaryPartAddedEvent(summary_index=index) if index > 0:
                return [ReasoningDelta(text="\n\n")]
            case (
                ResponseReasoningSummaryTextDeltaEvent(delta=delta)
                | ResponseReasoningTextDeltaEvent(delta=delta)
            ):
                return [ReasoningDelta(text=delta)]
            case ResponseTextDeltaEvent(delta=delta, output_index=index):
                self._with_text.add(index)
                return [TextDelta(text=delta)]
            case ResponseRefusalDeltaEvent(delta=delta, output_index=index):
                self._with_text.add(index)
                self._refused = True
                return [TextDelta(text=delta)]
            case ResponseFunctionCallArgumentsDeltaEvent(delta=delta, output_index=index):
                self._with_args.add(index)
                return [ToolArgsDelta(index=index, json_fragment=delta)]
            case ResponseOutputItemDoneEvent(item=item, output_index=index):
                return self.item_done(item, index)
            case (
                ResponseCompletedEvent(response=response)
                | ResponseIncompleteEvent(response=response)
            ):
                self.finished(response)
            case ResponseFailedEvent(response=response):
                raise _failed(response.error)
            case ResponseErrorEvent(code=code, message=message):
                raise classify_error(message, http_status=None, hints=(code,))
            case _:
                pass
        return []

    def finish(self) -> list[ModelChunk]:
        chunks: list[ModelChunk] = []
        if self._usage is not None:
            chunks.append(UsageDelta(usage=self._usage))
        chunks.append(Stopped(reason=self._stop_reason(), model_id=self._model))
        return chunks

    def call_started(self, call: ResponseFunctionToolCall, index: int) -> ToolCallStarted:
        self._calls.add(index)
        return ToolCallStarted(index=index, call_id=call.call_id, name=call.name)

    def item_done(self, item: ResponseOutputItem, index: int) -> list[ModelChunk]:
        """Élément terminé : ce que les deltas n'ont pas apporté, et sa fin."""
        match item:
            case ResponseFunctionToolCall(arguments=arguments):
                chunks: list[ModelChunk] = []
                if index not in self._with_args and arguments:
                    chunks.append(ToolArgsDelta(index=index, json_fragment=arguments))
                return [*chunks, ToolCallEnded(index=index)]
            case ResponseReasoningItem(id=item_id, encrypted_content=encrypted):
                # Le contenu chiffré complet n'est sûr qu'à la fin de l'élément.
                meta = OpenAIMeta(item_id=item_id, encrypted_content=encrypted)
                return [ReasoningDelta(provider_meta={PROVIDER: meta})]
            case ResponseOutputMessage(content=content) if index not in self._with_text:
                chunks = []
                for part in content:
                    if part.type == "refusal":
                        self._refused = True
                        chunks.append(TextDelta(text=part.refusal))
                    else:
                        chunks.append(TextDelta(text=part.text))
                return chunks
            case _:
                return []

    def finished(self, response: Response) -> None:
        """Fin de la réponse : modèle, consommation, motif d'une réponse incomplète."""
        self._model = response.model
        if response.usage is not None:
            self._usage = to_usage(response.usage)
        details = response.incomplete_details
        if response.status == "incomplete" and details is not None:
            self._incomplete = details.reason

    def _stop_reason(self) -> StopReason:
        if self._refused or self._incomplete == "content_filter":
            return "refusal"
        if self._incomplete == "max_output_tokens":
            return "max_tokens"
        return "tool_use" if self._calls else "end"


def response_to_chunks(response: Response) -> list[ModelChunk]:
    """Réponse non streamée → flux simulé, élément par élément."""
    parser = StreamParser()
    chunks: list[ModelChunk] = []
    for index, item in enumerate(response.output):
        match item:
            case ResponseReasoningItem(summary=summary, content=content):
                parts = [part.text for part in summary] + [part.text for part in content or ()]
                chunks.append(ReasoningDelta(text="\n\n".join(parts), start=True))
                chunks += parser.item_done(item, index)
            case ResponseFunctionToolCall():
                chunks.append(parser.call_started(item, index))
                chunks += parser.item_done(item, index)
            case ResponseOutputMessage():
                chunks += parser.item_done(item, index)
            case _:
                logger.debug("Élément de réponse ignoré : %s", item.type)
    parser.finished(response)
    return chunks + parser.finish()


def to_usage(usage: ResponseUsage) -> Usage:
    """Les tokens en cache sont compris dans ``input_tokens`` : ils en sont retirés."""
    details = usage.input_tokens_details
    cached = details.cached_tokens or 0
    written = details.cache_write_tokens or 0
    return Usage(
        input_tokens=max(0, usage.input_tokens - cached - written),
        output_tokens=usage.output_tokens,
        cache_read_tokens=cached,
        cache_write_tokens=written,
        reasoning_tokens=usage.output_tokens_details.reasoning_tokens or 0,
    )


def _failed(error: ResponseError | None) -> ModelError:
    if error is None:
        return ModelError("transient", "Réponse en échec, sans détail du fournisseur")
    return classify_error(error.message, http_status=None, hints=(error.code,))
