# SPDX-License-Identifier: Apache-2.0
"""Ce que seul un vrai courtier peut montrer : la file, et un run qui change de worker.

``LOOM_TEST_RABBITMQ`` désigne le courtier ; sans elle, tout est sauté.

Deux essais portent le cœur de la phase, et il faut les deux :

- celui du **bail** montre le mécanisme isolé — un travail redélivré trop tôt
  est reposé pour l'après-bail, et c'est ce qui fait qu'un run passe d'un
  worker à l'autre sans qu'on s'en occupe ;
- celui du **kill** montre la scène en vrai — un `loom worker` tué en plein
  appel d'outil, un autre qui mène le run au bout. Deux chemins y mènent (la
  redélivrance du courtier et la reprise au démarrage du second worker) et
  l'essai ne dit pas lequel a gagné : il dit que le run finit.
"""

import asyncio
import json
import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml

# Le module importe l'adaptateur : sans l'extra, tout l'essai se saute.
pytest.importorskip("aio_pika", reason="extra 'rabbitmq' absent")

from loom_ia.access.api import Loom
from loom_ia.adapters.queue.rabbitmq import RabbitMqTaskQueue
from loom_ia.adapters.stores import JsonlEventStore
from loom_ia.core.events import Event, EventDraft, RunClaimed
from loom_ia.core.model import DEFAULT_TENANT, RunId, SessionId, TenantId
from loom_ia.core.ports import Job, JobKind
from loom_ia.testing import RunJournal

pytestmark = pytest.mark.integration

SESSION = SessionId("atelier")
QUESTION = "Combien font 12 fois 7, plus 3 ?"
VARIABLE = "LOOM_RABBITMQ_URL"
# Bail assez long pour qu'un travail redélivré tombe franchement dedans, même
# sur une machine chargée.
BAIL = 6.0
# L'outil dort le temps qu'il faut pour se faire tuer en plein appel.
SOMMEIL = 3.0

OUTILS = f'''
import asyncio

from loom_ia.tools import tool


@tool
async def dormir(secondes: float = {SOMMEIL}) -> str:
    """Attend, pour se faire tuer en plein appel."""
    await asyncio.sleep(secondes)
    return "réveillé"
'''

WORKER = """
import sys

from loom_ia.access.cli import main

sys.exit(main(["--config", sys.argv[1], "worker"]))
"""


def config_file(root: Path, *, tool: bool) -> Path:
    """Config à journal JSONL et file RabbitMQ : deux process la partagent."""
    (root / "agents").mkdir(exist_ok=True)
    models: list[dict[str, Any]] = [
        {
            "id": "FAKE",
            "sdk": "fake",
            "model": "fake-1",
            "params": {
                "script": (
                    [
                        {"text": "Je dors.", "tool_calls": [{"name": "dormir", "arguments": {}}]},
                        {"text": "87."},
                    ]
                    if tool
                    else [{"text": "87."}]
                )
            },
        }
    ]
    config: dict[str, Any] = {
        "version": 1,
        "models": models,
        "storage": {
            "events": {"backend": "jsonl", "path": "data"},
            "queue": {"backend": "rabbitmq", "url_env": VARIABLE},
        },
        "telemetry": {"logging": {"level": "CRITICAL"}},
        "execution": {"lease": BAIL},
    }
    agent: dict[str, Any] = {
        "name": "demo",
        "description": "Calcule.",
        "main": {"model": "FAKE", "system": "Tu calcules."},
    }
    if tool:
        (root / "outil_dormeur.py").write_text(OUTILS, encoding="utf-8")
        config["imports"] = ["outil_dormeur"]
        agent["tools"] = [{"python": "dormir", "side_effects": "none", "idempotent": True}]
    (root / "loom.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    (root / "agents" / "demo.yaml").write_text(yaml.safe_dump(agent), encoding="utf-8")
    return root / "loom.yaml"


def job_of(kind: JobKind, run: str | None = None) -> Job:
    """Un travail d'essai : le client et la session importent peu, le type si."""
    return Job(
        kind=kind,
        tenant_id=TenantId("t"),
        session_id=SessionId("s"),
        run_id=RunId(run) if run is not None else None,
    )


async def waited(condition: Callable[[], bool], *, limit: float) -> bool:
    """Attend qu'une condition tienne, et dit si elle a fini par tenir."""
    started = time.monotonic()
    while time.monotonic() - started < limit:
        if condition():
            return True
        await asyncio.sleep(0.05)
    return condition()


# --- La file ------------------------------------------------------------------


async def test_a_published_job_reaches_a_consumer(rabbitmq_url: str) -> None:
    seen: list[Job] = []
    queue = RabbitMqTaskQueue(rabbitmq_url, {"run": lambda job: _remember(seen, job)})
    try:
        await queue.submit(job_of("run", "r-1"))
        task = asyncio.create_task(queue.serve())
        assert await waited(lambda: len(seen) == 1, limit=10.0)
        await queue.stop()
        await task
        assert seen[0].run_id == "r-1"
    finally:
        await queue.aclose()


async def test_a_delayed_job_waits_its_turn(rabbitmq_url: str) -> None:
    """RabbitMQ n'a pas de délai : c'est la file d'attente à durée de vie qui l'imite."""
    seen: list[Job] = []
    queue = RabbitMqTaskQueue(rabbitmq_url, {"compaction": lambda job: _remember(seen, job)})
    try:
        await queue.submit(job_of("compaction"), delay=1.0)
        task = asyncio.create_task(queue.serve())
        assert not await waited(lambda: bool(seen), limit=0.4), "arrivé avant l'heure"
        assert await waited(lambda: bool(seen), limit=10.0), "jamais arrivé"
        await queue.stop()
        await task
    finally:
        await queue.aclose()


async def test_the_key_is_the_job_id(rabbitmq_url: str) -> None:
    """Le courtier ne dédoublonne pas : la clé rend au moins le même identifiant."""
    queue = RabbitMqTaskQueue(rabbitmq_url)
    try:
        job = job_of("run", "r-2")
        first = await queue.submit(job, key="run:r-2")
        again = await queue.submit(job, key="run:r-2")
        assert first == again == "run:r-2"
        assert await queue.submit(job) != first
    finally:
        await queue.aclose()


async def test_a_message_that_is_not_a_job_is_dropped_not_replayed(rabbitmq_url: str) -> None:
    """Un message que loom ne comprendra jamais est acquitté : sinon il tourne en rond."""
    import aio_pika

    from loom_ia.adapters.queue.rabbitmq import WORK_QUEUE

    seen: list[Job] = []
    queue = RabbitMqTaskQueue(rabbitmq_url, {"run": lambda job: _remember(seen, job)})
    try:
        # La file est déclarée par celui qui publie : sans quoi le courtier
        # jetterait le message sans un mot.
        await queue.submit(job_of("run", "r-3"))
        connection = await aio_pika.connect_robust(rabbitmq_url)
        try:
            channel = await connection.channel()
            await channel.default_exchange.publish(
                aio_pika.Message(b"ceci n'est pas un travail"), routing_key=WORK_QUEUE
            )
        finally:
            await connection.close()
        task = asyncio.create_task(queue.serve())
        assert await waited(lambda: len(seen) == 1, limit=10.0)
        # Le message illisible a été consommé lui aussi : rien ne revient.
        assert not await waited(lambda: len(seen) > 1, limit=1.0)
        await queue.stop()
        await task
    finally:
        await queue.aclose()


async def test_a_job_that_fails_is_not_replayed_for_ever(rabbitmq_url: str) -> None:
    tries: list[Job] = []

    async def fails(job: Job) -> None:
        tries.append(job)
        raise RuntimeError("le traitement casse")

    queue = RabbitMqTaskQueue(rabbitmq_url, {"run": fails})
    try:
        await queue.submit(job_of("run", "r-4"))
        task = asyncio.create_task(queue.serve())
        assert await waited(lambda: len(tries) == 1, limit=10.0)
        assert not await waited(lambda: len(tries) > 1, limit=1.5), "rejoué sans fin"
        await queue.stop()
        await task
    finally:
        await queue.aclose()


async def test_a_job_in_flight_goes_to_the_end_after_a_stop(rabbitmq_url: str) -> None:
    """Un arrêt demandé ne coupe pas la tâche en cours : elle serait redélivrée pour rien."""
    done: list[str] = []

    async def slow(job: Job) -> None:
        await asyncio.sleep(0.5)
        done.append("fini")

    queue = RabbitMqTaskQueue(rabbitmq_url, {"run": slow})
    try:
        await queue.submit(job_of("run", "r-5"))
        task = asyncio.create_task(queue.serve())
        await asyncio.sleep(0.2)
        await queue.stop()
        await task
        assert done == ["fini"]
    finally:
        await queue.aclose()


async def _remember(seen: list[Job], job: Job) -> None:
    seen.append(job)


# --- Le bail ------------------------------------------------------------------


async def test_a_job_whose_run_is_held_comes_back_after_the_lease(
    rabbitmq_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Le travail redélivré arrive trop tôt : il est reposé pour l'après-bail.

    C'est le mécanisme qui fait qu'un run passe d'un worker mort à un vivant.
    Ici le mort n'a jamais existé : sa concession est écrite à la main, avec un
    bail court, et personne ne la renouvellera.
    """
    monkeypatch.setenv(VARIABLE, rabbitmq_url)
    monkeypatch.chdir(tmp_path)
    path = config_file(tmp_path, tool=False)
    bail = 2.0
    journal = await _held(tmp_path / "data", worker="worker-mort", seconds=bail)
    started = time.monotonic()
    async with Loom.from_config(path) as loom:
        # C'est la reprise qui met le run en file : le travail part vers le
        # courtier, et le bail du mort le fera attendre.
        assert await loom.recover() == (journal.run_id,)
        task = asyncio.create_task(loom.work())
        finished = await waited(
            lambda: "run.completed" in types_at(tmp_path / "data"), limit=bail + 10.0
        )
        await loom.stop_work()
        await task
    assert finished, "le run n'a jamais été mené au bout"
    # Pas avant la fin du bail : c'est la concession qui a tenu le second pilote.
    assert time.monotonic() - started >= bail


async def _held(store_path: Path, *, worker: str, seconds: float) -> RunJournal:
    """Journal d'un run en plan, avec une concession prise par un autre worker."""
    journal = RunJournal(agent="demo", session_id=SESSION)
    journal.start(QUESTION)
    drafts: list[EventDraft] = [
        *journal.take(),
        journal.scope.draft(
            RunClaimed(worker_id=worker, lease_until=datetime.now(UTC) + timedelta(seconds=seconds))
        ),
    ]
    store = JsonlEventStore(store_path)
    try:
        await store.append(drafts, expected_seq=0)
    finally:
        await store.aclose()
    return journal


def types_at(root: Path) -> list[str]:
    """Les types du journal, lus à même le fichier JSONL.

    Lu ligne à ligne plutôt que par le store : la lecture a lieu pendant qu'une
    boucle tourne, et le store est asynchrone.
    """
    path = root / DEFAULT_TENANT / f"{SESSION}.jsonl"
    if not path.is_file():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    return [str(json.loads(line).get("type")) for line in lines if line]


async def _read(root: Path) -> list[Event]:
    store = JsonlEventStore(root)
    try:
        return await store.read(DEFAULT_TENANT, SESSION)
    finally:
        await store.aclose()


# --- La scène en vrai ---------------------------------------------------------


def test_a_killed_worker_hands_its_run_over(
    rabbitmq_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un `loom worker` tué en plein appel d'outil, un autre qui mène le run au bout.

    C'est le scénario du jalon. Deux chemins y mènent — le courtier redélivre
    le travail que le mort n'a pas acquitté, et le second worker reprend au
    démarrage ce qu'il trouve en plan — et l'essai ne dit pas lequel a gagné :
    il dit que personne n'a eu à s'en occuper.
    """
    monkeypatch.setenv(VARIABLE, rabbitmq_url)
    path = config_file(tmp_path, tool=True)
    (tmp_path / "worker.py").write_text(WORKER, encoding="utf-8")
    journal = tmp_path / "data"

    premier = _worker_process(tmp_path, path)
    try:
        assert _listening(premier), "le premier worker n'a pas pris l'écoute"
        run_id = asyncio.run(_submit(path))
        assert _until(lambda: "tool.called" in types_at(journal), limit=30.0), "run jamais parti"
        os.kill(premier.pid, signal.SIGKILL)
    finally:
        premier.wait(timeout=30)
    assert premier.returncode == -signal.SIGKILL
    assert "run.completed" not in types_at(journal)
    mort = _claims(journal)[-1]

    second = _worker_process(tmp_path, path)
    try:
        assert _listening(second), "le second worker n'a pas pris l'écoute"
        fini = _until(lambda: "run.completed" in types_at(journal), limit=BAIL + 60.0)
    finally:
        second.send_signal(signal.SIGTERM)
        second.wait(timeout=30)

    assert fini, "le run n'a pas été repris"
    events = asyncio.run(_read(journal))
    assert [e.type for e in events][-1] == "run.completed"
    # Un autre pilote a pris la concession et rejoué l'appel resté en suspens.
    assert _claims(journal)[-1] != mort
    assert types_at(journal).count("tool.called") == 2
    assert run_id in {e.run_id for e in events}


def _worker_process(root: Path, config: Path) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [sys.executable, str(root / "worker.py"), str(config)],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        cwd=root,
        env={**os.environ},
    )


def _listening(child: subprocess.Popen[str], limit: float = 60.0) -> bool:
    """Attend la ligne d'écoute du worker : elle dit que la file est branchée."""
    assert child.stdout is not None
    fin = time.monotonic() + limit
    while time.monotonic() < fin:
        line = child.stdout.readline()
        if not line:
            return False
        if "En écoute" in line:
            return True
    return False


async def _submit(config: Path) -> RunId:
    async with Loom.from_config(config) as loom:
        return await loom.submit("demo", QUESTION, session_id=SESSION)


def _claims(root: Path) -> list[str]:
    """Les workers qui ont pris la concession, dans l'ordre."""
    path = root / DEFAULT_TENANT / f"{SESSION}.jsonl"
    found: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line:
            continue
        event: Any = json.loads(line)
        if event.get("type") == "run.claimed":
            found.append(str(event["payload"]["worker_id"]))
    return found


def _until(condition: Callable[[], bool], *, limit: float) -> bool:
    fin = time.monotonic() + limit
    while time.monotonic() < fin:
        if condition():
            return True
        time.sleep(0.05)
    return condition()
