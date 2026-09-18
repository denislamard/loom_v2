# SPDX-License-Identifier: Apache-2.0
"""Références aux résultats d'outils : ``{"$ref": "result:3"}`` (#12).

L'orchestrateur transmet un résultat précédent à un outil sans le recopier.
L'exécuteur remplace la référence par le résultat avant de valider les
arguments : le ``data`` du résultat s'il existe, sinon son texte.

``result:3`` désigne le 3ᵉ appel d'outil du run, dans l'ordre où le modèle
les a demandés. Seuls les résultats du run courant sont adressables, et
seulement ceux obtenus avant le tour en cours : un appel du même tour n'a pas
encore de résultat, et le rejeu reste déterministe.

Pour que le modèle connaisse ces numéros, chaque résultat qu'il reçoit
commence par sa référence (``[result:3]``) et une consigne s'ajoute au prompt
système. Ce marquage n'existe que dans la requête : le journal garde les
résultats tels quels.
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

REF_KEY: Final = "$ref"
REF_PREFIX: Final = "result:"
REFS_HINT: Final = (
    "Chaque résultat d'outil commence par sa référence, par exemple [result:3]. "
    "Pour transmettre un résultat à un outil sans le recopier, donne à l'argument "
    'la valeur {"$ref": "result:3"}.'
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
    """Appels d'outils d'un run, numérotés dans l'ordre des demandes du modèle."""

    def __init__(self, messages: Iterable[Message]) -> None:
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

    def resolve(
        self, arguments: dict[str, JsonValue]
    ) -> tuple[dict[str, JsonValue], tuple[str, ...]]:
        """Arguments aux références remplacées, et les références résolues.

        Lève ``RefError`` si une référence ne peut pas être résolue.
        """
        found: list[str] = []
        resolved = cast(dict[str, JsonValue], self._walk(arguments, found))
        return resolved, tuple(found)

    def _walk(self, value: JsonValue, found: list[str]) -> JsonValue:
        if isinstance(value, dict):
            ref = value.get(REF_KEY)
            if len(value) == 1 and isinstance(ref, str) and ref.startswith(REF_PREFIX):
                return self._value(ref, found)
            return {key: self._walk(item, found) for key, item in value.items()}
        if isinstance(value, list):
            return [self._walk(item, found) for item in value]
        return value

    def _value(self, ref: str, found: list[str]) -> JsonValue:
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
        value = output_value(record.output)
        if value == "":
            raise RefError(f"{ref} ({record.name}) est vide : rien à transmettre.")
        found.append(ref)
        return value

    def _known(self) -> str:
        usable = [f"{r.ref} ({r.name})" for r in self.records if r.usable]
        if not usable:
            return "Aucun résultat à référencer dans ce run."
        return f"Références disponibles : {', '.join(usable)}."


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
