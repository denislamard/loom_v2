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
session, plus celle du run en cours.

Le budget d'un **client** (``tenant``, J5.1b) est d'une autre nature : il
borne ce qu'il a le droit de lancer sur une journée ou un mois, et se lit
donc une fois, avant d'ouvrir le run, et non avant chaque appel.

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
# Fenêtres calendaires d'un budget de client (J5.1b) ; UTC.
type BudgetPeriod = Literal["day", "month"]

# Clés prévues pour plus tard.
LATER_BUDGETS: Final[dict[str, str]] = {}


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


class TenantBudget(DomainModel):
    """Plafonds d'un client sur une fenêtre calendaire, en UTC (J4, L3).

    Ce n'est pas un budget de run : il ne borne pas ce qu'un run dépense, il
    borne ce qu'un client a le droit de **lancer**. Il est donc lu une fois,
    avant d'ouvrir le run — la journée d'un artisan est finie ou elle ne l'est
    pas, et l'avertir en cours de route par une réponse forcée lui ferait
    payer une génération pour l'apprendre.

    Pas de ``max_calls`` : compter les appels de modèle d'une journée n'apprend
    rien de plus que ses tokens, et ce que le schéma annonce (§17.7) est le
    coût et les tokens.
    """

    max_cost_per_day: NonNegativeFloat | None = None
    max_tokens_per_day: NonNegativeInt | None = None
    max_cost_per_month: NonNegativeFloat | None = None
    max_tokens_per_month: NonNegativeInt | None = None

    @property
    def limited(self) -> bool:
        return any(getattr(self, name) is not None for name in type(self).model_fields)

    @property
    def periods(self) -> tuple[BudgetPeriod, ...]:
        """Fenêtres qui portent au moins une limite ; vide si le client n'en a aucune."""
        return tuple(period for period in ("day", "month") if self.limits(period))

    def limits(self, period: BudgetPeriod) -> tuple[tuple[BudgetLimit, float], ...]:
        """Limites de cette fenêtre : ``(max_cost, 5.0)``, ``(max_tokens, 200000)``…"""
        found: list[tuple[BudgetLimit, float]] = []
        for limit in ("max_cost", "max_tokens"):
            value = getattr(self, f"{limit}_per_{period}")
            if value is not None:
                found.append((limit, float(value)))
        return tuple(found)


class Budgets(DomainModel):
    """Budgets d'un agent : défauts de ``budgets`` (racine), surchargés par son ``budget``."""

    run: RunBudget = RunBudget()
    session: SessionBudget = SessionBudget()
    # Plafonds du client sur une période (J5.1b) ; ils ne dépendent pas de l'agent,
    # et c'est la fiche du client qui les porte le plus souvent.
    tenant: TenantBudget = TenantBudget()
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
        for part in ("run", "session", "tenant"):
            if part in given:
                mine: RunBudget | SessionBudget | TenantBudget = getattr(self, part)
                theirs: RunBudget | SessionBudget | TenantBudget = getattr(override, part)
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
