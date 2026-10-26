# SPDX-License-Identifier: Apache-2.0
"""Phase 5.4c : les portes d'entrée — un appel extérieur ouvre un run.

    uv run --extra http python examples/j5/declencheurs.py            # les trois cas
    uv run --extra http python examples/j5/declencheurs.py --cas porte
    uv run --extra http python examples/j5/declencheurs.py --cas doublons
    uv run --extra http python examples/j5/declencheurs.py --cas cles
    uv run --env-file .env --extra http --extra anthropic --extra openai \\
        python examples/j5/declencheurs.py --reel

Config : ``examples/j5/relance/``, celle de 5.1a, avec les déclencheurs posés
**en code** — les fichiers de ``relance/`` ne changent pas.

Un déclencheur existe parce que celui qui appelle **ne connaît pas l'API de
loom** : un planificateur envoie un corps vide, un CRM envoie sa charge à lui.
La config dit quoi en faire — quel agent, et quel message, rendu depuis la
charge reçue. Et loom ne tient **aucun cron** : la planification est celle de
la plateforme, qui sonne à la porte à l'heure dite.

* **porte** : deux livraisons sur la même porte — l'une sans corps (le
  planificateur du matin), l'autre avec la charge d'un CRM. Le message rendu se
  relit au journal, et la porte qui a ouvert le run y est une **facette** :
  on peut donc demander au journal ce qu'une porte a lancé.
* **doublons** : la même livraison deux fois. Avec un en-tête de livraison,
  elle **retrouve** son run (200, ``repeated``) au lieu d'en ouvrir un second ;
  sans en-tête, deux runs partent — le piège, montré plutôt que caché. Et un
  identifiant de livraison inutilisable est refusé à la porte.
* **cles** : la portée ``run`` sur l'agent du déclencheur, et le client qui
  vient de la clé — deux artisans, une porte, deux journaux.
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
from typing import Any, cast

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
from loom_ia.config.models import TriggerSpec
from loom_ia.core.events import EventQuery
from loom_ia.core.model import RunId, SessionId, TenantId, new_id
from loom_ia.runtime import apply_logging

CONFIG = Path(__file__).parent / "relance" / "loom.yaml"
CAS = ("porte", "doublons", "cles")

DUPONT = TenantId("dupont-plomberie")
MARTIN = TenantId("martin-chauffage")
DEVIS: dict[TenantId, str] = {DUPONT: "D-2026-042", MARTIN: "D-2026-117"}
# L'en-tête que la plateforme pose sur chaque livraison, et que loom prend
# pour identifiant de run : la même livraison ne rouvre rien.
LIVRAISON = "X-Delivery-Id"
# Préfixe des livraisons de cette exécution. Sans lui, un identifiant fixe
# serait « déjà vu » au second lancement, et l'exemple montrerait une
# relivraison là où il annonce une première.
TOUR = new_id()[-8:]
MATIN = "relance-du-matin"
CRM = "devis-signe"
SANS = "sans-entete"


def shown(path: Path) -> str:
    return os.path.relpath(path)


def titre(texte: str) -> None:
    print(f"\n=== {texte} ===")


def agent_de(args: argparse.Namespace) -> str:
    return "relance_reel" if args.reel else "relance"


def autre_que(agent: str) -> str:
    return "relance" if agent == "relance_reel" else "relance_reel"


def bref(identifiant: str) -> str:
    """Un identifiant raccourci, sans le rendre faux.

    Un run engendré par loom est un UUID dont la queue suffit à le
    reconnaître ; un identifiant de **livraison** est lisible, et c'est sa
    tête qui le dit (``cron-``, ``crm-``). Le couper par la queue afficherait
    un identifiant qui n'existe pas — vu au run réel du 24/09.
    """
    return f"…{identifiant[-12:]}" if len(identifiant) == 36 else identifiant


class Controle:
    """Ce que chaque appel devait rendre : l'exemple l'annonce, puis le vérifie."""

    def __init__(self) -> None:
        self.ecarts: list[str] = []

    def code(self, quoi: str, attendu: int, code: int, corps: Any) -> str:
        if code == attendu:
            return dit(code, corps)
        self.ecarts.append(f"{quoi} : {code} au lieu de {attendu}")
        return f"{dit(code, corps)}   ← attendu {attendu}"

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


# --- Les portes et les clés de l'exemple, posées en code ----------------------


def pose(config: LoomConfig, agent: str) -> tuple[LoomConfig, dict[str, str]]:
    """Trois portes et quatre clés, sans toucher aux fichiers de ``relance/``."""
    triggers = (
        # Le planificateur de la plateforme : il n'a rien à dire d'autre que
        # « c'est l'heure ». Le message est donc fixe.
        TriggerSpec(
            name=MATIN,
            agent=agent,
            message=(
                "C'est l'heure de la tournée : relance le client du devis "
                f"{DEVIS[DUPONT]}, sur un ton cordial."
            ),
            delivery_header=LIVRAISON,
        ),
        # Le CRM de l'artisan : il envoie sa charge à lui, et le gabarit y
        # puise. Les livraisons d'un même devis vont dans un même journal.
        TriggerSpec(
            name=CRM,
            agent=agent,
            message=(
                "Relance le client du devis {{ payload.devis.numero }} "
                "({{ payload.client.nom }}), sur un ton cordial."
            ),
            session="devis-{{ payload.devis.numero }}",
            delivery_header=LIVRAISON,
        ),
        # La même porte, sans en-tête de livraison : de quoi montrer ce que
        # coûte son absence.
        TriggerSpec(
            name=SANS,
            agent=agent,
            message=f"Relance le client du devis {DEVIS[DUPONT]}, sur un ton cordial.",
        ),
    )
    jetons = {nom: new_api_key() for nom in ("planificateur", "lecture", "bureau", "martin")}
    keys = (
        ApiKey(
            id="planificateur",
            hash=fingerprint(jetons["planificateur"]),
            tenant=DUPONT,
            scopes=("run", "read", "read_content"),
        ),
        # Elle ne sait que lire : une porte lance, donc elle est refusée.
        ApiKey(
            id="lecture",
            hash=fingerprint(jetons["lecture"]),
            tenant=DUPONT,
            scopes=("read", "read_content"),
        ),
        # Limitée à l'autre agent : la porte nomme le sien, donc refus.
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
    return config.model_copy(
        update={"security": SecurityConfig(api_keys=keys), "triggers": triggers}
    ), jetons


# --- Un vrai serveur, et de quoi sonner à la porte ---------------------------


@asynccontextmanager
async def serveur(loom: Loom) -> AsyncGenerator[str]:
    """Monte l'API REST de l'instance sur un port libre, le temps des cas."""
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


def _http(url: str, *, body: bytes | None, headers: dict[str, str]) -> tuple[int, Any]:
    """Une livraison, sans passer par un proxy : le serveur est sur la machine."""
    request = urllib.request.Request(url, data=body or b"", method="POST")
    for name, value in headers.items():
        request.add_header(name, value)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request) as response:
            return response.status, json.loads(response.read() or b"null")
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"null")


async def livre(
    base: str,
    porte: str,
    *,
    key: str,
    charge: Any = None,
    delivery: str | None = None,
    brut: bytes | None = None,
) -> tuple[int, Any]:
    """Sonne à une porte : la charge en JSON, la clé, l'identifiant de livraison."""
    headers = {"authorization": f"Bearer {key}"}
    body = brut
    if body is None and charge is not None:
        body = json.dumps(charge).encode()
        headers["content-type"] = "application/json"
    if delivery:
        headers[LIVRAISON] = delivery
    return await asyncio.to_thread(_http, f"{base}/v1/hooks/{porte}", body=body, headers=headers)


def dit(code: int, corps: Any) -> str:
    if code < 400:
        return str(code)
    table = cast("dict[str, object]", corps) if isinstance(corps, dict) else {}
    return f"{code} {table.get('detail', '')}"


def demande(events: Any) -> str:
    """Le message rendu, tel que le journal le porte."""
    for event in events:
        if event.type == "message.user":
            return str(getattr(event.payload.message, "text", ""))
    return ""


# --- Cas 1 : ce qu'une porte ouvre -------------------------------------------


async def porte(loom: Loom, base: str, jetons: dict[str, str], controle: Controle) -> None:
    titre("Deux portes, deux appelants qui ne parlent pas la même langue")
    print(f"  POST {base}/v1/hooks/{MATIN}   (corps vide : le planificateur)")
    print(f"  POST {base}/v1/hooks/{CRM}     (la charge du CRM)")

    titre("Le planificateur : rien à dire d'autre que « c'est l'heure »")
    code, ouvert = await livre(base, MATIN, key=jetons["planificateur"], delivery=f"cron-{TOUR}")
    ligne = controle.code(f"POST hooks/{MATIN}", 202, code, ouvert)
    print(f"  sans corps → {ligne} run {bref(str(ouvert.get('run_id')))}")
    print(f"  {'':<14}{ouvert}")

    titre("Le CRM : sa charge à lui, et le gabarit y puise")
    charge = {
        "evenement": "devis.signe",
        "devis": {"numero": DEVIS[DUPONT], "montant": 1840},
        "client": {"nom": "Mme Martin", "email": "mme.martin@example.com"},
    }
    code, vu = await livre(
        base, CRM, key=jetons["planificateur"], charge=charge, delivery=f"crm-{TOUR}"
    )
    ligne = controle.code(f"POST hooks/{CRM}", 202, code, vu)
    print(f"  avec charge → {ligne} run {bref(str(vu.get('run_id')))}")
    print(f"  {'':<14}session {vu.get('session_id')}")
    await loom.drain()

    titre("Ce que le journal porte")
    for nom, ouvre in ((MATIN, ouvert), (CRM, vu)):
        session_id = SessionId(str(ouvre["session_id"]))
        events = await loom.events(
            RunId(str(ouvre["run_id"])), session_id=session_id, tenant_id=DUPONT
        )
        [started] = [event for event in events if event.type == "run.started"]
        print(f"  {nom}")
        print(f"  {'':<2}facette trigger : {started.facets.get('trigger')}")
        print(f"  {'':<2}message rendu   : « {demande(events)} »")
        controle.tient(
            f"le run de {nom} ne porte pas sa porte en facette",
            started.facets.get("trigger") == nom,
        )
    events = await loom.events(
        RunId(str(vu["run_id"])), session_id=SessionId(str(vu["session_id"])), tenant_id=DUPONT
    )
    controle.tient(
        "le gabarit n'a pas puisé dans la charge du CRM",
        DEVIS[DUPONT] in demande(events) and "Mme Martin" in demande(events),
    )
    controle.tient(
        "la session du CRM ne vient pas de sa charge",
        str(vu["session_id"]) == f"devis-{DEVIS[DUPONT]}",
    )

    titre("La porte est une facette : on peut demander au journal ce qu'elle a lancé")
    ouverts = await loom.query(
        EventQuery(tenant_id=DUPONT, types=("run.started",), facets={"trigger": MATIN})
    )
    print(f"  EventQuery(facets={{trigger: {MATIN}}}) → {len(ouverts)} run(s)")
    for event in ouverts[-3:]:
        print(f"  {'':<2}{bref(event.run_id)}  {event.ts:%H:%M:%S}")
    print(
        "  le run du planificateur en fait partie, celui du CRM non : "
        + controle.tient(
            "la recherche par facette ne distingue pas les portes",
            str(ouvert["run_id"]) in [e.run_id for e in ouverts]
            and str(vu["run_id"]) not in [e.run_id for e in ouverts],
        )
    )

    titre("Et la planification, elle, n'est pas dans loom")
    print("  loom ne tient aucun cron : c'est la plateforme qui sonne à l'heure dite.")
    print("  Cloud Scheduler, cron, un planificateur de CI — tous savent faire un POST :")
    print(f"    0 6 * * *  curl -fsS -X POST {base}/v1/hooks/{MATIN} \\")
    print('               -H "Authorization: Bearer $LOOM_KEY" \\')
    print(f'               -H "{LIVRAISON}: $(date +%F)"')
    print("  L'en-tête de livraison du jour rend la tournée idempotente : deux")
    print("  réveils le même jour ne lancent qu'une tournée.")


# --- Cas 2 : la même livraison deux fois ------------------------------------


async def doublons(loom: Loom, base: str, jetons: dict[str, str], controle: Controle) -> None:
    key = jetons["planificateur"]
    titre("Avec un en-tête de livraison : la seconde retrouve son run")
    livraison = f"double-{TOUR}"
    code, premier = await livre(base, MATIN, key=key, delivery=livraison)
    ligne = controle.code("première livraison", 202, code, premier)
    print(f"  1re fois ({livraison}) → {ligne} run {bref(str(premier.get('run_id')))}")
    code, second = await livre(base, MATIN, key=key, delivery=livraison)
    ligne = controle.code("seconde livraison", 200, code, second)
    print(f"  2e fois  ({livraison}) → {ligne} run {bref(str(second.get('run_id')))}")
    print(f"  {'':<24}repeated = {second.get('repeated')}")
    print(
        "  un run, pas deux : "
        + controle.tient(
            "la seconde livraison a ouvert un second run",
            premier.get("run_id") == second.get("run_id")
            and premier.get("repeated") is False
            and second.get("repeated") is True,
        )
    )
    print("  (l'identifiant de livraison **est** l'identifiant du run : rien à tenir à côté)")

    titre("Sans en-tête de livraison : deux réveils, deux runs")
    code, un = await livre(base, SANS, key=key)
    controle.code("livraison sans en-tête", 202, code, un)
    code, deux = await livre(base, SANS, key=key)
    controle.code("seconde livraison sans en-tête", 202, code, deux)
    print(f"  1re fois → 202 run {bref(str(un.get('run_id')))}")
    print(f"  2e fois  → 202 run {bref(str(deux.get('run_id')))}")
    print(
        "  deux runs, et c'est bien le piège : "
        + controle.tient(
            "la porte sans en-tête a dédoublonné quelque chose",
            un.get("run_id") != deux.get("run_id"),
        )
    )
    print("  `loom validate` le dit de la porte, pour le dire avant la panne.")

    titre("Un identifiant de livraison inutilisable est refusé à la porte")
    code, refuse = await livre(base, MATIN, key=key, delivery="../ailleurs")
    ligne = controle.code("livraison inutilisable", 422, code, refuse)
    print(f"  {LIVRAISON}: ../ailleurs → {ligne}")
    print("  (il finirait en nom de journal : il est contrôlé à la porte, pas à l'écriture)")

    titre("Une charge illisible ne lance rien")
    code, casse = await livre(base, CRM, key=key, brut=b"{pas du json", delivery=f"casse-{TOUR}")
    ligne = controle.code("charge illisible", 422, code, casse)
    print(f"  corps « {{pas du json » → {ligne}")
    await loom.drain()
    journaux = {record.session_id for record in await loom.sessions(tenant_id=DUPONT)}
    print(
        "  rien au journal pour une charge refusée : "
        + controle.tient(
            "une charge illisible a laissé un journal", f"casse-{TOUR}" not in journaux
        )
    )


# --- Cas 3 : ce que la clé décide -------------------------------------------


async def cles(loom: Loom, base: str, jetons: dict[str, str], controle: Controle) -> None:
    titre("La portée `run`, sur l'agent que la porte nomme")
    attendus = {"planificateur": 202, "lecture": 403, "bureau": 403}
    for nom, attendu in attendus.items():
        code, corps = await livre(base, MATIN, key=jetons[nom], delivery=f"essai-{nom}-{TOUR}")
        ligne = controle.code(f"{nom} sur {MATIN}", attendu, code, corps)
        print(f"  clé {nom:<15} → {ligne}")
    print("  (la clé `bureau` est ouverte à un autre agent : la porte nomme le sien)")

    titre("Le client vient de la clé : une porte, deux artisans, deux journaux")
    charges = {
        DUPONT: {"devis": {"numero": DEVIS[DUPONT]}, "client": {"nom": "Mme Martin"}},
        MARTIN: {"devis": {"numero": DEVIS[MARTIN]}, "client": {"nom": "M. Leroy"}},
    }
    ouverts: dict[TenantId, Any] = {}
    for nom, tenant in (("planificateur", DUPONT), ("martin", MARTIN)):
        code, ouverts[tenant] = await livre(
            base,
            CRM,
            key=jetons[nom],
            charge=charges[tenant],
            delivery=f"crm-{tenant}-{TOUR}",
        )
        controle.code(f"{nom} sur {CRM}", 202, code, ouverts[tenant])
        print(f"  clé {nom:<15} → 202 session {ouverts[tenant].get('session_id')}")
    await loom.drain()
    for tenant, ouvert in ouverts.items():
        events = await loom.export_session(SessionId(str(ouvert["session_id"])), tenant_id=tenant)
        vus = {event.tenant_id for event in events}
        print(f"  {tenant:<18}{len(events)} événements, client(s) : {', '.join(vus)}")
        controle.tient(f"le journal de {tenant} porte un autre client", vus == {tenant})
    print("  (rien dans l'URL ni dans le corps ne nomme un client)")

    titre("Une porte qui n'existe pas")
    code, absent = await livre(base, "porte-imaginaire", key=jetons["planificateur"])
    ligne = controle.code("porte inconnue", 404, code, absent)
    print(f"  POST /v1/hooks/porte-imaginaire → {ligne}")
    portes = ", ".join(spec.name for spec in loom.triggers)
    print(f"  (le refus nomme les portes déclarées : {portes})")


# --- Mise en route -----------------------------------------------------------


async def jouer(
    nom: str, loom: Loom, base: str, jetons: dict[str, str], controle: Controle
) -> None:
    print(f"\n{'─' * 78}\nCas {nom}\n{'─' * 78}")
    if nom == "porte":
        await porte(loom, base, jetons, controle)
    elif nom == "doublons":
        await doublons(loom, base, jetons, controle)
    else:
        await cles(loom, base, jetons, controle)


async def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Déclencheurs : une porte déclarée, sa charge, ses doublons, sa clé"
    )
    parser.add_argument("--reel", action="store_true", help="vrais modèles")
    parser.add_argument("--cas", action="append", choices=CAS, help="cas à jouer (tous par défaut)")
    args = parser.parse_args(argv)
    cas = tuple(args.cas) if args.cas else CAS

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
    print(f"Portes   : {MATIN}, {CRM}, {SANS}")
    controle = Controle()
    try:
        posee, jetons = pose(config, agent)
    except ConfigError as error:
        print(f"Configuration : {error}", file=sys.stderr)
        return 2
    try:
        async with Loom(posee) as loom, serveur(loom) as base:
            for nom in cas:
                await jouer(nom, loom, base, jetons, controle)
    except (ModelConfigError, ConfigError) as error:
        print(f"Configuration : {error}", file=sys.stderr)
        return 2
    except ImportError as error:
        print(f"Extra manquant : {error}", file=sys.stderr)
        return 2
    return 0 if controle.bilan() else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
