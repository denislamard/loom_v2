# SPDX-License-Identifier: Apache-2.0
"""Phase 4.5 : le même scénario par les trois accès.

    uv run --extra http --extra mcp python examples/j4/acces.py
    uv run --extra http --extra mcp python examples/j4/acces.py --cas rest
    uv run --env-file .env --extra http --extra mcp --extra anthropic \\
        --extra openai python examples/j4/acces.py --reel

Config : ``examples/j4/relance/``, à laquelle l'exemple ajoute en code
l'outil sensible — ``envoyer_email``, en ``approval: always`` — et rend le
rôle non terminal pour que l'orchestrateur reçoive l'e-mail et l'envoie. Les
fichiers de ``relance/`` ne changent pas.

Un run qui attend un humain n'est pas une panne : c'est un état, et chaque
accès a sa façon de le rendre et de le trancher.

* **Python** (``--cas python``) : ``submit()`` rend un identifiant sans
  attendre — le run est déjà au journal. Il se met en pause ; ``session()``
  dit ce qu'il faut trancher pour que la conversation avance, ``approve()``
  tranche, et le run repart.
* **REST** (``--cas rest``) : le même déroulé par HTTP, sur un serveur monté
  ici pour l'occasion. ``POST …/runs`` avec ``background: true`` rend 202 et
  l'identifiant ; ``POST …/runs/<id>/approve`` demande la portée ``approve``
  — une clé qui ne l'a pas se fait refuser, et c'est **l'identité de la clé**
  qui signe la décision au journal. ``GET …/sessions/<id>`` rend la fiche.
* **MCP** (``--cas mcp``) : ``approve`` n'est jamais un outil MCP (#39) —
  le LLM du client validerait ce qu'il demande. Deux chemins, selon le
  client : s'il déclare l'``elicitation``, la demande part en formulaire et
  la réponse tranche **dans la boucle**, sans que le run passe par
  ``PAUSED`` ; sinon l'outil **rend la main aussitôt**, sans erreur, avec
  ``status: paused``, le ``run_id`` et ce qui attend — un humain tranche
  ailleurs, puis ``run_status`` relit le run.

La CLI tranche de la même façon (``loom approve <run_id> --by …``), et
l'exemple affiche la commande ; il ne la lance pas, parce que l'outil
sensible vit dans ce fichier et non dans la config que la commande
chargerait.
"""

import argparse
import asyncio
import json
import os
import socket
import sys
import urllib.error
import urllib.request
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from loom_ia.access.api import Loom, RunResult
from loom_ia.adapters.models import ModelConfigError
from loom_ia.config import (
    ApiKey,
    ConfigError,
    LoomConfig,
    SecurityConfig,
    fingerprint,
    load_config,
    new_api_key,
)
from loom_ia.core.events import Event, ToolCompleted
from loom_ia.core.model import RunId, SessionId, new_id
from loom_ia.runtime import apply_logging, prompt_text
from loom_ia.tools import tool

CONFIG = Path(__file__).parent / "relance" / "loom.yaml"
DEMANDE = "Relance le client du devis D-2026-042, sur un ton cordial."
CAS = ("python", "rest", "mcp")
ENVOI = "envoyer_email"
CLIENTE = "mme.martin@example.com"
ARTISAN = "l'artisan"
# Au-delà, l'attente d'un état abandonne : l'exemple ne doit pas pendre.
ATTENTE_MAX = 120.0

# Ce que l'outil a vraiment fait, hors du journal : un envoi ne se défait pas.
BOITE: list[str] = []

CONSIGNE_ENVOI = (
    "\n\nUne fois la relance rédigée, envoie-la avec `envoyer_email` : objet "
    "et corps tels que le rôle les a produits, destinataire "
    f"`{CLIENTE}`. N'annonce pas l'envoi avant de l'avoir fait.\n"
)


@tool(name=ENVOI, side_effects="irreversible")
async def envoyer_email(destinataire: str, objet: str = "", corps: str = "") -> str:
    """Envoie l'e-mail de relance au client. Irréversible : parti, il est parti."""
    BOITE.append(destinataire)
    return f"Envoyé à {destinataire} — objet « {objet[:50]} »"


MAIN: list[dict[str, Any]] = [
    {
        "text": "Je relis le devis.",
        "tool_calls": [{"name": "chercher_devis", "arguments": {"numero": "D-2026-042"}}],
    },
    {"tool_calls": [{"name": "rediger_relance", "arguments": {"ton": "cordial"}}]},
    {
        "text": "L'e-mail est prêt, je l'envoie.",
        "tool_calls": [
            {
                "name": ENVOI,
                "arguments": {
                    "destinataire": CLIENTE,
                    "objet": "Votre devis D-2026-042",
                    "corps": "Bonjour Madame Martin, …",
                },
            }
        ],
    },
    {"text": "La relance du devis D-2026-042 est partie à Mme Martin."},
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

# Clés de l'accès REST (#39). Celle de l'atelier peut trancher ; celle du
# tableau de bord lit et lance, sans jamais approuver.
CLE_ATELIER = new_api_key()
CLE_TABLEAU = new_api_key()


def shown(path: Path) -> str:
    return os.path.relpath(path)


def adjusted(config: LoomConfig, *, reel: bool) -> tuple[LoomConfig, str]:
    """Ajoute l'outil sensible à l'agent, rend le rôle non terminal, déclare les clés."""
    base = "relance_reel" if reel else "relance"
    spec = next(a for a in config.agents if a.name == base)
    main = spec.main.model_copy(
        update={"system": prompt_text(spec.main) + CONSIGNE_ENVOI, "system_file": None}
    )
    roles = tuple(
        role.model_copy(update={"terminal": False}) if role.name == "rediger_relance" else role
        for role in spec.roles
    )
    agent = spec.model_copy(
        update={"main": main, "roles": roles, "tools": (*spec.tools, _envoi(spec))}
    )
    agents = tuple(agent if a.name == base else a for a in config.agents)
    models = list(config.models)
    if not reel:
        models = [
            _script(m, MAIN)
            if m.id == "FAKE_MAIN"
            else _script(m, ROLE)
            if m.id == "FAKE_ROLE"
            else m
            for m in models
        ]
    return (
        config.model_copy(
            update={"models": tuple(models), "agents": agents, "security": _security()}
        ),
        base,
    )


def _script(model: Any, replies: list[dict[str, Any]]) -> Any:
    return model.model_copy(update={"params": {**model.params, "script": replies}})


def _envoi(spec: Any) -> Any:
    """Référence à l'outil sensible, telle que l'écrirait la config."""
    return spec.tools[0].model_copy(
        update={
            "python": ENVOI,
            "side_effects": "irreversible",
            "idempotent": None,
            "approval": "always",
        }
    )


def _security() -> SecurityConfig:
    """Deux clés : l'une tranche, l'autre non. La config n'en garde que l'empreinte."""
    return SecurityConfig(
        api_keys=(
            ApiKey(
                id="atelier",
                hash=fingerprint(CLE_ATELIER),
                scopes=("run", "read", "approve"),
            ),
            ApiKey(id="tableau", hash=fingerprint(CLE_TABLEAU), scopes=("run", "read")),
        )
    )


# --- Lecture du journal -------------------------------------------------------


def outcome(result: RunResult) -> str:
    if result.ok:
        return "terminé"
    if result.error_type:
        return f"{result.status} ({result.error_type})"
    return str(result.status)


def envoi(events: list[Event]) -> str:
    """Ce que l'envoi est devenu : parti, ou jamais tenté."""
    finis = [
        e.payload
        for e in events
        if e.type == "tool.completed" and e.facets.get("tool_name") == ENVOI
    ]
    if not finis:
        return "jamais tenté"
    fini = finis[-1]
    assert isinstance(fini, ToolCompleted)
    texte = "".join(getattr(block, "text", "") for block in fini.output.blocks)
    return texte.strip()


def decide_par(events: list[Event], type_: str) -> str:
    """Qui a tranché, tel que le journal le garde — c'est tout l'audit qu'il y aura."""
    auteurs = [str(e.facets.get("by")) for e in events if e.type == type_]
    return ", ".join(auteurs) or "—"


def pauses(events: list[Event]) -> int:
    return len([e for e in events if e.facets.get("to_state") == "paused"])


async def attendre(loom: Loom, run_id: RunId, session: SessionId, *etats: str) -> RunResult:
    """Relit le run jusqu'à l'un des états attendus, sans dépasser ``ATTENTE_MAX``."""
    limite = asyncio.get_running_loop().time() + ATTENTE_MAX
    while True:
        result = await loom.result(run_id, session_id=session)
        if str(result.status) in etats:
            return result
        if asyncio.get_running_loop().time() > limite:
            raise TimeoutError(f"Run {run_id} : {result.status}, attendu {etats}")
        await asyncio.sleep(0.05)


# --- Python -------------------------------------------------------------------


async def par_python(loom: Loom, agent: str, session: SessionId) -> None:
    print("— Python : le run part en arrière-plan, l'artisan tranche, le run repart\n")
    run_id = await loom.submit(agent, DEMANDE, session_id=session)
    print(f"  submit    : {run_id} — déjà inscrit au journal, rendu sans attendre")
    await loom.drain()

    fiche = await loom.session(session)
    attente = fiche.pending_approvals
    print(f"  fiche     : {len(fiche.runs)} run, {len(attente)} approbation(s) à trancher")
    for asked in attente:
        print(f"    {asked.tool_name} ({asked.call_id}) → {asked.arguments.get('destinataire')}")
    print(f'  en CLI    : loom approve {run_id} --session {session} --by "{ARTISAN}"')

    accordes = await loom.approve(run_id, by=ARTISAN, reason="devis vérifié", session_id=session)
    print(f"  décision  : {len(accordes)} appel accordé par « {ARTISAN} »")
    await loom.drain()
    fin = await loom.result(run_id, session_id=session)
    events = await loom.export_session(session)
    print(f"  reprise   : {outcome(fin)} en {fin.iterations} itération(s)")
    print(f"  envoi     : {envoi(events)}")
    print(f"  boîte     : {len(BOITE)} relance(s) partie(s)")
    print(f"  réponse   : {_ligne(fin)}\n")


# --- REST ---------------------------------------------------------------------


@asynccontextmanager
async def serveur(loom: Loom) -> AsyncGenerator[str]:
    """Monte l'API REST de l'instance sur un port libre, le temps du cas."""
    import uvicorn

    from loom_ia.access.http import create_app

    # La socket est ouverte ici : le port est connu avant que le serveur
    # démarre, et le noyau met les premières requêtes en file d'attente
    # plutôt que de les refuser. Rien à guetter.
    sock = socket.create_server(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(loom), log_config=None))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        yield f"http://127.0.0.1:{port}/v1"
    finally:
        server.should_exit = True
        await task
        sock.close()


def _http(url: str, *, method: str = "GET", body: Any = None, key: str = "") -> tuple[int, Any]:
    """Une requête, sans passer par un proxy : le serveur est sur la machine."""
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        request.add_header("content-type", "application/json")
    if key:
        request.add_header("authorization", f"Bearer {key}")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=30) as response:
            return response.status, json.loads(response.read() or b"null")
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"null")


async def appel(url: str, *, method: str = "GET", body: Any = None, key: str = "") -> Any:
    """La requête, hors de la boucle : urllib est synchrone."""
    code, payload = await asyncio.to_thread(_http, url, method=method, body=body, key=key)
    return code, payload


async def par_rest(loom: Loom, agent: str, session: SessionId) -> None:
    print("— REST : le même déroulé par HTTP, avec une clé d'API et ses portées\n")
    async with serveur(loom) as base:
        print(f"  serveur   : {base}")
        code, accepted = await appel(
            f"{base}/agents/{agent}/runs",
            method="POST",
            body={"message": DEMANDE, "session_id": session, "background": True},
            key=CLE_ATELIER,
        )
        run_id = RunId(str(accepted["run_id"]))
        print(f"  POST runs : {code} — {run_id} ({accepted['status']})")

        await attendre(loom, run_id, session, "paused")
        # Le run a un journal à lui : chaque route le veut en paramètre.
        dans = f"?session_id={session}"
        _, en_attente = await appel(f"{base}/runs/{run_id}{dans}", key=CLE_ATELIER)
        attente = en_attente["pending_approvals"]
        print(f"  GET  run  : {en_attente['status']}, {len(attente)} approbation(s)")

        code, refus = await appel(
            f"{base}/runs/{run_id}/approve{dans}", method="POST", body={}, key=CLE_TABLEAU
        )
        print(f"  approve   : {code} avec la clé « tableau » — {refus['detail']}")

        code, decided = await appel(
            f"{base}/runs/{run_id}/approve{dans}",
            method="POST",
            body={"reason": "devis vérifié"},
            key=CLE_ATELIER,
        )
        print(f"  approve   : {code} avec la clé « atelier » — {len(decided['calls'])} accordé")

        fin = await attendre(loom, run_id, session, "completed", "failed")
        _, fiche = await appel(f"{base}/sessions/{session}", key=CLE_ATELIER)
        events = await loom.export_session(session)
        print(f"  GET  run  : {outcome(fin)}")
        print(
            f"  GET  fiche: {len(fiche['runs'])} run, {len(fiche['pending_approvals'])} en attente"
        )
        print(f"  journal   : accordé par « {decide_par(events, 'approval.granted')} » (la clé)")
        print(f"  envoi     : {envoi(events)}")
        print(f"  réponse   : {_ligne(fin)}\n")


# --- MCP ----------------------------------------------------------------------


async def par_mcp(loom: Loom, agent: str, session: SessionId) -> None:
    print("— MCP : elicitation si le client sait, sinon pause et run_status\n")
    from mcp.shared.context import RequestContext
    from mcp.shared.memory import create_connected_server_and_client_session as connected
    from mcp.types import ElicitRequestParams, ElicitResult, Implementation

    from loom_ia.access.mcp_server import STATUS_TOOL, create_server

    async def formulaire(context: RequestContext[Any, Any], params: Any) -> ElicitResult:
        assert isinstance(params, ElicitRequestParams)
        print(f"    [formulaire] {params.message.splitlines()[0]}")
        return ElicitResult(
            action="accept", content={"decision": "accorder", "motif": "cliente connue"}
        )

    avec = SessionId(f"{session}-elicite")
    client = Implementation(name="atelier-mcp", version="1.0")
    print("  client qui déclare l'elicitation :")
    async with connected(
        create_server(loom), client_info=client, elicitation_callback=formulaire
    ) as mcp:
        result = await mcp.call_tool(agent, {"message": DEMANDE, "session_id": avec})
        structure = result.structuredContent or {}
    events = await loom.export_session(avec)
    print(f"    outil     : {structure.get('status')}, isError={bool(result.isError)}")
    print(f"    pauses    : {pauses(events)} — la décision est venue dans la boucle")
    print(f"    journal   : accordé par « {decide_par(events, 'approval.granted')} »")
    print(f"    envoi     : {envoi(events)}")

    sans = SessionId(f"{session}-pause")
    print("\n  client qui ne la déclare pas :")
    async with connected(create_server(loom), client_info=client) as mcp:
        arrete = await mcp.call_tool(agent, {"message": DEMANDE, "session_id": sans})
        structure = arrete.structuredContent or {}
        run_id = RunId(str(structure["run_id"]))
        attente = structure.get("pending_approvals", [])
        print(f"    outil     : {structure.get('status')}, isError={bool(arrete.isError)}")
        print(f"    rendu     : run {run_id}, {len(attente)} approbation(s) — pas une erreur")

        await loom.approve(run_id, by=ARTISAN, session_id=sans)
        await loom.drain()
        print(f"    un humain tranche ailleurs (REST, ou loom approve {run_id})")
        relu = await mcp.call_tool(STATUS_TOOL, {"run_id": run_id, "session_id": sans})
        structure = relu.structuredContent or {}
    events = await loom.export_session(sans)
    print(f"    {STATUS_TOOL}: {structure.get('status')}")
    print(f"    journal   : accordé par « {decide_par(events, 'approval.granted')} »")
    print(f"    envoi     : {envoi(events)}\n")


def _ligne(result: RunResult) -> str:
    return result.text.splitlines()[0][:70] if result.text else "—"


# --- Déroulé ------------------------------------------------------------------


async def jouer(nom: str, config: LoomConfig, agent: str, session: SessionId) -> None:
    """Un cas, dans son instance : l'outil sensible est posé en code."""
    BOITE.clear()
    async with Loom(config) as loom:
        loom.register(ENVOI, envoyer_email)
        if nom == "python":
            await par_python(loom, agent, session)
        elif nom == "rest":
            await par_rest(loom, agent, session)
        else:
            await par_mcp(loom, agent, session)


async def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Le même scénario par les trois accès")
    parser.add_argument("--reel", action="store_true", help="vrais modèles")
    parser.add_argument("--cas", action="append", choices=CAS, help="cas à jouer (tous par défaut)")
    parser.add_argument("--session", default=None, help="préfixe des sessions")
    args = parser.parse_args(argv)
    cas = tuple(args.cas) if args.cas else CAS
    prefixe = args.session or f"atelier-{new_id()[-8:]}"

    try:
        base = load_config(CONFIG)
    except ConfigError as error:
        print(f"Configuration : {error}", file=sys.stderr)
        return 2
    apply_logging(base)
    print(f"Sessions : {prefixe}-<cas>")
    print(f"Outil    : {ENVOI} (side_effects: irreversible, approval: always)")
    print("Clés     : atelier (run, read, approve) · tableau (run, read)\n")
    for nom in cas:
        try:
            config, agent = adjusted(base, reel=args.reel)
            await jouer(nom, config, agent, SessionId(f"{prefixe}-{nom}"))
        except (ModelConfigError, ConfigError) as error:
            print(f"Configuration : {error}", file=sys.stderr)
            return 2
        except ImportError as error:
            print(f"Extra manquant pour le cas {nom} : {error}", file=sys.stderr)
            return 2
    print(f"Export : uv run loom --config {shown(CONFIG)} sessions list")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
