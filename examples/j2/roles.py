# SPDX-License-Identifier: Apache-2.0
"""Phase 2.1 : un orchestrateur qui délègue la rédaction à un rôle, sur un autre modèle.

    uv run python examples/j2/roles.py                     # modèles simulés
    uv run --env-file .env --extra anthropic --extra openai python examples/j2/roles.py --reel

La config est dans ``examples/j2/relance/``. L'orchestrateur cherche un devis
(outil Python), puis appelle le rôle ``rediger_relance``. Ce rôle ne reçoit
que la demande, le devis et le ton, sur son propre modèle. Il est terminal :
sa sortie est la réponse finale, sans nouveau passage par l'orchestrateur.

``--reel`` prend l'agent ``relance_reel`` : MiniMax-M3 (clé dans
``M3_API_KEY``) pour l'orchestrateur, gpt-oss-120b chez Together (clé dans
``TOGETHER_API_KEY``) pour le rôle. loom-ia lit les clés dans l'environnement
et ne charge pas ``.env`` lui-même : ``--env-file .env`` demande à uv de le
faire. Les clés ne sont jamais affichées.

Après la réponse, le déroulé du run : les appels de modèle attribués à leur
rôle (``main`` ou ``rediger_relance``), les outils, et les références
``$ref`` résolues. Le journal est écrit en JSONL dans
``examples/j2/relance/data/`` (ignoré par git).
"""

import argparse
import asyncio
import sys
from pathlib import Path

from loom_ia.access.api import Loom
from loom_ia.adapters.models import ModelConfigError
from loom_ia.adapters.stores import JsonlEventStore
from loom_ia.config import ConfigError, load_config
from loom_ia.core.events import Event, ModelResponded, ModelRetried, ToolCalled, ToolCompleted
from loom_ia.core.model import DEFAULT_TENANT
from loom_ia.runtime import apply_logging

CONFIG = Path(__file__).parent / "relance" / "loom.yaml"
QUESTION = "Relance le client du devis D-2026-042, sur un ton cordial."


def describe(event: Event) -> str | None:
    """Une ligne par étape marquante du run."""
    match event.payload:
        case ModelResponded(model_id=model, usage=usage, cost_usd=cost):
            return (
                f"  modèle   [{event.role}] {model} · {usage.input_tokens}/"
                f"{usage.output_tokens} tokens · {cost:.5f} $"
            )
        case ModelRetried(error_kind=kind, attempt=attempt):
            return f"  retry    [{event.role}] tentative {attempt} ratée ({kind})"
        case ToolCalled(tool_name=name, tool_kind=kind, refs=refs):
            resolved = f" · références {', '.join(refs)}" if refs else ""
            return f"  appel    {name} ({kind}){resolved}"
        case ToolCompleted(tool_name=name, output=output):
            return f"  résultat {name}{' (erreur)' if output.is_error else ''}"
        case _:
            return None


async def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Orchestrateur et rôle délégué")
    parser.add_argument("--reel", action="store_true", help="vrais modèles (MiniMax-M3, gpt-oss)")
    parser.add_argument("--question", default=QUESTION)
    args = parser.parse_args(argv)
    agent = "relance_reel" if args.reel else "relance"

    try:
        config = load_config(CONFIG)
    except ConfigError as error:
        print(f"Configuration : {error}", file=sys.stderr)
        return 2
    apply_logging(config)

    async with Loom(config) as loom:
        print(f"> {args.question}\n")
        try:
            result = await loom.run(agent, args.question)
        except ModelConfigError as error:
            print(f"Modèle : {error}", file=sys.stderr)
            return 2
        events = await loom.events(result.run_id)

    print(result.text or result.error or "—")
    print("\nDéroulé :")
    for line in filter(None, map(describe, events)):
        print(line)
    print(
        f"\nStatut     : {result.status} · itérations de l'orchestrateur : {result.iterations}"
        f" · coût total : {result.cost_usd:.5f} $"
    )
    store = loom.store.inner
    if isinstance(store, JsonlEventStore):
        print(f"Journal    : {store.path(DEFAULT_TENANT, result.session_id)}")
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
