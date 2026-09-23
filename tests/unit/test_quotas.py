# SPDX-License-Identifier: Apache-2.0
"""Budget d'un client par période, quotas et débit (L3, J5.1b, #39).

Deux limites de natures différentes. Le **budget** dit combien un client peut
dépenser sur une journée ou un mois ; il est lu une fois, au lancement du run,
dans un compteur réchauffé depuis le journal. Le **quota** dit à quelle vitesse
il peut demander ; il est lu dans une fenêtre glissante de soixante secondes.

Ce que ces essais protègent avant tout, c'est l'invariant du compteur :
``record`` **pose** ce qu'un run a coûté au lieu de l'ajouter. Sans ça, un run
commencé avant le réchauffage et fini après serait compté deux fois — et un
artisan se verrait refuser sa journée au milieu de l'après-midi.
"""

import json
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest
from conftest import ANSWER, MODEL, QUESTION, TREE_QUESTION, ConfigFactory

from loom_ia.access import BudgetExhausted, Loom, QuotaExceeded
from loom_ia.adapters.stores import InMemoryEventStore
from loom_ia.adapters.usage import InMemoryUsageCounter
from loom_ia.adapters.usage.memory import MAX_PERIODS
from loom_ia.config import load_config
from loom_ia.core.events import Event
from loom_ia.core.events.query import EventQuery
from loom_ia.core.model import WINDOW, Message, RunId, Spent, TenantId, Usage, new_run_id
from loom_ia.core.ports import EventStore
from loom_ia.tenancy import PERIODS, Period, Quota, RateWindow, Tenants, TenantUsage
from loom_ia.tenancy import usage as usage_module
from loom_ia.testing import RunJournal

DUPONT = TenantId("dupont-plomberie")
MARTIN = TenantId("martin-chauffage")
JOURNAL: dict[str, Any] = {"events": {"backend": "jsonl", "path": "data"}}
PRICED: dict[str, Any] = {**MODEL, "pricing": {"input": 1.0, "output": 5.0}}
# Un plafond qu'aucun run n'atteint avant d'avoir tourné : le contrôle a lieu
# au lancement, donc le premier run passe toujours et le second est refusé.
AFTER_ONE: dict[str, Any] = {"tenant": {"max_tokens_per_day": 1}}


def clients(**overrides: Any) -> list[dict[str, Any]]:
    """Deux artisans ; les surcharges sont pour le premier."""
    return [{"id": DUPONT, **overrides}, {"id": MARTIN}]


def registry(demo: ConfigFactory, **overrides: Any) -> Tenants:
    """Les clients d'une config, résolus comme le fait une instance."""
    return Tenants(load_config(demo(tenants=clients(**overrides))))


def spending(
    tenant_id: TenantId,
    calls: int = 1,
    *,
    tokens: int = 100,
    cost: float = 0.0,
    run_id: RunId | None = None,
) -> RunJournal:
    """Un run terminé qui a consommé ``calls`` appels de modèle."""
    journal = RunJournal(tenant_id=tenant_id, run_id=run_id)
    journal.start("Où en est mon devis ?")
    for _ in range(calls):
        journal.model_turn(
            Message.assistant("Voilà."),
            usage=Usage(input_tokens=tokens, output_tokens=0),
            cost_usd=cost,
        )
    return journal.complete()


async def written(store: EventStore, journal: RunJournal) -> None:
    scope = journal.scope
    last = await store.last_seq(scope.tenant_id, scope.session_id)
    await store.append(journal.take(), expected_seq=last)


class CountingStore(InMemoryEventStore):
    """Journal qui compte ses relectures : c'est ainsi qu'on voit un réchauffage."""

    def __init__(self) -> None:
        super().__init__()
        self.queries = 0

    async def query(self, query: EventQuery) -> list[Event]:
        self.queries += 1
        return await super().query(query)


# --- Fenêtres calendaires (§15) ------------------------------------------------


def test_a_day_starts_at_midnight_utc_and_a_month_on_the_first() -> None:
    moment = datetime(2026, 9, 23, 14, 30, tzinfo=UTC)
    day = Period.of("day", moment)
    month = Period.of("month", moment)
    assert (day.key, month.key) == ("day:2026-09-23", "month:2026-09")
    assert day.start == datetime(2026, 9, 23, tzinfo=UTC)
    assert month.start == datetime(2026, 9, 1, tzinfo=UTC)
    assert day.end == datetime(2026, 9, 24, tzinfo=UTC)
    assert month.end == datetime(2026, 10, 1, tzinfo=UTC)
    assert day.resets_in(moment) == pytest.approx(9.5 * 3600)
    assert (str(day), str(month)) == ("la journée", "le mois")


def test_a_december_month_rolls_over_to_the_next_year() -> None:
    december = Period.of("month", datetime(2026, 12, 31, 23, 59, tzinfo=UTC))
    assert december.end == datetime(2027, 1, 1, tzinfo=UTC)
    assert december.resets_in(datetime(2026, 12, 31, 23, 59, tzinfo=UTC)) == pytest.approx(60.0)


def test_a_period_is_read_in_utc_whatever_the_caller_s_timezone() -> None:
    # 1 h 30 du matin à Paris en été (UTC+2) : en UTC, c'est encore la veille.
    paris = datetime(2026, 7, 14, 1, 30, tzinfo=timezone(timedelta(hours=2)))
    assert Period.of("day", paris).key == "day:2026-07-13"


def test_a_reset_is_never_in_the_past() -> None:
    day = Period.of("day", datetime(2026, 9, 23, tzinfo=UTC))
    assert day.resets_in(datetime(2026, 9, 30, tzinfo=UTC)) == 0.0


def test_period_keys_sort_in_time_order() -> None:
    # Le compteur élague par ``min(periods)`` : les clés doivent se trier.
    days = [Period.of("day", datetime(2026, 9, d, tzinfo=UTC)).key for d in (1, 9, 23)]
    assert sorted(days) == days
    assert PERIODS == ("day", "month")


# --- Le compteur pose une valeur par run, il ne l'ajoute pas -------------------


async def test_recording_the_same_run_twice_does_not_double_it() -> None:
    counter = InMemoryUsageCounter()
    run, key = new_run_id(), Period.of("day").key
    for _ in range(2):
        await counter.record(DUPONT, key, run, Spent(Usage(input_tokens=300), 0.5, 2))
    consumed = await counter.consumed(DUPONT, key)
    assert (consumed.tokens, consumed.cost, consumed.calls) == (300, 0.5, 2)


async def test_recording_a_run_again_replaces_what_it_had_cost() -> None:
    # Un run enregistré à mi-chemin puis à la fin : c'est la dernière valeur
    # qui compte, et non la somme des deux.
    counter = InMemoryUsageCounter()
    run, key = new_run_id(), Period.of("day").key
    await counter.record(DUPONT, key, run, Spent(Usage(input_tokens=100)))
    await counter.record(DUPONT, key, run, Spent(Usage(input_tokens=400)))
    assert (await counter.consumed(DUPONT, key)).tokens == 400


async def test_runs_add_up_for_a_tenant_and_the_neighbour_is_counted_apart() -> None:
    counter = InMemoryUsageCounter()
    key = Period.of("day").key
    for tokens in (100, 250):
        await counter.record(DUPONT, key, new_run_id(), Spent(Usage(input_tokens=tokens)))
    await counter.record(MARTIN, key, new_run_id(), Spent(Usage(input_tokens=999)))
    assert (await counter.consumed(DUPONT, key)).tokens == 350
    assert (await counter.consumed(MARTIN, key)).tokens == 999
    assert (await counter.consumed(TenantId("inconnu"), key)).tokens == 0


async def test_the_counter_keeps_only_its_most_recent_periods() -> None:
    counter = InMemoryUsageCounter()
    days = [Period.of("day", datetime(2026, 9, d, tzinfo=UTC)) for d in range(1, MAX_PERIODS + 3)]
    for day in days:
        await counter.record(DUPONT, day.key, new_run_id(), Spent(Usage(input_tokens=10)))
    # Les clés se trient dans le temps : ce sont les plus anciennes qui partent.
    assert (await counter.consumed(DUPONT, days[0].key)).tokens == 0
    assert (await counter.consumed(DUPONT, days[1].key)).tokens == 0
    assert (await counter.consumed(DUPONT, days[2].key)).tokens == 10
    assert (await counter.consumed(DUPONT, days[-1].key)).tokens == 10


async def test_closing_the_counter_forgets_everything() -> None:
    counter = InMemoryUsageCounter()
    key = Period.of("day").key
    await counter.record(DUPONT, key, new_run_id(), Spent(Usage(input_tokens=10)))
    await counter.aclose()
    assert (await counter.consumed(DUPONT, key)).tokens == 0


# --- Réchauffage : le compteur est un cache du journal ------------------------


async def test_the_counter_warms_up_from_the_journal_once_per_window(
    demo: ConfigFactory,
) -> None:
    store = CountingStore()
    await written(store, spending(DUPONT, 3))
    tenant = registry(demo, budgets=AFTER_ONE).get(DUPONT)
    usage = TenantUsage(InMemoryUsageCounter(), store)
    with pytest.raises(BudgetExhausted):
        await usage.check(tenant)
    read = store.queries
    assert read == 1
    with pytest.raises(BudgetExhausted):
        await usage.check(tenant)
    assert store.queries == read


async def test_a_run_recorded_during_the_warm_up_is_not_counted_twice(
    demo: ConfigFactory,
) -> None:
    """L'invariant du compteur : réchauffage et vie courante peuvent se croiser."""
    store = InMemoryEventStore()
    journal = spending(DUPONT, 2)
    await written(store, journal)
    tenant = registry(demo, budgets={"tenant": {"max_tokens_per_day": 10_000}}).get(DUPONT)
    counter = InMemoryUsageCounter()
    usage = TenantUsage(counter, store)
    key = Period.of("day").key

    await usage.check(tenant)
    assert (await counter.consumed(DUPONT, key)).tokens == 200
    # Le même run se termine après le réchauffage et pose ce qu'il a coûté.
    await usage.record(tenant, journal.run_id, Spent(Usage(input_tokens=200)))
    assert (await counter.consumed(DUPONT, key)).tokens == 200


async def test_a_run_spread_over_two_warm_up_pages_keeps_all_its_calls(
    demo: ConfigFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(usage_module, "PAGE", 2)
    store = InMemoryEventStore()
    await written(store, spending(DUPONT, 5))
    tenant = registry(demo, budgets={"tenant": {"max_tokens_per_day": 10_000}}).get(DUPONT)
    counter = InMemoryUsageCounter()
    await TenantUsage(counter, store).check(tenant)
    assert (await counter.consumed(DUPONT, Period.of("day").key)).tokens == 500


async def test_the_warm_up_ignores_what_precedes_the_window(demo: ConfigFactory) -> None:
    store = InMemoryEventStore()
    await written(store, spending(DUPONT, 3))
    tenant = registry(demo, budgets=AFTER_ONE).get(DUPONT)
    usage = TenantUsage(InMemoryUsageCounter(), store)
    # Demain : la fenêtre commence après tout ce qui est écrit, rien n'est dû.
    await usage.check(tenant, datetime.now(UTC) + timedelta(days=1))


async def test_the_spending_of_a_neighbour_is_not_counted(demo: ConfigFactory) -> None:
    store = InMemoryEventStore()
    await written(store, spending(MARTIN, 5, tokens=1_000))
    tenants = registry(demo, budgets=AFTER_ONE)
    await TenantUsage(InMemoryUsageCounter(), store).check(tenants.get(DUPONT))


async def test_a_tenant_without_budget_is_neither_read_nor_counted(demo: ConfigFactory) -> None:
    store = CountingStore()
    await written(store, spending(MARTIN, 3))
    martin = registry(demo, budgets=AFTER_ONE).get(MARTIN)
    counter = InMemoryUsageCounter()
    usage = TenantUsage(counter, store)
    await usage.check(martin)
    await usage.record(martin, new_run_id(), Spent(Usage(input_tokens=300)))
    assert store.queries == 0
    assert (await counter.consumed(MARTIN, Period.of("day").key)).tokens == 0


async def test_a_shared_counter_has_nothing_to_warm_up(demo: ConfigFactory) -> None:
    # Un compteur partagé et durable (J5.3) porte déjà la valeur de tous.
    store = CountingStore()
    await written(store, spending(DUPONT, 5, tokens=1_000))
    tenant = registry(demo, budgets=AFTER_ONE).get(DUPONT)
    await TenantUsage(InMemoryUsageCounter(), store, warm=False).check(tenant)
    assert store.queries == 0


# --- Ce que le refus dit, et sur quelle fenêtre -------------------------------


async def test_an_exhausted_day_says_its_limit_and_when_it_resets(demo: ConfigFactory) -> None:
    store = InMemoryEventStore()
    await written(store, spending(DUPONT, 1, tokens=500))
    tenant = registry(demo, budgets={"tenant": {"max_tokens_per_day": 400}}).get(DUPONT)
    with pytest.raises(BudgetExhausted) as raised:
        await TenantUsage(InMemoryUsageCounter(), store).check(tenant)
    error = raised.value
    assert error.tenant_id == DUPONT
    assert (error.reached.limit, error.reached.value, error.reached.spent) == (
        "max_tokens",
        400.0,
        500.0,
    )
    assert error.reached.period.kind == "day"
    assert str(error.reached).startswith("budget du client atteint pour la journée")
    assert 0.0 < error.retry_after <= 86_400.0


async def test_the_month_is_checked_as_well_as_the_day(demo: ConfigFactory) -> None:
    store = InMemoryEventStore()
    await written(store, spending(DUPONT, 1, tokens=500))
    budget = {"tenant": {"max_tokens_per_day": 10_000, "max_tokens_per_month": 400}}
    tenant = registry(demo, budgets=budget).get(DUPONT)
    assert tenant.budget.periods == ("day", "month")
    with pytest.raises(BudgetExhausted) as raised:
        await TenantUsage(InMemoryUsageCounter(), store).check(tenant)
    assert raised.value.reached.period.kind == "month"


async def test_record_posts_the_value_in_every_window_of_the_budget(demo: ConfigFactory) -> None:
    budget = {"tenant": {"max_cost_per_day": 5.0, "max_tokens_per_month": 900}}
    tenant = registry(demo, budgets=budget).get(DUPONT)
    counter = InMemoryUsageCounter()
    usage = TenantUsage(counter, InMemoryEventStore())
    await usage.record(tenant, new_run_id(), Spent(Usage(input_tokens=42), 0.25, 1))
    for kind in PERIODS:
        assert (await counter.consumed(DUPONT, Period.of(kind).key)).tokens == 42


async def test_consumption_reads_the_journal_and_says_what_is_left(demo: ConfigFactory) -> None:
    store = InMemoryEventStore()
    await written(store, spending(DUPONT, 2, tokens=150, cost=0.25))
    budget = {"tenant": {"max_tokens_per_day": 1_000, "max_cost_per_day": 2.0}}
    tenant = registry(demo, budgets=budget).get(DUPONT)
    usage = TenantUsage(InMemoryUsageCounter(), store)

    found = await usage.consumption(DUPONT, tenant.budget)
    assert (found.runs, found.spent.tokens) == (1, 300)
    assert found.spent.cost == pytest.approx(0.5)
    assert found.left("max_tokens") == 700.0
    assert found.left("max_cost") == pytest.approx(1.5)
    assert found.left("max_calls") is None
    assert found.resets_at == found.period.end
    # Un client sans plafond a droit au même rapport, sans ligne de plafond.
    without = await usage.consumption(DUPONT)
    assert without.limits == () and without.spent.tokens == 300


# --- Débit : une fenêtre glissante de soixante secondes (#39) -----------------


def test_the_window_lets_the_limit_through_then_refuses() -> None:
    window = RateWindow()
    assert window.take("k", 2, now=0.0) is None
    assert window.take("k", 2, now=1.0) is None
    assert window.take("k", 2, now=2.0) == pytest.approx(58.0)
    assert WINDOW == 60.0


def test_the_window_slides_instead_of_following_the_clock() -> None:
    window = RateWindow()
    for moment in (0.0, 0.5):
        assert window.take("k", 2, now=moment) is None
    # Une minute calendaire laisserait passer le double à cheval sur la minute.
    assert window.take("k", 2, now=59.9) is not None
    assert window.take("k", 2, now=60.1) is None


def test_a_refused_request_is_not_counted() -> None:
    window = RateWindow()
    assert window.take("k", 1, now=0.0) is None
    for moment in (10.0, 20.0, 30.0):
        assert window.take("k", 1, now=moment) is not None
    # Les refus n'ont pas repoussé la fenêtre : elle s'ouvre bien 60 s après
    # la demande servie, et non 60 s après le dernier refus.
    assert window.take("k", 1, now=60.0) is None


def test_each_counted_thing_has_its_own_window() -> None:
    window = RateWindow()
    assert window.take("tenant:a", 1, now=0.0) is None
    assert window.take("tenant:b", 1, now=0.0) is None
    assert window.take("tenant:a", 1, now=1.0) is not None
    assert "2 compteur(s)" in repr(window)
    window.forget("tenant:a")
    assert window.take("tenant:a", 1, now=1.0) is None


def test_a_tenant_quota_refuses_the_second_run_of_the_minute() -> None:
    quota = Quota()
    quota.check(DUPONT, 1)
    with pytest.raises(QuotaExceeded, match="1 par minute") as raised:
        quota.check(DUPONT, 1)
    assert raised.value.limit == 1
    assert 0.0 < raised.value.retry_after <= WINDOW
    # Le voisin a sa propre fenêtre, et un client sans quota n'est pas compté.
    quota.check(MARTIN, 1)
    for _ in range(5):
        quota.check(DUPONT, None)


# --- Bout en bout : ce qu'une instance refuse, et ce qu'elle n'écrit pas -------


async def test_the_budget_is_read_at_launch_so_the_first_run_always_passes(
    demo: ConfigFactory,
) -> None:
    config = demo(storage=JOURNAL, tenants=clients(budgets=AFTER_ONE))
    async with Loom.from_config(config) as loom:
        assert (await loom.run("demo", QUESTION, tenant=DUPONT)).text == ANSWER
        with pytest.raises(BudgetExhausted, match="budget du client atteint"):
            await loom.run("demo", QUESTION, tenant=DUPONT)
        # Un run refusé n'a pas existé : le journal ne porte que le premier.
        assert (await loom.consumption(DUPONT)).runs == 1
        # Le voisin, sans budget, n'a pas à en souffrir.
        assert (await loom.run("demo", QUESTION, tenant=MARTIN)).text == ANSWER


async def test_a_new_instance_learns_the_day_from_the_journal(demo: ConfigFactory) -> None:
    config = demo(storage=JOURNAL, tenants=clients(budgets=AFTER_ONE))
    async with Loom.from_config(config) as first:
        await first.run("demo", QUESTION, tenant=DUPONT)
    # Le compteur ne survit pas à l'instance ; le journal, lui, dit la vérité.
    async with Loom.from_config(config) as second:
        with pytest.raises(BudgetExhausted):
            await second.run("demo", QUESTION, tenant=DUPONT)


async def test_a_dollar_budget_counts_what_the_pricing_says(demo: ConfigFactory) -> None:
    config = demo(
        models=[PRICED],
        storage=JOURNAL,
        tenants=clients(budgets={"tenant": {"max_cost_per_month": 0.000001}}),
    )
    async with Loom.from_config(config) as loom:
        result = await loom.run("demo", QUESTION, tenant=DUPONT)
        assert result.cost_usd > 0.000001
        with pytest.raises(BudgetExhausted) as raised:
            await loom.run("demo", QUESTION, tenant=DUPONT)
    assert (raised.value.reached.limit, raised.value.reached.period.kind) == ("max_cost", "month")


async def test_a_subrun_is_counted_with_its_parent_and_not_apart(tree: ConfigFactory) -> None:
    path = tree(tenants=clients(budgets={"tenant": {"max_tokens_per_day": 1_000_000}}))
    counter = InMemoryUsageCounter()
    async with Loom(load_config(path), counter=counter) as loom:
        await loom.run("demo", TREE_QUESTION, tenant=DUPONT)
        found = await loom.consumption(DUPONT)
        posed = await counter.consumed(DUPONT, Period.of("day").key)
    # Deux runs au journal, une seule valeur au compteur : la consommation de
    # l'enfant est déjà dans le total de son parent.
    assert (found.runs, posed.tokens) == (1, found.spent.tokens)
    assert "1 run(s)" in repr(counter)


async def test_a_tenant_quota_refuses_the_second_launch_and_writes_nothing(
    demo: ConfigFactory,
) -> None:
    config = demo(storage=JOURNAL, tenants=clients(quotas={"runs_per_minute": 1}))
    async with Loom.from_config(config) as loom:
        assert (await loom.run("demo", QUESTION, tenant=DUPONT)).text == ANSWER
        with pytest.raises(QuotaExceeded, match="par minute"):
            await loom.run("demo", QUESTION, tenant=DUPONT)
        assert (await loom.consumption(DUPONT)).runs == 1
        assert (await loom.run("demo", QUESTION, tenant=MARTIN)).text == ANSWER


async def test_a_stream_is_admitted_like_a_run(demo: ConfigFactory) -> None:
    config = demo(storage=JOURNAL, tenants=clients(quotas={"runs_per_minute": 1}))
    async with Loom.from_config(config) as loom:
        await loom.run("demo", QUESTION, tenant=DUPONT)
        with pytest.raises(QuotaExceeded):
            async for _ in loom.stream("demo", QUESTION, tenant=DUPONT):
                pass


# --- Accès REST : 429 et Retry-After (#39) ------------------------------------


async def test_rest_answers_429_when_the_day_is_exhausted(demo: ConfigFactory) -> None:
    pytest.importorskip("fastapi", reason="extra 'http' absent")
    pytest.importorskip("httpx2", reason="client HTTP de test absent")
    import httpx2

    from loom_ia.access.http import create_app
    from loom_ia.config.keys import fingerprint, new_api_key

    cle = new_api_key()
    path = demo(
        storage=JOURNAL,
        tenants=clients(budgets=AFTER_ONE),
        security={"api_keys": [{"id": "app", "hash": fingerprint(cle), "tenant": DUPONT}]},
    )
    async with Loom.from_config(path) as loom:
        transport = httpx2.ASGITransport(create_app(loom))
        async with httpx2.AsyncClient(transport=transport, base_url="http://loom.test") as http:
            headers = {"Authorization": f"Bearer {cle}"}
            body = {"message": QUESTION}
            assert (
                await http.post("/v1/agents/demo/runs", json=body, headers=headers)
            ).status_code == 201
            refus = await http.post("/v1/agents/demo/runs", json=body, headers=headers)
    assert refus.status_code == 429
    assert "budget du client atteint" in refus.json()["detail"]
    # La remise à zéro est une date : l'attente se compte en heures, pas en secondes.
    assert int(refus.headers["Retry-After"]) > 1


async def test_rest_answers_429_when_the_tenant_quota_is_reached(demo: ConfigFactory) -> None:
    pytest.importorskip("fastapi", reason="extra 'http' absent")
    pytest.importorskip("httpx2", reason="client HTTP de test absent")
    import httpx2

    from loom_ia.access.http import create_app
    from loom_ia.config.keys import fingerprint, new_api_key

    cle = new_api_key()
    path = demo(
        storage=JOURNAL,
        tenants=clients(quotas={"runs_per_minute": 1}),
        security={"api_keys": [{"id": "app", "hash": fingerprint(cle), "tenant": DUPONT}]},
    )
    async with Loom.from_config(path) as loom:
        transport = httpx2.ASGITransport(create_app(loom))
        async with httpx2.AsyncClient(transport=transport, base_url="http://loom.test") as http:
            headers = {"Authorization": f"Bearer {cle}"}
            body = {"message": QUESTION}
            assert (
                await http.post("/v1/agents/demo/runs", json=body, headers=headers)
            ).status_code == 201
            refus = await http.post("/v1/agents/demo/runs", json=body, headers=headers)
    assert refus.status_code == 429
    assert 1 <= int(refus.headers["Retry-After"]) <= 60
    assert "par minute" in refus.json()["detail"]


async def test_a_key_rate_limit_protects_the_server_even_on_a_read(demo: ConfigFactory) -> None:
    pytest.importorskip("fastapi", reason="extra 'http' absent")
    pytest.importorskip("httpx2", reason="client HTTP de test absent")
    import httpx2

    from loom_ia.access.http import create_app
    from loom_ia.config.keys import fingerprint, new_api_key

    bridee, libre = new_api_key(), new_api_key()
    path = demo(
        tenants=clients(),
        security={
            "api_keys": [
                {
                    "id": "bridee",
                    "hash": fingerprint(bridee),
                    "tenant": DUPONT,
                    "rate_limit": {"per_minute": 2},
                },
                {"id": "libre", "hash": fingerprint(libre), "tenant": MARTIN},
            ]
        },
    )
    async with Loom.from_config(path) as loom:
        transport = httpx2.ASGITransport(create_app(loom))
        async with httpx2.AsyncClient(transport=transport, base_url="http://loom.test") as http:
            chez_bridee = {"Authorization": f"Bearer {bridee}"}
            for _ in range(2):
                assert (await http.get("/v1/agents", headers=chez_bridee)).status_code == 200
            refus = await http.get("/v1/agents", headers=chez_bridee)
            assert refus.status_code == 429
            assert "requêtes par minute" in refus.json()["detail"]
            assert 1 <= int(refus.headers["Retry-After"]) <= 60
            # Une clé sans limite de débit n'est pas comptée.
            chez_libre = {"Authorization": f"Bearer {libre}"}
            for _ in range(5):
                assert (await http.get("/v1/agents", headers=chez_libre)).status_code == 200


# --- Ligne de commande : `report --periode`, et ce que `validate` en dit -------


def test_report_periode_prints_what_a_tenant_spent_and_what_is_left(
    demo: ConfigFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    from loom_ia.access.cli import main

    config = demo(
        models=[PRICED],
        storage=JOURNAL,
        tenants=clients(budgets={"tenant": {"max_cost_per_day": 5.0}}),
    )
    assert main(["--config", str(config), "run", "demo", QUESTION, "--tenant", DUPONT]) == 0
    capsys.readouterr()

    assert main(["--config", str(config), "report", "--periode", "jour", "--tenant", DUPONT]) == 0
    out = capsys.readouterr().out
    assert f"Client     : {DUPONT}" in out
    assert "Runs       : 1" in out
    assert "plafond max_cost" in out and "reste" in out
    assert "Remise à 0 :" in out

    arguments = ["report", "--periode", "mois", "--tenant", DUPONT, "--json"]
    assert main(["--config", str(config), *arguments]) == 0
    found = json.loads(capsys.readouterr().out)
    assert found["tenant_id"] == DUPONT
    assert found["period"].startswith("month:")
    assert found["runs"] == 1
    assert found["cost_usd"] > 0.0
    # Le plafond est posé sur la journée : le mois n'en porte aucun.
    assert found["limits"] == {}


def test_report_without_a_target_says_what_it_expects(
    demo: ConfigFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    from loom_ia.access.cli import main

    assert main(["--config", str(demo()), "report"]) == 2
    assert "--periode" in capsys.readouterr().err


def test_validate_shows_the_budget_and_the_quota_of_a_tenant(
    demo: ConfigFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    from loom_ia.access.cli import main

    budgets = {"tenant": {"max_cost_per_day": 5.0, "max_tokens_per_month": 200_000}}
    config = demo(tenants=clients(budgets=budgets, quotas={"runs_per_minute": 3}))
    assert main(["--config", str(config), "validate"]) == 0
    out = capsys.readouterr().out
    assert "    budget : max_cost par jour" in out
    assert "max_tokens par mois" in out
    assert "    quota : 3 run(s) par minute" in out


def test_sessions_list_can_name_the_tenant_it_asks_for(
    demo: ConfigFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    # Le `--tenant` manquait à `sessions` et à `report` en 5.1a.
    from loom_ia.access.cli import main

    config = demo(storage=JOURNAL, tenants=clients())
    assert main(["--config", str(config), "run", "demo", QUESTION, "--tenant", DUPONT]) == 0
    capsys.readouterr()
    assert main(["--config", str(config), "sessions", "list", "--tenant", DUPONT]) == 0
    assert "événements" in capsys.readouterr().out
    assert main(["--config", str(config), "sessions", "list", "--tenant", MARTIN]) == 0
    assert "Aucune session." in capsys.readouterr().out
