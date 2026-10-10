# SPDX-License-Identifier: Apache-2.0
"""Contrôle de fidélité d'un résumé de session (#23).

Un résumé n'a d'intérêt que s'il garde ce qui sert à la suite : numéros de
devis, montants, quantités, adresses. Le contrôle est déterministe — aucun
modèle n'y participe : on relève les repères du segment d'origine et on
vérifie qu'ils se retrouvent dans le résumé.

Repères relevés :

- les **références** du type ``D-2026-042`` (lettres, tiret, chiffres) ;
- les **adresses e-mail** ;
- les **nombres** d'au moins trois chiffres, séparateurs de milliers retirés et
  décimales comprises (``1 234,56`` n'est pas ``1 234,99``).

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
# Le lookbehind ne laisse commencer une adresse qu'au début d'un mot : sans lui,
# chaque lettre d'un long mot sans « @ » relançait la lecture jusqu'à sa fin.
_EMAIL: Final = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w-]+\.[\w.-]+")
# Des chiffres que séparent un seul caractère : espace (fine, insécable ou non),
# point ou virgule. Ce qu'ils forment — milliers, décimales, plusieurs nombres —
# se tranche dans ``_readings``.
_SEPARATOR: Final = re.compile("[\\u00a0\\u202f .,]")
_NUMBER: Final = re.compile("\\d+(?:[\\u00a0\\u202f .,]\\d+)*")

# Une lecture : les nombres que dit un même texte ; plusieurs si le texte est ambigu.
Reading = tuple[str, ...]


def markers(text: str) -> set[str]:
    """Repères d'un texte : références, adresses, nombres assez longs.

    Un texte ambigu (« 120 150 ») les donne toutes les deux lectures : c'est ce
    qu'un résumé peut avoir écrit de plus large.
    """
    found: set[str] = set()
    for _, readings in _requirements(text):
        for reading in readings:
            found.update(reading)
    return found


def missing(source: str, summary: str) -> list[str]:
    """Repères du segment absents du résumé, dans l'ordre du texte.

    Un repère ambigu est présent si le résumé a l'une de ses lectures en entier.
    """
    have = markers(summary)
    seen: list[str] = []
    for _, readings in _requirements(source):
        if any(have.issuperset(reading) for reading in readings):
            continue
        for marker in readings[0]:
            if marker not in have and marker not in seen:
                seen.append(marker)
    return seen


def _requirements(text: str) -> list[tuple[int, list[Reading]]]:
    """Repères du texte, dans l'ordre où ils apparaissent, avec leurs lectures possibles."""
    spotted: list[tuple[int, list[Reading]]] = []
    spotted += [(m.start(), [(m.group().upper(),)]) for m in _REFERENCE.finditer(text)]
    # Un point final de phrase n'est pas dans l'adresse.
    spotted += [(m.start(), [(m.group().rstrip(".-").lower(),)]) for m in _EMAIL.finditer(text)]
    for match in _NUMBER.finditer(text):
        readings = [kept for kept in map(_long_enough, _readings(match.group())) if kept]
        if readings:
            spotted.append((match.start(), readings))
    return sorted(spotted, key=lambda item: item[0])


def _long_enough(reading: Reading) -> Reading:
    """Les nombres d'une lecture qui ont assez de chiffres pour compter."""
    return tuple(n for n in reading if sum(c.isdigit() for c in n) >= MIN_DIGITS)


def _readings(token: str) -> list[Reading]:
    """Façons de lire un nombre brut, la plus probable d'abord.

    - ``1.234.567``, ``1,234,567``, ``1 234 567`` : des groupes de milliers ;
    - ``1 234,56``, ``1.234,56``, ``1,234.56`` : une partie décimale, que ses
      zéros finaux n'allongent pas (``1 840,00`` vaut ``1 840``) ;
    - ``10.10.2026``, ``3 15`` : plusieurs nombres.

    Une espace simple peut grouper des milliers ou séparer deux nombres
    (``120 150``) : les deux lectures sont rendues. Un point ou une virgule
    suivi d'exactement trois chiffres est lu comme un groupe de milliers.
    """
    parts = _SEPARATOR.split(token)
    separators = _SEPARATOR.findall(token)
    if not separators:
        return [(token,)]
    last, tail = separators[-1], parts[-1]
    decimal = last in ",." and (
        len(tail) != 3
        or (len(parts) == 2 and parts[0] == "0")
        or any(s in ",." and s != last for s in separators[:-1])
    )
    if decimal and _grouped(parts[:-1], separators[:-1]):
        fraction = tail.rstrip("0")
        suffix = f",{fraction}" if fraction else ""
        return [("".join(parts[:-1]) + suffix,)]
    if not _grouped(parts, separators):
        # Des dates (``10.10.2026``), des listes (``3,5,8``) : des nombres à part.
        return [tuple(parts)]
    joined: Reading = ("".join(parts),)
    # Seule l'espace simple est ambiguë : un point ou une virgule répété groupe sûrement.
    return [joined, tuple(parts)] if set(separators) == {" "} else [joined]


def _grouped(parts: list[str], separators: list[str]) -> bool:
    """Ces chiffres sont-ils un seul nombre, en groupes de milliers réguliers ?"""
    if len(parts) == 1:
        return True
    return (
        len(set(separators)) == 1
        and 1 <= len(parts[0]) <= 3
        and all(len(p) == 3 for p in parts[1:])
    )


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
