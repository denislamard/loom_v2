# SPDX-License-Identifier: Apache-2.0
"""Ce dont les essais de service ont besoin, partagé par les suites qui en font.

Deux services, deux variables : ``LOOM_TEST_POSTGRES`` et ``LOOM_TEST_RABBITMQ``.
Ce qui suit vaut pour Postgres ; le courtier, plus bas, se contente d'être là et
de voir ses files vidées avant chaque essai.

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
from importlib.util import find_spec
from typing import Any, NoReturn

import pytest

from loom_ia.adapters.postgres.sql import EVENTS_TABLE, IDEMPOTENCY_TABLE

POSTGRES_ENV = "LOOM_TEST_POSTGRES"
RABBITMQ_ENV = "LOOM_TEST_RABBITMQ"
REDIS_ENV = "LOOM_TEST_REDIS"

# Drapeau des passes où les services sont **censés** tourner : la CI complète,
# et toute vérification qui prétend les avoir éprouvés.
REQUIRE = "--require-services"


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        REQUIRE,
        action="store_true",
        help=(
            "Refuse de sauter un essai de service : variable absente ou extra manquant "
            "devient un échec. À passer là où Postgres, RabbitMQ et Redis sont censés tourner."
        ),
    )


def _absent(request: pytest.FixtureRequest, why: str) -> NoReturn:
    """Saute l'essai, ou le met en échec sous ``--require-services``.

    Un essai de service sauté ne se voit pas : la suite reste verte et personne
    n'apprend que rien n'a été éprouvé. C'est acceptable sur une machine qui n'a
    pas de courtier ; ça ne l'est pas là où les services sont fournis, car une
    variable mal écrite ou un conteneur tombé y rendrait exactement la même
    couleur qu'une suite qui a tout vérifié.
    """
    if request.config.getoption(REQUIRE):
        pytest.fail(f"{why} — et {REQUIRE} exige que cet essai tourne")
    pytest.skip(why)


@pytest.fixture
def postgres_dsn(request: pytest.FixtureRequest) -> str:
    """DSN d'un Postgres de test, tables vidées ; saute l'essai s'il n'y en a pas.

    Montage **synchrone**, pour être demandé par n'importe quelle fabrique,
    asynchrone ou non (``request.getfixturevalue``). Le ménage, lui, est
    asynchrone : il tourne dans sa propre boucle, au besoin dans un fil à
    part — une fixture asynchrone a déjà la sienne, et on n'en imbrique pas.
    """
    dsn = os.environ.get(POSTGRES_ENV, "")
    if not dsn:
        _absent(request, f"{POSTGRES_ENV} absent : pas de Postgres pour cet essai")
    if _asyncpg() is None:  # pragma: no cover - dépend de l'extra installé
        _absent(request, "extra 'postgres' absent")
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


@pytest.fixture
def rabbitmq_url(request: pytest.FixtureRequest) -> str:
    """URL d'un RabbitMQ de test, files vidées ; saute l'essai s'il n'y en a pas.

    Les deux files de loom sont purgées avant chaque essai : un travail resté
    d'un essai précédent serait pris par le worker du suivant.
    """
    url = os.environ.get(RABBITMQ_ENV, "")
    if not url:
        _absent(request, f"{RABBITMQ_ENV} absent : pas de courtier pour cet essai")
    if find_spec("aio_pika") is None:  # pragma: no cover - dépend de l'extra installé
        _absent(request, "extra 'rabbitmq' absent")
    _apart(lambda: _empty(url))
    return url


async def _empty(url: str) -> None:
    import aio_pika

    from loom_ia.adapters.queue.rabbitmq import DELAY_ARGUMENTS, DELAY_QUEUE, WORK_QUEUE

    connection = await aio_pika.connect_robust(url)
    try:
        channel = await connection.channel()
        delayed = await channel.declare_queue(DELAY_QUEUE, durable=True, arguments=DELAY_ARGUMENTS)
        await delayed.purge()
        work = await channel.declare_queue(WORK_QUEUE, durable=True)
        await work.purge()
    finally:
        await connection.close()


@pytest.fixture
def redis_url(request: pytest.FixtureRequest) -> str:
    """URL d'un Redis de test, base vidée ; saute l'essai s'il n'y en a pas.

    Vidée, parce que les clés d'un essai seraient vues du suivant — Redis n'a
    ni table ni schéma à recréer, seulement un espace de noms partagé.
    """
    url = os.environ.get(REDIS_ENV, "")
    if not url:
        _absent(request, f"{REDIS_ENV} absent : pas de Redis pour cet essai")
    if find_spec("redis") is None:  # pragma: no cover - dépend de l'extra installé
        _absent(request, "extra 'redis' absent")
    _apart(lambda: _flush(url))
    return url


async def _flush(url: str) -> None:
    import redis.asyncio as redis

    client = redis.from_url(url)
    try:
        await client.flushdb()  # pyright: ignore[reportUnknownMemberType]
    finally:
        await client.aclose()
