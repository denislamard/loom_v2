# SPDX-License-Identifier: Apache-2.0
"""Fenêtres calendaires d'un budget de client (J5.1b, §15).

Le jour et le mois, **en UTC**. Calendaire et non glissant : un compteur par
fenêtre suffit alors, il se reconstruit depuis le journal en lisant les dates
des événements, et une remise à zéro est une date qu'on peut annoncer à
l'appelant (``Retry-After``). Une fenêtre glissante serait plus juste au sens
strict, mais elle interdirait le compteur agrégé — il faudrait garder chaque
appel horodaté et le sommer à chaque contrôle.

UTC plutôt que le fuseau du client : un fuseau par client demanderait une clé
de plus dans sa fiche et une conversion à chaque contrôle, pour un gain qui se
résume à l'heure où la journée bascule. C'est un choix assumé, pas un oubli —
un artisan français voit sa journée changer à 2 h du matin l'été.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Self

from loom_ia.core.model import BudgetPeriod

# Fenêtres, de la plus courte à la plus longue.
PERIODS: tuple[BudgetPeriod, ...] = ("day", "month")


@dataclass(frozen=True, slots=True)
class Period:
    """Une fenêtre, par son genre et son début."""

    kind: BudgetPeriod
    start: datetime

    @classmethod
    def of(cls, kind: BudgetPeriod, now: datetime | None = None) -> Self:
        """La fenêtre qui contient ``now`` (l'instant présent par défaut)."""
        moment = (now or datetime.now(UTC)).astimezone(UTC)
        midnight = moment.replace(hour=0, minute=0, second=0, microsecond=0)
        return cls(kind=kind, start=midnight if kind == "day" else midnight.replace(day=1))

    @property
    def key(self) -> str:
        """Clé de rangement, triable dans le temps : ``day:2026-09-23``, ``month:2026-09``."""
        stamp = "%Y-%m-%d" if self.kind == "day" else "%Y-%m"
        return f"{self.kind}:{self.start.strftime(stamp)}"

    @property
    def end(self) -> datetime:
        """Instant de la remise à zéro : le début de la fenêtre suivante."""
        if self.kind == "day":
            return self.start + timedelta(days=1)
        if self.start.month == 12:
            return self.start.replace(year=self.start.year + 1, month=1)
        return self.start.replace(month=self.start.month + 1)

    def resets_in(self, now: datetime | None = None) -> float:
        """Secondes avant la remise à zéro ; jamais négatif."""
        moment = (now or datetime.now(UTC)).astimezone(UTC)
        return max(0.0, (self.end - moment).total_seconds())

    def __str__(self) -> str:
        return "la journée" if self.kind == "day" else "le mois"
