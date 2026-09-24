# SPDX-License-Identifier: Apache-2.0
"""Ce que seul un vrai bus peut montrer : un process qui suit l'autre (J5.3c).

``LOOM_TEST_POSTGRES`` et ``LOOM_TEST_REDIS`` désignent les services ; chaque
essai est joué sur les deux, et sauté pour celui qui manque.

Le second essai est celui de la phase : un run piloté **dans un autre
process**, suivi en SSE depuis celui-ci. Le flux est ouvert sur la position du
journal au moment où le run dort dans son outil, si bien que tout ce qui arrive
ensuite n'a pu venir que du bus — sans lui, l'abonné attendrait la fin de son
flux sans jamais voir `run.completed`.
"""

import asyncio
import json
import os
import subprocess
import sys
import time
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any

import httpx2
import pytest
import yaml

from loom_ia.access.api import Loom
from loom_ia.adapters.stores import InMemoryEventStore, NotifyingEventStore
from loom_ia.config import load_config
from loom_ia.core.events import Event, EventDraft
from loom_ia.core.model import DEFAULT_TENANT, Message, SessionId, ToolOutput
from loom_ia.runtime import create_bus
from loom_ia.testing import RunJournal, tool_call_message

pytestmark = pytest.mark.integration

SESSION = SessionId("atelier")
QUESTION = "Combien font 12 fois 7, plus 3 ?"
VARIABLE = "LOOM_BUS_URL"
# L'outil dort le temps que l'abonné ouvre son flux.
SOMMEIL = 2.0
# De quoi laisser un abonnement s'établir chez le service. Large : il s'agit de
# millisecondes en pratique, et une nouvelle publiée avant l'abonnement serait
# perdue — un bus ne garde rien.
ABONNEMENT = 1.0

OUTILS = f'''
import asyncio

from loom_ia.tools import tool


@tool
async def dormir(secondes: float = {SOMMEIL}) -> str:
    """Attend, pour laisser le temps d'ouvrir un flux."""
    await asyncio.sleep(secondes)
    return "réveillé"
'''

PILOTE = """
import asyncio
import sys

from loom_ia.access.api import Loom

CONFIG, SESSION = sys.argv[1], sys.argv[2]


async def main() -> None:
    async with Loom.from_config(CONFIG) as loom:
        run = await loom.submit("demo", "QUESTION", session_id=SESSION)
        print(run, flush=True)
        await loom.drain()


asyncio.run(main())
"""


@pytest.fixture(params=["postgres", "redis"])
def bus_storage(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Bloc ``storage.bus`` d'un vrai bus, sa variable posée ; saute sinon."""
    kind = str(request.param)
    fixtures = {"postgres": ("postgres_dsn", "dsn_env"), "redis": ("redis_url", "url_env")}
    fixture, key = fixtures[kind]
    monkeypatch.setenv(VARIABLE, str(request.getfixturevalue(fixture)))
    return {"backend": kind, key: VARIABLE}


def write_config(root: Path, bus: dict[str, Any], *, tool: bool) -> Path:
    """Config partagée par les deux process : journal JSONL et bus réel."""
    (root / "agents").mkdir(exist_ok=True)
    script: list[dict[str, Any]] = (
        [
            {"text": "Je dors.", "tool_calls": [{"name": "dormir", "arguments": {}}]},
            {"text": "87."},
        ]
        if tool
        else [{"text": "87."}]
    )
    config: dict[str, Any] = {
        "version": 1,
        "models": [{"id": "FAKE", "sdk": "fake", "model": "fake-1", "params": {"script": script}}],
        "storage": {"events": {"backend": "jsonl", "path": "data"}, "bus": bus},
        "telemetry": {"logging": {"level": "CRITICAL"}},
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


def drafts() -> list[EventDraft]:
    journal = RunJournal(session_id=SESSION)
    journal.start(QUESTION)
    journal.model_turn(tool_call_message(("c1", "calculer", {"expr": "12*7+3"})))
    journal.tool_results({"c1": ToolOutput.text("87")})
    journal.model_turn(Message.assistant("87."))
    journal.complete()
    return journal.take()


# --- Le bus traverse ----------------------------------------------------------


async def test_a_subscriber_sees_what_another_instance_writes(
    bus_storage: dict[str, Any], tmp_path: Path
) -> None:
    """Deux journaux notifiants, deux bus, un seul journal : la forme de deux process.

    Chacun a **sa** connexion au bus, comme deux process l'auraient : ce qui
    passe fait un vrai aller-retour par le service.
    """
    config = load_config(write_config(tmp_path, bus_storage, tool=False))
    ici_bus, ailleurs_bus = create_bus(config), create_bus(config)
    assert ici_bus is not None and ailleurs_bus is not None
    inner = InMemoryEventStore()
    ici = NotifyingEventStore(inner, bus=ici_bus, source="ici")
    ailleurs = NotifyingEventStore(inner, bus=ailleurs_bus, source="ailleurs")
    vus: list[Event] = []
    try:
        with ici.listen(vus.append):
            async with _following(ici):
                written = await ailleurs.append(drafts(), expected_seq=0)
                assert await _until(lambda: len(vus) >= len(written), limit=15.0)
        assert [e.event_id for e in vus] == [e.event_id for e in written]
    finally:
        await ici_bus.aclose()
        await ailleurs_bus.aclose()


# --- Le SSE suit un run d'ailleurs --------------------------------------------


def test_sse_follows_a_run_piloted_in_another_process(
    bus_storage: dict[str, Any], tmp_path: Path
) -> None:
    # Essai synchrone, comme les autres essais à sous-process : le flux SSE,
    # lui, est lu dans sa propre boucle.
    path = write_config(tmp_path, bus_storage, tool=True)
    (tmp_path / "pilote.py").write_text(PILOTE.replace("QUESTION", QUESTION), encoding="utf-8")
    child = subprocess.Popen(
        [sys.executable, str(tmp_path / "pilote.py"), str(path), str(SESSION)],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        cwd=tmp_path,
        env={**os.environ},
    )
    try:
        assert child.stdout is not None
        run_id = child.stdout.readline().strip()
        assert run_id, "le pilote n'a pas annoncé son run"
        # Le run dort dans son outil : on ouvre le flux sur la position du
        # moment, donc tout ce qui suivra n'aura pu venir que du bus.
        assert _waited(lambda: "tool.called" in _types(tmp_path / "data"), limit=30.0)
        depuis = len(_types(tmp_path / "data"))
        recus = asyncio.run(_streamed(path, run_id, depuis))
        assert "run.completed" in recus, f"rien n'est venu du bus : {recus}"
    finally:
        child.wait(timeout=60)
    assert child.returncode == 0


# --- Outillage ----------------------------------------------------------------


@asynccontextmanager
async def _following(store: NotifyingEventStore) -> AsyncGenerator[None]:
    """Fait tourner le suivi du bus le temps du bloc, abonnement établi."""
    task = asyncio.create_task(store.follow())
    await asyncio.sleep(ABONNEMENT)
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


async def _streamed(config: Path, run_id: str, after: int) -> list[str]:
    """Ouvre l'application REST de ce process et lit le flux du run jusqu'à sa fin."""
    from loom_ia.access.http import create_app

    async with Loom.from_config(config) as loom:
        transport = httpx2.ASGITransport(app=create_app(loom))
        async with httpx2.AsyncClient(transport=transport, base_url="http://essai") as http:
            url = f"/v1/runs/{run_id}/events?session_id={SESSION}&after_seq={after}"
            return await _sse(http, url, limit=SOMMEIL + 15.0)


def _waited(condition: Callable[[], bool], *, limit: float) -> bool:
    fin = time.monotonic() + limit
    while time.monotonic() < fin:
        if condition():
            return True
        time.sleep(0.05)
    return condition()


async def _until(condition: Callable[[], bool], *, limit: float) -> bool:
    started = time.monotonic()
    while time.monotonic() - started < limit:
        if condition():
            return True
        await asyncio.sleep(0.05)
    return condition()


def _types(root: Path) -> list[str]:
    path = root / DEFAULT_TENANT / f"{SESSION}.jsonl"
    if not path.is_file():
        return []
    return [
        str(json.loads(line).get("type"))
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


async def _sse(http: httpx2.AsyncClient, url: str, *, limit: float) -> list[str]:
    """Types d'événements lus sur un flux SSE, jusqu'à sa fin — ou jusqu'au délai.

    Le délai est **la** précaution de cet essai : un flux SSE qui n'attend
    plus rien ne se ferme pas de lui-même, il envoie des battements de cœur.
    Sans bus, l'essai attendrait donc pour toujours au lieu d'échouer ; borné,
    il rend ce qu'il a vu, et l'assertion dit ce qui manque.
    """
    seen: list[str] = []
    with suppress(TimeoutError):
        async with asyncio.timeout(limit), http.stream("GET", url, timeout=limit) as response:
            assert response.status_code == 200
            async for line in response.aiter_lines():
                field, _, value = line.partition(":")
                if field.strip() == "event":
                    seen.append(value.strip())
    return seen
