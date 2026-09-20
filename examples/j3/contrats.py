# SPDX-License-Identifier: Apache-2.0
"""Phase 3.2 : un contrat de sortie, la normalisation et la réparation par le modèle auteur.

    uv run python examples/j3/contrats.py                        # variante a : normalisation
    uv run python examples/j3/contrats.py --variante r           # réparation par le rôle
    uv run python examples/j3/contrats.py --variante e --echec unverified
    uv run --env-file .env --extra anthropic --extra openai \\
        python examples/j3/contrats.py --reel

Même config que ``politiques.py`` (``examples/j3/relance/``). Le rôle
``rediger_relance`` doit rendre un JSON ``{objet, corps}`` : c'est son contrat
de sortie (``output`` dans ``agents/relance.yaml``). À chaque sortie du rôle,
le guard ``loom.contract`` écrit un ``guard.checked``, réussi ou non :

- **a, normalisation** : le rôle enveloppe son JSON dans du texte et un bloc de
  code ; la normalisation l'en extrait, sans appel au modèle ;
- **r, réparation** : le JSON du rôle est incomplet ; son propre modèle reçoit,
  à la suite de sa conversation, sa réponse et le diagnostic, et la corrige ;
- **e, échec** : la réparation échoue aussi ; ``on_failure`` décide (``--echec``) :
  ``fail`` renvoie une erreur à l'orchestrateur, qui répond sans e-mail,
  ``unverified`` garde la sortie en la marquant, ``fallback`` la remplace.

La sortie retenue est la réponse finale (rôle terminal) ; l'objet JSON est dans
``RunResult.data``. La réponse n'est diffusée qu'une fois contrôlée
(``stream_output: after_guards``, choisi parce que la sortie est contrôlée).

En simulé, chaque variante donne aux modèles simulés un script à elle : la
config est modifiée en Python avant le run (M2). ``--reel`` prend
``relance_reel`` : MiniMax-M3 orchestre, GLM-5.3-Flash rédige ; seules les
étapes nécessaires apparaissent alors.
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
from loom_ia.core.model import DEFAULT_TENANT, ModelSpec
from loom_ia.runtime import apply_logging

CONFIG = Path(__file__).parent / "relance" / "loom.yaml"
QUESTION = "Relance le client du devis D-2026-042, sur un ton cordial."

EMAIL = {
    "objet": "Votre devis D-2026-042",
    "corps": (
        "Bonjour Madame Martin,\n\nJe reviens vers vous au sujet du devis D-2026-042 de "
        "1 840 € pour le remplacement de votre chauffe-eau, envoyé le 2 septembre. "
        "Avez-vous pu en prendre connaissance ?\n\nBien cordialement,\nPlomberie Dupont"
    ),
}
# L'orchestrateur simulé : chercher le devis, faire rédiger, puis (si la
# rédaction échoue) le dire en une phrase.
MAIN: list[dict[str, Any]] = [
    {"tool_calls": [{"name": "chercher_devis", "arguments": {"numero": "D-2026-042"}}]},
    {"tool_calls": [{"name": "rediger_relance", "arguments": {"ton": "cordial"}}]},
    {"text": "La relance du devis D-2026-042 n'a pas pu être rédigée : à reprendre à la main."},
]
# Le rôle simulé, selon la variante.
ROLE: dict[str, list[dict[str, Any]]] = {
    "a": [{"text": f"Voici l'e-mail :\n\n```json\n{json.dumps(EMAIL, ensure_ascii=False)}\n```"}],
    "r": [
        {"text": json.dumps({"objet": EMAIL["objet"]}, ensure_ascii=False)},
        {"text": json.dumps(EMAIL, ensure_ascii=False)},
    ],
    "e": [
        {"text": "Bonjour Madame Martin, je reviens vers vous au sujet de votre devis."},
        {"text": "Objet : votre devis. Bonjour Madame Martin, …"},
    ],
}


def describe(event: Event, progress: Progress) -> str | None:
    """Une ligne par étape : appels de modèle, puis le déroulé partagé avec la CLI."""
    if isinstance(event.payload, ModelResponded):
        usage = event.payload.usage
        return (
            f"· modèle [{event.role}] {event.payload.model_id} · "
            f"{usage.input_tokens}/{usage.output_tokens} tokens"
        )
    return progress.line(event)


def scripted(config: LoomConfig, variant: str, on_failure: str) -> LoomConfig:
    """Config où les modèles simulés suivent la variante, et le contrat son ``on_failure``."""

    def script(spec: ModelSpec, replies: list[dict[str, Any]]) -> ModelSpec:
        return spec.model_copy(update={"params": {**spec.params, "script": replies}})

    models = tuple(
        script(m, MAIN)
        if m.id == "FAKE_MAIN"
        else script(m, ROLE[variant])
        if m.id == "FAKE_ROLE"
        else m
        for m in config.models
    )
    agents: list[AgentSpec] = []
    for spec in config.agents:
        if spec.name == "relance":
            # Le rôle n'a pas à citer le numéro dans cet exemple : on retire cite_le_devis.
            policies = tuple(p for p in spec.policies if p.hook != "cite_le_devis")
            roles = tuple(
                role.model_copy(
                    update={
                        "output": role.output.model_copy(
                            update={
                                "on_failure": on_failure,
                                "fallback_message": '{"objet": "Relance", "corps": "À rédiger."}',
                            }
                        )
                    }
                )
                if role.output is not None
                else role
                for role in spec.roles
            )
            spec = spec.model_copy(update={"policies": policies, "roles": roles})
        agents.append(spec)
    return config.model_copy(update={"models": models, "agents": tuple(agents)})


async def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Contrat de sortie d'un rôle")
    parser.add_argument("--reel", action="store_true", help="vrais modèles (MiniMax-M3, GLM)")
    parser.add_argument("--variante", choices=["a", "r", "e"], default="a")
    parser.add_argument("--echec", choices=["fail", "unverified", "fallback"], default="fail")
    parser.add_argument("--question", default=QUESTION)
    args = parser.parse_args(argv)
    agent = "relance_reel" if args.reel else "relance"

    try:
        config = load_config(CONFIG)
    except ConfigError as error:
        print(f"Configuration : {error}", file=sys.stderr)
        return 2
    if not args.reel:
        config = scripted(config, args.variante, args.echec)
    apply_logging(config)

    async with Loom(config) as loom:
        print(f"> {args.question}\n")
        try:
            result = await loom.run(agent, args.question)
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
    print(
        f"\nStatut     : {result.status} · non vérifiée : {'oui' if result.unverified else 'non'}"
        f" · itérations : {result.iterations} · coût : {result.cost_usd:.5f} $"
    )
    store = loom.store.inner
    if isinstance(store, JsonlEventStore):
        print(f"Journal    : {store.path(DEFAULT_TENANT, result.session_id)}")
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
