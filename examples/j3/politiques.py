# SPDX-License-Identifier: Apache-2.0
"""Phase 3.1 : des politiques qui décident, à chaque point d'accroche d'un run.

    uv run python examples/j3/politiques.py                  # modèles simulés
    uv run python examples/j3/politiques.py --plafond 2      # arrêt par plafond_appels
    uv run --env-file .env --extra anthropic --extra openai \\
        python examples/j3/politiques.py --reel

La config est dans ``examples/j3/relance/`` ; les politiques de l'exemple
dans ``relance/politiques.py``. L'agent en branche quatre :

- ``loom.require_tool`` (fournie, ``before_model``) : tant que le run n'a
  appelé aucun outil, la requête impose un appel (``tool_choice: required``) ;
- ``numero_devis`` (``before_tool``) : le numéro passé à ``chercher_devis`` est
  normalisé (``Replace``), ou l'appel est refusé s'il est mal formé (``Deny``) ;
- ``plafond_appels`` (``before_model``) : au-delà de ``max_appels`` appels du
  modèle, plus d'outils (``Stop``) : le run passe en réponse forcée ;
- ``cite_le_devis`` (``on_output``) : la relance, sortie du rôle terminal, doit
  citer le numéro du devis ; sinon elle revient à l'orchestrateur avec un
  diagnostic (``Retry``), qui rappelle le rôle avec des consignes. Le prompt
  du rôle ne demande plus le numéro : c'est la politique qui l'exige.

Chaque décision autre que ``Continue`` est journalisée (``policy.decided``),
avant son effet. Le script affiche la réponse, puis le déroulé relu dans le
journal. ``--plafond N`` change ``max_appels`` (config modifiée en Python).

``--reel`` prend ``relance_reel`` : MiniMax-M3 orchestre (``M3_API_KEY``),
GLM-5.3-Flash rédige chez Together (``TOGETHER_API_KEY``). Avec de vrais
modèles, seules les décisions nécessaires apparaissent.
"""

import argparse
import asyncio
import sys
from pathlib import Path

from loom_ia.access.api import Loom
from loom_ia.adapters.models import ModelConfigError
from loom_ia.adapters.stores import JsonlEventStore
from loom_ia.agents import AgentSpec
from loom_ia.config import ConfigError, LoomConfig, load_config
from loom_ia.core.events import (
    Event,
    ModelResponded,
    PolicyDecided,
    RunTransitioned,
    ToolCalled,
    ToolCompleted,
    UserMessage,
)
from loom_ia.core.model import DEFAULT_TENANT, RunStatus, ToolOutput
from loom_ia.runtime import apply_logging

CONFIG = Path(__file__).parent / "relance" / "loom.yaml"
QUESTION = "Relance le client du devis D-2026-042, sur un ton cordial."


def describe(event: Event) -> str | None:
    """Une ligne par étape marquante du run."""
    match event.payload:
        case PolicyDecided(policy=name, point=point, decision=decision, reason=reason):
            return f"  politique {name} ({point}) : {decision}{f' — {reason}' if reason else ''}"
        case ModelResponded(model_id=model, usage=usage, cost_usd=cost):
            return (
                f"  modèle    [{event.role}] {model} · {usage.input_tokens}/"
                f"{usage.output_tokens} tokens · {cost:.5f} $"
            )
        case ToolCalled(tool_name=name, arguments=arguments):
            return f"  appel     {name} {arguments}"
        case ToolCompleted(tool_name=name, output=output):
            return f"  résultat  {name}{' (erreur)' if output.is_error else ''} : {shown(output)}"
        case UserMessage(kind="repair", message=message):
            return f"  réparation demandée : {message.text}"
        case RunTransitioned(to_state=RunStatus.FINALIZING):
            return "  → réponse forcée, sans outils"
        case _:
            return None


def shown(output: ToolOutput) -> str:
    """Début d'un résultat : son texte, ou ses données."""
    text = output.as_text or (str(output.data) if output.data is not None else "")
    text = text.replace("\n", " ")
    return text if len(text) <= 90 else f"{text[:89]}…"


def with_limit(config: LoomConfig, agent: str, maximum: int) -> LoomConfig:
    """Config où ``plafond_appels`` de l'agent vaut ``maximum`` (construction en Python, M2)."""
    agents: list[AgentSpec] = []
    for spec in config.agents:
        if spec.name == agent:
            policies = tuple(
                ref.model_copy(update={"params": {**ref.params, "max_appels": maximum}})
                if ref.hook == "plafond_appels"
                else ref
                for ref in spec.policies
            )
            spec = spec.model_copy(update={"policies": policies})
        agents.append(spec)
    return config.model_copy(update={"agents": tuple(agents)})


async def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Politiques d'un agent de relance")
    parser.add_argument("--reel", action="store_true", help="vrais modèles (MiniMax-M3, GLM)")
    parser.add_argument("--plafond", type=int, default=None, help="max_appels de plafond_appels")
    parser.add_argument("--question", default=QUESTION)
    args = parser.parse_args(argv)
    agent = "relance_reel" if args.reel else "relance"

    try:
        config = load_config(CONFIG)
    except ConfigError as error:
        print(f"Configuration : {error}", file=sys.stderr)
        return 2
    if args.plafond is not None:
        config = with_limit(config, agent, args.plafond)
    apply_logging(config)

    async with Loom(config) as loom:
        print(f"> {args.question}\n")
        try:
            result = await loom.run(agent, args.question)
        except (ModelConfigError, ConfigError) as error:
            print(f"Configuration : {error}", file=sys.stderr)
            return 2
        events = await loom.events(result.run_id)

    print(result.text or result.error or "—")
    print("\nDéroulé :")
    for line in filter(None, map(describe, events)):
        print(line)
    decisions = sum(isinstance(e.payload, PolicyDecided) for e in events)
    print(
        f"\nStatut     : {result.status} · itérations : {result.iterations}"
        f" · décisions journalisées : {decisions} · coût : {result.cost_usd:.5f} $"
    )
    store = loom.store.inner
    if isinstance(store, JsonlEventStore):
        print(f"Journal    : {store.path(DEFAULT_TENANT, result.session_id)}")
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
