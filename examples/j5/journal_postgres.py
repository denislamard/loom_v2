# SPDX-License-Identifier: Apache-2.0
"""Phase 5.3a : le journal en Postgres, et la barrière que la base pose elle-même.

    uv run --extra postgres python examples/j5/journal_postgres.py
    uv run --extra postgres python examples/j5/journal_postgres.py --cas barriere
    uv run --extra postgres python examples/j5/journal_postgres.py \\
        --dsn postgresql://loom:loom@127.0.0.1:5432/loom
    uv run --env-file .env --extra postgres --extra anthropic --extra openai \\
        python examples/j5/journal_postgres.py --reel

Il faut un vrai Postgres : l'exemple dit quoi lancer s'il n'en trouve pas, et
refuse un DSN de superutilisateur — un superutilisateur contourne la sécurité
au niveau des lignes, et il n'y aurait plus rien à voir.

Config : ``examples/j5/relance/``, celle de 5.1a, avec le stockage posé **en
code** — journal et clés d'idempotence en Postgres, et les **deux** artisans
dans la même table. Dupont perd donc, le temps de cet exemple, le journal à lui
que la config lui donne : c'est justement quand la table est partagée que la
politique de lignes a quelque chose à faire.

* **journal** : un run pour chaque artisan, tous deux dans la même table, et
  chacun ne retrouve que ses sessions. La table, le rôle et la politique sont
  créés à la première requête (le SQL s'imprime avec ``loom storage sql``).
* **barriere** : ce que la base refuse, en dehors de tout code de loom — un
  ``SELECT`` sans filtre sur le client ne rend que le client courant, sans le
  réglage il ne rend rien, un ``UPDATE`` sur un événement écrit est refusé, et
  écrire pour un autre client l'est aussi.
* **cles** : deux magasins d'idempotence sur la même base, comme deux workers
  sur deux machines : ce que l'un réserve, l'autre le voit ; ce que l'un
  enregistre, l'autre le relit au lieu de refaire.
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

from loom_ia.access import Loom
from loom_ia.adapters.models import ModelConfigError
from loom_ia.adapters.postgres.sql import EVENTS_TABLE, IDEMPOTENCY_TABLE
from loom_ia.config import ConfigError, LoomConfig, load_config
from loom_ia.config.models import ArtifactsStorage, EventsStorage, IdempotencyStorage, StorageConfig
from loom_ia.core.model import SessionId, TenantId, new_id
from loom_ia.core.ports import IdempotencyStore, KeyScope
from loom_ia.runtime import apply_logging, create_idempotency_store

CONFIG = Path(__file__).parent / "relance" / "loom.yaml"
CAS = ("journal", "barriere", "cles")

# La config ne porte jamais un DSN, seulement le nom de la variable qui le
# porte : l'exemple pose donc la variable, comme le ferait un service.
VARIABLE = "LOOM_EXEMPLE_PG_DSN"
DSN_PAR_DEFAUT = "postgresql://loom:loom@127.0.0.1:5432/loom"

DUPONT = TenantId("dupont-plomberie")
MARTIN = TenantId("martin-chauffage")
DEVIS: dict[TenantId, str] = {DUPONT: "D-2026-042", MARTIN: "D-2026-117"}

AIDE = f"""\
Aucun Postgres à cette adresse. Pour en avoir un, le temps de l'exemple :

  docker run --rm -d --name loom-pg -e POSTGRES_PASSWORD=postgres \\
      -p 5432:5432 postgres:16
  docker exec loom-pg psql -U postgres \\
      -c "CREATE ROLE loom LOGIN PASSWORD 'loom' CREATEROLE;" \\
      -c "CREATE DATABASE loom OWNER loom;"

Puis relancer. Le rôle du DSN n'est **pas** le superutilisateur du conteneur :
un superutilisateur contourne la politique de lignes, et le cas `barriere`
n'aurait plus rien à montrer. Pour tout arrêter : docker rm -f loom-pg
Par défaut : {DSN_PAR_DEFAUT}"""

SUPERUTILISATEUR = """\
Ce DSN mène à un superutilisateur : il contourne la sécurité au niveau des
lignes, donc la barrière que cet exemple montre serait absente sans que rien
ne le dise. Utiliser un rôle ordinaire, propriétaire de sa base."""


def demande(tenant: TenantId) -> str:
    return f"Relance le client du devis {DEVIS[tenant]}, sur un ton cordial."


def shown(path: Path) -> str:
    return os.path.relpath(path)


def titre(texte: str) -> None:
    print(f"\n=== {texte} ===")


def agent_de(args: argparse.Namespace) -> str:
    return "relance_reel" if args.reel else "relance"


def sans_mot_de_passe(dsn: str) -> str:
    """Le DSN tel qu'on peut l'imprimer : hôte, port et base, jamais le secret."""
    parts = urlsplit(dsn)
    port = f":{parts.port}" if parts.port else ""
    return f"{parts.username or '?'}@{parts.hostname or '?'}{port}{parts.path}"


class Controle:
    """Ce que chaque appel devait rendre : l'exemple l'annonce, puis le vérifie."""

    def __init__(self) -> None:
        self.ecarts: list[str] = []

    def tient(self, quoi: str, vrai: bool) -> str:
        if not vrai:
            self.ecarts.append(quoi)
        return "oui" if vrai else "NON"

    def vaut(self, quoi: str, attendu: object, trouve: object) -> str:
        if trouve == attendu:
            return str(trouve)
        self.ecarts.append(f"{quoi} : {trouve} au lieu de {attendu}")
        return f"{trouve}   ← attendu {attendu}"

    def bilan(self) -> bool:
        if not self.ecarts:
            print("\nChaque appel a rendu ce que l'exemple annonçait.")
            return True
        print("\nUn appel au moins n'a pas rendu ce qui était annoncé :")
        for ecart in self.ecarts:
            print(f"  {ecart}")
        return False


# --- Le stockage de l'exemple, posé en code ----------------------------------


def en_postgres(config: LoomConfig) -> LoomConfig:
    """Journal et clés en Postgres, et les deux artisans dans la même table.

    Les artefacts restent en mémoire : un journal Postgres ne donne pas de
    dossier, et la config exige alors qu'on les déclare (l'exemple n'en
    produit pas). Le ``storage`` propre à Dupont est retiré : c'est ce
    partage-là que la politique de lignes garde.
    """
    storage = StorageConfig(
        events=EventsStorage(backend="postgres", dsn_env=VARIABLE),
        artifacts=ArtifactsStorage(backend="memory"),
        idempotency=IdempotencyStorage(backend="postgres", dsn_env=VARIABLE),
    )
    tenants = tuple(tenant.model_copy(update={"storage": None}) for tenant in config.tenants)
    return config.model_copy(update={"storage": storage, "tenants": tenants})


# --- Les cas ------------------------------------------------------------------


async def cas_journal(loom: Loom, prefixe: str, agent: str, controle: Controle) -> None:
    titre("journal — deux artisans, une table, chacun ses sessions")
    print(f"Table : {EVENTS_TABLE}, créée à la première requête (loom storage sql l'imprime)")
    for tenant in (DUPONT, MARTIN):
        session = SessionId(f"{prefixe}-{tenant}")
        result = await loom.run(agent, demande(tenant), tenant=tenant, session_id=session)
        print(f"\n  {tenant} : run {result.run_id}, statut {result.status}")
        print(f"    coût {result.cost_usd:.6f} $, {result.iterations} itération(s)")
        # La base garde ce que les lancements précédents y ont écrit : on ne
        # compte donc pas les sessions, on vérifie que celle-ci y est.
        vues = await loom.sessions(tenant_id=tenant)
        noms = [record.session_id for record in vues]
        ecrits = next((record.last_seq for record in vues if record.session_id == session), 0)
        sienne = controle.tient("session au journal", session in noms)
        print(f"    session retrouvée : {sienne} ({len(noms)} au journal de ce client)")
        print(f"    événements de cette session : {ecrits}")
        # Ce que l'autre voit du même journal : rien de celui-ci.
        autre = MARTIN if tenant == DUPONT else DUPONT
        chez_autre = [r.session_id for r in await loom.sessions(tenant_id=autre)]
        absente = controle.tient("fuite de session", session not in chez_autre)
        print(f"    absente des sessions de {autre} : {absente}")


async def cas_barriere(dsn: str, controle: Controle) -> None:
    titre("barriere — ce que la base refuse, sans une ligne de loom")
    import asyncpg

    from loom_ia.adapters.postgres.sql import DEFAULT_ROLE

    connection = await asyncpg.connect(dsn)
    try:
        await connection.execute(f"SET ROLE {DEFAULT_ROLE}")
        print(f"Rôle : {DEFAULT_ROLE} (celui que prend chaque connexion de loom)")

        total = await connection.fetchval(f"SELECT count(*) FROM {EVENTS_TABLE}")
        nue = controle.vaut("vue nue", 0, total)
        print(f"\n  sans le réglage du client, SELECT sans filtre : {nue} ligne(s)")

        # Ce que la même table porte, client par client : chacun ne compte que
        # ses lignes, et la somme dit qu'ils sont bien tous les deux dedans.
        comptes: dict[TenantId, int] = {}
        for tenant in (DUPONT, MARTIN):
            async with connection.transaction():
                await connection.execute("SELECT set_config('loom.tenant_id', $1, true)", tenant)
                comptes[tenant] = int(
                    await connection.fetchval(f"SELECT count(*) FROM {EVENTS_TABLE}") or 0
                )
        somme = " + ".join(f"{tenant} {n}" for tenant, n in comptes.items())
        print(f"  la table porte les deux journaux : {somme}")

        async with connection.transaction():
            await connection.execute("SELECT set_config('loom.tenant_id', $1, true)", DUPONT)
            vus = await connection.fetch(f"SELECT DISTINCT tenant_id FROM {EVENTS_TABLE}")
            clients = sorted(str(row["tenant_id"]) for row in vus)
            lus = controle.vaut("clients vus", [DUPONT], clients)
            print(f"  réglage {DUPONT}, SELECT sans filtre : {lus}")
            vues = await connection.fetchval(f"SELECT count(*) FROM {EVENTS_TABLE}")
            rendues = controle.vaut("lignes rendues", comptes[DUPONT], int(vues or 0))
            print(f"  et il en rend : {rendues} ligne(s), les siennes")

            refuse = False
            try:
                await connection.execute(f"UPDATE {EVENTS_TABLE} SET type = 'menti'")
            except asyncpg.InsufficientPrivilegeError:
                refuse = True
            tenu = controle.tient("UPDATE refusé", refuse)
            print(f"  modifier un événement écrit : refusé {tenu}")

        async with connection.transaction():
            await connection.execute("SELECT set_config('loom.tenant_id', $1, true)", DUPONT)
            refuse = False
            try:
                await connection.execute(
                    f"INSERT INTO {EVENTS_TABLE} (tenant_id, session_id, seq, event_id, ts,"
                    " run_id, root_run_id, type, category, status, facets, event)"
                    " VALUES ($1, 's', 1, 'e', now(), 'r', 'r', 'run.started', 'lifecycle',"
                    " 'info', '{}'::jsonb, '{}')",
                    MARTIN,
                )
            except asyncpg.InsufficientPrivilegeError:
                refuse = True
            print(f"  écrire pour {MARTIN} : refusé {controle.tient('INSERT refusé', refuse)}")
    finally:
        await connection.close()


async def cas_cles(config: LoomConfig, prefixe: str, controle: Controle) -> None:
    titre("cles — deux magasins sur la même base, comme deux workers")
    print(f"Table : {IDEMPOTENCY_TABLE} (pas de politique de lignes : la clé porte son client)")
    session = SessionId(f"{prefixe}-cles")
    scope = KeyScope(tenant_id=DUPONT, session_id=session)
    cle = f"{DUPONT}:relance:{DEVIS[DUPONT]}"
    un = create_idempotency_store(config)
    deux = create_idempotency_store(config)
    if un is None or deux is None:  # pragma: no cover - la config le garantit
        raise ConfigError("magasin d'idempotence attendu")
    try:
        print(f"\n  clé : {cle}")
        pris = await un.reserve(cle, ttl=60, scope=scope)
        print(f"  worker 1 réserve : {controle.tient('réservation prise', pris)}")
        encore = await deux.reserve(cle, ttl=60, scope=scope)
        seule = controle.tient("seconde réservation refusée", not encore)
        print(f"  worker 2 réserve la même : refusée {seule}")

        await un.complete(cle, {"envoye": True, "devis": DEVIS[DUPONT]})
        relu = await deux.get(cle)
        etat = relu.status if relu is not None else "rien"
        print(f"  worker 2 relit ce que 1 a fait : {controle.vaut('état relu', 'completed', etat)}")

        oubliees = await deux.forget(DUPONT, session)
        comptees = controle.vaut("clés oubliées", 1, oubliees)
        print(f"  la session s'efface avec ses clés : {comptees}")
        print(f"  après l'oubli : {controle.vaut('clé restante', None, await un.get(cle))}")
    finally:
        await referme(un)
        await referme(deux)


async def referme(store: IdempotencyStore) -> None:
    """Ferme un magasin qui tient une connexion ; les autres n'ont rien à fermer.

    Le port ne déclare pas ``aclose`` : seuls les magasins adossés à une base
    en ont un. C'est ce que fait ``Loom`` à sa fermeture, ici à la main
    puisque ces deux magasins sont montés hors de lui.
    """
    closing = getattr(store, "aclose", None)
    if closing is not None:
        await closing()


# --- Mise en route ------------------------------------------------------------


async def joignable(dsn: str) -> str | None:
    """Rend un message d'aide si la base n'est pas utilisable, sinon ``None``."""
    try:
        import asyncpg
    except ImportError:
        return "Extra manquant : installer loom-ia[postgres] (uv run --extra postgres)"
    try:
        connection = await asyncpg.connect(dsn, timeout=5)
    except (OSError, asyncpg.PostgresError) as error:
        return f"{AIDE}\n\nLa connexion a dit : {error}"
    try:
        if await connection.fetchval("SELECT current_setting('is_superuser')") == "on":
            return SUPERUTILISATEUR
    finally:
        await connection.close()
    return None


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Journal Postgres : table partagée, barrière RLS")
    parser.add_argument("--reel", action="store_true", help="vrais modèles")
    parser.add_argument(
        "--dsn", default=DSN_PAR_DEFAUT, help=f"DSN Postgres (défaut : {DSN_PAR_DEFAUT})"
    )
    parser.add_argument("--cas", action="append", choices=CAS, help="cas à jouer (tous par défaut)")
    parser.add_argument("--session", default=None, help="préfixe des sessions")
    args = parser.parse_args(argv)
    cas = tuple(args.cas) if args.cas else CAS
    prefixe = args.session or f"pg-{new_id()[-8:]}"
    if "barriere" in cas and "journal" not in cas:
        # `barriere` lit ce que `journal` écrit : plutôt que de lire une table
        # vide, on joue `journal` d'abord et on le dit.
        print("Le cas `barriere` lit un journal : `journal` est joué d'abord.")
        cas = ("journal", *cas)

    empeche = await joignable(args.dsn)
    if empeche is not None:
        print(empeche, file=sys.stderr)
        return 2

    try:
        config = load_config(CONFIG)
    except ConfigError as error:
        print(f"Configuration : {error}", file=sys.stderr)
        return 2
    apply_logging(config)
    os.environ[VARIABLE] = args.dsn
    agent = agent_de(args)
    print(f"Config   : {shown(CONFIG)}")
    print(f"Agent    : {agent}")
    print(f"Base     : {sans_mot_de_passe(args.dsn)} (DSN lu dans {VARIABLE})")
    print(f"Clients  : {', '.join(config.tenant_ids)}")
    print(f"Sessions : {prefixe}-…")
    controle = Controle()

    try:
        config = en_postgres(config)
        async with Loom(config) as loom:
            if "journal" in cas:
                await cas_journal(loom, prefixe, agent, controle)
            if "barriere" in cas:
                await cas_barriere(args.dsn, controle)
            if "cles" in cas:
                await cas_cles(config, prefixe, controle)
    except (ModelConfigError, ConfigError) as error:
        print(f"Configuration : {error}", file=sys.stderr)
        return 2
    except ImportError as error:
        print(f"Extra manquant : {error}", file=sys.stderr)
        return 2
    return 0 if controle.bilan() else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
