# SPDX-License-Identifier: Apache-2.0
"""Cycle de vie d'un run (J4.2a) : délai maximal, annulation, reprise."""

import asyncio
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import Any

import pytest
import yaml

from loom_ia.access.api import Loom
from loom_ia.adapters.stores import JsonlEventStore
from loom_ia.config import ConfigError, load_config
from loom_ia.core.events import Event, EventDraft, RunCancelled, RunFailed
from loom_ia.core.model import Message, RunStatus, SessionId, new_run_id
from loom_ia.core.projections import fold, history
from loom_ia.testing import RunJournal

type ConfigFactory = Callable[..., Path]

SESSION = SessionId("atelier")
QUESTION = "Combien font 12 fois 7, plus 3 ?"

LENT = '''
import asyncio

from loom_ia.tools import tool


@tool
async def attendre(secondes: float) -> str:
    """Attend, pour laisser un délai se dépasser."""
    await asyncio.sleep(secondes)
    return "fini"
'''


@pytest.fixture
def patient(tmp_path: Path) -> ConfigFactory:
    """Agent dont le seul outil dort : de quoi dépasser un délai en plein effet."""

    def build(*, timeout: float | None = None, attente: float = 5.0, **root: Any) -> Path:
        (tmp_path / "agents").mkdir(exist_ok=True)
        (tmp_path / "outils_lents.py").write_text(LENT, encoding="utf-8")
        config: dict[str, Any] = {
            "version": 1,
            "imports": ["outils_lents"],
            "models": [
                {
                    "id": "FAKE",
                    "sdk": "fake",
                    "model": "fake-1",
                    "params": {
                        "script": [
                            {
                                "text": "J'attends.",
                                "tool_calls": [
                                    {"name": "attendre", "arguments": {"secondes": attente}}
                                ],
                            },
                            {"text": "Voilà."},
                        ]
                    },
                }
            ],
            "storage": {"events": {"backend": "jsonl", "path": "data"}},
            "telemetry": {"logging": {"level": "CRITICAL"}},
            **root,
        }
        (tmp_path / "loom.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
        agent: dict[str, Any] = {
            "name": "demo",
            "description": "Attend.",
            "main": {"model": "FAKE", "system": "Tu attends."},
            "tools": [{"python": "attendre"}],
        }
        if timeout is not None:
            agent["timeout"] = timeout
        (tmp_path / "agents" / "demo.yaml").write_text(yaml.safe_dump(agent), encoding="utf-8")
        return tmp_path / "loom.yaml"

    return build


# --- Temps de pilotage cumulé -------------------------------------------------


def test_the_active_time_adds_up_the_steps() -> None:
    journal = RunJournal(session_id=SESSION, step_ms=250.0)
    journal.start(QUESTION)
    journal.model_turn(Message.assistant("87."))
    journal.complete()
    events = _numbered(journal.take())

    state = fold(events, journal.run_id)

    # Une étape de modèle, une clôture : seules les étapes terminées comptent.
    assert state.active_ms == 250.0
    assert state.status is RunStatus.COMPLETED


def test_the_clock_does_not_count_what_is_not_piloting() -> None:
    """Deux runs d'une même session : chacun ne compte que ses propres étapes."""
    first = RunJournal(session_id=SESSION, step_ms=100.0)
    first.start(QUESTION)
    first.model_turn(Message.assistant("87."))
    first.complete()
    second = RunJournal(session_id=SESSION, step_ms=100.0)
    second.start("Et encore ?")
    second.model_turn(Message.assistant("90."))
    second.complete()
    events = _numbered([*first.take(), *second.take()])

    assert fold(events, second.run_id).active_ms == 100.0


# --- Délai maximal ------------------------------------------------------------


def test_a_run_that_runs_out_of_time_fails_with_a_timeout(patient: ConfigFactory) -> None:
    path = patient(timeout=0.2, attente=5.0)

    async def go() -> list[Event]:
        async with Loom.from_config(path) as loom:
            result = await loom.run("demo", QUESTION, session_id=SESSION)
            assert result.status is RunStatus.FAILED
            assert result.error_type == "timeout"
            return await loom.export_session(SESSION)

    events = asyncio.run(go())
    failed = next(e.payload for e in events if e.type == "run.failed")

    assert isinstance(failed, RunFailed)
    assert failed.error_type == "timeout"
    assert "délai maximal de 0.2 s dépassé" in failed.error
    assert "interrompue en cours" in failed.error
    # L'étape a été coupée en plein appel d'outil : elle n'a pas de fin.
    starts = [e for e in events if e.type == "step.started"]
    ends = [e for e in events if e.type == "step.completed"]
    assert len(starts) == len(ends) + 1


def test_a_run_without_a_deadline_takes_its_time(patient: ConfigFactory) -> None:
    path = patient(attente=0.01)

    async def go() -> RunStatus:
        async with Loom.from_config(path) as loom:
            return (await loom.run("demo", QUESTION, session_id=SESSION)).status

    assert asyncio.run(go()) is RunStatus.COMPLETED


def test_a_run_already_over_budget_fails_before_its_next_step(patient: ConfigFactory) -> None:
    """Le délai borne le temps déjà passé : une reprise le relit du journal."""
    path = patient(timeout=1.0, attente=0.01)
    config = load_config(path)
    store_path = Path(config.storage.events.path or "")

    async def go() -> Event:
        # Journal d'un run laissé en plan après 2 s de pilotage, soit deux
        # fois son délai : la reprise doit échouer sans rien tenter de plus.
        journal = RunJournal(agent="demo", session_id=SESSION, step_ms=2_000.0)
        journal.start(QUESTION)
        journal.model_turn(Message.assistant("87."))
        store = JsonlEventStore(store_path)
        await store.append(journal.take(), expected_seq=0)
        await store.aclose()
        async with Loom.from_config(path) as loom:
            result = await loom.resume(journal.run_id, session_id=SESSION)
            assert result.error_type == "timeout"
            events = await loom.export_session(SESSION)
        return next(e for e in events if e.type == "run.failed")

    failed = asyncio.run(go()).payload
    assert isinstance(failed, RunFailed)
    assert "2.0 s de pilotage déjà écoulées" in failed.error


def test_a_timed_out_run_stays_closed(patient: ConfigFactory) -> None:
    """Constat, pas un choix : ``run.failed`` clôt le run, délai relevé ou non.

    Un run clos ne se rouvre pas — ``drive`` rend la main dès que le journal
    porte sa clôture. Relever le délai et repartir demanderait de rouvrir un
    run fermé, ce qui n'existe pas (à trancher, cf. avancement J4.2a).
    """
    path = patient(timeout=0.2, attente=0.5)

    async def go() -> RunStatus:
        async with Loom.from_config(path) as loom:
            first = await loom.run("demo", QUESTION, session_id=SESSION)
            assert first.error_type == "timeout"
            run_id = first.run_id
        raised = patient(timeout=30.0, attente=0.5)
        async with Loom.from_config(raised) as loom:
            return (await loom.resume(run_id, session_id=SESSION)).status

    assert asyncio.run(go()) is RunStatus.FAILED


# --- Annulation ---------------------------------------------------------------


def test_cancelling_a_running_run_closes_it(patient: ConfigFactory) -> None:
    path = patient(attente=5.0)

    async def go() -> list[Event]:
        run_id = new_run_id()
        async with Loom.from_config(path) as loom:
            run = asyncio.create_task(loom.run("demo", QUESTION, session_id=SESSION, run_id=run_id))
            await asyncio.sleep(0.15)
            assert await loom.cancel(run_id, session_id=SESSION, by="denis")
            run.cancel()
            with suppress(asyncio.CancelledError):
                await run
            return await loom.export_session(SESSION)

    events = asyncio.run(go())
    cancelled = next(e for e in events if e.type == "run.cancelled")
    payload = cancelled.payload

    assert isinstance(payload, RunCancelled)
    assert payload.reason == "requested"
    assert payload.by == "denis"
    assert cancelled.status == "warning"
    assert fold(events, cancelled.run_id).status is RunStatus.CANCELLED


def test_cancelling_a_finished_run_changes_nothing(patient: ConfigFactory) -> None:
    path = patient(attente=0.01)

    async def go() -> bool:
        async with Loom.from_config(path) as loom:
            result = await loom.run("demo", QUESTION, session_id=SESSION)
            return await loom.cancel(result.run_id, session_id=SESSION)

    assert asyncio.run(go()) is False


def test_a_run_left_in_the_air_is_cancelled_from_its_journal(patient: ConfigFactory) -> None:
    """Un run qu'aucune instance ne pilote se clôt quand même."""
    path = patient(attente=0.01)
    store_path = Path(load_config(path).storage.events.path or "")

    async def go() -> list[Event]:
        journal = RunJournal(agent="demo", session_id=SESSION)
        journal.start(QUESTION)
        journal.model_turn(Message.assistant("87."))
        store = JsonlEventStore(store_path)
        await store.append(journal.take(), expected_seq=0)
        await store.aclose()
        async with Loom.from_config(path) as loom:
            assert await loom.cancel(journal.run_id, session_id=SESSION)
            return await loom.export_session(SESSION)

    events = asyncio.run(go())
    assert [e.type for e in events[-2:]] == ["run.transitioned", "run.cancelled"]


def test_a_cancelled_run_is_not_a_turn_of_the_session(patient: ConfigFactory) -> None:
    """Un échange laissé à mi-chemin ne doit pas peupler le tour suivant."""
    path = patient(attente=0.01)
    store_path = Path(load_config(path).storage.events.path or "")

    async def go() -> list[Message]:
        journal = RunJournal(agent="demo", session_id=SESSION)
        journal.start(QUESTION)
        journal.model_turn(Message.assistant("87."))
        store = JsonlEventStore(store_path)
        await store.append(journal.take(), expected_seq=0)
        await store.aclose()
        async with Loom.from_config(path) as loom:
            await loom.cancel(journal.run_id, session_id=SESSION)
            return history(await loom.export_session(SESSION))

    assert asyncio.run(go()) == []


# --- Config -------------------------------------------------------------------


def test_the_deadline_is_read_from_the_agent(patient: ConfigFactory) -> None:
    config = load_config(patient(timeout=12.5))

    assert config.agents[0].timeout == 12.5


def test_a_deadline_must_be_positive(patient: ConfigFactory) -> None:
    with pytest.raises(ConfigError, match="timeout"):
        load_config(patient(timeout=0))


# --- Utilitaires --------------------------------------------------------------


def _numbered(drafts: list[EventDraft]) -> list[Event]:
    return [draft.to_event(seq) for seq, draft in enumerate(drafts, start=1)]
