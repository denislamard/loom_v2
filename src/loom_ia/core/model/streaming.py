# SPDX-License-Identifier: Apache-2.0
"""Requête, réponse et morceaux de flux d'un appel de modèle (#11).

Le streaming est la seule primitive des adaptateurs : ils émettent des
``ModelChunk`` neutres, que ``ResponseAccumulator`` rassemble en
``ModelResponse``. ``message_to_chunks`` fait le chemin inverse, pour les
fournisseurs sans streaming et les faux modèles.
"""

import hashlib
import json
from typing import Annotated, Final, Literal

from pydantic import Field, JsonValue, NonNegativeInt, PositiveInt

from loom_ia.core.model.base import DomainModel
from loom_ia.core.model.content import (
    ContentBlock,
    ProviderMeta,
    ReasoningBlock,
    TextBlock,
    ToolCallBlock,
)
from loom_ia.core.model.messages import Message
from loom_ia.core.model.tooling import ToolDefinition
from loom_ia.core.model.usage import Usage

type StopReason = Literal["end", "tool_use", "max_tokens", "refusal"]
# ``required`` impose un appel d'outil (backlog #012) ; jamais en ``FINALIZING``.
type ToolChoice = Literal["auto", "none", "required"]
# Classement neutre des erreurs d'appel (#10).
type ModelErrorKind = Literal[
    "transient",
    "overloaded",
    "quota_exhausted",
    "context_overflow",
    "auth",
    "invalid_request",
    "content_filtered",
]

# Clé posée dans les arguments quand le modèle a produit un JSON illisible :
# l'exécuteur renvoie alors une erreur que le modèle peut corriger.
INVALID_JSON_KEY: Final = "_loom_invalid_json"


# --- Requête -----------------------------------------------------------------


class ModelRequest(DomainModel):
    model_id: str
    system: str = ""
    messages: tuple[Message, ...]
    tools: tuple[ToolDefinition, ...] = ()
    tool_choice: ToolChoice = "auto"
    max_tokens: PositiveInt | None = None
    params: dict[str, JsonValue] = Field(default_factory=dict)

    def request_hash(self) -> str:
        """Empreinte SHA-256 du JSON canonique de la requête (#31)."""
        canonical = json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        return hashlib.sha256(canonical.encode()).hexdigest()


# --- Morceaux de flux --------------------------------------------------------


class TextDelta(DomainModel):
    type: Literal["text_delta"] = "text_delta"
    text: str


class ReasoningDelta(DomainModel):
    type: Literal["reasoning_delta"] = "reasoning_delta"
    text: str = ""
    # Vrai au premier morceau d'un bloc : deux blocs qui se suivent restent
    # distincts (chacun a sa signature).
    start: bool = False
    # Les données du fournisseur (signature…) peuvent arriver en cours de flux.
    provider_meta: dict[str, ProviderMeta] = Field(default_factory=dict)


class ToolCallStarted(DomainModel):
    type: Literal["tool_call_started"] = "tool_call_started"
    index: NonNegativeInt
    call_id: str
    name: str


class ToolArgsDelta(DomainModel):
    type: Literal["tool_args_delta"] = "tool_args_delta"
    index: NonNegativeInt
    json_fragment: str


class ToolCallEnded(DomainModel):
    type: Literal["tool_call_ended"] = "tool_call_ended"
    index: NonNegativeInt


class UsageDelta(DomainModel):
    """Consommation à ajouter au total (certains fournisseurs la donnent en fin de flux)."""

    type: Literal["usage"] = "usage"
    usage: Usage


class Stopped(DomainModel):
    type: Literal["stopped"] = "stopped"
    reason: StopReason
    # Modèle effectivement utilisé, s'il diffère de celui demandé.
    model_id: str | None = None


class StreamReset(DomainModel):
    """Émis par le moteur seulement, avant de relancer un appel déjà commencé.

    Le texte partiel reçu jusque-là doit être effacé.
    """

    type: Literal["reset"] = "reset"
    # Tentative qui va commencer.
    attempt: PositiveInt


type ModelChunk = Annotated[
    TextDelta
    | ReasoningDelta
    | ToolCallStarted
    | ToolArgsDelta
    | ToolCallEnded
    | UsageDelta
    | Stopped
    | StreamReset,
    Field(discriminator="type"),
]


# --- Réponse -----------------------------------------------------------------


class ModelResponse(DomainModel):
    model_id: str
    provider: str
    message: Message
    usage: Usage = Usage()
    stop_reason: StopReason = "end"


class _PendingCall:
    def __init__(self, call_id: str, name: str) -> None:
        self.call_id = call_id
        self.name = name
        self.fragments: list[str] = []

    def block(self) -> ToolCallBlock:
        raw = "".join(self.fragments).strip()
        arguments: dict[str, JsonValue]
        try:
            parsed: JsonValue = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            arguments = parsed
        else:
            arguments = {INVALID_JSON_KEY: raw}
        return ToolCallBlock(call_id=self.call_id, name=self.name, arguments=arguments)


class ResponseAccumulator:
    """Rassemble les morceaux d'un flux en message, dans l'ordre d'arrivée."""

    def __init__(self) -> None:
        self._reset()

    def _reset(self) -> None:
        # Blocs terminés, ou en cours de construction pour texte et raisonnement.
        self._parts: list[ContentBlock | _PendingCall] = []
        self._calls: dict[int, _PendingCall] = {}
        self._text: list[str] | None = None
        self._reasoning: list[str] | None = None
        self._reasoning_meta: dict[str, ProviderMeta] = {}
        self.usage = Usage()
        self.stop_reason: StopReason | None = None
        self.model_id: str | None = None

    def add(self, chunk: ModelChunk) -> None:
        match chunk:
            case TextDelta(text=text):
                self._flush_reasoning()
                if self._text is None:
                    self._text = []
                self._text.append(text)
            case ReasoningDelta(text=text, provider_meta=meta, start=start):
                self._flush_text()
                if start:
                    self._flush_reasoning()
                if self._reasoning is None:
                    self._reasoning = []
                self._reasoning.append(text)
                self._reasoning_meta.update(meta)
            case ToolCallStarted(index=index, call_id=call_id, name=name):
                self._flush_text()
                self._flush_reasoning()
                call = _PendingCall(call_id, name)
                self._calls[index] = call
                self._parts.append(call)
            case ToolArgsDelta(index=index, json_fragment=fragment):
                self._calls[index].fragments.append(fragment)
            case ToolCallEnded():
                pass
            case UsageDelta(usage=usage):
                self.usage += usage
            case Stopped(reason=reason, model_id=model_id):
                self.stop_reason = reason
                self.model_id = model_id
            case StreamReset():
                self._reset()

    def result(self, *, model_id: str, provider: str) -> ModelResponse:
        self._flush_text()
        self._flush_reasoning()
        blocks = tuple(p.block() if isinstance(p, _PendingCall) else p for p in self._parts)
        if not blocks:
            blocks = (TextBlock(text=""),)
        has_calls = any(isinstance(b, ToolCallBlock) for b in blocks)
        stop = self.stop_reason or ("tool_use" if has_calls else "end")
        return ModelResponse(
            model_id=self.model_id or model_id,
            provider=provider,
            message=Message(role="assistant", blocks=blocks),
            usage=self.usage,
            stop_reason=stop,
        )

    def _flush_text(self) -> None:
        if self._text is not None:
            self._parts.append(TextBlock(text="".join(self._text)))
            self._text = None

    def _flush_reasoning(self) -> None:
        if self._reasoning is not None:
            self._parts.append(
                ReasoningBlock(text="".join(self._reasoning), provider_meta=self._reasoning_meta)
            )
            self._reasoning = None
            self._reasoning_meta = {}


def message_to_chunks(
    message: Message,
    *,
    usage: Usage | None = None,
    stop_reason: StopReason | None = None,
    model_id: str | None = None,
    fragment_size: int = 8,
) -> list[ModelChunk]:
    """Morceaux de flux qu'un fournisseur enverrait pour ce message."""
    chunks: list[ModelChunk] = []
    index = 0
    for block in message.blocks:
        match block:
            case TextBlock(text=text):
                chunks += [TextDelta(text=part) for part in _split(text, fragment_size)]
            case ReasoningBlock(text=text, provider_meta=meta):
                parts = _split(text, fragment_size)
                last = len(parts) - 1
                chunks += [
                    ReasoningDelta(text=part, start=i == 0, provider_meta=meta if i == last else {})
                    for i, part in enumerate(parts)
                ]
            case ToolCallBlock(call_id=call_id, name=name, arguments=arguments):
                chunks.append(ToolCallStarted(index=index, call_id=call_id, name=name))
                raw = json.dumps(arguments, ensure_ascii=False)
                chunks += [
                    ToolArgsDelta(index=index, json_fragment=part)
                    for part in _split(raw, fragment_size)
                ]
                chunks.append(ToolCallEnded(index=index))
                index += 1
            case _:
                raise ValueError(f"Bloc {block.type!r} impossible dans une réponse de modèle")
    if usage is not None:
        chunks.append(UsageDelta(usage=usage))
    reason: StopReason = stop_reason or ("tool_use" if message.tool_calls else "end")
    chunks.append(Stopped(reason=reason, model_id=model_id))
    return chunks


def _split(text: str, size: int) -> list[str]:
    """Découpe en morceaux ; un texte vide donne un morceau vide."""
    return [text[i : i + size] for i in range(0, len(text), size)] or [""]
