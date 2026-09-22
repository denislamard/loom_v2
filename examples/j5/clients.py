# SPDX-License-Identifier: Apache-2.0
"""Phase 5.1a : deux artisans, un seul agent, et rien qui passe de l'un à l'autre.

    uv run python examples/j5/clients.py                        # les quatre cas
    uv run python examples/j5/clients.py --cas isolation
    uv run python examples/j5/clients.py --cas surcharges
    uv run --extra mcp python examples/j5/clients.py --cas mcp
    uv run --extra http --extra mcp python examples/j5/clients.py --cas acces
    uv run --env-file .env --extra http --extra mcp --extra anthropic \\
        --extra openai python examples/j5/clients.py --reel

Config : ``examples/j5/relance/``, reprise de celle du jalon J4 et complétée
d'un bloc ``tenants``. Deux clients, Dupont Plomberie et Chauffage Martin :
mêmes agents, mêmes prompts, mêmes outils. Ce qui les sépare tient en
quelques lignes de configuration — et c'est ce que ces cas montrent.

* **isolation** : chacun relance son devis. Deux journaux (Dupont a le sien,
  Martin partage le commun), deux carnets, et le devis de l'autre n'existe
  pas. Ce qui appartient à l'un ne se lit pas au nom de l'autre.
* **surcharges** : ce que chaque client voit du **même** agent — le modèle
  qui rédige, la variable que son prompt cite, les outils qu'on lui retire,
  l'approbation qu'on lui impose. L'outil sensible et ces deux dernières
  surcharges sont ajoutés **en code** : les fichiers de ``relance/`` ne
  changent pas.
* **mcp** : un serveur MCP en ``scope: tenant``, ajouté en code. Une
  connexion par client, lancée avec le jeton de ce client — le serveur ne
  connaît que le carnet de celui qui l'a ouvert.
* **acces** : le même client, nommé par chacun des trois accès. En Python il
  s'écrit ``tenant=`` ; en REST c'est la **clé d'API** qui le porte, et rien
  dans le corps d'une requête ne peut le changer ; en MCP stdio, où il n'y a
  pas de clé, un serveur sert un client, choisi à son lancement.
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

from loom_ia.access.api import Loom, RunResult, UnknownSession
from loom_ia.adapters.models import ModelConfigError
from loom_ia.agents.spec import McpTools
from loom_ia.config import (
    ApiKey,
    ConfigError,
    LoomConfig,
    SecurityConfig,
    fingerprint,
    load_config,
    new_api_key,
)
from loom_ia.core.model import McpServerSpec, SessionId, TenantId, new_id
from loom_ia.runtime import apply_logging
from loom_ia.tools import tool

CONFIG = Path(__file__).parent / "relance" / "loom.yaml"
SERVEUR_CRM = Path(__file__).parent / "relance" / "serveurs" / "serveur_crm.py"
CAS = ("isolation", "surcharges", "mcp", "acces")

DUPONT = TenantId("dupont-plomberie")
MARTIN = TenantId("martin-chauffage")
# Chaque artisan a son carnet, et son devis en attente.
DEVIS: dict[TenantId, str] = {DUPONT: "D-2026-042", MARTIN: "D-2026-117"}
# Les jetons de CRM que le serveur MCP reconnaît, chacun dans la variable que
# son client a déclarée (`secrets` dans loom.yaml).
JETONS = {"DUPONT_CRM_TOKEN": "jeton-dupont", "MARTIN_CRM_TOKEN": "jeton-martin"}

ENVOI = "envoyer_email"


def demande(tenant: TenantId) -> str:
    return f"Relance le client du devis {DEVIS[tenant]}, sur un ton cordial."


def shown(path: Path) -> str:
    """Chemin relatif au dossier courant, pour une commande à copier."""
    return os.path.relpath(path)


def titre(texte: str) -> None:
    print(f"\n=== {texte} ===")


def ligne(result: RunResult) -> str:
    objet = result.data.get("objet", "") if isinstance(result.data, dict) else ""
    return (
        f"{result.status.value:<10} {result.iterations} itération(s)  "
        f"{result.cost_usd:.6f} $  {objet or result.text[:48]}"
    )


def agent_de(args: argparse.Namespace) -> str:
    return "relance_reel" if args.reel else "relance"


@tool(side_effects="irreversible")
def envoyer_email(destinataire: str, objet: str, corps: str) -> str:
    """Envoie l'e-mail de relance au client."""
    return f"E-mail « {objet} » envoyé à {destinataire}."


# --- Cas 1 : ce qui appartient à l'un ne se lit pas au nom de l'autre ---------


async def isolation(config: LoomConfig, agent: str, prefixe: str) -> None:
    titre("Chacun son devis, chacun son journal")
    async with Loom(config) as loom:
        for tenant in (DUPONT, MARTIN):
            session = SessionId(f"{prefixe}-{tenant}")
            result = await loom.run(agent, demande(tenant), session_id=session, tenant=tenant)
            print(f"  {tenant:<18} {ligne(result)}")

        titre("Le devis de l'autre n'existe pas")
        # Même demande, au nom du mauvais client : l'outil ne sert que le
        # carnet de son appelant (`context.tenant_id`, outils.py).
        egare = await loom.run(
            agent,
            demande(DUPONT),
            session_id=SessionId(f"{prefixe}-{MARTIN}-egare"),
            tenant=MARTIN,
        )
        print(f"  {MARTIN:<18} {ligne(egare)}")

        titre("Ce que chacun voit du journal")
        for tenant in (DUPONT, MARTIN):
            sessions = await loom.sessions(tenant_id=tenant)
            noms = ", ".join(sorted(record.session_id for record in sessions))
            print(f"  {tenant:<18} {len(sessions)} session(s) : {noms}")
        # La session de Dupont, demandée au nom de Martin : elle n'existe pas.
        chez_dupont = SessionId(f"{prefixe}-{DUPONT}")
        try:
            vus = await loom.export_session(chez_dupont, tenant_id=MARTIN)
            verdict = f"{len(vus)} événement(s) — fuite !"
        except UnknownSession:
            # La bonne réponse n'est pas « interdit » mais « elle n'existe
            # pas » : Martin n'a aucun moyen d'apprendre qu'elle existe.
            verdict = "session inconnue"
        print(f"  {MARTIN} lisant la session de {DUPONT} : {verdict}")

    base = CONFIG.parent
    titre("Sur le disque")
    print(f"  {shown(base / 'data' / 'dupont' / DUPONT)}  (journal propre à Dupont)")
    print(f"  {shown(base / 'data' / MARTIN)}  (journal commun, un dossier par client)")


# --- Cas 2 : le même agent, vu par chacun ------------------------------------


def cloisonne(config: LoomConfig, agent: str) -> tuple[LoomConfig, str]:
    """Ajoute en code l'outil d'envoi, puis ce que chaque client en fait.

    Dupont le laisse tel qu'il est déclaré — sans approbation. Martin se le
    fait retirer purement et simplement. Un troisième pourrait l'imposer à
    l'approbation : c'est la même clé, ``approvals``.
    """
    spec = next(a for a in config.agents if a.name == agent)
    # Référence à l'outil, telle que l'écrirait la config ; l'objet lui-même
    # est donné à l'instance par ``register`` (plus bas).
    envoi = spec.tools[0].model_copy(
        update={"python": ENVOI, "side_effects": "irreversible", "idempotent": None}
    )
    outille = spec.model_copy(update={"tools": (*spec.tools, envoi)})
    tenants = tuple(
        tenant.model_copy(
            update={
                "tools_deny": (ENVOI,) if tenant.id == MARTIN else (),
                "approvals": {ENVOI: "always"} if tenant.id == DUPONT else {},
            }
        )
        for tenant in config.tenants
    )
    agents = tuple(outille if a.name == agent else a for a in config.agents)
    return LoomConfig.model_validate(
        {**config.model_dump(), "agents": agents, "tenants": tenants}
    ), agent


async def surcharges(config: LoomConfig, agent: str, _prefixe: str) -> None:
    config, agent = cloisonne(config, agent)
    async with Loom(config) as loom:
        loom.register(ENVOI, envoyer_email)
        titre(f"L'agent « {agent} », vu par chacun de ses clients")
        for tenant_id in loom.tenants:
            tenant = loom.tenant(tenant_id)
            spec = next(a for a in config.agents if a.name == agent)
            role = spec.roles[0]
            contexte = loom.context(agent, tenant_id)
            outils = sorted(contexte.tools.names)
            print(f"  {tenant_id}")
            print(f"    rôle {role.name} : modèle {role.model} → {_modele(tenant, role.model)}")
            print(f"    prompt : « signe au nom de {tenant.variables.get('entreprise', '—')} »")
            print(f"    outils : {', '.join(outils)}")
            impose = ", ".join(f"{k} : {v}" for k, v in tenant.approvals.items()) or "aucune"
            print(f"    approbation imposée : {impose}")


def _modele(tenant: Any, model_id: str) -> str:
    """Ce que l'identifiant désigne chez ce client (sdk + modèle du fournisseur)."""
    spec = tenant.config.model_spec(model_id)
    return f"{spec.model}"


# --- Cas 3 : un serveur MCP par client ---------------------------------------


def avec_crm(config: LoomConfig, agent: str) -> tuple[LoomConfig, str]:
    """Ajoute en code le serveur MCP ``crm``, en portée ``tenant``."""
    serveur = McpServerSpec(
        name="crm",
        transport="stdio",
        command=sys.executable,
        args=(str(SERVEUR_CRM),),
        # Le jeton vient des secrets du client : `CRM_TOKEN` est redirigé par
        # `secrets` dans loom.yaml vers DUPONT_CRM_TOKEN ou MARTIN_CRM_TOKEN.
        env_from={"CRM_TOKEN": "CRM_TOKEN"},
        scope="tenant",
    )
    spec = next(a for a in config.agents if a.name == agent)
    outille = spec.model_copy(update={"tools": (*spec.tools, McpTools(mcp="crm"))})
    agents = tuple(outille if a.name == agent else a for a in config.agents)
    return LoomConfig.model_validate(
        {**config.model_dump(), "agents": agents, "mcp_servers": (serveur,)}
    ), agent


async def par_mcp(config: LoomConfig, agent: str, _prefixe: str) -> None:
    from loom_ia.core.model import RunId
    from loom_ia.core.ports import SourceContext, ToolContext

    config, agent = avec_crm(config, agent)
    titre("Un serveur MCP par client, ouvert avec son jeton")
    async with Loom(config, environ={**os.environ, **JETONS}) as loom:
        for tenant_id in (DUPONT, MARTIN):
            contexte = loom.context(agent, tenant_id)
            source = contexte.tools.sources[0]
            appel = SourceContext(
                tenant_id=tenant_id,
                session_id=SessionId("crm"),
                run_id=RunId("crm"),
                agent=agent,
            )
            async with source.open(appel) as outils:
                par_nom = {outil.spec.name: outil for outil in outils}
                fiche = par_nom["crm__coordonnees"]
                mien = await fiche.invoke(
                    {"numero": DEVIS[tenant_id]},
                    ToolContext(
                        tenant_id=tenant_id,
                        session_id=SessionId("crm"),
                        run_id=RunId("crm"),
                        call_id="c1",
                        agent=agent,
                    ),
                )
                autre = DEVIS[MARTIN if tenant_id == DUPONT else DUPONT]
                refuse = await fiche.invoke(
                    {"numero": autre},
                    ToolContext(
                        tenant_id=tenant_id,
                        session_id=SessionId("crm"),
                        run_id=RunId("crm"),
                        call_id="c2",
                        agent=agent,
                    ),
                )
                verdict = _une_ligne(refuse.as_text) if refuse.is_error else "rendu — fuite !"
                print(f"  {tenant_id}")
                print(f"    connexion : {getattr(source, 'pool_key', source.name)}")
                print(f"    {DEVIS[tenant_id]} : {_une_ligne(mien.as_text)}")
                print(f"    {autre} : {verdict}")


def _une_ligne(texte: str) -> str:
    """Un résultat JSON ou un message d'erreur, ramené à une ligne lisible."""
    return " ".join(texte.split())[:90]


# --- Cas 4 : le client nommé par chacun des trois accès ----------------------


def avec_cles(config: LoomConfig) -> tuple[LoomConfig, dict[TenantId, str]]:
    """Une clé d'API par client : c'est elle qui porte le client en REST."""
    cles = {tenant: new_api_key() for tenant in (DUPONT, MARTIN)}
    keys = tuple(
        ApiKey(
            id=f"app-{tenant.split('-')[0]}",
            hash=fingerprint(cle),
            tenant=tenant,
            scopes=("run", "read"),
        )
        for tenant, cle in cles.items()
    )
    return config.model_copy(update={"security": SecurityConfig(api_keys=keys)}), cles


@asynccontextmanager
async def serveur(loom: Loom) -> AsyncGenerator[str]:
    """Monte l'API REST de l'instance sur un port libre, le temps du cas."""
    import uvicorn

    from loom_ia.access.http import create_app

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
        with opener.open(request) as response:
            return response.status, json.loads(response.read() or b"null")
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"null")


async def appel(url: str, *, method: str = "GET", body: Any = None, key: str = "") -> Any:
    """La requête, hors de la boucle : urllib est synchrone, le serveur est ici."""
    return await asyncio.to_thread(_http, url, method=method, body=body, key=key)


async def acces(config: LoomConfig, agent: str, prefixe: str) -> None:
    config, cles = avec_cles(config)
    session = SessionId(f"{prefixe}-acces")

    titre("Python : le client s'écrit tenant=")
    async with Loom(config) as loom:
        result = await loom.run(agent, demande(DUPONT), session_id=session, tenant=DUPONT)
        print(f"  {DUPONT:<18} {ligne(result)}")

        titre("REST : c'est la clé qui porte le client")
        async with serveur(loom) as base:
            code, lance = await appel(
                f"{base}/agents/{agent}/runs",
                method="POST",
                body={"message": demande(MARTIN)},
                key=cles[MARTIN],
            )
            print(f"  POST …/runs (clé de {MARTIN}) → {code}")
            chez_martin = lance["session_id"]
            for tenant, cle in cles.items():
                code, _ = await appel(f"{base}/sessions/{chez_martin}", key=cle)
                verdict = "sa session" if code == 200 else "introuvable"
                print(
                    f"  GET  …/sessions/<session de {MARTIN}> (clé de {tenant}) → {code} {verdict}"
                )
            code, liste = await appel(f"{base}/sessions", key=cles[DUPONT])
            noms = ", ".join(sorted(record["session_id"] for record in liste))
            print(f"  GET  …/sessions (clé de {DUPONT}) → {code} : {noms}")

        titre("MCP : un serveur sert un client, choisi à son lancement")
        # En stdio il n'y a pas de clé : rien dans le protocole ne dirait au
        # nom de qui une requête arrive. Un serveur sert donc un client, et
        # tout ce qu'il lance porte ce client-là.
        from mcp.shared.memory import create_connected_server_and_client_session as connected
        from mcp.types import Implementation

        from loom_ia.access.mcp_server import create_server

        client = Implementation(name="atelier-mcp", version="1.0")
        for tenant in (DUPONT, MARTIN):
            par_mcp_session = SessionId(f"{prefixe}-mcp-{tenant}")
            async with connected(create_server(loom, tenant=tenant), client_info=client) as mcp:
                rendu = await mcp.call_tool(
                    agent, {"message": demande(tenant), "session_id": par_mcp_session}
                )
            structure = rendu.structuredContent or {}
            events = await loom.export_session(par_mcp_session, tenant_id=tenant)
            clients = {event.tenant_id for event in events}
            print(f"  loom mcp --tenant {tenant}")
            print(f"    outil   : {structure.get('status')}, isError={bool(rendu.isError)}")
            print(f"    journal : {len(events)} événement(s), client(s) : {', '.join(clients)}")
    print(f'\n  CLI : uv run loom --config {shown(CONFIG)} run {agent} "…" --tenant {DUPONT}')


# --- Mise en route -----------------------------------------------------------


async def jouer(nom: str, config: LoomConfig, agent: str, prefixe: str) -> None:
    print(f"\n{'─' * 78}\nCas {nom}\n{'─' * 78}")
    if nom == "isolation":
        await isolation(config, agent, prefixe)
    elif nom == "surcharges":
        await surcharges(config, agent, prefixe)
    elif nom == "mcp":
        await par_mcp(config, agent, prefixe)
    else:
        await acces(config, agent, prefixe)


async def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Deux artisans sur le même agent")
    parser.add_argument("--reel", action="store_true", help="vrais modèles")
    parser.add_argument("--cas", action="append", choices=CAS, help="cas à jouer (tous par défaut)")
    parser.add_argument("--session", default=None, help="préfixe des sessions")
    args = parser.parse_args(argv)
    cas = tuple(args.cas) if args.cas else CAS
    prefixe = args.session or f"atelier-{new_id()[-8:]}"

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
    print(f"Sessions : {prefixe}-<client>")
    for nom in cas:
        try:
            await jouer(nom, config, agent, prefixe)
        except (ModelConfigError, ConfigError) as error:
            print(f"Configuration : {error}", file=sys.stderr)
            return 2
        except ImportError as error:
            print(f"Extra manquant pour le cas {nom} : {error}", file=sys.stderr)
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
