# SPDX-License-Identifier: Apache-2.0
"""Exécution durable (J4.2b) : concession, arrière-plan, reprise au démarrage.

Et l'arrêt écrit par un autre process (6.4) : un run arrêté ailleurs juste
avant sa concession, ou en plein pilotage, ne repart pas — le pilote s'arrête
à sa prochaine écriture, sans rien écrire après la clôture ; un arrêt qui
arrive après la fin rend « déjà fini ».
"""

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml

from loom_ia.access.api import Loom
from loom_ia.adapters.stores import JsonlEventStore
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
from loom_ia.core.model import DEFAULT_TENANT, Message, RunId, RunStatus, SessionId
from loom_ia.core.projections import fold
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
