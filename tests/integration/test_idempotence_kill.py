# SPDX-License-Identifier: Apache-2.0
"""Un ``kill -9`` après l'effet : la relance ne part qu'une fois (J4.4a, #18, #49).

Un sous-process pilote un run, l'outil envoie la relance — une ligne écrite
dans un fichier, qui tient lieu de courriel parti —, puis le process se fait
tuer avant que son résultat soit au journal. L'appel n'a donc pas de
``tool.completed`` : à la reprise, il repart.

C'est là que se joue l'idempotence. L'effet, lui, a bien été mémorisé
(``idempotency.recorded``, écrit par le magasin avant tout le reste) ; l'appel
rejoué le retrouve et rend le résultat d'alors au lieu de renvoyer la
relance. La boîte ne contient qu'une ligne.

La fenêtre entre l'effet et son résultat est élargie par une politique
``after_tool`` qui relit lentement. Ce n'est pas un artifice : entre un
résultat et son écriture, il y a les politiques, les juges et le déport des
gros résultats — de quoi mourir dedans.
"""

import asyncio
import json
import os
import signal
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml

from loom_ia.access.api import Loom
from loom_ia.adapters.stores import JsonlEventStore
from loom_ia.core.model import DEFAULT_TENANT, RunStatus, SessionId

pytestmark = pytest.mark.integration

SESSION = SessionId("atelier")
DEMANDE = "Relance madame Martin."
CLIENTE = "mme.martin@example.com"
# Assez long pour que la première reprise tombe franchement dans le bail.
BAIL = 6.0
MARGE = 0.3
# Relecture du résultat : le temps de se faire tuer entre l'effet et le
# ``tool.completed`` de l'appel.
RELECTURE = 3.0

OUTILS = '''
import os
from pathlib import Path

from loom_ia.tools import idempotent, tool


@idempotent
@tool(side_effects="irreversible")
async def envoyer(destinataire: str) -> str:
    """Envoie la relance au client."""
    with Path(os.environ["BOITE"]).open("a", encoding="utf-8") as boite:
        boite.write(destinataire + "\\n")
    return f"relance envoyée à {destinataire}"
'''

POLITIQUE = f'''
import asyncio

from loom_ia.core.model import CONTINUE, AfterTool, Decision
from loom_ia.policies import policy


@policy(points=["after_tool"], decisions=["continue"])
async def relecture(subject: AfterTool) -> Decision:
    """Relit le résultat, lentement."""
    await asyncio.sleep({RELECTURE})
    return CONTINUE
'''

PILOTE = """
import asyncio
import sys

from loom_ia.access.api import Loom

CONFIG = sys.argv[1]
SESSION = sys.argv[2]


async def main() -> None:
    async with Loom.from_config(CONFIG) as loom:
        run_id = await loom.submit("demo", "DEMANDE", session_id=SESSION)
        print(run_id, flush=True)
        await loom.drain()


asyncio.run(main())
"""


@pytest.fixture
def poste(tmp_path: Path) -> Path:
    (tmp_path / "agents").mkdir()
    (tmp_path / "outil_poste.py").write_text(OUTILS, encoding="utf-8")
    (tmp_path / "politique_lente.py").write_text(POLITIQUE, encoding="utf-8")
    (tmp_path / "pilote.py").write_text(PILOTE.replace("DEMANDE", DEMANDE), encoding="utf-8")
    config: dict[str, Any] = {
        "version": 1,
        "imports": ["outil_poste", "politique_lente"],
        "models": [
            {
                "id": "FAKE",
                "sdk": "fake",
                "model": "fake-1",
                "params": {
                    "script": [
                        {
                            "text": "J'envoie.",
                            "tool_calls": [
                                {"name": "envoyer", "arguments": {"destinataire": CLIENTE}}
                            ],
                        },
                        {"text": "C'est fait."},
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
        "description": "Relance les clients.",
        "main": {"model": "FAKE", "system": "Tu relances."},
        "tools": [{"python": "envoyer"}],
        "policies": [{"hook": "relecture", "timeout": None}],
    }
    (tmp_path / "agents" / "demo.yaml").write_text(yaml.safe_dump(agent), encoding="utf-8")
    return tmp_path / "loom.yaml"


def test_a_killed_run_does_not_send_the_reminder_twice(poste: Path, tmp_path: Path) -> None:
    boite = tmp_path / "boite.txt"
    environ = {**os.environ, "BOITE": str(boite)}
    child = subprocess.Popen(
        [sys.executable, str(tmp_path / "pilote.py"), str(poste), str(SESSION)],
        stdout=subprocess.PIPE,
        text=True,
        cwd=tmp_path,
        env=environ,
    )
    try:
        assert child.stdout is not None
        run_id = child.stdout.readline().strip()
        assert run_id, "le pilote n'a pas annoncé son run"
        # L'effet a eu lieu et le magasin l'a noté ; le résultat, lui, est
        # encore en relecture.
        _wait_for(tmp_path / "data", "idempotency.recorded")
        os.kill(child.pid, signal.SIGKILL)
    finally:
        child.wait(timeout=30)

    assert child.returncode == -signal.SIGKILL
    assert boite.read_text(encoding="utf-8").splitlines() == [CLIENTE]
    events = asyncio.run(_journal(tmp_path / "data"))
    assert not [e for e in events if e.type == "tool.completed"]

    bail = _lease(events)
    assert datetime.now(UTC) < bail, "le bail avait déjà expiré : essai non concluant"
    time.sleep(max((bail - datetime.now(UTC)).total_seconds(), 0.0) + MARGE)

    os.environ["BOITE"] = str(boite)
    try:
        assert asyncio.run(_take_back(poste, run_id)) is RunStatus.COMPLETED
    finally:
        del os.environ["BOITE"]

    # L'appel est reparti — et la relance n'est pas repartie avec lui.
    repris = asyncio.run(_journal(tmp_path / "data"))
    types = [e.type for e in repris]
    assert types.count("tool.called") == 2
    assert types.count("idempotency.recorded") == 1
    assert types[-1] == "run.completed"
    assert boite.read_text(encoding="utf-8").splitlines() == [CLIENTE]


async def _journal(root: Path) -> list[Any]:
    store = JsonlEventStore(root)
    try:
        return await store.read(DEFAULT_TENANT, SESSION)
    finally:
        await store.aclose()


async def _take_back(config: Path, run_id: str) -> RunStatus:
    """Une autre instance reprend le run resté en plan, puis rend son état."""
    async with Loom.from_config(config) as loom:
        await loom.recover(session_id=SESSION)
        await loom.drain()
        return (await loom.state(run_id, session_id=SESSION)).status  # type: ignore[arg-type]


def _lease(events: list[Any]) -> datetime:
    """Jusqu'à quand court la concession du mort."""
    return [e.payload.lease_until for e in events if e.type == "run.claimed"][-1]


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
