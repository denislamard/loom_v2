# SPDX-License-Identifier: Apache-2.0
"""Port du bus : ce qu'un process dit aux autres après avoir écrit (point 5, fonctions H6 et I4).

Le bus ne transporte **pas** les événements. Il transporte une nouvelle :
« il y a du neuf dans le journal de (client, session), des seq N à M ». Qui
s'y intéresse relit le journal.

Trois raisons, et la première suffirait :

- un ``NOTIFY`` de Postgres est borné à 8 000 octets, et un événement les
  dépasse souvent — un bus qui porterait les événements ne marcherait pas sur
  Postgres, ou pas toujours, ce qui est pire ;
- le contenu ne sort jamais du journal. La politique de lignes (phase 5.3a) et
  le masquage (phase 5.2a) continuent donc de s'appliquer, puisqu'ils
  s'appliquent à la lecture ;
- le journal reste la seule source de vérité. Un bus qui perd un message fait
  perdre une notification, jamais un événement.

La nouvelle porte l'étendue du lot (``first_seq``, ``last_seq``) et la
**source** qui l'a écrit : un process ignore ses propres nouvelles — il a déjà
remis ces événements à ses abonnés au moment de les écrire.

Livraison au mieux, et assumée : un bus n'est pas durable. Ce qu'un abonné
rate pendant une coupure, il le rattrape à la nouvelle suivante, puisqu'il
garde sa position et relit depuis elle.

Implémentations fournies (``adapters/bus``) : en mémoire (un seul process),
Postgres (``LISTEN``/``NOTIFY``) et Redis. Le bus se déclare sous
``storage.bus``.
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol

from loom_ia.core.model.base import DomainModel
from loom_ia.core.model.ids import SessionId, TenantId


class Notice(DomainModel):
    """« Du neuf dans ce journal » : de quoi aller le relire.

    Un abonné qui n'a pas de position pour cette session part de
    ``first_seq``, et celui qui en a une relit depuis elle — ce qui rattrape ce
    que le bus aurait laissé passer.

    Attributes:
        tenant_id: Client du journal.
        session_id: Session du journal.
        first_seq: Premier ``seq`` du lot qui vient d'être écrit.
        last_seq: Dernier ``seq`` de ce lot.
        source: Instance qui a écrit ; sert à ne pas se réécouter soi-même.
    """

    tenant_id: TenantId
    session_id: SessionId
    first_seq: int
    last_seq: int
    source: str


@dataclass(frozen=True, slots=True)
class BusUnavailable(Exception):
    """Le bus n'a pas pu être joint ; l'écriture, elle, a eu lieu.

    Attributes:
        reason: Pourquoi ; le message dit « Bus indisponible : <raison> ».
    """

    reason: str

    def __str__(self) -> str:
        """Le message : « Bus indisponible : <raison> »."""
        return f"Bus indisponible : {self.reason}"


class EventBus(Protocol):
    """Diffuse les nouvelles d'écriture entre les process d'un même service."""

    async def publish(self, notice: Notice) -> None:
        """Annonce un lot écrit ; ne doit jamais faire échouer l'écriture.

        Args:
            notice: La nouvelle, avec l'étendue du lot et la source.
        """
        ...

    def notices(self) -> AsyncIterator[Notice]:
        """Les nouvelles des autres process, jusqu'à la fermeture du bus.

        Returns:
            Un itérateur asynchrone ; les nouvelles de ce process lui-même n'y
            sont pas.
        """
        ...

    async def aclose(self) -> None:
        """Ferme le bus ; l'itérateur de ``notices`` s'arrête."""
        ...
