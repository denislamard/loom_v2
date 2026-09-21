# SPDX-License-Identifier: Apache-2.0
"""Exécution durable (J4.2b) : concession, arrière-plan, reprise au démarrage."""

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml

from loom_ia.access.api import Loom
from loom_ia.adapters.stores import JsonlEventStore
from loom_ia.config import load_config
from loom_ia.core.events import Event, EventDraft, RunClaimed, RunScope, SessionTrimmed
from loom_ia.core.model import DEFAULT_TENANT, Message, RunId, RunStatus, SessionId
from loom_ia.engine import ClaimConflict
from loom_ia.testing import RunJournal

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
