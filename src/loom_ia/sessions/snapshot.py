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

from loom_ia.core.events import Event, RunScope, SessionSnapshot
from loom_ia.core.model import Message, RunId, RunState
from loom_ia.core.projections import fold_all, history
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
    """Position couverte par le dernier marqueur de session, 0 s'il n'y en a pas."""
    covered = 0
    for event in events:
        payload = event.payload
        if isinstance(payload, SessionSnapshot):
            covered = max(covered, payload.up_to_seq)
    return covered


def boundary(events: Sequence[Event]) -> int:
    """Dernière position où tous les runs commencés sont terminés.

    Les marqueurs de session n'y comptent pas : sans cela, écrire un snapshot
    repousserait la position et en appellerait aussitôt un autre.
    """
    first: dict[RunId, int] = {}
    for event in events:
        first.setdefault(event.run_id, event.seq)
    running = [first[run_id] for run_id, state in fold_all(events).items() if not state.finished]
    if running:
        return min(running) - 1
    return max((event.seq for event in events if event.category != "session"), default=0)


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
