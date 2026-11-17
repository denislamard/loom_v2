# SPDX-License-Identifier: Apache-2.0
"""Bus des nouvelles par la publication/abonnement de Redis (point 5, fonction H6).

Le bus de qui a déjà Redis, ou qui ne veut pas faire porter ses nouvelles par
sa base. Redis publie sans rien garder : un abonné absent au moment de la
publication ne la verra jamais. C'est exactement ce que le port assume — ce
qui est manqué se rattrape à la nouvelle suivante, puisque l'abonné garde sa
position et relit le journal depuis elle.

Une connexion à part pour écouter, comme chez Postgres : un abonnement
occupe sa connexion.

Le SDK ``redis`` est typé, mais ses méthodes asynchrones laissent des
``Unknown`` (des ``**kwargs`` non typés) : pyright strict s'en plaint à chaque
appel. Plutôt que huit ``ignore`` en ligne, les trois règles concernées sont
levées pour ce module — qui tient en quatre-vingts lignes et ne fait que
publier et écouter.
"""

# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false

import logging
from collections.abc import AsyncIterator
from typing import Final

import redis.asyncio as redis
from redis.asyncio.client import PubSub

from loom_ia.core.ports.bus import Notice

logger = logging.getLogger(__name__)

CHANNEL: Final = "loom:events"
"""Le canal des nouvelles. Fixe, comme le canal Postgres."""
SOCKET_TIMEOUT: Final = 5.0
"""Délai d'une connexion, et d'une réponse à une publication, en secondes.
L'écoute n'en a pas (le silence du canal y est normal) : un contrôle de
santé la remplace.
"""
HEALTH_CHECK: Final = 30
"""Intervalle du contrôle de santé de la connexion d'écoute, en secondes."""


class RedisBus:
    """Nouvelles diffusées par le canal ``loom:events`` d'un serveur Redis."""

    def __init__(self, url: str) -> None:
        self._url = url
        self._speaking: redis.Redis | None = None
        self._hearing: redis.Redis | None = None
        self._pubsub: PubSub | None = None
        self._closed = False

    def __repr__(self) -> str:
        return f"RedisBus({CHANNEL!r})"

    async def publish(self, notice: Notice) -> None:
        """Annonce un lot écrit sur le canal ``loom:events`` de Redis ; rien après la fermeture."""
        if self._closed:
            return
        if self._speaking is None:
            self._speaking = redis.from_url(self._url, socket_timeout=SOCKET_TIMEOUT, socket_connect_timeout=SOCKET_TIMEOUT)
        _ = await self._speaking.publish(CHANNEL, notice.model_dump_json())

    async def notices(self) -> AsyncIterator[Notice]:
        """Écoute le canal jusqu'à la fermeture du bus.

        Si la connexion d'écoute tombe, l'erreur du SDK (``ConnectionError``)
        sort telle quelle : c'est à l'appelant de s'abonner de nouveau.
        """
        if self._closed:
            return
        self._hearing = redis.from_url(self._url, socket_connect_timeout=SOCKET_TIMEOUT, health_check_interval=HEALTH_CHECK)
        pubsub = self._hearing.pubsub()
        self._pubsub = pubsub
        try:
            await pubsub.subscribe(CHANNEL)
            # ``listen`` s'arrête de lui-même quand il ne reste plus d'abonnement :
            # c'est ce que fait ``aclose``, et c'est ce qui rend la main ici.
            async for message in pubsub.listen():
                if message.get("type") != "message":
                    continue
                try:
                    yield Notice.model_validate_json(message["data"])
                except ValueError:
                    logger.warning("Bus Redis : nouvelle illisible, ignorée")
        finally:
            self._pubsub = None
            await pubsub.aclose()
            hearing = self._hearing
            self._hearing = None
            await hearing.aclose()

    async def aclose(self) -> None:
        """Ferme le bus : le désabonnement arrête l'écoute, puis les connexions sont fermées."""
        self._closed = True
        pubsub = self._pubsub
        if pubsub is not None:
            # Se désabonner met fin à l'écoute, sans passer par le canal :
            # un message de réveil irait à tous les autres process.
            await pubsub.unsubscribe(CHANNEL)
        speaking, self._speaking = self._speaking, None
        if speaking is not None:
            await speaking.aclose()
