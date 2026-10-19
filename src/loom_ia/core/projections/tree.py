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

from loom_ia.core.events import Event, RunStarted, SessionCompacted
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


class OpeningSummaries:
    """Résumés de session faits à l'ouverture d'un run, reconnus au fil du journal.

    Avant son premier tour, un run peut faire résumer la session (compaction
    bloquante, ou filet ``hard_tokens``) : un run de compaction démarre avec
    ``triggered_by`` égal à ce run, et écrit ``session.compacted``. Ni l'un ni
    l'autre ne sont dans l'arbre du run — un marqueur de session n'en fait pas
    partie, et le run de compaction n'est pas un sous-run. Au test F3 du
    19/10, ni la console ni ``--stream`` ne disaient donc qu'un résumé avait eu
    lieu. Seuls comptent ceux d'avant le ``run.started`` du run : un résumé de
    fond qu'il déclenche en finissant parle du run suivant.
    """

    def __init__(self, run_id: RunId) -> None:
        self.run_id = run_id
        self._summarisers: set[RunId] = set()
        self._opened = False

    def admit(self, event: Event) -> bool:
        """Vrai pour un ``session.compacted`` écrit à l'ouverture du run."""
        if self._opened:
            return False
        payload = event.payload
        if isinstance(payload, RunStarted):
            if event.run_id == self.run_id:
                self._opened = True
            elif payload.kind == "compaction" and payload.triggered_by == self.run_id:
                self._summarisers.add(event.run_id)
            return False
        return isinstance(payload, SessionCompacted) and event.run_id in self._summarisers

    def select(self, events: Iterable[Event]) -> list[Event]:
        """Les ``session.compacted`` de l'ouverture du run, dans l'ordre du journal."""
        return [event for event in events if self.admit(event)]
