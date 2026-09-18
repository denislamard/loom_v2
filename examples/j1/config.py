# SPDX-License-Identifier: Apache-2.0
"""Phase 1.5 : l'agent demo, décrit par une config plutôt qu'en Python.

    uv run python examples/j1/config.py
    uv run python examples/j1/config.py --schema        # JSON Schema du fichier racine

La config est dans ``examples/j1/demo/`` : ``loom.yaml``, ``agents/demo.yaml``,
``prompts/demo.md`` et ``outils.py``. Le modèle y est simulé (``sdk: fake``),
donc l'exemple tourne sans clé ni réseau. Le journal est écrit en JSONL dans
``examples/j1/demo/data/`` (ignoré par git).

C'est cette même config que reprendront la CLI, l'API REST et le serveur MCP
de la phase 1.6.
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from loom_ia.adapters.stores import JsonlEventStore
from loom_ia.config import ConfigError, config_json_schema, load_config
from loom_ia.core.model import DEFAULT_TENANT
from loom_ia.engine import begin_run, drive
from loom_ia.runtime import apply_logging, build_agent, create_event_store, load_registry

CONFIG = Path(__file__).parent / "demo" / "loom.yaml"
QUESTION = "Combien font 12 fois 7, plus 3 ?"


async def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Agent demo décrit par une configuration")
    parser.add_argument("--schema", action="store_true", help="affiche le JSON Schema et sort")
    parser.add_argument("--config", type=Path, default=CONFIG)
    args = parser.parse_args(argv)

    if args.schema:
        print(json.dumps(config_json_schema(), ensure_ascii=False, indent=2))
        return 0

    try:
        config = load_config(args.config)
    except ConfigError as error:
        print(f"Configuration : {error}", file=sys.stderr)
        return 2

    apply_logging(config)
    registry = load_registry(config)
    print(f"Config     : {args.config}")
    print(f"Modèles    : {', '.join(spec.id for spec in config.models)}")
    print(f"Agents     : {', '.join(agent.name for agent in config.agents)}")
    print(f"Outils     : {', '.join(registry.names)}\n")

    store = create_event_store(config)
    agent = build_agent(config, "demo", store, registry=registry)
    print(f"> {QUESTION}")
    try:
        run = await begin_run(agent.context, QUESTION)
        state = await drive(agent.context, run.run_id)
    finally:
        await agent.aclose()

    print(f"\nRéponse    : {state.output.text if state.output else '—'}")
    print(f"Statut     : {state.status}, itérations : {state.iterations}")
    if isinstance(store, JsonlEventStore):
        print(f"Journal    : {store.path(DEFAULT_TENANT, run.session_id)}")
    await store.aclose()
    return 0 if state.error is None else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
