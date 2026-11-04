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

from loom_ia.access.cli.main import USER_ENV
from loom_ia.config.models import LOOPBACK_HOSTS

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

# --- Qui agit sur loom (D-1) ------------------------------------------------------------

# Toutes les permissions, sauf act_as (qui demande act_as_roles) : le rôle des configs
# de test qui n'éprouvent pas les droits.
EVERYTHING: list[str] = [
    "run",
    "answer",
    "cancel",
    "approve",
    "self_approve",
    "amend",
    "read",
    "read_content",
    "admin",
]
OWNER: dict[str, Any] = {"id": "owner", "permissions": EVERYTHING}
# Utilisateur des configs sans clients.
DEV = "dev"


# Le même bloc, à écrire tel quel dans un loom.yaml rédigé à la main.
SECURITY_YAML = (
    "security:\n"
    f"  roles: [{{id: owner, permissions: [{', '.join(EVERYTHING)}]}}]\n"
    f"  users: [{{id: {DEV}, roles: [owner]}}]\n"
)


def secured(config: dict[str, Any]) -> dict[str, Any]:
    """La racine d'une config, avec son bloc ``security`` complété (D-1).

    Ce que la config déclare déjà est gardé. Sans ``roles``, le rôle ``owner`` ;
    sans ``users``, un utilisateur par client déclaré (de même identifiant, dans
    ce client), ou ``dev`` quand la config n'en nomme aucun.

    Une API REST sans clé agit au nom de ``dev`` (``server.http.user``, D-1b)
    quand c'est permis : sans client déclaré, sans MCP HTTP, sur la boucle
    locale, et si ``dev`` est déclaré. Le profil ``prod``, qui le refuse, le
    retire : un essai qui charge la config en production la reçoit sans lui.
    """
    security: dict[str, Any] = dict(config.get("security") or {})
    security.setdefault("roles", [OWNER])
    declared: list[dict[str, Any]] = config.get("tenants") or []
    tenants: list[str] = [tenant["id"] for tenant in declared]
    security.setdefault(
        "users",
        [{"id": tenant, "roles": ["owner"], "tenant": tenant} for tenant in tenants]
        or [{"id": DEV, "roles": ["owner"]}],
    )
    secured = {**config, "security": security}
    server: dict[str, Any] = dict(config.get("server") or {})
    http: dict[str, Any] = dict(server.get("http") or {})
    mcp: dict[str, Any] = server.get("mcp") or {}
    if (
        "user" not in http
        and not security.get("api_keys")
        and not tenants
        and not mcp.get("http")
        and http.get("host", "127.0.0.1") in LOOPBACK_HOSTS
        and any(user["id"] == DEV for user in security["users"])
    ):
        http["user"] = DEV
        secured["server"] = {**server, "http": http}
        profiles: dict[str, Any] = dict(config.get("profiles") or {})
        if not isinstance(profiles.get("prod", {}), dict):
            # Un bloc de profil invalide : la config doit le refuser telle quelle.
            return secured
        prod: dict[str, Any] = dict(profiles.get("prod") or {})
        prod_server: dict[str, Any] = dict(prod.get("server") or {})
        prod_http: dict[str, Any] = dict(prod_server.get("http") or {})
        prod_http.setdefault("user", None)
        profiles["prod"] = {**prod, "server": {**prod_server, "http": prod_http}}
        secured["profiles"] = profiles
    return secured


@pytest.fixture(autouse=True)
def loom_user(monkeypatch: pytest.MonkeyPatch) -> None:
    """L'utilisateur des commandes de la CLI : ``dev``, comme dans le ``.env`` d'un développeur.

    Un essai qui éprouve son absence le retire (``monkeypatch.delenv``) ; un
    essai d'une config à clients passe ``--user``.
    """
    monkeypatch.setenv(USER_ENV, DEV)


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
        (tmp_path / "loom.yaml").write_text(yaml.safe_dump(secured(config)), encoding="utf-8")
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


# --- Un texte qui piloterait le terminal ----------------------------------------------

# Ce qu'un modèle ou un outil pourrait rendre pour piloter le terminal de
# l'opérateur : écrire dans son presse-papier (OSC 52), effacer l'écran, puis
# une séquence CSI sur 8 bits.
HOSTILE = "Fait.\x1b]52;c;Y29waWU=\x07\x1b[2J\x9b6n"
# Le même, tel que la CLI le montre : chaque caractère de contrôle rendu visible.
HOSTILE_SHOWN = "Fait.\\x1b]52;c;Y29waWU=\\x07\\x1b[2J\\x9b6n"

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
        (tmp_path / "loom.yaml").write_text(yaml.safe_dump(secured(config)), encoding="utf-8")
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
        (tmp_path / "loom.yaml").write_text(yaml.safe_dump(secured(config)), encoding="utf-8")
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
