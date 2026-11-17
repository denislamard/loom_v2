# SPDX-License-Identifier: Apache-2.0
"""Port du journal d'événements (point 22).

Le journal d'une session est une suite ordonnée d'événements numérotés à
partir de 1 (``seq``). Un run sans session a pour journal ``session_id =
run_id``. Le journal fait foi : tout le reste en est déduit.

Implémentations fournies (``adapters/stores``) : en mémoire (défaut, perdu à
l'arrêt), JSONL (un fichier par session), SQLite et Postgres. Le journal se
déclare sous ``storage.events``. Un agent qui peut se mettre en pause exige un
journal durable.
"""

from collections.abc import Sequence
from typing import Protocol

from pydantic import AwareDatetime, NonNegativeInt

from loom_ia.core.events.envelope import Event, EventDraft
from loom_ia.core.events.query import EventQuery
from loom_ia.core.model.base import DomainModel
from loom_ia.core.model.ids import RunId, SessionId, TenantId


class SessionRecord(DomainModel):
    """Repère d'une session, pour la lister (fonction F7).

    Attributes:
        session_id: La session.
        last_seq: Dernier ``seq`` écrit : la taille du journal.
        updated_at: Instant de la dernière écriture.
    """

    session_id: SessionId
    last_seq: NonNegativeInt = 0
    updated_at: AwareDatetime


class SequenceConflict(Exception):
    """Le journal a avancé depuis la dernière lecture (verrou optimiste) ; rien n'a été écrit.

    Args:
        session_id: La session.
        expected: Dernier ``seq`` que l'appelant croyait lire.
        actual: Dernier ``seq`` trouvé.

    Attributes:
        session_id: La session.
        expected: Dernier ``seq`` attendu.
        actual: Dernier ``seq`` trouvé.
    """

    def __init__(self, session_id: SessionId, expected: int, actual: int) -> None:
        super().__init__(f"Journal {session_id!r} : dernier seq attendu {expected}, trouvé {actual}")
        self.session_id = session_id
        self.expected = expected
        self.actual = actual


class JournalCorrupted(Exception):
    """Une ligne complète du journal est illisible (JSON invalide ou événement qui ne se valide pas)."""


class StorageNotReady(RuntimeError):
    """Le stockage n'est pas prêt à servir : c'est à son administrateur de le préparer.

    Base Postgres sans schéma ou à migrer par un rôle qui n'en a pas le droit,
    migration refusée, rôle applicatif impossible à prendre. Rien n'a été écrit.
    Défini dans le noyau, sans dépendre d'asyncpg, pour que la ligne de commande
    et les accès le reconnaissent sans l'extra ``postgres``.
    """


class JournalBusy(Exception):
    """Le journal est tenu par un autre écrivain au-delà du délai de verrou.

    Rien n'a été écrit : la même demande peut être refaite plus tard.
    """


class UnusableId(ValueError):
    """Un identifiant (client, session) que ce journal ne peut pas nommer.

    Reste une ``ValueError``, comme avant : celui qui l'interceptait ainsi la
    reçoit toujours. Les accès, eux, la distinguent d'une panne.
    """


def journal_key(drafts: Sequence[EventDraft]) -> tuple[TenantId, SessionId]:
    """Client et session communs à un lot d'écriture.

    Args:
        drafts: Le lot, non vide.

    Returns:
        Le couple (client, session) de tous les brouillons.

    Raises:
        ValueError: Les brouillons visent plusieurs journaux ; un lot s'écrit
            dans un seul.
    """
    keys = {(d.tenant_id, d.session_id) for d in drafts}
    if len(keys) != 1:
        raise ValueError("Un lot d'événements doit viser un seul journal (même client, même session)")
    return keys.pop()


class EventStore(Protocol):
    """Journal des événements, une suite par session."""

    async def append(self, drafts: Sequence[EventDraft], *, expected_seq: int | None) -> list[Event]:
        """Écrit un lot à la suite du journal d'une session, d'un seul tenant.

        Args:
            drafts: Le lot, dans un seul journal (même client, même session).
            expected_seq: Dernier ``seq`` connu de l'appelant ; ``None``
                désactive ce contrôle (compaction, point 23).

        Returns:
            Les événements écrits, numérotés à la suite.

        Raises:
            SequenceConflict: Le journal a avancé depuis ``expected_seq`` ;
                rien n'est écrit.
            JournalBusy: Un autre écrivain tient le journal au-delà du délai
                de verrou ; rien n'est écrit.
            UnusableId: Le client ou la session ne peut pas nommer un journal
                dans ce stockage.
        """
        ...

    async def read(
        self,
        tenant_id: TenantId,
        session_id: SessionId,
        *,
        after_seq: int = 0,
        run_id: RunId | None = None,
    ) -> list[Event]:
        """Lit le journal d'une session, dans l'ordre des ``seq``.

        Args:
            tenant_id: Le client.
            session_id: La session.
            after_seq: Ne rend que les événements après ce ``seq`` ; 0 : tout.
            run_id: Ne rend que les événements de ce run.

        Returns:
            Les événements ; une liste vide pour un journal inconnu.

        Raises:
            JournalCorrupted: Une ligne du journal est illisible.
        """
        ...

    async def query(self, query: EventQuery) -> list[Event]:
        """Cherche des événements par enveloppe et facettes, dans les journaux d'un client.

        Args:
            query: Les critères ; voir ``EventQuery`` pour l'ordre et la
                pagination.

        Returns:
            Une page d'événements, ``query.limit`` au plus.
        """
        ...

    async def last_seq(self, tenant_id: TenantId, session_id: SessionId) -> int:
        """Dernier ``seq`` du journal d'une session ; 0 s'il est vide ou inconnu."""
        ...

    async def sessions(self, tenant_id: TenantId) -> list[SessionRecord]:
        """Sessions d'un client, de la plus récemment écrite à la plus ancienne (fonction F7).

        Args:
            tenant_id: Le client.

        Returns:
            Un repère par session.
        """
        ...

    async def delete(self, tenant_id: TenantId, session_id: SessionId) -> int:
        """Supprime le journal d'une session (RGPD).

        Exception assumée à l'immuabilité du journal : le RGPD demande une
        suppression physique (conception §11.5). ``loom sessions delete`` et
        ``DELETE /v1/sessions/{id}`` y passent.

        Args:
            tenant_id: Le client.
            session_id: La session.

        Returns:
            Le nombre d'événements retirés.
        """
        ...

    async def aclose(self) -> None:
        """Ferme le journal et libère ses ressources (connexions, fichiers)."""
        ...
