# SPDX-License-Identifier: Apache-2.0
"""State de session : ce que l'application et les outils posent, ce que le modèle reçoit.

    uv run python examples/j7/state.py
    uv run python examples/j7/state.py --session atelier-1

Config : ``examples/j7/relance/``, la relance de devis de J5 pour un seul
artisan. Sa session tient un **state** : un objet JSON qui passe d'un run à
l'autre, déclaré clé par clé dans ``agents/relance.yaml`` — un schéma par
clé, et ce qui la pose.

1. Un run sans ``signature`` est refusé avant de commencer : la clé est
   obligatoire (``required``), et rien n'est écrit au journal.
2. Premier run : l'application pose ``signature`` ; le résultat de
   ``chercher_devis`` pose ``devis_courant`` et ``client_courant`` (``from``),
   sans que l'outil sache rien du state ; ``ton`` vaut ``cordial`` par
   défaut, sans être écrit.
3. Second run, « Refais-la. » : la demande ne cite aucun devis, le state le
   donne ; l'application y pose ``ton: ferme``, que le rôle reçoit.
4. Les deux runs se rejouent à l'identique, state compris.

À chaque requête, l'orchestrateur reçoit le state en dernier message, entre
balises ``<session_state>`` : l'exemple le montre tel que le modèle l'a reçu,
en gardant au journal les échanges bruts (``telemetry.capture.raw_exchanges``).
Le state d'une session se relit aussi en ligne de commande : ``loom state``.
"""

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Mapping
from pathlib import Path

from pydantic import JsonValue

from loom_ia.access.api import Loom, RunResult
from loom_ia.config import ConfigError, LoomConfig, load_config
from loom_ia.core.events import Event, ModelExchanged
from loom_ia.core.model import ModelRequest, SessionId, new_id
from loom_ia.engine import StateRefused
from loom_ia.runtime import apply_logging

CONFIG = Path(__file__).parent / "relance" / "loom.yaml"
AGENT = "relance"
SIGNATURE = "Plomberie Dupont"
PREMIERE = "Relance le client du devis D-2026-042."
SECONDE = "Refais-la."


def shown(path: Path) -> str:
    """Chemin relatif au dossier courant, pour une commande à copier."""
    return os.path.relpath(path)


def avec_echanges(config: LoomConfig) -> LoomConfig:
    """La config, avec au journal ce que chaque modèle a reçu (échanges bruts)."""
    capture = config.telemetry.capture.model_copy(update={"raw_exchanges": True})
    telemetry = config.telemetry.model_copy(update={"capture": capture})
    return config.model_copy(update={"telemetry": telemetry})


def blocs(events: list[Event], run_id: str) -> list[str]:
    """Le dernier message de chaque requête de l'orchestrateur, tel qu'il l'a reçu."""
    vus: list[str] = []
    for event in events:
        payload = event.payload
        # Un échange du rôle porte l'appel qu'il sert (``call_id``) : on ne garde
        # que ceux de l'orchestrateur.
        if event.run_id != run_id or not isinstance(payload, ModelExchanged):
            continue
        if payload.call_id is not None or payload.judge is not None:
            continue
        if payload.request_truncated:
            vus.append("(requête coupée au journal)")
            continue
        request = ModelRequest.model_validate_json(payload.request_body)
        vus.append(request.messages[-1].text)
    return vus


def reponse(result: RunResult) -> str:
    if isinstance(result.data, dict):
        return f"objet : {result.data.get('objet', '')}\n{result.data.get('corps', '')}"
    return result.text or result.error or "—"


def dit(etat: Mapping[str, JsonValue]) -> str:
    return json.dumps(etat, ensure_ascii=False, sort_keys=True)


async def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="State de session : posé, reçu, rejoué")
    parser.add_argument("--session", default=None, help="session à créer (neuve)")
    args = parser.parse_args(argv)
    session = SessionId(args.session or f"atelier-{new_id()[-8:]}")

    try:
        config = avec_echanges(load_config(CONFIG))
    except ConfigError as error:
        print(f"Configuration : {error}", file=sys.stderr)
        return 2
    apply_logging(config)

    async with Loom(config) as loom:
        if session in {record.session_id for record in await loom.sessions()}:
            print(f"Session {session} : déjà au journal ; en choisir une neuve.", file=sys.stderr)
            return 2
        print(f"Session    : {session}\n")
        try:
            await loom.run(AGENT, PREMIERE, session_id=session)
        except StateRefused as refus:
            print(f"--- Sans signature : {refus}")
            sessions = {record.session_id for record in await loom.sessions()}
            print(f"  journal de la session : {'écrit' if session in sessions else 'vide'}\n")
            assert session not in sessions, "un run refusé n'écrit rien"
        else:
            print("Le run sans signature aurait dû être refusé.", file=sys.stderr)
            return 1

        pose = {"signature": SIGNATURE}
        premier = await loom.run(AGENT, PREMIERE, session_id=session, state=pose)
        apres_premier = await loom.session_state(session)
        second = await loom.run(AGENT, SECONDE, session_id=session, state={"ton": "ferme"})
        apres_second = await loom.session_state(session)
        events = await loom.export_session(session)
        rejeux = [await loom.replay(r.run_id, session_id=session) for r in (premier, second)]

    runs = (
        (PREMIERE, {"signature": SIGNATURE}, premier, apres_premier),
        (SECONDE, {"ton": "ferme"}, second, apres_second),
    )
    for numero, (question, pose, result, etat) in enumerate(runs, start=1):
        print(f"--- Run {numero} : {question}")
        print(f"  posé par l'application : {dit(pose)}")
        print("  reçu par l'orchestrateur, en fin de chaque requête :")
        for bloc in blocs(events, result.run_id):
            print("    " + bloc.replace("\n", "\n    "))
        print(f"  state de la session après le run : {dit(etat)}")
        print(f"\n{reponse(result)}\n")

    # Écrit au journal : ce que l'application a posé, ce que chercher_devis a
    # rendu. Le ton par défaut, lui, n'y est jamais.
    assert apres_premier == {
        "signature": SIGNATURE,
        "devis_courant": "D-2026-042",
        "client_courant": "Mme Martin",
    }
    assert apres_second == {**apres_premier, "ton": "ferme"}
    for result, report in zip((premier, second), rejeux, strict=True):
        etat = "à l'identique" if report.identical else f"divergent : {report.divergence}"
        print(f"Rejeu      : run {result.run_id}, {etat}")
    print(f"Relire     : uv run loom --config {shown(CONFIG)} state {session}")
    ok = all(r.ok for r in (premier, second)) and all(r.identical for r in rejeux)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
