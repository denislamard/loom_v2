# SPDX-License-Identifier: Apache-2.0
"""Ce dont les essais Postgres ont besoin, partagé par les suites qui en font.

Un vrai Postgres, désigné par la variable ``LOOM_TEST_POSTGRES`` : sans elle,
les essais qui en dépendent sont sautés, comme ceux d'un extra absent.

Le DSN doit mener à un rôle qui **n'est pas superutilisateur**. Un
superutilisateur contourne la sécurité au niveau des lignes : les essais
passeraient sans rien prouver, ce qui est pire que de ne pas les lancer. Le
montage refuse donc de tourner dans ce cas au lieu de verdir.

Chaque essai part de tables neuves : elles sont supprimées avant, et les
magasins les recréent à leur première requête — c'est aussi, au passage, la
création à la demande éprouvée trente fois par suite.
"""

import asyncio
import os
from collections.abc import Callable, Coroutine
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest

from loom_ia.adapters.postgres.sql import EVENTS_TABLE, IDEMPOTENCY_TABLE

POSTGRES_ENV = "LOOM_TEST_POSTGRES"


@pytest.fixture
def postgres_dsn() -> str:
    """DSN d'un Postgres de test, tables vidées ; saute l'essai s'il n'y en a pas.

    Montage **synchrone**, pour être demandé par n'importe quelle fabrique,
    asynchrone ou non (``request.getfixturevalue``). Le ménage, lui, est
    asynchrone : il tourne dans sa propre boucle, au besoin dans un fil à
    part — une fixture asynchrone a déjà la sienne, et on n'en imbrique pas.
    """
    dsn = os.environ.get(POSTGRES_ENV, "")
    if not dsn:
        pytest.skip(f"{POSTGRES_ENV} absent : pas de Postgres pour cet essai")
    if _asyncpg() is None:  # pragma: no cover - dépend de l'extra installé
        pytest.skip("extra 'postgres' absent")
    _apart(lambda: _clean(dsn))
    return dsn


def _asyncpg() -> object | None:
    try:
        import asyncpg
    except ImportError:  # pragma: no cover - dépend de l'extra installé
        return None
    return asyncpg


def _apart(work: Callable[[], Coroutine[Any, Any, None]]) -> None:
    """Exécute une coroutine, qu'une boucle tourne déjà ou non."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(work())
        return
    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(asyncio.run, work()).result()


async def _clean(dsn: str) -> None:
    import asyncpg

    connection = await asyncpg.connect(dsn)
    try:
        if await connection.fetchval("SELECT current_setting('is_superuser')") == "on":
            pytest.fail(
                f"{POSTGRES_ENV} mène à un superutilisateur : il contourne la sécurité au "
                "niveau des lignes, et ces essais ne prouveraient rien. Utiliser un rôle "
                "ordinaire, propriétaire de sa base."
            )
        await connection.execute(f"DROP TABLE IF EXISTS {EVENTS_TABLE}, {IDEMPOTENCY_TABLE}")
    finally:
        await connection.close()
