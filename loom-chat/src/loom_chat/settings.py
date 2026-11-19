"""Les fichiers loom du dossier de l'agent : création au premier lancement, puis lecture.

La console lit la config comme ``loom`` : l'amorce ``loom-boot.yaml`` du dossier
d'où l'on lance la console, la config qu'elle désigne, puis les agents de
``agents_dir``. L'utilisateur les modifie à sa guise ; loom-chat ne les écrit
qu'une fois, quand ils n'existent pas encore.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml
from loom_ia.config.loader import load_config
from loom_ia.config.models import LoomConfig
from pydantic import JsonValue

BOOT = "loom-boot.yaml"
ENV_KEY = "ANTHROPIC_API_KEY"
ANTHROPIC_MODEL = "claude-haiku-5-5"
# Les noms posés dans les fichiers créés : l'utilisateur peut les changer ensuite.
AGENT = "assistant"
MODEL_ID = "model"
USER = "local"
ROLE = "owner"

SYSTEM = (
    "Tu es un assistant simple, en français, et tu réponds brièvement. "
    "Utilise l'heure, la lecture d'une page web publique, ou une question à l'utilisateur quand cela aide."
)

# Script du modèle factice (sans clé d'API) : pour chaque message, il appelle
# l'heure, lit une page (approbation requise), pose une question, puis répond.
DEMO_SCRIPT: list[JsonValue] = [
    {"text": "Je regarde l'heure.", "tool_calls": [{"name": "current_datetime", "arguments": {}}]},
    {"text": "Je lis la page demandée.", "tool_calls": [{"name": "fetch_page", "arguments": {"url": "https://example.com"}}]},
    {
        "text": "Une question avant de répondre.",
        "tool_calls": [
            {
                "name": "ask",
                "arguments": {
                    "questions": [
                        {
                            "question": "Quel style de réponse préférez-vous ?",
                            "header": "Style",
                            "options": [{"label": "Court"}, {"label": "Détaillé"}],
                        }
                    ]
                },
            }
        ],
    },
    {"text": "Merci. C'est la fin de la démonstration : vous pouvez poser une autre question."},
]

type ModelKind = Literal["fake", "anthropic"]


@dataclass(frozen=True, slots=True)
class ChatSettings:
    """Ce que la console a besoin de savoir avant d'ouvrir loom.

    Attributes:
        config: La config loom lue dans le dossier de l'agent.
        user: L'utilisateur au nom duquel la console agit.
    """

    config: LoomConfig
    user: str


def choose_model(environ: Mapping[str, str]) -> ModelKind:
    """Le modèle des fichiers créés : Claude si une clé est présente, sinon le modèle factice.

    Args:
        environ: L'environnement où chercher la clé.
    """
    return "anthropic" if environ.get(ENV_KEY) else "fake"


def _models(kind: ModelKind) -> str:
    if kind == "anthropic":
        model: dict[str, JsonValue] = {
            "id": MODEL_ID,
            "sdk": "anthropic",
            "model": ANTHROPIC_MODEL,
            "api_key_env": ENV_KEY,
            "max_tokens": 2048,
            "pricing": {"input": 0.1, "output": 0.5, "cache_read": 0.01, "cache_write": 0.125},
        }
    else:
        model = {"id": MODEL_ID, "sdk": "fake", "model": "fake-1", "params": {"script": DEMO_SCRIPT}}
    return yaml.safe_dump({"models": [model]}, allow_unicode=True, sort_keys=False, width=120)


def project_files(kind: ModelKind) -> dict[str, str]:
    """Les fichiers d'un dossier d'agent prêt à l'emploi, par chemin relatif.

    Args:
        kind: ``fake`` (démo jouée sans clé) ou ``anthropic`` (Claude, facturé).
    """
    loom = f"""version: 1
workpath: .

agents_dir: agents/
resources_dir: prompts/

{_models(kind)}
storage:
  events: {{ backend: jsonl, path: data/journal }}

security:
  roles:
  - id: {ROLE}
    # self_approve : dans la console, l'utilisateur approuve les appels de ses propres runs.
    permissions: [run, answer, cancel, approve, self_approve, read]
  users:
  - id: {USER}
    roles: [{ROLE}]
  audit:
    path: data/audit.jsonl
"""
    agent = f"""name: {AGENT}
description: Assistant de démonstration de loom-chat.

max_iterations: 6

main:
  model: {MODEL_ID}
  system_file: system.md

tools:
- python: loom.current_datetime
- python: loom.fetch_page
  approval: always
- python: loom.ask
"""
    boot = """version: 1
config:
  source: file
  path: .
  file: loom.yaml
"""
    return {BOOT: boot, "loom.yaml": loom, f"agents/{AGENT}.yaml": agent, "prompts/system.md": SYSTEM + "\n"}


def existing(folder: Path, kind: ModelKind = "fake") -> list[Path]:
    """Les fichiers que ``create_project`` écrirait et qui existent déjà.

    Args:
        folder: Le dossier de l'agent.
        kind: Le modèle choisi.
    """
    return [folder / name for name in project_files(kind) if (folder / name).exists()]


def create_project(folder: Path, kind: ModelKind) -> list[Path]:
    """Écrit les fichiers loom d'un agent de démonstration ; n'écrase jamais rien.

    Args:
        folder: Le dossier de l'agent.
        kind: ``fake`` ou ``anthropic``.

    Returns:
        Les fichiers écrits.

    Raises:
        FileExistsError: Un des fichiers existe déjà ; rien n'est écrit.
    """
    if taken := existing(folder, kind):
        raise FileExistsError(f"Déjà présent, rien n'a été écrit : {', '.join(str(p.relative_to(folder)) for p in taken)}")
    written: list[Path] = []
    for name, content in project_files(kind).items():
        path = folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        written.append(path)
    return written


def load(folder: Path) -> LoomConfig:
    """La config loom du dossier de l'agent, lue depuis son amorce.

    Args:
        folder: Le dossier de l'agent.

    Raises:
        ConfigError: Amorce absente, fichier invalide (le message dit lequel).
    """
    return load_config(folder / BOOT)


def pick_user(config: LoomConfig, wanted: str | None) -> str:
    """L'utilisateur de la console : celui demandé, ou le seul déclaré.

    Args:
        config: La config loom.
        wanted: L'utilisateur demandé (``--user``).

    Raises:
        ValueError: Utilisateur inconnu, ou plusieurs déclarés sans choix.
    """
    users = [user.id for user in config.security.users]
    if wanted is not None:
        if wanted not in users:
            raise ValueError(f"Utilisateur {wanted!r} inconnu ; déclarés dans la config : {', '.join(users)}")
        return wanted
    if len(users) == 1:
        return users[0]
    raise ValueError(f"Plusieurs utilisateurs déclarés ({', '.join(users)}) : choisissez avec --user.")


def model_label(config: LoomConfig, agent: str) -> str:
    """Le modèle principal d'un agent, tel que la barre du haut l'affiche.

    Args:
        config: La config loom.
        agent: Nom de l'agent.
    """
    spec = next((a for a in config.agents if a.name == agent), None)
    model = next((m for m in config.models if spec is not None and m.id == spec.main.model), None)
    if model is None:
        return "?"
    return "modèle factice (démo)" if model.sdk == "fake" else model.model


def is_billed(config: LoomConfig, agent: str) -> bool:
    """Vrai si le modèle principal de l'agent est un vrai modèle (facturé).

    Args:
        config: La config loom.
        agent: Nom de l'agent.
    """
    return model_label(config, agent) not in {"modèle factice (démo)", "?"}
