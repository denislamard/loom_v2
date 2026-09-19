# SPDX-License-Identifier: Apache-2.0
"""Adaptateur ``sdk: fake`` : modèle scripté, sans réseau ni clé (O2).

Le script est lu dans ``params.script`` : une liste de réponses, chacune avec
un texte, des appels d'outils et éventuellement un raisonnement.

    models:
      - id: FAKE
        sdk: fake
        model: fake-1
        params:
          script:
            - tool_calls: [{name: calculer, arguments: {expr: "12*7+3"}}]
            - text: "12 * 7 + 3 = 87"

Le modèle est sans état : la réponse est choisie selon le nombre de réponses
déjà données depuis la dernière demande de l'utilisateur. Le même script sert
donc à chaque run, quel que soit le point d'accès. Sans script, le modèle
renvoie la demande en écho.

Une réponse peut dépendre des outils proposés au modèle : ``with_tool`` la
garde seulement si cet outil est proposé, ``without_tool`` seulement s'il ne
l'est pas. Un même script sert ainsi un run avec pièce jointe (rôle vision
visible) et un run sans (rôle masqué).

          - text: Je regarde la photo.
            with_tool: decrire_image
            tool_calls: [{name: decrire_image, arguments: {consigne: "Décris-la."}}]
"""

from collections.abc import AsyncGenerator
from typing import Final

from pydantic import Field, JsonValue, TypeAdapter

from loom_ia.core.model import (
    MOVED_IMAGES,
    ContentBlock,
    DomainModel,
    Message,
    ModelChunk,
    ModelRequest,
    ModelSpec,
    ReasoningBlock,
    TextBlock,
    ToolCallBlock,
    Usage,
    message_to_chunks,
)
from loom_ia.core.ports import ModelError

PROVIDER: Final = "fake"
_CHARS_PER_TOKEN: Final = 4


class FakeToolCall(DomainModel):
    name: str
    arguments: dict[str, JsonValue] = Field(default_factory=dict)


class FakeReply(DomainModel):
    text: str = ""
    tool_calls: tuple[FakeToolCall, ...] = ()
    reasoning: str | None = None
    # Réponse gardée seulement si cet outil est proposé au modèle, ou s'il ne l'est pas.
    with_tool: str | None = None
    without_tool: str | None = None

    def fits(self, tools: set[str]) -> bool:
        if self.with_tool is not None and self.with_tool not in tools:
            return False
        return self.without_tool is None or self.without_tool not in tools


_SCRIPT: Final = TypeAdapter(tuple[FakeReply, ...])


class FakeModel:
    """Client ``ModelClient`` scripté par la config."""

    def __init__(self, spec: ModelSpec) -> None:
        self.spec = spec
        raw = spec.params.get("script")
        self.script: tuple[FakeReply, ...] = () if raw is None else _SCRIPT.validate_python(raw)

    @property
    def provider(self) -> str:
        return PROVIDER

    async def aclose(self) -> None:
        pass

    def __repr__(self) -> str:
        return f"FakeModel({self.spec.id!r}, {len(self.script)} réponse(s))"

    async def stream(self, request: ModelRequest) -> AsyncGenerator[ModelChunk]:
        turn = _turn(request)
        offered = {tool.name for tool in request.tools}
        script = [reply for reply in self.script if reply.fits(offered)]
        if not self.script:
            reply = FakeReply(text=f"Écho : {_last_user_text(request)}")
        elif turn < len(script):
            reply = script[turn]
        else:
            raise ModelError(
                "invalid_request",
                f"Script du modèle {self.spec.id!r} épuisé : réponse n°{turn + 1} demandée, "
                f"{len(script)} prévue(s) avec ces outils",
            )
        message = _message(reply, turn)
        usage = Usage(
            input_tokens=len(request.model_dump_json()) // _CHARS_PER_TOKEN,
            output_tokens=len(message.model_dump_json()) // _CHARS_PER_TOKEN,
        )
        for chunk in message_to_chunks(message, usage=usage, model_id=request.model_id):
            yield chunk


def _turn(request: ModelRequest) -> int:
    """Réponses déjà données depuis la dernière demande de l'utilisateur.

    Le message qui porte les images des résultats d'outils n'est pas une demande.
    """
    count = 0
    for message in reversed(request.messages):
        if message.role == "user" and not _moved_images(message):
            break
        if message.role == "assistant":
            count += 1
    return count


def _moved_images(message: Message) -> bool:
    first = message.blocks[0]
    return isinstance(first, TextBlock) and first.text == MOVED_IMAGES


def _last_user_text(request: ModelRequest) -> str:
    for message in reversed(request.messages):
        if message.role == "user":
            return message.text
    return ""


def _message(reply: FakeReply, turn: int) -> Message:
    blocks: list[ContentBlock] = []
    if reply.reasoning:
        blocks.append(ReasoningBlock(text=reply.reasoning))
    if reply.text or not reply.tool_calls:
        blocks.append(TextBlock(text=reply.text))
    blocks += [
        ToolCallBlock(call_id=f"fake_{turn}_{i}", name=call.name, arguments=call.arguments)
        for i, call in enumerate(reply.tool_calls)
    ]
    return Message(role="assistant", blocks=tuple(blocks))
