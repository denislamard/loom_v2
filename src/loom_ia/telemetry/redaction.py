# SPDX-License-Identifier: Apache-2.0
"""Masquage par motifs du contenu exporté (K3, #30, §14.2).

Deux masquages coexistent, et ne se confondent pas :

- celui des **champs** (``core.events.redaction``, 5.2a) retire tout ce
  qu'une charge déclare comme contenu : c'est ce qui part quand la capture
  est ``metadata``, et ce qu'une clé sans ``read_content`` ne lit pas ;
- celui-ci, par **motifs**, remplace dans le contenu qui sort ce qui ressemble
  à une donnée personnelle (e-mail, téléphone, IBAN, ou un motif à soi) par
  son nom entre crochets. Il ne sert qu'aux exports en ``content``.

Un motif est une heuristique : il masque ce qui lui ressemble, il ne garantit
pas que rien d'autre ne passe. C'est la capture ``metadata`` qui garantit
qu'aucun contenu ne sort.
"""

import re
from collections.abc import Iterable, Mapping
from typing import Final, cast

from pydantic import JsonValue

# Motifs fournis, dans l'ordre où ils s'appliquent par défaut.
BUILTIN_PATTERNS: Final = ("email", "phone", "iban")

_BUILTIN: Final[Mapping[str, str]] = {
    "email": r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+",
    # Numéros français (0X ou +33 X, puis quatre paires) et internationaux
    # (+indicatif puis groupes) : un montant ou une référence de devis n'en a
    # ni le préfixe ni la forme.
    "phone": (
        r"(?<![\w+])(?:(?:\+33[\s.-]?|0)[1-9](?:[\s.-]?\d{2}){4}"
        r"|\+(?!33)\d{1,3}(?:[\s.-]?\d{2,4}){2,5})(?!\d)"
    ),
    # Deux lettres, deux chiffres de contrôle, puis 11 à 30 caractères, en
    # groupes de quatre séparés ou non par une espace.
    "iban": r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]{4}){2,7}(?:[ ]?[A-Z0-9]{1,3})?\b",
}


class Redactor:
    """Remplace ce que ses motifs reconnaissent par ``[nom]``."""

    def __init__(self, patterns: Iterable[tuple[str, str]]) -> None:
        self._patterns = tuple((name, re.compile(regex)) for name, regex in patterns)

    @classmethod
    def of(cls, declared: Iterable[str | tuple[str, str]]) -> Redactor:
        """Motifs déclarés : un nom de motif fourni, ou ``(nom, expression)``."""
        resolved: list[tuple[str, str]] = []
        for item in declared:
            if isinstance(item, str):
                resolved.append((item, _BUILTIN[item]))
            else:
                resolved.append(item)
        return cls(resolved)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(name for name, _ in self._patterns)

    def text(self, value: str) -> str:
        for name, pattern in self._patterns:
            value = pattern.sub(f"[{name}]", value)
        return value

    def json(self, value: JsonValue) -> JsonValue:
        """Masque chaque chaîne d'une valeur JSON, clés comprises."""
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, list):
            return [self.json(item) for item in cast(list[JsonValue], value)]
        if isinstance(value, dict):
            table = cast(dict[str, JsonValue], value)
            return {self.text(key): self.json(item) for key, item in table.items()}
        return value
