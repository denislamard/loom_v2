# SPDX-License-Identifier: Apache-2.0
"""Snapshot de l'historique d'une session (#22, §11.2).

Rejouer tout le journal à chaque run coûte de plus en plus cher à mesure que
la conversation s'allonge. À la fin d'un run, l'historique déjà calculé est
donc écrit tel quel dans le journal, en ``session.snapshot`` : le run suivant
en repart et ne rejoue que la suite.

Le snapshot n'est qu'une vue : un lecteur qui l'ignore reconstruit le même
historique en relisant tout. Il n'est donc jamais nécessaire, et on ne
l'écrit que lorsqu'il fait gagner quelque chose — au moins
``snapshot_every`` événements depuis le dernier marqueur — pour ne pas
recopier l'historique dans le journal à chaque run.

Sa position s'arrête avant le premier événement d'un run encore en cours :
un run repris au milieu ne se projette pas.

À ne pas confondre avec la compaction (#23) : le snapshot est une vue
matérialisée sans LLM, pour lire vite ; la compaction est un résumé par LLM,
pour réduire le contexte.
"""

from collections.abc import Sequence
from typing import Final

from loom_ia.core.events import (
    Event,
    RunCancelled,
    RunCompleted,
    RunFailed,
    RunScope,
    RunStarted,
    SessionSnapshot,
)
from loom_ia.core.model import Message, RunId, RunState
from loom_ia.core.projections import history, last_marker
from loom_ia.engine import SessionWriter

# Estimation sans tokenizer : la sérialisation JSON d'un message, divisée par
# ce nombre de caractères. Elle sert aux seuils, pas à une facturation.
CHARS_PER_TOKEN: Final = 4


def estimate_tokens(messages: Sequence[Message]) -> int:
    """Taille approximative d'un historique, en tokens."""
    return sum(len(message.model_dump_json()) for message in messages) // CHARS_PER_TOKEN


def snapshot(events: Sequence[Event]) -> SessionSnapshot:
    """Snapshot de l'historique porté par ces événements, sans condition."""
    up_to_seq = boundary(events)
    covered = [event for event in events if event.seq <= up_to_seq]
    messages = tuple(history(covered))
    return SessionSnapshot(up_to_seq=up_to_seq, messages=messages, tokens=estimate_tokens(messages))


def due(events: Sequence[Event], *, every: int) -> bool:
    """Vrai si un snapshot ferait gagner assez de relecture pour être écrit."""
    return boundary(events) - marked(events) >= every


def marked(events: Sequence[Event]) -> int:
    """Position couverte par le dernier marqueur de session écrit, 0 s'il n'y en a pas."""
    found = last_marker(events)
    return found[0] if found is not None else 0


def boundary(events: Sequence[Event]) -> int:
    """Dernière position où l'on peut couper le journal sans couper un run."""
    return Cuts(events).at_most()


class Cuts:
    """Positions où couper le journal d'une session : jamais au milieu d'un run.

    Une coupe tombe avant tout run encore en cours, et jamais à l'intérieur
    d'un run clos : un run qui a des événements des deux côtés serait lu sans
    son ``run.started`` à la reprise, donc perdu de l'historique. Deux runs de
    la session qui se chevauchent (le premier fini, le second encore en cours)
    reculent donc la coupe avant le premier.

    Les marqueurs de session n'y comptent pas : sans cela, écrire un snapshot
    repousserait la position et en appellerait aussitôt un autre. Un marqueur
    écrit hors de tout run (coupe de sécurité) porte le run_id de la session :
    ce n'est pas un run, il n'est jamais « en cours ».

    Un seul parcours du journal, sans projeter les runs : seule compte la
    clôture (``run.completed``, ``run.failed`` ou ``run.cancelled``), que
    ``fold_all`` ne faisait que retrouver au prix d'une copie d'état par
    événement.

    Un sous-run ne repart que par l'appel de son parent : le parent clos, il
    ne repartira plus, et il est clos de fait, à son dernier événement. C'est
    ce que sont les orphelins des journaux d'avant la fermeture des sous-runs
    avec leur parent (2.0.0) : sans cela, leur parent clos, ils figeraient la
    frontière pour toujours. Rien n'est écrit : le journal reste tel quel.
    """

    def __init__(self, events: Sequence[Event]) -> None:
        first: dict[RunId, int] = {}
        last: dict[RunId, int] = {}
        parents: dict[RunId, RunId] = {}
        closed: set[RunId] = set()
        self._newest = 0
        for event in events:
            if event.category == "session":
                continue
            first.setdefault(event.run_id, event.seq)
            last[event.run_id] = event.seq
            self._newest = max(self._newest, event.seq)
            payload = event.payload
            if isinstance(payload, RunStarted) and payload.parent_run_id is not None:
                parents[event.run_id] = payload.parent_run_id
            elif isinstance(payload, RunCompleted | RunFailed | RunCancelled):
                closed.add(event.run_id)
        # Du plus ancien au plus récent : un parent est vu avant ses enfants.
        for run_id in sorted(first, key=first.__getitem__):
            if parents.get(run_id) in closed:
                closed.add(run_id)
        # Du dernier run commencé au premier ; sans fin tant que le run tourne.
        self._runs = [
            (first[run_id], last[run_id] if run_id in closed else None)
            for run_id in sorted(first, key=first.__getitem__, reverse=True)
        ]

    def at_most(self, position: int | None = None) -> int:
        """Plus grande coupe qui ne dépasse pas ``position`` (le journal entier par défaut).

        Elle ne fait que reculer, et un run ne la recoupe plus une fois qu'elle
        est passée avant lui : un seul passage, du dernier run au premier.
        """
        limit = self._newest if position is None else min(position, self._newest)
        for start, end in self._runs:
            if start <= limit and (end is None or limit < end):
                limit = start - 1
        return limit


async def write_snapshot(
    writer: SessionWriter,
    events: Sequence[Event],
    run: RunState,
    *,
    every: int,
) -> SessionSnapshot | None:
    """Écrit un snapshot si l'un est dû ; rend celui qui a été écrit."""
    if not due(events, every=every):
        return None
    payload = snapshot(events)
    if not payload.messages:
        return None
    scope = RunScope(
        tenant_id=run.context.tenant_id,
        session_id=run.session_id,
        run_id=run.run_id,
        root_run_id=run.root_run_id,
        agent=run.agent,
        span_id=run.span_id,
    )
    await writer.append([scope.draft(payload)])
    return payload
