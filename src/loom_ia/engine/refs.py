# SPDX-License-Identifier: Apache-2.0
"""Références aux résultats d'outils : ``{"$ref": "result:3"}`` (#12).

L'orchestrateur transmet un résultat précédent à un outil sans le recopier.
L'exécuteur remplace la référence par le résultat avant de valider les
arguments : le ``data`` du résultat s'il existe, sinon son texte.

``result:3`` désigne le 3ᵉ appel d'outil du run, dans l'ordre où le modèle
les a demandés. Seuls les résultats du run courant sont adressables, et
seulement ceux obtenus avant le tour en cours : un appel du même tour n'a pas
encore de résultat, et le rejeu reste déterministe.

Certains modèles écrivent la référence sérialisée, en chaîne :
``"{\\"$ref\\": \\"result:3\\"}"``. Une chaîne dont tout le contenu est cet
objet est traitée comme la référence elle-même ; sinon, la valeur transmise
serait le texte de la référence, sans aucune erreur.

Pour que le modèle connaisse ces numéros, chaque résultat qu'il reçoit
commence par sa référence (``[result:3]``) et une consigne s'ajoute au prompt
système, dès qu'un rôle est proposé dans le run. Ce marquage n'existe que dans
la requête : le journal garde les résultats tels quels, dans leur ordre
d'arrivée, et la requête les remet dans l'ordre des appels.

Un résultat déporté (#16) n'a dans le journal qu'un aperçu : sa référence
transmet le contenu complet, relu dans le stockage d'artefacts. L'orchestrateur
peut ainsi passer un gros résultat à un rôle sans l'avoir lu.
"""

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Final, cast

from pydantic import JsonValue

from loom_ia.core.model import (
    JsonBlock,
    Message,
    OutputBlock,
    TextBlock,
    ToolOutput,
    ToolResultBlock,
)
from loom_ia.core.ports import ArtifactNotFound, ArtifactStore

REF_KEY: Final = "$ref"
REF_PREFIX: Final = "result:"
REFS_HINT: Final = (
    "Chaque résultat d'outil commence par sa référence, par exemple [result:3]. "
    "Pour passer un résultat à un argument prévu par un outil sans le recopier, "
    'donne à cet argument la valeur {"$ref": "result:3"} : un objet JSON, pas une chaîne.'
)


class RefError(Exception):
    """Référence impossible à résoudre ; le message est destiné au modèle."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True, slots=True)
class CallRecord:
    """Un appel d'outil du run, et son résultat s'il est arrivé."""

    number: int
    call_id: str
    name: str
    output: ToolOutput | None

    @property
    def ref(self) -> str:
        return f"{REF_PREFIX}{self.number}"

    @property
    def usable(self) -> bool:
        return self.output is not None and not self.output.is_error


def output_value(output: ToolOutput) -> JsonValue:
    """Ce qu'une référence transmet : ``data`` s'il existe, sinon le texte."""
    return output.data if output.data is not None else output.as_text


def output_text(output: ToolOutput) -> str:
    """Contenu d'un résultat en texte : le texte, ou le JSON de ``data``."""
    value = output_value(output)
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


class ResultIndex:
    """Appels d'outils d'un run, numérotés dans l'ordre des demandes du modèle.

    ``artifacts`` sert à relire le contenu complet d'un résultat déporté.
    """

    def __init__(self, messages: Iterable[Message], artifacts: ArtifactStore | None = None) -> None:
        self._artifacts = artifacts
        messages = tuple(messages)
        outputs = {
            block.call_id: block.output
            for message in messages
            if message.role == "tool"
            for block in message.blocks
            if isinstance(block, ToolResultBlock)
        }
        calls = [
            call
            for message in messages
            if message.role == "assistant"
            for call in message.tool_calls
        ]
        self.records: tuple[CallRecord, ...] = tuple(
            CallRecord(number, call.call_id, call.name, outputs.get(call.call_id))
            for number, call in enumerate(calls, start=1)
        )
        self._by_call = {record.call_id: record for record in self.records}

    def ref_of(self, call_id: str) -> str | None:
        record = self._by_call.get(call_id)
        return record.ref if record is not None else None

    def results_of(self, name: str) -> tuple[CallRecord, ...]:
        """Résultats réussis d'un outil, dans l'ordre des appels."""
        return tuple(r for r in self.records if r.name == name and r.usable)

    async def resolve(
        self, arguments: dict[str, JsonValue]
    ) -> tuple[dict[str, JsonValue], tuple[str, ...]]:
        """Arguments aux références remplacées, et les références résolues.

        Lève ``RefError`` si une référence ne peut pas être résolue.
        """
        found: list[str] = []
        resolved = cast(dict[str, JsonValue], await self._walk(arguments, found))
        return resolved, tuple(found)

    async def content(self, record: CallRecord) -> JsonValue:
        """Ce que transmet un résultat : ``data`` ou texte, relu s'il a été déporté.

        Lève ``RefError`` si le contenu déporté ne peut pas être relu.
        """
        if record.output is None:
            raise RefError(f"{record.ref} ({record.name}) n'a pas encore de résultat.")
        uri = record.output.offloaded
        if uri is None:
            return output_value(record.output)
        if self._artifacts is None:
            raise RefError(
                f"{record.ref} ({record.name}) a été déporté, et aucun stockage d'artefacts "
                "ne permet de le relire."
            )
        try:
            data = await self._artifacts.get(uri)
        except ArtifactNotFound as exc:
            raise RefError(f"{record.ref} ({record.name}) : contenu déporté introuvable.") from exc
        text = data.decode("utf-8")
        return cast(JsonValue, json.loads(text)) if uri.endswith(".json") else text

    async def text(self, record: CallRecord) -> str:
        """Contenu d'un résultat en texte : le texte, ou le JSON de ``data``."""
        value = await self.content(record)
        return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)

    async def _walk(self, value: JsonValue, found: list[str]) -> JsonValue:
        ref = _ref_in(value)
        if ref is not None:
            return await self._value(ref, found)
        if isinstance(value, dict):
            return {key: await self._walk(item, found) for key, item in value.items()}
        if isinstance(value, list):
            return [await self._walk(item, found) for item in value]
        return value

    async def _value(self, ref: str, found: list[str]) -> JsonValue:
        number = ref.removeprefix(REF_PREFIX)
        if not number.isdigit():
            raise RefError(
                f"Référence invalide : {ref!r}, attendu result:<numéro>. {self._known()}"
            )
        index = int(number) - 1
        if not 0 <= index < len(self.records):
            raise RefError(f"Référence inconnue : {ref}. {self._known()}")
        record = self.records[index]
        if record.output is None:
            raise RefError(
                f"{ref} ({record.name}) n'a pas encore de résultat : un appel du même "
                "tour ne peut pas être référencé."
            )
        if record.output.is_error:
            raise RefError(f"{ref} ({record.name}) est une erreur : il ne peut pas être transmis.")
        value = await self.content(record)
        if value == "":
            raise RefError(f"{ref} ({record.name}) est vide : rien à transmettre.")
        found.append(ref)
        return value

    def _known(self) -> str:
        usable = [f"{r.ref} ({r.name})" for r in self.records if r.usable]
        if not usable:
            return "Aucun résultat à référencer dans ce run."
        return f"Références disponibles : {', '.join(usable)}."


def _ref_in(value: JsonValue) -> str | None:
    """Référence portée par une valeur : l'objet ``{"$ref": …}``, ou ce même objet en chaîne."""
    if isinstance(value, str):
        text = value.strip()
        if not (text.startswith("{") and text.endswith("}") and REF_KEY in text):
            return None
        try:
            value = cast(JsonValue, json.loads(text))
        except ValueError:
            return None
    if isinstance(value, dict) and len(value) == 1:
        ref = value.get(REF_KEY)
        if isinstance(ref, str) and ref.startswith(REF_PREFIX):
            return ref
    return None


def in_call_order(messages: Sequence[Message]) -> tuple[Message, ...]:
    """Messages d'une requête, les résultats de chaque tour dans l'ordre des appels.

    Le journal garde les résultats dans leur ordre d'arrivée, qui dépend des
    durées des outils. La requête, elle, ne doit dépendre que de ce que le
    modèle a demandé : reproductible d'un run à l'autre, et stable pour le
    cache des fournisseurs.
    """
    ordered: list[Message] = []
    results: list[Message] = []
    positions: dict[str, int] = {}

    def position(message: Message) -> int:
        first = message.blocks[0]
        if isinstance(first, ToolResultBlock):
            return positions.get(first.call_id, len(positions))
        return len(positions)

    for message in messages:
        if message.role == "tool":
            results.append(message)
            continue
        ordered += sorted(results, key=position)
        results.clear()
        if message.role == "assistant":
            positions = {call.call_id: n for n, call in enumerate(message.tool_calls)}
        ordered.append(message)
    ordered += sorted(results, key=position)
    return tuple(ordered)


def mark_results(messages: Sequence[Message], index: ResultIndex) -> tuple[Message, ...]:
    """Messages d'une requête, chaque résultat réussi précédé de sa référence."""
    return tuple(
        _marked(message, index) if message.role == "tool" else message for message in messages
    )


def _marked(message: Message, index: ResultIndex) -> Message:
    blocks = tuple(
        _with_ref(block, ref)
        if isinstance(block, ToolResultBlock)
        and not block.output.is_error
        and (ref := index.ref_of(block.call_id)) is not None
        else block
        for block in message.blocks
    )
    return message.model_copy(update={"blocks": blocks})


def _with_ref(block: ToolResultBlock, ref: str) -> ToolResultBlock:
    content: tuple[OutputBlock, ...] = block.output.blocks
    if not content and block.output.data is not None:
        # Sans blocs, les adaptateurs montrent ``data`` : il ne doit pas disparaître.
        content = (JsonBlock(data=block.output.data),)
    output = block.output.model_copy(update={"blocks": (TextBlock(text=f"[{ref}]"), *content)})
    return block.model_copy(update={"output": output})
