# SPDX-License-Identifier: Apache-2.0
"""Arbre d'un run : le run et ses sous-runs, reconnus au fil du journal (C5).

Un sous-run s'écrit dans le journal de la session de son parent (#4). Pour
suivre un run avec ce qu'il délègue, on garde l'ensemble des runs de
l'arbre : un ``run.started`` dont le parent est déjà dans l'ensemble y fait
entrer son run. Comme un enfant démarre toujours après l'appel de son
parent, un seul passage dans l'ordre du journal suffit, y compris en direct.

Les marqueurs de session (``session.snapshot``, J4.1) n'en font pas partie :
ils portent le ``run_id`` du run qui les écrit, mais parlent de la session.
"""

from collections.abc import Iterable

from loom_ia.core.events import Event, RunStarted
from loom_ia.core.model import RunId


class RunTree:
    """Runs d'un arbre, enrichi par ``admit`` à chaque événement vu dans l'ordre.

    Avec ``subruns=False``, l'arbre se réduit au run lui-même.
    """

    def __init__(self, run_id: RunId, *, subruns: bool = True) -> None:
        self.run_id = run_id
        self.subruns = subruns
        self._runs: set[RunId] = {run_id}

    @property
    def runs(self) -> frozenset[RunId]:
        return frozenset(self._runs)

    def admit(self, event: Event) -> bool:
        """Vrai si l'événement appartient à l'arbre ; un sous-run qui démarre y entre."""
        if event.category == "session":
            return False
        if event.run_id in self._runs:
            return True
        payload = event.payload
        if self.subruns and isinstance(payload, RunStarted) and payload.parent_run_id in self._runs:
            self._runs.add(event.run_id)
            return True
        return False

    def select(self, events: Iterable[Event]) -> list[Event]:
        """Événements de l'arbre, dans l'ordre donné (celui du journal)."""
        return [event for event in events if self.admit(event)]
