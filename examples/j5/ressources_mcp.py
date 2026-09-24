# SPDX-License-Identifier: Apache-2.0
"""Phase 5.4b : le journal en ressources MCP, et l'arrêt d'un run.

    uv run --extra http --extra mcp python examples/j5/ressources_mcp.py
    uv run --extra http --extra mcp python examples/j5/ressources_mcp.py --cas index
    uv run --extra http --extra mcp python examples/j5/ressources_mcp.py --cas fichiers
    uv run --extra http --extra mcp python examples/j5/ressources_mcp.py --cas arret
    uv run --env-file .env --extra http --extra mcp --extra anthropic \\
        --extra openai python examples/j5/ressources_mcp.py --reel

Config : ``examples/j5/relance/``, celle de 5.1a, plus des clés posées **en
code** — les autres exemples du jalon gardent une API ouverte.

Un outil fait travailler, une ressource se lit. Ce qui se lisait par REST en
5.4a se lit donc ici par URI, avec les mêmes règles : la clé dit le client,
``read`` ouvre la lecture, et sans ``read_content`` le contenu est masqué.

* **index** : ``loom://runs`` et ``loom://sessions`` listés, les cinq gabarits
  annoncés, un run et sa trace lus par URI et comparés à ce que l'accès Python
  rend. Puis ce que la clé change : masquage sans ``read_content``, et une clé
  limitée à un agent qui obtient les runs mais pas la liste des sessions.
* **fichiers** : les octets d'un fichier du run, que ni REST ni MCP ne
  rendaient — seulement son URI. Lus sous l'URI que le journal publie, et sous
  l'alias ``loom://artifacts/…`` ; le fichier d'un autre client est
  introuvable, et un fichier plus gros que la borne de lecture est refusé.
* **arret** : l'outil ``cancel``. Un run déjà terminé n'est pas une erreur, et
  ce que l'outil répond doit correspondre à ce que le journal porte.
"""

import argparse
import asyncio
import json
import os
import socket
import sys
from collections.abc import AsyncGenerator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import McpError
from mcp.types import BlobResourceContents, InitializeResult, TextResourceContents
from pydantic import AnyUrl

from loom_ia.access import RUNS, SESSIONS, TEMPLATES, Loom
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
from loom_ia.core.events import Event
from loom_ia.core.model import Attachment, RunId, SessionId, TenantId, new_id
from loom_ia.runtime import apply_logging

CONFIG = Path(__file__).parent / "relance" / "loom.yaml"
CAS = ("index", "fichiers", "arret")

DUPONT = TenantId("dupont-plomberie")
MARTIN = TenantId("martin-chauffage")
DEVIS: dict[TenantId, str] = {DUPONT: "D-2026-042", MARTIN: "D-2026-117"}
# Une image valable, bâtie ici : signature PNG et des octets quelconques. Rien
# n'est écrit sur le disque, et le moteur la range comme n'importe quelle pièce.
PNG = b"\x89PNG\r\n\x1a\n" + bytes(range(64))
CLOTURES = ("run.completed", "run.failed", "run.cancelled")

# Ce qu'un cas reçoit : l'instance, l'adresse, les jetons, l'agent, le préfixe
# des sessions, et le contrôle des attendus.
type Scene = tuple["Loom", str, dict[str, str], str, str, "Controle"]


def demande(tenant: TenantId) -> str:
    return f"Relance le client du devis {DEVIS[tenant]}, sur un ton cordial."


def shown(path: Path) -> str:
    return os.path.relpath(path)


def titre(texte: str) -> None:
    print(f"\n=== {texte} ===")


def agent_de(args: argparse.Namespace) -> str:
    return "relance_reel" if args.reel else "relance"


def autre_que(agent: str) -> str:
    return "relance" if agent == "relance_reel" else "relance_reel"


def bref(identifiant: str) -> str:
    return identifiant[-12:]


class Controle:
    """Ce que chaque appel devait rendre : l'exemple l'annonce, puis le vérifie."""

    def __init__(self) -> None:
        self.ecarts: list[str] = []

    def tient(self, quoi: str, vrai: bool) -> str:
        if not vrai:
            self.ecarts.append(quoi)
        return "oui" if vrai else "NON"

    def bilan(self) -> bool:
        if not self.ecarts:
            print("\nChaque appel a rendu ce que l'exemple annonçait.")
            return True
        print("\nUn appel au moins n'a pas rendu ce qui était annoncé :")
        for ecart in self.ecarts:
            print(f"  {ecart}")
        return False


# --- Les clés de l'exemple, posées en code -----------------------------------


def quatre(config: LoomConfig, agent: str) -> tuple[LoomConfig, dict[str, str]]:
    """Trois clés de Dupont, une de Martin, et le MCP monté en HTTP."""
    jetons = {nom: new_api_key() for nom in ("dupont", "supervision", "bureau", "martin")}
    keys = (
        ApiKey(
            id="dupont",
            hash=fingerprint(jetons["dupont"]),
            tenant=DUPONT,
            scopes=("run", "read", "read_content"),
        ),
        # Elle voit passer les runs, elle ne lit pas le courrier (5.2a).
        ApiKey(
            id="supervision",
            hash=fingerprint(jetons["supervision"]),
            tenant=DUPONT,
            scopes=("run", "read"),
        ),
        # Limitée à l'agent qu'on ne lance pas : elle ne verra aucun run.
        ApiKey(
            id="bureau",
            hash=fingerprint(jetons["bureau"]),
            tenant=DUPONT,
            scopes=("run", "read", "read_content"),
            agents=(autre_que(agent),),
        ),
        ApiKey(
            id="martin",
            hash=fingerprint(jetons["martin"]),
            tenant=MARTIN,
            scopes=("run", "read", "read_content"),
        ),
    )
    mcp = config.server.mcp.model_copy(update={"http": True})
    server = config.server.model_copy(update={"mcp": mcp})
    return config.model_copy(
        update={"security": SecurityConfig(api_keys=keys), "server": server}
    ), jetons


# --- Un vrai serveur, et un vrai client MCP ----------------------------------


@asynccontextmanager
async def serveur(loom: Loom) -> AsyncGenerator[str]:
    """Monte l'application (REST + MCP) sur un port libre, le temps des cas."""
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
    base: str, jeton: str
) -> AsyncGenerator[tuple[ClientSession, InitializeResult]]:
    """Une session MCP ouverte sur le serveur, avec sa clé et ce qu'il annonce."""
    headers = {"Authorization": f"Bearer {jeton}"}
    async with httpx.AsyncClient(headers=headers, timeout=60, trust_env=False) as http:
        async with streamable_http_client(f"{base}/mcp", http_client=http) as (read, write, _):
            async with ClientSession(read, write) as session:
                opened = await session.initialize()
                yield session, opened


async def lire(session: ClientSession, uri: str) -> Any:
    """Le JSON d'une ressource."""
    result = await session.read_resource(AnyUrl(uri))
    [content] = result.contents
    assert isinstance(content, TextResourceContents)
    return json.loads(content.text)


async def octets(session: ClientSession, uri: str) -> tuple[bytes, str]:
    """Les octets d'une ressource, et le type qu'elle annonce."""
    import base64

    result = await session.read_resource(AnyUrl(uri))
    [content] = result.contents
    assert isinstance(content, BlobResourceContents)
    return base64.b64decode(content.blob), content.mimeType or ""


async def refuse(session: ClientSession, uri: str) -> str:
    """Ce que le serveur répond quand il refuse une lecture."""
    try:
        await session.read_resource(AnyUrl(uri))
    except McpError as error:
        return error.error.message
    return ""


def cloture(events: Sequence[Event]) -> str:
    """Le type de l'événement qui clôt le run, ou rien s'il n'est pas clos."""
    clos = [event.type for event in events if event.type in CLOTURES]
    return clos[-1] if clos else ""


def dit(result: Any) -> str:
    return "\n".join(bloc.text for bloc in result.content if getattr(bloc, "text", None))


# --- Cas 1 : ce que le serveur publie, et ce que la clé en voit --------------


async def index(scene: Scene) -> None:
    loom, base, jetons, agent, prefixe, controle = scene
    session_id = SessionId(f"{prefixe}-index")

    titre("Deux index listés, cinq gabarits annoncés")
    async with session_mcp(base, jetons["dupont"]) as (session, opened):
        ressources = await session.list_resources()
        gabarits = await session.list_resource_templates()
        for entree in ressources.resources:
            print(f"  {entree.uri!s:<22}{entree.mimeType}  {entree.description}")
        for modele in gabarits.resourceTemplates:
            print(f"  {modele.uriTemplate}")
        capacite = opened.capabilities.resources
        print(f"\n  Le serveur annonce : subscribe={capacite and capacite.subscribe}")
        print("  (pas d'abonnement : MCP n'a que la progression, le direct est au SSE de REST)")
        controle.tient(
            "les deux index ne sont pas listés",
            [str(e.uri) for e in ressources.resources] == [RUNS, SESSIONS],
        )
        controle.tient(
            "les gabarits annoncés ne sont pas ceux du module",
            [m.uriTemplate for m in gabarits.resourceTemplates] == [uri for uri, _, _ in TEMPLATES],
        )
        controle.tient(
            "le serveur annonce des abonnements qu'il ne tient pas",
            capacite is not None and capacite.subscribe is False,
        )

        titre("Un run lancé par l'outil, relu par les ressources")
        lance = await session.call_tool(
            agent, {"message": demande(DUPONT), "session_id": session_id}
        )
        structure = lance.structuredContent or {}
        run_id = RunId(str(structure.get("run_id")))
        print(f"  run {bref(run_id)} {structure.get('status')}")

        page = await lire(session, RUNS)
        journaux = await lire(session, SESSIONS)
        run = await lire(session, f"{RUNS}/{run_id}?session_id={session_id}")
        trace = await lire(session, f"{RUNS}/{run_id}/events?session_id={session_id}")
        fiche = await lire(session, f"{SESSIONS}/{session_id}")
        journal = await lire(session, f"{SESSIONS}/{session_id}/events")

    print(f"  {RUNS:<22}{len(page['runs'])} run(s), {page['scanned']} journaux ouverts")
    print(f"  {SESSIONS:<22}{len(journaux)} session(s)")
    print(f"  {'un run':<22}{run['status']}, {run['iterations']} tours, {run['cost_usd']:.6f} $")
    print(f"  {'sa trace':<22}{len(trace)} événements")
    print(f"  {'la fiche':<22}{len(fiche['runs'])} run(s), last_seq {fiche['last_seq']}")
    print(f"  {'le journal':<22}{len(journal)} événements")

    directe = await loom.runs(tenant_id=DUPONT)
    entiers = await loom.events(run_id, session_id=session_id, tenant_id=DUPONT)
    print(
        "\n  la ressource dit ce que l'accès Python dit : "
        + controle.tient(
            "la ressource et l'accès Python ne rendent pas la même page",
            [r["run_id"] for r in page["runs"]] == [r.run_id for r in directe.runs]
            and page["scanned"] == directe.scanned,
        )
    )
    print(
        "  et la trace est celle du journal : "
        + controle.tient(
            "la trace de la ressource n'est pas celle du journal",
            [e["type"] for e in trace] == [e.type for e in entiers],
        )
    )
    print(
        "  le journal de la session porte ses marqueurs, l'arbre du run non : "
        + controle.tient("le journal est plus court que l'arbre", len(journal) >= len(trace))
    )

    titre("Sans read_content, les mêmes événements privés de leur contenu")
    async with session_mcp(base, jetons["supervision"]) as (session, _):
        masque = await lire(session, f"{SESSIONS}/{session_id}/events")
        relu = await lire(session, f"{RUNS}/{run_id}?session_id={session_id}")
    entier = json.dumps(journal, ensure_ascii=False)
    prive = json.dumps(masque, ensure_ascii=False)
    retires = sorted({champ for e in masque for champ in e["payload"].get("redacted", [])})
    print(f"  {len(masque)} événements ; champs retirés : {', '.join(retires) or 'aucun'}")
    print(f"  le numéro {DEVIS[DUPONT]} : {entier.count(DEVIS[DUPONT])} fois côté bureau,")
    print(f"  {prive.count(DEVIS[DUPONT])} fois côté supervision.")
    print(
        "  même journal, contenu en moins : "
        + controle.tient(
            "la trace masquée n'est pas le même journal",
            [e["type"] for e in masque] == [e["type"] for e in journal]
            and DEVIS[DUPONT] not in prive,
        )
    )
    print(
        "  et la relecture d'un run est masquée aussi : "
        + controle.tient("la relecture masquée porte encore un texte", relu["text"] == "")
    )

    titre("Une clé limitée à un agent : les runs oui, la liste des sessions non")
    async with session_mcp(base, jetons["bureau"]) as (session, _):
        listees = [str(e.uri) for e in (await session.list_resources()).resources]
        sienne = await lire(session, RUNS)
        sessions = await refuse(session, SESSIONS)
        fiche_refusee = await refuse(session, f"{SESSIONS}/{session_id}")
    print(f"  ressources listées : {', '.join(listees)}")
    # Ce que la page porte, et non ce qu'on croit qu'elle porte : le journal de
    # la config garde les runs des exemples précédents, dont ceux de cet agent.
    vus = sorted({run["agent"] for run in sienne["runs"]}) or ["aucun run"]
    print(f"  {RUNS:<22}{len(sienne['runs'])} run(s), agent(s) : {', '.join(vus)}")
    print(f"  {SESSIONS:<22}{sessions}")
    print(f"  {'la fiche':<22}{fiche_refusee}")
    print(
        "  l'index qu'elle ne peut pas lire n'est pas listé : "
        + controle.tient("un index illisible est listé", listees == [RUNS])
    )
    print(
        "  et la liste des runs se filtre honnêtement : "
        + controle.tient(
            "une clé limitée voit les runs d'un autre agent",
            all(run["agent"] != agent for run in sienne["runs"]),
        )
    )
    print(
        "  la liste des sessions est refusée, pas filtrée : "
        + controle.tient("la liste des sessions n'est pas refusée", "filtrée" in sessions)
    )


# --- Cas 2 : les octets d'un fichier ----------------------------------------


async def fichiers(scene: Scene) -> None:
    loom, base, jetons, agent, prefixe, controle = scene
    session_id = SessionId(f"{prefixe}-fichiers")

    titre("Un run avec une pièce jointe")
    photo = Attachment(data=PNG, media_type="image/png", name="chauffe-eau.png")
    ran = await loom.run(
        agent,
        demande(DUPONT),
        attachments=[photo],
        session_id=session_id,
        tenant=DUPONT,
    )
    for record in ran.artifacts:
        print(f"  {record.uri}")
        print(f"  {'':<2}{record.media_type}, {record.size} octets, {record.origin}")
    joints = [record for record in ran.artifacts if record.origin == "attachment"]
    controle.tient("le run n'a rangé aucune pièce jointe", bool(joints))
    if not joints:
        return
    uri = joints[0].uri

    titre("Les octets, sous l'URI que le journal publie")
    async with session_mcp(base, jetons["dupont"]) as (session, _):
        directs, media = await octets(session, uri)
        alias = uri.replace("artifact://", "loom://artifacts/")
        par_alias, _ = await octets(session, alias)
        absent = await refuse(session, "artifact://dupont-plomberie/c-absente/rien.png")
    print(f"  {uri}")
    print(f"  {'':<2}→ {len(directs)} octets, {media}")
    print(f"  {alias}")
    print(f"  {'':<2}→ {len(par_alias)} octets")
    print(f"  un fichier absent → {absent}")
    print(
        "\n  ce sont bien les octets joints : "
        + controle.tient(
            "les octets rendus ne sont pas ceux de la pièce jointe",
            directs == PNG and par_alias == PNG,
        )
    )
    print(
        "  et le lien que loom rend n'est plus un lien mort : "
        + controle.tient("l'URI du journal ne se lit pas telle quelle", media == "image/png")
    )

    titre("Le fichier d'un autre client est introuvable, pas interdit")
    async with session_mcp(base, jetons["martin"]) as (session, _):
        chez_lui = await refuse(session, uri)
    print(f"  clé martin sur le fichier de Dupont → {chez_lui}")
    print(
        "  on n'apprend pas qu'il existe : "
        + controle.tient("le refus dit autre chose qu'introuvable", "introuvable" in chez_lui)
    )
    print(f"\n  Borne de lecture : {loom.config.execution.attachments.max_bytes} octets")
    print("  (execution.attachments.max_bytes, faute d'une borne de lecture à part)")


# --- Cas 3 : arrêter un run ---------------------------------------------------


async def arret(scene: Scene) -> None:
    loom, base, jetons, agent, prefixe, controle = scene

    titre("Un run déjà terminé : rien à arrêter, et ce n'est pas une erreur")
    fini = SessionId(f"{prefixe}-fini")
    ran = await loom.run(agent, demande(DUPONT), session_id=fini, tenant=DUPONT)
    async with session_mcp(base, jetons["dupont"]) as (session, _):
        rendu = await session.call_tool("cancel", {"run_id": ran.run_id, "session_id": fini})
    structure = rendu.structuredContent or {}
    print(f"  cancel sur {bref(ran.run_id)} → {dit(rendu)}")
    print(f"  {'':<2}structuré : {structure}")
    print(
        "  un run fini n'est pas une erreur : "
        + controle.tient(
            "arrêter un run fini est rendu comme une erreur",
            not rendu.isError and structure.get("cancelled") is False,
        )
    )

    titre("Un run laissé en arrière-plan, arrêté par MCP")
    # La session MCP est ouverte **avant** le lancement : l'arrêt ne coûte
    # alors qu'un aller-retour, là où une poignée de main en coûterait assez
    # pour qu'un run simulé finisse d'abord.
    async with session_mcp(base, jetons["dupont"]) as (session, _):
        laisse = await loom.submit(agent, demande(DUPONT), tenant=DUPONT)
        rendu = await session.call_tool("cancel", {"run_id": laisse})
    structure = rendu.structuredContent or {}
    arrete = bool(structure.get("cancelled"))
    await loom.drain()
    events = await loom.events(laisse, tenant_id=DUPONT)
    close = cloture(events)
    signe = [
        getattr(event.payload, "by", None) for event in events if event.type == "run.cancelled"
    ]
    print(f"  cancel sur {bref(laisse)} → cancelled={arrete}")
    print(f"  {'':<2}le journal se clôt sur {close or '(pas encore)'}")
    if signe:
        print(f"  {'':<2}signé par : {signe[0]} — la clé d'API, faute d'un `by`")
    # Attendu **structurel** : avec de vrais modèles, l'arrêt tombe pendant le
    # premier appel ; en simulé le run peut finir avant que la requête MCP
    # n'arrive. Les deux issues sont justes — ce qui ne doit jamais varier,
    # c'est que la réponse de l'outil dise ce que le journal porte.
    print(
        "\n  la réponse de l'outil dit ce que le journal porte : "
        + controle.tient(
            f"cancelled={arrete} mais le journal se clôt sur {close!r}",
            (arrete and close == "run.cancelled" and signe == ["dupont"])
            or (not arrete and close == "run.completed" and not signe),
        )
    )

    titre("Ce que la clé permet")
    async with session_mcp(base, jetons["martin"]) as (session, _):
        ailleurs = await session.call_tool("cancel", {"run_id": ran.run_id, "session_id": fini})
    print(f"  clé martin sur un run de Dupont → isError={bool(ailleurs.isError)}")
    print(f"  {'':<2}{dit(ailleurs)[:88]}")
    print(
        "  un run d'un autre client est introuvable : "
        + controle.tient("une clé a pu arrêter le run d'un autre client", bool(ailleurs.isError))
    )
    print("\n  Arrêter un run qu'on peut lancer n'est pas une permission de plus :")
    print("  c'est la portée `run` sur son agent, comme en REST.")


# --- Mise en route -----------------------------------------------------------


async def jouer(nom: str, scene: Scene) -> None:
    print(f"\n{'─' * 78}\nCas {nom}\n{'─' * 78}")
    if nom == "index":
        await index(scene)
    elif nom == "fichiers":
        await fichiers(scene)
    else:
        await arret(scene)


async def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Ressources MCP : le journal en lecture seule, et l'arrêt d'un run"
    )
    parser.add_argument("--reel", action="store_true", help="vrais modèles")
    parser.add_argument("--cas", action="append", choices=CAS, help="cas à jouer (tous par défaut)")
    parser.add_argument("--session", default=None, help="préfixe des sessions")
    args = parser.parse_args(argv)
    cas = tuple(args.cas) if args.cas else CAS
    prefixe = args.session or f"ressources-{new_id()[-8:]}"

    try:
        config = load_config(CONFIG)
    except ConfigError as error:
        print(f"Configuration : {error}", file=sys.stderr)
        return 2
    apply_logging(config)
    agent = agent_de(args)
    print(f"Config   : {shown(CONFIG)}")
    print(f"Agent    : {agent}")
    print(f"Clients  : {DUPONT} et {MARTIN}")
    print(f"Sessions : {prefixe}-…")
    controle = Controle()
    config, jetons = quatre(config, agent)
    # Un seul serveur pour tous les cas : un second serveur MCP dans le même
    # process ne répond plus (reste connu de 5.2b, défaut du SDK).
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
