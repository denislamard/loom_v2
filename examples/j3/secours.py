# SPDX-License-Identifier: Apache-2.0
"""Phase 3.5a : l'orchestrateur tombe en panne, son secours prend la suite ; le disjoncteur s'ouvre.

    uv run python examples/j3/secours.py                  # trois runs, modèles simulés
    uv run python examples/j3/secours.py --runs 1
    uv run --env-file .env --extra anthropic --extra openai \\
        python examples/j3/secours.py --reel

Même config que les exemples précédents (``examples/j3/relance/``). L'exemple
y ajoute un modèle en panne, copie de celui de l'orchestrateur, et le donne
à l'orchestrateur, avec pour secours le modèle habituel (``fallbacks``) :

- en simulé, ``FAKE_PANNE`` : son script lève une surcharge
  (``error: overloaded``) à chaque appel ;
- avec ``--reel``, ``M3_PANNE`` : son adresse (``base_url``) ne répond pas,
  et chaque tentative échoue en ``transient`` ; le secours est MiniMax-M3.

Son disjoncteur est resserré à deux échecs de suite, pour 60 s. Les runs se
suivent dans la même instance ``Loom``, dont les disjoncteurs sont communs à
tous les runs :

1. le modèle en panne échoue après ses tentatives : ``model.fell_back``, et
   le run se termine sur le secours ;
2. deuxième échec : le disjoncteur s'ouvre (``circuit.opened``), bascule ;
3. le modèle en panne est écarté sans être appelé (motif ``circuit_open``).

Dans un run, une fois la bascule faite, l'orchestrateur reste sur le secours
(adhérence) ; le coût suit le tarif du secours. Variante (c) du jalon,
seconde moitié.
"""

import argparse
import asyncio
import json
import os
import sys
from collections import Counter
from pathlib import Path

from loom_ia.access.api import Loom, RunResult
from loom_ia.access.progress import Progress
from loom_ia.adapters.models import ModelConfigError
from loom_ia.adapters.stores import JsonlEventStore
from loom_ia.agents import AgentSpec
from loom_ia.config import ConfigError, LoomConfig, load_config
from loom_ia.core.events import CircuitOpened, Event, ModelFellBack, ModelResponded, ModelRetried
from loom_ia.core.model import DEFAULT_TENANT, CircuitBreaker, RetryPolicy
from loom_ia.runtime import apply_logging

CONFIG = Path(__file__).parent / "relance" / "loom.yaml"
QUESTION = "Relance le client du devis D-2026-042, sur un ton cordial."
# Adresse locale sans serveur : la connexion est refusée aussitôt.
INJOIGNABLE = "http://127.0.0.1:9/anthropic"
DISJONCTEUR = CircuitBreaker(failures=2, cooldown=60)


def with_outage(config: LoomConfig, agent: str, *, reel: bool) -> tuple[LoomConfig, str, str]:
    """Config où l'orchestrateur de ``agent`` est en panne, avec son modèle habituel en secours."""
    spec = next(a for a in config.agents if a.name == agent)
    usual = spec.main.model
    down = "M3_PANNE" if reel else "FAKE_PANNE"
    changes: dict[str, object] = {
        "id": down,
        "retry": RetryPolicy(max_attempts=2, initial_delay=0.5),
        "circuit_breaker": DISJONCTEUR,
    }
    if reel:
        changes["base_url"] = INJOIGNABLE
    else:
        changes["params"] = {"script": [{"error": "overloaded"}]}
    outage = config.model_spec(usual).model_copy(update=changes)
    main = spec.main.model_copy(update={"model": down, "fallbacks": (usual,)})
    agents: list[AgentSpec] = [
        a.model_copy(update={"main": main}) if a.name == agent else a for a in config.agents
    ]
    updated = config.model_copy(
        update={"models": (*config.models, outage), "agents": tuple(agents)}
    )
    return updated, down, usual


def calls(events: list[Event]) -> str:
    """Appels de modèle du run, par rôle et par modèle."""
    counted = Counter(
        (e.role or "?", e.payload.model_id) for e in events if isinstance(e.payload, ModelResponded)
    )
    return " · ".join(f"[{role}] {model} ({n})" for (role, model), n in counted.items())


def shown(path: Path) -> str:
    """Chemin relatif au dossier courant, pour une commande à copier."""
    return os.path.relpath(path)


async def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Modèle de secours et disjoncteur")
    parser.add_argument("--reel", action="store_true", help="vrais modèles")
    parser.add_argument("--runs", type=int, default=3, help="runs à la suite (défaut : 3)")
    parser.add_argument("--question", default=QUESTION)
    args = parser.parse_args(argv)
    if args.runs < 1:
        parser.error("--runs : au moins 1")
    agent = "relance_reel" if args.reel else "relance"

    try:
        config, down, usual = with_outage(load_config(CONFIG), agent, reel=args.reel)
    except ConfigError as error:
        print(f"Configuration : {error}", file=sys.stderr)
        return 2
    apply_logging(config)

    print(f"> {args.question}")
    print(
        f"  orchestrateur : {down} (en panne) → secours {usual} ; disjoncteur : "
        f"{DISJONCTEUR.failures} échecs de suite, écarté {DISJONCTEUR.cooldown:g} s\n"
    )
    results: list[RunResult] = []
    async with Loom(config) as loom:
        for number in range(1, args.runs + 1):
            try:
                result = await loom.run(agent, args.question)
            except (ModelConfigError, ConfigError) as error:
                print(f"Configuration : {error}", file=sys.stderr)
                return 2
            results.append(result)
            events = await loom.events(result.run_id)
            state = await loom.state(result.run_id)
            retried = sum(isinstance(e.payload, ModelRetried) for e in events)
            print(
                f"Run {number} · {result.status} · {result.iterations} itération(s) · "
                f"{result.cost_usd:.5f} $ · tentatives ratées : {retried}"
            )
            progress = Progress()
            for event in events:
                if isinstance(event.payload, ModelFellBack | CircuitOpened):
                    print(f"  {progress.line(event)}")
            print(f"  · appels : {calls(events)}")
            print(f"  · modèle retenu pour la suite du run : {state.models or '—'}\n")

    result = results[-1]
    print("Réponse du dernier run :")
    if result.data is not None:
        print(json.dumps(result.data, ensure_ascii=False, indent=2))
    else:
        print(result.text or result.error or "—")
    store = loom.store.inner
    if isinstance(store, JsonlEventStore):
        print(f"\nJournal    : {store.path(DEFAULT_TENANT, result.session_id)}")
        print(f"Rapport    : uv run loom --config {shown(CONFIG)} report {result.run_id}")
    return 0 if all(r.ok for r in results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
