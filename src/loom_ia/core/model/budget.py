# SPDX-License-Identifier: Apache-2.0
"""Budgets d'un run et d'une session (J4, #1, §15).

Un budget borne la consommation : en dollars (``max_cost``), en tokens
(``max_tokens``, utile pour un modèle sans tarif) et, pour un run, en appels
de modèle (``max_calls`` : orchestrateur, rôles et juges du run ; un
sous-agent a son propre budget). Il est vérifié avant chaque appel de
l'orchestrateur (politique fournie ``loom.budget``) : une limite atteinte
écrit un ``budget.exceeded`` ; avec ``on_exceed: stop``, le run passe en
``FINALIZING`` et donne une réponse forcée sans outils, et le dépassement est
borné à cette dernière génération.

Le budget de session compte la consommation des runs précédents de la
session, plus celle du run en cours. Budgets par client et par période : J5.

Sous-agent (C5) : ``budget_share`` lui donne une part de ce qui reste au run
parent au moment de l'appel ; cette part est écrite dans le ``run.started``
de l'enfant (``RunBudget``), et s'ajoute au budget propre de son agent (la
limite la plus basse l'emporte).
"""

import math
from dataclasses import dataclass, field
from typing import Final, Literal, Self

from pydantic import NonNegativeFloat, NonNegativeInt, model_validator

from loom_ia.core.model.base import DomainModel, reject_later
from loom_ia.core.model.usage import Usage

type BudgetScope = Literal["run", "session"]
type BudgetLimit = Literal["max_cost", "max_tokens", "max_calls"]
type OnExceed = Literal["warn", "stop"]

# Clés prévues pour plus tard.
LATER_BUDGETS: Final[dict[str, str]] = {"tenant": "J5 (budgets par client et par période)"}


class RunBudget(DomainModel):
    """Limites d'un run ; ``None`` : pas de limite."""

    max_cost: NonNegativeFloat | None = None
    max_tokens: NonNegativeInt | None = None
    max_calls: NonNegativeInt | None = None

    @property
    def limited(self) -> bool:
        return any(v is not None for v in (self.max_cost, self.max_tokens, self.max_calls))

    def tightest(self, other: RunBudget | None) -> RunBudget:
        """Limites les plus basses des deux budgets."""
        if other is None:
            return self
        return RunBudget(
            max_cost=_lowest(self.max_cost, other.max_cost),
            max_tokens=_lowest(self.max_tokens, other.max_tokens),
            max_calls=_lowest(self.max_calls, other.max_calls),
        )

    def share(self, part: float, spent: Spent) -> RunBudget | None:
        """Part de ce qui reste, pour un sous-agent ; None si une limite est déjà atteinte."""
        values: dict[str, float | int | None] = {}
        for limit, used in (
            ("max_cost", spent.cost),
            ("max_tokens", spent.tokens),
            ("max_calls", spent.calls),
        ):
            value: float | int | None = getattr(self, limit)
            if value is None:
                values[limit] = None
                continue
            left = value - used
            if left <= 0:
                return None
            # Un entier arrondi à zéro ne permettrait aucun appel : au moins un.
            values[limit] = part * left if limit == "max_cost" else max(1, math.floor(part * left))
        return RunBudget.model_validate(values)


class SessionBudget(DomainModel):
    """Limites d'une session : runs précédents et run en cours."""

    max_cost: NonNegativeFloat | None = None
    max_tokens: NonNegativeInt | None = None

    @property
    def limited(self) -> bool:
        return self.max_cost is not None or self.max_tokens is not None


class Budgets(DomainModel):
    """Budgets d'un agent : défauts de ``budgets`` (racine), surchargés par son ``budget``."""

    run: RunBudget = RunBudget()
    session: SessionBudget = SessionBudget()
    on_exceed: OnExceed = "stop"

    @model_validator(mode="before")
    @classmethod
    def _later(cls, data: object) -> object:
        reject_later(data, LATER_BUDGETS)
        return data

    @property
    def limited(self) -> bool:
        return self.run.limited or self.session.limited

    @property
    def in_dollars(self) -> bool:
        return self.run.max_cost is not None or self.session.max_cost is not None

    def merged(self, override: Budgets | None) -> Self:
        """Ce budget, surchargé clé par clé par les champs donnés dans ``override``."""
        if override is None:
            return self
        given = override.model_fields_set
        update: dict[str, object] = {}
        for part in ("run", "session"):
            if part in given:
                mine: RunBudget | SessionBudget = getattr(self, part)
                theirs: RunBudget | SessionBudget = getattr(override, part)
                update[part] = mine.model_copy(
                    update={name: getattr(theirs, name) for name in theirs.model_fields_set}
                )
        if "on_exceed" in given:
            update["on_exceed"] = override.on_exceed
        return self.model_copy(update=update)


@dataclass(frozen=True, slots=True)
class Spent:
    """Consommation : usage, coût et nombre d'appels de modèle."""

    usage: Usage = field(default_factory=Usage)
    cost: float = 0.0
    calls: int = 0

    @property
    def tokens(self) -> int:
        return self.usage.total_tokens

    def __add__(self, other: Spent) -> Spent:
        return Spent(self.usage + other.usage, self.cost + other.cost, self.calls + other.calls)


def _lowest[T: (float, int)](a: T | None, b: T | None) -> T | None:
    if a is None:
        return b
    if b is None:
        return a
    return min(a, b)
