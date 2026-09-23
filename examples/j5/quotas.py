# SPDX-License-Identifier: Apache-2.0
"""Phase 5.1b : ce qu'un artisan peut dépenser dans sa journée, et à quelle vitesse.

    uv run python examples/j5/quotas.py                       # les quatre cas
    uv run python examples/j5/quotas.py --cas budget
    uv run python examples/j5/quotas.py --cas quota
    uv run python examples/j5/quotas.py --cas reprise
    uv run --extra http python examples/j5/quotas.py --cas debit
    uv run --env-file .env --extra http --extra anthropic --extra openai \\
        python examples/j5/quotas.py --reel

Config : ``examples/j5/relance/``, celle de la phase 5.1a. Les plafonds et les
quotas sont posés **en code**, client par client : ils valent pour cet exemple
et ne pèsent pas sur les autres. Chaque lancement écrit dans son propre journal
(``data/quotas/<prefixe>``) — sans quoi la journée du lancement précédent aurait
déjà mangé l'enveloppe, et le premier run serait refusé d'entrée.

* **budget** : Dupont a une enveloppe pour la journée. Elle est lue **une
  fois**, au lancement du run : celui qui la dépasse va jusqu'au bout, et c'est
  le suivant qui est refusé. Un refus n'écrit rien au journal — il n'a pas eu
  lieu — et il dit quand la fenêtre se remet à zéro.
* **quota** : Martin a un débit, en runs par minute. Il ne borne pas ce qu'il
  dépense mais ce qu'il demande, et sa fenêtre glisse : deux runs à cheval sur
  la minute ne valent pas quatre.
* **reprise** : le compteur n'est qu'un cache. Une instance neuve, dont le
  compteur est vide, relit le journal depuis le début de la fenêtre et refuse
  du premier coup.
* **debit** : ce que reçoit l'appelant REST — un 429 et un ``Retry-After``,
  quelques secondes pour un quota, des heures pour une journée épuisée. Plus le
  débit d'une **clé** d'API, qui protège le serveur et non le client : il
  compte les lectures comme les lancements, et deux clés du même client n'ont
  pas le même.
"""

import argparse
import asyncio
import json
import os
import socket
import sys
import urllib.error
import urllib.request
from collections.abc import AsyncGenerator, Mapping
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from loom_ia.access import BudgetExhausted, Loom, QuotaExceeded, RunResult, TenantConsumption
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
from loom_ia.core.model import (
    WINDOW,
    BudgetLimit,
    BudgetPeriod,
    Budgets,
    Quotas,
    RateLimit,
    SessionId,
    TenantBudget,
    TenantId,
    new_id,
)
from loom_ia.runtime import apply_logging
from loom_ia.tenancy import PERIODS
from loom_ia.usage import amount

CONFIG = Path(__file__).parent / "relance" / "loom.yaml"
CAS = ("budget", "quota", "reprise", "debit")

DUPONT = TenantId("dupont-plomberie")
MARTIN = TenantId("martin-chauffage")
DEVIS: dict[TenantId, str] = {DUPONT: "D-2026-042", MARTIN: "D-2026-117"}
FENETRES: dict[BudgetPeriod, str] = {"day": "journée", "month": "mois"}

# Enveloppes de l'exemple, volontairement minuscules : un run simulé coûte
# environ 0,002 $, donc la journée de Dupont tient en trois relances.
PLAFOND_JOUR = 0.005
PLAFOND_MOIS = 0.05
# Moins qu'un run : le premier passe (l'enveloppe est intacte au lancement),
# le second est refusé. De quoi montrer un 429 sans attendre.
PLAFOND_COURT = 0.001
# Débit de Martin, et celui d'une clé de lecture.
PAR_MINUTE = 2
PAR_CLE = 2
# Garde-fou des boucles qui tournent jusqu'au refus.
ESSAIS = 8


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
        f"{result.cost_usd:.6f} $  {objet or result.text[:40]}"
    )


def attente(secondes: float) -> str:
    """Un délai, dit comme on l'annonce à un appelant."""
    if secondes < 90:
        return f"{secondes:.0f} s"
    heures, reste = divmod(int(secondes), 3600)
    return f"{heures} h {reste // 60:02d}"


def etat(found: TenantConsumption, limit: BudgetLimit = "max_cost") -> str:
    """Ce qui a été dépensé sur la fenêtre, et ce qu'il reste s'il y a un plafond."""
    left = found.left(limit)
    dit = (
        f"dépensé {amount(limit, found.spent.cost if limit == 'max_cost' else found.spent.tokens)}"
    )
    return dit if left is None else f"{dit}, reste {amount(limit, left)}"


def pose(found: TenantConsumption, limit: BudgetLimit, value: float) -> str:
    """Un plafond de la fenêtre, et ce qu'il reste dessus."""
    left = found.left(limit)
    return f"{limit} {amount(limit, value)} (reste {amount(limit, left or 0.0)})"


def agent_de(args: argparse.Namespace) -> str:
    return "relance_reel" if args.reel else "relance"


# --- Ce que l'exemple pose en code -------------------------------------------


def limite(
    config: LoomConfig,
    *,
    budgets: Mapping[TenantId, Mapping[str, float]] | None = None,
    quotas: Mapping[TenantId, int] | None = None,
) -> LoomConfig:
    """Pose les plafonds de période et les quotas, client par client.

    ``model_copy`` et non un aller-retour par ``model_dump`` : la fusion des
    budgets se fait clé par clé, et elle lit ``model_fields_set`` — qu'un
    aller-retour par le dictionnaire effacerait.
    """
    posed, debits = budgets or {}, quotas or {}
    tenants = tuple(
        tenant.model_copy(
            update={
                "budgets": Budgets(tenant=TenantBudget.model_validate(posed[tenant.id]))
                if tenant.id in posed
                else tenant.budgets,
                "quotas": Quotas(runs_per_minute=debits[tenant.id])
                if tenant.id in debits
                else tenant.quotas,
            }
        )
        for tenant in config.tenants
    )
    return config.model_copy(update={"tenants": tenants})


def journal_neuf(config: LoomConfig, prefixe: str) -> LoomConfig:
    """Un journal par lancement, pour que la journée reparte de zéro.

    C'est le seul artifice de cet exemple : sans lui, deux lancements le même
    jour partageraient la même enveloppe. En production, c'est justement ce
    partage qu'on veut — la journée d'un client est la journée d'un client.

    Un client qui déclare son propre journal garde le **nom de dossier** que sa
    fiche lui donne (``data/dupont`` → ``dupont``) : le journal range déjà ses
    événements sous le client, et prendre son identifiant ici le redoublerait.
    """
    base = CONFIG.parent / "data" / "quotas" / prefixe
    storage = config.storage.model_copy(
        update={"events": config.storage.events.model_copy(update={"path": base})}
    )
    tenants = tuple(
        tenant
        if tenant.storage is None or tenant.storage.events.path is None
        else tenant.model_copy(
            update={
                "storage": tenant.storage.model_copy(
                    update={
                        "events": tenant.storage.events.model_copy(
                            update={"path": base / tenant.storage.events.path.name}
                        )
                    }
                )
            }
        )
        for tenant in config.tenants
    )
    return config.model_copy(update={"storage": storage, "tenants": tenants})


# --- Cas 1 : l'enveloppe de la journée ---------------------------------------


async def budget(config: LoomConfig, agent: str, prefixe: str) -> None:
    config = limite(
        config,
        budgets={DUPONT: {"max_cost_per_day": PLAFOND_JOUR, "max_cost_per_month": PLAFOND_MOIS}},
    )
    titre("Ce que Dupont a le droit de dépenser")
    print(f"  tenants[{DUPONT}].budgets.tenant")
    print(f"    max_cost_per_day   : {amount('max_cost', PLAFOND_JOUR)}")
    print(f"    max_cost_per_month : {amount('max_cost', PLAFOND_MOIS)}")

    async with Loom(config) as loom:
        titre("Des relances jusqu'au refus")
        refuse = 0
        for numero in range(1, ESSAIS + 1):
            avant = etat(await loom.consumption(DUPONT))
            session = SessionId(f"{prefixe}-jour-{numero}")
            try:
                result = await loom.run(agent, demande(DUPONT), session_id=session, tenant=DUPONT)
            except BudgetExhausted as refus:
                refuse = numero
                print(f"  run {numero} : REFUSÉ    {avant}")
                print(f"           {refus.reached}")
                print(
                    f"           remise à zéro dans {attente(refus.retry_after)}, "
                    f"le {refus.reached.period.end:%Y-%m-%d %H:%M} UTC"
                )
                break
            print(f"  run {numero} : {ligne(result)}")
            print(f"           {avant} avant le lancement")

        titre("Le run refusé n'a pas existé")
        sessions = await loom.sessions(tenant_id=DUPONT)
        print(f"  journal de Dupont : {len(sessions)} session(s)")
        print(f"  session demandée par le run refusé : {prefixe}-jour-{refuse} — absente")

        titre("Les deux fenêtres, lues au journal")
        for kind in PERIODS:
            found = await loom.consumption(DUPONT, period=kind)
            plafonds = (
                ", ".join(pose(found, limit, value) for limit, value in found.limits)
                or "aucun plafond"
            )
            print(f"  {FENETRES[kind]:<8} {found.runs} run(s), {etat(found)}")
            print(f"           {plafonds}, remise à zéro le {found.resets_at:%Y-%m-%d %H:%M} UTC")

        titre("Martin n'a pas d'enveloppe : rien ne le borne")
        result = await loom.run(
            agent, demande(MARTIN), session_id=SessionId(f"{prefixe}-martin"), tenant=MARTIN
        )
        print(f"  {MARTIN:<18} {ligne(result)}")
        # Le rapport relit le journal, donc il vaut même pour un client dont le
        # compteur ne retient rien.
        print(f"  {' ' * 18} {etat(await loom.consumption(MARTIN))}")


# --- Cas 2 : le débit accordé à un client ------------------------------------


async def quota(config: LoomConfig, agent: str, prefixe: str) -> None:
    config = limite(config, quotas={MARTIN: PAR_MINUTE})
    titre(f"Le débit accordé à Martin : {PAR_MINUTE} run(s) par minute")
    print(f"  tenants[{MARTIN}].quotas.runs_per_minute : {PAR_MINUTE}")
    print("  vérifié à la façade : il vaut par Python, REST, MCP et la ligne de commande")

    async with Loom(config) as loom:
        titre("Quatre demandes coup sur coup")
        for numero in range(1, 5):
            session = SessionId(f"{prefixe}-minute-{numero}")
            try:
                result = await loom.run(agent, demande(MARTIN), session_id=session, tenant=MARTIN)
            except QuotaExceeded as refus:
                print(f"  run {numero} : REFUSÉ    {refus}")
                continue
            print(f"  run {numero} : {ligne(result)}")
        print(f"\n  La fenêtre glisse : la place se libère {WINDOW:g} s après le premier")
        print("  run servi, et non à la minute suivante de l'horloge.")

        titre("Un quota borne les demandes, pas la dépense")
        found = await loom.consumption(MARTIN)
        print(f"  {found.runs} run(s) au journal, {etat(found)}")
        print("  aucun plafond en dollars : Martin pourrait dépenser sans fin, lentement")

        titre("Dupont, sans quota, n'est pas concerné")
        for numero in range(1, 4):
            session = SessionId(f"{prefixe}-libre-{numero}")
            result = await loom.run(agent, demande(DUPONT), session_id=session, tenant=DUPONT)
            print(f"  run {numero} : {ligne(result)}")


# --- Cas 3 : le compteur n'est qu'un cache du journal ------------------------


async def reprise(config: LoomConfig, agent: str, prefixe: str) -> None:
    config = limite(config, budgets={DUPONT: {"max_cost_per_day": PLAFOND_JOUR}})
    titre("Une première instance épuise la journée de Dupont")
    async with Loom(config) as premiere:
        for numero in range(1, ESSAIS + 1):
            session = SessionId(f"{prefixe}-avant-{numero}")
            try:
                result = await premiere.run(
                    agent, demande(DUPONT), session_id=session, tenant=DUPONT
                )
            except BudgetExhausted as refus:
                print(f"  run {numero} : REFUSÉ — {refus.reached}")
                break
            print(f"  run {numero} : {ligne(result)}")
        found = await premiere.consumption(DUPONT)
    print(f"  journée : {found.runs} run(s), {etat(found)}")

    titre("Une instance neuve, dont le compteur est vide")
    async with Loom(config) as neuve:
        # Rien en mémoire : à la première question posée pour ce client et
        # cette fenêtre, le compteur se réchauffe en relisant les
        # `model.responded` du journal depuis minuit, puis il refuse.
        session = SessionId(f"{prefixe}-neuve")
        try:
            await neuve.run(agent, demande(DUPONT), session_id=session, tenant=DUPONT)
            print("  le premier run est passé : le compteur n'a rien relu !")
        except BudgetExhausted as refus:
            print(f"  premier run : REFUSÉ — {refus.reached}")
            print("  la vérité est au journal ; le compteur n'en est qu'un cache,")
            print("  et il se reconstruit fenêtre par fenêtre, à la première demande.")


# --- Cas 4 : ce que reçoit l'appelant REST -----------------------------------


def avec_cles(config: LoomConfig) -> tuple[LoomConfig, dict[TenantId, str], str]:
    """Une clé par client, plus une clé de lecture bridée en débit (#39).

    Le débit d'une clé n'est pas le quota d'un client : il protège le serveur,
    il compte toutes les requêtes — lectures comprises —, et deux clés du même
    client peuvent ne pas avoir le même.
    """
    cles = {DUPONT: new_api_key(), MARTIN: new_api_key()}
    bridee = new_api_key()
    keys = (
        ApiKey(id="app-dupont", hash=fingerprint(cles[DUPONT]), tenant=DUPONT),
        ApiKey(id="app-martin", hash=fingerprint(cles[MARTIN]), tenant=MARTIN),
        ApiKey(
            id="lecture-dupont",
            hash=fingerprint(bridee),
            tenant=DUPONT,
            scopes=("read",),
            rate_limit=RateLimit(per_minute=PAR_CLE),
        ),
    )
    return config.model_copy(update={"security": SecurityConfig(api_keys=keys)}), cles, bridee


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


def _http(
    url: str, *, method: str = "GET", body: Any = None, key: str = ""
) -> tuple[int, str, Any]:
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
            after = response.headers.get("retry-after") or ""
            return response.status, after, json.loads(response.read() or b"null")
    except urllib.error.HTTPError as error:
        after = error.headers.get("retry-after") or ""
        return error.code, after, json.loads(error.read() or b"null")


async def appel(
    url: str, *, method: str = "GET", body: Any = None, key: str = ""
) -> tuple[int, str, Any]:
    """La requête, hors de la boucle : urllib est synchrone, le serveur est ici."""
    return await asyncio.to_thread(_http, url, method=method, body=body, key=key)


def repondu(code: int, after: str, corps: Any) -> str:
    """Une réponse, en une ligne : le code, le motif, et l'attente annoncée."""
    dit = corps.get("detail") if code >= 400 else corps.get("status")
    delai = f"   Retry-After: {after} s ({attente(float(after))})" if after else ""
    return f"{code} {dit}{delai}"


async def debit(config: LoomConfig, agent: str, prefixe: str) -> None:
    config = limite(
        config,
        budgets={DUPONT: {"max_cost_per_day": PLAFOND_COURT}},
        quotas={MARTIN: 1},
    )
    config, cles, bridee = avec_cles(config)
    titre("Ce que porte la configuration")
    print(f"  {DUPONT:<18} budget  max_cost_per_day {amount('max_cost', PLAFOND_COURT)}")
    print(f"  {MARTIN:<18} quota   1 run par minute")
    print(f"  clé lecture-dupont  rate_limit {PAR_CLE} requêtes par minute")

    async with Loom(config) as loom, serveur(loom) as base:
        titre("Journée épuisée : 429, et l'attente se compte en heures")
        for numero in (1, 2):
            code, after, corps = await appel(
                f"{base}/agents/{agent}/runs",
                method="POST",
                body={"message": demande(DUPONT), "session_id": f"{prefixe}-rest-{numero}"},
                key=cles[DUPONT],
            )
            print(f"  POST …/runs (clé de Dupont) → {repondu(code, after, corps)}")

        titre("Quota dépassé : 429 aussi, mais l'attente est courte")
        for numero in (1, 2):
            code, after, corps = await appel(
                f"{base}/agents/{agent}/runs",
                method="POST",
                body={"message": demande(MARTIN), "session_id": f"{prefixe}-rest-m{numero}"},
                key=cles[MARTIN],
            )
            print(f"  POST …/runs (clé de Martin) → {repondu(code, after, corps)}")

        titre("Débit d'une clé : même une lecture est comptée")
        for _ in range(PAR_CLE + 1):
            code, after, corps = await appel(f"{base}/agents", key=bridee)
            dit = (
                corps.get("detail")
                if code >= 400
                else f"{len(corps)} agent(s) publié(s) pour {DUPONT}"
            )
            delai = f"   Retry-After: {after} s" if after else ""
            print(f"  GET  …/agents (clé bridée) → {code} {dit}{delai}")
        # La clé du même client, elle, n'est pas bridée : c'est bien la clé
        # qu'on protège, et non le client.
        code, _, corps = await appel(f"{base}/agents", key=cles[DUPONT])
        print(f"  GET  …/agents (clé de Dupont, non bridée) → {code} {len(corps)} agent(s)")

    print(f"\n  CLI : uv run loom --config {shown(CONFIG)} report --periode jour --tenant {DUPONT}")


# --- Mise en route -----------------------------------------------------------


async def jouer(nom: str, config: LoomConfig, agent: str, prefixe: str) -> None:
    print(f"\n{'─' * 78}\nCas {nom}\n{'─' * 78}")
    if nom == "budget":
        await budget(config, agent, prefixe)
    elif nom == "quota":
        await quota(config, agent, prefixe)
    elif nom == "reprise":
        await reprise(config, agent, prefixe)
    else:
        await debit(config, agent, prefixe)


async def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Budgets par période, quotas et débit")
    parser.add_argument("--reel", action="store_true", help="vrais modèles")
    parser.add_argument("--cas", action="append", choices=CAS, help="cas à jouer (tous par défaut)")
    parser.add_argument("--session", default=None, help="préfixe des sessions et du journal")
    args = parser.parse_args(argv)
    cas = tuple(args.cas) if args.cas else CAS
    prefixe = args.session or f"quotas-{new_id()[-8:]}"

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
    print(f"Journal  : {shown(CONFIG.parent / 'data' / 'quotas' / prefixe)}  (neuf à chaque appel)")
    for nom in cas:
        # Un journal par cas : chacun pose ses propres plafonds, et aucun
        # n'hérite de ce que le précédent a dépensé.
        neuf = journal_neuf(config, f"{prefixe}-{nom}")
        try:
            await jouer(nom, neuf, agent, prefixe)
        except (ModelConfigError, ConfigError) as error:
            print(f"Configuration : {error}", file=sys.stderr)
            return 2
        except ImportError as error:
            print(f"Extra manquant pour le cas {nom} : {error}", file=sys.stderr)
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
