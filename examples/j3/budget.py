# SPDX-License-Identifier: Apache-2.0
"""Phase 3.4 : un budget arrête le run, qui donne une réponse forcée ; puis le rapport de coûts.

    uv run python examples/j3/budget.py                    # budget atteint : réponse forcée
    uv run python examples/j3/budget.py --action warn      # avertissement : le run continue
    uv run python examples/j3/budget.py --plafond 1        # budget large : rien ne se passe
    uv run --env-file .env --extra anthropic --extra openai \\
        python examples/j3/budget.py --reel --appels 1

Même config que les exemples précédents (``examples/j3/relance/``). Les deux
agents ont un budget par défaut (``budgets`` dans ``loom.yaml``), vérifié
avant chaque appel de l'orchestrateur par la politique fournie ``loom.budget``.
L'exemple le resserre pour l'agent (clé ``budget`` de l'agent, M2) :
``--plafond`` en dollars, ``--appels`` en appels de modèle du run.

Variante (c) du jalon, première moitié. En simulé, le rôle promet deux fois
une réduction absente du devis : le juge refuse deux fois, et l'orchestrateur
reçoit une erreur. Les appels du juge ont coûté ; avant l'appel suivant de
l'orchestrateur, le budget du run est atteint :

- ``stop`` (défaut) : ``budget.exceeded``, puis réponse forcée sans outils ;
- ``warn`` : ``budget.exceeded``, et le run continue : l'orchestrateur rappelle
  le rôle avec des consignes, et la relance passe.

Le rapport de consommation (``Loom.report``, ``loom report <run_id>``) ventile
ensuite le coût par run, par rôle (orchestrateur, rôle, juge) et par modèle.

``--reel`` prend ``relance_reel`` (MiniMax-M3, GLM-5.3-Flash, Claude Haiku
4.5) ; avec ``--appels 1``, le budget arrête l'orchestrateur après son premier
appel.
"""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from loom_ia.access.api import Loom
from loom_ia.access.progress import Progress
from loom_ia.adapters.models import ModelConfigError
from loom_ia.adapters.stores import JsonlEventStore
from loom_ia.agents import AgentSpec
from loom_ia.config import ConfigError, LoomConfig, load_config
from loom_ia.core.events import Event, ModelResponded
from loom_ia.core.model import DEFAULT_TENANT, Budgets, ModelSpec
from loom_ia.runtime import apply_logging
from loom_ia.usage import render

CONFIG = Path(__file__).parent / "relance" / "loom.yaml"
QUESTION = "Relance le client du devis D-2026-042, sur un ton cordial."
CONSIGNES = "Aucune réduction : elle n'est pas dans le devis."

CORPS = (
    "Bonjour Madame Martin,\n\nJe reviens vers vous au sujet du devis D-2026-042 de "
    "1 840 € pour le remplacement de votre chauffe-eau, envoyé le 2 septembre. "
    "Avez-vous pu en prendre connaissance ?{promesse}\n\nBien cordialement,\nPlomberie Dupont"
)
PROMESSE = " Pour une signature avant la fin du mois, nous vous accordons une réduction de 10 %."


def email(corps: str) -> str:
    return json.dumps({"objet": "Votre devis D-2026-042", "corps": corps}, ensure_ascii=False)


# L'orchestrateur simulé : chercher le devis, faire rédiger, puis rappeler le
# rôle avec des consignes ; « forced » : sa réponse forcée, sans outils.
MAIN: list[dict[str, Any]] = [
    {"tool_calls": [{"name": "chercher_devis", "arguments": {"numero": "D-2026-042"}}]},
    {"tool_calls": [{"name": "rediger_relance", "arguments": {"ton": "cordial"}}]},
    {
        "tool_calls": [
            {"name": "rediger_relance", "arguments": {"ton": "cordial", "consignes": CONSIGNES}}
        ],
        "forced": (
            "Relance du devis D-2026-042 non rédigée : budget du run atteint. "
            "À reprendre à la main."
        ),
    },
]
# Le rôle simulé : la promesse inventée, deux fois (appel, puis réparation) ;
# avec les consignes, la relance sans elle.
ROLE: list[dict[str, Any]] = [
    {"without_text": CONSIGNES, "text": email(CORPS.format(promesse=PROMESSE))},
    {"without_text": CONSIGNES, "text": email(CORPS.format(promesse=PROMESSE))},
    {"with_text": CONSIGNES, "text": email(CORPS.format(promesse=""))},
]


def describe(event: Event, progress: Progress) -> str | None:
    """Une ligne par étape : appels de modèle (rôle, coût), puis le déroulé de la CLI."""
    if isinstance(event.payload, ModelResponded):
        payload = event.payload
        usage = payload.usage
        return (
            f"· modèle [{event.role}] {payload.model_id} · "
            f"{usage.input_tokens}/{usage.output_tokens} tokens · {payload.cost_usd:.5f} $"
        )
    return progress.line(event)


def adjusted(config: LoomConfig, agent: str, args: argparse.Namespace) -> LoomConfig:
    """Budget de l'agent resserré (plafond, appels, action) ; scripts simulés hors ``--reel``."""

    def script(spec: ModelSpec, replies: list[dict[str, Any]]) -> ModelSpec:
        return spec.model_copy(update={"params": {**spec.params, "script": replies}})

    models = config.models
    if not args.reel:
        models = tuple(
            script(m, MAIN)
            if m.id == "FAKE_MAIN"
            else script(m, ROLE)
            if m.id == "FAKE_ROLE"
            else m
            for m in config.models
        )
    run: dict[str, float | int] = {}
    if args.plafond is not None:
        run["max_cost"] = args.plafond
    if args.appels is not None:
        run["max_calls"] = args.appels
    budget = Budgets.model_validate({"run": run, "on_exceed": args.action})
    agents: list[AgentSpec] = [
        spec.model_copy(update={"budget": budget}) if spec.name == agent else spec
        for spec in config.agents
    ]
    return config.model_copy(update={"models": models, "agents": tuple(agents)})


def shown(path: Path) -> str:
    """Chemin relatif au dossier courant, pour une commande à copier."""
    return os.path.relpath(path)


async def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Budget d'un run et rapport de consommation")
    parser.add_argument("--reel", action="store_true", help="vrais modèles")
    parser.add_argument("--plafond", type=float, default=None, help="budget du run, en dollars")
    parser.add_argument("--appels", type=int, default=None, help="appels de modèle du run")
    parser.add_argument("--action", choices=["stop", "warn"], default="stop")
    parser.add_argument("--question", default=QUESTION)
    args = parser.parse_args(argv)
    agent = "relance_reel" if args.reel else "relance"
    if not args.reel and args.plafond is None and args.appels is None:
        args.plafond = 0.002

    try:
        config = adjusted(load_config(CONFIG), agent, args)
    except ConfigError as error:
        print(f"Configuration : {error}", file=sys.stderr)
        return 2
    apply_logging(config)
    budgets = config.budget_of(agent)

    async with Loom(config) as loom:
        print(f"> {args.question}")
        limits = ", ".join(
            f"{name} {value:g}" for name, value in budgets.run.model_dump().items() if value
        )
        print(f"  budget du run : {limits} ; {budgets.on_exceed}\n")
        try:
            result = await loom.run(agent, args.question)
        except (ModelConfigError, ConfigError) as error:
            print(f"Configuration : {error}", file=sys.stderr)
            return 2
        events = await loom.events(result.run_id)
        report = await loom.report(result.run_id, session_id=result.session_id)

    if result.data is not None:
        print(json.dumps(result.data, ensure_ascii=False, indent=2))
    else:
        print(result.text or result.error or "—")
    print("\nDéroulé :")
    progress = Progress()
    for line in filter(None, (describe(e, progress) for e in events)):
        print(f"  {line}")
    print(f"\nStatut     : {result.status} · itérations : {result.iterations}\n")
    print("\n".join(render(report)))
    store = loom.store.inner
    if isinstance(store, JsonlEventStore):
        print(f"\nJournal    : {store.path(DEFAULT_TENANT, result.session_id)}")
        print(f"Rapport    : uv run loom --config {shown(CONFIG)} report {result.run_id}")
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
