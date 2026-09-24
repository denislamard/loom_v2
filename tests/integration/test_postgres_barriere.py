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

from loom_ia.adapters.postgres.pool import PostgresNotPrepared, RoleUnavailable
from loom_ia.adapters.postgres.sql import DEFAULT_ROLE, EVENTS_TABLE
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
