# SPDX-License-Identifier: Apache-2.0
"""Exportateur : direct, rattrapage, coupure, saturation, redémarrage, effacement (docs/analyse-exortateur-loom.md, lot 2)."""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("websockets", reason="extra 'console' absent")

from loom_ia.adapters.stores import InMemoryEventStore, JsonlEventStore
from loom_ia.console import channel as channel_module
from loom_ia.console import exporter as exporter_module
from loom_ia.console.channel import ChannelSettings, ConsoleChannel
from loom_ia.console.contract import CONTRACTS, AckPayload, Envelope, EventsPayload, HelloPayload, Payload, SessionDeletedPayload
from loom_ia.console.cursor import ConsoleCursor, CursorState, InMemoryCursor, JsonlCursor
from loom_ia.console.exporter import ConsoleExporter
from loom_ia.core.events import Event
from loom_ia.core.events.payloads import ApprovalRequested
from loom_ia.core.model import DEFAULT_TENANT, Message, SessionId, TenantId, ToolOutput
from loom_ia.core.ports import EventStore
from loom_ia.core.ports.event_store import JournalCorrupted
from loom_ia.telemetry.redaction import Redactor
from loom_ia.testing import RunJournal, tool_call_message
from loom_ia.testing.console_server import FakeConsole

KEY = "s3cret"
HELLO = HelloPayload(instance_id="atelier", worker_id="worker-1", loom_version="2.0.23", contracts=CONTRACTS, tenant=DEFAULT_TENANT)


@pytest.fixture(autouse=True)
def quick(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(channel_module, "RETRY_BASE", 0.02)
    monkeypatch.setattr(channel_module, "RETRY_CAP", 0.05)
    monkeypatch.setattr(exporter_module, "FLUSH_INTERVAL", 0.01)
    monkeypatch.setattr(exporter_module, "ACK_WAIT", 0.2)


async def write(store: EventStore, session: str, *, tenant: TenantId = DEFAULT_TENANT) -> list[Event]:
    """Un run complet dans une session : huit à dix événements, écrits à la suite de ce que la session a déjà."""
    journal = RunJournal(agent="demo", session_id=SessionId(session), tenant_id=tenant)
    journal.start("Combien font 2 + 2 ?").model_turn(tool_call_message(("c1", "calculer", {"expr": "2+2"})))
    journal.tool_results({"c1": ToolOutput.text("4")}).model_turn(Message.assistant("4")).complete()
    last = await store.last_seq(tenant, SessionId(session))
    return await store.append(journal.take(), expected_seq=last)


@dataclass
class Rig:
    """Une fausse console, un canal, un exportateur et ses deux tâches, sur un journal et un curseur."""

    store: EventStore
    cursor: ConsoleCursor
    fake: FakeConsole
    options: dict[str, Any] = field(default_factory=dict[str, Any])
    exporter: ConsoleExporter | None = None
    channel: ConsoleChannel | None = None
    tasks: list[asyncio.Task[None]] = field(default_factory=list[asyncio.Task[None]])

    async def start(self) -> ConsoleExporter:
        channel = ConsoleChannel(ChannelSettings(url=self.fake.url, key=KEY, hello=HELLO), on_message=self._message, on_connected=self._connected)
        exporter = ConsoleExporter(self.store, self.cursor, channel, tenant=DEFAULT_TENANT, mode="on_demand", **self.options)
        self.channel, self.exporter = channel, exporter
        self.tasks = [asyncio.create_task(exporter.run()), asyncio.create_task(channel.run())]
        return exporter

    async def offer(self, events: list[Event]) -> None:
        assert self.exporter is not None
        for event in events:
            self.exporter.offer(event)

    async def stop(self) -> None:
        assert self.exporter is not None and self.channel is not None
        await self.exporter.aclose(2.0)
        await self.channel.aclose()
        for task in self.tasks:
            async with asyncio.timeout(5):
                await task

    def _connected(self) -> None:
        if self.exporter is not None:
            self.exporter.connected()

    async def _message(self, envelope: Envelope, payload: Payload) -> None:
        if isinstance(payload, AckPayload) and self.exporter is not None:
            await self.exporter.received(payload)


def positions(fake: FakeConsole) -> list[tuple[str, int]]:
    """Ce que la console a reçu, dans l'ordre : (session, seq), doublons compris."""
    found: list[tuple[str, int]] = []
    for item in fake.of("events"):
        assert isinstance(item.payload, EventsPayload)
        found.extend((str(event["session_id"]), int(str(event["seq"]))) for event in item.payload.events)
    return found


async def until(check: Callable[[], bool | Awaitable[bool]]) -> None:
    async with asyncio.timeout(5):
        while True:
            result = check()
            if result if isinstance(result, bool) else await result:
                return
            await asyncio.sleep(0.01)


async def acked(cursor: ConsoleCursor, session: str) -> int:
    state = await cursor.load(DEFAULT_TENANT)
    return 0 if state is None else state.acked.get(SessionId(session), 0)


@pytest.fixture
async def console() -> AsyncIterator[FakeConsole]:
    async with FakeConsole(key=KEY) as fake:
        yield fake


async def test_live_events_reach_the_console_in_order_and_are_acked(console: FakeConsole) -> None:
    rig = Rig(InMemoryEventStore(), InMemoryCursor(), console)
    await rig.start()
    await until(lambda: rig.channel is not None and rig.channel.connected)
    events = await write(rig.store, "s1")
    await rig.offer(events)
    await until(lambda: len(positions(console)) == len(events))
    assert positions(console) == [("s1", event.seq) for event in events]
    await rig.stop()
    assert await acked(rig.cursor, "s1") == events[-1].seq


async def test_an_unreachable_console_gets_everything_once_it_is_up(tmp_path: Path) -> None:
    async with FakeConsole(key=KEY) as probe:
        port = probe.port
    store, cursor = InMemoryEventStore(), InMemoryCursor()
    fake = FakeConsole(key=KEY, port=port)
    rig = Rig(store, cursor, fake)
    exporter = await rig.start()
    events = await write(store, "s1")
    await rig.offer(events)
    assert exporter.pending == len(events), "rien n'attend : l'événement est en file, le run continue"
    async with fake:
        await until(lambda: len(set(positions(fake))) == len(events))
        await rig.stop()
    assert sorted(set(positions(fake))) == [("s1", event.seq) for event in events]


async def test_a_cut_before_the_ack_is_resent_without_loss(console: FakeConsole) -> None:
    console.auto_ack = False
    rig = Rig(InMemoryEventStore(), InMemoryCursor(), console)
    await rig.start()
    await until(lambda: rig.channel is not None and rig.channel.connected)
    events = await write(rig.store, "s1")
    await rig.offer(events)
    await until(lambda: len(positions(console)) == len(events))
    await console.drop()
    console.auto_ack = True
    await until(lambda: len(console.of("hello")) == 2)
    await until(lambda: len(positions(console)) >= 2 * len(events))
    await rig.stop()
    assert sorted(set(positions(console))) == [("s1", event.seq) for event in events], "aucune perte ; la console écarte les doublons"
    assert await acked(rig.cursor, "s1") == events[-1].seq


async def test_the_first_start_sends_nothing_of_the_history(console: FakeConsole) -> None:
    store = InMemoryEventStore()
    before = await write(store, "ancienne")
    rig = Rig(store, InMemoryCursor(), console)
    await rig.start()
    await until(lambda: rig.channel is not None and rig.channel.connected)
    after = await write(store, "nouvelle")
    await rig.offer(after)
    await until(lambda: len(positions(console)) == len(after))
    await rig.stop()
    assert {session for session, _ in positions(console)} == {"nouvelle"}
    assert await acked(rig.cursor, "ancienne") == before[-1].seq, "le curseur part de la position actuelle (Q-11)"


async def write_unfinished(store: EventStore, session: str) -> list[Event]:
    """Un run qui attend le résultat de son outil : démarré, jamais fini."""
    journal = RunJournal(agent="demo", session_id=SessionId(session))
    journal.start("Combien font 3 + 3 ?").model_turn(tool_call_message(("c1", "calculer", {"expr": "3+3"})))
    last = await store.last_seq(DEFAULT_TENANT, SessionId(session))
    return await store.append(journal.take(), expected_seq=last)


async def test_the_first_start_sends_the_runs_still_open(console: FakeConsole) -> None:
    """L-1 : une session qui a un run en cours part du premier événement de ce run ; les autres, de leur position actuelle."""
    store = InMemoryEventStore()
    done = await write(store, "finie")
    history = await write(store, "melee")
    open_run = await write_unfinished(store, "melee")
    alone = await write_unfinished(store, "seule")
    rig = Rig(store, InMemoryCursor(), console)
    await rig.start()
    expected = [("melee", event.seq) for event in open_run] + [("seule", event.seq) for event in alone]
    await until(lambda: sorted(set(positions(console))) == sorted(expected))
    await rig.stop()
    assert {session for session, _ in positions(console)} == {"melee", "seule"}
    assert min(seq for session, seq in positions(console) if session == "melee") == history[-1].seq + 1, "rien d'avant le run en cours"
    assert await acked(rig.cursor, "finie") == done[-1].seq
    assert await acked(rig.cursor, "melee") == open_run[-1].seq
    assert await acked(rig.cursor, "seule") == alone[-1].seq


async def test_the_open_runs_are_sent_once_then_never_again(console: FakeConsole) -> None:
    store, cursor = InMemoryEventStore(), InMemoryCursor()
    open_run = await write_unfinished(store, "s1")
    rig = Rig(store, cursor, console)
    await rig.start()
    await until(lambda: len(positions(console)) == len(open_run))
    await rig.stop()
    again = Rig(store, cursor, console)
    await again.start()
    await until(lambda: len(console.of("hello")) == 2)
    await asyncio.sleep(0.1)
    await again.stop()
    assert len(positions(console)) == len(open_run), "le curseur existe : le second démarrage ne renvoie rien"


async def test_an_unreadable_session_is_skipped_at_the_first_start(
    console: FakeConsole, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    store = InMemoryEventStore()
    broken = await write_unfinished(store, "cassee")
    sound = await write_unfinished(store, "saine")
    read = store.read

    async def failing(tenant: TenantId, session: SessionId, **kwargs: Any) -> list[Event]:
        if session == "cassee" and not kwargs:
            raise JournalCorrupted("ligne 3 illisible")
        return await read(tenant, session, **kwargs)

    monkeypatch.setattr(store, "read", failing)
    rig = Rig(store, InMemoryCursor(), console)
    await rig.start()
    await until(lambda: len(positions(console)) == len(sound))
    await rig.stop()
    assert {session for session, _ in positions(console)} == {"saine"}
    assert await acked(rig.cursor, "cassee") == broken[-1].seq, "passée, elle part de sa position actuelle"
    assert "session cassee illisible" in caplog.text


async def test_the_open_runs_follow_the_exclusions(console: FakeConsole) -> None:
    store = InMemoryEventStore()
    open_run = await write_unfinished(store, "s1")
    rig = Rig(store, InMemoryCursor(), console, options={"exclude": ("message.user",)})
    await rig.start()
    kept = [event for event in open_run if event.type != "message.user"]
    await until(lambda: len(positions(console)) == len(kept))
    await rig.stop()
    assert positions(console) == [("s1", event.seq) for event in kept]


async def test_a_full_queue_loses_nothing(console: FakeConsole) -> None:
    rig = Rig(InMemoryEventStore(), InMemoryCursor(), console, options={"buffer_size": 2, "batch_size": 3})
    exporter = await rig.start()
    await until(lambda: rig.channel is not None and rig.channel.connected)
    first, second = await write(rig.store, "s1"), await write(rig.store, "s2")
    await rig.offer(first + second)
    assert exporter.pending == 2, "la file reste bornée"
    await until(lambda: len(set(positions(console))) == len(first) + len(second))
    await rig.stop()
    received = positions(console)
    for session in ("s1", "s2"):
        seqs = [seq for name, seq in received if name == session]
        assert seqs == sorted(seqs), "l'ordre d'une session est gardé"
    assert sorted(set(received)) == sorted([("s1", e.seq) for e in first] + [("s2", e.seq) for e in second])


async def test_a_restart_sends_only_what_was_not_acked(tmp_path: Path, console: FakeConsole) -> None:
    store, cursor = JsonlEventStore(tmp_path / "journal"), JsonlCursor(tmp_path / "journal")
    rig = Rig(store, cursor, console)
    await rig.start()
    await until(lambda: rig.channel is not None and rig.channel.connected)
    first = await write(store, "s1")
    await rig.offer(first)
    await until(lambda: len(positions(console)) == len(first))
    await rig.stop()
    later = await write(store, "s1")  # écrit pendant que rien n'exporte
    again = Rig(store, cursor, console)
    await again.start()
    await until(lambda: len(positions(console)) == len(first) + len(later))
    await again.stop()
    assert positions(console)[len(first) :] == [("s1", event.seq) for event in later]


async def test_a_session_deleted_here_is_said_until_acked(console: FakeConsole) -> None:
    rig = Rig(InMemoryEventStore(), InMemoryCursor(), console)
    exporter = await rig.start()
    await until(lambda: rig.channel is not None and rig.channel.connected)
    events = await write(rig.store, "s1")
    await rig.offer(events)
    await until(lambda: len(positions(console)) == len(events))
    await rig.store.delete(DEFAULT_TENANT, SessionId("s1"))
    await exporter.session_deleted(DEFAULT_TENANT, SessionId("s1"))
    await until(lambda: bool(console.of("session.deleted")))
    payload = console.of("session.deleted")[0].payload
    assert payload == SessionDeletedPayload(tenant=DEFAULT_TENANT, session="s1")

    async def forgotten() -> bool:
        state = await rig.cursor.load(DEFAULT_TENANT)
        return state is not None and SessionId("s1") not in state.acked and not state.deleted

    await until(forgotten)
    await rig.stop()


async def test_a_session_gone_from_the_journal_is_said(console: FakeConsole) -> None:
    """Effacée par un autre process (``loom sessions delete``) : le curseur l'a, le journal non."""
    cursor = InMemoryCursor()
    await cursor.advance(DEFAULT_TENANT, {SessionId("effacee"): 4}, flushed_at=datetime.now(UTC))
    rig = Rig(InMemoryEventStore(), cursor, console)
    await rig.start()
    await until(lambda: bool(console.of("session.deleted")))
    await rig.stop()
    assert console.of("session.deleted")[0].payload == SessionDeletedPayload(tenant=DEFAULT_TENANT, session="effacee")


async def test_a_session_a_crash_kept_out_of_the_cursor_is_caught_by_the_margin(console: FakeConsole) -> None:
    """Écrite juste avant un arrêt brutal, avant que le curseur ne l'ait notée (Q-6)."""
    store, cursor = InMemoryEventStore(), InMemoryCursor()
    await cursor.advance(DEFAULT_TENANT, {}, flushed_at=datetime.now(UTC))
    events = await write(store, "oubliee")
    rig = Rig(store, cursor, console)
    await rig.start()
    await until(lambda: len(positions(console)) == len(events))
    await rig.stop()
    assert positions(console) == [("oubliee", event.seq) for event in events]


async def test_other_clients_and_excluded_types_stay_home(console: FakeConsole) -> None:
    rig = Rig(InMemoryEventStore(), InMemoryCursor(), console, options={"exclude": ("model.responded",)})
    await rig.start()
    await until(lambda: rig.channel is not None and rig.channel.connected)
    mine, theirs = await write(rig.store, "s1"), await write(rig.store, "s9", tenant=TenantId("martin"))
    await rig.offer(mine + theirs)
    expected = [("s1", event.seq) for event in mine if event.type != "model.responded"]
    await until(lambda: len(positions(console)) == len(expected))
    await asyncio.sleep(0.05)
    await rig.stop()
    assert positions(console) == expected


async def test_push_sends_the_content_masked(console: FakeConsole) -> None:
    rig = Rig(InMemoryEventStore(), InMemoryCursor(), console, options={"redactor": Redactor.of(["email"])})
    exporter = await rig.start()
    exporter._mode = "push"  # pyright: ignore[reportPrivateUsage]
    await until(lambda: rig.channel is not None and rig.channel.connected)
    journal = RunJournal(agent="demo", session_id=SessionId("s1"))
    journal.start("Écris à jean@exemple.fr")
    drafts = journal.take()
    drafts.append(journal.scope.draft(ApprovalRequested(call_id="c", tool_name="envoyer", arguments={"to": "jean@exemple.fr"})))
    events = await rig.store.append(drafts, expected_seq=0)
    await rig.offer(events)
    await until(lambda: len(positions(console)) == len(events))
    await rig.stop()
    sent = [event for item in console.of("events") if isinstance(item.payload, EventsPayload) for event in item.payload.events]
    approval = next(event for event in sent if event["type"] == "approval.requested")
    assert approval["payload"]["arguments"] == {"to": "[email]"}  # type: ignore[index]


async def test_closing_waits_for_the_acks_then_writes_the_cursor(tmp_path: Path, console: FakeConsole) -> None:
    store, cursor = JsonlEventStore(tmp_path / "journal"), JsonlCursor(tmp_path / "journal")
    rig = Rig(store, cursor, console)
    await rig.start()
    await until(lambda: rig.channel is not None and rig.channel.connected)
    events = await write(store, "s1")
    await rig.offer(events)
    await rig.stop()
    state = await JsonlCursor(tmp_path / "journal").load(DEFAULT_TENANT)
    assert state == CursorState(acked={SessionId("s1"): events[-1].seq}, flushed_at=state.flushed_at if state else None)


async def unreachable_rig() -> Rig:
    """Un exportateur dont la console ne répond jamais : un port libéré aussitôt réservé."""
    async with FakeConsole(key=KEY) as probe:
        port = probe.port
    rig = Rig(InMemoryEventStore(), InMemoryCursor(), FakeConsole(key=KEY, port=port))
    await rig.start()
    await asyncio.sleep(0.05)  # le curseur est chargé, le canal tente sa connexion
    return rig


async def close_unreachable(rig: Rig) -> None:
    """Ferme le canal, puis arrête ses tâches : la connexion ne vient jamais."""
    assert rig.channel is not None
    await rig.channel.aclose()
    for task in rig.tasks:
        if not task.done():
            task.cancel()
    await asyncio.wait(rig.tasks)


async def test_closing_with_nothing_to_send_does_not_wait_for_an_unreachable_console(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("WARNING")
    rig = await unreachable_rig()
    assert rig.exporter is not None
    loop = asyncio.get_running_loop()
    started = loop.time()
    await rig.exporter.aclose(5.0)
    assert loop.time() - started < 1.0, "rien à envoyer : la fermeture n'attend pas la connexion"
    assert "injoignable à l'arrêt" not in caplog.text
    await close_unreachable(rig)


async def test_closing_with_events_still_waits_then_says_what_is_left(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("WARNING")
    rig = await unreachable_rig()
    assert rig.exporter is not None
    events = await write(rig.store, "s1")
    await rig.offer(events)
    loop = asyncio.get_running_loop()
    started = loop.time()
    await rig.exporter.aclose(0.3)
    assert loop.time() - started >= 0.3, "des événements à envoyer : la fermeture attend la connexion dans le délai"
    assert f"Console injoignable à l'arrêt : {len(events)} événement(s) non envoyés" in caplog.text
    await close_unreachable(rig)
