# SPDX-License-Identifier: Apache-2.0
"""Bus des nouvelles d'écriture : ce qui se vérifie sans service (J5.3c).

Le suivi s'éprouve entièrement avec le bus en mémoire : deux journaux
notifiants au-dessus du **même** journal, reliés par un bus — c'est la forme
qu'ont deux process, sans les process. Les vrais bus sont dans
``tests/integration/test_bus.py``.
"""

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator, Sequence
from contextlib import asynccontextmanager, suppress
from importlib.util import find_spec
from typing import Any

import pytest
from conftest import ConfigFactory

from loom_ia.access.api import Loom
from loom_ia.access.cli import main
from loom_ia.adapters.bus import InMemoryBus
from loom_ia.adapters.stores import InMemoryEventStore, NotifyingEventStore
from loom_ia.config import ConfigError, load_config
from loom_ia.config.models import BUS_BACKENDS, SHARED_BUSES, BusStorage
from loom_ia.core.events import Event, EventDraft
from loom_ia.core.model import DEFAULT_TENANT, Message, SessionId, ToolOutput
from loom_ia.core.ports import Notice
from loom_ia.runtime import create_bus, storage_warnings
from loom_ia.testing import RunJournal, tool_call_message

SESSION = SessionId("atelier")
VARIABLE = "LOOM_BUS_ESSAI"
POSTGRES: dict[str, Any] = {"bus": {"backend": "postgres", "dsn_env": VARIABLE}}
REDIS: dict[str, Any] = {"bus": {"backend": "redis", "url_env": VARIABLE}}

sans_redis = pytest.mark.skipif(find_spec("redis") is None, reason="extra 'redis' absent")
avec_redis = pytest.mark.skipif(find_spec("redis") is not None, reason="extra 'redis' présent")
sans_pg = pytest.mark.skipif(find_spec("asyncpg") is None, reason="extra 'postgres' absent")
sans_rabbit = pytest.mark.skipif(find_spec("aio_pika") is None, reason="extra 'rabbitmq' absent")
avec_pg = pytest.mark.skipif(find_spec("asyncpg") is not None, reason="extra 'postgres' présent")
# Un bus déclaré est monté avant d'être joint : l'extra manquant se dit d'abord.
DECLARES = [pytest.param(POSTGRES, marks=sans_pg), pytest.param(REDIS, marks=sans_redis)]
MANQUE = [
    pytest.param(POSTGRES, "postgres", marks=avec_pg),
    pytest.param(REDIS, "redis", marks=avec_redis),
]


def drafts(tool: str = "calculer") -> list[EventDraft]:
    journal = RunJournal(session_id=SESSION)
    journal.start("Combien font 2 + 2 ?")
    journal.model_turn(tool_call_message(("c1", tool, {"expr": "2+2"})))
    journal.tool_results({"c1": ToolOutput.text("4")})
    journal.model_turn(Message.assistant("4"))
    journal.complete()
    return journal.take()


def notice_of(events: Sequence[Event], source: str) -> Notice:
    return Notice(
        tenant_id=events[0].tenant_id,
        session_id=events[0].session_id,
        first_seq=events[0].seq,
        last_seq=events[-1].seq,
        source=source,
    )


@asynccontextmanager
async def _following(store: NotifyingEventStore, bus: InMemoryBus) -> AsyncGenerator[None]:
    """Fait tourner le suivi du bus le temps du bloc, une fois l'abonnement posé."""
    task = asyncio.create_task(store.follow())
    for _ in range(100):
        if bus.subscribers:
            break
        await asyncio.sleep(0.01)
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


async def _settled() -> None:
    """Laisse le bus livrer ce qui est en vol."""
    for _ in range(10):
        await asyncio.sleep(0.01)


# --- La config ---------------------------------------------------------------


def test_the_default_bus_stays_in_this_process() -> None:
    declared = BusStorage()
    assert declared.backend == "memory"
    assert not declared.shared
    assert declared.variable is None
    assert set(SHARED_BUSES) < set(BUS_BACKENDS)


def test_a_shared_bus_names_its_variable() -> None:
    assert BusStorage(backend="postgres", dsn_env=VARIABLE).variable == VARIABLE
    assert BusStorage(backend="redis", url_env=VARIABLE).variable == VARIABLE
    assert BusStorage(backend="postgres", dsn_env=VARIABLE).shared


def test_the_surplus_key_says_which_one_was_meant() -> None:
    with pytest.raises(ValueError, match=r"'url_env' n'a pas de sens, c'est 'dsn_env' qu'il faut"):
        BusStorage(backend="postgres", url_env=VARIABLE)


# --- Le câblage --------------------------------------------------------------


def test_a_memory_bus_is_no_bus_at_all(demo: ConfigFactory) -> None:
    """Dans un seul process, le journal remet déjà ses écritures à ses abonnés."""
    assert create_bus(load_config(demo())) is None


@pytest.mark.parametrize("storage", DECLARES)
def test_an_empty_variable_is_named_in_the_error(
    demo: ConfigFactory, monkeypatch: pytest.MonkeyPatch, storage: dict[str, Any]
) -> None:
    monkeypatch.delenv(VARIABLE, raising=False)
    with pytest.raises(ConfigError, match=rf"{VARIABLE}.* est vide ou absente"):
        create_bus(load_config(demo(storage=storage)))


@sans_redis
def test_a_redis_bus_is_built_without_connecting(
    demo: ConfigFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(VARIABLE, "redis://127.0.0.1:6379/0")
    bus = create_bus(load_config(demo(storage=REDIS)))
    assert repr(bus) == "RedisBus('loom:events')"


@sans_pg
def test_a_postgres_bus_is_built_without_connecting(
    demo: ConfigFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(VARIABLE, "postgresql://loom@127.0.0.1:5432/loom")
    bus = create_bus(load_config(demo(storage=POSTGRES)))
    assert repr(bus) == "PostgresBus('loom_events')"


@pytest.mark.parametrize(("storage", "extra"), MANQUE)
def test_without_the_extra_the_refusal_names_it(
    demo: ConfigFactory, monkeypatch: pytest.MonkeyPatch, storage: dict[str, Any], extra: str
) -> None:
    """Ce que voit qui déclare un bus sans avoir installé l'extra."""
    monkeypatch.setenv(VARIABLE, "peu importe")
    with pytest.raises(ConfigError, match=rf"loom-ia\[{extra}\]"):
        create_bus(load_config(demo(storage=storage)))


# --- La nouvelle -------------------------------------------------------------


def test_a_notice_travels_as_json() -> None:
    notice = Notice(
        tenant_id=DEFAULT_TENANT, session_id=SESSION, first_seq=3, last_seq=7, source="worker-1"
    )
    back = Notice.model_validate_json(notice.model_dump_json())
    assert back == notice
    # De quoi tenir dans un NOTIFY de Postgres (8 000 octets).
    assert len(notice.model_dump_json()) < 300


# --- Le suivi ----------------------------------------------------------------


async def test_a_subscriber_sees_what_the_other_instance_writes() -> None:
    """Deux journaux notifiants, un journal, un bus : la forme de deux process."""
    inner = InMemoryEventStore()
    bus = InMemoryBus()
    ici = NotifyingEventStore(inner, bus=bus, source="ici")
    ailleurs = NotifyingEventStore(inner, bus=bus, source="ailleurs")
    vus: list[Event] = []
    with ici.listen(vus.append):
        async with _following(ici, bus):
            written = await ailleurs.append(drafts(), expected_seq=0)
            await _settled()
    # Exactement ce que l'autre a écrit, dans l'ordre du journal.
    assert [e.event_id for e in vus] == [e.event_id for e in written]
    await bus.aclose()


async def test_an_instance_does_not_listen_to_itself() -> None:
    """Ce qu'on écrit est déjà remis : le relire le remettrait deux fois."""
    inner = InMemoryEventStore()
    bus = InMemoryBus()
    ici = NotifyingEventStore(inner, bus=bus, source="ici")
    vus: list[Event] = []
    with ici.listen(vus.append):
        async with _following(ici, bus):
            written = await ici.append(drafts(), expected_seq=0)
            await _settled()
    assert len(vus) == len(written)
    await bus.aclose()


async def test_a_gap_in_the_bus_is_caught_up_at_the_next_notice() -> None:
    """Une nouvelle perdue coûte une notification, jamais un événement."""
    inner = InMemoryEventStore()
    bus = InMemoryBus()
    ici = NotifyingEventStore(inner, bus=bus, source="ici")
    tout = drafts()
    premier = await inner.append(tout[:3], expected_seq=0)
    vus: list[Event] = []
    with ici.listen(vus.append):
        await ici._catch_up(notice_of(premier, "ailleurs"))  # pyright: ignore[reportPrivateUsage]
        assert [e.seq for e in vus] == [1, 2, 3]
        # Deux lots de plus, dont la nouvelle du premier est perdue en route.
        perdu = await inner.append(tout[3:5], expected_seq=3)
        suite = await inner.append(tout[5:], expected_seq=5)
        await ici._catch_up(notice_of(suite, "ailleurs"))  # pyright: ignore[reportPrivateUsage]
    # Le lot perdu est arrivé quand même : la position, pas la nouvelle, fait foi.
    assert [e.seq for e in vus] == [e.seq for e in (*premier, *perdu, *suite)]
    await bus.aclose()


async def test_a_session_never_seen_gives_only_what_the_notice_announces() -> None:
    """Sans position, on ne remonte pas toute la session : juste le lot annoncé."""
    inner = InMemoryEventStore()
    bus = InMemoryBus()
    ici = NotifyingEventStore(inner, bus=bus, source="ici")
    events = await inner.append(drafts(), expected_seq=0)
    vus: list[Event] = []
    with ici.listen(vus.append):
        await ici._catch_up(notice_of(events[-2:], "ailleurs"))  # pyright: ignore[reportPrivateUsage]
    assert [e.seq for e in vus] == [e.seq for e in events[-2:]]
    await bus.aclose()


async def test_with_nobody_listening_nothing_is_read() -> None:
    """Un process qui n'a pas d'abonné ne relit pas : il avance sa position."""
    inner = InMemoryEventStore()
    bus = InMemoryBus()
    ici = NotifyingEventStore(inner, bus=bus, source="ici")
    events = await inner.append(drafts(), expected_seq=0)
    reads = 0
    original = inner.read

    async def counted(*args: Any, **kwargs: Any) -> list[Event]:
        nonlocal reads
        reads += 1
        return await original(*args, **kwargs)

    inner.read = counted  # pyright: ignore[reportAttributeAccessIssue]
    await ici._catch_up(notice_of(events, "ailleurs"))  # pyright: ignore[reportPrivateUsage]
    assert reads == 0
    # La position a quand même avancé : un abonné arrivé après ne reçoit que la suite.
    vus: list[Event] = []
    with ici.listen(vus.append):
        await ici._catch_up(notice_of(events, "ailleurs"))  # pyright: ignore[reportPrivateUsage]
    assert vus == []
    await bus.aclose()


async def test_a_bus_that_fails_does_not_fail_the_write() -> None:
    """Une écriture au journal a eu lieu : la manquer sur le bus n'y change rien."""

    class Casse:
        async def publish(self, notice: Notice) -> None:
            raise RuntimeError("bus en panne")

        def notices(self) -> AsyncIterator[Notice]:  # pragma: no cover - jamais écouté ici
            raise RuntimeError("bus en panne")

        async def aclose(self) -> None:
            return None

    store = NotifyingEventStore(InMemoryEventStore(), bus=Casse(), source="ici")
    asked = drafts()
    written = await store.append(asked, expected_seq=0)
    assert len(written) == len(asked)


# --- L'instance --------------------------------------------------------------


async def test_an_instance_without_a_bus_has_nothing_to_follow(demo: ConfigFactory) -> None:
    async with Loom(load_config(demo())) as loom:
        assert loom.store.source == loom.worker_id


def test_validate_shows_the_bus(
    demo: ConfigFactory, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv(VARIABLE, raising=False)
    assert main(["--config", str(demo()), "validate"]) == 0
    assert (
        "Bus        : memory (les nouvelles ne sortent pas de ce process)"
        in capsys.readouterr().out
    )


def test_validate_says_when_the_bus_variable_is_missing(
    demo: ConfigFactory, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv(VARIABLE, raising=False)
    assert main(["--config", str(demo(storage=POSTGRES)), "validate"]) == 2
    assert f"Bus        : postgres ({VARIABLE} : ABSENTE)" in capsys.readouterr().out


# --- Ce qui ne va pas ensemble à plusieurs process ---------------------------


def test_a_single_process_service_is_warned_of_nothing(demo: ConfigFactory) -> None:
    assert storage_warnings(load_config(demo())) == []


@pytest.mark.parametrize("storage", [POSTGRES, REDIS])
def test_a_shared_bus_with_local_files_says_they_must_be_shared_too(
    demo: ConfigFactory, monkeypatch: pytest.MonkeyPatch, storage: dict[str, Any]
) -> None:
    """Le trou des artefacts : le journal et les nouvelles traversent, pas les fichiers."""
    monkeypatch.setenv(VARIABLE, "peu importe")
    path = demo(storage={**storage, "events": {"backend": "jsonl", "path": "data"}})
    (warning,) = storage_warnings(load_config(path))
    assert "artefacts 'local'" in warning
    assert "partager ce dossier" in warning


def test_a_brokered_queue_with_files_in_memory_says_they_go_nowhere(
    demo: ConfigFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(VARIABLE, "amqp://loom@127.0.0.1:5672/")
    path = demo(storage={"queue": {"backend": "rabbitmq", "url_env": VARIABLE}})
    (warning,) = storage_warnings(load_config(path))
    assert "artefacts 'memory'" in warning
    assert "file servie par un courtier" in warning


@sans_rabbit
def test_validate_says_it_too(
    demo: ConfigFactory, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(VARIABLE, "amqp://loom@127.0.0.1:5672/")
    path = demo(storage={"queue": {"backend": "rabbitmq", "url_env": VARIABLE}})
    assert main(["--config", str(path), "validate"]) == 0
    assert "artefacts 'memory'" in capsys.readouterr().out
