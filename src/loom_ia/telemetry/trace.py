# SPDX-License-Identifier: Apache-2.0
"""La trace d'un run, à relire : ses spans, tirés du journal (K5, K6, #29).

Ce sont les spans de l'export (``run_spans``, 6.1a) — mêmes noms, mêmes
attributs ``gen_ai.*`` et ``loom.*`` —, pour le run **et ses sous-runs**, avec
un en-tête qui dit où en est le run et ce qu'il a coûté. Trois différences
avec l'export, qui tiennent à ce qu'on relit au lieu d'envoyer :

- un événement n'est pas aplati en attributs : il porte sa charge **sans son
  contenu** (``data`` — ce que rend ``/events`` sans ``read_content`` : qui a
  approuvé, les notes d'un juge, les tailles…), et son contenu **à part**
  (``content``), champ par champ (``content_fields``), ou ``None`` quand
  l'appelant n'a pas le droit de le voir ;
- **pas de masquage par motifs** : c'est la portée qui décide (décision du
  05/10, 6.2c) — sans ``read_content`` aucun contenu, avec, le contenu en
  clair, comme ``/runs/{id}`` et ``/events`` ; les motifs restent aux exports ;
- un run **inachevé** a sa trace : ses spans encore ouverts le disent
  (``open``), et leur fin est le dernier événement écrit.

Les corps d'un échange brut (``model.exchanged``) n'y sont jamais, comme à
l'export : le journal est leur place (``/events``).
"""

from collections.abc import Sequence
from datetime import datetime
from typing import cast

from pydantic import Field, JsonValue

from loom_ia.core.events import (
    Event,
    RunCancelled,
    RunCompleted,
    RunFailed,
    StepCompleted,
    ToolCalled,
    ToolCompleted,
    contents,
    redacted,
)
from loom_ia.core.model import RunId, RunStatus, SessionId, TenantId, Usage
from loom_ia.core.model.base import DomainModel
from loom_ia.core.projections import fold
from loom_ia.telemetry.spans import AttributeValue, SpanKind, SpanRecord, run_spans


class TraceEvent(DomainModel):
    """Un événement du journal, sur le span où il s'est produit."""

    name: str
    at: datetime
    seq: int
    status: str
    # Sa charge, privée de ses champs de contenu (``redacted``).
    data: dict[str, JsonValue] = Field(default_factory=dict[str, JsonValue])
    # Ses champs de contenu, par chemin ; ``None`` sans le droit de les voir.
    content: dict[str, JsonValue] | None = None


class TraceSpan(DomainModel):
    """Un span : ce qu'il est, quand, ce qu'il a coûté, ce qui s'y est passé."""

    span_id: str
    parent_span_id: str | None
    run_id: RunId
    name: str
    kind: SpanKind
    start: datetime
    end: datetime
    duration_ms: float
    # Pas encore fermé (run, étape ou appel en cours) : ``end`` est provisoire.
    open: bool = False
    # Type de ce qui a échoué, jamais le message.
    error: str | None = None
    attributes: dict[str, AttributeValue] = Field(default_factory=dict[str, AttributeValue])
    events: tuple[TraceEvent, ...] = ()


class Trace(DomainModel):
    """La trace d'un run et de ses sous-runs."""

    run_id: RunId
    session_id: SessionId
    tenant_id: TenantId
    trace_id: str
    agent: str
    status: RunStatus
    finished: bool
    iterations: int
    usage: Usage
    cost_usd: float
    # Temps de pilotage, sans attente ni pause.
    active_ms: float
    output: str | None = None
    error_type: str | None = None
    # Vrai si les événements portent leur contenu.
    content: bool
    spans: tuple[TraceSpan, ...] = ()


def run_trace(events: Sequence[Event], run_id: RunId, *, content: bool) -> Trace:
    """La trace du run ``run_id``, tirée de ses événements et de ceux de ses sous-runs.

    ``events`` sont ceux de l'arbre du run, dans l'ordre du journal
    (``Loom.events``). Les spans sont rendus dans l'ordre où ils s'ouvrent.
    """
    own = [e for e in events if e.run_id == run_id]
    if not own:
        raise ValueError(f"Run {run_id} : aucun événement")
    by_id: dict[str, Event] = {str(e.event_id): e for e in events}
    spans: list[TraceSpan] = []
    for run in dict.fromkeys(e.run_id for e in events):
        members = [e for e in events if e.run_id == run]
        closed = _closed(members)
        spans += [
            _span(record, RunId(run), by_id, closed, content) for record in run_spans(members)
        ]
    spans.sort(key=lambda span: span.start)
    state = fold(own, run_id)
    first = own[0]
    return Trace(
        run_id=run_id,
        session_id=first.session_id,
        tenant_id=first.tenant_id,
        trace_id=first.root_run_id,
        agent=state.agent,
        status=state.status,
        finished=state.finished,
        iterations=state.iterations,
        usage=state.usage,
        cost_usd=state.cost_usd,
        active_ms=state.active_ms,
        output=state.output.text if content and state.output is not None else None,
        error_type=state.error_type,
        content=content,
        spans=tuple(spans),
    )


def _closed(members: Sequence[Event]) -> set[str]:
    """Spans fermés d'un run : run clos, étape conclue, appel rendu."""
    closed: set[str] = set()
    calls: dict[str, str] = {}
    for event in members:
        match event.payload:
            case RunCompleted() | RunFailed() | RunCancelled() | StepCompleted():
                closed.add(event.span_id)
            case ToolCalled(call_id=call_id):
                calls[call_id] = event.span_id
            case ToolCompleted(call_id=call_id):
                closed.add(calls.get(call_id, event.span_id))
            case _:
                pass
    return closed


def _span(
    record: SpanRecord,
    run_id: RunId,
    by_id: dict[str, Event],
    closed: set[str],
    content: bool,
) -> TraceSpan:
    # Un span de modèle ou de juge n'a pas d'événement de clôture : il est fini
    # quand ce qu'il raconte est écrit. Seuls un run, une étape et un appel
    # d'outil restent ouverts.
    opening = record.kind in ("run", "step", "tool")
    return TraceSpan(
        span_id=record.span_id,
        parent_span_id=record.parent_span_id,
        run_id=run_id,
        name=record.name,
        kind=record.kind,
        start=record.start,
        end=record.end,
        duration_ms=(record.end - record.start).total_seconds() * 1000,
        open=opening and record.span_id not in closed,
        error=record.error,
        attributes=dict(record.attributes),
        events=tuple(
            _event(by_id[str(e.attributes["loom.event_id"])], content) for e in record.events
        ),
    )


def _event(source: Event, content: bool) -> TraceEvent:
    shown: dict[str, JsonValue] | None = None
    if content:
        # Les corps bruts ne quittent pas le journal (6.1b).
        shown = dict(contents(source)) if source.payload.export_content else {}
    charge = redacted(source).get("payload")
    return TraceEvent(
        name=source.type,
        at=source.ts,
        seq=source.seq,
        status=source.status,
        data=cast("dict[str, JsonValue]", charge) if isinstance(charge, dict) else {},
        content=shown,
    )
