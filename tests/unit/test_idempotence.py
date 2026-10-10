# SPDX-License-Identifier: Apache-2.0
"""Idempotence (J4.4) : magasins, décorateur, clé métier, règle de reprise."""

import asyncio
import threading
from collections.abc import AsyncGenerator, Callable
from dataclasses import replace
from datetime import UTC, datetime
from importlib.util import find_spec
from pathlib import Path
from types import CoroutineType
from typing import Any

import pytest
import yaml
from pydantic import JsonValue

from loom_ia.access.api import Loom
from loom_ia.adapters.idempotency import InMemoryIdempotency
from loom_ia.adapters.stores import InMemoryEventStore
from loom_ia.config import ConfigError, load_config
from loom_ia.core.events import (
    ApprovalGranted,
    ApprovalRequested,
    DurablePayload,
    IdempotencyRecorded,
    IdempotencyReused,
    RunScope,
    ToolCalled,
    ToolCompleted,
)
from loom_ia.core.model import (
    DEFAULT_TENANT,
    MAX_RECORDED,
    ApprovalOutcome,
    ApprovalSettings,
    Approved,
    CallerContext,
    Message,
    ModelSpec,
    PendingApproval,
    PendingCall,
    Pricing,
    Rejected,
    ResultTooLarge,
    RetryPolicy,
    RunState,
    RunStatus,
    SessionId,
    TenantId,
    ToolOutput,
    ToolResultBlock,
    ToolSpec,
    UnknownState,
    new_span_id,
    recordable,
)
from loom_ia.core.ports import (
    IdempotencyStore,
    KeyScope,
    ToolContext,
    ToolError,
    UnknownEffect,
    idempotency_key,
)
from loom_ia.core.projections import apply, fold
from loom_ia.engine import (
    UNKNOWN_STATE,
    JournalIdempotency,
    RunContext,
    SessionWriter,
    ToolExecutor,
    begin_run,
    drive,
)
from loom_ia.engine.executor import ToolEvent
from loom_ia.runtime import build_agent
from loom_ia.testing import RunJournal, ScriptedModel, tool_call_message
from loom_ia.tools import idempotent, tool
from loom_ia.tools.idempotent import BUSY, DEFAULT_RESERVATION, UNKNOWN_EFFECT, IdempotentTool

SESSION = SessionId("atelier")
SCOPE = KeyScope(tenant_id=DEFAULT_TENANT, session_id=SESSION)


# --- Aides --------------------------------------------------------------------


def spec(name: str, **options: object) -> ToolSpec:
    schema: dict[str, JsonValue] = {"type": "object", "properties": {}}
    return ToolSpec.model_validate(
        {"name": name, "description": name, "kind": "python", "input_schema": schema, **options}
    )


def awaiting(*calls: PendingCall) -> RunState:
    journal = RunJournal(agent="demo", session_id=SESSION)
    journal.start("?", context=CallerContext(tenant_id=DEFAULT_TENANT, user_id="u1"))
    events = [draft.to_event(seq) for seq, draft in enumerate(journal.take(), start=1)]
    return fold(events, journal.run_id).model_copy(
        update={"status": RunStatus.AWAITING_TOOLS, "pending_calls": calls}
    )


def call(call_id: str, name: str, *, started: bool = False, **arguments: JsonValue) -> PendingCall:
    return PendingCall(call_id=call_id, name=name, arguments=arguments, started=started)


def _granted(state: RunState, call_id: str, tool_name: str, by: str = "l'artisan") -> RunState:
    """Le même état, avec l'approbation de cet appel déjà accordée au journal."""
    asked = PendingApproval(
        call_id=call_id,
        tool_name=tool_name,
        reason="État inconnu",
        outcome=ApprovalOutcome(verdict="granted", by=by),
    )
    return state.model_copy(update={"approvals": (asked,)})


def _after(state: RunState, *payloads: DurablePayload) -> RunState:
    """L'état après ces événements de l'appel, appliqués dans l'ordre du journal."""
    scope = RunScope(
        tenant_id=state.context.tenant_id,
        session_id=state.session_id,
        run_id=state.run_id,
        root_run_id=state.root_run_id,
        agent=state.agent,
    )
    for seq, payload in enumerate(payloads, start=state.last_seq + 1):
        state = apply(state, scope.draft(payload).to_event(seq))
    return state


def context(*, run_id: str = "run-1", call_id: str = "c1", store: object = None) -> ToolContext:
    return ToolContext(
        tenant_id=DEFAULT_TENANT,
        session_id=SESSION,
        run_id=run_id,  # pyright: ignore[reportArgumentType]
        call_id=call_id,
        agent="demo",
        idempotency=store,  # pyright: ignore[reportArgumentType]
    )


def completed(events: list[ToolEvent]) -> dict[str, ToolOutput]:
    return {e.call_id: e.output for e in events if isinstance(e, ToolCompleted)}


# --- Clé ----------------------------------------------------------------------


def test_the_technical_key_is_the_same_at_every_replay() -> None:
    first = context(call_id="c1")
    assert first.idempotency_key == idempotency_key(first.run_id, "c1")
    assert first.idempotency_key == context(call_id="c1").idempotency_key
    assert first.idempotency_key != context(call_id="c2").idempotency_key
    assert first.idempotency_key != context(run_id="run-2").idempotency_key


# --- Ce qui se mémorise -------------------------------------------------------


def test_a_result_beyond_the_cap_is_loudly_refused() -> None:
    assert recordable({"ok": True}) == {"ok": True}
    with pytest.raises(ResultTooLarge, match="artefact"):
        recordable("x" * (MAX_RECORDED + 1))


def test_a_result_json_does_not_carry_is_refused() -> None:
    with pytest.raises(TypeError):
        recordable(object())


# --- Magasins partagés --------------------------------------------------------
#
# ``memory`` et ``sqlite`` tiennent le même contrat : ce sont les mêmes essais
# qui passent sur l'un et sur l'autre. Le magasin ``journal`` non — sa
# réservation est le ``tool.called`` de l'appel —, il a sa section à lui.

type Magasin = Callable[..., IdempotencyStore]

_SQLITE = pytest.param(
    "sqlite",
    marks=pytest.mark.skipif(find_spec("aiosqlite") is None, reason="extra 'sqlite' absent"),
)
# Postgres demande un service : ``postgres_dsn`` saute l'essai s'il n'y en a pas.
_POSTGRES = pytest.param("postgres", marks=pytest.mark.integration)
_REDIS = pytest.param("redis", marks=pytest.mark.integration)


def _sqlite(path: Path, **options: float) -> IdempotencyStore:
    """Magasin SQLite, importé au besoin : l'extra peut être absent."""
    from loom_ia.adapters.idempotency.sqlite import SqliteIdempotency

    return SqliteIdempotency(path, **options)  # pyright: ignore[reportArgumentType]


def _redis(url: str, **options: float) -> IdempotencyStore:
    """Magasin Redis, importé au besoin : l'extra peut être absent."""
    from loom_ia.adapters.idempotency.redis import RedisIdempotency

    return RedisIdempotency(url, **options)  # pyright: ignore[reportArgumentType]


def _postgres(dsn: str, **options: float) -> IdempotencyStore:
    """Magasin Postgres, importé au besoin : l'extra peut être absent."""
    from loom_ia.adapters.idempotency.postgres import PostgresIdempotency

    return PostgresIdempotency(dsn, **options)  # pyright: ignore[reportArgumentType]


@pytest.fixture(params=["memory", _SQLITE, _POSTGRES, _REDIS])
async def magasin(request: pytest.FixtureRequest, tmp_path: Path) -> AsyncGenerator[Magasin]:
    """Fabrique un magasin partagé du type demandé, et le referme après l'essai."""
    ouverts: list[IdempotencyStore] = []
    # Le service d'abord : la fixture saute l'essai s'il n'y en a pas, et
    # l'import du pilote ne doit pas précéder ce saut.
    fixtures = {"postgres": "postgres_dsn", "redis": "redis_url"}
    needed = fixtures.get(str(request.param))
    service = str(request.getfixturevalue(needed)) if needed else ""

    def build(**options: float) -> IdempotencyStore:
        if request.param == "memory":
            store: IdempotencyStore = InMemoryIdempotency(**options)  # pyright: ignore[reportArgumentType]
        elif request.param == "postgres":
            store = _postgres(service, **options)
        elif request.param == "redis":
            store = _redis(service, **options)
        else:
            store = _sqlite(tmp_path / "idempotence.db", **options)
        ouverts.append(store)
        return store

    yield build
    for store in ouverts:
        await _referme(store)


async def _referme(store: IdempotencyStore) -> None:
    """Ferme un magasin qui tient une connexion ; les autres n'ont rien à fermer."""
    closing = getattr(store, "aclose", None)
    if closing is not None:
        await closing()


async def test_a_held_key_is_refused_to_the_second_caller(magasin: Magasin) -> None:
    store = magasin()
    assert await store.reserve("k", 60, SCOPE) is True
    assert await store.reserve("k", 60, SCOPE) is False
    record = await store.get("k")
    assert record is not None and record.status == "in_progress"


async def test_a_completed_key_gives_its_result_back(magasin: Magasin) -> None:
    store = magasin()
    await store.reserve("k", 60, SCOPE)
    await store.complete("k", {"envoye": True})
    record = await store.get("k")
    assert record is not None
    assert (record.status, record.result) == ("completed", {"envoye": True})
    # L'effet a eu lieu : personne ne le refait.
    assert await store.reserve("k", 60, SCOPE) is False


async def test_an_expired_reservation_can_be_taken_back(magasin: Magasin) -> None:
    store = magasin()
    await store.reserve("k", -1, SCOPE)
    record = await store.get("k")
    assert record is not None and not record.alive(datetime.now(UTC))
    assert await store.reserve("k", 60, SCOPE) is True


async def test_a_released_key_is_free_again(magasin: Magasin) -> None:
    store = magasin()
    await store.reserve("k", 60, SCOPE)
    await store.release("k")
    assert await store.get("k") is None
    assert await store.reserve("k", 60, SCOPE) is True


async def test_a_completed_key_is_not_released(magasin: Magasin) -> None:
    """``release`` rend une réservation, jamais un effet produit."""
    store = magasin()
    await store.reserve("k", 60, SCOPE)
    await store.complete("k", "fait")
    await store.release("k")
    record = await store.get("k")
    assert record is not None and record.status == "completed"


async def test_results_out_of_retention_are_forgotten_reservations_are_not(
    magasin: Magasin,
) -> None:
    store = magasin(retention=-1)
    await store.reserve("vieux", 60, SCOPE)
    await store.complete("vieux", "fait")
    await store.reserve("perimee", -1, SCOPE)
    await store.reserve("frais", 60, SCOPE)
    await store.complete("frais", "fait", 60)
    assert await store.get("vieux") is None
    assert await store.get("frais") is not None
    # La réservation périmée reste : c'est la trace d'un effet d'état inconnu.
    assert await store.get("perimee") is not None


# --- Jeton de détenteur ---------------------------------------------------------
#
# Une réservation périmée est reprise par un autre ; son premier détenteur, resté
# en vie, rend ou complète ensuite sa clé — et ne doit rien défaire de l'autre.


async def test_a_stale_holder_cannot_release_the_key_of_its_successor(magasin: Magasin) -> None:
    store = magasin()
    await store.reserve("k", -1, SCOPE, holder="premier")
    assert await store.reserve("k", 60, SCOPE, holder="second") is True
    await store.release("k", holder="premier")
    # La réservation du second est intacte : personne d'autre ne la prend.
    record = await store.get("k")
    assert record is not None and record.status == "in_progress"
    assert await store.reserve("k", 60, SCOPE, holder="troisieme") is False


async def test_a_stale_holder_cannot_complete_over_its_successor(magasin: Magasin) -> None:
    store = magasin()
    await store.reserve("k", -1, SCOPE, holder="premier")
    await store.reserve("k", 60, SCOPE, holder="second")
    # Sans effet, et sans erreur : la clé existe, elle est à un autre.
    await store.complete("k", "résultat du premier", holder="premier")
    record = await store.get("k")
    assert record is not None and (record.status, record.result) == ("in_progress", None)

    await store.complete("k", "résultat du second", holder="second")
    # Le résultat posé n'est pas écrasé non plus.
    await store.complete("k", "résultat du premier", holder="premier")
    record = await store.get("k")
    assert record is not None
    assert (record.status, record.result) == ("completed", "résultat du second")


async def test_the_holder_completes_and_releases_its_own_key(magasin: Magasin) -> None:
    store = magasin()
    await store.reserve("a", 60, SCOPE, holder="moi")
    await store.complete("a", "fait", holder="moi")
    record = await store.get("a")
    assert record is not None and (record.status, record.result) == ("completed", "fait")

    await store.reserve("b", 60, SCOPE, holder="moi")
    await store.release("b", holder="moi")
    assert await store.get("b") is None


async def test_a_stale_holder_that_nobody_replaced_still_completes(magasin: Magasin) -> None:
    """Le jeton compte, pas la date : le résultat de l'effet est mieux gardé que perdu."""
    store = magasin()
    await store.reserve("k", -1, SCOPE, holder="premier")
    await store.complete("k", "fait", holder="premier")
    record = await store.get("k")
    assert record is not None and (record.status, record.result) == ("completed", "fait")


async def test_without_a_token_nothing_is_checked(magasin: Magasin) -> None:
    """Comportement d'avant le jeton : un appelant qui n'en présente pas n'est pas borné."""
    store = magasin()
    await store.reserve("a", 60, SCOPE, holder="quelqu-un")
    await store.complete("a", "fait")
    record = await store.get("a")
    assert record is not None and record.status == "completed"

    await store.reserve("b", 60, SCOPE, holder="quelqu-un")
    await store.release("b")
    assert await store.get("b") is None

    # Et une clé prise sans jeton ne répond à aucun jeton.
    await store.reserve("c", 60, SCOPE)
    await store.complete("c", "autre", holder="un-jeton")
    record = await store.get("c")
    assert record is not None and record.status == "in_progress"
    await store.release("c", holder="un-jeton")
    assert await store.get("c") is not None


async def test_a_key_that_is_gone_still_raises_with_a_token(magasin: Magasin) -> None:
    store = magasin()
    with pytest.raises(KeyError, match="non réservée"):
        await store.complete("absente", "fait", holder="moi")


@pytest.mark.skipif(find_spec("aiosqlite") is None, reason="extra 'sqlite' absent")
async def test_sqlite_closes_a_connection_it_could_not_set_up(tmp_path: Path) -> None:
    """Une connexion qu'il n'a pas pu mettre en place, le magasin la ferme.

    Sinon son fil survit, et retient le process à la sortie : c'est le blocage
    vu le 22/09, quand un autre process tenait la base à l'ouverture. Ici la
    mise en place échoue parce que le fichier n'est pas une base — même
    chemin, sans attendre l'échéance d'un verrou.
    """
    import sqlite3

    path = tmp_path / "idempotence.db"
    path.write_bytes(b"pas une base SQLite. " * 64)
    store = _sqlite(path)
    avant = set(threading.enumerate())
    with pytest.raises(sqlite3.DatabaseError) as echec:
        await store.get("cle")
    nouveaux = [fil for fil in threading.enumerate() if fil not in avant]
    for fil in nouveaux:
        fil.join(timeout=2)
    assert not [fil.name for fil in nouveaux if fil.is_alive()], echec.value
    # Le magasin n'en reste pas bloqué : la base remise en état, il s'ouvre.
    path.unlink()
    assert await store.get("cle") is None
    await _referme(store)


def _octets(path: Path) -> bytes:
    """Octets d'un fichier, vides s'il n'existe pas (le ``-wal`` d'une base fermée)."""
    return path.read_bytes() if path.exists() else b""


@pytest.mark.skipif(find_spec("aiosqlite") is None, reason="extra 'sqlite' absent")
@pytest.mark.parametrize("where", ["base", "wal"])
async def test_sqlite_forget_purges_the_database_and_the_wal(tmp_path: Path, where: str) -> None:
    """Après ``forget``, la chaîne oubliée n'est plus dans la base ni dans le ``-wal`` (RGPD).

    ``base`` : le résultat a été reporté dans le fichier de la base (la fermeture
    fait ce point de reprise). ``wal`` : il est encore dans le ``-wal``, qui garde
    aussi l'ancienne version des pages. Avant : la chaîne restait dans l'un ou l'autre.
    """
    secret = "JEAN-DUPONT-0612345678"
    path = tmp_path / "idempotence.db"
    wal = Path(f"{path}-wal")
    store = _sqlite(path)
    try:
        assert await store.reserve("cle", 60, SCOPE, holder="moi") is True
        await store.complete("cle", {"mail": secret}, holder="moi")
        if where == "base":
            await _referme(store)
            store = _sqlite(path)
        assert secret.encode() in _octets(path if where == "base" else wal)

        assert await store.forget(DEFAULT_TENANT, SESSION) == 1

        assert secret.encode() not in _octets(path)
        assert secret.encode() not in _octets(wal)
        assert await store.get("cle") is None
    finally:
        await _referme(store)


@pytest.mark.skipif(find_spec("aiosqlite") is None, reason="extra 'sqlite' absent")
async def test_sqlite_forget_still_succeeds_when_a_reader_keeps_the_wal(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Un lecteur d'un autre process bloque le point de reprise : ``forget`` réussit et prévient."""
    import logging
    import sqlite3

    from loom_ia.adapters.idempotency.sqlite import SqliteIdempotency

    store = SqliteIdempotency(tmp_path / "idempotence.db")
    await store.reserve("cle", 60, SCOPE)
    reader = sqlite3.connect(tmp_path / "idempotence.db", isolation_level=None)
    try:
        reader.execute("BEGIN")
        reader.execute("SELECT count(*) FROM idempotency").fetchone()  # un instantané reste ouvert
        connection = await store._connect()  # pyright: ignore[reportPrivateUsage]
        await connection.execute("PRAGMA busy_timeout = 50")  # l'essai n'attend pas les 5 s d'usage
        with caplog.at_level(logging.WARNING):
            assert await store.forget(DEFAULT_TENANT, SESSION) == 1
        assert await store.get("cle") is None
    finally:
        reader.close()
        await _referme(store)
    assert "le WAL n'est pas purgé" in caplog.text


# Le schéma que créait la 2.0.0, avant la colonne ``holder``.
SCHEMA_2_0_0 = """
CREATE TABLE idempotency (
    key        TEXT NOT NULL PRIMARY KEY,
    tenant_id  TEXT NOT NULL,
    session_id TEXT NOT NULL,
    status     TEXT NOT NULL,
    result     TEXT,
    expires_at TEXT NOT NULL
);
CREATE INDEX idempotency_owner ON idempotency (tenant_id, session_id);
CREATE INDEX idempotency_expiry ON idempotency (status, expires_at);
"""


@pytest.mark.skipif(find_spec("aiosqlite") is None, reason="extra 'sqlite' absent")
async def test_sqlite_opens_a_database_made_before_the_holder_column(tmp_path: Path) -> None:
    """Une base de la 2.0.0 s'ouvre, garde ses lignes, et gagne la colonne sans les perdre."""
    import sqlite3

    path = tmp_path / "idempotence.db"
    ancienne = sqlite3.connect(path)
    ancienne.executescript(SCHEMA_2_0_0)
    demain, hier = "2999-01-01T00:00:00+00:00", "2000-01-01T00:00:00+00:00"
    ancienne.executemany(
        "INSERT INTO idempotency VALUES (?, 'default', ?, ?, ?, ?)",
        [
            ("fait", SESSION, "completed", '"relance"', demain),
            ("tenue", SESSION, "in_progress", None, demain),
            ("perimee", SESSION, "in_progress", None, hier),
        ],
    )
    ancienne.commit()
    ancienne.close()

    # Deux ouvertures simultanées : l'une ajoute la colonne, l'autre la trouve ou s'y heurte.
    store, voisin = _sqlite(path), _sqlite(path)
    lus = await asyncio.gather(store.get("fait"), voisin.get("fait"))
    assert [(r.status, r.result) for r in lus if r is not None] == [("completed", "relance")] * 2
    held = await store.get("tenue")
    assert held is not None and held.status == "in_progress"

    # Une ligne d'avant n'a pas de jeton : aucun jeton ne la touche, l'absence de jeton si.
    await store.complete("tenue", "pas à moi", holder="un-jeton")
    await store.release("tenue", holder="un-jeton")
    held = await store.get("tenue")
    assert held is not None and held.status == "in_progress"
    await store.complete("tenue", "fini")
    held = await store.get("tenue")
    assert held is not None and (held.status, held.result) == ("completed", "fini")

    # Reprendre une réservation périmée d'avant avec un jeton marche, jusqu'au résultat.
    assert await store.reserve("perimee", 60, SCOPE, holder="moi") is True
    await store.complete("perimee", "refait", holder="moi")
    held = await store.get("perimee")
    assert held is not None and (held.status, held.result) == ("completed", "refait")
    await _referme(store)
    await _referme(voisin)

    # Rouverte, la base est telle qu'on l'a laissée.
    relu = _sqlite(path)
    held = await relu.get("perimee")
    assert held is not None and held.result == "refait"
    await _referme(relu)


class _FauxRedis:
    """Client qui note les scripts qu'on lui envoie ; ``reponse`` est ce que dirait Redis."""

    def __init__(self) -> None:
        self.reponse = 1
        self.evals: list[tuple[str, tuple[Any, ...]]] = []

    async def eval(self, script: str, numkeys: int, *args: Any) -> int:
        self.evals.append((script, (numkeys, *args)))
        return self.reponse

    async def aclose(self) -> None:
        return None


@pytest.mark.skipif(find_spec("redis") is None, reason="extra 'redis' absent")
async def test_the_redis_store_hands_the_holder_to_its_scripts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ce que cet essai ne peut pas montrer sans service : le Lua lui-même, qui n'est pas exécuté.

    Il vérifie le côté Python : le jeton est écrit dans l'enregistrement, et
    n'est passé aux scripts de ``complete`` et ``release`` que s'il y en a un.
    """
    import json

    import redis.asyncio as redis_asyncio

    faux = _FauxRedis()

    def from_url(url: str) -> _FauxRedis:
        return faux

    monkeypatch.setattr(redis_asyncio, "from_url", from_url)
    store = _redis("redis://essai")

    await store.reserve("k", 60, SCOPE, holder="moi")
    script, args = faux.evals[-1]
    assert json.loads(args[3])["holder"] == "moi"
    # La session de la clé voyage avec elle : ``complete`` ne la connaît pas.
    assert json.loads(args[3])["owner"] == args[2]

    await store.complete("k", "fait", holder="moi")
    script, args = faux.evals[-1]
    assert "held.holder ~= ARGV[3]" in script
    assert (args[0], args[1], args[4]) == (1, "loom:idem:k:k", "moi") and len(args) == 5
    assert json.loads(args[2])["holder"] == "moi"
    await store.complete("k", "fait")
    assert len(faux.evals[-1][1]) == 4

    await store.release("k", holder="moi")
    script, args = faux.evals[-1]
    assert "held.holder ~= ARGV[1]" in script
    assert args == (1, "loom:idem:k:k", "moi")
    await store.release("k")
    assert faux.evals[-1][1] == (1, "loom:idem:k:k")

    # 2 : la clé est à un autre détenteur, sans effet ; 0 : elle n'existe plus.
    faux.reponse = 2
    await store.complete("k", "fait", holder="périmé")
    faux.reponse = 0
    with pytest.raises(KeyError, match="non réservée"):
        await store.complete("k", "fait", holder="périmé")
    await _referme(store)


# --- Redis : l'ensemble d'appartenance vit autant que sa plus longue clé -------


@pytest.fixture
async def redis_client(redis_url: str) -> AsyncGenerator[Any]:
    """Un client brut sur le même Redis, pour lire ce que le magasin y laisse."""
    import redis.asyncio as redis_asyncio

    client = redis_asyncio.from_url(redis_url)
    yield client
    await client.aclose()


async def _until_gone(client: Any, *keys: str) -> None:
    """Attend que Redis ait lui-même retiré ces clés : son horloge, pas un sommeil deviné."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 5
    while loop.time() < deadline:
        if not await client.exists(*keys):
            return
        await asyncio.sleep(0.02)
    pytest.fail(f"Redis n'a pas retiré {keys} en cinq secondes")


@pytest.mark.integration
async def test_a_shorter_redis_key_does_not_shorten_the_set_of_a_longer_one(
    redis_url: str, redis_client: Any
) -> None:
    """Réserver une clé courte après une longue laissait l'ensemble expirer avec la courte."""
    store = _redis(redis_url, retention=0.2)
    await store.reserve("longue", 60, SCOPE)
    await store.reserve("courte", 0.1, SCOPE)
    await _until_gone(redis_client, "loom:idem:k:courte")

    assert await store.forget(DEFAULT_TENANT, SESSION) == 1
    assert await store.get("longue") is None
    await _referme(store)


@pytest.mark.integration
async def test_a_redis_result_that_outlives_the_set_extends_it(
    redis_url: str, redis_client: Any
) -> None:
    """``complete`` rallongeait l'enregistrement sans rallonger l'ensemble qui le nomme."""
    store = _redis(redis_url, retention=0.2)
    await store.reserve("k", 0.1, SCOPE)
    await store.complete("k", "fait", 60)
    # Dans cet ordre : lu avant, l'ensemble ne peut pas sembler vivre moins que la clé lue après.
    owner_ttl = await redis_client.pttl(f"loom:idem:o:{DEFAULT_TENANT}:{SESSION}")
    assert owner_ttl >= await redis_client.pttl("loom:idem:k:k") > 50_000

    # Le temps qu'avait l'ensemble avant le correctif s'écoule, sur l'horloge de Redis.
    await redis_client.set("sonde", 1, px=250)
    await _until_gone(redis_client, "sonde")
    assert await store.forget(DEFAULT_TENANT, SESSION) == 1
    assert await store.get("k") is None
    await _referme(store)


@pytest.mark.integration
async def test_a_completed_redis_record_keeps_its_result_and_its_owner(
    redis_url: str, redis_client: Any
) -> None:
    """Le script colle le nom de l'ensemble au JSON sans le recoder : ``[]`` reste ``[]``."""
    import json

    store = _redis(redis_url)
    result: dict[str, JsonValue] = {"liste": [], "objet": {}, "texte": 'é"/', "nombre": 0.1}
    await store.reserve("k", 60, SCOPE)
    await store.complete("k", result)
    record = await store.get("k")
    assert record is not None and record.result == result
    held = json.loads(await redis_client.get("loom:idem:k:k"))
    assert held["owner"] == f"loom:idem:o:{DEFAULT_TENANT}:{SESSION}"

    # Enregistrer une seconde fois, plus longtemps, rallonge encore l'ensemble.
    await store.complete("k", result, 3 * 86_400)
    owner_ttl = await redis_client.pttl(held["owner"])
    assert owner_ttl >= await redis_client.pttl("loom:idem:k:k") > 2 * 86_400_000
    await _referme(store)


@pytest.mark.integration
async def test_a_redis_reservation_from_before_the_owner_still_completes(
    redis_url: str, redis_client: Any
) -> None:
    """Un enregistrement écrit avant ``owner`` n'a pas d'ensemble à rallonger, et se complète."""
    import json

    legacy = {"status": "in_progress", "result": None, "expires_at": 4e9, "holder": None}
    await redis_client.set("loom:idem:k:vieux", json.dumps(legacy), px=60_000)
    store = _redis(redis_url)
    await store.complete("vieux", "fait")
    record = await store.get("vieux")
    assert record is not None and (record.status, record.result) == ("completed", "fait")
    assert "owner" not in json.loads(await redis_client.get("loom:idem:k:vieux"))
    await _referme(store)


# --- Magasin journal ----------------------------------------------------------


async def _journal() -> tuple[JournalIdempotency, SessionWriter]:
    store = InMemoryEventStore()
    journal = RunJournal(agent="demo", session_id=SESSION)
    journal.start("?")
    writer = SessionWriter(store, DEFAULT_TENANT, SESSION, 0)
    await writer.append(journal.take())
    return JournalIdempotency(writer, journal.scope, call_id="c1", tool_name="envoyer"), writer


async def test_the_journal_store_knows_nothing_before_the_effect() -> None:
    store, _ = await _journal()
    assert await store.get("k") is None
    # Le ``tool.called`` tient lieu de réservation : il n'y a rien à prendre.
    assert await store.reserve("k", 60, SCOPE) is True


async def test_the_journal_store_writes_the_effect_and_reads_it_back() -> None:
    store, writer = await _journal()
    await store.complete("k", {"envoye": True})
    record = await store.get("k")
    assert record is not None
    assert (record.status, record.result) == ("completed", {"envoye": True})

    events = await writer.store.read(DEFAULT_TENANT, SESSION)
    written = [e for e in events if isinstance(e.payload, IdempotencyRecorded)]
    assert len(written) == 1
    payload = written[0].payload
    assert isinstance(payload, IdempotencyRecorded)
    assert (payload.key, payload.call_id, payload.tool_name) == ("k", "c1", "envoyer")
    # Sa catégorie lui est propre : il n'est la cause d'aucune transition.
    assert written[0].category == "idempotency"


async def test_the_journal_store_ignores_another_key() -> None:
    store, _ = await _journal()
    await store.complete("k", "fait")
    assert await store.get("autre") is None


async def test_the_journal_store_ignores_a_completion_from_another_holder() -> None:
    store, writer = await _journal()
    await store.reserve("k", 60, SCOPE, holder="moi")
    await store.complete("k", "du voisin", holder="un-autre")
    assert await store.get("k") is None
    await store.complete("k", "fait", holder="moi")
    record = await store.get("k")
    assert record is not None and record.result == "fait"
    events = await writer.store.read(DEFAULT_TENANT, SESSION)
    assert len([e for e in events if isinstance(e.payload, IdempotencyRecorded)]) == 1


# --- Décorateur ---------------------------------------------------------------


type Envoyeur = IdempotentTool[[], CoroutineType[Any, Any, str]]


def _compteur() -> tuple[list[str], Envoyeur]:
    envois: list[str] = []

    @idempotent
    @tool(side_effects="irreversible")
    async def envoyer() -> str:
        """Envoie la relance."""
        envois.append("envoi")
        return f"relance {len(envois)}"

    return envois, envoyer


def test_decorating_declares_the_tool_idempotent() -> None:
    _, outil = _compteur()
    assert outil.spec.idempotent is True
    assert outil.spec.safe_to_retry is True
    assert outil.spec.side_effects == "irreversible"


def test_the_reservation_follows_the_tool_timeout() -> None:
    @idempotent
    @tool(timeout=12)
    async def lent() -> str:
        """Lent."""
        return "ok"

    @idempotent(reservation=5)
    @tool
    async def court() -> str:
        """Court."""
        return "ok"

    @idempotent
    @tool
    async def simple() -> str:
        """Simple."""
        return "ok"

    assert (lent.held_for, court.held_for, simple.held_for) == (12, 5, DEFAULT_RESERVATION)


async def test_without_a_store_the_tool_runs_plainly() -> None:
    envois, outil = _compteur()
    output = await outil.invoke({}, context())
    assert output.as_text == "relance 1"
    assert envois == ["envoi"]


async def test_a_replayed_call_gives_the_memorised_result_without_the_effect() -> None:
    envois, outil = _compteur()
    store = InMemoryIdempotency()
    first = await outil.invoke({}, context(store=store))
    second = await outil.invoke({}, context(store=store))
    assert (first.as_text, second.as_text) == ("relance 1", "relance 1")
    assert envois == ["envoi"]


async def test_two_distinct_calls_are_two_effects() -> None:
    """La clé technique protège la reprise d'un appel, pas le doublon métier."""
    envois, outil = _compteur()
    store = InMemoryIdempotency()
    await outil.invoke({}, context(call_id="c1", store=store))
    await outil.invoke({}, context(call_id="c2", store=store))
    assert envois == ["envoi", "envoi"]


async def test_a_call_already_running_is_not_started_a_second_time() -> None:
    envois, outil = _compteur()
    store = InMemoryIdempotency()
    await store.reserve(context().idempotency_key, 60, SCOPE)
    with pytest.raises(ToolError, match="déjà en cours"):
        await outil.invoke({}, context(store=store))
    assert envois == []
    assert BUSY.startswith("Cet appel")


async def test_an_expired_reservation_leaves_the_effect_unknown() -> None:
    envois, outil = _compteur()
    store = InMemoryIdempotency()
    await store.reserve(context().idempotency_key, -1, SCOPE)
    with pytest.raises(ToolError, match="État inconnu"):
        await outil.invoke({}, context(store=store))
    assert envois == []
    assert UNKNOWN_EFFECT.startswith("État inconnu")


async def test_retry_unknown_takes_the_reservation_back() -> None:
    envois: list[str] = []

    @idempotent(retry_unknown=True)
    @tool(side_effects="irreversible")
    async def envoyer() -> str:
        """Envoie la relance."""
        envois.append("envoi")
        return "relance"

    store = InMemoryIdempotency()
    await store.reserve(context().idempotency_key, -1, SCOPE)
    output = await envoyer.invoke({}, context(store=store))
    assert output.as_text == "relance"
    assert envois == ["envoi"]


async def test_a_tool_that_fails_gives_its_key_back() -> None:
    essais: list[str] = []

    @idempotent
    @tool(side_effects="irreversible")
    async def fragile() -> str:
        """Échoue une fois."""
        essais.append("essai")
        if len(essais) == 1:
            raise ToolError("pas aujourd'hui")
        return "fait"

    store = InMemoryIdempotency()
    ctx = context(store=store)
    with pytest.raises(ToolError, match="pas aujourd'hui"):
        await fragile.invoke({}, ctx)
    assert await store.get(ctx.idempotency_key) is None
    assert (await fragile.invoke({}, ctx)).as_text == "fait"


@pytest.mark.parametrize("error", [RuntimeError("panne"), TimeoutError("connexion")])
async def test_an_error_raised_by_the_tool_gives_its_key_back(
    magasin: Magasin, error: Exception
) -> None:
    """Une exception levée par l'outil rend la clé, un ``TimeoutError`` comme une autre."""

    @idempotent
    @tool(side_effects="irreversible")
    async def fragile() -> str:
        """Échoue avant tout effet."""
        raise error

    store = magasin()
    ctx = context(store=store)
    with pytest.raises(type(error)):
        await fragile.invoke({}, ctx)
    assert await store.get(ctx.idempotency_key) is None


@pytest.mark.parametrize(
    ("reservation", "reply"),
    [(60, "déjà en cours"), (-1, "État inconnu")],
    ids=["tenue", "perimee"],
)
async def test_a_deadline_from_outside_keeps_the_key_and_the_effect_is_not_redone(
    magasin: Magasin, reservation: float, reply: str
) -> None:
    """Délai venu de l'extérieur : on ignore où l'effet en était, il n'est pas refait."""
    effets: list[str] = []
    entre = asyncio.Event()

    @idempotent(reservation=reservation)
    @tool(side_effects="irreversible")
    async def relancer() -> str:
        """Envoie la relance ; l'API distante ne répond jamais."""
        effets.append("envoi")
        entre.set()
        await asyncio.Event().wait()
        return "envoyée"

    store = magasin()
    ctx = context(store=store)

    async def expire_apres_l_effet(scope: asyncio.Timeout) -> None:
        await entre.wait()
        scope.reschedule(asyncio.get_running_loop().time())

    # Le délai de l'exécuteur est un ``asyncio.timeout`` : l'outil reçoit une annulation.
    with pytest.raises(TimeoutError):
        async with asyncio.timeout(None) as scope:
            guet = asyncio.create_task(expire_apres_l_effet(scope))
            try:
                await relancer.invoke({}, ctx)
            finally:
                guet.cancel()
    record = await store.get(ctx.idempotency_key)
    assert record is not None and record.status == "in_progress"

    with pytest.raises(ToolError, match=reply):
        await relancer.invoke({}, ctx)
    assert effets == ["envoi"]


async def test_the_executor_deadline_keeps_the_key_too() -> None:
    """Le cas rapporté : « Délai dépassé » au modèle, puis la relance rend l'état inconnu."""
    effets: list[str] = []

    @idempotent(key=lambda a: f"relance:{a['devis']}")
    @tool(side_effects="irreversible", timeout=0.05)
    async def relancer(devis: str) -> str:
        """Envoie la relance ; l'API distante ne répond jamais."""
        effets.append(devis)
        await asyncio.Event().wait()
        return "envoyée"

    executor = ToolExecutor([relancer], idempotency=InMemoryIdempotency())  # pyright: ignore[reportArgumentType]
    first = await _run(executor, awaiting(call("c1", "relancer", devis="D-1")))
    assert "Délai dépassé" in completed(first)["c1"].as_text
    again = await _run(executor, awaiting(call("c2", "relancer", devis="D-1")))
    # La réservation suit le délai de l'outil : elle est périmée quand le modèle relance.
    assert "État inconnu" in completed(again)["c2"].as_text
    assert effets == ["D-1"]


async def test_a_cancelled_call_keeps_its_key(magasin: Magasin) -> None:
    """Annulation (arrêt du run, du process) : même règle que le délai, la clé reste prise."""
    effets: list[str] = []
    entre = asyncio.Event()

    @idempotent(reservation=60)
    @tool(side_effects="irreversible")
    async def envoyer() -> str:
        """Envoie, puis attend une réponse qui n'arrive pas."""
        effets.append("envoi")
        entre.set()
        await asyncio.Event().wait()
        return "envoyée"

    store = magasin()
    ctx = context(store=store)
    task = asyncio.create_task(envoyer.invoke({}, ctx))
    await entre.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    record = await store.get(ctx.idempotency_key)
    assert record is not None and record.status == "in_progress"
    with pytest.raises(ToolError, match="déjà en cours"):
        await envoyer.invoke({}, ctx)
    assert effets == ["envoi"]


async def test_the_memorised_result_keeps_its_data() -> None:
    @idempotent
    @tool
    async def chiffres() -> dict[str, int]:
        """Rend des chiffres."""
        return {"devis": 3}

    store = InMemoryIdempotency()
    ctx = context(store=store)
    first = await chiffres.invoke({}, ctx)
    second = await chiffres.invoke({}, ctx)
    assert second == first
    assert second.data == {"devis": 3}


# --- Un effet produit est rapporté comme produit ---------------------------------


class _Panne(InMemoryIdempotency):
    """Magasin qui accepte les clés, puis tombe en panne au moment d'enregistrer l'effet."""

    async def complete(
        self, key: str, result: object, ttl: float | None = None, *, holder: str | None = None
    ) -> None:
        raise ConnectionError("magasin injoignable")


async def test_a_store_failure_after_the_effect_does_not_turn_it_into_a_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """L'effet a eu lieu : l'appel rend son résultat, et le dit au journal applicatif."""
    envois, outil = _compteur()
    store = _Panne()
    ctx = context(store=store)
    with caplog.at_level("ERROR", logger="loom_ia.tools.idempotent"):
        output = await outil.invoke({}, ctx)
    assert (output.as_text, output.is_error) == ("relance 1", False)
    assert "non mémorisé" in caplog.text
    # La clé reste prise, sans résultat : le prochain appel sera d'état inconnu.
    record = await store.get(ctx.idempotency_key)
    assert record is not None and record.status == "in_progress"
    assert envois == ["envoi"]


async def test_the_model_is_not_told_of_a_failure_when_only_the_recording_failed() -> None:
    """Sinon il relance, sous un autre appel donc une autre clé, et l'effet est refait."""
    envois, outil = _compteur()
    executor = ToolExecutor([outil], idempotency=_Panne())  # pyright: ignore[reportArgumentType]
    events = await _run(executor, awaiting(call("c1", "envoyer")))
    assert completed(events)["c1"].is_error is False
    assert completed(events)["c1"].as_text == "relance 1"
    assert envois == ["envoi"]


async def test_an_oversized_result_is_given_back_and_recorded_in_a_reduced_form(
    magasin: Magasin, caplog: pytest.LogCaptureFixture
) -> None:
    envois: list[str] = []

    @idempotent
    @tool(side_effects="irreversible")
    async def exporter() -> ToolOutput:
        """Exporte tout."""
        envois.append("export")
        return ToolOutput.text("x" * (MAX_RECORDED + 1)).model_copy(
            update={"artifacts": ("loom://rapport",)}
        )

    store = magasin()
    ctx = context(store=store)
    with caplog.at_level("WARNING", logger="loom_ia.tools.idempotent"):
        first = await exporter.invoke({}, ctx)
    # L'appel rend le vrai résultat, en entier.
    assert (len(first.as_text), first.is_error) == (MAX_RECORDED + 1, False)
    assert "trop gros" in caplog.text

    record = await store.get(ctx.idempotency_key)
    assert record is not None and record.status == "completed"
    assert len(str(record.result)) < MAX_RECORDED
    # Un rejeu dit que l'effet a eu lieu, sans le refaire ni crier à la panne.
    again = await exporter.invoke({}, ctx)
    assert again.as_text.startswith("Cet appel a déjà produit son effet")
    assert "n'est pas relancé" in again.as_text
    assert (again.is_error, again.artifacts) == (False, ("loom://rapport",))
    assert envois == ["export"]


async def test_the_journal_store_records_a_reduced_form_of_an_oversized_result() -> None:
    store, writer = await _journal()
    envois: list[str] = []

    @idempotent
    @tool(side_effects="irreversible")
    async def exporter() -> str:
        """Exporte tout."""
        envois.append("export")
        return "x" * (MAX_RECORDED + 1)

    ctx = context(store=store)
    first = await exporter.invoke({}, ctx)
    assert len(first.as_text) == MAX_RECORDED + 1
    again = await exporter.invoke({}, ctx)
    assert again.as_text.startswith("Cet appel a déjà produit son effet")
    assert envois == ["export"]
    events = await writer.store.read(DEFAULT_TENANT, SESSION)
    assert len([e for e in events if isinstance(e.payload, IdempotencyRecorded)]) == 1


# --- Jeton de détenteur, côté outil -------------------------------------------


@pytest.mark.parametrize("premier", ["echoue", "reussit"])
async def test_a_stale_holder_leaves_the_key_of_its_successor_alone(
    magasin: Magasin, premier: str
) -> None:
    """Réservation expirée puis reprise : le premier appel, resté en vie, ne la défait pas."""
    arrivee = [asyncio.Event(), asyncio.Event()]
    depart = [asyncio.Event(), asyncio.Event()]
    appels: list[int] = []

    @idempotent(reservation=-1, retry_unknown=True)
    @tool(side_effects="irreversible")
    async def envoyer() -> str:
        """Envoie."""
        moi = len(appels)
        appels.append(moi)
        arrivee[moi].set()
        await depart[moi].wait()
        if moi == 0 and premier == "echoue":
            raise ToolError("tombé")
        return f"envoi {moi + 1}"

    store = magasin()
    ctx = context(store=store)
    un = asyncio.create_task(envoyer.invoke({}, ctx))
    await arrivee[0].wait()
    # Sa réservation est périmée : le second la reprend, et refait l'effet (retry_unknown).
    deux = asyncio.create_task(envoyer.invoke({}, ctx))
    await arrivee[1].wait()

    depart[0].set()
    if premier == "echoue":
        with pytest.raises(ToolError, match="tombé"):
            await un
    else:
        assert (await un).as_text == "envoi 1"
    # La clé est toujours au second : ni rendue, ni passée au résultat du premier.
    record = await store.get(ctx.idempotency_key)
    assert record is not None and (record.status, record.result) == ("in_progress", None)

    depart[1].set()
    assert (await deux).as_text == "envoi 2"
    record = await store.get(ctx.idempotency_key)
    assert record is not None and record.status == "completed"
    assert ToolOutput.model_validate(record.result).as_text == "envoi 2"


class _Ancien:
    """Magasin écrit avant le jeton : ses méthodes n'ont pas de paramètre ``holder``."""

    def __init__(self) -> None:
        self._dedans = InMemoryIdempotency()

    async def get(self, key: str) -> Any:
        return await self._dedans.get(key)

    async def reserve(self, key: str, ttl: float, scope: KeyScope) -> bool:
        return await self._dedans.reserve(key, ttl, scope)

    async def complete(self, key: str, result: object, ttl: float | None = None) -> None:
        await self._dedans.complete(key, result, ttl)

    async def release(self, key: str) -> None:
        await self._dedans.release(key)

    async def forget(self, tenant_id: TenantId, session_id: SessionId | None = None) -> int:
        return await self._dedans.forget(tenant_id, session_id)


async def test_a_store_written_before_the_token_is_called_as_before() -> None:
    envois, outil = _compteur()
    store = _Ancien()
    ctx = context(store=store)
    assert (await outil.invoke({}, ctx)).as_text == "relance 1"
    assert (await outil.invoke({}, ctx)).as_text == "relance 1"
    assert envois == ["envoi"]

    @idempotent
    @tool(side_effects="irreversible")
    async def fragile() -> str:
        """Échoue avant tout effet."""
        raise ToolError("pas aujourd'hui")

    autre = context(call_id="c2", store=store)
    with pytest.raises(ToolError, match="pas aujourd'hui"):
        await fragile.invoke({}, autre)
    assert await store.get(autre.idempotency_key) is None


# --- Dans le moteur -----------------------------------------------------------


async def _run(executor: ToolExecutor, state: RunState, **options: object) -> list[ToolEvent]:
    return [event async for event in executor.run_batch(state, **options)]  # pyright: ignore[reportArgumentType]


async def test_the_engine_hands_the_journal_store_to_the_tool() -> None:
    store = InMemoryEventStore()
    journal = RunJournal(agent="demo", session_id=SESSION)
    journal.start("?")
    writer = SessionWriter(store, DEFAULT_TENANT, SESSION, 0)
    await writer.append(journal.take())
    vus: list[object] = []

    @tool
    async def regarder(ctx: ToolContext) -> str:
        """Regarde son magasin."""
        vus.append(ctx.idempotency)
        return "ok"

    state = awaiting(call("c1", "regarder"))
    await _run(ToolExecutor([regarder]), state, writer=writer, scope=journal.scope)
    assert len(vus) == 1
    assert isinstance(vus[0], JournalIdempotency)


async def test_the_record_sits_under_the_span_of_its_call() -> None:
    """Un enregistrement se lit avec l'appel qui l'a produit, pas à part."""
    store = InMemoryEventStore()
    journal = RunJournal(agent="demo", session_id=SESSION)
    journal.start("?")
    writer = SessionWriter(store, DEFAULT_TENANT, SESSION, 0)
    await writer.append(journal.take())
    _, outil = _compteur()
    appel, etape = new_span_id(), new_span_id()

    state = awaiting(call("c1", "envoyer"))
    state = state.model_copy(update={"run_id": journal.run_id, "session_id": SESSION})
    await _run(
        ToolExecutor([outil]),  # pyright: ignore[reportArgumentType]
        state,
        writer=writer,
        scope=journal.scope,
        spans={"c1": appel},
        step_span=etape,
    )
    events = await store.read(DEFAULT_TENANT, SESSION)
    [recorded] = [e for e in events if isinstance(e.payload, IdempotencyRecorded)]
    assert (recorded.span_id, recorded.parent_span_id) == (appel, etape)


async def test_a_shared_store_is_handed_to_the_tool_instead() -> None:
    partage = InMemoryIdempotency()
    vus: list[object] = []

    @tool
    async def regarder(ctx: ToolContext) -> str:
        """Regarde son magasin."""
        vus.append(ctx.idempotency)
        return "ok"

    executor = ToolExecutor([regarder], idempotency=partage)
    await _run(executor, awaiting(call("c1", "regarder")))
    assert vus == [partage]


async def test_an_idempotent_tool_is_replayed_and_its_effect_happens_once() -> None:
    store = InMemoryEventStore()
    journal = RunJournal(agent="demo", session_id=SESSION)
    journal.start("?")
    writer = SessionWriter(store, DEFAULT_TENANT, SESSION, 0)
    await writer.append(journal.take())
    envois, outil = _compteur()
    executor = ToolExecutor([outil])  # pyright: ignore[reportArgumentType]

    state = awaiting(call("c1", "envoyer"))
    state = state.model_copy(update={"run_id": journal.run_id, "session_id": SESSION})
    first = await _run(executor, state, writer=writer, scope=journal.scope)
    assert completed(first)["c1"].as_text == "relance 1"

    # Le run est repris : l'appel était lancé, il repart — et ne renvoie rien.
    resumed = state.model_copy(update={"pending_calls": (call("c1", "envoyer", started=True),)})
    second = await _run(executor, resumed, writer=writer, scope=journal.scope)
    assert completed(second)["c1"].as_text == "relance 1"
    assert envois == ["envoi"]
    assert [e.resumed for e in second if isinstance(e, ToolCalled)] == [True]


# --- Règle de reprise (#18) ---------------------------------------------------


async def test_a_resumed_call_of_unknown_effect_tells_the_model() -> None:
    envois: list[str] = []

    @tool(side_effects="irreversible")
    async def envoyer() -> str:
        """Envoie."""
        envois.append("envoi")
        return "fait"

    events = await _run(ToolExecutor([envoyer]), awaiting(call("c1", "envoyer", started=True)))
    assert completed(events)["c1"] == ToolOutput.error(UNKNOWN_STATE)
    assert envois == []


async def test_a_resumed_call_can_ask_a_human_instead() -> None:
    envois: list[str] = []

    @tool(side_effects="irreversible", on_unknown="pause")
    async def envoyer() -> str:
        """Envoie."""
        envois.append("envoi")
        return "fait"

    events = await _run(ToolExecutor([envoyer]), awaiting(call("c1", "envoyer", started=True)))
    demandes = [e for e in events if isinstance(e, ApprovalRequested)]
    assert len(demandes) == 1
    assert demandes[0].reason == UNKNOWN_STATE
    assert not completed(events)
    assert envois == []


async def test_a_human_who_grants_lets_the_call_run() -> None:
    envois: list[str] = []
    vues: list[PendingApproval] = []

    @tool(side_effects="irreversible", on_unknown="pause")
    async def envoyer() -> str:
        """Envoie."""
        envois.append("envoi")
        return "fait"

    async def accorde(asked: PendingApproval) -> Approved:
        vues.append(asked)
        return Approved(by="denis")

    events = await _run(
        ToolExecutor([envoyer]),
        awaiting(call("c1", "envoyer", started=True)),
        approver=accorde,
    )
    assert [a.reason for a in vues] == [UNKNOWN_STATE]
    assert completed(events)["c1"].as_text == "fait"
    assert envois == ["envoi"]


async def test_a_human_who_refuses_stops_the_call() -> None:
    envois: list[str] = []

    @tool(side_effects="irreversible", on_unknown="pause")
    async def envoyer() -> str:
        """Envoie."""
        envois.append("envoi")
        return "fait"

    async def refuse(asked: PendingApproval) -> Rejected:
        return Rejected(reason="je vérifie d'abord", by="denis")

    events = await _run(
        ToolExecutor([envoyer], approval=ApprovalSettings()),
        awaiting(call("c1", "envoyer", started=True)),
        approver=refuse,
    )
    assert completed(events)["c1"].is_error
    assert "je vérifie d'abord" in completed(events)["c1"].as_text
    assert envois == []


async def test_a_tool_without_effects_is_replayed_without_asking() -> None:
    lectures: list[str] = []

    @tool(on_unknown="pause")
    async def lire() -> str:
        """Lit."""
        lectures.append("lecture")
        return "lu"

    events = await _run(ToolExecutor([lire]), awaiting(call("c1", "lire", started=True)))
    assert not [e for e in events if isinstance(e, ApprovalRequested)]
    assert completed(events)["c1"].as_text == "lu"
    assert lectures == ["lecture"]


class Mort(BaseException):
    """Le process meurt pendant l'outil : rien de ce qui suit ne s'exécute."""


async def test_a_transfer_approved_then_killed_is_not_made_twice() -> None:
    """De bout en bout : accord en ligne, effet produit, process tué, reprise (P0-5)."""
    virements: list[int] = []
    vivant = {"oui": True}

    @tool(side_effects="irreversible", approval="always")
    async def virer(montant: int) -> str:
        """Effectue un virement irréversible."""
        virements.append(montant)
        if vivant["oui"]:
            vivant["oui"] = False
            raise Mort
        return "virement effectué"

    async def accorde(asked: PendingApproval) -> Approved:
        return Approved(by="l'artisan")

    store = InMemoryEventStore()
    modele = ScriptedModel(
        tool_call_message(("c1", "virer", {"montant": 500})), Message.assistant("Vérifié.")
    )
    spec_modele = ModelSpec(
        id="FAKE",
        sdk="fake",
        model="fake-1",
        pricing=Pricing(input=1.0, output=5.0),
        retry=RetryPolicy(initial_delay=0),
    )
    ctx = RunContext(
        agent="banque",
        store=store,
        model=modele,
        model_spec=spec_modele,
        tools=ToolExecutor([virer]),
        system="Tu vires.",
        approver=accorde,
    )
    run = await begin_run(ctx, "Vire 500 euros.")
    with pytest.raises(Mort):
        await drive(ctx, run.run_id)

    final = await drive(ctx, run.run_id)

    assert final.status is RunStatus.COMPLETED
    assert virements == [500]
    journal = await store.read(DEFAULT_TENANT, final.session_id)
    assert [e.type for e in journal if e.type.startswith(("tool.", "approval."))] == [
        "approval.requested",
        "approval.granted",
        "tool.called",
        "tool.completed",
    ]
    # Le modèle est prévenu que l'effet est d'état inconnu, il ne croit pas à un succès.
    [resultat] = [b for m in final.messages for b in m.blocks if isinstance(b, ToolResultBlock)]
    assert resultat.output == ToolOutput.error(UNKNOWN_STATE)


# --- Deux appels en parallèle -------------------------------------------------


async def test_two_parallel_calls_of_the_same_key_leave_one_effect() -> None:
    """La concession l'interdit dans un run ; le magasin ne s'y fie pas."""
    envois: list[str] = []
    depart = asyncio.Event()

    @idempotent
    @tool(side_effects="irreversible")
    async def envoyer() -> str:
        """Envoie."""
        await depart.wait()
        envois.append("envoi")
        return "relance"

    store = InMemoryIdempotency()
    ctx = context(store=store)
    taches = [asyncio.create_task(envoyer.invoke({}, ctx)) for _ in range(2)]
    await asyncio.sleep(0)
    depart.set()
    resultats = await asyncio.gather(*taches, return_exceptions=True)
    erreurs = [r for r in resultats if isinstance(r, ToolError)]
    assert len(envois) == 1
    assert len(erreurs) == 1
    assert erreurs[0].message == BUSY


# --- Clé métier ---------------------------------------------------------------

type Relanceur = IdempotentTool[[str], CoroutineType[Any, Any, str]]


def _relance() -> tuple[list[str], Relanceur]:
    """Outil à clé métier : c'est le devis qui fait la clé, pas l'appel."""
    envois: list[str] = []

    @idempotent(key=lambda a: f"relance:{a['devis']}")
    @tool(side_effects="irreversible")
    async def envoyer(devis: str) -> str:
        """Envoie la relance du devis."""
        envois.append(devis)
        return f"relance {len(envois)}"

    return envois, envoyer


def test_a_business_key_is_prefixed_by_the_client_and_the_tool() -> None:
    _, outil = _relance()
    assert outil.spec.business_key is True
    assert outil.key_for({"devis": "D-42"}, context()) == "default:envoyer:relance:D-42"
    autre = replace(context(), tenant_id=TenantId("acme"))
    assert outil.key_for({"devis": "D-42"}, autre) == "acme:envoyer:relance:D-42"


def test_the_technical_key_declares_nothing(magasin: Magasin) -> None:
    _, outil = _compteur()
    assert outil.spec.business_key is False
    assert outil.key_for({}, context()) == context().idempotency_key


async def test_two_calls_that_ask_the_same_thing_are_one_effect(magasin: Magasin) -> None:
    """Ce que la clé technique ne sait pas faire : deux appels, un seul envoi."""
    envois, outil = _relance()
    store = magasin()
    arguments: dict[str, JsonValue] = {"devis": "D-2026-042"}
    first = await outil.invoke(arguments, context(call_id="c1", store=store))
    second = await outil.invoke(arguments, context(call_id="c2", store=store))
    assert (first.as_text, second.as_text) == ("relance 1", "relance 1")
    assert envois == ["D-2026-042"]


async def test_two_runs_that_ask_the_same_thing_are_one_effect(magasin: Magasin) -> None:
    """Et même depuis deux runs : c'est là tout l'intérêt d'un magasin partagé."""
    envois, outil = _relance()
    store = magasin()
    arguments: dict[str, JsonValue] = {"devis": "D-2026-042"}
    await outil.invoke(arguments, context(run_id="run-1", call_id="c1", store=store))
    await outil.invoke(arguments, context(run_id="run-2", call_id="c9", store=store))
    assert envois == ["D-2026-042"]


async def test_another_client_sends_its_own(magasin: Magasin) -> None:
    envois, outil = _relance()
    store = magasin()
    arguments: dict[str, JsonValue] = {"devis": "D-2026-042"}
    await outil.invoke(arguments, context(store=store))
    autre = replace(context(call_id="c2", store=store), tenant_id=TenantId("acme"))
    await outil.invoke(arguments, autre)
    assert envois == ["D-2026-042", "D-2026-042"]


async def test_another_quote_is_another_key(magasin: Magasin) -> None:
    envois, outil = _relance()
    store = magasin()
    await outil.invoke({"devis": "D-1"}, context(call_id="c1", store=store))
    await outil.invoke({"devis": "D-2"}, context(call_id="c2", store=store))
    assert envois == ["D-1", "D-2"]


async def test_a_tool_can_fix_how_long_its_result_is_kept(magasin: Magasin) -> None:
    envois: list[str] = []

    @idempotent(key=lambda a: f"relance:{a['devis']}", ttl=-1)
    @tool(side_effects="irreversible")
    async def envoyer(devis: str) -> str:
        """Envoie la relance du devis."""
        envois.append(devis)
        return "partie"

    store = magasin()
    arguments: dict[str, JsonValue] = {"devis": "D-1"}
    await envoyer.invoke(arguments, context(call_id="c1", store=store))
    # Le résultat est déjà hors de sa durée de vie : plus rien ne le protège.
    await envoyer.invoke(arguments, context(call_id="c2", store=store))
    assert envois == ["D-1", "D-1"]


async def test_two_tools_that_compute_the_same_business_key_do_not_block_each_other(
    magasin: Magasin,
) -> None:
    """Un courriel et un SMS de relance du même devis : deux effets, chacun avec son résultat."""
    envois: list[str] = []

    @idempotent(key=lambda a: f"relance:{a['devis']}")
    @tool(side_effects="irreversible")
    async def courriel(devis: str) -> str:
        """Envoie le courriel de relance."""
        envois.append("courriel")
        return "courriel envoyé"

    @idempotent(key=lambda a: f"relance:{a['devis']}")
    @tool(side_effects="irreversible")
    async def sms(devis: str) -> str:
        """Envoie le SMS de relance."""
        envois.append("sms")
        return "sms envoyé"

    store = magasin()
    arguments: dict[str, JsonValue] = {"devis": "D-1"}
    mail = await courriel.invoke(arguments, context(call_id="c1", store=store))
    texto = await sms.invoke(arguments, context(call_id="c2", store=store))
    assert (mail.as_text, texto.as_text) == ("courriel envoyé", "sms envoyé")
    # Et chacun reste dédoublonné pour lui-même.
    await courriel.invoke(arguments, context(call_id="c3", store=store))
    await sms.invoke(arguments, context(call_id="c4", store=store))
    assert envois == ["courriel", "sms"]


# Avant le préfixe par l'outil, la clé métier était « client:clé ». Les traces
# qui datent d'avant le déploiement doivent encore protéger leur effet.


async def test_a_trace_under_the_old_business_key_still_protects_its_effect(
    magasin: Magasin,
) -> None:
    envois, outil = _relance()
    store = magasin()
    await store.reserve("default:relance:D-1", 60, SCOPE)
    ancien = ToolOutput.text("relance d'avant").model_dump(mode="json")
    await store.complete("default:relance:D-1", ancien)
    vus: list[str] = []
    ctx = replace(context(store=store), on_reuse=vus.append)

    output = await outil.invoke({"devis": "D-1"}, ctx)
    assert output.as_text == "relance d'avant"
    assert envois == []
    # Le journal dit sous quelle clé l'effet a été retrouvé.
    assert vus == ["default:relance:D-1"]


async def test_a_reservation_under_the_old_business_key_is_respected(magasin: Magasin) -> None:
    envois, outil = _relance()
    store = magasin()
    await store.reserve("default:relance:D-1", 60, SCOPE)
    with pytest.raises(ToolError, match="déjà en cours"):
        await outil.invoke({"devis": "D-1"}, context(store=store))
    await store.reserve("default:relance:D-2", -1, SCOPE)
    with pytest.raises(ToolError, match="État inconnu"):
        await outil.invoke({"devis": "D-2"}, context(store=store))
    assert envois == []


async def test_a_stale_reservation_under_the_old_key_is_taken_back_under_it(
    magasin: Magasin,
) -> None:
    """Sinon la trace d'avant resterait, intacte, et bloquerait la clé pour toujours."""
    envois: list[str] = []

    @idempotent(key=lambda a: f"relance:{a['devis']}", retry_unknown=True)
    @tool(side_effects="irreversible")
    async def envoyer(devis: str) -> str:
        """Envoie la relance du devis."""
        envois.append(devis)
        return "relance"

    store = magasin()
    await store.reserve("default:relance:D-1", -1, SCOPE)
    await envoyer.invoke({"devis": "D-1"}, context(store=store))
    assert envois == ["D-1"]
    ancienne = await store.get("default:relance:D-1")
    assert ancienne is not None and ancienne.status == "completed"
    assert await store.get("default:envoyer:relance:D-1") is None
    # Rejoué : c'est la trace d'avant qui répond, l'effet n'est pas refait.
    await envoyer.invoke({"devis": "D-1"}, context(call_id="c2", store=store))
    assert envois == ["D-1"]


async def test_without_an_old_trace_the_new_key_is_used(magasin: Magasin) -> None:
    envois, outil = _relance()
    store = magasin()
    await outil.invoke({"devis": "D-1"}, context(store=store))
    assert envois == ["D-1"]
    assert await store.get("default:relance:D-1") is None
    nouvelle = await store.get("default:envoyer:relance:D-1")
    assert nouvelle is not None and nouvelle.status == "completed"


# --- Oubli des clés (RGPD) ----------------------------------------------------


async def test_a_session_takes_its_keys_with_it(magasin: Magasin) -> None:
    store = magasin()
    autre = KeyScope(tenant_id=DEFAULT_TENANT, session_id=SessionId("ailleurs"))
    await store.reserve("ici", 60, SCOPE)
    await store.reserve("la-bas", 60, autre)
    assert await store.forget(DEFAULT_TENANT, SESSION) == 1
    assert await store.get("ici") is None
    assert await store.get("la-bas") is not None


async def test_a_client_takes_all_of_them(magasin: Magasin) -> None:
    store = magasin()
    etranger = KeyScope(tenant_id=TenantId("acme"), session_id=SESSION)
    await store.reserve("a", 60, SCOPE)
    await store.reserve("b", 60, SCOPE)
    await store.reserve("c", 60, etranger)
    assert await store.forget(DEFAULT_TENANT) == 2
    assert await store.get("c") is not None


async def test_the_journal_store_has_nothing_of_its_own_to_forget() -> None:
    """Ses enregistrements sont des événements : ils partent avec la session."""
    store, _ = await _journal()
    await store.complete("k", "fait")
    assert await store.forget(DEFAULT_TENANT, SESSION) == 0


# --- État inconnu levé par l'outil --------------------------------------------


def _fragile(on_unknown: UnknownState = "error") -> tuple[list[str], Relanceur]:
    """Outil dont la réservation précédente a expiré sans résultat."""
    envois: list[str] = []

    @idempotent(key=lambda a: f"relance:{a['devis']}")
    @tool(side_effects="irreversible", on_unknown=on_unknown)
    async def envoyer(devis: str) -> str:
        """Envoie la relance du devis."""
        envois.append(devis)
        return f"relance {len(envois)}"

    return envois, envoyer


async def _perimee(store: IdempotencyStore, outil: Relanceur) -> None:
    """Laisse une réservation périmée sur la clé de l'appel à venir."""
    await store.reserve(outil.key_for({"devis": "D-1"}, context()), -1, SCOPE)


async def test_an_unknown_effect_raised_by_the_tool_reaches_the_model(
    magasin: Magasin,
) -> None:
    envois, outil = _fragile()
    store = magasin()
    await _perimee(store, outil)
    executor = ToolExecutor([outil], idempotency=store)  # pyright: ignore[reportArgumentType]
    events = await _run(executor, awaiting(call("c1", "envoyer", devis="D-1")))
    sortie = completed(events)["c1"]
    assert sortie.is_error and "État inconnu" in sortie.as_text
    assert envois == []
    # L'appel est bien parti : c'est son effet qui est incertain, pas son départ.
    assert [e.call_id for e in events if isinstance(e, ToolCalled)] == ["c1"]


async def test_an_unknown_effect_can_ask_a_human_instead(magasin: Magasin) -> None:
    envois, outil = _fragile("pause")
    store = magasin()
    await _perimee(store, outil)
    executor = ToolExecutor([outil], idempotency=store)  # pyright: ignore[reportArgumentType]
    events = await _run(executor, awaiting(call("c1", "envoyer", devis="D-1")))
    demandes = [e for e in events if isinstance(e, ApprovalRequested)]
    assert len(demandes) == 1
    assert "État inconnu" in demandes[0].reason
    assert not completed(events)
    assert envois == []


async def test_a_granted_approval_takes_the_stale_reservation_back(
    magasin: Magasin,
) -> None:
    """L'humain a vérifié : l'appel repart, et reprend la clé restée en plan."""
    envois, outil = _fragile("pause")
    store = magasin()
    await _perimee(store, outil)
    executor = ToolExecutor([outil], idempotency=store)  # pyright: ignore[reportArgumentType]
    state = awaiting(call("c1", "envoyer", started=True, devis="D-1"))
    state = _granted(state, "c1", "envoyer")
    events = await _run(executor, state)
    assert completed(events)["c1"].as_text == "relance 1"
    assert envois == ["D-1"]


def _virement() -> tuple[list[str], Relanceur]:
    """Outil idempotent à approbation obligatoire, dont la clé est métier."""
    envois: list[str] = []

    @idempotent(key=lambda a: f"relance:{a['devis']}")
    @tool(side_effects="irreversible", approval="always")
    async def envoyer(devis: str) -> str:
        """Envoie la relance du devis."""
        envois.append(devis)
        return f"relance {len(envois)}"

    return envois, envoyer


def _accorde(state: RunState, *, lance: bool) -> RunState:
    """Accord donné avant le lancement, suivi ou non de ce lancement (le plantage)."""
    suite: list[DurablePayload] = [
        ApprovalRequested(call_id="c1", tool_name="envoyer", arguments={"devis": "D-1"}),
        ApprovalGranted(call_id="c1", tool_name="envoyer", by="l'artisan"),
    ]
    if lance:
        suite.append(
            ToolCalled(call_id="c1", tool_name="envoyer", tool_kind="python", arguments={})
        )
    return _after(state, *suite)


async def test_an_approved_launch_that_died_after_its_effect_is_not_redone(
    magasin: Magasin,
) -> None:
    """Accord, lancement, effet, plantage avant l'enregistrement : la reprise ne refait rien."""
    envois, outil = _virement()
    store = magasin()
    await _perimee(store, outil)
    executor = ToolExecutor([outil], idempotency=store)  # pyright: ignore[reportArgumentType]
    state = awaiting(call("c1", "envoyer", started=True, devis="D-1"))
    events = await _run(executor, _accorde(state, lance=True))
    sortie = completed(events)["c1"]
    assert sortie.is_error and "État inconnu" in sortie.as_text
    assert envois == []


async def test_an_approval_before_the_launch_does_not_lift_a_stale_reservation(
    magasin: Magasin,
) -> None:
    """Réservation périmée d'un autre run : l'accord, donné sans le savoir, ne l'efface pas."""
    envois, outil = _virement()
    store = magasin()
    await _perimee(store, outil)
    executor = ToolExecutor([outil], idempotency=store)  # pyright: ignore[reportArgumentType]
    state = awaiting(call("c1", "envoyer", devis="D-1"))
    events = await _run(executor, _accorde(state, lance=False))
    sortie = completed(events)["c1"]
    assert sortie.is_error and "État inconnu" in sortie.as_text
    assert envois == []


async def test_an_approval_asked_after_the_launch_takes_the_stale_reservation_back(
    magasin: Magasin,
) -> None:
    """L'état inconnu est découvert après le lancement ; l'accord qui suit y répond."""
    envois, outil = _virement()
    store = magasin()
    await _perimee(store, outil)
    state = awaiting(call("c1", "envoyer", started=True, devis="D-1"))
    state = _after(
        state,
        ApprovalRequested(call_id="c1", tool_name="envoyer", reason=UNKNOWN_EFFECT),
        ApprovalGranted(call_id="c1", tool_name="envoyer", by="l'artisan"),
    )
    executor = ToolExecutor([outil], idempotency=store)  # pyright: ignore[reportArgumentType]
    events = await _run(executor, state)
    assert completed(events)["c1"].as_text == "relance 1"
    assert envois == ["D-1"]


async def test_an_online_approver_decides_after_the_batch(magasin: Magasin) -> None:
    """Le lot est fini quand l'état inconnu se découvre : l'appel repartira au tour suivant."""
    envois, outil = _fragile("pause")
    store = magasin()
    await _perimee(store, outil)
    vues: list[PendingApproval] = []

    async def accorde(asked: PendingApproval) -> Approved:
        vues.append(asked)
        return Approved(by="l'artisan")

    executor = ToolExecutor([outil], idempotency=store)  # pyright: ignore[reportArgumentType]
    events = await _run(executor, awaiting(call("c1", "envoyer", devis="D-1")), approver=accorde)
    assert [a.tool_name for a in vues] == ["envoyer"]
    assert [type(e).__name__ for e in events if isinstance(e, ApprovalGranted)] == [
        "ApprovalGranted"
    ]
    assert not completed(events)
    assert envois == []


async def test_an_online_approver_who_refuses_ends_the_call(magasin: Magasin) -> None:
    envois, outil = _fragile("pause")
    store = magasin()
    await _perimee(store, outil)

    async def refuse(asked: PendingApproval) -> Rejected:
        return Rejected(reason="je vérifie d'abord", by="l'artisan")

    executor = ToolExecutor([outil], idempotency=store)  # pyright: ignore[reportArgumentType]
    events = await _run(executor, awaiting(call("c1", "envoyer", devis="D-1")), approver=refuse)
    sortie = completed(events)["c1"]
    assert sortie.is_error and "je vérifie d'abord" in sortie.as_text
    assert envois == []


async def test_retry_unknown_never_asks(magasin: Magasin) -> None:
    envois: list[str] = []

    @idempotent(key=lambda a: f"relance:{a['devis']}", retry_unknown=True)
    @tool(side_effects="irreversible", on_unknown="pause")
    async def envoyer(devis: str) -> str:
        """Envoie la relance du devis."""
        envois.append(devis)
        return "partie"

    store = magasin()
    await store.reserve(envoyer.key_for({"devis": "D-1"}, context()), -1, SCOPE)
    executor = ToolExecutor([envoyer], idempotency=store)
    events = await _run(executor, awaiting(call("c1", "envoyer", devis="D-1")))
    assert not [e for e in events if isinstance(e, ApprovalRequested)]
    assert completed(events)["c1"].as_text == "partie"
    assert envois == ["D-1"]


def test_an_unknown_effect_is_a_tool_error() -> None:
    """Sans moteur pour l'intercepter, son message vaut pour le modèle."""
    assert issubclass(UnknownEffect, ToolError)


# --- Trace au journal d'un effet déjà mémorisé --------------------------------


def _reused(events: list[ToolEvent]) -> list[IdempotencyReused]:
    return [e for e in events if isinstance(e, IdempotencyReused)]


async def test_a_reused_effect_says_so_in_the_journal(magasin: Magasin) -> None:
    """Sans ce mot, le run montrerait un appel sans effet, et rien qui l'explique."""
    envois, outil = _relance()
    store = magasin()
    executor = ToolExecutor([outil], idempotency=store)  # pyright: ignore[reportArgumentType]

    premier = await _run(executor, awaiting(call("c1", "envoyer", devis="D-1")))
    assert not _reused(premier)
    assert envois == ["D-1"]

    # Un autre appel, la même clé : l'effet ne se refait pas, et il le dit.
    second = await _run(executor, awaiting(call("c2", "envoyer", devis="D-1")))
    [dit] = _reused(second)
    assert (dit.call_id, dit.tool_name, dit.key) == ("c2", "envoyer", "default:envoyer:relance:D-1")
    assert envois == ["D-1"]
    assert completed(second)["c2"].as_text == "relance 1"


async def test_the_journal_store_says_it_too() -> None:
    """Le magasin `journal` écrivait déjà son enregistrement ; il manquait le rejeu."""
    store = InMemoryEventStore()
    journal = RunJournal(agent="demo", session_id=SESSION)
    journal.start("?")
    writer = SessionWriter(store, DEFAULT_TENANT, SESSION, 0)
    await writer.append(journal.take())
    envois, outil = _compteur()
    executor = ToolExecutor([outil])  # pyright: ignore[reportArgumentType]
    state = awaiting(call("c1", "envoyer")).model_copy(
        update={"run_id": journal.run_id, "session_id": SESSION}
    )

    await _run(executor, state, writer=writer, scope=journal.scope)
    repris = state.model_copy(update={"pending_calls": (call("c1", "envoyer", started=True),)})
    events = await _run(executor, repris, writer=writer, scope=journal.scope)
    [dit] = _reused(events)
    assert dit.call_id == "c1"
    assert envois == ["envoi"]


# --- Config et suppression ----------------------------------------------------

OUTILS_CONFIG = '''
from loom_ia.tools import idempotent, tool


@idempotent(key=lambda a: f"relance:{a['devis']}")
@tool(side_effects="irreversible")
async def envoyer_relance(devis: str) -> str:
    """Envoie la relance du devis."""
    return f"relance de {devis}"


@idempotent
@tool(side_effects="irreversible")
async def envoyer_simple(devis: str) -> str:
    """Envoie la relance du devis, sans clé métier."""
    return f"relance de {devis}"
'''


@pytest.fixture
def atelier(tmp_path: Path) -> Callable[..., Path]:
    """Config minimale dont on fait varier le magasin d'idempotence et l'outil."""

    def build(
        *,
        magasin: dict[str, Any] | None = None,
        outil: str = "envoyer_relance",
        script: list[dict[str, Any]] | None = None,
    ) -> Path:
        (tmp_path / "agents").mkdir(exist_ok=True)
        (tmp_path / "outils_idem.py").write_text(OUTILS_CONFIG, encoding="utf-8")
        storage: dict[str, Any] = {"events": {"backend": "jsonl", "path": "data"}}
        if magasin is not None:
            storage["idempotency"] = magasin
        config: dict[str, Any] = {
            "version": 1,
            "imports": ["outils_idem"],
            "models": [
                {
                    "id": "FAKE",
                    "sdk": "fake",
                    "model": "fake-1",
                    "params": {"script": script or [{}]},
                }
            ],
            "storage": storage,
            "telemetry": {"logging": {"level": "CRITICAL"}},
        }
        (tmp_path / "loom.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
        agent: dict[str, Any] = {
            "name": "demo",
            "description": "Relance.",
            "main": {"model": "FAKE", "system": "Tu relances."},
            "tools": [{"python": outil}],
        }
        (tmp_path / "agents" / "demo.yaml").write_text(yaml.safe_dump(agent), encoding="utf-8")
        return tmp_path / "loom.yaml"

    return build


def test_a_business_key_needs_a_shared_store(atelier: Callable[..., Path]) -> None:
    """Le journal ne voit que son run : la promesse y serait tenue nulle part."""
    config = load_config(atelier())
    with pytest.raises(ConfigError, match="clé métier"):
        build_agent(config, "demo", InMemoryEventStore())


def test_memory_is_shared_but_does_not_last(atelier: Callable[..., Path]) -> None:
    config = load_config(atelier(magasin={"backend": "memory"}))
    with pytest.raises(ConfigError, match="envoyer_relance"):
        build_agent(config, "demo", InMemoryEventStore())


@pytest.mark.skipif(find_spec("aiosqlite") is None, reason="extra 'sqlite' absent")
def test_sqlite_carries_a_business_key(atelier: Callable[..., Path], tmp_path: Path) -> None:
    config = load_config(atelier(magasin={"backend": "sqlite", "path": "keys.db"}))
    assert config.storage.idempotency.path == tmp_path / "keys.db"
    assert build_agent(config, "demo", InMemoryEventStore()).spec.name == "demo"


def test_a_technical_key_runs_on_any_store(atelier: Callable[..., Path]) -> None:
    config = load_config(atelier(outil="envoyer_simple"))
    assert build_agent(config, "demo", InMemoryEventStore()).spec.name == "demo"


def test_sqlite_wants_its_own_file(atelier: Callable[..., Path]) -> None:
    with pytest.raises(ConfigError, match="'path' est obligatoire"):
        load_config(atelier(magasin={"backend": "sqlite"}))


def test_the_other_stores_have_no_file(atelier: Callable[..., Path]) -> None:
    with pytest.raises(ConfigError, match="n'a pas de sens"):
        load_config(atelier(magasin={"backend": "memory", "path": "keys.db"}))


ENVOI_SCRIPT: list[dict[str, Any]] = [
    {
        "text": "J'envoie.",
        "tool_calls": [{"name": "envoyer_relance", "arguments": {"devis": "D-2026-042"}}],
    },
    {"text": "C'est fait."},
]


@pytest.mark.skipif(find_spec("aiosqlite") is None, reason="extra 'sqlite' absent")
async def test_the_trace_reaches_the_journal_under_its_call(
    atelier: Callable[..., Path],
) -> None:
    """Deux conversations, un seul effet — et le second run le raconte."""
    path = atelier(magasin={"backend": "sqlite", "path": "keys.db"}, script=ENVOI_SCRIPT)
    autre = SessionId("atelier-bis")
    async with Loom.from_config(path) as loom:
        await loom.run("demo", "Relance.", session_id=SESSION)
        await loom.run("demo", "Relance.", session_id=autre)
        premiers = await loom.export_session(SESSION)
        seconds = await loom.export_session(autre)

    assert not [e for e in premiers if isinstance(e.payload, IdempotencyReused)]
    dits = [e for e in seconds if isinstance(e.payload, IdempotencyReused)]
    appels = [
        e
        for e in seconds
        if isinstance(payload := e.payload, ToolCalled) and payload.tool_name == "envoyer_relance"
    ]
    assert len(dits) == len(appels) == 1
    payload = dits[0].payload
    assert isinstance(payload, IdempotencyReused)
    assert payload.key == "default:envoyer_relance:relance:D-2026-042"
    # Il se lit avec son appel : même span, et l'appel est juste avant.
    assert dits[0].span_id == appels[0].span_id
    assert dits[0].seq == appels[0].seq + 1


@pytest.mark.skipif(find_spec("aiosqlite") is None, reason="extra 'sqlite' absent")
async def test_deleting_a_session_takes_its_keys(
    atelier: Callable[..., Path], tmp_path: Path
) -> None:
    """RGPD : une clé métier peut porter une référence client."""
    path = atelier(magasin={"backend": "sqlite", "path": "keys.db"})
    store = _sqlite(tmp_path / "keys.db")
    await store.reserve("default:relance:D-1", 60, SCOPE)
    await _referme(store)

    async with Loom.from_config(path) as loom:
        removed = await loom.delete_session(SESSION)
    assert removed.keys == 1

    relu = _sqlite(tmp_path / "keys.db")
    assert await relu.get("default:relance:D-1") is None
    await _referme(relu)
