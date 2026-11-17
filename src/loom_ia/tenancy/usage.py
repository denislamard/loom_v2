# SPDX-License-Identifier: Apache-2.0
"""Budget d'un client sur une période : le compteur et le contrôle (fonctions J4 et L3, conception §15).

Le contrôle a lieu **au lancement d'un run**, une fois, et pas avant chaque
appel de modèle comme le budget d'un run. Deux raisons. La sémantique : une
enveloppe quotidienne épuisée ne veut pas dire « réponds vite », elle veut
dire « reviens demain », et un run refusé n'écrit rien au journal. Le coût :
lire la dépense d'un client demande d'agréger toutes ses sessions, ce qu'on
ne fait pas quatre fois par run.

Le compteur est un cache, reconstructible : à la première question posée pour
un client et une période, ``TenantUsage`` le **réchauffe** en relisant les
``model.responded`` (et ``model.retried`` facturés) du journal depuis le début
de la fenêtre ; la vie courante l'entretient — un run enregistre ce qu'il a
coûté en se terminant. Comme ``record`` pose une valeur par run racine au lieu
de l'ajouter, les deux sources peuvent se chevaucher sans jamais compter deux
fois.

**Chaque contrôle relit la suite du journal** (essai N du 23/10). Le journal
est commun à tous les process — un autre ``loom serve``, un worker, ``loom
run`` — et le compteur ne l'est pas : réchauffé une seule fois par fenêtre,
celui d'un ``serve`` qui dure ne voyait plus que ses propres runs, et deux
``serve`` sur un plafond de 2 615 tokens en ont laissé passer 4 184. Avant de
répondre, ``check`` lit donc ce qui a été écrit depuis sa dernière lecture
(``_Window`` : les totaux par run tirés du journal, les événements déjà vus,
et une borne qui recule de ``OVERLAP`` pour rattraper un événement écrit en
retard) et repose le total de chaque run touché. Reste l'écart des runs
admis ensemble, avant qu'aucun n'ait consommé : le contrôle a lieu au
lancement.

La lecture est **atomique** : une seule à la fois par client et par période
(un verrou par couple, un client lent n'attend pas les autres), et ce qu'elle
apprend n'est retenu qu'**une fois la lecture réussie**. Les ``check()``
concurrents attendent donc un compteur rempli, pas un compteur vide ; une
lecture qui échoue se propage à l'appelant — le run est refusé, rien n'est
écrit — au lieu de laisser le plafond ouvert, et le contrôle suivant retente.

Le rapport (``consumption``), lui, ne passe pas par le compteur : il relit le
journal à chaque appel. C'est une question qu'un humain pose, pas une
vérification par run, et elle doit valoir même pour un client sans budget —
dont le compteur, justement, ne retient rien.

Config : ``budgets.tenant`` (``max_cost_per_day``, ``max_tokens_per_day``,
``max_cost_per_month``, ``max_tokens_per_month``), à la racine ou dans la
fiche d'un client.
"""

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Final

from loom_ia.core import TOLD
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
"""Type des appels aboutis, lus au réchauffage."""
MODEL_RETRIED: Final = "model.retried"
"""Type des tentatives ratées ; le ledger garde celles que le fournisseur a facturées (fonction J1)."""
PAGE: Final = 5_000
"""Événements lus par requête au réchauffage ; la pagination fait le reste."""
OVERLAP: Final = timedelta(minutes=2)
"""Recul de la borne de lecture.

Un événement daté avant la dernière lecture mais écrit après (un process plus
lent, une horloge en retard) est rattrapé ; les événements déjà vus sont
écartés par leur identifiant.
"""


@dataclass(frozen=True, slots=True, kw_only=True)
class Exceeded:
    """Une limite de client atteinte : ce qu'elle valait, ce qui a été dépensé.

    Attributes:
        period: La fenêtre.
        limit: ``max_cost`` ou ``max_tokens``.
        value: Le plafond.
        spent: La dépense.
    """

    period: Period
    limit: BudgetLimit
    value: float
    spent: float

    def __str__(self) -> str:
        return f"budget du client atteint pour {self.period} : {_amount(self.limit, self.spent)} (plafond {_amount(self.limit, self.value)})"


class BudgetExhausted(RuntimeError):
    """Le client a épuisé son budget de la période : le run n'est pas lancé.

    ``retry_after`` porte les secondes jusqu'à la remise à zéro de la fenêtre,
    de quoi répondre honnêtement à l'appelant (``Retry-After`` en HTTP).

    Args:
        tenant_id: Le client.
        reached: La limite atteinte.
        retry_after: Secondes jusqu'à la remise à zéro.

    Attributes:
        tenant_id: Le client.
        reached: La limite atteinte.
        retry_after: Secondes jusqu'à la remise à zéro.
    """

    def __init__(self, tenant_id: TenantId, reached: Exceeded, retry_after: float) -> None:
        super().__init__(f"Client {tenant_id!r} : {reached}")
        self.tenant_id = tenant_id
        self.reached = reached
        self.retry_after = retry_after


@dataclass(frozen=True, slots=True, kw_only=True)
class TenantConsumption:
    """Ce qu'un client a dépensé sur une fenêtre, et ce qu'elle lui permettait.

    Attributes:
        tenant_id: Le client.
        period: La fenêtre.
        spent: La dépense : coût, tokens, appels.
        limits: Plafonds posés pour cette fenêtre : limite et valeur.
        runs: Runs racines comptés.
    """

    tenant_id: TenantId
    period: Period
    spent: Spent
    limits: tuple[tuple[BudgetLimit, float], ...] = ()
    runs: int = 0

    @property
    def resets_at(self) -> datetime:
        """Instant de la remise à zéro, en UTC."""
        return self.period.end

    def left(self, limit: BudgetLimit) -> float | None:
        """Ce qui reste sur une limite ; ``None`` si elle n'est pas posée.

        Args:
            limit: ``max_cost`` ou ``max_tokens``.
        """
        for name, value in self.limits:
            if name == limit:
                return max(0.0, value - _used(limit, self.spent))
        return None


@dataclass(slots=True)
class _Window:
    """Ce que ce process a lu du journal pour un client et une période.

    Attributes:
        period: La fenêtre.
        since: Prochaine lecture à partir de cette date (le début de la
            fenêtre, puis la dernière date lue moins ``OVERLAP``).
        totals: Dépense par run racine, tirée du journal : la somme de ce qui
            a été lu.
        seen: Événements déjà comptés, et leur date : ceux d'avant ``since``
            sont oubliés.
    """

    period: Period
    since: datetime
    totals: dict[RunId, Spent] = field(default_factory=dict[RunId, Spent])
    seen: dict[str, datetime] = field(default_factory=dict[str, datetime])


class TenantUsage:
    """Compteur de consommation par client, et contrôle de son budget de période.

    Args:
        counter: Le compteur (port ``UsageCounter``) : en mémoire, ou
            partagé et durable.
        store: Le journal, relu pour réchauffer le compteur et pour les
            rapports.
        warm: Faux pour un compteur partagé et durable (jalon J5.3), qui n'a
            rien à réchauffer.
    """

    def __init__(self, counter: UsageCounter, store: EventStore, *, warm: bool = True) -> None:
        self._counter = counter
        self._store = store
        # Un compteur partagé et durable (jalon J5.3) n'a rien à réchauffer : sa
        # valeur est déjà celle de tout le monde.
        self._warms = warm
        self._windows: dict[tuple[TenantId, str], _Window] = {}
        # Un verrou par (client, période) : une lecture du journal à la fois.
        self._reading: dict[tuple[TenantId, str], asyncio.Lock] = {}

    async def check(self, tenant: Tenant, now: datetime | None = None) -> None:
        """Lève ``BudgetExhausted`` si le client n'a plus de quoi lancer un run.

        Si le journal ne peut pas être relu pour réchauffer le compteur, l'erreur
        de lecture se propage telle quelle : le run n'est pas lancé.

        Args:
            tenant: Le client, et ses plafonds par période.
            now: L'instant, avec un fuseau ; maintenant par défaut.

        Raises:
            BudgetExhausted: Une limite d'une fenêtre est atteinte.
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
                    logger.warning("Run refusé — client %s : %s", tenant.id, reached, extra={TOLD: True})
                    raise BudgetExhausted(tenant.id, reached, period.resets_in(now))

    async def record(self, tenant: Tenant, run_id: RunId, spent: Spent, now: datetime | None = None) -> None:
        """Enregistre ce qu'un run racine a coûté, dans les fenêtres du client.

        Rien n'est retenu pour un client sans budget : le compteur ne sert qu'à
        ``check``, et le rapport relit le journal.

        Args:
            tenant: Le client.
            run_id: Le run racine.
            spent: Ce qu'il a coûté, sous-runs compris.
            now: L'instant ; maintenant par défaut.
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
        """Dépense d'un client sur une fenêtre, relue dans le journal.

        Sert à ``loom report --period`` et ``Loom.consumption()``.

        Args:
            tenant_id: Le client.
            budget: Ses plafonds par période, pour ``limits`` ; ``None`` :
                aucun.
            kind: ``day`` ou ``month``.
            now: L'instant ; maintenant par défaut.

        Returns:
            La dépense, le nombre de runs et les plafonds de la fenêtre.
        """
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
        if self._warms:
            key = (tenant_id, period.key)
            # Les appels concurrents attendent le premier ; si sa lecture échoue
            # ou est annulée, rien n'en est retenu et le suivant la refait.
            async with self._reading.setdefault(key, asyncio.Lock()):
                await self._caught_up(tenant_id, period)
        return await self._counter.consumed(tenant_id, period.key)

    async def _caught_up(self, tenant_id: TenantId, period: Period) -> None:
        """Lit ce que le journal a reçu depuis la dernière lecture, et le porte au compteur."""
        key = (tenant_id, period.key)
        window = self._windows.get(key) or _Window(period=period, since=period.start)
        fresh = [e for e in await self._read(tenant_id, window.since) if e.event_id not in window.seen]
        # Rien n'est retenu avant d'avoir tout lu : une lecture qui échoue laisse
        # la fenêtre telle qu'elle était.
        touched: set[RunId] = set()
        for entry in ledger(fresh):
            window.totals[entry.root_run_id] = window.totals.get(entry.root_run_id, Spent()) + entry.spent
            touched.add(entry.root_run_id)
        for event in fresh:
            window.seen[event.event_id] = event.ts
        if window.seen:
            window.since = max(window.since, max(window.seen.values()) - OVERLAP)
            window.seen = {id_: ts for id_, ts in window.seen.items() if ts >= window.since}
        self._windows[key] = window
        self._forget_before(tenant_id, period)
        for run_id in touched:
            await self._counter.record(tenant_id, period.key, run_id, window.totals[run_id])

    def _forget_before(self, tenant_id: TenantId, period: Period) -> None:
        """Oublie les fenêtres de ce client finies avant celle-ci (même genre)."""
        for key, window in list(self._windows.items()):
            if key[0] == tenant_id and window.period.kind == period.kind and window.period.end <= period.start:
                del self._windows[key]
                self._reading.pop(key, None)

    async def _read(self, tenant_id: TenantId, since: datetime) -> list[Event]:
        """Appels aboutis et tentatives facturées du client depuis ``since``, toutes pages."""
        events: list[Event] = []
        after = None
        while True:
            page = await self._store.query(
                EventQuery(
                    tenant_id=tenant_id,
                    types=(MODEL_RESPONDED, MODEL_RETRIED),
                    since=since,
                    after=after,
                    limit=PAGE,
                )
            )
            events.extend(page)
            if len(page) < PAGE:
                return events
            after = page[-1].event_id

    async def _from_journal(self, tenant_id: TenantId, period: Period) -> dict[RunId, Spent]:
        """Dépense par run racine, lue dans le journal depuis le début de la fenêtre.

        Appels aboutis et tentatives ratées facturées (fonction J1) : un rapport de période
        ou un plafond de client qui ne lirait que les premiers sous-compterait
        (essai de la fonction J5, 21/10).

        Les totaux sont assemblés sur **toutes** les pages avant d'être rendus :
        un run dont les appels se répartissent sur deux pages ne doit pas être
        réduit à ceux de la dernière.
        """
        totals: dict[RunId, Spent] = {}
        _accumulate(totals, await self._read(tenant_id, period.start))
        return totals


def _accumulate(totals: dict[RunId, Spent], events: Sequence[Event]) -> None:
    for entry in ledger(events):
        totals[entry.root_run_id] = totals.get(entry.root_run_id, Spent()) + entry.spent


def _used(limit: BudgetLimit, spent: Spent) -> float:
    return spent.cost if limit == "max_cost" else float(spent.tokens)


def _amount(limit: BudgetLimit, value: float) -> str:
    if limit == "max_cost":
        return f"{value:.5f} $".replace(".", ",")
    return f"{int(value)} tokens"
