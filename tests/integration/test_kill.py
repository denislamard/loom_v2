# SPDX-License-Identifier: Apache-2.0
"""Reprise après un vrai ``kill -9`` (J4.2b, H3).

Un sous-process pilote un run puis se fait tuer sans préavis, en plein appel
d'outil. Le journal, lui, a tout gardé : un autre process reprend le run,
rejoue l'appel resté en suspens et le mène au bout.

Le test montre aussi à quoi sert la concession. Juste après la mise à mort,
le run porte encore le bail du mort : personne ne le reprend. Ce n'est
qu'une fois le bail expiré qu'un autre pilote s'en saisit. C'est le prix à
payer pour qu'un worker simplement lent ne se fasse pas doubler.
"""

import asyncio
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest
import yaml

from loom_ia.access.api import Loom
from loom_ia.adapters.stores import JsonlEventStore
from loom_ia.core.model import DEFAULT_TENANT, RunStatus, SessionId

pytestmark = pytest.mark.integration

SESSION = SessionId("atelier")
QUESTION = "Combien font 12 fois 7, plus 3 ?"
BAIL = 3.0
# L'outil dort le temps qu'il faut pour se faire tuer en plein appel — la
# mise à mort suit de quelques millisecondes le ``tool.called`` au journal.
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

PILOTE = """
import asyncio
import sys

from loom_ia.access.api import Loom

CONFIG = sys.argv[1]
SESSION = sys.argv[2]


async def main() -> None:
    async with Loom.from_config(CONFIG) as loom:
        run_id = await loom.submit("demo", "QUESTION", session_id=SESSION)
        print(run_id, flush=True)
        await loom.drain()


asyncio.run(main())
"""


@pytest.fixture
def atelier(tmp_path: Path) -> Path:
    (tmp_path / "agents").mkdir()
    (tmp_path / "outil_dormeur.py").write_text(OUTILS, encoding="utf-8")
    (tmp_path / "pilote.py").write_text(PILOTE.replace("QUESTION", QUESTION), encoding="utf-8")
    config: dict[str, Any] = {
        "version": 1,
        "imports": ["outil_dormeur"],
        "models": [
            {
                "id": "FAKE",
                "sdk": "fake",
                "model": "fake-1",
                "params": {
                    "script": [
                        {"text": "Je dors.", "tool_calls": [{"name": "dormir", "arguments": {}}]},
                        {"text": "87."},
                    ]
                },
            }
        ],
        "storage": {"events": {"backend": "jsonl", "path": "data"}},
        "telemetry": {"logging": {"level": "CRITICAL"}},
        "execution": {"lease": BAIL},
    }
    (tmp_path / "loom.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    agent: dict[str, Any] = {
        "name": "demo",
        "description": "Dort, puis calcule.",
        "main": {"model": "FAKE", "system": "Tu calcules."},
        "tools": [{"python": "dormir", "side_effects": "none", "idempotent": True}],
    }
    (tmp_path / "agents" / "demo.yaml").write_text(yaml.safe_dump(agent), encoding="utf-8")
    return tmp_path / "loom.yaml"


def test_a_killed_run_is_taken_back_once_its_lease_expires(atelier: Path, tmp_path: Path) -> None:
    child = subprocess.Popen(
        [sys.executable, str(tmp_path / "pilote.py"), str(atelier), str(SESSION)],
        stdout=subprocess.PIPE,
        text=True,
        cwd=tmp_path,
    )
    try:
        assert child.stdout is not None
        run_id = child.stdout.readline().strip()
        assert run_id, "le pilote n'a pas annoncé son run"
        _wait_for(tmp_path / "data", "tool.called")
        os.kill(child.pid, signal.SIGKILL)
    finally:
        child.wait(timeout=30)

    assert child.returncode == -signal.SIGKILL
    events = asyncio.run(_journal(tmp_path / "data"))
    assert [e.type for e in events][-1] == "tool.called"
    assert not [e for e in events if e.type == "run.completed"]
    mort = _worker(events)

    # Le bail du mort court encore : personne ne touche au run.
    assert asyncio.run(_take_back(atelier, run_id)) is not RunStatus.COMPLETED

    time.sleep(BAIL)
    assert asyncio.run(_take_back(atelier, run_id)) is RunStatus.COMPLETED

    # Un autre pilote a pris la concession, rejoué l'appel resté en suspens
    # — l'outil est déclaré idempotent — et mené le run au bout.
    repris = asyncio.run(_journal(tmp_path / "data"))
    assert _worker(repris) != mort
    assert [e.type for e in repris].count("tool.called") == 2
    assert [e.type for e in repris][-1] == "run.completed"


async def _journal(root: Path) -> list[Any]:
    store = JsonlEventStore(root)
    try:
        return await store.read(DEFAULT_TENANT, SESSION)
    finally:
        await store.aclose()


async def _take_back(config: Path, run_id: str) -> RunStatus:
    """Une autre instance reprend ce qu'elle trouve, puis rend l'état du run."""
    async with Loom.from_config(config) as loom:
        await loom.recover()
        await loom.drain()
        return (await loom.state(run_id, session_id=SESSION)).status  # type: ignore[arg-type]


def _worker(events: list[Any]) -> str:
    """Le worker de la dernière concession prise sur le run."""
    return [e.payload.worker_id for e in events if e.type == "run.claimed"][-1]


def _wait_for(root: Path, kind: str, limite: float = 30.0) -> None:
    """Attend qu'un événement de ce type soit au journal du sous-process."""
    fin = time.monotonic() + limite
    fichier = root / DEFAULT_TENANT / f"{SESSION}.jsonl"
    while time.monotonic() < fin:
        if fichier.is_file():
            lignes = fichier.read_text(encoding="utf-8").splitlines()
            if any(json.loads(ligne).get("type") == kind for ligne in lignes if ligne):
                return
        time.sleep(0.05)
    raise TimeoutError(f"aucun {kind} au journal après {limite:g} s")
