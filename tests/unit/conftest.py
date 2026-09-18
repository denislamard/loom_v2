# SPDX-License-Identifier: Apache-2.0
"""Configuration de démonstration, partagée par les tests des trois accès.

L'agent ``demo`` calcule : le modèle est scripté (``sdk: fake``), l'outil
``calculer`` vient d'un module voisin du fichier de config. C'est le scénario
du jalon J1, celui des exemples.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

QUESTION = "Combien font 12 fois 7, plus 3 ?"
ANSWER = "12 fois 7, plus 3, font 87."

SCRIPT: list[dict[str, Any]] = [
    {
        "text": "Je calcule.",
        "tool_calls": [{"name": "calculer", "arguments": {"expr": "12*7+3"}}],
    },
    {"text": ANSWER},
]
MODEL: dict[str, Any] = {
    "id": "FAKE",
    "sdk": "fake",
    "model": "fake-1",
    "params": {"script": SCRIPT},
}

OUTILS = '''
from loom_ia.tools import tool


@tool
def calculer(expr: str) -> str:
    """Calcule une expression arithmétique."""
    return str(eval(expr))
'''

type ConfigFactory = Callable[..., Path]


@pytest.fixture
def demo(tmp_path: Path) -> ConfigFactory:
    """Écrit une configuration ``demo`` et renvoie le chemin du fichier racine.

    Les mots-clés viennent compléter — ou remplacer — la racine du fichier ;
    ``agents`` remplace la liste des agents écrits dans ``agents/``.
    """

    def build(*, agents: list[dict[str, Any]] | None = None, **root: Any) -> Path:
        (tmp_path / "agents").mkdir(exist_ok=True)
        (tmp_path / "prompts").mkdir(exist_ok=True)
        (tmp_path / "prompts" / "demo.md").write_text("Tu calcules.", encoding="utf-8")
        (tmp_path / "outils_acces.py").write_text(OUTILS, encoding="utf-8")
        config: dict[str, Any] = {
            "version": 1,
            "imports": ["outils_acces"],
            "models": [MODEL],
            **root,
        }
        (tmp_path / "loom.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
        for agent in agents or [demo_agent()]:
            path = tmp_path / "agents" / f"{agent['name']}.yaml"
            path.write_text(yaml.safe_dump(agent), encoding="utf-8")
        return tmp_path / "loom.yaml"

    return build


def demo_agent(**changes: Any) -> dict[str, Any]:
    agent: dict[str, Any] = {
        "name": "demo",
        "description": "Répond aux questions de calcul.",
        "main": {"model": "FAKE", "system_file": "demo.md"},
        "max_iterations": 4,
        "tools": [{"python": "calculer"}],
    }
    return {**agent, **changes}
