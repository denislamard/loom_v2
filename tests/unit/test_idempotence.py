# SPDX-License-Identifier: Apache-2.0
"""Idempotence (J4.4) : magasins, décorateur, clé métier, règle de reprise."""

import asyncio
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
    IdempotencyRecorded,
    IdempotencyReused,
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
    PendingApproval,
    PendingCall,
    Rejected,
    ResultTooLarge,
    RunState,
    RunStatus,
    SessionId,
    TenantId,
    ToolOutput,
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
from loom_ia.core.projections import fold
from loom_ia.engine import UNKNOWN_STATE, JournalIdempotency, SessionWriter, ToolExecutor
from loom_ia.engine.executor import ToolEvent
from loom_ia.runtime import build_agent
from loom_ia.testing import RunJournal
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


def test_a_business_key_is_prefixed_by_the_client() -> None:
    _, outil = _relance()
    assert outil.spec.business_key is True
    assert outil.key_for({"devis": "D-42"}, context()) == "default:relance:D-42"
    autre = replace(context(), tenant_id=TenantId("acme"))
    assert outil.key_for({"devis": "D-42"}, autre) == "acme:relance:D-42"


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
    assert (dit.call_id, dit.tool_name, dit.key) == ("c2", "envoyer", "default:relance:D-1")
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
    assert payload.key == "default:relance:D-2026-042"
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
