# SPDX-License-Identifier: Apache-2.0
"""Phase 4.1a : une conversation sur deux runs, un historique matérialisé, une session supprimée.

    uv run python examples/j4/sessions.py                  # conversation sur deux runs
    uv run python examples/j4/sessions.py --paralleles     # deux runs lancés en même temps
    uv run python examples/j4/sessions.py --purger         # puis suppression RGPD
    uv run --extra sqlite python examples/j4/sessions.py --sqlite
    uv run --env-file .env --extra anthropic --extra openai \\
        python examples/j4/sessions.py --reel

Config : ``examples/j4/relance/``, reprise de celle du jalon J3 (politiques,
contrat de sortie, juge, budget) et complétée au fil des phases de J4.

Deux runs partagent un journal de session. Le premier demande une relance
pour le devis D-2026-042 ; le second dit seulement « rends-la plus brève ».
Le numéro n'est plus dans la demande : c'est l'historique de la session qui
le rappelle au modèle, qui appelle le rôle sans qu'on ait à le lui redire.

Le rôle, lui, ne voit que le run : il déclare avoir besoin d'un résultat de
``chercher_devis`` et son premier appel est refusé, le temps que
l'orchestrateur cherche le devis. Le contexte d'un rôle ouvert à la session
(``scope: session``) arrive en 4.1b.

À la fin d'un run, l'historique déjà calculé est écrit dans le journal
(``session.snapshot``, §11.2) dès qu'il fait gagner ``sessions.snapshot_every``
événements de relecture. Ce n'est qu'une vue : l'exemple vérifie que
l'historique reconstruit sans les marqueurs est exactement le même.

``--paralleles`` lance deux runs en même temps sur une même session : ils
écrivent par le même écrivain, et les numéros de séquence restent contigus
(#22). ``--purger`` supprime ensuite la session, journal et fichiers (F7).
"""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from loom_ia.access.api import Loom, RunResult
from loom_ia.access.progress import Progress
from loom_ia.adapters.models import ModelConfigError
from loom_ia.config import ConfigError, LoomConfig, load_config
from loom_ia.config.models import EventsStorage
from loom_ia.core.events import Event, SessionSnapshot, ToolCalled
from loom_ia.core.model import SessionId, new_id
from loom_ia.core.projections import history
from loom_ia.runtime import apply_logging

CONFIG = Path(__file__).parent / "relance" / "loom.yaml"
PREMIERE = "Relance le client du devis D-2026-042, sur un ton cordial."
SECONDE = "Rends-la plus brève, trois phrases au plus."


def shown(path: Path) -> str:
    """Chemin relatif au dossier courant, pour une commande à copier."""
    return os.path.relpath(path)


def storage(config: LoomConfig) -> LoomConfig:
    """Journal SQLite à la place des fichiers JSONL, dans le même dossier."""
    base = config.base_dir or CONFIG.parent
    events = EventsStorage(backend="sqlite", path=base / "data" / "sessions.sqlite3")
    return config.model_copy(
        update={"storage": config.storage.model_copy(update={"events": events})}
    )


def deroule(events: list[Event]) -> list[str]:
    progress = Progress()
    return [line for line in (progress.line(event) for event in events) if line]


def reponse(result: RunResult) -> str:
    if isinstance(result.data, dict):
        objet = result.data.get("objet", "")
        corps = result.data.get("corps", "")
        return f"objet : {objet}\n{corps}"
    return result.text or result.error or "—"


def cherche(events: list[Event], run_id: str) -> list[dict[str, Any]]:
    """Arguments des appels à ``chercher_devis`` d'un run."""
    return [
        dict(event.payload.arguments)
        for event in events
        if event.run_id == run_id
        and isinstance(event.payload, ToolCalled)
        and event.payload.tool_name == "chercher_devis"
    ]


def marqueurs(events: list[Event]) -> list[tuple[int, SessionSnapshot]]:
    return [
        (event.seq, event.payload) for event in events if isinstance(event.payload, SessionSnapshot)
    ]


async def conversation(loom: Loom, agent: str, session: SessionId) -> tuple[RunResult, RunResult]:
    first = await loom.run(agent, PREMIERE, session_id=session)
    second = await loom.run(agent, SECONDE, session_id=session)
    return first, second


async def paralleles(loom: Loom, agent: str, session: SessionId) -> list[RunResult]:
    """Deux runs lancés en même temps sur la même session."""
    return list(
        await asyncio.gather(
            loom.run(agent, PREMIERE, session_id=session),
            loom.run(agent, PREMIERE, session_id=session),
        )
    )


async def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Sessions : conversation, snapshot, suppression")
    parser.add_argument("--reel", action="store_true", help="vrais modèles")
    parser.add_argument("--sqlite", action="store_true", help="journal SQLite (extra sqlite)")
    parser.add_argument(
        "--paralleles", action="store_true", help="deux runs en même temps sur la session"
    )
    parser.add_argument("--purger", action="store_true", help="supprime la session à la fin (RGPD)")
    parser.add_argument("--session", default=None, help="session à rejoindre ou à créer")
    args = parser.parse_args(argv)
    agent = "relance_reel" if args.reel else "relance"
    session = SessionId(args.session or f"atelier-{new_id()[-8:]}")

    try:
        config = load_config(CONFIG)
    except ConfigError as error:
        print(f"Configuration : {error}", file=sys.stderr)
        return 2
    if args.sqlite:
        config = storage(config)
    apply_logging(config)

    async with Loom(config) as loom:
        print(f"Session    : {session}")
        print(f"Journal    : {config.storage.events.backend}\n")
        try:
            if args.paralleles:
                results = await paralleles(loom, agent, session)
            else:
                results = list(await conversation(loom, agent, session))
        except (ModelConfigError, ConfigError) as error:
            print(f"Configuration : {error}", file=sys.stderr)
            return 2
        events = await loom.export_session(session)
        listed = await loom.sessions()
        removed = await loom.delete_session(session) if args.purger else None

    questions = [PREMIERE, PREMIERE] if args.paralleles else [PREMIERE, SECONDE]
    for number, (question, result) in enumerate(
        zip(questions, results, strict=True),
        start=1,
    ):
        print(f"--- Run {number} : {question}")
        print(f"{reponse(result)}\n")
        for line in deroule([e for e in events if e.run_id == result.run_id]):
            print(f"  {line}")
        print(
            f"  statut {result.status} · {result.iterations} itération(s) · {result.cost_usd:.5f} $"
        )
        print()

    if not args.paralleles:
        # Le second run n'a plus le numéro dans sa demande : il le tient de
        # l'historique de la session.
        appels = cherche(events, results[1].run_id)
        print(f"Le second run a cherché : {json.dumps(appels, ensure_ascii=False)}")
        assert any(call.get("numero") == "D-2026-042" for call in appels), (
            "le second run n'a pas retrouvé le devis dans l'historique"
        )

    seqs = [event.seq for event in events]
    print(f"Journal    : {len(events)} événements, seq {seqs[0]} à {seqs[-1]}")
    assert seqs == list(range(1, len(events) + 1)), "numéros de séquence non contigus"

    sans = [event for event in events if event.category != "session"]
    print(f"Historique : {len(history(events))} messages, identique sans les marqueurs : ", end="")
    print("oui" if history(events) == history(sans) else "NON")
    for seq, snapshot in marqueurs(events):
        print(
            f"  · session.snapshot (seq {seq}) : couvre le journal jusqu'à "
            f"{snapshot.up_to_seq}, {len(snapshot.messages)} messages, "
            f"~{snapshot.tokens} tokens"
        )

    print(f"\nSessions   : {', '.join(record.session_id for record in listed) or 'aucune'}")
    if removed is not None:
        print(
            f"Supprimée  : {removed.session_id} — {removed.events} événement(s), "
            f"{removed.artifacts} fichier(s)"
        )
    else:
        print(f"Export     : uv run loom --config {shown(CONFIG)} sessions export {session}")
        print(f"Suppression: uv run loom --config {shown(CONFIG)} sessions delete {session}")
    return 0 if all(result.ok for result in results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
