# SPDX-License-Identifier: Apache-2.0
"""Outils en thread qui dépassent leur délai (B-2, ``docs/moteur-delai-thread-analyse.md``)."""

import asyncio
import logging
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
import yaml
from pydantic import JsonValue
from test_call_order import atelier, awaiting

from loom_ia.access.api import Loom
from loom_ia.config import ConfigError
from loom_ia.core.events import ApprovalRequested, ToolCompleted
from loom_ia.core.model import PendingCall, RunState, SideEffects, TenantId, UnknownState
from loom_ia.core.ports import ToolContext
from loom_ia.core.tool_threads import ToolThreads
from loom_ia.engine import ToolExecutor
from loom_ia.engine.executor import (
    LATE_EFFECT,
    NOT_RUN,
    NOT_RUN_SINCE,
    NOT_STARTED,
    STOPPED_EFFECT,
    AnyTool,
    ToolEvent,
)
from loom_ia.tools import tool


class Disque:
    """Un fichier en mémoire, écrit par un thread qu'on libère à la main."""

    def __init__(self) -> None:
        self.texte = "v1"
        self.libre = threading.Event()
        self.parti = threading.Event()


def outils(
    disque: Disque, *, on_unknown: UnknownState = "error", effects: SideEffects = "reversible"
) -> list[AnyTool]:
    @tool(name="ecrire", side_effects=effects, timeout=0.05, on_unknown=on_unknown)
    def ecrire(texte: str) -> str:
        """Écrit, une fois libéré (au plus 2 s)."""
        disque.parti.set()
        disque.libre.wait(2)
        disque.texte = texte
        return "écrit"

    @tool(side_effects="none")
    def lire() -> str:
        """Lit."""
        return disque.texte

    @tool(side_effects="reversible", timeout=0.05)
    async def ecrire_async(texte: str) -> str:
        """Écrit après 0,3 s, sans thread."""
        await asyncio.sleep(0.3)
        disque.texte = texte
        return "écrit"

    return [ecrire, lire, ecrire_async]


def appel(call_id: str, name: str, **arguments: JsonValue) -> PendingCall:
    return PendingCall(call_id=call_id, name=name, arguments=arguments)


async def collect(executor: ToolExecutor, *calls: PendingCall) -> list[ToolEvent]:
    async with asyncio.timeout(5):
        return [e async for e in executor.run_batch(awaiting(*calls))]


def results(events: list[ToolEvent]) -> dict[str, str]:
    return {e.call_id: e.output.as_text for e in events if isinstance(e, ToolCompleted)}


def libere_dans(disque: Disque, secondes: float) -> None:
    threading.Timer(secondes, disque.libre.set).start()


async def tour(executor: ToolExecutor, run: RunState, *calls: PendingCall) -> list[ToolEvent]:
    """Un lot de plus dans le même run : le tour suivant du modèle."""
    async with asyncio.timeout(5):
        state = run.model_copy(update={"pending_calls": calls})
        return [e async for e in executor.run_batch(state)]


async def test_a_late_write_last_of_its_batch_is_awaited_by_the_next_batch_of_its_run() -> None:
    disque = Disque()
    libere_dans(disque, 0.3)
    executor = ToolExecutor(outils(disque))
    run = awaiting()

    premier = await tour(executor, run, appel("w", "ecrire", texte="v2"))
    second = await tour(executor, run, appel("r", "lire"))

    assert results(premier) == {"w": LATE_EFFECT.format(limit=0.05)}
    assert results(second) == {"r": "v2"}


async def test_past_the_grace_the_next_batch_of_the_run_is_returned_without_running() -> None:
    disque = Disque()
    executor = ToolExecutor(outils(disque), grace=0.1)
    run = awaiting()

    await tour(executor, run, appel("w", "ecrire", texte="v2"))
    second = await tour(executor, run, appel("r", "lire"))
    disque.libre.set()
    await asyncio.sleep(0.05)
    troisieme = await tour(executor, run, appel("r2", "lire"))

    assert results(second) == {"r": NOT_RUN_SINCE.format(tool="ecrire")}
    assert results(troisieme) == {"r2": "v2"}


async def test_a_late_write_holds_only_its_own_run() -> None:
    disque = Disque()
    executor = ToolExecutor(outils(disque), grace=5)

    await tour(executor, awaiting(), appel("w", "ecrire", texte="v2"))
    autre = await tour(executor, awaiting(), appel("r", "lire"))
    disque.libre.set()

    assert results(autre) == {"r": "v1"}


async def test_a_call_whose_deadline_passes_before_it_gets_a_thread_did_nothing() -> None:
    parti = threading.Event()

    @tool(side_effects="none", timeout=5)
    def long_calcul() -> str:
        """Occupe le seul thread."""
        time.sleep(0.3)
        return "fini"

    @tool(side_effects="reversible", timeout=0.1, on_unknown="pause")
    def ecrire() -> str:
        """Écrit, instantanément."""
        parti.set()
        return "écrit"

    pool = ToolThreads(1)
    un = ToolExecutor([long_calcul, ecrire], threads=pool)
    autre = ToolExecutor([long_calcul, ecrire], threads=pool)
    occupe = asyncio.create_task(collect(un, appel("c", "long_calcul")))
    await asyncio.sleep(0.05)

    events = await collect(autre, appel("w", "ecrire"))
    await occupe
    await asyncio.sleep(0.05)

    assert results(events) == {"w": NOT_STARTED.format(limit=0.1)}
    assert not any(isinstance(e, ApprovalRequested) for e in events)
    assert not parti.is_set()
    pool.close(0)


async def test_a_sync_tool_runs_in_the_tools_pool() -> None:
    @tool(side_effects="none")
    def ou() -> str:
        """Le nom du thread."""
        return threading.current_thread().name

    events = await collect(ToolExecutor([ou]), appel("c", "ou"))

    assert results(events)["c"].startswith("loom-outil")


async def test_a_write_past_its_deadline_is_of_unknown_effect_and_the_read_waits_for_it() -> None:
    disque = Disque()
    libere_dans(disque, 0.3)
    executor = ToolExecutor(outils(disque))

    events = await collect(executor, appel("w", "ecrire", texte="v2"), appel("r", "lire"))

    assert results(events) == {"w": LATE_EFFECT.format(limit=0.05), "r": "v2"}


async def test_past_the_grace_the_next_calls_are_returned_without_running() -> None:
    disque = Disque()
    executor = ToolExecutor(outils(disque), grace=0.1)

    events = await collect(
        executor, appel("w", "ecrire", texte="v2"), appel("r", "lire"), appel("r2", "lire")
    )

    assert results(events) == {
        "r": NOT_RUN.format(tool="ecrire"),
        "r2": NOT_RUN.format(tool="ecrire"),
        "w": LATE_EFFECT.format(limit=0.05),
    }
    disque.libre.set()


async def test_a_late_write_a_human_must_check_pauses_the_run() -> None:
    disque = Disque()
    libere_dans(disque, 0.2)
    executor = ToolExecutor(outils(disque, on_unknown="pause"))

    events = await collect(executor, appel("w", "ecrire", texte="v2"), appel("r", "lire"))

    [request] = [e for e in events if isinstance(e, ApprovalRequested)]
    assert request.reason == LATE_EFFECT.format(limit=0.05)
    assert "r" not in results(events)


async def test_a_read_past_its_deadline_keeps_its_message_and_holds_nothing() -> None:
    disque = Disque()
    executor = ToolExecutor(outils(disque, effects="none"))

    started = time.perf_counter()
    events = await collect(executor, appel("w", "ecrire", texte="v2"), appel("r", "lire"))

    assert results(events) == {"w": "Délai dépassé : pas de réponse en 0.05 s.", "r": "v1"}
    assert time.perf_counter() - started < 1
    disque.libre.set()


async def test_an_async_write_past_its_deadline_is_of_unknown_effect_too() -> None:
    """Annulé à son ``await``, il n'écrit pas ; mais rien ne dit où il en était."""
    disque = Disque()
    executor = ToolExecutor(outils(disque))

    events = await collect(executor, appel("w", "ecrire_async", texte="v2"), appel("r", "lire"))

    assert results(events) == {"w": STOPPED_EFFECT.format(limit=0.05), "r": "v1"}
    await asyncio.sleep(0.4)
    assert disque.texte == "v1"


async def test_blocked_tool_threads_leave_the_asyncio_pool_free() -> None:
    """Le journal JSONL et les fichiers passent par le pool d'asyncio : il reste libre."""
    disque = Disque()
    executor = ToolExecutor(outils(disque, effects="none"))
    calls = [appel(f"w{n}", "ecrire", texte="v2") for n in range(40)]

    await collect(executor, *calls)
    started = time.perf_counter()
    await asyncio.to_thread(lambda: None)

    assert time.perf_counter() - started < 0.5
    disque.libre.set()


async def test_a_late_end_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    disque = Disque()
    libere_dans(disque, 0.1)
    executor = ToolExecutor(outils(disque, effects="none"))

    with caplog.at_level(logging.WARNING, logger="loom_ia.core.tool_threads"):
        await collect(executor, appel("w", "ecrire", texte="v2"))
        await asyncio.sleep(0.3)

    [record] = caplog.records
    assert record.getMessage().startswith("Outil ecrire : travail fini ")


async def test_cancelling_the_batch_leaves_the_thread_to_its_end(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Un arrêt du run (``cancel``, délai du run) n'arrête pas le thread : sa fin se dit."""
    disque = Disque()
    executor = ToolExecutor(outils(disque))

    async def drain() -> None:
        async for _ in executor.run_batch(awaiting(appel("w", "ecrire", texte="v2"))):
            pass

    with caplog.at_level(logging.WARNING, logger="loom_ia.core.tool_threads"):
        batch = asyncio.create_task(drain())
        await asyncio.to_thread(disque.parti.wait, 2)
        batch.cancel()
        with pytest.raises(asyncio.CancelledError):
            await batch
        disque.libre.set()
        await asyncio.sleep(0.2)

    assert disque.texte == "v2"
    assert len(caplog.records) == 1


def config_avec(tmp_path: Path, **tools: object) -> Path:
    path = atelier(tmp_path, order="effects", approval=None)
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    config["execution"]["tools"] |= tools
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    return path


def test_each_loom_instance_has_its_own_pool(tmp_path: Path) -> None:
    """D-4 : la taille et la grâce viennent de la config, et deux instances ne partagent rien."""
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    first = config_avec(tmp_path / "a", threads=3, grace=2.5)
    second = config_avec(tmp_path / "b", threads=5)

    async def go() -> tuple[ToolExecutor, ToolExecutor]:
        async with Loom.from_config(first) as a, Loom.from_config(second) as b:
            return (
                a.context("demo", TenantId("default")).tools,
                b.context("demo", TenantId("default")).tools,
            )

    a, b = asyncio.run(go())
    assert (a.grace, a.threads.size, a.threads.grace) == (2.5, 3, 2.5)
    assert (b.grace, b.threads.size) == (30.0, 5)
    assert a.threads is not b.threads


async def test_the_tool_is_told_when_the_engine_stops_waiting() -> None:
    """D-8 : ``ToolContext.cancelled`` se lève au délai ; l'outil qui le regarde s'arrête."""
    etapes: list[int] = []

    @tool(side_effects="reversible", timeout=0.1)
    def copier(context: ToolContext) -> str:
        """Copie dix fichiers, un toutes les 50 ms, en regardant le signal."""
        assert context.cancelled is not None
        for n in range(10):
            if context.cancelled.is_set():
                return "arrêté"
            time.sleep(0.05)
            etapes.append(n)
        return "copié"

    events = await collect(ToolExecutor([copier]), appel("c", "copier"))
    await asyncio.sleep(0.2)

    # Le thread a fini tôt, de lui-même : le travail était encore suivi au délai.
    assert results(events)["c"] == LATE_EFFECT.format(limit=0.1)
    assert 1 <= len(etapes) <= 3


def test_closing_waits_for_the_tools_at_most_the_grace(tmp_path: Path) -> None:
    """D-5 : la fermeture dit ce qu'elle attend, attend au plus la grâce, puis s'arrête."""
    script = tmp_path / "ferme.py"
    script.write_text(
        """
import asyncio, logging, sys, time
from loom_ia.core.tool_threads import ToolThreads, CallThreads, tracking, in_thread

logging.basicConfig(level=logging.WARNING, format="%(message)s")

async def main() -> None:
    pool = ToolThreads(2, grace=0.5)
    with tracking(CallThreads("long", pool)):
        task = asyncio.ensure_future(in_thread(time.sleep, 30))
    await asyncio.sleep(0.2)
    task.cancel()
    await pool.aclose()

started = time.monotonic()
asyncio.run(main())
print(f"sortie {time.monotonic() - started:.1f}", file=sys.stderr)
""",
        encoding="utf-8",
    )
    started = time.monotonic()
    done = subprocess.run(
        [sys.executable, str(script)], capture_output=True, text=True, timeout=20, check=True
    )
    elapsed = time.monotonic() - started

    # Le process s'arrête sans attendre les 30 s du thread, qui est un démon.
    assert elapsed < 10
    lines = done.stderr.splitlines()
    assert lines[0].startswith("Fermeture : attend la fin de 1 outil(s) en arrière-plan (long")
    assert lines[1].startswith("Fermeture sans attendre 1 outil(s) encore au travail (long)")


def test_a_pausing_tool_with_a_deadline_needs_a_durable_journal(tmp_path: Path) -> None:
    """D-10 : un délai dépassé met le run en pause ; un journal en mémoire le perdrait."""
    path = atelier(tmp_path, order="effects", approval=None)
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    config["storage"] = {"events": {"backend": "memory"}}
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    source = (tmp_path / "outils_ordre.py").read_text(encoding="utf-8")
    (tmp_path / "outils_ordre.py").write_text(
        source.replace(
            '@tool(side_effects="reversible", idempotent=True)',
            '@tool(side_effects="reversible", idempotent=True, on_unknown="pause")',
        ),
        encoding="utf-8",
    )

    async def go() -> None:
        async with Loom.from_config(path) as loom:
            loom.context("demo", TenantId("default"))

    with pytest.raises(ConfigError, match="on_unknown: pause, avec un délai"):
        asyncio.run(go())
