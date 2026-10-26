# SPDX-License-Identifier: Apache-2.0
"""Phase 5.3c : le bus des nouvelles, et un run suivi depuis un autre process.

    uv run --extra postgres --extra http python examples/j5/bus_et_sse.py
    uv run --extra postgres --extra http python examples/j5/bus_et_sse.py --cas sse
    uv run --extra redis --extra http python examples/j5/bus_et_sse.py --bus redis
    uv run --env-file .env --extra postgres --extra redis --extra http \\
        --extra anthropic --extra openai python examples/j5/bus_et_sse.py --reel

Il faut un vrai bus — Postgres par défaut, Redis avec ``--bus redis`` — et, pour
le troisième cas, un Redis. L'exemple dit quoi lancer s'il ne trouve pas.

Config : ``examples/j5/relance/``, celle de 5.1a, avec le bus posé **en code**.
Le journal reste celui de la config (des fichiers JSONL), et c'est important :
le bus ne transporte pas les événements, seulement « du neuf ici, jusqu'au
seq N ». Qui s'y intéresse relit le journal.

* **traverse** : deux instances, deux connexions au bus, un seul journal. Ce
  que l'une écrit, l'autre le remet à ses abonnés — sans savoir que ça vient
  d'ailleurs.
* **sse** : un run piloté dans un **vrai sous-process**, suivi ici en SSE par
  l'API REST. Le flux s'ouvre sur la position du journal pendant que le run
  dort dans son outil : tout ce qui suit n'a pu venir que du bus.
* **cles** : les clés d'idempotence dans Redis, partagées par deux magasins
  comme par deux machines — et ce que Redis fait de plus que SQL, oublier tout
  seul au bout de la rétention.
"""

import argparse
import asyncio
import os
import sys
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from urllib.parse import urlsplit

from loom_ia.access import Loom
from loom_ia.adapters.models import ModelConfigError
from loom_ia.adapters.stores import NotifyingEventStore
from loom_ia.config import (
    ApiKey,
    ConfigError,
    LoomConfig,
    SecurityConfig,
    fingerprint,
    load_config,
    new_api_key,
)
from loom_ia.config.models import BusStorage, IdempotencyStorage, StorageConfig
from loom_ia.core.events import Event
from loom_ia.core.model import SessionId, TenantId, new_id
from loom_ia.core.ports import KeyScope
from loom_ia.runtime import apply_logging, create_bus, create_event_store, create_idempotency_store

type Attente = Callable[[], Awaitable[bool]]

CONFIG = Path(__file__).parent / "relance" / "loom.yaml"
CAS = ("traverse", "sse", "cles")
BUS = ("postgres", "redis")

# La config nomme la variable qui porte le raccordement, jamais le
# raccordement : l'exemple les pose, comme le ferait un service.
BUS_VARIABLE = "LOOM_EXEMPLE_BUS"
REDIS_VARIABLE = "LOOM_EXEMPLE_REDIS"
DSN_PAR_DEFAUT = "postgresql://loom:loom@127.0.0.1:5432/loom"
REDIS_PAR_DEFAUT = "redis://127.0.0.1:6379/0"

DUPONT = TenantId("dupont-plomberie")
DEVIS = "D-2026-042"
# De quoi laisser un abonnement s'établir : une nouvelle publiée avant lui
# serait perdue, un bus ne garde rien.
ABONNEMENT = 1.0

PILOTE = """
import asyncio
import sys

from loom_ia.access.api import Loom
from loom_ia.config import load_config
from loom_ia.config.models import BusStorage

CONFIG, SESSION, RUN, KIND, KEY, VARIABLE = sys.argv[1:7]


async def main() -> None:
    # Le bus est posé ici aussi : la config du dépôt ne le déclare pas, et un
    # bus que seul l'un des deux process connaît ne porte rien. En production,
    # c'est le fichier de config qui le dit, et les deux le lisent.
    config = load_config(CONFIG)
    bus = BusStorage.model_validate({"backend": KIND, KEY: VARIABLE})
    storage = config.storage.model_copy(update={"bus": bus})
    config = config.model_copy(update={"storage": storage})
    # Reprend un run laissé en plan par l'exemple : c'est ici, dans cet autre
    # process, que les appels de modèle ont lieu et que le journal s'écrit.
    async with Loom(config) as loom:
        result = await loom.resume(RUN, session_id=SESSION, tenant_id="dupont-plomberie")
        print(result.status, flush=True)


asyncio.run(main())
"""

AIDE_POSTGRES = f"""\
Aucun Postgres à cette adresse. Celui de la phase 5.3a suffit :

  docker run --rm -d --name loom-pg -e POSTGRES_PASSWORD=postgres \\
      -p 5432:5432 postgres:16
  docker exec loom-pg psql -U postgres \\
      -c "CREATE ROLE loom LOGIN PASSWORD 'loom' CREATEROLE;" \\
      -c "CREATE DATABASE loom OWNER loom;"

Par défaut : {DSN_PAR_DEFAUT}"""

AIDE_REDIS = f"""\
Aucun Redis à cette adresse. Pour en avoir un, le temps de l'exemple :

  docker run --rm -d --name loom-redis -p 6379:6379 redis:7

Pour tout arrêter : docker rm -f loom-redis
Par défaut : {REDIS_PAR_DEFAUT}"""


def demande() -> str:
    return f"Relance le client du devis {DEVIS}, sur un ton cordial."


def shown(path: Path) -> str:
    return os.path.relpath(path)


def titre(texte: str) -> None:
    print(f"\n=== {texte} ===")


def agent_de(args: argparse.Namespace) -> str:
    return "relance_reel" if args.reel else "relance"


def sans_mot_de_passe(url: str) -> str:
    """L'adresse telle qu'on peut l'imprimer : jamais le secret."""
    parts = urlsplit(url)
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


# --- Le bus de l'exemple, posé en code ---------------------------------------


def sur_le_bus(config: LoomConfig, kind: str) -> LoomConfig:
    """Pose le bus déclaré, et laisse le journal de la config tel qu'il est."""
    key = "dsn_env" if kind == "postgres" else "url_env"
    bus = BusStorage.model_validate({"backend": kind, key: BUS_VARIABLE})
    storage = config.storage.model_copy(update={"bus": bus})
    return config.model_copy(update={"storage": storage})


def avec_une_cle(config: LoomConfig) -> tuple[LoomConfig, str]:
    """Une clé de lecture pour Dupont : c'est elle qui dit au nom de qui on suit.

    Sans clé, l'API REST agit pour ``default``, qui n'est pas un client déclaré
    (5.1a) : le flux d'un run de Dupont serait refusé, et l'exemple montrerait
    un bus muet là où c'est l'authentification qui manque.
    """
    jeton = new_api_key()
    key = ApiKey(
        id="suivi",
        hash=fingerprint(jeton),
        tenant=DUPONT,
        scopes=("read", "read_content"),
    )
    return config.model_copy(update={"security": SecurityConfig(api_keys=(key,))}), jeton


def sur_redis(config: LoomConfig) -> LoomConfig:
    """Pose le magasin d'idempotence Redis, pour le troisième cas."""
    keys = IdempotencyStorage(backend="redis", url_env=REDIS_VARIABLE)
    storage = config.storage.model_copy(update={"idempotency": keys})
    return config.model_copy(update={"storage": storage})


# --- Les cas ------------------------------------------------------------------


async def cas_traverse(config: LoomConfig, prefixe: str, controle: Controle) -> None:
    titre("traverse — ce qu'une instance écrit, l'autre le voit")
    session = SessionId(f"{prefixe}-traverse")
    ici_bus, ailleurs_bus = create_bus(config), create_bus(config)
    if ici_bus is None or ailleurs_bus is None:  # pragma: no cover - la config le garantit
        raise ConfigError("bus attendu")
    journal = create_event_store(_storage_of(config))
    ici = NotifyingEventStore(journal, bus=ici_bus, source="ici")
    ailleurs = NotifyingEventStore(journal, bus=ailleurs_bus, source="ailleurs")
    vus: list[Event] = []
    try:
        print(f"  bus : {config.storage.bus.backend}, une connexion par instance")
        with ici.listen(vus.append):
            suivi = asyncio.create_task(ici.follow())
            await asyncio.sleep(ABONNEMENT)
            try:
                run = await _petit_run(ailleurs, session)
                arrive = await attendre(lambda: vu(vus, len(run)), limite=15.0)
                print(f"  écrit par « ailleurs » : {len(run)} événement(s)")
                print(f"  reçus par « ici » : {controle.vaut('reçus', len(run), len(vus))}")
                print(f"  sans rien savoir d'ailleurs : {controle.tient('traversée', arrive)}")
            finally:
                suivi.cancel()
    finally:
        await ici_bus.aclose()
        await ailleurs_bus.aclose()
        await journal.aclose()


async def cas_sse(config: LoomConfig, prefixe: str, agent: str, controle: Controle) -> None:
    titre("sse — un run piloté ailleurs, suivi ici")
    import httpx
    from httpx import ASGITransport

    from loom_ia.access.http import create_app

    session = SessionId(f"{prefixe}-sse")
    # Un run laissé en plan, et le flux ouvert **avant** que quiconque le
    # reprenne : tout ce qui arrivera ensuite sera venu du bus, sans supposition
    # sur qui a été le plus rapide.
    run_id, depuis = await run_en_plan(config, session, agent)
    print(f"  run en plan : {run_id}, journal au seq {depuis}")
    servi, jeton = avec_une_cle(config)
    scratch = Path(os.environ.get("TMPDIR", "/tmp"))
    pilote = scratch / f"pilote-{new_id()[-8:]}.py"
    pilote.write_text(PILOTE, encoding="utf-8")
    async with Loom(servi) as loom:
        transport = ASGITransport(app=create_app(loom))
        async with httpx.AsyncClient(transport=transport, base_url="http://exemple") as http:
            url = f"/v1/runs/{run_id}/events?session_id={session}&after_seq={depuis}"
            flux = asyncio.create_task(lire_sse(http, url, jeton, limite=60.0))
            await asyncio.sleep(ABONNEMENT)
            child = await asyncio.create_subprocess_exec(
                sys.executable,
                str(pilote),
                str(CONFIG),
                str(session),
                run_id,
                config.storage.bus.backend,
                "dsn_env" if config.storage.bus.dsn_env else "url_env",
                BUS_VARIABLE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                env={**os.environ},
            )
            assert child.stdout is not None
            statut = (await child.stdout.readline()).decode().strip()
            await child.wait()
            print(f"  repris ailleurs : {controle.vaut('statut', 'completed', statut)}")
            code, recus = await flux
    pilote.unlink(missing_ok=True)
    print(f"  flux : HTTP {controle.vaut('code du flux', 200, code)}")
    print(f"  événements reçus du bus : {len(recus)}")
    fin = controle.tient("fin du run reçue", "run.completed" in recus)
    print(f"  dont run.completed : {fin}")


async def cas_cles(config: LoomConfig, prefixe: str, controle: Controle) -> None:
    titre("cles — l'idempotence dans Redis, partagée comme entre machines")
    session = SessionId(f"{prefixe}-cles")
    scope = KeyScope(tenant_id=DUPONT, session_id=session)
    cle = f"{DUPONT}:relance:{DEVIS}"
    un, deux = create_idempotency_store(config), create_idempotency_store(config)
    if un is None or deux is None:  # pragma: no cover - la config le garantit
        raise ConfigError("magasin d'idempotence attendu")
    try:
        print(f"  clé : {cle}")
        pris = await un.reserve(cle, ttl=60, scope=scope)
        print(f"  machine 1 réserve : {controle.tient('réservation prise', pris)}")
        encore = await deux.reserve(cle, ttl=60, scope=scope)
        print(f"  machine 2 réserve la même : refusée {controle.tient('refus', not encore)}")
        await un.complete(cle, {"envoye": True, "devis": DEVIS})
        relu = await deux.get(cle)
        etat = controle.vaut("état", "completed", relu.status if relu else "-")
        print(f"  machine 2 relit : {etat}")
        # Ce que Redis fait de plus que SQL : il oublie tout seul. Une clé dont
        # la rétention est nulle disparaît au lieu de rester consultable.
        court = f"{cle}:court"
        assert await un.reserve(court, ttl=0.2, scope=scope)
        await un.complete(court, {"vieux": True}, ttl=0.2)
        await asyncio.sleep(0.5)
        print(f"  résultat hors rétention : {controle.vaut('oublié', None, await deux.get(court))}")
        oubliees = controle.vaut("clés oubliées", 1, await deux.forget(DUPONT, session))
        print(f"  la session s'efface avec ses clés : {oubliees}")
    finally:
        await referme(un)
        await referme(deux)


# --- Outils de l'exemple -------------------------------------------------------


def _storage_of(config: LoomConfig) -> StorageConfig:
    """Le stockage de Dupont, qui déclare son propre journal (5.1a)."""
    declared = next((t.storage for t in config.tenants if t.id == DUPONT), None)
    return declared or config.storage


async def _petit_run(store: NotifyingEventStore, session: SessionId) -> list[Event]:
    """Écrit au journal les quelques événements d'un run fictif."""
    from loom_ia.testing import RunJournal

    journal = RunJournal(agent="relance", session_id=session, tenant_id=DUPONT)
    journal.start(demande())
    journal.complete()
    last = await store.last_seq(DUPONT, session)
    return await store.append(journal.take(), expected_seq=last)


async def run_en_plan(config: LoomConfig, session: SessionId, agent: str) -> tuple[str, int]:
    """Écrit un run qui attend son premier appel de modèle, et rend sa position.

    En plan et sans concession : le premier pilote qui le reprend le mène au
    bout — ici, celui du sous-process.
    """
    from loom_ia.testing import RunJournal

    journal = RunJournal(agent=agent, session_id=session, tenant_id=DUPONT)
    journal.start(demande())
    store = create_event_store(_storage_of(config))
    try:
        events = await store.append(journal.take(), expected_seq=0)
    finally:
        await store.aclose()
    return str(journal.run_id), events[-1].seq


async def vu(vus: list[Event], combien: int) -> bool:
    return len(vus) >= combien


async def referme(store: object) -> None:
    """Ferme un magasin qui tient une connexion ; les autres n'ont rien à fermer."""
    closing = getattr(store, "aclose", None)
    if closing is not None:
        await closing()


async def attendre(condition: Attente, *, limite: float) -> bool:
    depart = time.monotonic()
    while time.monotonic() - depart < limite:
        if await condition():
            return True
        await asyncio.sleep(0.1)
    return await condition()


async def lire_sse(http: object, url: str, jeton: str, *, limite: float) -> tuple[int, list[str]]:
    """Types des événements lus sur un flux SSE, jusqu'à sa fin ou au délai.

    Le délai compte : un flux qui n'attend plus rien ne se ferme pas, il envoie
    des battements de cœur. Sans bus, l'exemple attendrait pour toujours au
    lieu de dire ce qui manque.
    """
    import httpx

    assert isinstance(http, httpx.AsyncClient)
    seen: list[str] = []
    headers = {"Authorization": f"Bearer {jeton}"}
    code = 0
    try:
        async with asyncio.timeout(limite):
            async with http.stream("GET", url, headers=headers, timeout=limite) as response:
                code = response.status_code
                if code != 200:
                    return code, seen
                async for line in response.aiter_lines():
                    field, _, value = line.partition(":")
                    if field.strip() == "event":
                        seen.append(value.strip())
    except TimeoutError:
        return code, seen
    return code, seen


# --- Mise en route ------------------------------------------------------------


async def joignable(kind: str, url: str) -> str | None:
    """Rend un message d'aide si le service n'est pas utilisable, sinon ``None``."""
    if kind == "postgres":
        try:
            import asyncpg
        except ImportError:
            return "Extra manquant : installer loom-ia[postgres] (uv run --extra postgres)"
        try:
            connection = await asyncpg.connect(url, timeout=5)
        except (OSError, asyncpg.PostgresError, TimeoutError) as error:
            return f"{AIDE_POSTGRES}\n\nLa connexion a dit : {error}"
        await connection.close()
        return None
    try:
        import redis.asyncio as redis
    except ImportError:
        return "Extra manquant : installer loom-ia[redis] (uv run --extra redis)"
    client = redis.from_url(url)
    try:
        _ = await client.ping()  # pyright: ignore[reportUnknownMemberType]
    except (OSError, redis.RedisError, TimeoutError) as error:
        return f"{AIDE_REDIS}\n\nLa connexion a dit : {error}"
    finally:
        await client.aclose()
    return None


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bus des nouvelles et SSE entre process")
    parser.add_argument("--reel", action="store_true", help="vrais modèles")
    parser.add_argument("--bus", choices=BUS, default="postgres", help="bus à utiliser")
    parser.add_argument("--url", default=None, help="adresse du bus (défaut : selon --bus)")
    parser.add_argument(
        "--redis",
        default=REDIS_PAR_DEFAUT,
        help=f"Redis du cas `cles` (défaut : {REDIS_PAR_DEFAUT})",
    )
    parser.add_argument("--cas", action="append", choices=CAS, help="cas à jouer (tous par défaut)")
    parser.add_argument("--session", default=None, help="préfixe des sessions")
    args = parser.parse_args(argv)
    cas = tuple(args.cas) if args.cas else CAS
    prefixe = args.session or f"bus-{new_id()[-8:]}"
    url = args.url or (DSN_PAR_DEFAUT if args.bus == "postgres" else REDIS_PAR_DEFAUT)

    empeche = await joignable(args.bus, url)
    if empeche is not None:
        print(empeche, file=sys.stderr)
        return 2
    if "cles" in cas:
        empeche = await joignable("redis", args.redis)
        if empeche is not None:
            print(empeche, file=sys.stderr)
            return 2

    try:
        config = load_config(CONFIG)
    except ConfigError as error:
        print(f"Configuration : {error}", file=sys.stderr)
        return 2
    apply_logging(config)
    os.environ[BUS_VARIABLE] = url
    os.environ[REDIS_VARIABLE] = args.redis
    agent = agent_de(args)
    print(f"Config   : {shown(CONFIG)}")
    print(f"Agent    : {agent}")
    print(f"Bus      : {args.bus} — {sans_mot_de_passe(url)} (lu dans {BUS_VARIABLE})")
    print(f"Client   : {DUPONT}")
    print(f"Sessions : {prefixe}-…")
    controle = Controle()

    try:
        config = sur_le_bus(config, args.bus)
        if "traverse" in cas:
            await cas_traverse(config, prefixe, controle)
        if "sse" in cas:
            await cas_sse(config, prefixe, agent, controle)
        if "cles" in cas:
            await cas_cles(sur_redis(config), prefixe, controle)
    except (ModelConfigError, ConfigError) as error:
        print(f"Configuration : {error}", file=sys.stderr)
        return 2
    except ImportError as error:
        print(f"Extra manquant : {error}", file=sys.stderr)
        return 2
    return 0 if controle.bilan() else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
