# SPDX-License-Identifier: Apache-2.0
"""Phase 4.1b : une conversation qui s'allonge, résumée en tâche de fond.

    uv run python examples/j4/compaction.py                # 4 tours, puis résumé
    uv run python examples/j4/compaction.py --tours 6
    uv run python examples/j4/compaction.py --filet        # le résumé échoue : coupe
    uv run --env-file .env --extra anthropic --extra openai \\
        python examples/j4/compaction.py --reel

Config : ``examples/j4/relance/``, complétée par un bloc ``sessions.compaction``.

Au bout de quelques tours, l'historique dépasse ``over_tokens``. À la fin du
run, un résumé est mis en file ; il tourne comme n'importe quel agent
(``_compaction``, run système dans le journal de la session) et écrit un
``session.compacted``. L'historique repart alors du résumé, les ``keep_last``
derniers tours restant intacts.

Le résumé est contrôlé sans modèle : les repères du segment — références,
adresses, nombres d'au moins trois chiffres — doivent se retrouver dedans.
Le modèle simulé en oublie un au premier essai ; le contrôle le lui renvoie,
et le second passe. Un résumé encore fautif serait gardé, avec
``fidelity: warning``.

``--filet`` met le modèle de résumé en panne et resserre ``hard_tokens`` :
la compaction devient bloquante au démarrage du run suivant, échoue, et les
tours les plus anciens sont retirés (``session.trimmed``).

Les réponses des modèles simulés ne varient pas d'un tour à l'autre : ce que
l'exemple montre est la mécanique de la session, pas la rédaction.
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import Any

from loom_ia.access.api import Loom
from loom_ia.adapters.models import ModelConfigError
from loom_ia.config import ConfigError, LoomConfig, load_config
from loom_ia.core.events import Event, GuardChecked, SessionCompacted, SessionTrimmed
from loom_ia.core.model import ModelSpec, SessionId, new_id
from loom_ia.core.projections import history
from loom_ia.runtime import apply_logging
from loom_ia.sessions import estimate_tokens
from loom_ia.usage import render

CONFIG = Path(__file__).parent / "relance" / "loom.yaml"
TOURS = [
    "Relance le client du devis D-2026-042, sur un ton cordial.",
    "Rends-la plus brève.",
    "Remets un ton plus chaleureux.",
    "Propose-lui d'en parler au téléphone.",
    "Reprends la version courte.",
    "Ajoute une formule de politesse plus soignée.",
]
# L'orchestrateur simulé fait le même travail à chaque tour : il relit le devis,
# puis fait rédiger. Le compte des réponses repart à chaque demande.
MAIN: list[dict[str, Any]] = [
    {
        "text": "Je relis le devis.",
        "tool_calls": [{"name": "chercher_devis", "arguments": {"numero": "D-2026-042"}}],
    },
    {
        "tool_calls": [
            {
                "name": "rediger_relance",
                "arguments": {
                    "ton": "cordial",
                    "consignes": "Cite le numéro du devis D-2026-042 dans l'objet.",
                },
            }
        ]
    },
]
ROLE: list[dict[str, Any]] = [
    {
        "text": (
            '{"objet": "Votre devis D-2026-042", "corps": "Bonjour Madame Martin,\\n\\nJe '
            "reviens vers vous au sujet du devis D-2026-042 de 1 840 € pour le remplacement "
            "de votre chauffe-eau, envoyé le 2 septembre. Avez-vous pu en prendre "
            'connaissance ?\\n\\nBien cordialement,\\nPlomberie Dupont"}'
        )
    },
]


def shown(path: Path) -> str:
    return os.path.relpath(path)


def adjusted(config: LoomConfig, args: argparse.Namespace) -> LoomConfig:
    """Scripts simulés, modèle de résumé réel, et panne du résumé pour ``--filet``."""
    compaction = config.sessions.compaction
    assert compaction is not None, "la config doit déclarer sessions.compaction"

    def script(spec: ModelSpec, replies: list[dict[str, Any]]) -> ModelSpec:
        return spec.model_copy(update={"params": {**spec.params, "script": replies}})

    models = list(config.models)
    if not args.reel:
        models = [
            script(m, MAIN)
            if m.id == "FAKE_MAIN"
            else script(m, ROLE)
            if m.id == "FAKE_ROLE"
            else m
            for m in models
        ]
    if args.filet:
        # Le modèle de résumé tombe en panne : la compaction ne peut plus rien.
        panne = [{"error": "invalid_request"}]
        models = [script(m, panne) if m.id in {"FAKE_RESUME", "HAIKU"} else m for m in models]
        models = [
            m.model_copy(update={"sdk": "fake", "model": "fake-panne"})
            if m.id == "HAIKU" and args.reel
            else m
            for m in models
        ]
    update: dict[str, Any] = {"model": "HAIKU"} if args.reel else {}
    if args.filet:
        update["hard_tokens"] = compaction.over_tokens + 1
    sessions = config.sessions.model_copy(
        update={"compaction": compaction.model_copy(update=update)}
    )
    return config.model_copy(update={"models": tuple(models), "sessions": sessions})


def markers(events: list[Event]) -> list[Event]:
    return [event for event in events if event.category == "session"]


def checks(events: list[Event]) -> list[GuardChecked]:
    return [
        payload
        for event in events
        if isinstance(payload := event.payload, GuardChecked) and payload.guard == "fidelity"
    ]


async def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Compaction d'une session trop longue")
    parser.add_argument("--reel", action="store_true", help="vrais modèles")
    parser.add_argument("--tours", type=int, default=4, help="tours de conversation")
    parser.add_argument(
        "--filet", action="store_true", help="met le résumé en panne : coupe de sécurité"
    )
    parser.add_argument("--session", default=None, help="session à rejoindre ou à créer")
    args = parser.parse_args(argv)
    agent = "relance_reel" if args.reel else "relance"
    session = SessionId(args.session or f"atelier-{new_id()[-8:]}")
    if not 1 <= args.tours <= len(TOURS):
        print(f"--tours : entre 1 et {len(TOURS)}", file=sys.stderr)
        return 2

    try:
        config = adjusted(load_config(CONFIG), args)
    except ConfigError as error:
        print(f"Configuration : {error}", file=sys.stderr)
        return 2
    apply_logging(config)
    compaction = config.sessions.compaction
    assert compaction is not None

    async with Loom(config) as loom:
        print(f"Session    : {session}")
        print(
            f"Compaction : modèle {compaction.model}, au-delà de {compaction.over_tokens} "
            f"tokens, {compaction.keep_last} tours gardés (filet : {compaction.hard_tokens})\n"
        )
        try:
            for number, question in enumerate(TOURS[: args.tours], start=1):
                result = await loom.run(agent, question, session_id=session)
                await loom.drain()
                events = await loom.export_session(session)
                taille = estimate_tokens(history(events))
                statut = "ok" if result.ok else f"échec ({result.error_type})"
                print(f"  tour {number} : {question}")
                print(f"           historique ~{taille} tokens · {statut}")
        except (ModelConfigError, ConfigError) as error:
            print(f"Configuration : {error}", file=sys.stderr)
            return 2
        events = await loom.export_session(session)
        report = await loom.report(session_id=session)

    print("\nMarqueurs de session :")
    for event in markers(events):
        payload = event.payload
        if isinstance(payload, SessionCompacted):
            print(
                f"  · seq {event.seq} session.compacted — couvre jusqu'à {payload.up_to_seq}, "
                f"{payload.kept} tour(s) gardé(s), {payload.tokens_before} → "
                f"{payload.tokens_after} tokens, fidélité {payload.fidelity}, "
                f"{payload.cost_usd:.5f} $"
            )
        elif isinstance(payload, SessionTrimmed):
            print(
                f"  · seq {event.seq} session.trimmed — couvre jusqu'à {payload.up_to_seq}, "
                f"{payload.dropped} message(s) retiré(s) : {payload.reason}"
            )
        else:
            print(f"  · seq {event.seq} {event.type}")

    controles = checks(events)
    if controles:
        print("\nContrôle de fidélité :")
        for check in controles:
            print(f"  · {check.outcome} — {check.reason or 'tous les repères retrouvés'}")

    messages = history(events)
    print(f"\nHistorique : {len(messages)} messages, ~{estimate_tokens(messages)} tokens")
    for message in messages[:1]:
        head = message.text.splitlines()[0] if message.text else ""
        print(f"  premier message ({message.role}) : {head[:80]}")

    print()
    print("\n".join(render(report)))
    print(f"\nExport     : uv run loom --config {shown(CONFIG)} sessions export {session}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
