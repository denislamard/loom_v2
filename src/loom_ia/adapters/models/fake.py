# SPDX-License-Identifier: Apache-2.0
"""Adaptateur ``sdk: fake`` : modèle scripté, sans réseau ni clé (fonction O2).

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

Une réponse peut aussi dépendre de la demande : ``with_text`` la garde
seulement si le dernier message de l'utilisateur contient ce texte,
``without_text`` seulement s'il ne le contient pas. Un rôle, appelé à chaque
fois avec un seul message, répond ainsi selon ce qu'on lui demande.

Une réponse peut être une panne : ``error`` (``transient``, ``overloaded``,
``quota_exhausted``…) fait lever l'erreur de ce type à la place de la réponse,
à chaque tentative. Un modèle dont le script ne contient que ``- error:
overloaded`` est en panne pour tous les appels : de quoi montrer un secours
(point 10).

Sans HTTP, pas d'échange brut à garder : quand le moteur ouvre un registre
(``telemetry.capture.raw_exchanges``, jalon J6.1b), le modèle y dépose un échange
**synthétique** — la requête reçue et la réponse rendue, en JSON, marquées
``synthetic`` — pour que le chemin des échanges s'éprouve sans réseau.

Comme un fournisseur, le modèle respecte ``tool_choice`` : avec ``required``,
une réponse sans appel d'outil est une erreur du script ; avec ``none``, une
réponse qui appelle des outils donne à la place son texte ``forced`` (la
réponse forcée d'un run arrêté), ou à défaut son texte. Un diagnostic de
réparation (point 20) n'est pas une nouvelle demande, ni la consigne de la réponse
forcée : le script continue.
"""

import json
from collections.abc import AsyncGenerator
from typing import Final

from pydantic import Field, JsonValue, TypeAdapter

from loom_ia.core.model import (
    CHARS_PER_TOKEN,
    FINALIZE_HINT,
    MOVED_IMAGES,
    REPAIR_PREFIX,
    ContentBlock,
    DomainModel,
    Message,
    ModelChunk,
    ModelErrorKind,
    ModelRequest,
    ModelSpec,
    ReasoningBlock,
    TextBlock,
    ToolCallBlock,
    Usage,
    is_session_state,
    is_system_note,
    message_to_chunks,
)
from loom_ia.core.ports import ModelError, RawExchange, exchange_log

PROVIDER: Final = "fake"
"""Nom du fournisseur, tel que le journal et la télémétrie l'écrivent."""


class FakeToolCall(DomainModel):
    """Un appel d'outil du script.

    Attributes:
        name: Nom de l'outil.
        arguments: Ses arguments.
    """

    name: str
    arguments: dict[str, JsonValue] = Field(default_factory=dict)


class FakeReply(DomainModel):
    """Une réponse du script : texte, appels d'outils, raisonnement, conditions et panne.

    Attributes:
        text: Texte de la réponse.
        tool_calls: Appels d'outils de la réponse.
        reasoning: Raisonnement rendu avant le texte.
        with_tool: Réponse gardée seulement si cet outil est proposé au modèle.
        without_tool: Réponse gardée seulement si cet outil n'est pas proposé.
        with_text: Réponse gardée seulement si la dernière demande contient ce
            texte.
        without_text: Réponse gardée seulement si elle ne le contient pas.
        forced: Texte rendu à la place des appels d'outils quand la requête les
            interdit (``tool_choice: none``).
        error: Panne simulée : erreur de ce type levée à la place de la
            réponse.
    """

    text: str = ""
    tool_calls: tuple[FakeToolCall, ...] = ()
    reasoning: str | None = None
    with_tool: str | None = None
    without_tool: str | None = None
    with_text: str | None = None
    without_text: str | None = None
    forced: str | None = None
    error: ModelErrorKind | None = None

    def fits(self, tools: set[str], asked: str) -> bool:
        """Vrai si les outils proposés et la dernière demande remplissent les conditions de la réponse.

        Conditions : ``with_tool``, ``without_tool``, ``with_text``,
        ``without_text``.

        Args:
            tools: Noms des outils proposés au modèle.
            asked: Texte de la dernière demande de l'utilisateur.
        """
        if self.with_tool is not None and self.with_tool not in tools:
            return False
        if self.without_tool is not None and self.without_tool in tools:
            return False
        if self.with_text is not None and self.with_text not in asked:
            return False
        return self.without_text is None or self.without_text not in asked


_SCRIPT: Final = TypeAdapter(tuple[FakeReply, ...])


class FakeModel:
    """Client ``ModelClient`` scripté par la config."""

    def __init__(self, spec: ModelSpec) -> None:
        self.spec = spec
        raw = spec.params.get("script")
        self.script: tuple[FakeReply, ...] = () if raw is None else _SCRIPT.validate_python(raw)

    @property
    def provider(self) -> str:
        """Nom du fournisseur : ``fake``."""
        return PROVIDER

    async def aclose(self) -> None:
        """Rien à fermer."""
        pass

    def __repr__(self) -> str:
        return f"FakeModel({self.spec.id!r}, {len(self.script)} réponse(s))"

    def _deposit(self, request: ModelRequest, message: Message | None, error: str | None) -> None:
        """Échange synthétique, si le moteur en garde : ce qui a été reçu et rendu."""
        log = exchange_log()
        if log is None:
            return
        log.record(
            RawExchange(
                method="POST",
                url=f"fake://{self.spec.model}",
                request_body=_json(request.model_dump(mode="json")),
                response_body=b"" if message is None else _json(message.model_dump(mode="json")),
                status=None if message is None else 200,
                request_headers={"content-type": "application/json"},
                response_headers={} if message is None else {"content-type": "application/json"},
                error=error,
                synthetic=True,
            )
        )

    async def stream(self, request: ModelRequest) -> AsyncGenerator[ModelChunk]:
        """Rend la réponse du script qui suit, en morceaux neutres.

        La réponse est choisie parmi celles qui conviennent, selon le nombre de
        réponses déjà données depuis la dernière demande ; sans script, l'écho de
        la demande. ``tool_choice: none`` remplace les appels d'outils par le
        texte ``forced`` ; la consommation est estimée sur la taille des messages.

        Args:
            request: La requête neutre.

        Yields:
            Les morceaux de la réponse.

        Raises:
            ModelError: Script épuisé, réponse sans appel d'outil quand la requête
                en impose un (``invalid_request``), ou panne simulée (``error``).
        """
        turn = _turn(request)
        offered = {tool.name for tool in request.tools}
        asked = _last_user_text(request)
        script = [reply for reply in self.script if reply.fits(offered, asked)]
        if not self.script:
            reply = FakeReply(text=f"Écho : {_last_user_text(request)}")
        elif turn < len(script):
            reply = script[turn]
        else:
            raise ModelError(
                "invalid_request",
                f"Script du modèle {self.spec.id!r} épuisé : réponse n°{turn + 1} demandée, {len(script)} prévue(s) avec ces outils",
            )
        if reply.error is not None:
            error = ModelError(
                reply.error,
                f"Panne simulée par le script du modèle {self.spec.id!r} (réponse n°{turn + 1} : {reply.error})",
            )
            self._deposit(request, None, error.message)
            raise error
        if request.tool_choice == "none" and reply.tool_calls:
            reply = FakeReply(text=reply.forced if reply.forced is not None else reply.text)
        message = _message(reply, turn)
        if request.tool_choice == "required" and request.tools and not message.tool_calls:
            raise ModelError(
                "invalid_request",
                f"Script du modèle {self.spec.id!r} : réponse n°{turn + 1} sans appel d'outil, alors que la requête en impose un (tool_choice: required)",
            )
        usage = Usage(
            input_tokens=len(request.model_dump_json()) // CHARS_PER_TOKEN,
            output_tokens=len(message.model_dump_json()) // CHARS_PER_TOKEN,
        )
        self._deposit(request, message, None)
        for chunk in message_to_chunks(message, usage=usage, model_id=request.model_id):
            yield chunk


def _json(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False).encode("utf-8")


def _turn(request: ModelRequest) -> int:
    """Réponses déjà données depuis la dernière demande de l'utilisateur.

    Ne sont pas des demandes : le message qui porte les images des résultats
    d'outils, le diagnostic d'une réparation, la consigne de la réponse forcée,
    le state de la session, une note du système.
    """
    count = 0
    for position in range(len(request.messages) - 1, -1, -1):
        message = request.messages[position]
        if message.role == "user" and not _engine_note(message, first=position == 0):
            break
        if message.role == "assistant":
            count += 1
    return count


def _moved_images(message: Message) -> bool:
    first = message.blocks[0]
    return isinstance(first, TextBlock) and first.text == MOVED_IMAGES


def _repair(message: Message) -> bool:
    first = message.blocks[0]
    return isinstance(first, TextBlock) and first.text.startswith(REPAIR_PREFIX)


def _engine_note(message: Message, *, first: bool) -> bool:
    """Message ajouté par le moteur : images déplacées, réparation, réponse forcée, state, note.

    Le state n'est jamais le premier message (``first``) : la demande le précède.
    Un message de rôle qui commence par sa section ``<session_state>`` est une demande.
    """
    return _moved_images(message) or _repair(message) or message.text == FINALIZE_HINT or (is_session_state(message) and not first) or is_system_note(message)


def _last_user_text(request: ModelRequest) -> str:
    """Dernière demande de l'utilisateur, sans les messages ajoutés par le moteur."""
    for position in range(len(request.messages) - 1, -1, -1):
        message = request.messages[position]
        if message.role == "user" and not _engine_note(message, first=position == 0):
            return message.text
    return ""


def _message(reply: FakeReply, turn: int) -> Message:
    blocks: list[ContentBlock] = []
    if reply.reasoning:
        blocks.append(ReasoningBlock(text=reply.reasoning))
    if reply.text or not reply.tool_calls:
        blocks.append(TextBlock(text=reply.text))
    blocks += [ToolCallBlock(call_id=f"fake_{turn}_{i}", name=call.name, arguments=call.arguments) for i, call in enumerate(reply.tool_calls)]
    return Message(role="assistant", blocks=tuple(blocks))
