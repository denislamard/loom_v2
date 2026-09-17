# SPDX-License-Identifier: Apache-2.0
"""Port du journal d'événements (#22).

Le journal d'une session est une suite ordonnée d'événements numérotés à
partir de 1 (``seq``). Un run sans session a pour journal ``session_id =
run_id``.
"""

from collections.abc import Sequence
from typing import Protocol

from loom_ia.core.events.envelope import Event, EventDraft
from loom_ia.core.events.query import EventQuery
from loom_ia.core.model.ids import RunId, SessionId, TenantId


class SequenceConflict(Exception):
    """Le journal a avancé depuis la dernière lecture (verrou optimiste)."""

    def __init__(self, session_id: SessionId, expected: int, actual: int) -> None:
        super().__init__(
            f"Journal {session_id!r} : dernier seq attendu {expected}, trouvé {actual}"
        )
        self.session_id = session_id
        self.expected = expected
        self.actual = actual


class JournalCorrupted(Exception):
    """Une ligne complète du journal est illisible."""


def journal_key(drafts: Sequence[EventDraft]) -> tuple[TenantId, SessionId]:
    """Client et session communs à un lot d'écriture.

    Un lot s'écrit dans un seul journal : sinon ``ValueError``.
    """
    keys = {(d.tenant_id, d.session_id) for d in drafts}
    if len(keys) != 1:
        raise ValueError(
            "Un lot d'événements doit viser un seul journal (même client, même session)"
        )
    return keys.pop()


class EventStore(Protocol):
    async def append(
        self, drafts: Sequence[EventDraft], *, expected_seq: int | None
    ) -> list[Event]:
        """Écrit le lot à la suite du journal et renvoie les événements numérotés.

        ``expected_seq`` est le dernier ``seq`` connu de l'appelant : si le
        journal a avancé, ``SequenceConflict`` est levée et rien n'est écrit.
        ``None`` désactive ce contrôle (compaction, #23).
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
        """Événements du journal après ``after_seq``, éventuellement d'un seul run."""
        ...

    async def query(self, query: EventQuery) -> list[Event]: ...

    async def last_seq(self, tenant_id: TenantId, session_id: SessionId) -> int:
        """Dernier ``seq`` du journal, 0 s'il est vide."""
        ...

    async def aclose(self) -> None: ...
