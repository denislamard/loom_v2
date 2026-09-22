# SPDX-License-Identifier: Apache-2.0
"""Deux process, une seule relance : la clé métier tient entre eux (J4.4b, #49).

Deux conversations distinctes, dans deux process distincts, demandent la même
chose : relancer le devis D-2026-042. Rien dans leurs journaux ne les relie —
ce sont deux sessions, deux runs, deux instances. Ce qui les relie est le
magasin d'idempotence, une base SQLite partagée, et la clé que l'outil en
tire : ``relance:D-2026-042``.

Résultat : **une** relance part. C'est ce qu'aucune clé technique ne sait
faire, puisqu'elle ne vaut que pour un appel, et qu'aucun magasin en mémoire
ne saurait faire non plus, puisqu'il ne sort pas de son process.

Les deux process partent en même temps, donc la course a lieu pour de vrai :
celui qui perd trouve la clé prise (« exécution en cours ») ou le résultat
déjà mémorisé. Dans les deux cas, il n'envoie rien.
"""

import json
import os
import subprocess
import sys
from importlib.util import find_spec
from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(find_spec("aiosqlite") is None, reason="extra 'sqlite' absent"),
]

DEMANDE = "Relance le client du devis D-2026-042."
KEYS = "keys.db"

OUTILS = '''
import os
from importlib.util import find_spec
from pathlib import Path

from loom_ia.tools import idempotent, tool


@idempotent(key=lambda a: f"relance:{a['devis']}")
@tool(side_effects="irreversible")
async def envoyer_relance(devis: str) -> str:
    """Envoie la relance du devis au client."""
    with Path(os.environ["BOITE"]).open("a", encoding="utf-8") as boite:
        boite.write(devis + "\\n")
    return f"relance du devis {devis} envoyée"
'''

PILOTE = """
import asyncio
import json
import sys

from loom_ia.access.api import Loom

CONFIG, SESSION = sys.argv[1], sys.argv[2]


async def main() -> None:
    async with Loom.from_config(CONFIG) as loom:
        result = await loom.run("demo", "DEMANDE", session_id=SESSION)
        print(json.dumps({"status": str(result.status), "texte": result.text or ""}), flush=True)


asyncio.run(main())
"""


@pytest.fixture
def atelier(tmp_path: Path) -> Path:
    (tmp_path / "agents").mkdir()
    (tmp_path / "outil_poste.py").write_text(OUTILS, encoding="utf-8")
    (tmp_path / "pilote.py").write_text(PILOTE.replace("DEMANDE", DEMANDE), encoding="utf-8")
    config: dict[str, Any] = {
        "version": 1,
        "imports": ["outil_poste"],
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
                                {
                                    "name": "envoyer_relance",
                                    "arguments": {"devis": "D-2026-042"},
                                }
                            ],
                        },
                        {"text": "C'est fait."},
                    ]
                },
            }
        ],
        "storage": {
            "events": {"backend": "jsonl", "path": "data"},
            "idempotency": {"backend": "sqlite", "path": KEYS},
        },
        "telemetry": {"logging": {"level": "CRITICAL"}},
    }
    (tmp_path / "loom.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    agent: dict[str, Any] = {
        "name": "demo",
        "description": "Relance les clients.",
        "main": {"model": "FAKE", "system": "Tu relances."},
        "tools": [{"python": "envoyer_relance"}],
    }
    (tmp_path / "agents" / "demo.yaml").write_text(yaml.safe_dump(agent), encoding="utf-8")
    return tmp_path / "loom.yaml"


def test_two_processes_send_the_reminder_once(atelier: Path, tmp_path: Path) -> None:
    boite = tmp_path / "boite.txt"
    environ = {**os.environ, "BOITE": str(boite)}
    pilotes = [
        subprocess.Popen(
            [sys.executable, str(tmp_path / "pilote.py"), str(atelier), session],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=tmp_path,
            env=environ,
        )
        for session in ("atelier-un", "atelier-deux")
    ]
    sorties = [pilote.communicate(timeout=120) for pilote in pilotes]

    for pilote, (_, erreurs) in zip(pilotes, sorties, strict=True):
        assert pilote.returncode == 0, erreurs
    resultats = [json.loads(sortie.strip()) for sortie, _ in sorties]

    # Deux runs sont allés au bout, chacun dans sa session.
    assert [r["status"] for r in resultats] == ["completed", "completed"]
    assert (tmp_path / "data" / "default" / "atelier-un.jsonl").is_file()
    assert (tmp_path / "data" / "default" / "atelier-deux.jsonl").is_file()

    # Et la relance n'est partie qu'une fois.
    assert boite.read_text(encoding="utf-8").splitlines() == ["D-2026-042"]
    assert (tmp_path / KEYS).is_file()
