# SPDX-License-Identifier: Apache-2.0
"""Contrôle de fidélité d'un résumé de session (#23).

Un résumé n'a d'intérêt que s'il garde ce qui sert à la suite : numéros de
devis, montants, quantités, adresses. Le contrôle est déterministe — aucun
modèle n'y participe : on relève les repères du segment d'origine et on
vérifie qu'ils se retrouvent dans le résumé.

Repères relevés :

- les **références** du type ``D-2026-042`` (lettres, tiret, chiffres) ;
- les **adresses e-mail** ;
- les **nombres** d'au moins trois chiffres, séparateurs de milliers retirés.

Le seuil de trois chiffres écarte le bruit — « trois phrases », le ``09``
d'une date en ISO que le résumé écrira « 2 septembre » — tout en gardant les
montants, les années et les quantités. Un manque fait demander une nouvelle
tentative au modèle ; à la suivante, le résumé est gardé tel quel et le
marqueur de compaction porte ``fidelity: warning``.
"""

import re
from typing import Final

from loom_ia.core.model import (
    CONTINUE,
    Decision,
    DecisionKind,
    GuardCheck,
    HookPoint,
    Message,
    OnOutput,
    PolicyContext,
    PolicySubject,
    Retry,
)

FIDELITY_POLICY: Final = "loom.fidelity"
FIDELITY_GUARD: Final = "fidelity"
# En deçà, un nombre ne dit rien qu'un résumé doive garder.
MIN_DIGITS: Final = 3
# Repères nommés dans le diagnostic, pour ne pas lui faire recopier le segment.
SHOWN: Final = 12

_REFERENCE: Final = re.compile(r"\b[A-Za-z]{1,6}[-_]\d{2,}(?:[-_]\d+)*\b")
_EMAIL: Final = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
# Chiffres, avec leurs séparateurs de milliers (espace fine, insécable) et
# leur partie décimale éventuelle.
_NUMBER: Final = re.compile("\\d[\\d\\u00a0\\u202f ]*(?:[.,]\\d+)?")
_GROUPING: Final = str.maketrans("", "", "\u00a0\u202f ")


def markers(text: str) -> set[str]:
    """Repères d'un texte : références, adresses, nombres assez longs."""
    found = {match.group().upper() for match in _REFERENCE.finditer(text)}
    found |= {match.group().lower() for match in _EMAIL.finditer(text)}
    for match in _NUMBER.finditer(text):
        digits = _canonical(match.group())
        if len(digits) >= MIN_DIGITS:
            found.add(digits)
    return found


def missing(source: str, summary: str) -> list[str]:
    """Repères du segment absents du résumé, dans l'ordre du texte."""
    absent = markers(source) - markers(summary)
    seen: list[str] = []
    for marker in _ordered(source):
        if marker in absent and marker not in seen:
            seen.append(marker)
    return seen


def _ordered(text: str) -> list[str]:
    """Repères dans l'ordre où ils apparaissent."""
    spotted: list[tuple[int, str]] = []
    spotted += [(m.start(), m.group().upper()) for m in _REFERENCE.finditer(text)]
    spotted += [(m.start(), m.group().lower()) for m in _EMAIL.finditer(text)]
    for match in _NUMBER.finditer(text):
        digits = _canonical(match.group())
        if len(digits) >= MIN_DIGITS:
            spotted.append((match.start(), digits))
    return [marker for _, marker in sorted(spotted)]


def _canonical(number: str) -> str:
    """Partie entière d'un nombre, séparateurs de milliers retirés."""
    whole = re.split(r"[.,]", number.strip(), maxsplit=1)[0]
    return whole.translate(_GROUPING)


class FidelityGuard:
    """Politique ``loom.fidelity`` : les repères du segment sont-ils dans le résumé ?"""

    def __init__(self, max_attempts: int = 1) -> None:
        # Réparations demandées au modèle avant de garder le résumé tel quel.
        self.max_attempts = max_attempts

    @property
    def name(self) -> str:
        return FIDELITY_POLICY

    @property
    def points(self) -> frozenset[HookPoint]:
        return frozenset({"on_output"})

    @property
    def decisions(self) -> frozenset[DecisionKind]:
        return frozenset({"retry"})

    def __repr__(self) -> str:
        return f"FidelityGuard({self.max_attempts} tentative(s))"

    async def decide(self, subject: PolicySubject, context: PolicyContext) -> Decision:
        if not isinstance(subject, OnOutput):
            return CONTINUE
        segment = _segment(subject.state.messages)
        if not segment:
            context.record(
                GuardCheck(
                    guard=FIDELITY_GUARD,
                    target="output",
                    outcome="skipped",
                    reason="aucun segment à comparer",
                )
            )
            return CONTINUE
        absent = missing(segment, subject.output.text)
        if not absent:
            context.record(GuardCheck(guard=FIDELITY_GUARD, target="output", outcome="passed"))
            return CONTINUE
        shown = ", ".join(absent[:SHOWN])
        rest = f" et {len(absent) - SHOWN} autre(s)" if len(absent) > SHOWN else ""
        reason = f"repères absents du résumé : {shown}{rest}"
        if context.attempt >= self.max_attempts:
            # Le résumé est gardé : un résumé imparfait vaut mieux que pas de résumé.
            context.record(
                GuardCheck(
                    guard=FIDELITY_GUARD,
                    target="output",
                    outcome="failed",
                    reason=reason,
                    resolution="unverified",
                )
            )
            return CONTINUE
        context.record(
            GuardCheck(
                guard=FIDELITY_GUARD,
                target="output",
                outcome="failed",
                reason=reason,
                resolution="retry",
            )
        )
        return Retry(
            f"le résumé perd des repères du segment : {shown}{rest}. Reprends-le en les "
            "conservant tels quels.",
            tools=False,
        )


def _segment(messages: tuple[Message, ...]) -> str:
    """Segment à résumer : la demande faite à l'agent de compaction."""
    return next((m.text for m in messages if m.role == "user"), "")
