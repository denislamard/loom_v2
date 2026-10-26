# SPDX-License-Identifier: Apache-2.0
"""Phase 2.4 : un sous-agent qui vérifie le travail de l'orchestrateur.

    uv run --extra mcp python examples/j2/sous_agent.py                  # modèles simulés
    uv run --env-file .env --extra mcp --extra anthropic --extra openai \\
        python examples/j2/sous_agent.py --reel

Même config que ``client_mcp.py`` et ``vision.py`` (``examples/j2/assistant/``).
L'orchestrateur lit l'heure, compte les jours et calcule, puis confie la
vérification à l'outil ``verifier`` : un autre agent de la config
(``verificateur``), avec sa propre boucle, son modèle et ses outils MCP.

Ce qui se passe :

- l'appel de ``verifier`` crée un **run enfant**, écrit dans le même journal
  que le parent : même ``root_run_id``, ``parent_run_id`` et ``depth`` 1 ;
- l'enfant ne reçoit que le message de l'orchestrateur, sans la conversation ;
- sa réponse finale revient à l'orchestrateur comme résultat d'outil, et son
  coût s'ajoute à celui du parent.

Le script affiche ensuite l'arbre des runs, relu dans le journal. ``--reel``
prend ``assistant_reel`` : MiniMax-M3 orchestre (``M3_API_KEY``) et
GLM-5.3-Flash vérifie, chez Together (``TOGETHER_API_KEY``).
"""

import argparse
import asyncio
import sys
from pathlib import Path

from loom_ia.access.api import Loom
from loom_ia.adapters.models import ModelConfigError
from loom_ia.adapters.stores import JsonlEventStore
from loom_ia.config import ConfigError, load_config
from loom_ia.core.events import (
    Event,
    ModelResponded,
    ToolCalled,
    ToolCompleted,
    ToolSourceUnavailable,
)
from loom_ia.core.model import DEFAULT_TENANT, RunId, RunState
from loom_ia.core.projections import fold_all
from loom_ia.runtime import apply_logging

CONFIG = Path(__file__).parent / "assistant" / "loom.yaml"
QUESTION = (
    "Quelle heure est-il à Paris ? Et combien d'heures y a-t-il entre "
    "le 1er septembre et le 31 décembre 2026 ?"
)


def describe(event: Event) -> str | None:
    """Une ligne par étape marquante d'un run."""
    match event.payload:
        case ModelResponded(model_id=model, usage=usage, cost_usd=cost):
            return (
                f"modèle   [{event.role}] {model} · {usage.input_tokens}/"
                f"{usage.output_tokens} tokens · {cost:.5f} $"
            )
        case ToolSourceUnavailable(source=source, error=error):
            return f"serveur  {source} indisponible : {error}"
        case ToolCalled(tool_name=name, tool_kind=kind, arguments=arguments):
            return f"appel    {name} ({kind}) {arguments}"
        case ToolCompleted(tool_name=name, output=output):
            shown = output.as_text.replace("\n", " ")[:80]
            return f"résultat {name}{' (erreur)' if output.is_error else ''} : {shown}"
        case _:
            return None


def show_tree(
    events: list[Event], states: dict[RunId, RunState], run_id: RunId, indent: str = "  "
) -> None:
    """Un run, ses étapes, et le sous-arbre de chaque sous-agent sous son appel."""
    state = states[run_id]
    print(
        f"{indent}{state.agent} · run {run_id} · profondeur {state.depth} · {state.status}"
        f" · {state.iterations} itération(s) · {state.cost_usd:.5f} $"
    )
    for event in (e for e in events if e.run_id == run_id):
        line = describe(event)
        if line is not None:
            print(f"{indent}  {line}")
        child = event.payload.child_run_id if isinstance(event.payload, ToolCalled) else None
        if child is not None and child in states:
            show_tree(events, states, child, indent + "    ")


async def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Orchestrateur et sous-agent")
    parser.add_argument(
        "--reel", action="store_true", help="vrais modèles (MiniMax-M3, GLM-5.3-Flash)"
    )
    parser.add_argument("--question", default=QUESTION)
    args = parser.parse_args(argv)
    agent = "assistant_reel" if args.reel else "assistant"

    try:
        config = load_config(CONFIG)
        loom = Loom(config)
    except ConfigError as error:
        print(f"Configuration : {error}", file=sys.stderr)
        return 2
    apply_logging(config)

    async with loom:
        print(f"> {args.question}\n")
        try:
            result = await loom.run(agent, args.question)
        except (ModelConfigError, ConfigError) as error:
            print(f"Configuration : {error}", file=sys.stderr)
            return 2
        # Le journal de la session contient l'arbre : le run et ses sous-runs.
        events = await loom.store.read(DEFAULT_TENANT, result.session_id)

    print(result.text or result.error or "—")
    print("\nArbre des runs :")
    show_tree(events, fold_all(events), result.run_id)
    print(
        f"\nStatut     : {result.status} · itérations : {result.iterations}"
        f" · coût total (sous-runs compris) : {result.cost_usd:.5f} $"
    )
    store = loom.store.inner
    if isinstance(store, JsonlEventStore):
        print(f"Journal    : {store.path(DEFAULT_TENANT, result.session_id)}")
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
