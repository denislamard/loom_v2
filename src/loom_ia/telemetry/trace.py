# SPDX-License-Identifier: Apache-2.0
"""La trace d'un run, à relire : ses spans, tirés du journal (fonctions K5 et K6, point 29).

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
    """Un événement du journal, sur le span où il s'est produit.

    Attributes:
        name: Type de l'événement.
        at: Horodatage.
        seq: Position dans le journal de la session.
        status: Statut de l'événement (``ok``, ``error``…).
        data: Sa charge, privée de ses champs de contenu (``redacted``).
        content: Ses champs de contenu, par chemin ; ``None`` sans le droit de
            les voir.
    """

    name: str
    at: datetime
    seq: int
    status: str
    data: dict[str, JsonValue] = Field(default_factory=dict[str, JsonValue])
    content: dict[str, JsonValue] | None = None


class TraceSpan(DomainModel):
    """Un span : ce qu'il est, quand, ce qu'il a coûté, ce qui s'y est passé.

    Attributes:
        span_id: Identifiant du span.
        parent_span_id: Span parent.
        run_id: Run du span.
        name: Nom du span.
        kind: Genre du span.
        start: Début.
        end: Fin, provisoire si le span est ouvert.
        duration_ms: Durée, en millisecondes.
        open: Pas encore fermé (run, étape ou appel en cours) : ``end`` est
            provisoire.
        error: Type de ce qui a échoué, jamais le message.
        attributes: Attributs : modèle, tokens, coût, outil…
        events: Les événements du span.
    """

    span_id: str
    parent_span_id: str | None
    run_id: RunId
    name: str
    kind: SpanKind
    start: datetime
    end: datetime
    duration_ms: float
    open: bool = False
    error: str | None = None
    attributes: dict[str, AttributeValue] = Field(default_factory=dict[str, AttributeValue])
    events: tuple[TraceEvent, ...] = ()


class Trace(DomainModel):
    """La trace d'un run et de ses sous-runs.

    Attributes:
        run_id: Le run.
        session_id: Sa session.
        tenant_id: Son client.
        trace_id: Identifiant de la trace : le run racine.
        agent: Agent du run.
        status: Statut du run.
        finished: Vrai si le run est clos.
        iterations: Appels de l'orchestrateur.
        usage: Tokens consommés, sous-runs compris.
        cost_usd: Coût en dollars.
        active_ms: Temps de pilotage, sans attente ni pause.
        output: Réponse finale, si le contenu est montré.
        error_type: Nature de l'échec, s'il a échoué.
        content: Vrai si les événements portent leur contenu (droit
            ``read_content``).
        spans: Les spans du run et de ses sous-runs, dans l'ordre où ils
            s'ouvrent.
    """

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
    active_ms: float
    output: str | None = None
    error_type: str | None = None
    content: bool
    spans: tuple[TraceSpan, ...] = ()


def run_trace(events: Sequence[Event], run_id: RunId, *, content: bool) -> Trace:
    """La trace du run ``run_id``, tirée de ses événements et de ceux de ses sous-runs.

    ``events`` sont ceux de l'arbre du run, dans l'ordre du journal
    (``Loom.events``). Les spans sont rendus dans l'ordre où ils s'ouvrent.

    Args:
        events: Les événements de l'arbre du run.
        run_id: Le run.
        content: Vrai pour joindre le contenu (droit ``read_content``).

    Returns:
        La trace.

    Raises:
        ValueError: Aucun événement de ce run.
    """
    own = [e for e in events if e.run_id == run_id]
    if not own:
        raise ValueError(f"Run {run_id} : aucun événement")
    by_id: dict[str, Event] = {str(e.event_id): e for e in events}
    spans: list[TraceSpan] = []
    for run in dict.fromkeys(e.run_id for e in events):
        members = [e for e in events if e.run_id == run]
        closed = _closed(members)
        spans += [_span(record, RunId(run), by_id, closed, content) for record in run_spans(members)]
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
    """Un span de la trace ; un run, une étape ou un appel d'outil sans clôture reste ouvert."""
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
        events=tuple(_event(by_id[str(e.attributes["loom.event_id"])], content) for e in record.events),
    )


def _event(source: Event, content: bool) -> TraceEvent:
    shown: dict[str, JsonValue] | None = None
    if content:
        # Les corps bruts ne quittent pas le journal (jalon J6.1b).
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
