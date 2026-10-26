# SPDX-License-Identifier: Apache-2.0
"""Phase 5.3b : la file chez un courtier, et les workers qui mènent les runs.

    uv run --extra rabbitmq python examples/j5/file_et_worker.py
    uv run --extra rabbitmq python examples/j5/file_et_worker.py --cas reprise
    uv run --extra rabbitmq python examples/j5/file_et_worker.py \\
        --url amqp://loom:loom@127.0.0.1:5672/
    uv run --env-file .env --extra rabbitmq --extra anthropic --extra openai \\
        python examples/j5/file_et_worker.py --reel

Il faut un vrai RabbitMQ : l'exemple dit quoi lancer s'il n'en trouve pas.

Config : ``examples/j5/relance/``, celle de 5.1a, avec la file posée **en
code**. Les trois instances de cet exemple — celle qui soumet et les deux
workers — sont dans le même process et partagent donc cette config ; c'est ce
qui permet de tout montrer sans écrire un fichier de config quelque part.

* **file** : qui publie n'exécute pas. L'instance qui soumet rend la main tout
  de suite ; c'est le worker qui pilote le run, et la concession au journal
  porte **son** identité, pas celle du soumettant.
* **reprise** : un run tenu par un worker qui ne répond plus. Le travail
  redélivré arrive trop tôt — le bail du mort court encore — et il est reposé
  pour l'après-bail ; un autre worker le mène alors au bout, sans que personne
  s'en occupe. C'est le mécanisme de la phase, en vitesse réduite.
* **differe** : RabbitMQ n'a pas de délai. Un travail différé va dans une file
  d'attente à durée de vie et retombe dans la file de travail à l'échéance.

Un vrai ``kill -9`` sur un vrai `loom worker` est dans
``tests/integration/test_worker_rabbitmq.py`` : ici, on coupe la connexion du
worker, ce qui revient au même pour le courtier.
"""

import argparse
import asyncio
import os
import sys
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

from loom_ia.access import Loom
from loom_ia.adapters.models import ModelConfigError
from loom_ia.config import ConfigError, LoomConfig, load_config
from loom_ia.config.models import QueueStorage
from loom_ia.core.events import EventDraft, RunClaimed
from loom_ia.core.model import RunId, SessionId, TenantId, new_id
from loom_ia.core.ports import Job
from loom_ia.runtime import apply_logging, create_event_store

type Attente = Callable[[], Awaitable[bool]]

CONFIG = Path(__file__).parent / "relance" / "loom.yaml"
CAS = ("file", "reprise", "differe")

# La config nomme la variable qui porte l'URL, jamais l'URL : l'exemple la pose
# comme le ferait un service.
VARIABLE = "LOOM_EXEMPLE_RABBITMQ_URL"
URL_PAR_DEFAUT = "amqp://loom:loom@127.0.0.1:5672/"

DUPONT = TenantId("dupont-plomberie")
DEVIS = "D-2026-042"
# Bail volontairement court : l'exemple attend dessus, et sans cela il faudrait
# patienter une minute pour voir la reprise.
BAIL = 3.0

AIDE = f"""\
Aucun RabbitMQ à cette adresse. Pour en avoir un, le temps de l'exemple :

  docker run --rm -d --name loom-rabbit \\
      -e RABBITMQ_DEFAULT_USER=loom -e RABBITMQ_DEFAULT_PASS=loom \\
      -p 5672:5672 rabbitmq:3

Puis relancer. L'utilisateur n'est pas `guest` : RabbitMQ ne le laisse se
connecter que depuis la machine du courtier, ce qu'un port publié par docker
n'est pas. Pour tout arrêter : docker rm -f loom-rabbit
Par défaut : {URL_PAR_DEFAUT}"""


def demande() -> str:
    return f"Relance le client du devis {DEVIS}, sur un ton cordial."


def shown(path: Path) -> str:
    return os.path.relpath(path)


def titre(texte: str) -> None:
    print(f"\n=== {texte} ===")


def agent_de(args: argparse.Namespace) -> str:
    return "relance_reel" if args.reel else "relance"


def sans_mot_de_passe(url: str) -> str:
    """L'URL telle qu'on peut l'imprimer : l'hôte et le compte, jamais le secret."""
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


# --- La file de l'exemple, posée en code -------------------------------------


def en_file(config: LoomConfig) -> LoomConfig:
    """File chez le courtier, et bail court pour que la reprise se voie."""
    storage = config.storage.model_copy(
        update={"queue": QueueStorage(backend="rabbitmq", url_env=VARIABLE)}
    )
    execution = config.execution.model_copy(update={"lease": BAIL})
    return config.model_copy(update={"storage": storage, "execution": execution})


# --- Les cas ------------------------------------------------------------------


async def cas_file(config: LoomConfig, prefixe: str, agent: str, controle: Controle) -> None:
    titre("file — qui publie n'exécute pas")
    session = SessionId(f"{prefixe}-file")
    async with Loom(config) as worker, Loom(config) as facade:
        print(f"  worker    : {worker.worker_id}")
        print(f"  soumettant: {facade.worker_id}")
        service = asyncio.create_task(worker.work())
        try:
            run_id = await facade.submit(agent, demande(), session_id=session, tenant=DUPONT)
            print(f"  run soumis : {run_id}")
            fini = await attendre(lambda: termine(facade, run_id, session), limite=30.0)
            print(f"  mené au bout : {controle.tient('run terminé', fini)}")
            pilotes = await claims(facade, session)
            pris = controle.vaut("pilote", [worker.worker_id], relais(pilotes))
            print(f"  concession prise par : {pris}")
            print(f"  dont renouvellements : {len(pilotes) - 1}")
        finally:
            await worker.stop_work()
            await service


async def cas_reprise(config: LoomConfig, prefixe: str, controle: Controle) -> None:
    titre("reprise — le bail d'un worker qui ne répond plus")
    session = SessionId(f"{prefixe}-reprise")
    mort = f"worker-{new_id()[-12:]}"
    run_id = await run_en_plan(config, session, worker=mort, bail=BAIL)
    print(f"  run en plan : {run_id}, tenu par {mort}")
    print(f"  bail        : {BAIL:g} s, que personne ne renouvellera")
    async with Loom(config) as worker:
        print(f"  worker      : {worker.worker_id}")
        depart = time.monotonic()
        # Le journal garde ce que les lancements précédents y ont laissé : on
        # ne compte pas les runs en plan, on vérifie que celui-ci y est.
        repris = await worker.recover(tenant_id=DUPONT)
        mis = controle.tient("run remis en file", run_id in repris)
        print(f"  remis en file : {mis} ({len(repris)} run(s) en plan au journal)")
        service = asyncio.create_task(worker.work())
        try:
            fini = await attendre(lambda: termine(worker, run_id, session), limite=BAIL + 30.0)
            duree = time.monotonic() - depart
            print(f"  mené au bout : {controle.tient('run terminé', fini)}, en {duree:.1f} s")
            # Le travail est arrivé trop tôt une première fois : c'est la
            # concession qui a tenu le second pilote jusqu'à la fin du bail.
            print(f"  après le bail : {controle.tient('attente du bail', duree >= BAIL)}")
            pilotes = relais(await claims(worker, session))
            passe = controle.tient("passage de relais", pilotes == [mort, worker.worker_id])
            print(f"  pilotes de ce run : {' puis '.join(pilotes)} ({passe})")
        finally:
            await worker.stop_work()
            await service


async def cas_differe(config: LoomConfig, controle: Controle) -> None:
    titre("differe — un travail qui attend son heure")
    from loom_ia.adapters.queue.rabbitmq import DELAY_QUEUE, RabbitMqTaskQueue

    vus: list[float] = []
    depart = time.monotonic()

    async def noter(job: Job) -> None:
        vus.append(time.monotonic() - depart)

    url = os.environ[VARIABLE]
    queue = RabbitMqTaskQueue(url, {"compaction": noter})
    try:
        print(f"  file d'attente : {DELAY_QUEUE} (durée de vie par message)")
        await queue.submit(
            Job(kind="compaction", tenant_id=DUPONT, session_id=SessionId("differe")), delay=1.0
        )
        service = asyncio.create_task(queue.serve())
        try:
            tot = await attendre(lambda: vu(vus), limite=0.4)
            print(f"  rien avant 0,4 s : {controle.tient("arrivé avant l'heure", not tot)}")
            arrive = await attendre(lambda: vu(vus), limite=15.0)
            print(f"  arrivé : {controle.tient('travail différé arrivé', arrive)}", end="")
            print(f", après {vus[0]:.1f} s" if vus else "")
        finally:
            await queue.stop()
            await service
    finally:
        await queue.aclose()


# --- Outils de l'exemple -------------------------------------------------------


async def vu(vus: list[float]) -> bool:
    return bool(vus)


async def termine(loom: Loom, run_id: RunId, session: SessionId) -> bool:
    state = await loom.state(run_id, session_id=session, tenant_id=DUPONT)
    return state.finished


async def attendre(condition: Attente, *, limite: float) -> bool:
    """Attend qu'une condition asynchrone tienne, et dit si elle a fini par tenir."""
    depart = time.monotonic()
    while time.monotonic() - depart < limite:
        if await condition():
            return True
        await asyncio.sleep(0.1)
    return await condition()


def relais(pilotes: list[str]) -> list[str]:
    """Les pilotes dans l'ordre, renouvellements repliés.

    Un bail se renouvelle en écrivant un ``run.claimed`` de plus : un run qui
    dure en porte autant que de renouvellements, tous du même worker. Compter
    les concessions compte donc les renouvellements, pas les passages de
    relais — et c'est le relais qui nous intéresse ici.
    """
    passages: list[str] = []
    for pilote in pilotes:
        if not passages or passages[-1] != pilote:
            passages.append(pilote)
    return passages


async def claims(loom: Loom, session: SessionId) -> list[str]:
    """Les workers qui ont pris la concession du run, dans l'ordre du journal."""
    events = await loom.store.read(DUPONT, session)
    return [e.payload.worker_id for e in events if isinstance(e.payload, RunClaimed)]


async def run_en_plan(config: LoomConfig, session: SessionId, *, worker: str, bail: float) -> RunId:
    """Écrit au journal un run laissé en plan, tenu par un worker qui ne répond plus."""
    from loom_ia.testing import RunJournal

    journal = RunJournal(agent="relance", session_id=session, tenant_id=DUPONT)
    journal.start(demande())
    drafts: list[EventDraft] = [
        *journal.take(),
        journal.scope.draft(
            RunClaimed(worker_id=worker, lease_until=datetime.now(UTC) + timedelta(seconds=bail))
        ),
    ]
    # Dupont déclare son propre journal (5.1a) : c'est là qu'il faut écrire.
    declared = next((t.storage for t in config.tenants if t.id == DUPONT), None)
    store = create_event_store(declared or config.storage)
    try:
        await store.append(drafts, expected_seq=0)
    finally:
        await store.aclose()
    return journal.run_id


# --- Mise en route ------------------------------------------------------------


async def joignable(url: str) -> str | None:
    """Rend un message d'aide si le courtier n'est pas utilisable, sinon ``None``."""
    try:
        import aio_pika
    except ImportError:
        return "Extra manquant : installer loom-ia[rabbitmq] (uv run --extra rabbitmq)"
    try:
        connection = await aio_pika.connect_robust(url, timeout=5)
    except (TimeoutError, OSError, aio_pika.exceptions.AMQPError) as error:
        return f"{AIDE}\n\nLa connexion a dit : {error}"
    await connection.close()
    return None


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="File RabbitMQ et workers : publier, mener, reprendre"
    )
    parser.add_argument("--reel", action="store_true", help="vrais modèles")
    parser.add_argument(
        "--url", default=URL_PAR_DEFAUT, help=f"URL du courtier (défaut : {URL_PAR_DEFAUT})"
    )
    parser.add_argument("--cas", action="append", choices=CAS, help="cas à jouer (tous par défaut)")
    parser.add_argument("--session", default=None, help="préfixe des sessions")
    args = parser.parse_args(argv)
    cas = tuple(args.cas) if args.cas else CAS
    prefixe = args.session or f"file-{new_id()[-8:]}"

    empeche = await joignable(args.url)
    if empeche is not None:
        print(empeche, file=sys.stderr)
        return 2

    try:
        config = load_config(CONFIG)
    except ConfigError as error:
        print(f"Configuration : {error}", file=sys.stderr)
        return 2
    apply_logging(config)
    os.environ[VARIABLE] = args.url
    agent = agent_de(args)
    print(f"Config   : {shown(CONFIG)}")
    print(f"Agent    : {agent}")
    print(f"Courtier : {sans_mot_de_passe(args.url)} (URL lue dans {VARIABLE})")
    print(f"Client   : {DUPONT}")
    print(f"Sessions : {prefixe}-…")
    controle = Controle()

    try:
        config = en_file(config)
        if "file" in cas:
            await cas_file(config, prefixe, agent, controle)
        if "reprise" in cas:
            await cas_reprise(config, prefixe, controle)
        if "differe" in cas:
            await cas_differe(config, controle)
    except (ModelConfigError, ConfigError) as error:
        print(f"Configuration : {error}", file=sys.stderr)
        return 2
    except ImportError as error:
        print(f"Extra manquant : {error}", file=sys.stderr)
        return 2
    return 0 if controle.bilan() else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
