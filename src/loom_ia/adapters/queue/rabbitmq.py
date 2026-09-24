# SPDX-License-Identifier: Apache-2.0
"""File de tâches servie par RabbitMQ : les runs tournent dans les workers (#27, H6).

Deux moitiés dans la même classe, et une seule vit à la fois dans un process :

- **publier** (`submit`) : c'est ce que fait une instance ``Loom`` ordinaire,
  `loom serve` comprise. Elle met en file et n'exécute rien ;
- **consommer** (`serve`) : c'est ce que fait `loom worker`, et lui seul.

Ce partage est le but de la phase : un process web qui pilote des runs n'en est
plus un. La contrepartie, dite au chargement par ``loom validate``, est qu'une
file `rabbitmq` sans worker derrière laisse ses tâches attendre.

**Au moins une fois, jamais exactement une fois.** L'acquittement suit
l'exécution : un worker tué sans préavis ne l'envoie pas, et le courtier
redélivre. Deux pilotes pour un même run, c'est la concession qui les sépare
(#27) ; une tâche rejouée retrouve un état qu'elle reconnaît, puisque le
journal fait foi. Une tâche qui **échoue** est acquittée quand même, et
journalisée : la rejouer sans fin serait pire que de la perdre.

**Le délai n'existe pas chez RabbitMQ.** Un travail différé va dans une file
sans consommateur, avec une durée de vie par message ; à l'échéance, le
courtier le laisse tomber dans la file de travail (``dead-letter``). Le piège
est connu et assumé : cette file d'attente est une file, donc un message à
longue échéance retient ceux qui le suivent. Les délais de loom sont du même
ordre de grandeur (l'expiration d'une approbation), et cette expiration est de
toute façon relue au journal (4.3a) — le travail différé n'est qu'un rappel.

``state`` rend toujours ``unknown`` et ``cancel`` toujours faux : un courtier
ne sait pas dire où en est une tâche, et ne retire pas un message publié. Le
port l'a prévu, et l'état d'un run se lit au journal.
"""

import asyncio
import json
import logging
from collections.abc import Mapping
from typing import Any, Final, cast

import aio_pika
from aio_pika.abc import AbstractIncomingMessage, AbstractQueue, AbstractQueueIterator

from loom_ia.adapters.queue.asyncio_queue import Handler
from loom_ia.core.model import new_id
from loom_ia.core.ports import Job, JobKind, JobState

logger = logging.getLogger(__name__)

# File de travail, et file d'attente des travaux différés.
WORK_QUEUE: Final = "loom.jobs"
DELAY_QUEUE: Final = "loom.jobs.delayed"
# À l'échéance de sa durée de vie, un message de la file d'attente retombe dans
# la file de travail. Exposées, parce que RabbitMQ refuse une redéclaration qui
# diverge : qui déclare cette file ailleurs doit le faire à l'identique.
DELAY_ARGUMENTS: Final[dict[str, Any]] = {
    "x-dead-letter-exchange": "",
    "x-dead-letter-routing-key": WORK_QUEUE,
}


def encoded(job_id: str, job: Job) -> bytes:
    """Le travail tel qu'il voyage : du JSON, lisible dans l'interface du courtier."""
    return json.dumps(
        {
            "id": job_id,
            "kind": job.kind,
            "tenant_id": job.tenant_id,
            "session_id": job.session_id,
            "run_id": job.run_id,
            "params": dict(job.params),
        },
        ensure_ascii=False,
    ).encode()


def decoded(body: bytes) -> tuple[str, Job]:
    """Rend l'identifiant et le travail ; ``ValueError`` si le message n'en est pas un."""
    try:
        found: Any = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Message illisible : {exc}") from exc
    if not isinstance(found, dict):
        raise ValueError("Message qui n'est pas un objet JSON")
    data = cast("dict[str, Any]", found)
    kind: object = data.get("kind")
    if kind not in ("compaction", "run", "resume", "expire_approval"):
        raise ValueError(f"Travail de type inconnu : {kind!r}")
    return str(data.get("id") or new_id()), Job(
        kind=kind,
        tenant_id=data["tenant_id"],
        session_id=data["session_id"],
        run_id=data.get("run_id"),
        params=data.get("params") or {},
    )


class RabbitMqTaskQueue:
    """File durable chez un courtier ; les tâches tournent dans `loom worker`.

    ``handlers`` ne sert qu'à consommer : une instance qui ne fait que publier
    peut les avoir sans que rien ne les appelle.
    """

    def __init__(self, url: str, handlers: Mapping[JobKind, Handler] | None = None) -> None:
        self._url = url
        self._handlers = dict(handlers or {})
        self._connection: aio_pika.abc.AbstractRobustConnection | None = None
        self._channel: aio_pika.abc.AbstractChannel | None = None
        self._declared: AbstractQueue | None = None
        self._iterator: AbstractQueueIterator | None = None
        self._stopping = False

    def __repr__(self) -> str:
        return f"RabbitMqTaskQueue({WORK_QUEUE!r})"

    # --- Raccordement -----------------------------------------------------

    async def _work_queue(self, *, prefetch: int = 0) -> AbstractQueue:
        """Le canal et les deux files, déclarés une fois, à la première demande.

        Les files sont déclarées **aussi par celui qui publie** : un message
        adressé à une file qui n'existe pas est jeté sans un mot par le
        courtier.
        """
        if self._channel is None:
            self._connection = await aio_pika.connect_robust(self._url)
            self._channel = await self._connection.channel()
            if prefetch:
                await self._channel.set_qos(prefetch_count=prefetch)
            await self._channel.declare_queue(DELAY_QUEUE, durable=True, arguments=DELAY_ARGUMENTS)
            self._declared = await self._channel.declare_queue(WORK_QUEUE, durable=True)
        assert self._declared is not None
        return self._declared

    # --- Publier ----------------------------------------------------------

    async def submit(self, job: Job, *, key: str | None = None, delay: float | None = None) -> str:
        """Publie le travail et rend son identifiant.

        ``key`` **est** l'identifiant quand elle est donnée : le courtier ne
        dédoublonne pas, mais deux mises en file de la même clé rendent le même
        identifiant, et ce que la clé protégeait est protégé ailleurs — la
        concession pour un run, l'idempotence pour un effet.
        """
        await self._work_queue()
        assert self._channel is not None
        job_id = key or new_id()
        message = aio_pika.Message(
            encoded(job_id, job),
            content_type="application/json",
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            message_id=job_id,
            type=job.kind,
            expiration=delay if delay else None,
        )
        await self._channel.default_exchange.publish(
            message, routing_key=DELAY_QUEUE if delay else WORK_QUEUE
        )
        return job_id

    async def state(self, job_id: str) -> JobState:
        """Toujours ``unknown`` : un courtier ne suit pas ses messages (voir l'en-tête)."""
        return "unknown"

    async def cancel(self, job_id: str) -> bool:
        """Toujours faux : un message publié ne se retire pas. Annuler un run, si."""
        return False

    async def drain(self) -> None:
        """Rien à attendre : ce qui est publié tourne ailleurs."""
        return None

    # --- Consommer (`loom worker`) ----------------------------------------

    async def serve(self, *, jobs: int = 1) -> None:
        """Consomme jusqu'à l'arrêt ; ne rend la main qu'une fois tout fini.

        ``jobs`` est le nombre de travaux menés en même temps, et c'est aussi
        ce que le courtier accepte de confier d'avance à ce worker.
        """
        queue = await self._work_queue(prefetch=jobs)
        places = asyncio.Semaphore(jobs)
        self._stopping = False
        async with asyncio.TaskGroup() as group:
            self._iterator = queue.iterator()
            async with self._iterator as arrivals:
                async for message in arrivals:
                    if self._stopping:
                        # Le travail retourne en file : un autre le prendra.
                        await message.nack(requeue=True)
                        break
                    await places.acquire()
                    _ = group.create_task(self._handle(message, places))
        self._iterator = None

    async def stop(self) -> None:
        """Demande l'arrêt : plus de travail pris, ceux en cours vont au bout."""
        self._stopping = True
        if self._iterator is not None:
            await self._iterator.close()

    async def _handle(self, message: AbstractIncomingMessage, places: asyncio.Semaphore) -> None:
        """Mène un travail, puis l'acquitte — dans cet ordre, c'est tout l'enjeu."""
        try:
            try:
                job_id, job = decoded(message.body)
            except ValueError as error:
                # Un message que loom ne comprend pas ne sera jamais compris :
                # l'acquitter est la seule sortie qui ne boucle pas.
                logger.error("Travail illisible, abandonné : %s", error)
                await message.ack()
                return
            handler = self._handlers.get(job.kind)
            if handler is None:
                logger.error("Travail %r sans traitement dans ce worker, abandonné", job.kind)
                await message.ack()
                return
            try:
                await handler(job)
            except Exception:
                # Journalisé, acquitté : rejouer sans fin serait pire. Ce que
                # le run en retient, il l'a écrit lui-même au journal.
                logger.exception("Travail %s (%s) en échec", job_id, job.kind)
            await message.ack()
        finally:
            places.release()

    async def aclose(self) -> None:
        await self.stop()
        if self._connection is not None:
            await self._connection.close()
        self._connection = None
        self._channel = None
