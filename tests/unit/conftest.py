# SPDX-License-Identifier: Apache-2.0
"""Configurations de démonstration, partagées par les tests des trois accès.

L'agent ``demo`` calcule : le modèle est scripté (``sdk: fake``), l'outil
``calculer`` vient d'un module voisin du fichier de config. C'est le scénario
du jalon J1, celui des exemples.

L'``atelier``, lui, relance une cliente par e-mail : son unique outil demande
une approbation, et c'est ce qu'il faut pour éprouver les chemins d'un run qui
s'arrête en attendant un humain (J4.5).
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


# --- Délégation à un sous-agent (J2.5) ---------------------------------------------

TREE_QUESTION = "Combien font 2 + 2 ?"
TREE_ANSWER = "Vérifié : 4."
# Signature PNG suivie d'octets quelconques : assez pour être reconnue.
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 24


@pytest.fixture
def tree(tmp_path: Path) -> ConfigFactory:
    """Config où ``demo`` fait vérifier un calcul par le sous-agent ``verificateur``.

    L'enfant appelle lui-même l'outil ``calculer`` : l'arbre a deux runs, et
    chacun des appels d'outil. Le journal est en JSONL, pour que plusieurs
    instances le partagent. Les mots-clés complètent la racine du fichier.
    """

    def build(**root: Any) -> Path:
        (tmp_path / "agents").mkdir(exist_ok=True)
        (tmp_path / "outils_acces.py").write_text(OUTILS, encoding="utf-8")
        main_script: list[dict[str, Any]] = [
            {
                "text": "Je fais vérifier.",
                "tool_calls": [{"name": "verifier", "arguments": {"message": "Vérifie 2 + 2."}}],
            },
            {"text": TREE_ANSWER},
        ]
        child_script: list[dict[str, Any]] = [
            {"tool_calls": [{"name": "calculer", "arguments": {"expr": "2+2"}}]},
            {"text": "2 + 2 = 4, c'est exact."},
        ]
        config: dict[str, Any] = {
            "version": 1,
            "imports": ["outils_acces"],
            "models": [
                {"id": "MAIN", "sdk": "fake", "model": "main-1", "params": {"script": main_script}},
                {
                    "id": "CHILD",
                    "sdk": "fake",
                    "model": "child-1",
                    "params": {"script": child_script},
                },
            ],
            "storage": {"events": {"backend": "jsonl", "path": "data"}},
            "telemetry": {"logging": {"level": "WARNING"}},
            **root,
        }
        agents: list[dict[str, Any]] = [
            {
                "name": "demo",
                "description": "Répond, après vérification.",
                "main": {"model": "MAIN", "system": "Tu fais vérifier tes calculs."},
                "subagents": [{"agent": "verificateur", "name": "verifier"}],
            },
            {
                "name": "verificateur",
                "description": "Vérifie un calcul.",
                "expose": {"rest": False, "mcp": False},
                "main": {"model": "CHILD", "system": "Tu vérifies."},
                "tools": [{"python": "calculer"}],
            },
        ]
        (tmp_path / "loom.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
        for agent in agents:
            path = tmp_path / "agents" / f"{agent['name']}.yaml"
            path.write_text(yaml.safe_dump(agent), encoding="utf-8")
        return tmp_path / "loom.yaml"

    return build


# --- Un outil qui demande une approbation (J4.5) ------------------------------

OUTIL_SENSIBLE = '''
from loom_ia.tools import tool


@tool
async def envoyer_email(destinataire: str) -> str:
    """Envoie un e-mail."""
    return f"envoyé à {destinataire}"
'''

RELANCE: list[dict[str, Any]] = [
    {
        "text": "J'envoie.",
        "tool_calls": [
            {"name": "envoyer_email", "arguments": {"destinataire": "mme.martin@example.com"}}
        ],
    },
    {"text": "Relance envoyée."},
]


@pytest.fixture
def atelier(tmp_path: Path) -> Callable[..., Path]:
    """Config dont l'unique outil demande une approbation ; journal JSONL (#28)."""

    def build(**root: Any) -> Path:
        (tmp_path / "agents").mkdir(exist_ok=True)
        (tmp_path / "outils_atelier.py").write_text(OUTIL_SENSIBLE, encoding="utf-8")
        config: dict[str, Any] = {
            "version": 1,
            "imports": ["outils_atelier"],
            "models": [
                {"id": "FAKE", "sdk": "fake", "model": "fake-1", "params": {"script": RELANCE}}
            ],
            "storage": {"events": {"backend": "jsonl", "path": "data"}},
            "telemetry": {"logging": {"level": "CRITICAL"}},
            **root,
        }
        (tmp_path / "loom.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
        agent: dict[str, Any] = {
            "name": "demo",
            "description": "Relance.",
            "main": {"model": "FAKE", "system": "Tu relances."},
            "tools": [
                {
                    "python": "envoyer_email",
                    "side_effects": "irreversible",
                    "approval": "always",
                }
            ],
        }
        (tmp_path / "agents" / "demo.yaml").write_text(yaml.safe_dump(agent), encoding="utf-8")
        return tmp_path / "loom.yaml"

    return build
