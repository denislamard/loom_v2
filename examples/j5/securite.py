# SPDX-License-Identifier: Apache-2.0
"""Phase 5.2a : ce qu'une clé d'API a le droit de faire, et de lire.

    uv run --extra http python examples/j5/securite.py            # les trois cas
    uv run --extra http python examples/j5/securite.py --cas cles
    uv run --extra http python examples/j5/securite.py --cas masquage
    uv run --extra http python examples/j5/securite.py --cas expiration
    uv run --env-file .env --extra http --extra anthropic --extra openai \\
        python examples/j5/securite.py --reel

Config : ``examples/j5/relance/``, celle de 5.1a. Les clés sont déclarées **en
code**, pour que les autres exemples du jalon gardent une API REST ouverte.
Elles agissent toutes au nom de Dupont Plomberie.

* **cles** : quatre clés sur les mêmes routes — une complète, une de
  supervision (``read`` sans ``read_content``), une limitée à un agent, une
  qui ne sait que lire. Une portée manquante donne 403, un agent fermé aussi.
* **masquage** : le même run relu par la clé complète et par celle de
  supervision, champ par champ, puis l'export JSONL des deux côtés. Et le
  journal sur le disque, pour montrer que **rien n'a été perdu** : le masquage
  est une affaire d'accès, pas de stockage — un run ne se rejoue pas sans ce
  que le modèle a lu et répondu.
* **expiration** : une clé qui vaut trois secondes. Reconnue, puis refusée en
  401 avec sa date — « expirée » se corrige, « inconnue » envoie chercher au
  mauvais endroit.
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
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

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
from loom_ia.core.model import RateLimit, SessionId, TenantId, new_id
from loom_ia.runtime import apply_logging

CONFIG = Path(__file__).parent / "relance" / "loom.yaml"
CAS = ("cles", "masquage", "expiration")

DUPONT = TenantId("dupont-plomberie")
DEVIS = "D-2026-042"
# Durée de vie de la clé du cas `expiration` : assez pour un appel, pas deux.
COURTE = timedelta(seconds=3)


def demande() -> str:
    return f"Relance le client du devis {DEVIS}, sur un ton cordial."


def shown(path: Path) -> str:
    return os.path.relpath(path)


def titre(texte: str) -> None:
    print(f"\n=== {texte} ===")


def agent_de(args: argparse.Namespace) -> str:
    return "relance_reel" if args.reel else "relance"


# --- Les clés de l'exemple, posées en code -----------------------------------


def declarees(config: LoomConfig, keys: tuple[ApiKey, ...]) -> LoomConfig:
    return config.model_copy(update={"security": SecurityConfig(api_keys=keys)})


def quatre(agent: str) -> tuple[tuple[ApiKey, ...], dict[str, str]]:
    """Quatre clés du même client, et les jetons qui vont avec."""
    jetons = {nom: new_api_key() for nom in ("complete", "supervision", "bureau", "lecture")}
    keys = (
        # Tout ce qu'un intégrateur demande : lancer, relire, et lire le contenu.
        ApiKey(
            id="complete",
            hash=fingerprint(jetons["complete"]),
            tenant=DUPONT,
            scopes=("run", "read", "read_content"),
        ),
        # Supervision : elle voit passer les runs, elle ne lit pas le courrier.
        ApiKey(
            id="supervision",
            hash=fingerprint(jetons["supervision"]),
            tenant=DUPONT,
            scopes=("run", "read"),
            rate_limit=RateLimit(per_minute=120),
        ),
        # Limitée à un agent : elle ne peut rien lancer d'autre.
        ApiKey(
            id="bureau",
            hash=fingerprint(jetons["bureau"]),
            tenant=DUPONT,
            scopes=("run", "read", "read_content"),
            agents=(agent,),
        ),
        # Elle ne sait que lire : pas de `run`.
        ApiKey(
            id="lecture",
            hash=fingerprint(jetons["lecture"]),
            tenant=DUPONT,
            scopes=("read", "read_content"),
        ),
    )
    return keys, jetons


def carte(key: ApiKey) -> str:
    """Ce qu'une clé permet, en une ligne."""
    details = [f"portées {', '.join(key.scopes)}"]
    details.append(f"agents {', '.join(key.agents)}" if key.agents else "tous les agents")
    if key.rate_limit is not None:
        details.append(f"débit {key.rate_limit.per_minute}/min")
    if key.expires is not None:
        details.append(f"expire le {key.expires:%Y-%m-%d %H:%M:%S} UTC")
    return ", ".join(details)


# --- Un vrai serveur, et de quoi lui parler ----------------------------------


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
            corps = response.read()
            return response.status, corps.decode() if _brut(url) else json.loads(corps or b"null")
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"null")


def _brut(url: str) -> bool:
    """L'export d'une session rend du JSONL, pas un objet."""
    return url.endswith("/events") and "/sessions/" in url


async def appel(url: str, *, method: str = "GET", body: Any = None, key: str = "") -> Any:
    """La requête, hors de la boucle : urllib est synchrone, le serveur est ici."""
    return await asyncio.to_thread(_http, url, method=method, body=body, key=key)


def dit(code: int, corps: Any) -> str:
    if code >= 400:
        return f"{code} {corps.get('detail', '')}"
    return str(code)


class Controle:
    """Ce que chaque appel devait rendre : l'exemple l'annonce, puis le vérifie.

    Un exemple qui se contente d'afficher ce qui revient finit par annoncer un
    refus et montrer une réussite sans que rien ne proteste — c'est arrivé au
    premier run réel de cette phase. Chaque appel dit donc son attendu, et la
    commande rend 1 si l'un d'eux tombe à côté.
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


# --- Cas 1 : ce que chaque clé a le droit de faire ---------------------------


async def cles(config: LoomConfig, agent: str, prefixe: str, controle: Controle) -> None:
    keys, jetons = quatre(agent)
    config = declarees(config, keys)
    titre("Les quatre clés de Dupont Plomberie")
    for key in keys:
        print(f"  {key.id:<12} {carte(key)}")

    async with Loom(config) as loom, serveur(loom) as base:
        titre("GET …/agents — il faut `read`")
        # Les quatre clés ont `read` : elles listent toutes, mais `bureau` ne
        # voit que l'agent qu'on lui a ouvert.
        for nom, jeton in jetons.items():
            code, corps = await appel(f"{base}/agents", key=jeton)
            vus = ", ".join(a["name"] for a in corps) if code == 200 else ""
            ligne = controle.code(f"GET agents ({nom})", 200, code, corps)
            print(f"  {nom:<12} → {ligne} {vus}")

        titre(f"POST …/agents/{agent}/runs — il faut `run`, et l'agent ouvert")
        attendus = {"complete": 201, "supervision": 201, "bureau": 201, "lecture": 403}
        for nom, jeton in jetons.items():
            code, corps = await appel(
                f"{base}/agents/{agent}/runs",
                method="POST",
                body={"message": demande(), "session_id": f"{prefixe}-{nom}"},
                key=jeton,
            )
            ligne = controle.code(f"POST runs ({nom})", attendus[nom], code, corps)
            print(f"  {nom:<12} → {ligne}")

        titre("Une clé limitée à un agent ne lance que celui-là")
        # L'autre agent de la config : celui que `bureau` n'a pas le droit de
        # lancer, qu'on soit en simulé ou en réel.
        autre = "relance_reel" if agent == "relance" else "relance"
        code, corps = await appel(
            f"{base}/agents/{autre}/runs",
            method="POST",
            body={"message": demande(), "session_id": f"{prefixe}-refuse"},
            key=jetons["bureau"],
        )
        ligne = controle.code(f"POST runs de bureau sur {autre}", 403, code, corps)
        print(f"  bureau sur « {autre} » → {ligne}")

        titre("Sans clé du tout")
        code, corps = await appel(f"{base}/agents")
        ligne = controle.code("GET agents sans clé", 401, code, corps)
        print(f"  (aucune)     → {ligne}")


# --- Cas 2 : ce que `read_content` change ------------------------------------


async def masquage(config: LoomConfig, agent: str, prefixe: str, controle: Controle) -> None:
    keys, jetons = quatre(agent)
    config = declarees(config, keys)
    session = SessionId(f"{prefixe}-masquage")

    async with Loom(config) as loom, serveur(loom) as base:
        titre("Un run lancé par la clé de supervision")
        code, lance = await appel(
            f"{base}/agents/{agent}/runs",
            method="POST",
            body={"message": demande(), "session_id": session},
            key=jetons["supervision"],
        )
        controle.code("POST runs (supervision)", 201, code, lance)
        run_id = lance["run_id"]
        # Ce qu'une clé lance, elle le reçoit : la réponse d'un POST n'est
        # jamais masquée, c'est la relecture qui demande la portée.
        print(f"  POST …/runs → {code}, et la réponse est là : « {lance['text'][:56]}… »")
        recue = controle.tient("la réponse du lancement est masquée", DEVIS in lance["text"])
        print(f"  elle porte le numéro de devis : {recue}")

        titre("Le même run, relu par chacune")
        vues: dict[str, Any] = {}
        for nom in ("complete", "supervision"):
            code, vues[nom] = await appel(
                f"{base}/runs/{run_id}?session_id={session}", key=jetons[nom]
            )
            ligne = controle.code(f"GET runs/<id> ({nom})", 200, code, vues[nom])
            print(f"  GET …/runs/<id> (clé {nom}) → {ligne}")
        champs = ("status", "iterations", "cost_usd", "text", "data", "error_type")
        print(f"  {'champ':<12} {'complete':<34} supervision")
        for champ in champs:
            entier = _court(vues["complete"].get(champ))
            masque = _court(vues["supervision"].get(champ))
            print(f"  {champ:<12} {entier:<34} {masque}")
        for nom in ("complete", "supervision"):
            verdicts: Any = vues[nom].get("verdicts") or []
            if not verdicts:
                continue
            note: Any = verdicts[0]["criteria"][0]
            motif: str = note["reason"] or "(retiré)"
            print(f"  verdict {nom:<12} {note['name']} {note['score']} — motif « {motif} »")

        titre("L'export JSONL de la session")
        exports: dict[str, str] = {}
        for nom in ("complete", "supervision"):
            code, exports[nom] = await appel(f"{base}/sessions/{session}/events", key=jetons[nom])
            controle.code(f"GET sessions/<id>/events ({nom})", 200, code, exports[nom])
        for nom, texte in exports.items():
            lignes = [json.loads(ligne) for ligne in texte.splitlines()]
            masques = [e for e in lignes if "redacted" in e["payload"]]
            retires = sorted({champ for e in masques for champ in e["payload"]["redacted"]})
            print(f"  {nom:<12} {len(lignes)} événements, {len(masques)} masqués")
            print(f"  {'':<12} champs retirés : {', '.join(retires) or 'aucun'}")
        print(
            f"  Le numéro {DEVIS} apparaît {exports['complete'].count(DEVIS)} fois côté complete,"
        )
        print(f"  {exports['supervision'].count(DEVIS)} fois côté supervision.")
        sans_texte = controle.tient(
            "la relecture masquée porte encore un texte", vues["supervision"]["text"] == ""
        )
        print(f"  relecture masquée sans texte : {sans_texte}")
        sans_devis = controle.tient(
            "le numéro de devis est resté dans l'export masqué",
            DEVIS not in exports["supervision"],
        )
        print(f"  export masqué sans le devis   : {sans_devis}")

    titre("Et sur le disque, le journal n'a rien perdu")
    fichier = _journal(session)
    if fichier is None:
        print("  (journal introuvable)")
        return
    contenu = fichier.read_text(encoding="utf-8")
    print(f"  {shown(fichier)}")
    print(f"  {DEVIS} y apparaît {contenu.count(DEVIS)} fois : le masquage est un accès,")
    print("  pas un stockage — sans ce que le modèle a lu, un run ne se rejoue pas.")
    intact = controle.tient("le journal sur le disque a perdu son contenu", DEVIS in contenu)
    print(f"  journal intact : {intact}")


def _court(valeur: Any) -> str:
    if valeur is None:
        return "—"
    texte = str(valeur)
    return texte if len(texte) <= 32 else f"{texte[:29]}…"


def _journal(session: SessionId) -> Path | None:
    """Le fichier JSONL de la session, sous le journal propre à Dupont."""
    data = CONFIG.parent / "data"
    trouves = sorted(data.rglob(f"{session}.jsonl"))
    return trouves[0] if trouves else None


# --- Cas 3 : une clé qui a une fin ------------------------------------------


async def expiration(config: LoomConfig, agent: str, prefixe: str, controle: Controle) -> None:
    jeton = new_api_key()
    fin = datetime.now(UTC) + COURTE
    key = ApiKey(
        id="temporaire",
        hash=fingerprint(jeton),
        tenant=DUPONT,
        scopes=("run", "read", "read_content"),
        expires=fin,
    )
    config = declarees(config, (key,))
    titre(f"Une clé valable {COURTE.seconds} secondes")
    print(f"  temporaire   {carte(key)}")

    async with Loom(config) as loom, serveur(loom) as base:
        code, corps = await appel(f"{base}/agents", key=jeton)
        ligne = controle.code("GET agents avant la fin", 200, code, corps)
        print(f"  tout de suite → {ligne}")
        reste = max(0.0, (fin - datetime.now(UTC)).total_seconds()) + 0.5
        print(f"  … on attend {reste:.1f} s …")
        await asyncio.sleep(reste)
        code, corps = await appel(f"{base}/agents", key=jeton)
        ligne = controle.code("GET agents après la fin", 401, code, corps)
        print(f"  après         → {ligne}")
        expiree = str(corps.get("detail", "")) if code == 401 else ""
        titre("Une clé inconnue ne dit pas la même chose")
        code, corps = await appel(f"{base}/agents", key=new_api_key())
        ligne = controle.code("GET agents, clé inconnue", 401, code, corps)
        print(f"  inconnue      → {ligne}")
        inconnue = str(corps.get("detail", "")) if code == 401 else ""
        distincts = controle.tient(
            "les deux 401 disent la même chose", bool(expiree) and expiree != inconnue
        )
        print(f"  deux 401 distincts : {distincts}")
    print(f"\n  CLI : uv run loom --config {shown(CONFIG)} validate  (montre l'état de chaque clé)")
    print(f"        uv run loom keys create app --tenant {DUPONT} --expires 90j --rate-limit 60")


# --- Mise en route -----------------------------------------------------------


async def jouer(nom: str, config: LoomConfig, agent: str, prefixe: str, controle: Controle) -> None:
    print(f"\n{'─' * 78}\nCas {nom}\n{'─' * 78}")
    if nom == "cles":
        await cles(config, agent, prefixe, controle)
    elif nom == "masquage":
        await masquage(config, agent, prefixe, controle)
    else:
        await expiration(config, agent, prefixe, controle)


async def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Clés d'API : portées, agents, expiration, contenu"
    )
    parser.add_argument("--reel", action="store_true", help="vrais modèles")
    parser.add_argument("--cas", action="append", choices=CAS, help="cas à jouer (tous par défaut)")
    parser.add_argument("--session", default=None, help="préfixe des sessions")
    args = parser.parse_args(argv)
    cas = tuple(args.cas) if args.cas else CAS
    prefixe = args.session or f"securite-{new_id()[-8:]}"

    try:
        config = load_config(CONFIG)
    except ConfigError as error:
        print(f"Configuration : {error}", file=sys.stderr)
        return 2
    apply_logging(config)
    agent = agent_de(args)
    print(f"Config   : {shown(CONFIG)}")
    print(f"Agent    : {agent}")
    print(f"Client   : {DUPONT}  (toutes les clés agissent en son nom)")
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
