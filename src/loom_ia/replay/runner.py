# SPDX-License-Identifier: Apache-2.0
"""Rejouer un run à l'identique, et dire où il s'écarte de son journal (K6, #31).

Le rejeu se fait **en mémoire**, sur une copie de la session arrêtée juste
après la demande du run : tout ce qui précédait — les runs d'avant, leurs
résumés, les instantanés — est là, si bien que l'historique et la requête se
reconstruisent comme la première fois. Le run garde **son identifiant** : un
juge tiré au sort (``sample``) l'est sur lui, et le tirage est donc le même.

Puis le moteur reprend le run là où la demande l'a laissé (``drive``), avec un
agent monté comme d'habitude à trois choses près : ses clients de modèle
répondent depuis le journal, ses outils aussi (sauf les rôles, qui sont de la
logique), et ses approbations sont tranchées par les décisions enregistrées.

Rien n'est écrit dans le vrai journal. ``export`` garde le journal du rejeu.
"""

import asyncio
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from loom_ia.adapters.stores import InMemoryEventStore
from loom_ia.core.events import (
    Event,
    EventDraft,
    RunCancelled,
    RunCompleted,
    RunFailed,
    RunStarted,
    RunTransitioned,
    UserMessage,
)
from loom_ia.core.model import RunId, RunStatus, SessionId, TenantId
from loom_ia.core.ports import EventStore
from loom_ia.engine import RunContext, drive
from loom_ia.replay.book import Divergence, ReplayBook

# États qui dépendent de la façon de piloter, pas de ce que le run a fait : une
# approbation tranchée en ligne ne passe pas par la pause, un sous-agent rejoué
# depuis le journal ne fait pas attendre son parent.
_ASIDE: Final = frozenset({RunStatus.PAUSED, RunStatus.WAITING_CHILD})
_TERMINAL: Final = (RunCompleted, RunFailed, RunCancelled)


class ReplayError(ValueError):
    """Ce run ne peut pas être rejoué (introuvable, inachevé, sous-run…)."""


@dataclass(frozen=True, slots=True)
class ReplayReport:
    """Ce que le rejeu a trouvé, comparé au journal."""

    run_id: RunId
    session_id: SessionId
    tenant_id: TenantId
    agent: str
    mode: str
    identical: bool
    divergence: Divergence | None
    # Issue du run : ``completed``, ``failed`` ou ``cancelled``.
    original_end: str
    replay_end: str
    # (au journal, servis au rejeu par le journal). Les outils sont ceux que le
    # journal sert — pas les rôles, qui tournent et se comptent par leurs
    # appels de modèle.
    model_calls: tuple[int, int]
    tool_calls: tuple[int, int]
    # Événements du run rejoué, demande comprise.
    events: tuple[Event, ...] = field(default=(), repr=False)

    def as_json(self) -> dict[str, object]:
        divergence = self.divergence
        return {
            "run_id": self.run_id,
            "session_id": self.session_id,
            "tenant_id": self.tenant_id,
            "agent": self.agent,
            "mode": self.mode,
            "identical": self.identical,
            "divergence": None
            if divergence is None
            else {
                "kind": divergence.kind,
                "where": divergence.where,
                "detail": divergence.detail,
                "parts": list(divergence.parts),
                "expected_hash": divergence.expected_hash,
                "actual_hash": divergence.actual_hash,
            },
            "original_end": self.original_end,
            "replay_end": self.replay_end,
            "model_calls": {"journal": self.model_calls[0], "replay": self.model_calls[1]},
            "tool_calls": {"journal": self.tool_calls[0], "replay": self.tool_calls[1]},
        }


type ContextFactory = Callable[[EventStore, ReplayBook, str, TenantId], RunContext]


async def replay_exact(
    session_events: Sequence[Event],
    run_id: RunId,
    context_for: ContextFactory,
    *,
    export: Path | None = None,
) -> ReplayReport:
    """Rejoue le run ``run_id`` de cette session et le compare à son journal.

    ``context_for(store, book, agent, tenant)`` monte l'agent du run pour le
    rejeu : le journal en mémoire, et le livre qui sert les réponses.
    """
    own = [e for e in session_events if e.run_id == run_id]
    if not own or not isinstance(own[0].payload, RunStarted):
        raise ReplayError(f"Run {run_id} introuvable dans cette session")
    first, started = own[0], own[0].payload
    if started.parent_run_id is not None:
        raise ReplayError(
            f"Run {run_id} : c'est le sous-run de {started.parent_run_id} — rejouer le run "
            "racine, qui sert le résultat de ce sous-agent depuis le journal"
        )
    if started.kind != "normal":
        raise ReplayError(f"Run {run_id} : run système ({started.kind}), non rejouable")
    ending = next((e for e in reversed(own) if isinstance(e.payload, _TERMINAL)), None)
    if ending is None:
        raise ReplayError(
            f"Run {run_id} : inachevé (en cours ou en pause) — un rejeu compare un run fini"
        )
    asked = next((e for e in own if isinstance(e.payload, UserMessage)), None)
    if asked is None:
        raise ReplayError(f"Run {run_id} : pas de demande au journal")

    # La session telle qu'elle était quand le run a reçu sa demande : mêmes
    # rangs, donc mêmes repères pour les instantanés et les résumés.
    prefix = [e for e in session_events if e.seq <= asked.seq]
    store = InMemoryEventStore()
    await store.append([_draft(e) for e in prefix], expected_seq=0)
    book = ReplayBook.of(own)
    ctx = context_for(store, book, first.agent or "", first.tenant_id)
    await drive(ctx, run_id, session_id=first.session_id, tenant_id=first.tenant_id)

    replayed = [
        e for e in await store.read(first.tenant_id, first.session_id) if e.run_id == run_id
    ]
    after = [e for e in replayed if e.seq > asked.seq]
    replay_ending = next((e for e in reversed(after) if isinstance(e.payload, _TERMINAL)), None)
    _compare(book, own, after, ending, replay_ending)
    report = ReplayReport(
        run_id=run_id,
        session_id=first.session_id,
        tenant_id=first.tenant_id,
        agent=first.agent or "",
        mode="exact",
        identical=book.divergence is None,
        divergence=book.divergence,
        original_end=_end(ending),
        replay_end=_end(replay_ending),
        model_calls=(len(book.responses), book.served_responses),
        tool_calls=(book.journal_tools, book.served_tools),
        events=tuple(replayed),
    )
    if export is not None:
        lines = "".join(f"{event.model_dump_json()}\n" for event in replayed)
        await asyncio.to_thread(export.write_text, lines, encoding="utf-8")
    return report


def _compare(
    book: ReplayBook,
    original: Sequence[Event],
    replayed: Sequence[Event],
    ending: Event,
    replay_ending: Event | None,
) -> None:
    """Ce qui ne se voit qu'à la fin : appels non refaits, issue, réponse, parcours."""
    if book.diverged:
        return
    missing_models = book.unserved_responses
    missing_tools = book.unserved_tools
    if missing_models or missing_tools:
        said: list[str] = []
        if missing_models:
            said.append(f"{len(missing_models)} appel(s) de modèle")
        if missing_tools:
            said.append(f"{len(missing_tools)} appel(s) d'outil")
        book.diverge(
            Divergence(
                kind="end",
                where="le rejeu s'est arrêté avant le run d'origine",
                detail=f"non refaits : {' et '.join(said)}",
            )
        )
        return
    if replay_ending is None or replay_ending.type != ending.type:
        book.diverge(
            Divergence(
                kind="end",
                where="le run ne finit pas de la même façon",
                detail=f"{_end(ending)} au journal, {_end(replay_ending)} au rejeu",
            )
        )
        return
    if not _same_outcome(ending, replay_ending):
        book.diverge(
            Divergence(
                kind="end",
                where="la réponse finale diffère",
                detail="même issue, autre réponse (texte ou données)",
            )
        )
        return
    if _path(original) != _path(replayed):
        book.diverge(
            Divergence(
                kind="end",
                where="le parcours du run diffère",
                detail=f"{' → '.join(_path(original))} au journal ; "
                f"{' → '.join(_path(replayed))} au rejeu",
            )
        )


def _path(events: Sequence[Event]) -> list[str]:
    """Les états traversés, sans ceux qui tiennent à la façon de piloter."""
    states: list[str] = []
    for event in events:
        if isinstance(event.payload, RunTransitioned):
            state = event.payload.to_state
            if state in _ASIDE or (states and states[-1] == state):
                continue
            states.append(state.value)
    return states


def _same_outcome(original: Event, replayed: Event) -> bool:
    match original.payload, replayed.payload:
        case RunCompleted() as a, RunCompleted() as b:
            text_a = a.output.text if a.output is not None else None
            text_b = b.output.text if b.output is not None else None
            return text_a == text_b and a.data == b.data
        case RunFailed() as a, RunFailed() as b:
            return a.error_type == b.error_type
        case _:
            return True


def _end(event: Event | None) -> str:
    return "inachevé" if event is None else event.type.removeprefix("run.")


def _draft(event: Event) -> EventDraft:
    return EventDraft.model_validate(json.loads(event.model_dump_json(exclude={"seq"})))
