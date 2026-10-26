# SPDX-License-Identifier: Apache-2.0
"""Phase 2.2 : un agent qui utilise deux serveurs MCP, « time » et « math ».

    uv run --extra mcp python examples/j2/client_mcp.py                  # modèle simulé
    uv run --env-file .env --extra mcp --extra anthropic python examples/j2/client_mcp.py --reel

La config est dans ``examples/j2/assistant/``. Les deux serveurs sont des
scripts FastMCP voisins (``serveurs/``), lancés en stdio par loom-ia :

- ``time`` (portée ``shared``) : une connexion gardée pour tous les runs ;
- ``math`` (portée ``run``) : une connexion ouverte et fermée avec chaque run.

Leurs outils sont préfixés par le nom du serveur (``time__maintenant``,
``math__calculer``). ``--reel`` prend l'agent ``assistant_reel`` : MiniMax-M3,
clé dans ``M3_API_KEY`` ; loom-ia ne charge pas ``.env`` lui-même, d'où
``--env-file .env``.

Après la réponse, le déroulé du run : appels de modèle, outils MCP, et
serveurs indisponibles s'il y en a eu ; ceux du sous-agent vérificateur sont
décalés. Le journal est écrit en JSONL dans
``examples/j2/assistant/data/`` (ignoré par git).
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
from loom_ia.core.model import DEFAULT_TENANT
from loom_ia.runtime import apply_logging

CONFIG = Path(__file__).parent / "assistant" / "loom.yaml"
QUESTION = (
    "Quelle heure est-il à Paris ? Et combien d'heures y a-t-il entre "
    "le 1er septembre et le 31 décembre 2026 ?"
)


def describe(event: Event) -> str | None:
    """Une ligne par étape marquante du run."""
    match event.payload:
        case ModelResponded(model_id=model, usage=usage, cost_usd=cost):
            return (
                f"  modèle   [{event.role}] {model} · {usage.input_tokens}/"
                f"{usage.output_tokens} tokens · {cost:.5f} $"
            )
        case ToolSourceUnavailable(source=source, error=error, required=required):
            return f"  serveur  {source} indisponible{' (requis)' if required else ''} : {error}"
        case ToolCalled(tool_name=name, tool_kind=kind, arguments=arguments):
            return f"  appel    {name} ({kind}) {arguments}"
        case ToolCompleted(tool_name=name, output=output):
            shown = output.as_text.replace("\n", " ")[:80]
            return f"  résultat {name}{' (erreur)' if output.is_error else ''} : {shown}"
        case _:
            return None


async def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Agent et serveurs MCP")
    parser.add_argument("--reel", action="store_true", help="vrai modèle (MiniMax-M3)")
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
        events = await loom.events(result.run_id)

    print(result.text or result.error or "—")
    print("\nDéroulé :")
    for event in events:
        if (line := describe(event)) is not None:
            # Les lignes du sous-agent vérificateur sont décalées.
            print(line if event.run_id == result.run_id else f"  {line}")
    print(
        f"\nStatut     : {result.status} · itérations : {result.iterations}"
        f" · coût total : {result.cost_usd:.5f} $"
    )
    store = loom.store.inner
    if isinstance(store, JsonlEventStore):
        print(f"Journal    : {store.path(DEFAULT_TENANT, result.session_id)}")
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
