# SPDX-License-Identifier: Apache-2.0
"""Phase 1.4 : l'agent demo sur un vrai fournisseur, ou sur le modèle ``fake``.

    uv run python examples/j1/modeles.py
    uv run --extra anthropic python examples/j1/modeles.py --sdk anthropic \\
        --base-url https://api.minimax.io/anthropic --model MiniMax-M2 --key-env M3_API_KEY
    uv run --extra openai python examples/j1/modeles.py --sdk openai \\
        --base-url https://api.together.xyz/v1 --model openai/gpt-oss-120b \\
        --key-env TOGETHER_API_KEY

La réponse s'affiche au fil du flux, puis le journal du run, écrit en JSONL
dans ``data/examples/j1/`` (ignoré par git) : c'est là que se lisent les
données brutes du fournisseur (usage, signatures de raisonnement,
``request_hash``). ``--memoire`` garde le journal en mémoire.

La clé est lue dans la variable nommée par ``--key-env`` et n'est jamais
affichée.
"""

import argparse
import asyncio
import sys
from pathlib import Path

from pydantic import JsonValue

from loom_ia.adapters.models import ModelConfigError, create_model_client
from loom_ia.adapters.stores import InMemoryEventStore, JsonlEventStore
from loom_ia.core.events import (
    ModelResponded,
    ModelRetried,
    RunTransitioned,
    StepStarted,
    ToolCompleted,
)
from loom_ia.core.model import (
    DEFAULT_TENANT,
    ModelChunk,
    ModelSpec,
    ReasoningDelta,
    Stopped,
    StreamReset,
    TextDelta,
)
from loom_ia.core.ports import EventStore
from loom_ia.engine import RunContext, ToolExecutor, begin_run, drive
from loom_ia.tools import tool

ROOT = Path("data/examples/j1")
QUESTION = "Combien font 12 fois 7, plus 3 ? Utilise l'outil de calcul."
FAKE_SCRIPT: JsonValue = [
    {"text": "Je calcule.", "tool_calls": [{"name": "calculer", "arguments": {"expr": "12*7+3"}}]},
    {"text": "12 fois 7, plus 3, font 87."},
]


@tool
def calculer(expr: str) -> str:
    """Évalue une expression arithmétique simple (chiffres, + - * / et parenthèses)."""
    if not set(expr) <= set("0123456789+-*/(). "):
        raise ValueError("caractères non autorisés")
    return str(eval(expr, {"__builtins__": {}}))


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Agent demo sur un fournisseur de modèles")
    parser.add_argument("--sdk", choices=["fake", "anthropic", "openai"], default="fake")
    parser.add_argument("--model", default="fake-1")
    parser.add_argument("--base-url")
    parser.add_argument("--key-env", help="variable d'environnement qui contient la clé")
    parser.add_argument("--max-tokens", type=int, default=1024)
    parser.add_argument(
        "--memoire", action="store_true", help="journal en mémoire, rien sur le disque"
    )
    return parser.parse_args(argv)


def build_spec(args: argparse.Namespace) -> ModelSpec:
    return ModelSpec(
        id="DEMO",
        sdk=args.sdk,
        model=args.model,
        base_url=args.base_url,
        api_key_env=args.key_env,
        max_tokens=args.max_tokens,
        params={"script": FAKE_SCRIPT} if args.sdk == "fake" else {},
    )


async def show(chunk: ModelChunk) -> None:
    match chunk:
        case TextDelta(text=text):
            print(text, end="", flush=True)
        case ReasoningDelta(text=text) if text:
            print(f"\x1b[2m{text}\x1b[0m", end="", flush=True)
        case StreamReset(attempt=attempt):
            print(f"\n[nouvelle tentative n°{attempt}]")
        case Stopped():
            print()
        case _:
            pass


async def main(args: argparse.Namespace) -> int:
    spec = build_spec(args)
    try:
        model = create_model_client(spec)
    except ModelConfigError as error:
        print(f"Configuration : {error}", file=sys.stderr)
        return 2
    store: EventStore = InMemoryEventStore() if args.memoire else JsonlEventStore(ROOT)
    ctx = RunContext(
        agent="demo",
        store=store,
        model=model,
        model_spec=spec,
        system="Tu réponds en français, brièvement. Pour tout calcul, utilise l'outil.",
        tools=ToolExecutor([calculer]),
        on_chunk=show,
    )
    try:
        run = await begin_run(ctx, QUESTION)
        print(f"{spec.sdk} / {spec.model}\n> {QUESTION}\n")
        state = await drive(ctx, run.run_id)
    finally:
        await model.aclose()

    print("\n\nJournal")
    if isinstance(store, JsonlEventStore):
        print(f"{store.path(DEFAULT_TENANT, run.session_id)}\n")
    for event in await store.read(DEFAULT_TENANT, run.session_id):
        match event.payload:
            case RunTransitioned(from_state=before, to_state=after):
                detail = f"{before} → {after}"
            case ModelResponded(model_id=model_id, stop_reason=reason, usage=usage):
                detail = f"{model_id}, {reason}, {usage.total_tokens} tokens"
            case StepStarted(step_no=no, effect=effect):
                detail = f"étape {no} : {effect}"
            case ToolCompleted(tool_name=name, output=output):
                detail = f"{name} → {output.as_text}"
            case ModelRetried(attempt=attempt, error_kind=kind, delay_s=delay):
                detail = f"tentative {attempt} : {kind}, attente {delay:.1f} s"
            case _:
                detail = ""
        print(f"{event.seq:>3}  {event.type:<18} {event.status:<7} {detail}")

    print(f"\nStatut : {state.status}")
    if state.error:
        print(f"Erreur : {state.error}")
    print(f"Itérations : {state.iterations}, tokens : {state.usage.total_tokens}")
    await store.aclose()
    return 0 if state.error is None else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main(parse_args(sys.argv[1:]))))
