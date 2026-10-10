# SPDX-License-Identifier: Apache-2.0
"""Ce que seul un vrai Postgres peut montrer : la barrière et les droits (J5.3a).

Le journal et le magasin d'idempotence sont éprouvés fonction par fonction
par les suites de contrat, qui tournent aussi sur Postgres. Ici, on éprouve ce
qui n'est pas du code de loom mais de la base : la politique de lignes tient-
elle quand le filtre applicatif est oublié, le rôle applicatif peut-il
modifier un événement écrit, et que dit loom quand la base n'est pas prête.

``LOOM_TEST_POSTGRES`` désigne la base ; sans elle, tout est sauté. Le rôle du
DSN doit posséder sa base et **ne pas** être superutilisateur — la fixture le
vérifie, un superutilisateur contournant tout ce qui suit.
"""

import asyncio
from urllib.parse import urlsplit, urlunsplit

import pytest

# Le module importe le pilote : sans l'extra, tout l'essai se saute.
pytest.importorskip("asyncpg", reason="extra 'postgres' absent")

import asyncpg

from loom_ia.adapters.postgres.pool import (
    PoolLimits,
    PostgresNotPrepared,
    PostgresPool,
    RoleUnavailable,
)
from loom_ia.adapters.postgres.sql import DEFAULT_ROLE, EVENTS_TABLE, ddl
from loom_ia.adapters.stores.postgres import PostgresEventStore
from loom_ia.core.events import EventDraft
from loom_ia.core.model import SessionId, TenantId
from loom_ia.testing import RunJournal

pytestmark = pytest.mark.integration

UN = TenantId("dupont-plomberie")
AUTRE = TenantId("martin-elec")

# Rôle fabriqué par les essais : il n'a aucun droit et n'appartient pas au
# rôle applicatif. Deux situations s'en déduisent — une base qu'il ne peut pas
# préparer, et un rôle applicatif qu'il ne peut pas prendre.
ETRANGER = "loom_essai_etranger"
MOT_DE_PASSE = "etranger"


def drafts(tenant: TenantId, session: SessionId) -> list[EventDraft]:
    journal = RunJournal(session_id=session, tenant_id=tenant)
    journal.start("Où en est le devis ?")
    journal.complete()
    return journal.take()


async def two_clients(dsn: str) -> PostgresEventStore:
    """Un journal par client, écrits par le rôle applicatif."""
    store = PostgresEventStore(dsn)
    await store.append(drafts(UN, SessionId("s-un")), expected_seq=0)
    await store.append(drafts(AUTRE, SessionId("s-autre")), expected_seq=0)
    return store


def as_role(dsn: str, user: str, password: str) -> str:
    parts = urlsplit(dsn)
    if not parts.scheme.startswith("postgres"):
        pytest.skip("LOOM_TEST_POSTGRES n'est pas une URL : rôle non remplaçable")
    port = f":{parts.port}" if parts.port else ""
    return urlunsplit(parts._replace(netloc=f"{user}:{password}@{parts.hostname}{port}"))


async def make_stranger(connection: asyncpg.Connection[asyncpg.Record]) -> None:
    try:
        await connection.execute(
            f"DO $$ BEGIN CREATE ROLE {ETRANGER} LOGIN PASSWORD '{MOT_DE_PASSE}';"
            " EXCEPTION WHEN duplicate_object THEN NULL; END $$;"
        )
    except asyncpg.InsufficientPrivilegeError:
        pytest.skip("le rôle du DSN ne peut pas créer de rôle : essai non jouable")


# --- La barrière -------------------------------------------------------------


async def test_a_forgotten_tenant_filter_gives_nothing_of_the_others(postgres_dsn: str) -> None:
    """Le ``WHERE tenant_id`` n'est plus ce qui protège : on l'oublie exprès."""
    store = await two_clients(postgres_dsn)
    connection = await asyncpg.connect(postgres_dsn)
    try:
        await connection.execute(f"SET ROLE {DEFAULT_ROLE}")
        async with connection.transaction():
            await connection.execute("SELECT set_config('loom.tenant_id', $1, true)", UN)
            # Aucune clause sur le client : la politique s'en charge.
            total = await connection.fetchval(f"SELECT count(*) FROM {EVENTS_TABLE}")
            vus = await connection.fetch(f"SELECT DISTINCT tenant_id FROM {EVENTS_TABLE}")
        assert [row["tenant_id"] for row in vus] == [UN]
        assert total == len(drafts(UN, SessionId("s-un")))
    finally:
        await connection.close()
        await store.aclose()


async def test_without_the_setting_the_journal_looks_empty(postgres_dsn: str) -> None:
    """Le réglage absent rend NULL : la comparaison est NULL, rien ne passe.

    Et c'est vrai même pour le rôle qui possède la table, par ``FORCE`` : ici
    on ne prend pas le rôle applicatif, et le journal paraît vide quand même.
    """
    store = await two_clients(postgres_dsn)
    connection = await asyncpg.connect(postgres_dsn)
    try:
        assert await connection.fetchval(f"SELECT count(*) FROM {EVENTS_TABLE}") == 0
    finally:
        await connection.close()
        await store.aclose()


async def test_writing_for_another_client_is_refused(postgres_dsn: str) -> None:
    """La politique vaut aussi à l'écriture (``WITH CHECK``)."""
    store = await two_clients(postgres_dsn)
    connection = await asyncpg.connect(postgres_dsn)
    try:
        await connection.execute(f"SET ROLE {DEFAULT_ROLE}")
        async with connection.transaction():
            await connection.execute("SELECT set_config('loom.tenant_id', $1, true)", UN)
            with pytest.raises(asyncpg.InsufficientPrivilegeError, match="row-level security"):
                await connection.execute(
                    f"INSERT INTO {EVENTS_TABLE} (tenant_id, session_id, seq, event_id, ts,"
                    " run_id, root_run_id, type, category, status, facets, event)"
                    " VALUES ($1, 's-autre', 99, 'e-99', now(), 'r-99', 'r-99', 'run.started',"
                    " 'lifecycle', 'info', '{}'::jsonb, '{}')",
                    AUTRE,
                )
    finally:
        await connection.close()
        await store.aclose()


# --- Les droits --------------------------------------------------------------


async def test_the_app_role_cannot_change_an_event(postgres_dsn: str) -> None:
    """L'immuabilité du journal est un privilège, pas une convention de code."""
    store = await two_clients(postgres_dsn)
    connection = await asyncpg.connect(postgres_dsn)
    try:
        await connection.execute(f"SET ROLE {DEFAULT_ROLE}")
        async with connection.transaction():
            await connection.execute("SELECT set_config('loom.tenant_id', $1, true)", UN)
            with pytest.raises(asyncpg.InsufficientPrivilegeError, match="permission denied"):
                await connection.execute(f"UPDATE {EVENTS_TABLE} SET type = 'menti'")
    finally:
        await connection.close()
        await store.aclose()


# --- Ce que loom dit quand la base n'est pas prête ---------------------------


async def test_a_base_the_role_cannot_prepare_says_where_to_look(postgres_dsn: str) -> None:
    connection = await asyncpg.connect(postgres_dsn)
    try:
        await make_stranger(connection)
    finally:
        await connection.close()
    store = PostgresEventStore(as_role(postgres_dsn, ETRANGER, MOT_DE_PASSE))
    try:
        with pytest.raises(PostgresNotPrepared, match="loom storage sql"):
            await store.last_seq(UN, SessionId("s-un"))
    finally:
        await store.aclose()


async def test_a_role_that_cannot_take_the_app_role_says_so(postgres_dsn: str) -> None:
    """Le schéma est là — c'est le ``SET ROLE`` qui échoue, et il le dit."""
    owner = await two_clients(postgres_dsn)
    connection = await asyncpg.connect(postgres_dsn)
    try:
        await make_stranger(connection)
        await connection.execute(
            f"GRANT SELECT ON {EVENTS_TABLE} TO {ETRANGER}"
        )  # de quoi lire, s'il pouvait
    finally:
        await connection.close()
        await owner.aclose()
    store = PostgresEventStore(as_role(postgres_dsn, ETRANGER, MOT_DE_PASSE))
    try:
        with pytest.raises(RoleUnavailable, match=f"GRANT {DEFAULT_ROLE} TO"):
            await store.last_seq(UN, SessionId("s-un"))
    finally:
        await store.aclose()


# --- Plusieurs écrivains -----------------------------------------------------


async def test_ten_writers_on_one_journal_do_not_interleave(postgres_dsn: str) -> None:
    """Le verrou consultatif sérialise ; la clé primaire est le dernier mot."""
    session = SessionId("s-concurrence")
    stores = [PostgresEventStore(postgres_dsn) for _ in range(4)]
    try:
        lots = [
            store.append(drafts(UN, session), expected_seq=None)
            for store in stores
            for _ in range(3)
        ]
        written = await asyncio.gather(*lots)
        seqs = sorted(event.seq for events in written for event in events)
        assert seqs == list(range(1, len(seqs) + 1))
        assert await stores[0].last_seq(UN, session) == len(seqs)
    finally:
        for store in stores:
            await store.aclose()


# --- Délais, horloge et pool (DON-2) ----------------------------------------


async def test_a_client_clock_ahead_cannot_steal_a_live_reservation(
    postgres_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """La date qui décide de la reprise d'une clé est celle de la base, pas celle du client."""
    from datetime import UTC, datetime, timedelta

    from loom_ia.adapters.idempotency import postgres as idempotency_module
    from loom_ia.adapters.idempotency.postgres import PostgresIdempotency
    from loom_ia.core.ports import KeyScope

    scope = KeyScope(tenant_id=UN, session_id=SessionId("s-un"))
    honest = PostgresIdempotency(postgres_dsn)
    skewed = PostgresIdempotency(postgres_dsn)

    class Ahead(datetime):
        @classmethod
        def now(cls, tz: object = None) -> Ahead:  # type: ignore[override]
            return cls.fromtimestamp((datetime.now(UTC) + timedelta(hours=1)).timestamp(), UTC)

    try:
        assert await honest.reserve("cle-vivante", 60, scope, holder="a") is True
        # Une machine dont l'horloge avance d'une heure : sa « maintenant » dépasse l'échéance.
        monkeypatch.setattr(idempotency_module, "datetime", Ahead, raising=False)
        assert await skewed.reserve("cle-vivante", 60, scope, holder="b") is False
    finally:
        await honest.aclose()
        await skewed.aclose()


async def test_a_writer_waiting_for_a_stuck_journal_lock_gives_up(postgres_dsn: str) -> None:
    store = PostgresEventStore(postgres_dsn, limits=PoolLimits(lock_timeout=0.5))
    session = SessionId("s-bloque")
    await store.append(drafts(UN, session), expected_seq=0)
    holder = await asyncpg.connect(postgres_dsn)
    try:
        # Le verrou d'un autre process, jamais rendu : celui que prend ``append``.
        await holder.execute("SELECT pg_advisory_lock(hashtext($1)::bigint)", f"{UN}/{session}")
        with pytest.raises(asyncpg.LockNotAvailableError):
            await asyncio.wait_for(store.append(drafts(UN, session), expected_seq=None), timeout=20)
        # Le verrou rendu, la même session se réécrit : l'abandon n'a rien abîmé.
        await holder.execute("SELECT pg_advisory_unlock_all()")
        await store.append(drafts(UN, session), expected_seq=None)
    finally:
        await holder.close()
        await store.aclose()


def _pool(dsn: str, limits: PoolLimits) -> PostgresPool:
    return PostgresPool(dsn, table=EVENTS_TABLE, ddl=ddl(role=None), role=None, limits=limits)


async def test_the_pool_has_the_size_it_is_given(postgres_dsn: str) -> None:
    pool = _pool(postgres_dsn, PoolLimits(min_size=2, max_size=3))
    try:
        opened = await pool.pool()
        assert (opened.get_min_size(), opened.get_max_size()) == (2, 3)
    finally:
        await pool.aclose()


async def test_waiting_for_a_free_connection_has_an_end(postgres_dsn: str) -> None:
    pool = _pool(postgres_dsn, PoolLimits(min_size=1, max_size=1, acquire_timeout=0.3))
    try:
        async with pool.transaction():
            with pytest.raises(TimeoutError):
                async with pool.transaction():
                    pass
    finally:
        await pool.aclose()


async def test_a_command_that_lasts_too_long_is_cut(postgres_dsn: str) -> None:
    pool = _pool(postgres_dsn, PoolLimits(command_timeout=0.3))
    try:
        with pytest.raises(TimeoutError):
            async with pool.transaction() as connection:
                await connection.execute("SELECT pg_sleep(5)")
    finally:
        await pool.aclose()


async def test_the_server_waits_no_longer_than_the_lock_timeout(postgres_dsn: str) -> None:
    pool = _pool(postgres_dsn, PoolLimits(lock_timeout=2.5))
    try:
        async with pool.transaction() as connection:
            assert await connection.fetchval("SHOW lock_timeout") == "2500ms"
    finally:
        await pool.aclose()


def test_a_pool_refuses_sizes_and_delays_that_make_no_sense() -> None:
    with pytest.raises(ValueError, match="min_size"):
        PoolLimits(min_size=5, max_size=2)
    with pytest.raises(ValueError, match="min_size"):
        PoolLimits(min_size=0)
    for field in ("command_timeout", "lock_timeout", "acquire_timeout"):
        with pytest.raises(ValueError, match=field):
            PoolLimits(**{field: 0})
