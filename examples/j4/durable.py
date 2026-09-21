# SPDX-License-Identifier: Apache-2.0
"""Phase 4.2a : arrêter un run — à la demande, ou parce que le délai est passé.

    uv run python examples/j4/durable.py                    # les trois cas
    uv run python examples/j4/durable.py --cas delai
    uv run --env-file .env --extra anthropic --extra openai \\
        python examples/j4/durable.py --reel

Config : ``examples/j4/relance/``, à laquelle l'exemple ajoute en code un
outil lent — ``consulter_erp`` — et les agents qui s'en servent. Les fichiers
de ``relance/`` ne changent pas.

Trois façons pour un run de s'arrêter avant sa réponse, et elles ne se
ressemblent pas :

* **annulé** (``--cas annulation``) : ``Loom.cancel()`` interrompt le pilotage
  puis écrit ``run.cancelled``. C'est **terminal** — le run ne se reprend pas,
  et une seconde annulation ne fait rien.
* **expiré** (``--cas delai``) : l'agent a un ``timeout`` et son étape le
  dépasse. Le run se clôt sur ``run.failed`` avec ``error_type: timeout``.
  Clos, donc lui non plus ne repart pas : c'est un **nouveau** run qui
  poursuit, en retrouvant la conversation dans l'historique de la session.
* **interrompu** (``--cas interruption``) : l'appelant abandonne, ou le
  process meurt. Rien n'est écrit, le run reste dans son dernier état
  actionnable, et ``resume()`` le termine là où il s'était arrêté.

Le délai ne borne pas l'horloge mais le **temps de pilotage cumulé**
(``step.completed``) : l'attente en file et le temps entre un plantage et sa
reprise ne comptent pas.
"""

import argparse
import asyncio
import os
import sys
import time
from contextlib import suppress
from pathlib import Path
from typing import Any

from loom_ia.access.api import Loom, RunResult, UnknownSession
from loom_ia.adapters.models import ModelConfigError
from loom_ia.config import ConfigError, LoomConfig, load_config
from loom_ia.core.events import Event, RunCancelled, RunFailed, ToolCalled, ToolCompleted
from loom_ia.core.model import (
    ModelSpec,
    RunId,
    SessionId,
    new_id,
    new_run_id,
)
from loom_ia.core.projections import fold
from loom_ia.runtime import apply_logging, prompt_text
from loom_ia.tools import tool

CONFIG = Path(__file__).parent / "relance" / "loom.yaml"
DEMANDE = "Relance le client du devis D-2026-042, sur un ton cordial."
SUITE = "Reprends, plus brièvement."
# Latence de l'ERP et délai de l'agent pressé. En simulé, le premier appel de
# modèle est instantané : un délai très court suffit à couper l'ERP. En réel,
# il faut laisser ce premier appel aboutir — sinon c'est lui qui serait coupé,
# et l'exemple montrerait autre chose que ce qu'il raconte.
LATENCE = 5.0
LATENCE_REELLE = 10.0
DELAI = 0.4
DELAI_REEL = 6.0
# Latence effective de l'ERP, posée par ``main`` selon ``--reel``.
_latence = LATENCE
# De quoi laisser un vrai modèle répondre avant d'arrêter le run.
ATTENTE_MAX = 60.0
CAS = ("annulation", "delai", "interruption")
ERP = "consulter_erp"
FINALS = frozenset({"completed", "failed", "cancelled"})
CONSIGNE_ERP = (
    "\n\nAvant toute chose, appelle `consulter_erp` : l'ERP dit si le devis a "
    "déjà été payé. Ne rédige rien avant d'avoir sa réponse.\n"
)


@tool
async def consulter_erp(secondes: float | None = None) -> str:
    """Consulte l'ERP de l'artisan : lent, et sans effet de bord.

    Sans argument, il prend ``_latence`` : un vrai modèle choisit ses
    arguments, donc c'est le défaut qui doit suivre `--reel`.
    """
    await asyncio.sleep(_latence if secondes is None else secondes)
    return "ERP : aucun paiement reçu pour ce devis."


MAIN: list[dict[str, Any]] = [
    {
        "text": "Je vérifie l'ERP.",
        "tool_calls": [{"name": "consulter_erp", "arguments": {}}],
    },
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


def adjusted(config: LoomConfig, *, reel: bool) -> tuple[LoomConfig, str, str]:
    """Ajoute l'outil lent aux agents, et un jumeau qui porte un délai.

    Rend la config, le nom de l'agent sans délai et celui de l'agent qui en a
    un. Les deux partagent tout le reste : mêmes modèles, mêmes politiques.
    """
    base = "relance_reel" if reel else "relance"
    spec = next(a for a in config.agents if a.name == base)
    # Sans consigne, un vrai modèle ignore l'ERP : il cherche le devis et
    # délègue, comme le lui dit le prompt de `relance/`. L'exemple raconterait
    # alors autre chose que ce qu'il montre (vu au run réel du 21/09).
    main = spec.main.model_copy(
        update={"system": prompt_text(spec.main) + CONSIGNE_ERP, "system_file": None}
    )
    lent = spec.model_copy(update={"main": main, "tools": (*spec.tools, _erp())})
    presse = lent.model_copy(
        update={"name": f"{base}_presse", "timeout": DELAI_REEL if reel else DELAI}
    )
    agents = [lent if a.name == base else a for a in config.agents]
    models = list(config.models)
    if not reel:

        def script(spec: ModelSpec, replies: list[dict[str, Any]]) -> ModelSpec:
            return spec.model_copy(update={"params": {**spec.params, "script": replies}})

        models = [
            script(m, MAIN)
            if m.id == "FAKE_MAIN"
            else script(m, ROLE)
            if m.id == "FAKE_ROLE"
            else m
            for m in models
        ]
    updated = config.model_copy(update={"models": tuple(models), "agents": (*agents, presse)})
    return updated, base, presse.name


def _erp() -> Any:
    """Référence à l'outil ``consulter_erp``, telle que l'écrirait la config."""
    spec = next(a for a in load_config(CONFIG).agents if a.name == "relance").tools[0]
    return spec.model_copy(update={"python": "consulter_erp"})


def piloting(events: list[Event], run_id: RunId) -> float:
    """Temps de pilotage cumulé du run, en secondes."""
    return fold([e for e in events if e.run_id == run_id], run_id).active_ms / 1000


def unfinished(events: list[Event], run_id: RunId) -> int:
    """Étapes commencées et jamais terminées : l'effet coupé en plein vol."""
    own = [e for e in events if e.run_id == run_id]
    return len([e for e in own if e.type == "step.started"]) - len(
        [e for e in own if e.type == "step.completed"]
    )


def outcome(result: RunResult) -> str:
    if result.ok:
        return "terminé"
    if result.error_type:
        return f"{result.status} ({result.error_type})"
    return str(result.status)


async def dans_l_erp(loom: Loom, run_id: RunId, session: SessionId) -> None:
    """Attend que l'appel à l'ERP soit en vol : commencé, pas encore fini.

    Deux façons de se tromper, rencontrées toutes les deux en réel :

    * dormir un temps fixe — le run n'existe pas encore au journal avec de
      vrais modèles, et ``cancel`` lève ``UnknownRun`` ;
    * guetter l'état ``awaiting_tools`` — c'est un état de **passage**. Un
      outil rapide le traverse en quelques dizaines de millisecondes (28 ms
      pour `chercher_devis` au run du 21/09), donc une scrutation le manque
      et attend le suivant.

    On guette donc le fait, pas l'état : un ``tool.called`` de l'ERP sans son
    ``tool.completed``.
    """
    fin = time.monotonic() + ATTENTE_MAX
    while time.monotonic() < fin:
        # La session n'existe pas encore au tout premier tour de boucle.
        events: list[Event] = []
        with suppress(UnknownSession):
            events = [e for e in await loom.export_session(session) if e.run_id == run_id]
        partis = {
            payload.call_id
            for e in events
            if isinstance(payload := e.payload, ToolCalled) and payload.tool_name == ERP
        }
        finis = {payload.call_id for e in events if isinstance(payload := e.payload, ToolCompleted)}
        if partis - finis or any(
            e.type.startswith("run.") and e.type[4:] in FINALS for e in events
        ):
            return
        await asyncio.sleep(0.05)
    raise TimeoutError(f"le run {run_id} n'a pas appelé {ERP} en {ATTENTE_MAX:g} s")


async def annulation(loom: Loom, agent: str, session: SessionId) -> None:
    print("— Annulé : la décision d'arrêter, écrite au journal\n")
    run_id = new_run_id()
    task = asyncio.create_task(loom.run(agent, DEMANDE, session_id=session, run_id=run_id))
    await dans_l_erp(loom, run_id, session)
    print("  l'artisan annule pendant la consultation de l'ERP…")
    arrete = await loom.cancel(run_id, session_id=session, by="l'artisan")
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task
    events = await loom.export_session(session)
    state = fold([e for e in events if e.run_id == run_id], run_id)
    marker = next(
        (p for e in events if isinstance(p := e.payload, RunCancelled) and e.run_id == run_id), None
    )
    assert marker is not None
    print(f"  cancel() : {arrete} — run.cancelled, motif « {marker.reason} », par {marker.by}")
    print(f"  état     : {state.status} (terminal : {state.status.is_terminal})")
    print(
        f"  étapes   : {piloting(events, run_id):.2f} s terminées, "
        f"{unfinished(events, run_id)} coupée en cours"
    )
    encore = await loom.cancel(run_id, session_id=session)
    repris = await loom.resume(run_id, session_id=session)
    print(f"  annuler à nouveau : {encore} · reprendre : {outcome(repris)}\n")


async def delai(loom: Loom, presse: str, session: SessionId, *, reel: bool) -> None:
    limite = DELAI_REEL if reel else DELAI
    latence = LATENCE_REELLE if reel else LATENCE
    print(f"— Expiré : l'agent {presse} a {limite:g} s, l'ERP en prend {latence:g}\n")
    result = await loom.run(presse, DEMANDE, session_id=session)
    events = await loom.export_session(session)
    failed = next(
        (p for e in events if isinstance(p := e.payload, RunFailed) and e.run_id == result.run_id),
        None,
    )
    assert failed is not None
    print(f"  résultat : {outcome(result)}")
    print(f"  message  : {failed.error}")
    print(
        f"  étapes   : {piloting(events, result.run_id):.2f} s terminées, "
        f"{unfinished(events, result.run_id)} coupée en cours"
    )
    repris = await loom.resume(result.run_id, session_id=session)
    print(f"  reprendre ce run : {outcome(repris)} — un run clos reste clos\n")


async def interruption(loom: Loom, agent: str, session: SessionId) -> None:
    print("— Interrompu : personne ne décide, donc rien n'est écrit\n")
    run_id = new_run_id()
    task = asyncio.create_task(loom.run(agent, DEMANDE, session_id=session, run_id=run_id))
    await dans_l_erp(loom, run_id, session)
    print("  l'appelant abandonne (process tué, flux fermé, requête coupée)…")
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task
    events = await loom.export_session(session)
    state = fold([e for e in events if e.run_id == run_id], run_id)
    closes = [e.type for e in events if e.run_id == run_id and e.type.startswith("run.")]
    print(f"  journal  : {closes[-1]} en dernier — aucune clôture")
    print(f"  état     : {state.status} (terminal : {state.status.is_terminal})")
    repris = await loom.resume(run_id, session_id=session)
    print(f"  reprendre : {outcome(repris)} en {repris.iterations} itération(s)")
    print(f"  réponse  : {repris.text.splitlines()[0][:70] if repris.text else '—'}\n")


async def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Arrêter un run : annulation, délai, reprise")
    parser.add_argument("--reel", action="store_true", help="vrais modèles")
    parser.add_argument("--cas", action="append", choices=CAS, help="cas à jouer (tous par défaut)")
    parser.add_argument("--session", default=None, help="session à rejoindre ou à créer")
    args = parser.parse_args(argv)
    cas = tuple(args.cas) if args.cas else CAS
    session = SessionId(args.session or f"atelier-{new_id()[-8:]}")

    try:
        config, agent, presse = adjusted(load_config(CONFIG), reel=args.reel)
    except ConfigError as error:
        print(f"Configuration : {error}", file=sys.stderr)
        return 2
    apply_logging(config)
    global _latence
    _latence = LATENCE_REELLE if args.reel else LATENCE

    async with Loom(config) as loom:
        loom.register("consulter_erp", consulter_erp)
        print(f"Session : {session}")
        print(
            f"Agents  : {agent} (sans délai), {presse} "
            f"(délai {DELAI_REEL if args.reel else DELAI:g} s)\n"
        )
        try:
            for nom in cas:
                if nom == "annulation":
                    await annulation(loom, agent, session)
                elif nom == "delai":
                    await delai(loom, presse, session, reel=args.reel)
                else:
                    await interruption(loom, agent, session)
        except (ModelConfigError, ConfigError) as error:
            print(f"Configuration : {error}", file=sys.stderr)
            return 2
        if "delai" in cas:
            # La conversation continue : le run suivant retrouve l'historique.
            suite = await loom.run(agent, SUITE, session_id=session)
            print(f"La session continue : « {SUITE} » → {outcome(suite)}")
        events = await loom.export_session(session)

    runs = {e.run_id for e in events if e.type == "run.started"}
    print(f"\nJournal : {len(events)} événements, {len(runs)} run(s) dans la session")
    print(f"Export  : uv run loom --config {shown(CONFIG)} sessions export {session}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
