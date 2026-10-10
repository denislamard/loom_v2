# SPDX-License-Identifier: Apache-2.0
"""Budget d'un client sur une période : le compteur et le contrôle (J4, L3, §15).

Le contrôle a lieu **au lancement d'un run**, une fois, et pas avant chaque
appel de modèle comme le budget d'un run. Deux raisons. La sémantique : une
enveloppe quotidienne épuisée ne veut pas dire « réponds vite », elle veut
dire « reviens demain », et un run refusé n'écrit rien au journal. Le coût :
lire la dépense d'un client demande d'agréger toutes ses sessions, ce qu'on
ne fait pas quatre fois par run.

Le compteur est un cache, reconstructible : à la première question posée pour
un client et une période, ``TenantUsage`` le **réchauffe** en relisant les
``model.responded`` du journal depuis le début de la fenêtre, puis la vie
courante l'entretient — un run enregistre ce qu'il a coûté en se terminant.
Comme ``record`` pose une valeur par run racine au lieu de l'ajouter, les deux
sources peuvent se chevaucher sans jamais compter deux fois.

Le réchauffage est **atomique** : un seul à la fois par client et par période
(un verrou par couple, un client lent n'attend pas les autres), et le couple
n'est tenu pour chaud qu'**une fois la lecture réussie**. Les ``check()``
concurrents attendent donc un compteur rempli, pas un compteur vide ; une
lecture qui échoue se propage à l'appelant — le run est refusé, rien n'est
écrit — au lieu de laisser le plafond ouvert, et le contrôle suivant retente.

Le rapport (``consumption``), lui, ne passe pas par le compteur : il relit le
journal à chaque appel. C'est une question qu'un humain pose, pas une
vérification par run, et elle doit valoir même pour un client sans budget —
dont le compteur, justement, ne retient rien.
"""

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from loom_ia.core.events import Event
from loom_ia.core.events.query import EventQuery
from loom_ia.core.model import (
    BudgetLimit,
    BudgetPeriod,
    RunId,
    Spent,
    TenantBudget,
    TenantId,
)
from loom_ia.core.ports import EventStore, UsageCounter
from loom_ia.core.projections import ledger
from loom_ia.tenancy.period import Period
from loom_ia.tenancy.registry import Tenant

logger = logging.getLogger(__name__)

MODEL_RESPONDED: Final = "model.responded"
# Événements lus par requête au réchauffage ; la pagination fait le reste.
PAGE: Final = 5_000


@dataclass(frozen=True, slots=True, kw_only=True)
class Exceeded:
    """Une limite de client atteinte : ce qu'elle valait, ce qui a été dépensé."""

    period: Period
    limit: BudgetLimit
    value: float
    spent: float

    def __str__(self) -> str:
        return (
            f"budget du client atteint pour {self.period} : "
            f"{_amount(self.limit, self.spent)} (plafond {_amount(self.limit, self.value)})"
        )


class BudgetExhausted(RuntimeError):
    """Le client a épuisé son budget de la période : le run n'est pas lancé.

    ``retry_after`` porte les secondes jusqu'à la remise à zéro de la fenêtre,
    de quoi répondre honnêtement à l'appelant (``Retry-After`` en HTTP).
    """

    def __init__(self, tenant_id: TenantId, reached: Exceeded, retry_after: float) -> None:
        super().__init__(f"Client {tenant_id!r} : {reached}")
        self.tenant_id = tenant_id
        self.reached = reached
        self.retry_after = retry_after


@dataclass(frozen=True, slots=True, kw_only=True)
class TenantConsumption:
    """Ce qu'un client a dépensé sur une fenêtre, et ce qu'elle lui permettait."""

    tenant_id: TenantId
    period: Period
    spent: Spent
    limits: tuple[tuple[BudgetLimit, float], ...] = ()
    runs: int = 0

    @property
    def resets_at(self) -> datetime:
        return self.period.end

    def left(self, limit: BudgetLimit) -> float | None:
        """Ce qui reste sur une limite ; ``None`` si elle n'est pas posée."""
        for name, value in self.limits:
            if name == limit:
                return max(0.0, value - _used(limit, self.spent))
        return None


class TenantUsage:
    """Compteur de consommation par client, et contrôle de son budget de période."""

    def __init__(self, counter: UsageCounter, store: EventStore, *, warm: bool = True) -> None:
        self._counter = counter
        self._store = store
        # Un compteur partagé et durable (J5.3) n'a rien à réchauffer : sa
        # valeur est déjà celle de tout le monde.
        self._warms = warm
        self._warmed: set[tuple[TenantId, str]] = set()
        # Un verrou par (client, période) tant que son réchauffage n'a pas
        # abouti : il est retiré dès que le couple est chaud.
        self._warming: dict[tuple[TenantId, str], asyncio.Lock] = {}

    async def check(self, tenant: Tenant, now: datetime | None = None) -> None:
        """Lève ``BudgetExhausted`` si le client n'a plus de quoi lancer un run.

        Si le journal ne peut pas être relu pour réchauffer le compteur, l'erreur
        de lecture se propage telle quelle : le run n'est pas lancé.
        """
        budget = tenant.budget
        if not budget.limited:
            return
        for kind in budget.periods:
            period = Period.of(kind, now)
            spent = await self._consumed(tenant.id, period)
            for limit, value in budget.limits(kind):
                used = _used(limit, spent)
                if used >= value:
                    reached = Exceeded(period=period, limit=limit, value=value, spent=used)
                    logger.warning("Run refusé — client %s : %s", tenant.id, reached)
                    raise BudgetExhausted(tenant.id, reached, period.resets_in(now))

    async def record(
        self, tenant: Tenant, run_id: RunId, spent: Spent, now: datetime | None = None
    ) -> None:
        """Enregistre ce qu'un run racine a coûté, dans les fenêtres du client.

        Rien n'est retenu pour un client sans budget : le compteur ne sert qu'à
        ``check``, et le rapport relit le journal.
        """
        if not tenant.budget.limited:
            return
        for kind in tenant.budget.periods:
            await self._counter.record(tenant.id, Period.of(kind, now).key, run_id, spent)

    async def consumption(
        self,
        tenant_id: TenantId,
        budget: TenantBudget | None = None,
        kind: BudgetPeriod = "day",
        now: datetime | None = None,
    ) -> TenantConsumption:
        """Dépense d'un client sur une fenêtre, relue dans le journal."""
        period = Period.of(kind, now)
        totals = await self._from_journal(tenant_id, period)
        spent = Spent()
        for run in totals.values():
            spent += run
        return TenantConsumption(
            tenant_id=tenant_id,
            period=period,
            spent=spent,
            limits=() if budget is None else budget.limits(kind),
            runs=len(totals),
        )

    async def _consumed(self, tenant_id: TenantId, period: Period) -> Spent:
        warm = (tenant_id, period.key)
        if self._warms and warm not in self._warmed:
            # Les appels concurrents attendent le premier ; si sa lecture échoue
            # ou est annulée, le couple reste froid et le suivant la refait.
            async with self._warming.setdefault(warm, asyncio.Lock()):
                if warm not in self._warmed:
                    for run_id, spent in (await self._from_journal(tenant_id, period)).items():
                        await self._counter.record(tenant_id, period.key, run_id, spent)
                    self._warmed.add(warm)
            self._warming.pop(warm, None)
        return await self._counter.consumed(tenant_id, period.key)

    async def _from_journal(self, tenant_id: TenantId, period: Period) -> dict[RunId, Spent]:
        """Dépense par run racine, lue dans le journal depuis le début de la fenêtre.

        Les totaux sont assemblés sur **toutes** les pages avant d'être rendus :
        un run dont les appels se répartissent sur deux pages ne doit pas être
        réduit à ceux de la dernière.
        """
        totals: dict[RunId, Spent] = {}
        after = None
        while True:
            page = await self._store.query(
                EventQuery(
                    tenant_id=tenant_id,
                    types=(MODEL_RESPONDED,),
                    since=period.start,
                    after=after,
                    limit=PAGE,
                )
            )
            _accumulate(totals, page)
            if len(page) < PAGE:
                return totals
            after = page[-1].event_id


def _accumulate(totals: dict[RunId, Spent], events: Sequence[Event]) -> None:
    for entry in ledger(events):
        totals[entry.root_run_id] = totals.get(entry.root_run_id, Spent()) + entry.spent


def _used(limit: BudgetLimit, spent: Spent) -> float:
    return spent.cost if limit == "max_cost" else float(spent.tokens)


def _amount(limit: BudgetLimit, value: float) -> str:
    if limit == "max_cost":
        return f"{value:.5f} $".replace(".", ",")
    return f"{int(value)} tokens"
