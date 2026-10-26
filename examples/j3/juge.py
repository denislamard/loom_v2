# SPDX-License-Identifier: Apache-2.0
"""Phase 3.3 : un juge vérifie la relance, et le rôle corrige lui-même ce qu'il a inventé.

    uv run python examples/j3/juge.py                      # refus du juge, puis correction
    uv run python examples/j3/juge.py --echantillon 0      # run non tiré : pas de jugement
    uv run python examples/j3/juge.py --echantillon 0 --juges force
    uv run python examples/j3/juge.py --juges skip
    uv run --env-file .env --extra anthropic --extra openai \\
        python examples/j3/juge.py --reel

Même config que ``politiques.py`` et ``contrats.py`` (``examples/j3/relance/``).
Le rôle ``rediger_relance`` a un juge (``judge`` dans ``agents/relance.yaml``),
qui passe après son contrat de sortie : le juge voit la demande, le devis et
les arguments du rôle, et note deux critères :

- ``fidele`` (bloquant) : rien d'inventé ;
- ``ton`` (non bloquant, seuil 0,6) : le ton demandé.

Variante (b) du jalon : le rôle simulé promet une réduction de 10 % qui n'est
pas dans le devis. Le juge refuse, le rôle reçoit le verdict à la suite de sa
conversation et corrige, le juge accepte. L'appel du juge est journalisé dans
le run, à son nom (``judge:rediger_relance``) : son coût s'ajoute au run.

Déclenchement : ``--echantillon`` fixe la part des runs jugés (tirage
déterministe par run) ; un run non tiré écrit un contrôle « ignoré ».
``--juges force`` juge quand même, ``--juges skip`` ne juge pas.

``--reel`` prend ``relance_reel`` : MiniMax-M3 orchestre, GLM-5.3-Flash
rédige, Claude Haiku 4.5 juge ; ``--question`` change la demande.
"""

import argparse
import asyncio
import json
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
from loom_ia.core.model import DEFAULT_TENANT, JUDGES_MODES, ModelSpec
from loom_ia.runtime import apply_logging

CONFIG = Path(__file__).parent / "relance" / "loom.yaml"
QUESTION = "Relance le client du devis D-2026-042, sur un ton cordial."

CORPS = (
    "Bonjour Madame Martin,\n\nJe reviens vers vous au sujet du devis D-2026-042 de "
    "1 840 € pour le remplacement de votre chauffe-eau, envoyé le 2 septembre. "
    "Avez-vous pu en prendre connaissance ?{promesse}\n\nBien cordialement,\nPlomberie Dupont"
)
INVENTEE = CORPS.format(
    promesse=" Pour une signature avant la fin du mois, nous vous accordons une réduction de 10 %."
)
# L'orchestrateur simulé : chercher le devis, puis faire rédiger.
MAIN: list[dict[str, Any]] = [
    {"tool_calls": [{"name": "chercher_devis", "arguments": {"numero": "D-2026-042"}}]},
    {"tool_calls": [{"name": "rediger_relance", "arguments": {"ton": "cordial"}}]},
    {"text": "La relance du devis D-2026-042 n'a pas pu être rédigée : à reprendre à la main."},
]
# Le rôle simulé : une promesse inventée, puis, après le verdict du juge, sans elle.
ROLE: list[dict[str, Any]] = [
    {
        "text": json.dumps(
            {"objet": "Votre devis D-2026-042", "corps": INVENTEE}, ensure_ascii=False
        )
    },
    {
        "text": json.dumps(
            {"objet": "Votre devis D-2026-042", "corps": CORPS.format(promesse="")},
            ensure_ascii=False,
        )
    },
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


def adjusted(config: LoomConfig, agent: str, sample: float | None, scripted: bool) -> LoomConfig:
    """Config de l'exemple : scripts de la variante (b) en simulé, part des runs jugés."""

    def script(spec: ModelSpec, replies: list[dict[str, Any]]) -> ModelSpec:
        return spec.model_copy(update={"params": {**spec.params, "script": replies}})

    models = config.models
    if scripted:
        models = tuple(
            script(m, MAIN)
            if m.id == "FAKE_MAIN"
            else script(m, ROLE)
            if m.id == "FAKE_ROLE"
            else m
            for m in config.models
        )
    agents: list[AgentSpec] = []
    for spec in config.agents:
        if spec.name == agent and sample is not None:
            roles = tuple(
                role.model_copy(
                    update={
                        "judge": role.judge.model_copy(
                            update={"when": role.judge.when.model_copy(update={"sample": sample})}
                        )
                    }
                )
                if role.judge is not None
                else role
                for role in spec.roles
            )
            spec = spec.model_copy(update={"roles": roles})
        agents.append(spec)
    return config.model_copy(update={"models": models, "agents": tuple(agents)})


async def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Juge d'un rôle")
    parser.add_argument(
        "--reel", action="store_true", help="vrais modèles (MiniMax-M3, GLM, Haiku)"
    )
    parser.add_argument(
        "--echantillon", type=float, default=None, help="part des runs jugés (0 à 1)"
    )
    parser.add_argument("--juges", choices=JUDGES_MODES, default="auto")
    parser.add_argument("--question", default=QUESTION)
    args = parser.parse_args(argv)
    agent = "relance_reel" if args.reel else "relance"

    try:
        config = adjusted(load_config(CONFIG), agent, args.echantillon, scripted=not args.reel)
    except ConfigError as error:
        print(f"Configuration : {error}", file=sys.stderr)
        return 2
    apply_logging(config)

    async with Loom(config) as loom:
        print(f"> {args.question}\n")
        try:
            result = await loom.run(agent, args.question, judges=args.juges)
        except (ModelConfigError, ConfigError) as error:
            print(f"Configuration : {error}", file=sys.stderr)
            return 2
        events = await loom.events(result.run_id)

    if result.data is not None:
        print(json.dumps(result.data, ensure_ascii=False, indent=2))
    else:
        print(result.text or result.error or "—")
    print("\nDéroulé :")
    progress = Progress()
    for line in filter(None, (describe(e, progress) for e in events)):
        print(f"  {line}")
    judged = sum(
        e.payload.cost_usd
        for e in events
        if isinstance(e.payload, ModelResponded) and e.payload.judge is not None
    )
    print(
        f"\nStatut     : {result.status} · non vérifiée : {'oui' if result.unverified else 'non'}"
        f" · itérations : {result.iterations}"
        f" · coût : {result.cost_usd:.5f} $ (dont juge : {judged:.5f} $)"
    )
    store = loom.store.inner
    if isinstance(store, JsonlEventStore):
        print(f"Journal    : {store.path(DEFAULT_TENANT, result.session_id)}")
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
