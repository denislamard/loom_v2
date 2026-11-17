# SPDX-License-Identifier: Apache-2.0
"""Requête, réponse et morceaux de flux d'un appel de modèle (point 11, fonction B8).

Le streaming est la seule primitive des adaptateurs : ils émettent des
``ModelChunk`` neutres, que ``ResponseAccumulator`` rassemble en
``ModelResponse``. ``message_to_chunks`` fait le chemin inverse, pour les
fournisseurs sans streaming et les faux modèles.

Morceaux, par ``type`` : ``text_delta``, ``reasoning_delta``,
``tool_call_started``, ``tool_args_delta``, ``tool_call_ended``, ``usage``,
``stopped`` (fin propre) et ``reset`` (émis par le moteur avant de relancer un
appel déjà commencé). Ce sont eux que reçoit ``on_chunk`` et que diffusent
``stream()`` et le SSE.
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
"""Pourquoi le modèle s'est arrêté : fin, appel d'outils, limite de tokens, refus."""
type ToolChoice = Literal["auto", "none", "required"]
"""Choix d'outil imposé au modèle ; ``required`` impose un appel (backlog #012), jamais en ``FINALIZING``."""
type ModelErrorKind = Literal[
    "transient",
    "overloaded",
    "quota_exhausted",
    "context_overflow",
    "auth",
    "invalid_request",
    "content_filtered",
    # Sortie coupée par la limite de tokens (``max_tokens``) : jamais une réponse.
    "truncated",
    # Tous les modèles de la chaîne sont écartés par leur disjoncteur (#10).
    "unavailable",
]
"""Genre d'une erreur d'appel de modèle, classée de façon neutre (point 10) ; voir ``ModelError``."""

INVALID_JSON_KEY: Final = "_loom_invalid_json"
"""Clé posée dans les arguments quand le modèle a produit un JSON illisible : l'exécuteur rend alors une erreur que le modèle peut corriger."""


# --- Requête -----------------------------------------------------------------


class ModelRequest(DomainModel):
    """Requête neutre à un modèle, que l'adaptateur traduit pour son fournisseur.

    C'est ce que voit une politique ``before_model`` et ce qu'elle peut
    remplacer. Son empreinte (``request_hash``) est écrite dans
    ``model.responded`` pour le rejeu.

    Attributes:
        model_id: Nom du modèle chez le fournisseur.
        system: Prompt système.
        messages: Conversation, dans l'ordre.
        tools: Outils exposés au modèle (nom, description, schéma).
        tool_choice: ``auto`` (défaut), ``none`` ou ``required``.
        max_tokens: Plafond de tokens de sortie.
        params: Réglages transmis tels quels au fournisseur.
        output_schema: Schéma JSON de la réponse attendue (contrat de
            sortie) : l'adaptateur le transmet au fournisseur si le modèle a
            la capacité ``native_json`` (fonction B9).
    """

    model_id: str
    system: str = ""
    messages: tuple[Message, ...]
    tools: tuple[ToolDefinition, ...] = ()
    tool_choice: ToolChoice = "auto"
    max_tokens: PositiveInt | None = None
    params: dict[str, JsonValue] = Field(default_factory=dict)
    output_schema: dict[str, JsonValue] | None = None

    def request_hash(self) -> str:
        """Empreinte SHA-256 du JSON canonique de la requête (point 31) : le rejeu détecte ainsi une divergence.

        Sans schéma de sortie, le champ est omis : les empreintes des
        journaux antérieurs restent les mêmes.
        """
        omitted = {"output_schema"} if self.output_schema is None else None
        canonical = json.dumps(
            self.model_dump(mode="json", exclude=omitted),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        return hashlib.sha256(canonical.encode()).hexdigest()

    def request_parts(self) -> dict[str, str]:
        """Empreinte de chaque partie de la requête : ce qui a changé, au rejeu (phase 6.2a).

        ``request_hash`` dit qu'une requête diffère ; ces empreintes disent
        **où** : le modèle, le prompt système, les outils, les messages ou les
        réglages (choix d'outil, plafond de tokens, paramètres, schéma de
        sortie). Seize caractères suffisent à comparer, pas à retrouver.

        Returns:
            ``model``, ``system``, ``tools``, ``messages``, ``settings`` →
            empreinte de 16 caractères, et ``messages_count`` → le nombre de
            messages, en clair.
        """
        dumped = self.model_dump(mode="json")
        groups: dict[str, JsonValue] = {
            "model": dumped["model_id"],
            "system": dumped["system"],
            "tools": dumped["tools"],
            "messages": dumped["messages"],
            "settings": {key: dumped[key] for key in ("tool_choice", "max_tokens", "params", "output_schema")},
        }
        parts = {name: _short_hash(value) for name, value in groups.items()}
        # Le nombre de messages se lit en clair : « 5 au journal, 6 maintenant »
        # dit tout de suite qu'un historique a grandi ou rétréci.
        parts["messages_count"] = str(len(self.messages))
        return parts


def _short_hash(value: JsonValue) -> str:
    """Les 16 premiers caractères du SHA-256 du JSON canonique d'une valeur."""
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


# --- Morceaux de flux --------------------------------------------------------


class TextDelta(DomainModel):
    """Morceau de texte de la réponse.

    Attributes:
        type: ``text_delta``.
        text: Le texte à ajouter.
    """

    type: Literal["text_delta"] = "text_delta"
    text: str


class ReasoningDelta(DomainModel):
    """Morceau du raisonnement du modèle.

    Attributes:
        type: ``reasoning_delta``.
        text: Le texte à ajouter.
        start: Vrai au premier morceau d'un bloc : deux blocs qui se suivent
            restent distincts (chacun a sa signature).
        provider_meta: Données du fournisseur (signature…), qui peuvent
            arriver en cours de flux.
    """

    type: Literal["reasoning_delta"] = "reasoning_delta"
    text: str = ""
    start: bool = False
    provider_meta: dict[str, ProviderMeta] = Field(default_factory=dict)


class ToolCallStarted(DomainModel):
    """Début d'un appel d'outil dans la réponse.

    Attributes:
        type: ``tool_call_started``.
        index: Rang de l'appel dans la réponse, qui relie les morceaux
            d'arguments.
        call_id: Identifiant de l'appel.
        name: Nom de l'outil.
    """

    type: Literal["tool_call_started"] = "tool_call_started"
    index: NonNegativeInt
    call_id: str
    name: str


class ToolArgsDelta(DomainModel):
    """Morceau des arguments JSON d'un appel d'outil.

    Attributes:
        type: ``tool_args_delta``.
        index: Rang de l'appel.
        json_fragment: Fragment du JSON des arguments.
    """

    type: Literal["tool_args_delta"] = "tool_args_delta"
    index: NonNegativeInt
    json_fragment: str


class ToolCallEnded(DomainModel):
    """Fin d'un appel d'outil dans la réponse.

    Attributes:
        type: ``tool_call_ended``.
        index: Rang de l'appel.
    """

    type: Literal["tool_call_ended"] = "tool_call_ended"
    index: NonNegativeInt


class UsageDelta(DomainModel):
    """Consommation à ajouter au total (certains fournisseurs la donnent en fin de flux).

    Attributes:
        type: ``usage``.
        usage: Les tokens à ajouter.
    """

    type: Literal["usage"] = "usage"
    usage: Usage


class Stopped(DomainModel):
    """Fin propre du flux ; sans lui, le flux s'est interrompu et l'appel est une erreur ``transient``.

    Attributes:
        type: ``stopped``.
        reason: Pourquoi le modèle s'est arrêté.
        model_id: Modèle effectivement utilisé, s'il diffère de celui
            demandé.
    """

    type: Literal["stopped"] = "stopped"
    reason: StopReason
    model_id: str | None = None


class StreamReset(DomainModel):
    """Émis par le moteur seulement, avant de relancer un appel déjà commencé.

    Le texte partiel reçu jusque-là doit être effacé (nouvelle tentative, ou
    bascule vers le secours).

    Attributes:
        type: ``reset``.
        attempt: Tentative qui va commencer.
    """

    type: Literal["reset"] = "reset"
    attempt: PositiveInt


type ModelChunk = Annotated[
    TextDelta | ReasoningDelta | ToolCallStarted | ToolArgsDelta | ToolCallEnded | UsageDelta | Stopped | StreamReset,
    Field(discriminator="type"),
]
"""Morceau du flux d'un appel de modèle, discriminé par ``type``."""


# --- Réponse -----------------------------------------------------------------


class ModelResponse(DomainModel):
    """Réponse complète d'un modèle, assemblée depuis le flux.

    Attributes:
        model_id: Nom du modèle qui a répondu.
        provider: Fournisseur.
        message: Le message ``assistant`` : texte, raisonnement, appels
            d'outils.
        usage: Tokens consommés.
        stop_reason: Pourquoi le modèle s'est arrêté. Défaut : ``end``.
    """

    model_id: str
    provider: str
    message: Message
    usage: Usage = Usage()
    stop_reason: StopReason = "end"


class _PendingCall:
    """Appel d'outil en cours d'assemblage : ses fragments d'arguments."""

    def __init__(self, call_id: str, name: str) -> None:
        self.call_id = call_id
        self.name = name
        self.fragments: list[str] = []

    def block(self) -> ToolCallBlock:
        """Le bloc d'appel ; un JSON illisible ou qui n'est pas un objet est rangé sous ``INVALID_JSON_KEY``."""
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
    """Rassemble les morceaux d'un flux en message, dans l'ordre d'arrivée.

    Un ``StreamReset`` efface tout ce qui a été reçu. Les deux appels d'une
    même réponse ne peuvent pas porter le même ``call_id`` : le second est
    renommé (``_2``, ``_3``…).

    Attributes:
        usage: Tokens cumulés jusqu'ici.
        stop_reason: Raison d'arrêt, une fois ``Stopped`` reçu.
        model_id: Modèle annoncé par ``Stopped``, s'il y en a un.
    """

    def __init__(self) -> None:
        self._reset()

    def _reset(self) -> None:
        """Remet l'accumulateur à vide."""
        # Blocs terminés, ou en cours de construction pour texte et raisonnement.
        self._parts: list[ContentBlock | _PendingCall] = []
        self._calls: dict[int, _PendingCall] = {}
        self._text: list[str] | None = None
        self._reasoning: list[str] | None = None
        self._reasoning_meta: dict[str, ProviderMeta] = {}
        self.usage = Usage()
        self.stop_reason: StopReason | None = None
        self.model_id: str | None = None

    @property
    def stopped(self) -> bool:
        """Vrai si le flux a livré son ``Stopped`` ; sans lui, il s'est interrompu en route."""
        return self.stop_reason is not None

    def add(self, chunk: ModelChunk) -> None:
        """Ajoute un morceau : texte et raisonnement se concatènent par bloc, les arguments par appel."""
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
        """La réponse assemblée.

        Args:
            model_id: Modèle demandé ; remplacé par celui de ``Stopped`` s'il
                en annonce un.
            provider: Fournisseur.

        Returns:
            La réponse ; un texte vide si le flux n'a rien livré. Sans
            ``Stopped``, la raison est ``tool_use`` s'il y a des appels,
            ``end`` sinon.
        """
        self._flush_text()
        self._flush_reasoning()
        blocks = _distinct_call_ids(tuple(p.block() if isinstance(p, _PendingCall) else self._stamped(p, model_id) for p in self._parts))
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
            self._parts.append(ReasoningBlock(text="".join(self._reasoning), provider_meta=self._reasoning_meta))
            self._reasoning = None
            self._reasoning_meta = {}

    @staticmethod
    def _stamped(block: ContentBlock, model_id: str) -> ContentBlock:
        """Raisonnement marqué du modèle demandé : après une bascule, il sera écarté (#7)."""
        if isinstance(block, ReasoningBlock) and block.model_id is None:
            return block.model_copy(update={"model_id": model_id})
        return block


def _distinct_call_ids(blocks: tuple[ContentBlock, ...]) -> tuple[ContentBlock, ...]:
    """Deux appels d'une même réponse ne portent jamais le même ``call_id``.

    Un fournisseur qui répète un identifiant (vu chez des serveurs compatibles
    qui numérotent leurs appels) ferait écrire deux ``tool.completed`` pour un
    seul appel en attente : la projection lèverait ``ProjectionError`` et la
    session deviendrait illisible. Le second reçoit donc un suffixe (``_2``,
    ``_3``…) qui ne heurte aucun autre identifiant de la réponse. Une réponse
    dont les identifiants sont distincts passe telle quelle, octet pour octet.
    """
    taken = {b.call_id for b in blocks if isinstance(b, ToolCallBlock)}
    seen: set[str] = set()
    distinct: list[ContentBlock] = []
    for block in blocks:
        if isinstance(block, ToolCallBlock):
            if block.call_id in seen:
                n = 2
                while f"{block.call_id}_{n}" in taken | seen:
                    n += 1
                block = block.model_copy(update={"call_id": f"{block.call_id}_{n}"})
            seen.add(block.call_id)
        distinct.append(block)
    return tuple(distinct)


def message_to_chunks(
    message: Message,
    *,
    usage: Usage | None = None,
    stop_reason: StopReason | None = None,
    model_id: str | None = None,
    fragment_size: int = 8,
) -> list[ModelChunk]:
    """Morceaux de flux qu'un fournisseur enverrait pour ce message.

    Sert aux fournisseurs sans streaming (``capabilities.streaming: false``) et
    aux faux modèles.

    Args:
        message: Message ``assistant`` : texte, raisonnement, appels d'outils.
        usage: Consommation à annoncer en fin de flux.
        stop_reason: Raison d'arrêt ; par défaut ``tool_use`` s'il y a des
            appels, ``end`` sinon.
        model_id: Modèle annoncé par ``Stopped``.
        fragment_size: Taille des morceaux de texte, en caractères. Défaut :
            8.

    Returns:
        Les morceaux, ``Stopped`` en dernier.

    Raises:
        ValueError: Le message porte un bloc qu'une réponse de modèle ne
            peut pas contenir.
    """
    chunks: list[ModelChunk] = []
    index = 0
    for block in message.blocks:
        match block:
            case TextBlock(text=text):
                chunks += [TextDelta(text=part) for part in _split(text, fragment_size)]
            case ReasoningBlock(text=text, provider_meta=meta):
                parts = _split(text, fragment_size)
                last = len(parts) - 1
                chunks += [ReasoningDelta(text=part, start=i == 0, provider_meta=meta if i == last else {}) for i, part in enumerate(parts)]
            case ToolCallBlock(call_id=call_id, name=name, arguments=arguments):
                chunks.append(ToolCallStarted(index=index, call_id=call_id, name=name))
                raw = json.dumps(arguments, ensure_ascii=False)
                chunks += [ToolArgsDelta(index=index, json_fragment=part) for part in _split(raw, fragment_size)]
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
