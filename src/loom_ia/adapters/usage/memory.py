# SPDX-License-Identifier: Apache-2.0
"""Compteur de consommation en mémoire (J5.1b, port ``UsageCounter``).

Une valeur par run racine, et non une somme qui s'incrémente : c'est ce qui
rend ``record`` idempotent et permet au réchauffage depuis le journal de
chevaucher la vie courante sans compter deux fois (voir le port).

Ce que ça coûte : une entrée par run de la période, en mémoire, dans ce
process. Le nombre de périodes retenues par client est borné — les clés étant
triables dans le temps, ce sont les plus anciennes qui partent. Un compteur
partagé entre workers et durable attend J5.3 (Postgres, Redis).
"""

from typing import Final

from loom_ia.core.model import RunId, Spent, TenantId

# Périodes gardées par client : le jour et le mois courants, plus de quoi
# traverser un changement de fenêtre sans perdre la précédente.
MAX_PERIODS: Final = 8


class InMemoryUsageCounter:
    """Consommation par client et par période, dans ce process."""

    def __init__(self) -> None:
        self._runs: dict[TenantId, dict[str, dict[RunId, Spent]]] = {}

    async def consumed(self, tenant_id: TenantId, period: str) -> Spent:
        total = Spent()
        for spent in self._runs.get(tenant_id, {}).get(period, {}).values():
            total += spent
        return total

    async def record(self, tenant_id: TenantId, period: str, run_id: RunId, spent: Spent) -> None:
        periods = self._runs.setdefault(tenant_id, {})
        periods.setdefault(period, {})[run_id] = spent
        while len(periods) > MAX_PERIODS:
            del periods[min(periods)]

    async def aclose(self) -> None:
        self._runs.clear()

    def __repr__(self) -> str:
        runs = sum(len(r) for p in self._runs.values() for r in p.values())
        return f"InMemoryUsageCounter({len(self._runs)} client(s), {runs} run(s))"
