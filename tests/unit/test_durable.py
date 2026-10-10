# SPDX-License-Identifier: Apache-2.0
"""Exécution durable (J4.2b) : concession, arrière-plan, reprise au démarrage.

Et l'arrêt écrit par un autre process (6.4) : un run arrêté ailleurs juste
avant sa concession, ou en plein pilotage, ne repart pas — le pilote s'arrête
à sa prochaine écriture, sans rien écrire après la clôture ; un arrêt qui
arrive après la fin rend « déjà fini ».
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Self

import pytest
import yaml
from conftest import demo_agent

from loom_ia.access.api import Loom, StreamItem
from loom_ia.adapters.stores import InMemoryEventStore, JsonlEventStore
from loom_ia.config import load_config
from loom_ia.core.events import (
    ApprovalGranted,
    Event,
    EventDraft,
    ModelResponded,
    RunCancelled,
    RunClaimed,
    RunScope,
    SessionTrimmed,
)
from loom_ia.core.model import (
    DEFAULT_TENANT,
    Message,
    RunId,
    RunState,
    RunStatus,
    SessionId,
    TenantId,
    new_run_id,
)
from loom_ia.core.ports import JournalCorrupted
from loom_ia.core.projections import ProjectionError, fold
from loom_ia.engine import ClaimConflict
from loom_ia.testing import RunJournal
from loom_ia.tools import tool

type ConfigFactory = Callable[..., Path]

SESSION = SessionId("atelier")
QUESTION = "Combien font 12 fois 7, plus 3 ?"
ANSWER = "87."


@pytest.fixture
def durable(tmp_path: Path) -> ConfigFactory:
    """Config JSONL : deux instances peuvent alors partager le même journal."""

    def build(*, lease: float | None = None, **root: Any) -> Path:
        (tmp_path / "agents").mkdir(exist_ok=True)
        execution: dict[str, Any] = {}
        if lease is not None:
            execution["lease"] = lease
        config: dict[str, Any] = {
            "version": 1,
            "models": [
                {
                    "id": "FAKE",
                    "sdk": "fake",
                    "model": "fake-1",
                    "params": {"script": [{"text": ANSWER}]},
                }
            ],
            "storage": {"events": {"backend": "jsonl", "path": "data"}},
            "telemetry": {"logging": {"level": "CRITICAL"}},
            **({"execution": execution} if execution else {}),
            **root,
        }
        (tmp_path / "loom.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
        agent: dict[str, Any] = {
            "name": "demo",
            "description": "Calcule.",
            "main": {"model": "FAKE", "system": "Tu calcules."},
        }
        (tmp_path / "agents" / "demo.yaml").write_text(yaml.safe_dump(agent), encoding="utf-8")
        return tmp_path / "loom.yaml"

    return build


# --- Concession ---------------------------------------------------------------


def test_a_run_is_claimed_before_it_is_piloted(durable: ConfigFactory) -> None:
    async def go() -> list[Event]:
        async with Loom.from_config(durable()) as loom:
            await loom.run("demo", QUESTION, session_id=SESSION)
            return await loom.export_session(SESSION)

    events = asyncio.run(go())
    claims = [e for e in events if e.type == "run.claimed"]
    payload = claims[0].payload

    assert len(claims) == 1
    assert isinstance(payload, RunClaimed)
    assert payload.worker_id.startswith("worker-")
    assert payload.lease_until > datetime.now(UTC)


def test_a_second_pilot_is_refused_while_the_lease_lives(durable: ConfigFactory) -> None:
    """Deux workers sur un même run : le journal les départage."""
    path = durable()
    store_path = Path(load_config(path).storage.events.path or "")

    async def go() -> None:
        journal, held = await _held(store_path, worker="worker-ailleurs", seconds=600)
        async with Loom.from_config(path) as loom:
            with pytest.raises(ClaimConflict) as raised:
                await loom.resume(journal.run_id, session_id=SESSION)
        assert raised.value.worker_id == "worker-ailleurs"
        assert raised.value.lease_until == held

    asyncio.run(go())


def test_an_expired_lease_lets_another_pilot_take_over(durable: ConfigFactory) -> None:
    path = durable()
    store_path = Path(load_config(path).storage.events.path or "")

    async def go() -> RunStatus:
        journal, _ = await _held(store_path, worker="worker-mort", seconds=-1)
        async with Loom.from_config(path) as loom:
            return (await loom.resume(journal.run_id, session_id=SESSION)).status

    assert asyncio.run(go()) is RunStatus.COMPLETED


def test_the_same_worker_takes_its_own_run_back(durable: ConfigFactory) -> None:
    """Une livraison en double de la file ne doit pas se refuser elle-même."""
    path = durable()

    async def go() -> RunStatus:
        async with Loom.from_config(path) as loom:
            result = await loom.run("demo", QUESTION, session_id=SESSION)
            # Même instance, donc même concession : la reprise passe.
            return (await loom.resume(result.run_id, session_id=SESSION)).status

    assert asyncio.run(go()) is RunStatus.COMPLETED


def test_a_subrun_takes_no_claim(durable: ConfigFactory) -> None:
    """Le parent pilote son enfant : une concession de plus n'aurait rien à départager."""
    path = durable()

    async def go() -> list[Event]:
        async with Loom.from_config(path) as loom:
            await loom.run("demo", QUESTION, session_id=SESSION)
            return await loom.export_session(SESSION)

    events = asyncio.run(go())
    runs = {e.run_id for e in events if e.type == "run.claimed"}

    assert len(runs) == 1


# --- Arrière-plan -------------------------------------------------------------


def test_a_submitted_run_exists_before_submit_returns(durable: ConfigFactory) -> None:
    """L'identifiant rendu désigne un run qu'on peut déjà interroger."""

    async def go() -> tuple[RunStatus, RunStatus]:
        async with Loom.from_config(durable()) as loom:
            run_id = await loom.submit("demo", QUESTION, session_id=SESSION)
            posee = (await loom.state(run_id, session_id=SESSION)).status
            await loom.drain()
            return posee, (await loom.state(run_id, session_id=SESSION)).status

    posee, finie = asyncio.run(go())
    assert posee is RunStatus.READY_FOR_MODEL
    assert finie is RunStatus.COMPLETED


def test_a_submitted_run_can_be_read_back(durable: ConfigFactory) -> None:
    async def go() -> str:
        async with Loom.from_config(durable()) as loom:
            run_id = await loom.submit("demo", QUESTION, session_id=SESSION)
            await loom.drain()
            return (await loom.result(run_id, session_id=SESSION)).text

    assert asyncio.run(go()) == ANSWER


def test_closing_the_instance_waits_for_a_submitted_run(durable: ConfigFactory) -> None:
    path = durable()

    async def go() -> RunStatus:
        async with Loom.from_config(path) as loom:
            run_id = await loom.submit("demo", QUESTION, session_id=SESSION)
        # `aclose` a attendu la file : le run est fini au journal.
        async with Loom.from_config(path) as second:
            return (await second.state(run_id, session_id=SESSION)).status

    assert asyncio.run(go()) is RunStatus.COMPLETED


# --- Reprise ------------------------------------------------------------------


def test_recover_requeues_a_run_left_in_the_air(durable: ConfigFactory) -> None:
    path = durable()
    store_path = Path(load_config(path).storage.events.path or "")

    async def go() -> tuple[int, RunStatus]:
        journal = RunJournal(agent="demo", session_id=SESSION)
        journal.start(QUESTION)
        store = JsonlEventStore(store_path)
        await store.append(journal.take(), expected_seq=0)
        await store.aclose()
        async with Loom.from_config(path) as loom:
            repris = await loom.recover()
            await loom.drain()
            state = await loom.state(journal.run_id, session_id=SESSION)
        return len(repris), state.status

    combien, status = asyncio.run(go())
    assert combien == 1
    assert status is RunStatus.COMPLETED


def test_recover_leaves_finished_runs_alone(durable: ConfigFactory) -> None:
    path = durable()

    async def go() -> tuple[RunId, ...]:
        async with Loom.from_config(path) as loom:
            await loom.run("demo", QUESTION, session_id=SESSION)
            return await loom.recover()

    assert asyncio.run(go()) == ()


def test_recover_skips_a_run_whose_agent_is_gone(durable: ConfigFactory) -> None:
    path = durable()
    store_path = Path(load_config(path).storage.events.path or "")

    async def go() -> tuple[RunId, ...]:
        journal = RunJournal(agent="disparu", session_id=SESSION)
        journal.start(QUESTION)
        store = JsonlEventStore(store_path)
        await store.append(journal.take(), expected_seq=0)
        await store.aclose()
        async with Loom.from_config(path) as loom:
            return await loom.recover()

    assert asyncio.run(go()) == ()


def test_recover_ignores_a_subrun(durable: ConfigFactory) -> None:
    """Un enfant est repris par l'appel d'outil de son parent, pas par la file."""
    path = durable()
    store_path = Path(load_config(path).storage.events.path or "")

    async def go() -> int:
        parent = RunJournal(agent="demo", session_id=SESSION)
        parent.start(QUESTION)
        parent.model_turn(Message.assistant(ANSWER))
        parent.complete()
        enfant = RunJournal(agent="demo", session_id=SESSION, run_id=None)
        enfant.scope = enfant.scope.model_copy(update={"root_run_id": parent.run_id})
        enfant.start(QUESTION, parent_run_id=parent.run_id)
        store = JsonlEventStore(store_path)
        await store.append([*parent.take(), *enfant.take()], expected_seq=0)
        await store.aclose()
        async with Loom.from_config(path) as loom:
            return len(await loom.recover())

    assert asyncio.run(go()) == 0


def test_recover_reads_past_a_session_marker(durable: ConfigFactory) -> None:
    """Un marqueur de session porte l'identifiant de sa session, pas d'un run."""
    path = durable()
    store_path = Path(load_config(path).storage.events.path or "")

    async def go() -> tuple[RunId, tuple[RunId, ...]]:
        journal = RunJournal(agent="demo", session_id=SESSION)
        journal.start(QUESTION)
        store = JsonlEventStore(store_path)
        await store.append([*journal.take(), _trimmed(SESSION)], expected_seq=0)
        await store.aclose()
        async with Loom.from_config(path) as loom:
            return journal.run_id, await loom.recover()

    run_id, repris = asyncio.run(go())
    assert repris == (run_id,)


def test_recover_can_be_scoped_to_one_session(durable: ConfigFactory) -> None:
    """Le balayage sert au démarrage d'un process ; un appelant peut viser."""
    path = durable()
    store_path = Path(load_config(path).storage.events.path or "")
    ailleurs = SessionId("ailleurs")

    async def go() -> tuple[tuple[RunId, ...], RunId, RunStatus]:
        store = JsonlEventStore(store_path)
        laisses: dict[SessionId, RunId] = {}
        for session in (SESSION, ailleurs):
            journal = RunJournal(agent="demo", session_id=session)
            journal.start(QUESTION)
            await store.append(journal.take(), expected_seq=0)
            laisses[session] = journal.run_id
        await store.aclose()
        async with Loom.from_config(path) as loom:
            repris = await loom.recover(session_id=ailleurs)
            await loom.drain()
            reste = await loom.state(laisses[SESSION], session_id=SESSION)
        return repris, laisses[ailleurs], reste.status

    repris, vise, reste = asyncio.run(go())
    assert repris == (vise,)
    assert reste is RunStatus.READY_FOR_MODEL


@pytest.mark.parametrize("backend", ["memory", "jsonl"])
async def test_recover_skips_a_session_whose_journal_is_unreadable(
    durable: ConfigFactory, caplog: pytest.LogCaptureFixture, backend: str
) -> None:
    """Un journal illisible ne prive pas les autres sessions de leur reprise : sauté, et nommé."""
    config = load_config(durable())
    sessions = {name: SessionId(name) for name in ("avant", "casse", "apres")}
    store = (
        InMemoryEventStore()
        if backend == "memory"
        else JsonlEventStore(Path(config.storage.events.path or ""))
    )
    left: dict[str, RunId] = {}
    for name, session in sessions.items():
        journal = RunJournal(agent="demo", session_id=session)
        journal.start(QUESTION).model_turn(Message.assistant(ANSWER))
        drafts = journal.take()
        if name == "casse" and backend == "memory":
            # Des événements que la projection refuse : pas de ``run.started``.
            drafts = drafts[1:]
        await store.append(drafts, expected_seq=0)
        left[name] = journal.run_id
    if backend == "jsonl":
        # Une ligne du milieu qui n'est pas du JSON ; la dernière reste lisible.
        file = Path(config.storage.events.path or "") / DEFAULT_TENANT / "casse.jsonl"
        lines = file.read_text(encoding="utf-8").splitlines()
        lines[1] = "{pas du json"
        file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    async with Loom(config, store=store) as loom:
        with caplog.at_level(logging.WARNING, logger="loom_ia.access.api"):
            repris = await loom.recover()
        await loom.drain()
        done = [await loom.state(left[n], session_id=sessions[n]) for n in ("avant", "apres")]
        # Visée par son nom, la session illisible dit pourquoi : c'est la réponse à la question.
        with pytest.raises((JournalCorrupted, ProjectionError)):
            await loom.recover(session_id=sessions["casse"])

    assert set(repris) == {left["avant"], left["apres"]}
    assert [state.status for state in done] == [RunStatus.COMPLETED, RunStatus.COMPLETED]
    [warning] = [r.getMessage() for r in caplog.records if "illisible" in r.getMessage()]
    assert "casse" in warning and DEFAULT_TENANT in warning


def test_a_run_left_between_its_final_transition_and_its_end_is_finished_cleanly(
    durable: ConfigFactory,
) -> None:
    """Le worker est mort après ``run.transitioned → completed``, avant ``run.completed``.

    La reprise n'écrit que la clôture : pas de nouvelle concession après l'état
    final, qui rendrait le journal illisible pour toujours.
    """
    path = durable()
    store_path = Path(load_config(path).storage.events.path or "")

    async def go() -> tuple[RunStatus, list[Event]]:
        journal = await _half_closed(store_path, worker="worker-mort", seconds=-1)
        async with Loom.from_config(path) as loom:
            status = (await loom.resume(journal.run_id, session_id=SESSION)).status
            return status, await loom.export_session(SESSION)

    status, events = asyncio.run(go())

    assert status is RunStatus.COMPLETED
    assert events[-1].type == "run.completed"
    # La seule concession du journal est celle du worker mort.
    assert [e.type for e in events].count("run.claimed") == 1
    fold(events, events[0].run_id)


def test_a_half_closed_run_still_waits_for_a_live_lease(durable: ConfigFactory) -> None:
    """Rien à concéder pour une clôture, mais la concession d'un autre pilote vivant tient."""
    path = durable()
    store_path = Path(load_config(path).storage.events.path or "")

    async def go() -> None:
        journal = await _half_closed(store_path, worker="worker-ailleurs", seconds=600)
        async with Loom.from_config(path) as loom:
            with pytest.raises(ClaimConflict):
                await loom.resume(journal.run_id, session_id=SESSION)

    asyncio.run(go())


# --- Un seul pilote par run, dans une instance ----------------------------------


class Porte:
    """Tient l'outil en plein effet jusqu'à ce qu'on l'ouvre, et compte ses départs.

    Elle s'ouvre toujours en sortant du bloc : un test qui échoue ne laisse pas
    la fermeture de l'instance attendre un outil que personne ne libérera.
    """

    def __init__(self) -> None:
        self.entered = asyncio.Queue[None]()
        self.open = asyncio.Event()
        self.runs = 0

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc: object) -> None:
        self.open.set()


def _bloque(porte: Porte) -> object:
    @tool
    async def calculer(expr: str) -> str:
        """Calcule une expression arithmétique, une fois la porte ouverte."""
        porte.runs += 1
        porte.entered.put_nowait(None)
        await porte.open.wait()
        return str(eval(expr))

    return calculer


def _bloquant(demo: ConfigFactory, **root: Any) -> Path:
    """Agent ``demo`` dont l'outil ``calculer`` est celui que le test enregistre."""
    return demo(imports=[], agents=[demo_agent(tools=[{"python": "calculer"}])], **root)


async def _calme() -> None:
    """Laisse la boucle aller au bout de ce qui peut avancer sans événement.

    Les journaux de ces essais sont en mémoire : rien n'y attend le disque, donc
    un pilote qui pouvait repartir l'a fait bien avant la dernière de ces passes.
    """
    for _ in range(200):
        await asyncio.sleep(0)


async def test_resume_waits_for_the_pilot_instead_of_running_the_tool_again(
    demo: ConfigFactory,
) -> None:
    """``submit`` puis ``resume`` : le second attend, au lieu de refaire l'effet."""
    async with Loom.from_config(_bloquant(demo)) as loom, Porte() as porte:
        loom.register("calculer", _bloque(porte))
        run_id = await loom.submit("demo", QUESTION)
        await porte.entered.get()
        second = asyncio.create_task(loom.resume(run_id))
        await _calme()
        assert porte.runs == 1
        porte.open.set()
        result = await second
        await loom.drain()
        state = await loom.state(run_id)

    assert result.status is RunStatus.COMPLETED
    assert state.status is RunStatus.COMPLETED
    assert porte.runs == 1


async def test_recover_leaves_a_run_this_instance_is_piloting(demo: ConfigFactory) -> None:
    """Sans erreur, et sans le compter : la concession est celle de l'instance elle-même."""
    async with Loom.from_config(_bloquant(demo)) as loom, Porte() as porte:
        loom.register("calculer", _bloque(porte))
        direct = asyncio.create_task(loom.run("demo", QUESTION))
        await porte.entered.get()
        repris = await loom.recover()
        await _calme()
        assert porte.runs == 1
        porte.open.set()
        result = await direct
        state = await loom.state(result.run_id)

    assert repris == ()
    assert state.status is RunStatus.COMPLETED
    assert porte.runs == 1


async def test_a_waiting_pilot_takes_over_when_the_first_one_is_interrupted(
    demo: ConfigFactory,
) -> None:
    """Le premier lâche sans finir le run : celui qui attendait le reprend où le journal en est."""
    run_id = new_run_id()
    async with Loom.from_config(_bloquant(demo)) as loom, Porte() as porte:
        loom.register("calculer", _bloque(porte))
        first = asyncio.create_task(loom.run("demo", QUESTION, run_id=run_id))
        await porte.entered.get()
        second = asyncio.create_task(loom.resume(run_id))
        await _calme()
        assert porte.runs == 1
        first.cancel()
        with suppress(asyncio.CancelledError):
            await first
        porte.open.set()
        async with asyncio.timeout(5):
            result = await second
        state = await loom.state(run_id)

    assert result.status is RunStatus.COMPLETED
    assert state.status is RunStatus.COMPLETED


async def test_cancel_stops_the_pilot_and_the_one_waiting_behind_it(demo: ConfigFactory) -> None:
    run_id = new_run_id()
    async with Loom.from_config(_bloquant(demo)) as loom, Porte() as porte:
        loom.register("calculer", _bloque(porte))
        first = asyncio.create_task(loom.run("demo", QUESTION, run_id=run_id))
        await porte.entered.get()
        second = asyncio.create_task(loom.resume(run_id))
        await _calme()
        assert porte.runs == 1
        assert await loom.cancel(run_id, by="denis")
        with suppress(asyncio.CancelledError):
            await first
        result = await second
        events = await loom.events(run_id)
        state = await loom.state(run_id)

    assert result.status is RunStatus.CANCELLED
    assert state.status is RunStatus.CANCELLED
    assert [e.type for e in events].count("run.cancelled") == 1
    assert events[-1].type == "run.cancelled"
    assert porte.runs == 1


async def test_cancel_gives_the_pilots_caller_the_cancelled_run(demo: ConfigFactory) -> None:
    """Comme le pilote en attente : l'appelant de ``run`` reçoit le run, non ``CancelledError``."""
    run_id = new_run_id()
    async with Loom.from_config(_bloquant(demo)) as loom, Porte() as porte:
        loom.register("calculer", _bloque(porte))
        first = asyncio.create_task(loom.run("demo", QUESTION, run_id=run_id))
        await porte.entered.get()
        assert await loom.cancel(run_id, by="denis")
        await asyncio.wait({first}, timeout=5)
        # Ni annulé ni en échec : l'appelant n'a pas été arrêté, c'est son run.
        assert first.done() and not first.cancelled() and first.exception() is None
        result = first.result()
        events = await loom.events(run_id)

    assert result.run_id == run_id and result.status is RunStatus.CANCELLED
    assert [e.type for e in events].count("run.cancelled") == 1
    assert events[-1].type == "run.cancelled"
    assert porte.runs == 1


async def test_cancel_gives_the_resuming_caller_the_cancelled_run(demo: ConfigFactory) -> None:
    async with Loom.from_config(_bloquant(demo)) as loom, Porte() as porte:
        loom.register("calculer", _bloque(porte))
        journal = RunJournal(agent="demo")
        journal.start(QUESTION)
        await loom.store.append(journal.take(), expected_seq=0)
        resuming = asyncio.create_task(loom.resume(journal.run_id))
        await porte.entered.get()
        assert await loom.cancel(journal.run_id)
        await asyncio.wait({resuming}, timeout=5)
        assert resuming.done() and not resuming.cancelled() and resuming.exception() is None
        result = resuming.result()

    assert result.status is RunStatus.CANCELLED


async def test_cancel_ends_the_stream_of_the_pilots_caller_on_run_cancelled(
    demo: ConfigFactory,
) -> None:
    """Le flux se termine après ``run.cancelled``, sans lever ``CancelledError``."""
    run_id = new_run_id()
    seen: list[StreamItem] = []
    async with Loom.from_config(_bloquant(demo)) as loom, Porte() as porte:
        loom.register("calculer", _bloque(porte))

        async def follow() -> None:
            async for item in loom.stream("demo", QUESTION, run_id=run_id):
                seen.append(item)

        streaming = asyncio.create_task(follow())
        await porte.entered.get()
        assert await loom.cancel(run_id, by="denis")
        await asyncio.wait({streaming}, timeout=5)
        assert streaming.done() and not streaming.cancelled() and streaming.exception() is None
        state = await loom.state(run_id)

    events = [item for item in seen if isinstance(item, Event)]
    assert events[-1].type == "run.cancelled" and state.status is RunStatus.CANCELLED
    assert [e.type for e in events].count("run.cancelled") == 1


async def test_cancelling_the_calling_task_still_raises_cancelled_error(
    demo: ConfigFactory,
) -> None:
    """Une annulation venue d'asyncio se propage, et le run reste reprenable.

    La tâche appelante annulée, un délai externe dépassé, le consommateur d'un
    flux arrêté : ``CancelledError`` (ou ``TimeoutError``) comme toujours, et
    rien n'est écrit au journal — seul ``cancel`` écrit ``run.cancelled``.
    """
    called, timed, streamed = new_run_id(), new_run_id(), new_run_id()
    async with Loom.from_config(_bloquant(demo)) as loom, Porte() as porte:
        loom.register("calculer", _bloque(porte))
        calling = asyncio.create_task(loom.run("demo", QUESTION, run_id=called))
        await porte.entered.get()
        calling.cancel()
        with pytest.raises(asyncio.CancelledError):
            await calling

        limit = asyncio.timeout(None)

        async def bounded() -> None:
            async with limit:
                await loom.run("demo", QUESTION, run_id=timed)

        waiting = asyncio.create_task(bounded())
        await porte.entered.get()
        # Le délai est dépassé, l'outil en plein effet.
        limit.reschedule(asyncio.get_running_loop().time())
        with pytest.raises(TimeoutError):
            await waiting

        async def follow() -> None:
            async for _ in loom.stream("demo", QUESTION, run_id=streamed):
                pass

        streaming = asyncio.create_task(follow())
        await porte.entered.get()
        streaming.cancel()
        with pytest.raises(asyncio.CancelledError):
            await streaming

        for run_id in (called, timed, streamed):
            state = await loom.state(run_id)
            types = [e.type for e in await loom.events(run_id)]
            assert not state.finished and "run.cancelled" not in types


async def test_an_interrupted_pilot_keeps_the_interruption_when_cancel_could_not_close_the_run(
    demo: ConfigFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Si l'arrêt n'a pas pu s'écrire, rien n'est clos : l'appelant l'apprend comme avant.

    Le run reste reprenable ; le pilote ne repart pas de lui-même, car on lui a
    demandé de s'arrêter.
    """
    run_id = new_run_id()
    async with Loom.from_config(_bloquant(demo)) as loom, Porte() as porte:
        loom.register("calculer", _bloque(porte))
        write = loom.store.append

        async def refusing(
            drafts: Sequence[EventDraft], *, expected_seq: int | None
        ) -> list[Event]:
            if any(isinstance(d.payload, RunCancelled) for d in drafts):
                raise OSError("disque plein")
            return await write(drafts, expected_seq=expected_seq)

        monkeypatch.setattr(loom.store, "append", refusing)
        first = asyncio.create_task(loom.run("demo", QUESTION, run_id=run_id))
        await porte.entered.get()
        with pytest.raises(OSError, match="disque plein"):
            await loom.cancel(run_id)
        await asyncio.wait({first}, timeout=5)
        assert first.cancelled()
        state = await loom.state(run_id)
        types = [e.type for e in await loom.events(run_id)]
        # Il reste à reprendre : personne ne le pilote plus, et rien ne l'a clos.
        monkeypatch.setattr(loom.store, "append", write)
        porte.open.set()
        resumed = await loom.resume(run_id)

    assert not state.finished and "run.cancelled" not in types
    assert porte.runs == 2
    assert resumed.status is RunStatus.COMPLETED


async def test_cancel_keeps_the_run_until_it_is_closed(
    demo: ConfigFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Le pilote interrompu, l'arrêt pas encore écrit : celui qui attend ne repart pas."""
    run_id = new_run_id()
    async with Loom.from_config(_bloquant(demo)) as loom, Porte() as porte:
        loom.register("calculer", _bloque(porte))
        first = asyncio.create_task(loom.run("demo", QUESTION, run_id=run_id))
        await porte.entered.get()
        second = asyncio.create_task(loom.resume(run_id))
        await _calme()
        reading, go = asyncio.Event(), asyncio.Event()
        read = loom.state

        async def slow_state(
            run_id: RunId, *, session_id: SessionId | None = None, tenant_id: TenantId | None = None
        ) -> RunState:
            reading.set()
            await go.wait()
            return await read(run_id, session_id=session_id, tenant_id=tenant_id)

        monkeypatch.setattr(loom, "state", slow_state)
        cancelling = asyncio.create_task(loom.cancel(run_id))
        await reading.wait()
        await _calme()
        assert porte.runs == 1
        go.set()
        assert await cancelling
        with suppress(asyncio.CancelledError):
            await first
        result = await second

    assert result.status is RunStatus.CANCELLED
    assert porte.runs == 1


async def test_two_clients_with_the_same_run_id_are_piloted_apart(demo: ConfigFactory) -> None:
    """Un même ``run_id`` chez deux clients : aucun n'attend l'autre, et l'arrêt vise le bon."""
    run_id = new_run_id()
    acme, beta = TenantId("acme"), TenantId("beta")
    path = _bloquant(demo, tenants=[{"id": acme}, {"id": beta}])
    async with Loom.from_config(path) as loom, Porte() as porte:
        loom.register("calculer", _bloque(porte))
        runs = {
            tenant: asyncio.create_task(loom.run("demo", QUESTION, run_id=run_id, tenant=tenant))
            for tenant in (acme, beta)
        }
        async with asyncio.timeout(5):
            # Chacun a son outil en cours : aucun n'attend l'autre.
            await porte.entered.get()
            await porte.entered.get()
        assert await loom.cancel(run_id, tenant_id=acme)
        porte.open.set()
        with suppress(asyncio.CancelledError):
            await runs[acme]
        result = await runs[beta]
        stopped = await loom.state(run_id, tenant_id=acme)
        done = await loom.state(run_id, tenant_id=beta)

    assert result.status is RunStatus.COMPLETED
    assert stopped.status is RunStatus.CANCELLED
    assert done.status is RunStatus.COMPLETED


# --- Arrêté ailleurs (6.4) ------------------------------------------------------


class Glissant(JsonlEventStore):
    """Le journal d'un process : juste avant une écriture choisie, un autre process écrit."""

    def __init__(
        self,
        path: Path,
        quand: type,
        glisse: Callable[[RunId], Awaitable[object]],
    ) -> None:
        super().__init__(path)
        self.quand = quand
        self.glisse = glisse
        self.glisse_fait = False

    async def append(
        self, drafts: Sequence[EventDraft], *, expected_seq: int | None
    ) -> list[Event]:
        if not self.glisse_fait and any(isinstance(d.payload, self.quand) for d in drafts):
            self.glisse_fait = True
            await self.glisse(drafts[0].run_id)
        return await super().append(drafts, expected_seq=expected_seq)


async def _arrete_ailleurs(path: Path, quand: type) -> tuple[RunStatus, list[Event]]:
    """Un run piloté par une instance, arrêté par une autre juste avant l'écriture ``quand``."""
    store_path = Path(load_config(path).storage.events.path or "")
    async with Loom.from_config(path) as ailleurs:

        async def arrete(run_id: RunId) -> None:
            assert await ailleurs.cancel(run_id, session_id=SESSION, by="ailleurs")

        pilote = Loom(load_config(path), store=Glissant(store_path, quand, arrete))
        async with pilote:
            result = await pilote.run("demo", QUESTION, session_id=SESSION)
        return result.status, await ailleurs.export_session(SESSION)


def test_a_run_stopped_elsewhere_before_its_claim_does_not_start(durable: ConfigFactory) -> None:
    """La course vue en 6.3c : l'arrêt glissé entre la lecture et la concession."""
    status, events = asyncio.run(_arrete_ailleurs(durable(), RunClaimed))
    types = [e.type for e in events]
    assert status is RunStatus.CANCELLED
    assert types[-1] == "run.cancelled" and "run.claimed" not in types
    assert "model.responded" not in types
    fold(events, events[0].run_id)  # le journal se relit


def test_a_run_stopped_elsewhere_while_piloted_stops_at_its_next_write(
    durable: ConfigFactory,
) -> None:
    status, events = asyncio.run(_arrete_ailleurs(durable(), ModelResponded))
    types = [e.type for e in events]
    assert status is RunStatus.CANCELLED
    # Concession prise, modèle appelé — sa réponse n'est pas écrite après l'arrêt.
    assert types.index("run.claimed") < types.index("run.cancelled") == len(types) - 1
    assert "model.responded" not in types
    cancelled = events[-1].payload
    assert isinstance(cancelled, RunCancelled) and cancelled.by == "ailleurs"
    fold(events, events[0].run_id)


def test_a_stop_that_arrives_after_the_end_says_already_finished(
    durable: ConfigFactory,
) -> None:
    path = durable()
    store_path = Path(load_config(path).storage.events.path or "")

    async def go() -> tuple[bool, list[Event]]:
        journal, _ = await _held(store_path, worker="worker-mort", seconds=-1)
        async with Loom.from_config(path) as pilote:

            async def finit(run_id: RunId) -> None:
                await pilote.resume(run_id, session_id=SESSION)

            arreteur = Loom(load_config(path), store=Glissant(store_path, RunCancelled, finit))
            async with arreteur:
                arrete = await arreteur.cancel(journal.run_id, session_id=SESSION)
            return arrete, await pilote.export_session(SESSION)

    arrete, events = asyncio.run(go())
    assert arrete is False
    assert events[-1].type == "run.completed"
    assert "run.cancelled" not in [e.type for e in events]


ENVOI = '''
from loom_ia.tools import tool


@tool
def envoyer(destinataire: str) -> str:
    """Envoie."""
    return f"envoyé à {destinataire}"
'''


def test_an_approval_after_a_stop_elsewhere_decides_nothing(
    durable: ConfigFactory, tmp_path: Path
) -> None:
    """Le run en attente est arrêté ailleurs pendant qu'on l'approuve : rien n'est accordé."""
    script = [{"tool_calls": [{"name": "envoyer", "arguments": {"destinataire": "martin"}}]}]
    path = durable(imports=["outils_arret"])
    (tmp_path / "outils_arret.py").write_text(ENVOI, encoding="utf-8")
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    config["models"][0]["params"] = {"script": script}
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    agent = yaml.safe_load((tmp_path / "agents" / "demo.yaml").read_text(encoding="utf-8"))
    agent["tools"] = [{"python": "envoyer", "approval": "always"}]
    (tmp_path / "agents" / "demo.yaml").write_text(yaml.safe_dump(agent), encoding="utf-8")
    store_path = Path(load_config(path).storage.events.path or "")

    async def go() -> tuple[tuple[str, ...], list[Event]]:
        async with Loom.from_config(path) as ailleurs:
            run = await ailleurs.run("demo", "Envoie.", session_id=SESSION)
            assert run.status is RunStatus.PAUSED

            async def arrete(run_id: RunId) -> None:
                assert await ailleurs.cancel(run_id, session_id=SESSION, by="ailleurs")

            approbateur = Loom(
                load_config(path), store=Glissant(store_path, ApprovalGranted, arrete)
            )
            async with approbateur:
                accordes = await approbateur.approve(run.run_id, session_id=SESSION)
            return accordes, await ailleurs.export_session(SESSION)

    accordes, events = asyncio.run(go())
    types = [e.type for e in events]
    assert accordes == ()
    assert types[-1] == "run.cancelled" and "approval.granted" not in types


# --- Utilitaires --------------------------------------------------------------


async def _held(store_path: Path, *, worker: str, seconds: float) -> tuple[RunJournal, datetime]:
    """Journal d'un run en plan, avec une concession prise par un autre worker."""
    journal = RunJournal(agent="demo", session_id=SESSION)
    journal.start(QUESTION)
    until = datetime.now(UTC) + timedelta(seconds=seconds)
    drafts: list[EventDraft] = [
        *journal.take(),
        journal.scope.draft(RunClaimed(worker_id=worker, lease_until=until)),
    ]
    store = JsonlEventStore(store_path)
    await store.append(drafts, expected_seq=0)
    await store.aclose()
    return journal, until


async def _half_closed(store_path: Path, *, worker: str, seconds: float) -> RunJournal:
    """Journal d'un run dont le pilote est mort entre sa transition finale et sa clôture."""
    journal = RunJournal(agent="demo", session_id=SESSION)
    journal.start(QUESTION)
    until = datetime.now(UTC) + timedelta(seconds=seconds)
    drafts: list[EventDraft] = [
        *journal.take(),
        journal.scope.draft(RunClaimed(worker_id=worker, lease_until=until)),
    ]
    journal.model_turn(Message.assistant(ANSWER)).transition(
        RunStatus.COMPLETED, cause="model.responded"
    )
    store = JsonlEventStore(store_path)
    await store.append([*drafts, *journal.take()], expected_seq=0)
    await store.aclose()
    return journal


def _trimmed(session_id: SessionId) -> EventDraft:
    """Marqueur de coupe de sécurité, écrit hors de tout run."""
    scope = RunScope(
        tenant_id=DEFAULT_TENANT,
        session_id=session_id,
        run_id=RunId(session_id),
        root_run_id=RunId(session_id),
        agent="",
    )
    return scope.draft(SessionTrimmed(up_to_seq=1, dropped=1, reason="essai"))
