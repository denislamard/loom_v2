# SPDX-License-Identifier: Apache-2.0
"""Phase 5.4a : lire le journal par REST — la liste des runs, les traces, le document.

    uv run --extra http python examples/j5/lectures_rest.py            # les trois cas
    uv run --extra http python examples/j5/lectures_rest.py --cas runs
    uv run --extra http python examples/j5/lectures_rest.py --cas traces
    uv run --extra http python examples/j5/lectures_rest.py --cas openapi
    uv run --env-file .env --extra http --extra anthropic --extra openai \\
        python examples/j5/lectures_rest.py --reel

Config : ``examples/j5/relance/``, celle de 5.1a. Les clés sont déclarées **en
code**, pour que les autres exemples du jalon gardent une API REST ouverte.

* **runs** : ``GET /runs``. Les runs du client, du plus récemment écrit au plus
  ancien, avec ``agent``, ``status``, ``since`` et ``until``. Ils sont **lus au
  journal**, session par session : rien à tenir à jour, rien à resynchroniser,
  et le prix est une lecture par journal ouvert. La page le dit — ``scanned``
  ce qu'elle a coûté, ``truncated`` si une borne l'a arrêtée. Aucun contenu
  là-dedans, si bien qu'une clé de supervision la lit entière, et qu'une clé
  limitée à un agent y a droit alors que ``/sessions`` la refuse.
* **traces** : ``GET /events``, la recherche au journal (``EventQuery``) — par
  type, par run, par outil, par date, et ``after`` pour reprendre la
  pagination. Le client vient de la clé : rien dans l'URL ne le nomme, donc on
  ne cherche que chez soi. Sans ``read_content``, les mêmes événements
  arrivent privés de ce qu'un modèle a lu et répondu.
* **openapi** : le document de l'API. Chaque route y porte un résumé et une
  famille, les deux façons de présenter une clé y sont déclarées, et les deux
  routes de ce cas-ci y annoncent chacun de leurs critères.
"""

import argparse
import asyncio
import json
import os
import socket
import sys
import urllib.error
import urllib.request
from collections.abc import AsyncGenerator, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from urllib.parse import quote

from loom_ia.access import Loom
from loom_ia.access.api import SESSIONS_MAX
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
from loom_ia.core.model import TenantId, new_id
from loom_ia.runtime import apply_logging

CONFIG = Path(__file__).parent / "relance" / "loom.yaml"
CAS = ("runs", "traces", "openapi")

DUPONT = TenantId("dupont-plomberie")
MARTIN = TenantId("martin-chauffage")
# Chaque artisan a son carnet : le devis de l'autre n'y figure pas.
DEVIS = {DUPONT: "D-2026-042", MARTIN: "D-2026-117"}


def demande(tenant: TenantId) -> str:
    return f"Relance le client du devis {DEVIS[tenant]}, sur un ton cordial."


def shown(path: Path) -> str:
    return os.path.relpath(path)


def titre(texte: str) -> None:
    print(f"\n=== {texte} ===")


def agent_de(args: argparse.Namespace) -> str:
    return "relance_reel" if args.reel else "relance"


def autre_que(agent: str) -> str:
    """L'autre agent de la config : celui qu'on ne lance pas dans ce mode."""
    return "relance" if agent == "relance_reel" else "relance_reel"


def bref(identifiant: str) -> str:
    return identifiant[-12:]


# --- Les clés de l'exemple, posées en code -----------------------------------


def declarees(config: LoomConfig, keys: tuple[ApiKey, ...]) -> LoomConfig:
    return config.model_copy(update={"security": SecurityConfig(api_keys=keys)})


def trois(agent: str) -> tuple[tuple[ApiKey, ...], dict[str, str]]:
    """Trois clés : le bureau de Dupont, sa supervision, et celui de Martin."""
    jetons = {nom: new_api_key() for nom in ("dupont", "supervision", "martin")}
    keys = (
        ApiKey(
            id="dupont",
            hash=fingerprint(jetons["dupont"]),
            tenant=DUPONT,
            scopes=("run", "read", "read_content"),
        ),
        # Elle voit passer les runs, elle ne lit pas le courrier — et comme une
        # liste de runs ne porte aucun contenu, elle la lit entière.
        ApiKey(
            id="supervision",
            hash=fingerprint(jetons["supervision"]),
            tenant=DUPONT,
            scopes=("run", "read"),
            agents=(agent,),
        ),
        ApiKey(
            id="martin",
            hash=fingerprint(jetons["martin"]),
            tenant=MARTIN,
            scopes=("run", "read", "read_content"),
        ),
    )
    return keys, jetons


# --- Un vrai serveur, et de quoi lui parler ----------------------------------


@asynccontextmanager
async def serveur(loom: Loom) -> AsyncGenerator[str]:
    """Monte l'API REST de l'instance sur un port libre, le temps du cas.

    Rend la racine : le document OpenAPI est à côté de ``/v1``, pas dedans.
    """
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


def dit(code: int, corps: Any) -> str:
    if code < 400:
        return str(code)
    table: dict[str, object] = cast("dict[str, object]", corps) if isinstance(corps, dict) else {}
    detail: object = table.get("detail", "")
    if isinstance(detail, list):
        # Un paramètre hors des clous : FastAPI rend une liste d'erreurs.
        return f"{code} {_refus(cast('list[object]', detail))}"
    return f"{code} {detail}"


def _refus(detail: list[object]) -> str:
    """La première erreur de validation : le paramètre fautif, et pourquoi."""
    premier = detail[0] if detail else None
    if not isinstance(premier, dict):
        return str(premier)
    entree = cast("dict[str, object]", premier)
    ou = entree.get("loc")
    chemin = [str(part) for part in cast("list[object]", ou)[1:]] if isinstance(ou, list) else []
    return f"{'.'.join(chemin)} : {entree.get('msg', '')}"


class Controle:
    """Ce que chaque appel devait rendre : l'exemple l'annonce, puis le vérifie.

    Un exemple qui se contente d'afficher ce qui revient finit par annoncer un
    refus et montrer une réussite sans que rien ne proteste. Chaque appel dit
    donc son attendu, et la commande rend 1 si l'un d'eux tombe à côté.
    """

    def __init__(self) -> None:
        self.ecarts: list[str] = []

    def code(self, quoi: str, attendu: int, code: int, corps: Any) -> str:
        """Rend la ligne à afficher, et retient l'écart s'il y en a un."""
        if code == attendu:
            return dit(code, corps)
        self.ecarts.append(f"{quoi} : {code} au lieu de {attendu}")
        return f"{dit(code, corps)}   ← attendu {attendu}"

    def tient(self, quoi: str, vrai: bool) -> str:
        """Rend « oui » ou « NON », et retient ce qui ne tient pas."""
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


async def lance(base: str, agent: str, *, session: str, key: str, tenant: TenantId) -> Any:
    """Un run mené à son terme, dans la session nommée."""
    _, corps = await appel(
        f"{base}/v1/agents/{agent}/runs",
        method="POST",
        body={"message": demande(tenant), "session_id": session},
        key=key,
    )
    return corps


async def page(base: str, requete: str, *, key: str) -> Any:
    _, corps = await appel(f"{base}/v1/runs{requete}", key=key)
    return corps


# --- Cas 1 : la liste des runs ----------------------------------------------


async def runs(config: LoomConfig, agent: str, prefixe: str, controle: Controle) -> None:
    keys, jetons = trois(agent)
    config = declarees(config, keys)

    async with Loom(config) as loom, serveur(loom) as base:
        # Le journal de la config garde ce que les exemples précédents y ont
        # écrit : les attendus de ce cas-ci portent donc sur les runs lancés
        # ici, et chaque recherche part de cette borne.
        depuis = f"?since={quote(datetime.now(UTC).isoformat(), safe='')}"

        titre("Trois sessions chez Dupont, une chez Martin")
        lances: list[Any] = []
        for numero in range(3):
            corps = await lance(
                base, agent, session=f"{prefixe}-d{numero}", key=jetons["dupont"], tenant=DUPONT
            )
            lances.append(corps)
            print(f"  {prefixe}-d{numero} → run {bref(corps['run_id'])} {corps['status']}")
        chez_martin = await lance(
            base, agent, session=f"{prefixe}-m", key=jetons["martin"], tenant=MARTIN
        )
        print(f"  {prefixe}-m  → run {bref(chez_martin['run_id'])} {chez_martin['status']}")

        titre("GET /runs — lus au journal, du plus récemment écrit au plus ancien")
        code, liste = await appel(f"{base}/v1/runs{depuis}", key=jetons["dupont"])
        controle.code("GET /runs", 200, code, liste)
        print(f"  {'run':<14}{'session':<26}{'agent':<14}{'statut':<11}{'tours':>6}{'coût $':>10}")
        for run in liste["runs"]:
            print(
                f"  {bref(run['run_id']):<14}{run['session_id']:<26}{run['agent']:<14}"
                f"{run['status']:<11}{run['iterations']:>6}{run['cost_usd']:>10.6f}"
            )
        print(f"  journaux ouverts : {liste['scanned']}   tronquée : {liste['truncated']}")
        miens = {corps["run_id"] for corps in lances}
        print(
            "  les trois runs de Dupont, et eux seuls : "
            + controle.tient(
                "la liste ne rend pas exactement les runs lancés ici",
                {run["run_id"] for run in liste["runs"]} == miens,
            )
        )
        print(
            "  du plus récemment écrit au plus ancien : "
            + controle.tient(
                "la liste n'est pas ordonnée sur la dernière écriture",
                _decroissant([run["updated_at"] for run in liste["runs"]]),
            )
        )
        print(
            "  chaque run porte sa session et son premier événement : "
            + controle.tient(
                "un run listé ne se situe pas dans le journal",
                all(
                    run["session_id"].startswith(prefixe) and run["started_at"] <= run["updated_at"]
                    for run in liste["runs"]
                ),
            )
        )
        print(
            "  aucun contenu dans la liste : "
            + controle.tient(
                "un champ de contenu s'est glissé dans la liste",
                all(
                    champ not in run
                    for run in liste["runs"]
                    for champ in ("text", "output", "error", "data")
                ),
            )
        )

        titre("Les filtres")
        aucun: set[str] = set()
        for requete, attendus in (
            (f"&agent={agent}", miens),
            (f"&agent={autre_que(agent)}", aucun),
            ("&status=completed", miens),
            ("&status=failed", aucun),
            ("&status=completed&status=failed", miens),
        ):
            trouvee = await page(base, f"{depuis}{requete}", key=jetons["dupont"])
            rendus = {run["run_id"] for run in trouvee["runs"]}
            marque = controle.tient(
                f"{requete} a rendu {len(rendus)} runs au lieu de {len(attendus)}",
                rendus == attendus,
            )
            print(f"  GET /runs …{requete:<38} {len(rendus)} run(s)  {marque}")

        titre("Les bornes, et ce que la page en dit")
        borne = await page(base, f"{depuis}&limit=1", key=jetons["dupont"])
        peu = await page(base, f"{depuis}&sessions=1", key=jetons["dupont"])
        print(f"  …&limit=1    → {len(borne['runs'])} run(s), tronquée {borne['truncated']}")
        print(
            f"  …&sessions=1 → {len(peu['runs'])} run(s), "
            f"{peu['scanned']} journal ouvert, tronquée {peu['truncated']}"
        )
        print(
            "  une borne qui arrête la recherche le dit : "
            + controle.tient(
                "une page bornée ne s'annonce pas tronquée",
                len(borne["runs"]) == 1
                and borne["truncated"]
                and peu["scanned"] == 1
                and peu["truncated"],
            )
        )
        filtree = await page(base, f"{depuis}&agent={agent}", key=jetons["dupont"])
        print(
            f"  un filtre ne baisse pas le prix — {liste['scanned']} journaux ouverts "
            f"sans lui, {filtree['scanned']} avec : "
            + controle.tient(
                "un filtre a changé le nombre de journaux ouverts",
                filtree["scanned"] == liste["scanned"],
            )
        )
        print("  (la recherche lit les journaux, puis filtre ce qu'elle y a replié)")

        titre("Une page qui n'a rencontré aucune borne ne se dit pas tronquée")
        code, journaux = await appel(f"{base}/v1/sessions", key=jetons["dupont"])
        controle.code("GET /sessions", 200, code, journaux)
        print(f"  le client a {len(journaux)} journaux, une page en ouvre {SESSIONS_MAX} au plus")
        if len(journaux) <= SESSIONS_MAX:
            entiere = await page(base, f"{depuis}&sessions={len(journaux)}", key=jetons["dupont"])
            print(
                f"  …&sessions={len(journaux)} → {entiere['scanned']} ouverts, "
                f"tronquée {entiere['truncated']}"
            )
            print(
                "  tous ouverts, rien de tronqué : "
                + controle.tient(
                    "une page qui a ouvert tous les journaux se dit tronquée",
                    entiere["scanned"] == len(journaux) and entiere["truncated"] is False,
                )
            )
        else:
            print("  plus de journaux qu'une page n'en ouvre : elle est forcément tronquée")
            print(
                "  et elle le dit : "
                + controle.tient("une page bornée ne s'annonce pas tronquée", liste["truncated"])
            )

        code, refusee = await appel(f"{base}/v1/runs?limit=0", key=jetons["dupont"])
        ligne = controle.code("GET /runs?limit=0", 422, code, refusee)
        print(f"\n  Une borne hors des clous est refusée : ?limit=0 → {ligne}")

        titre("La liste ne sort pas du client de la clé")
        chez_lui = await page(base, depuis, key=jetons["martin"])
        rendus = [run["run_id"] for run in chez_lui["runs"]]
        print(f"  clé martin → {len(rendus)} run(s) : {', '.join(bref(r) for r in rendus)}")
        print(
            "  il ne voit que le sien : "
            + controle.tient(
                "une clé a listé les runs d'un autre client", rendus == [chez_martin["run_id"]]
            )
        )

        titre("Une clé limitée à un agent : refusée sur /sessions, admise sur /runs")
        code, sessions = await appel(f"{base}/v1/sessions", key=jetons["supervision"])
        ligne = controle.code("GET /sessions (clé limitée)", 403, code, sessions)
        print(f"  GET /sessions → {ligne}")
        code, sienne = await appel(f"{base}/v1/runs{depuis}", key=jetons["supervision"])
        controle.code("GET /runs (clé limitée)", 200, code, sienne)
        print(f"  GET /runs     → 200, {len(sienne['runs'])} run(s)")
        print(
            "  chaque run dit son agent, la liste se filtre donc honnêtement : "
            + controle.tient(
                "la liste filtrée ne rend pas les runs de l'agent ouvert",
                {run["agent"] for run in sienne["runs"]} == {agent},
            )
        )
        code, ailleurs = await appel(
            f"{base}/v1/runs?agent={autre_que(agent)}", key=jetons["supervision"]
        )
        ligne = controle.code("GET /runs?agent=<fermé>", 403, code, ailleurs)
        print(f"  …?agent={autre_que(agent):<14} → {ligne}")
        print("  (nommer un agent qu'on n'a pas le droit de lire est un refus, pas un vide)")


def _decroissant(dates: Sequence[str]) -> bool:
    return all(dates[i] >= dates[i + 1] for i in range(len(dates) - 1))


# --- Cas 2 : la recherche au journal ----------------------------------------


async def traces(config: LoomConfig, agent: str, prefixe: str, controle: Controle) -> None:
    keys, jetons = trois(agent)
    config = declarees(config, keys)
    session = f"{prefixe}-traces"
    # Les recherches de ce cas portent sur cette session : le journal de la
    # config garde tout ce que les exemples précédents y ont écrit.
    sienne = f"?session_id={session}&limit=1000"

    async with Loom(config) as loom, serveur(loom) as base:
        corps = await lance(base, agent, session=session, key=jetons["dupont"], tenant=DUPONT)
        run_id = corps["run_id"]
        titre(f"Un run dans la session {session}")
        print(f"  run {bref(run_id)} {corps['status']}, {corps['iterations']} tours")

        titre("GET /events — les critères d'une EventQuery, en paramètres")
        code, tout = await appel(f"{base}/v1/events{sienne}", key=jetons["dupont"])
        controle.code("GET /events", 200, code, tout)
        for critere in (
            "",
            "&type=tool.called",
            "&type=tool.called&type=tool.completed",
            "&category=run",
            f"&run_id={run_id}&category=model",
            "&tool_name=chercher_devis",
            "&status=error",
        ):
            _, trouves = await appel(f"{base}/v1/events{sienne}{critere}", key=jetons["dupont"])
            types = ", ".join(sorted({event["type"] for event in trouves})) or "—"
            print(f"  {critere or '(la session entière)':<44} {len(trouves):>3} : {types}")

        _, appels = await appel(
            f"{base}/v1/events{sienne}&tool_name=chercher_devis", key=jetons["dupont"]
        )
        print(
            "  une facette ne rend que les événements qui la portent : "
            + controle.tient(
                "la recherche par facette a rendu autre chose que l'outil demandé",
                bool(appels)
                and all(event["facets"].get("tool_name") == "chercher_devis" for event in appels),
            )
        )
        _, uniquement = await appel(f"{base}/v1/events{sienne}&category=run", key=jetons["dupont"])
        print(
            "  une catégorie non plus : "
            + controle.tient(
                "la recherche par catégorie a rendu une autre catégorie",
                bool(uniquement) and all(e["category"] == "run" for e in uniquement),
            )
        )
        _, absente = await appel(
            f"{base}/v1/events?session_id={prefixe}-absente", key=jetons["dupont"]
        )
        print(
            "  une session inconnue rend une liste vide : "
            + controle.tient("une session inconnue a rendu des événements", absente == [])
        )

        titre("La pagination : after reprend après le dernier événement rendu")
        vus: list[str] = []
        curseur = ""
        pages = 0
        while True:
            _, lot = await appel(
                f"{base}/v1/events?session_id={session}&limit=4{curseur}", key=jetons["dupont"]
            )
            if not lot:
                break
            pages += 1
            print(f"  page {pages} : {len(lot)} événements, {lot[0]['type']} … {lot[-1]['type']}")
            vus.extend(event["event_id"] for event in lot)
            curseur = f"&after={lot[-1]['event_id']}"
        print(f"  {pages} pages, {len(vus)} événements pour {len(tout)} au journal")
        print(
            "  aucun doublon, aucun trou : "
            + controle.tient(
                "la pagination a rendu deux fois le même événement, ou en a perdu",
                len(vus) == len(set(vus)) == len(tout),
            )
        )
        print(
            "  et l'ordre est celui des identifiants, donc du temps : "
            + controle.tient(
                "les événements ne sont pas rendus dans l'ordre de leurs identifiants",
                vus == sorted(vus),
            )
        )

        titre("Les bornes de date, comparées à l'événement")
        milieu = tout[len(tout) // 2]["ts"]
        borne = quote(milieu, safe="")
        _, avant = await appel(f"{base}/v1/events{sienne}&until={borne}", key=jetons["dupont"])
        _, apres = await appel(f"{base}/v1/events{sienne}&since={borne}", key=jetons["dupont"])
        print(f"  …&until={milieu} → {len(avant):>3}")
        print(f"  …&since={milieu} → {len(apres):>3}")
        print(
            f"  since comprise, until exclue : les deux couvrent les {len(tout)} du journal, "
            "une fois chacun : "
            + controle.tient(
                "les deux bornes ne partagent pas exactement le journal",
                len(avant) + len(apres) == len(tout),
            )
        )

        titre("Sans read_content, les mêmes événements, privés de leur contenu")
        code, masques = await appel(f"{base}/v1/events{sienne}", key=jetons["supervision"])
        controle.code("GET /events (supervision)", 200, code, masques)
        retires = sorted({champ for e in masques for champ in e["payload"].get("redacted", [])})
        entier = json.dumps(tout, ensure_ascii=False)
        prive = json.dumps(masques, ensure_ascii=False)
        print(f"  {len(masques)} événements ; champs retirés : {', '.join(retires) or 'aucun'}")
        print(f"  le numéro {DEVIS[DUPONT]} : {entier.count(DEVIS[DUPONT])} fois côté bureau,")
        print(f"  {prive.count(DEVIS[DUPONT])} fois côté supervision.")
        print(
            "  le journal est le même, seule la charge est privée de contenu : "
            + controle.tient(
                "la recherche masquée ne rend pas le même journal",
                [e["type"] for e in masques] == [e["type"] for e in tout],
            )
        )
        print(
            "  et le devis n'y est plus : "
            + controle.tient(
                "le numéro de devis est resté dans la recherche masquée",
                DEVIS[DUPONT] not in prive,
            )
        )

        titre("Rien dans l'URL ne nomme un client")
        _, chez_martin = await appel(f"{base}/v1/events{sienne}", key=jetons["martin"])
        print(f"  la clé de Martin cherche la session de Dupont → {len(chez_martin)} événement(s)")
        print(
            "  on ne cherche que chez soi : "
            + controle.tient("une clé a lu le journal d'un autre client", chez_martin == [])
        )


# --- Cas 3 : le document de l'API -------------------------------------------


async def openapi(config: LoomConfig, agent: str, prefixe: str, controle: Controle) -> None:
    keys, jetons = trois(agent)
    config = declarees(config, keys)

    async with Loom(config) as loom, serveur(loom) as base:
        code, document = await appel(f"{base}/openapi.json", key=jetons["dupont"])
        controle.code("GET /openapi.json", 200, code, document)
        titre(f"{document['info']['title']} {document['info'].get('version', '?')}")
        routes = [
            (chemin, methode.upper(), spec)
            for chemin, methodes in document["paths"].items()
            for methode, spec in methodes.items()
        ]
        declarees_ = {tag["name"] for tag in document.get("tags", [])}
        print(f"  {len(routes)} routes, {len(declarees_)} familles décrites")
        for famille in document.get("tags", []):
            titre(f"{famille['name']} — {famille['description']}")
            for chemin, methode, spec in routes:
                if famille["name"] in spec.get("tags", []):
                    print(f"  {methode:<7}{chemin:<34}{spec['summary']}")

        titre("Ce que le document doit tenir")
        sans_resume = [f"{m} {c}" for c, m, spec in routes if not spec.get("summary")]
        mal_rangees = [
            f"{m} {c}" for c, m, spec in routes if not set(spec.get("tags", [])) <= declarees_
        ]
        sans_famille = [f"{m} {c}" for c, m, spec in routes if not spec.get("tags")]
        print(f"  familles déclarées : {', '.join(sorted(declarees_))}")
        print(
            "  chaque route a un résumé      : "
            + controle.tient(f"routes sans résumé : {', '.join(sans_resume)}", not sans_resume)
        )
        print(
            "  chaque route a une famille    : "
            + controle.tient(f"routes sans famille : {', '.join(sans_famille)}", not sans_famille)
        )
        print(
            "  et une famille qui est décrite : "
            + controle.tient(f"routes mal rangées : {', '.join(mal_rangees)}", not mal_rangees)
        )
        # Une liste figée de familles vieillirait au premier ajout de route :
        # ce qui doit tenir, c'est qu'aucune ne soit décrite sans servir.
        servies = {famille for _, _, spec in routes for famille in spec.get("tags", [])}
        orphelines = sorted(declarees_ - servies)
        print(
            "  et aucune n'est décrite sans servir : "
            + controle.tient(
                f"familles décrites sans route : {', '.join(orphelines)}", not orphelines
            )
        )

        schemes = document["components"].get("securitySchemes", {})
        print(f"  clés déclarées : {', '.join(sorted(schemes)) or 'aucune'}")
        for nom, scheme in sorted(schemes.items()):
            detail = scheme.get("name") or scheme.get("scheme", "")
            print(f"    {nom:<14}{scheme['type']:<8}{detail}")
        portantes = [
            f"{m} {c}"
            for c, m, spec in routes
            if {nom for entree in spec.get("security", []) for nom in entree} != set(schemes)
        ]
        print(
            "  chaque route les porte        : "
            + controle.tient(
                f"routes sans schéma d'authentification : {', '.join(portantes)}", not portantes
            )
        )

        titre("Les deux lectures de cette phase, telles que le document les annonce")
        for chemin in ("/v1/runs", "/v1/events"):
            spec = document["paths"][chemin]["get"]
            noms = [param["name"] for param in spec.get("parameters", [])]
            print(f"  GET {chemin:<12} {spec['summary']}")
            print(f"  {'':<16} critères : {', '.join(noms)}")
            print(
                f"  {'':<16} décrits  : "
                + controle.tient(
                    f"{chemin} annonce un critère sans schéma",
                    all("schema" in param for param in spec.get("parameters", [])),
                )
            )
        print(f"\n  La page : {base}/docs   (le document : {base}/openapi.json)")


# --- Mise en route -----------------------------------------------------------


async def jouer(nom: str, config: LoomConfig, agent: str, prefixe: str, controle: Controle) -> None:
    print(f"\n{'─' * 78}\nCas {nom}\n{'─' * 78}")
    if nom == "runs":
        await runs(config, agent, prefixe, controle)
    elif nom == "traces":
        await traces(config, agent, prefixe, controle)
    else:
        await openapi(config, agent, prefixe, controle)


async def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Lectures REST : liste des runs, recherche au journal, document OpenAPI"
    )
    parser.add_argument("--reel", action="store_true", help="vrais modèles")
    parser.add_argument("--cas", action="append", choices=CAS, help="cas à jouer (tous par défaut)")
    parser.add_argument("--session", default=None, help="préfixe des sessions")
    args = parser.parse_args(argv)
    cas = tuple(args.cas) if args.cas else CAS
    prefixe = args.session or f"lectures-{new_id()[-8:]}"

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
    for nom in cas:
        try:
            await jouer(nom, config, agent, prefixe, controle)
        except (ModelConfigError, ConfigError) as error:
            print(f"Configuration : {error}", file=sys.stderr)
            return 2
        except ImportError as error:
            print(f"Extra manquant pour le cas {nom} : {error}", file=sys.stderr)
            return 2
    return 0 if controle.bilan() else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
