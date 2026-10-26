# SPDX-License-Identifier: Apache-2.0
"""Phase 5.2b : le serveur MCP en HTTP, et la clé qui dit le client.

    uv run --extra http --extra mcp python examples/j5/serveur_mcp.py
    uv run --extra http --extra mcp python examples/j5/serveur_mcp.py --cas clients
    uv run --extra http --extra mcp python examples/j5/serveur_mcp.py --cas portees
    uv run --extra http --extra mcp python examples/j5/serveur_mcp.py --cas transport
    uv run --env-file .env --extra http --extra mcp --extra anthropic \\
        --extra openai python examples/j5/serveur_mcp.py --reel

Config : ``examples/j5/relance/``, celle de 5.1a, plus des clés posées **en
code** — les autres exemples du jalon gardent une API ouverte.

Ce que le transport change tient en une phrase : **la clé arrive à chaque
requête**. En stdio, rien dans le protocole ne dit au nom de qui on parle, donc
un serveur sert un client, choisi à son lancement (``loom mcp --tenant``). Ici,
un seul serveur les sert tous.

* **clients** : deux artisans sur le **même** serveur. Chacun voit publiés les
  agents que sa clé lui ouvre, et son run va dans son journal à lui.
* **portees** : lancer demande ``run``, relire demande ``read``, et sans
  ``read_content`` une relecture revient masquée — la même règle qu'en REST,
  puisque c'est la même clé. Ce qu'une clé lance, elle le reçoit.
* **transport** : ce que la spec MCP impose — une requête sans clé refusée
  avant le protocole, un ``Origin`` non déclaré refusé, un ``Host`` qui n'est
  pas celui où l'on croit parler refusé aussi.
"""

import argparse
import asyncio
import os
import socket
import sys
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from loom_ia.access import Loom
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
from loom_ia.core.model import SessionId, TenantId, new_id
from loom_ia.runtime import apply_logging

CONFIG = Path(__file__).parent / "relance" / "loom.yaml"
CAS = ("clients", "portees", "transport")

DUPONT = TenantId("dupont-plomberie")
MARTIN = TenantId("martin-chauffage")
DEVIS: dict[TenantId, str] = {DUPONT: "D-2026-042", MARTIN: "D-2026-117"}
ORIGINE = "https://atelier.example"

# Ce qu'un cas reçoit : l'instance, l'adresse du serveur, les jetons, l'agent,
# le préfixe des sessions, et le contrôle des attendus.
type Scene = tuple["Loom", str, dict[str, str], str, str, "Controle"]


def demande(tenant: TenantId) -> str:
    return f"Relance le client du devis {DEVIS[tenant]}, sur un ton cordial."


def shown(path: Path) -> str:
    return os.path.relpath(path)


def titre(texte: str) -> None:
    print(f"\n=== {texte} ===")


def agent_de(args: argparse.Namespace) -> str:
    return "relance_reel" if args.reel else "relance"


class Controle:
    """Ce que chaque appel devait rendre : l'exemple l'annonce, puis le vérifie."""

    def __init__(self) -> None:
        self.ecarts: list[str] = []

    def tient(self, quoi: str, vrai: bool) -> str:
        if not vrai:
            self.ecarts.append(quoi)
        return "oui" if vrai else "NON"

    def code(self, quoi: str, attendu: int, code: int) -> str:
        if code == attendu:
            return str(code)
        self.ecarts.append(f"{quoi} : {code} au lieu de {attendu}")
        return f"{code}   ← attendu {attendu}"

    def bilan(self) -> bool:
        if not self.ecarts:
            print("\nChaque appel a rendu ce que l'exemple annonçait.")
            return True
        print("\nUn appel au moins n'a pas rendu ce qui était annoncé :")
        for ecart in self.ecarts:
            print(f"  {ecart}")
        return False


# --- Les clés de l'exemple, posées en code -----------------------------------


def quatre(config: LoomConfig) -> tuple[LoomConfig, dict[str, str]]:
    """Deux clés de deux artisans, plus deux clés bridées de Dupont."""
    jetons = {nom: new_api_key() for nom in ("dupont", "martin", "supervision", "lecture")}
    keys = (
        ApiKey(
            id="dupont",
            hash=fingerprint(jetons["dupont"]),
            tenant=DUPONT,
            scopes=("run", "read", "read_content"),
        ),
        ApiKey(
            id="martin",
            hash=fingerprint(jetons["martin"]),
            tenant=MARTIN,
            scopes=("run", "read", "read_content"),
        ),
        # Elle voit passer les runs, elle ne lit pas le courrier (5.2a).
        ApiKey(
            id="supervision",
            hash=fingerprint(jetons["supervision"]),
            tenant=DUPONT,
            scopes=("run", "read"),
        ),
        # Elle ne sait que lire : pas de `run`.
        ApiKey(
            id="lecture",
            hash=fingerprint(jetons["lecture"]),
            tenant=DUPONT,
            scopes=("read", "read_content"),
        ),
    )
    mcp = config.server.mcp.model_copy(update={"http": True, "allowed_origins": (ORIGINE,)})
    server = config.server.model_copy(update={"mcp": mcp})
    return config.model_copy(
        update={"security": SecurityConfig(api_keys=keys), "server": server}
    ), jetons


# --- Un vrai serveur, et un vrai client MCP ----------------------------------


@asynccontextmanager
async def serveur(loom: Loom) -> AsyncGenerator[str]:
    """Monte l'application (REST + MCP) sur un port libre, le temps du cas."""
    import uvicorn

    from loom_ia.access.http import create_app

    sock = socket.create_server(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(loom), log_config=None))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        await task
        sock.close()


@asynccontextmanager
async def session_mcp(
    base: str, jeton: str, *, origin: str | None = None
) -> AsyncGenerator[ClientSession]:
    """Une session MCP ouverte sur le serveur, avec sa clé."""
    headers = {"Authorization": f"Bearer {jeton}"}
    if origin:
        headers["Origin"] = origin
    async with httpx.AsyncClient(headers=headers, timeout=60, trust_env=False) as http:
        async with streamable_http_client(f"{base}/mcp", http_client=http) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session


async def requete(base: str, jeton: str | None = None, **headers: str) -> int:
    """Une initialisation crue : ce que le transport répond, sans le protocole."""
    entete = {
        "content-type": "application/json",
        "accept": "application/json, text/event-stream",
        **headers,
    }
    if jeton:
        entete["authorization"] = f"Bearer {jeton}"
    body: dict[str, Any] = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "exemple", "version": "1"},
        },
    }
    async with httpx.AsyncClient(timeout=30, trust_env=False) as http:
        response = await http.post(f"{base}/mcp/", json=body, headers=entete)
        return response.status_code


def outils(result: Any) -> list[str]:
    return [tool.name for tool in result.tools]


def dit(result: Any) -> str:
    return "\n".join(bloc.text for bloc in result.content if getattr(bloc, "text", None))


# --- Cas 1 : deux artisans sur le même serveur -------------------------------


async def clients(scene: Scene) -> None:
    loom, base, jetons, agent, prefixe, controle = scene
    titre("Un seul serveur MCP, deux clients")
    print(f"  {base}/mcp")
    for nom, tenant in (("dupont", DUPONT), ("martin", MARTIN)):
        async with session_mcp(base, jetons[nom]) as session:
            publies = outils(await session.list_tools())
            print(f"  clé {nom:<12} client {tenant:<18} outils : {', '.join(publies)}")
            agents = [o for o in publies if o.startswith("relance")]
            controle.tient(f"la clé {nom} ne publie pas {agent}", agent in agents)

    titre("Chacun relance son devis, dans son journal")
    for nom, tenant in (("dupont", DUPONT), ("martin", MARTIN)):
        session_id = SessionId(f"{prefixe}-{nom}")
        async with session_mcp(base, jetons[nom]) as session:
            rendu = await session.call_tool(
                agent, {"message": demande(tenant), "session_id": session_id}
            )
        structure = rendu.structuredContent or {}
        print(f"  {tenant:<18} {structure.get('status')}  {structure.get('cost_usd', 0):.6f} $")
        events = await loom.export_session(session_id, tenant_id=tenant)
        vus = {event.tenant_id for event in events}
        print(f"  {'':<18} {len(events)} événements, client(s) : {', '.join(vus)}")
        controle.tient(f"le journal de {nom} porte un autre client", vus == {tenant})

    titre("Le devis de l'autre n'existe pas, ici non plus")
    egare = SessionId(f"{prefixe}-egare")
    async with session_mcp(base, jetons["martin"]) as session:
        rendu = await session.call_tool(agent, {"message": demande(DUPONT), "session_id": egare})
    print(f"  Martin demandant {DEVIS[DUPONT]} → {dit(rendu)[:96]}")
    # Le refus cite le numéro, et c'est normal : ce qu'il ne doit pas
    # porter, c'est l'e-mail — donc rien dans `data`.
    ecrit = (rendu.structuredContent or {}).get("data")
    controle.tient("Martin a obtenu une relance sur le devis de Dupont", not ecrit)
    print(f"\n  En stdio, il aurait fallu deux serveurs : loom mcp --tenant {DUPONT}")


# --- Cas 2 : les portées de la clé, et le contenu ----------------------------


async def portees(scene: Scene) -> None:
    _, base, jetons, agent, prefixe, controle = scene
    session_id = SessionId(f"{prefixe}-portees")
    titre("Lancer demande `run`")
    async with session_mcp(base, jetons["lecture"]) as session:
        refus = await session.call_tool(agent, {"message": demande(DUPONT)})
    print(f"  clé lecture  → isError={bool(refus.isError)} : {dit(refus)}")
    controle.tient("la clé sans `run` a pu lancer", bool(refus.isError))

    titre("Ce qu'une clé lance, elle le reçoit")
    async with session_mcp(base, jetons["supervision"]) as session:
        lance = await session.call_tool(
            agent, {"message": demande(DUPONT), "session_id": session_id}
        )
        structure = lance.structuredContent or {}
        run_id = str(structure.get("run_id"))
        print(f"  clé supervision (sans read_content) → {structure.get('status')}")
        print(f"  réponse reçue : « {str(structure.get('text', ''))[:64]}… »")
        controle.tient(
            "la réponse du lancement est masquée", DEVIS[DUPONT] in str(structure.get("text"))
        )

        titre("Mais la relecture, elle, est masquée")
        relu = await session.call_tool("run_status", {"run_id": run_id, "session_id": session_id})
        masque = relu.structuredContent or {}
    async with session_mcp(base, jetons["dupont"]) as session:
        entier = (
            await session.call_tool("run_status", {"run_id": run_id, "session_id": session_id})
        ).structuredContent or {}
    print(f"  {'champ':<12} {'clé dupont':<34} clé supervision")
    for champ in ("status", "iterations", "cost_usd"):
        print(f"  {champ:<12} {entier.get(champ)!s:<34} {masque.get(champ)}")
    print(f"  {'text':<12} {str(entier.get('text'))[:31]:<34} {masque.get('text')!r}")
    controle.tient("la relecture n'est pas masquée", masque.get("text") == "")
    controle.tient(
        "les coûts ont disparu du masquage", masque.get("cost_usd") == entier.get("cost_usd")
    )


# --- Cas 3 : ce que la spec MCP impose au transport --------------------------


async def transport(scene: Scene) -> None:
    base, jetons, controle = scene[1], scene[2], scene[5]
    titre("Avant le protocole : la clé")
    code = await requete(base)
    print(f"  sans clé                  → {controle.code('sans clé', 401, code)}")
    code = await requete(base, new_api_key())
    print(f"  clé inconnue              → {controle.code('clé inconnue', 401, code)}")
    code = await requete(base, jetons["dupont"])
    print(f"  clé de Dupont             → {controle.code('clé valable', 200, code)}")

    titre("Origin : absent il passe, déclaré il passe, inconnu il tombe")
    code = await requete(base, jetons["dupont"], origin=ORIGINE)
    print(f"  Origin déclaré            → {controle.code('origine déclarée', 200, code)}")
    code = await requete(base, jetons["dupont"], origin="https://ailleurs.example")
    print(f"  Origin inconnu            → {controle.code('origine inconnue', 403, code)}")

    titre("Host : celui où l'on croit parler, et pas un autre")
    code = await requete(base, jetons["dupont"], host="ailleurs.example")
    print(f"  Host étranger             → {controle.code('hôte étranger', 421, code)}")
    print("\n  loom remplit lui-même la liste des hôtes avec son adresse d'écoute :")
    print("  sans cela, le SDK refuserait tout le monde.")


# --- Mise en route -----------------------------------------------------------


async def jouer(nom: str, scene: Scene) -> None:
    print(f"\n{'─' * 78}\nCas {nom}\n{'─' * 78}")
    if nom == "clients":
        await clients(scene)
    elif nom == "portees":
        await portees(scene)
    else:
        await transport(scene)


async def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Serveur MCP en HTTP : clés, portées, transport")
    parser.add_argument("--reel", action="store_true", help="vrais modèles")
    parser.add_argument("--cas", action="append", choices=CAS, help="cas à jouer (tous par défaut)")
    parser.add_argument("--session", default=None, help="préfixe des sessions")
    args = parser.parse_args(argv)
    cas = tuple(args.cas) if args.cas else CAS
    prefixe = args.session or f"mcp-{new_id()[-8:]}"

    try:
        config = load_config(CONFIG)
    except ConfigError as error:
        print(f"Configuration : {error}", file=sys.stderr)
        return 2
    apply_logging(config)
    agent = agent_de(args)
    print(f"Config   : {shown(CONFIG)}")
    print(f"Agent    : {agent}")
    print(f"Clients  : {', '.join(config.tenant_ids)}")
    print(f"Sessions : {prefixe}-…")
    controle = Controle()
    config, jetons = quatre(config)
    # Un seul serveur pour tous les cas : c'est ce que la phase démontre, et
    # c'est aussi la seule façon de le faire — un second serveur MCP dans le
    # même process ne répond plus (reste connu, défaut du SDK).
    try:
        async with Loom(config) as loom, serveur(loom) as base:
            scene: Scene = (loom, base, jetons, agent, prefixe, controle)
            for nom in cas:
                await jouer(nom, scene)
    except (ModelConfigError, ConfigError) as error:
        print(f"Configuration : {error}", file=sys.stderr)
        return 2
    except ImportError as error:
        print(f"Extra manquant : {error}", file=sys.stderr)
        return 2
    return 0 if controle.bilan() else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
