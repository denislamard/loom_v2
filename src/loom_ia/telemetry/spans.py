# SPDX-License-Identifier: Apache-2.0
"""Les spans d'un run, tirés de son journal (#29, §14.1).

Le journal porte déjà l'arbre : chaque événement a son ``span_id`` et son
``parent_span_id``. Ce module en fait des **spans** — un nom, un début, une
fin, des attributs, les événements qui s'y sont produits — sans rien savoir
d'OpenTelemetry : l'adaptateur ``otel`` ne fait que traduire.

Ce qu'est un span, d'après ce qu'il contient :

- ``run`` : celui de ``run.started`` (``invoke_agent <agent>``) ;
- ``step`` : une étape de la boucle (``step <n>``) ;
- ``tool`` : un appel d'outil, de ``tool.called`` à ``tool.completed``
  (``execute_tool <outil>``) — un rôle délégué est un outil ;
- ``judge`` : le travail d'un juge ;
- ``model`` : un span qui ne porte que des appels de modèle, celui d'un rôle ;
- ``chat`` : **chaque** ``model.responded`` donne un span à lui (``chat
  <modèle>``), enfant du span où il a été écrit, qui dure ce qu'a duré
  l'appel. Son identifiant est celui de l'événement : il ne bouge pas d'un
  export à l'autre.

Le contenu ne part que si ``content`` est vrai, champ par champ
(``content_fields``), sous ``loom.content.<chemin>`` et masqué. Sinon les
attributs sont tous des métadonnées : facettes, usage, coûts, durées.
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Final, Literal

from pydantic import JsonValue

from loom_ia.core.events import (
    Event,
    JudgeEvaluated,
    ModelExchanged,
    ModelFellBack,
    ModelResponded,
    ModelRetried,
    RunCancelled,
    RunCompleted,
    RunFailed,
    RunStarted,
    StepStarted,
    ToolCalled,
    ToolCompleted,
    contents,
)
from loom_ia.core.model import Usage
from loom_ia.telemetry.redaction import Redactor

type AttributeValue = str | bool | int | float
type SpanKind = Literal["run", "step", "tool", "judge", "model", "chat", "other"]

# Préfixe des attributs propres à loom ; les autres suivent les conventions
# GenAI d'OpenTelemetry (``gen_ai.*``), pour qu'un outil qui les connaît s'y
# retrouve.
PREFIX: Final = "loom."
CONTENT_PREFIX: Final = "loom.content."

TERMINAL: Final = (RunCompleted, RunFailed, RunCancelled)


@dataclass(frozen=True, slots=True)
class SpanEvent:
    """Un événement du journal, posé sur le span où il s'est produit."""

    name: str
    at: datetime
    attributes: dict[str, AttributeValue] = field(default_factory=dict[str, AttributeValue])


@dataclass(frozen=True, slots=True)
class SpanRecord:
    """Un span, en termes du journal : identifiants de loom, horodatages réels."""

    trace_id: str
    span_id: str
    parent_span_id: str | None
    name: str
    kind: SpanKind
    start: datetime
    end: datetime
    attributes: dict[str, AttributeValue]
    events: tuple[SpanEvent, ...] = ()
    # Nom de ce qui a échoué (un type d'erreur, un type d'événement), jamais
    # le message : lui est du contenu.
    error: str | None = None


def run_spans(
    events: Sequence[Event], *, content: bool = False, redactor: Redactor | None = None
) -> list[SpanRecord]:
    """Les spans d'un run, dans l'ordre où ils s'ouvrent.

    ``events`` sont ceux d'un seul run, dans l'ordre du journal. Un sous-run
    est un autre run : ses spans viennent de ses propres événements, et son
    span racine a pour parent l'appel qui l'a lancé.
    """
    groups: dict[str, list[Event]] = {}
    for event in events:
        groups.setdefault(event.span_id, []).append(event)
    spans: list[SpanRecord] = []
    for span_id, members in groups.items():
        own = [e for e in members if not isinstance(e.payload, ModelResponded)]
        chats = [e for e in members if isinstance(e.payload, ModelResponded)]
        kind = _kind(members)
        start = min(_started(e) for e in members)
        end = max(e.ts for e in members)
        first = members[0]
        spans.append(
            SpanRecord(
                trace_id=first.root_run_id,
                span_id=span_id,
                parent_span_id=first.parent_span_id,
                name=_name(kind, members),
                kind=kind,
                start=start,
                end=end,
                attributes={**_common(members), **_specific(kind, members)},
                events=tuple(_event(e, content, redactor) for e in own),
                error=_error(members),
            )
        )
        spans.extend(_chat(e, content, redactor) for e in chats)
    spans.sort(key=lambda span: span.start)
    return spans


def finished(event: Event) -> bool:
    """Vrai pour le dernier événement qu'écrit un run : celui qui le clôt."""
    return isinstance(event.payload, TERMINAL)


# --- Interne -----------------------------------------------------------------


def _kind(members: Sequence[Event]) -> SpanKind:
    payloads = [e.payload for e in members]
    if any(isinstance(p, RunStarted) for p in payloads):
        return "run"
    if any(isinstance(p, StepStarted) for p in payloads):
        return "step"
    if any(isinstance(p, ToolCalled | ToolCompleted) for p in payloads):
        return "tool"
    if any(isinstance(p, JudgeEvaluated) for p in payloads) or any(
        isinstance(p, ModelResponded) and p.judge is not None for p in payloads
    ):
        return "judge"
    if all(
        isinstance(p, ModelResponded | ModelRetried | ModelExchanged | ModelFellBack)
        for p in payloads
    ):
        return "model"
    return "other"


def _name(kind: SpanKind, members: Sequence[Event]) -> str:
    first = members[0]
    for event in members:
        payload = event.payload
        match kind, payload:
            case "run", RunStarted():
                return f"invoke_agent {first.agent}"
            case "step", StepStarted(step_no=step_no):
                return f"step {step_no}"
            case "tool", ToolCalled(tool_name=name) | ToolCompleted(tool_name=name):
                return f"execute_tool {name}"
            case "judge", JudgeEvaluated(judge=judge):
                return f"judge {judge}"
            case "judge", ModelResponded(judge=str(judge)):
                return f"judge {judge}"
            case _:
                pass
    if kind == "model":
        return f"role {first.role}" if first.role else "model"
    return first.type


def _started(event: Event) -> datetime:
    """Début de ce qu'un événement raconte : l'appel d'un modèle a duré avant d'être écrit."""
    if isinstance(event.payload, ModelResponded):
        return event.ts - timedelta(milliseconds=event.payload.latency_ms)
    return event.ts


def _common(members: Sequence[Event]) -> dict[str, AttributeValue]:
    first = members[0]
    attributes: dict[str, AttributeValue] = {
        "loom.tenant_id": first.tenant_id,
        "loom.session_id": first.session_id,
        "loom.run_id": first.run_id,
    }
    if first.agent is not None:
        attributes["gen_ai.agent.name"] = first.agent
    role = next((e.role for e in members if e.role is not None), None)
    if role is not None:
        attributes["loom.role"] = role
    return attributes


def _specific(kind: SpanKind, members: Sequence[Event]) -> dict[str, AttributeValue]:
    attributes: dict[str, AttributeValue] = {}
    for event in members:
        match event.payload:
            case RunStarted() as started:
                attributes["gen_ai.operation.name"] = "invoke_agent"
                attributes["loom.run.kind"] = started.kind
                attributes["loom.run.depth"] = started.depth
                if started.parent_run_id is not None:
                    attributes["loom.run.parent_run_id"] = started.parent_run_id
                if started.trigger is not None:
                    attributes["loom.run.trigger"] = started.trigger
            case RunCompleted() | RunFailed() | RunCancelled() as closing:
                attributes["loom.run.status"] = closing.type.removeprefix("run.")
                attributes["loom.iterations"] = closing.iterations
                attributes["loom.cost_usd"] = closing.cost_usd
                attributes.update(_usage(closing.usage))
            case ToolCalled() as called:
                attributes["gen_ai.operation.name"] = "execute_tool"
                attributes["gen_ai.tool.name"] = called.tool_name
                attributes["gen_ai.tool.call.id"] = called.call_id
                attributes["loom.tool.kind"] = called.tool_kind
                if called.child_run_id is not None:
                    attributes["loom.tool.child_run_id"] = called.child_run_id
            case ToolCompleted() as completed:
                attributes["gen_ai.tool.name"] = completed.tool_name
                attributes["loom.latency_ms"] = completed.latency_ms
                attributes["loom.tool.is_error"] = completed.output.is_error
                attributes["loom.tool.size"] = completed.size
            case JudgeEvaluated() as verdict:
                attributes["loom.judge"] = verdict.judge
                attributes["loom.judge.passed"] = verdict.passed
                attributes["loom.judge.blocked"] = verdict.blocked
            case StepStarted() as step:
                attributes["loom.step"] = step.step_no
                attributes["loom.step.effect"] = step.effect
            case _:
                pass
    if kind == "run":
        attributes.setdefault("loom.run.status", "unfinished")
    return attributes


def _usage(usage: Usage) -> dict[str, AttributeValue]:
    return {
        "gen_ai.usage.input_tokens": usage.input_tokens,
        "gen_ai.usage.output_tokens": usage.output_tokens,
        "loom.usage.cache_read_tokens": usage.cache_read_tokens,
        "loom.usage.cache_write_tokens": usage.cache_write_tokens,
        "loom.usage.reasoning_tokens": usage.reasoning_tokens,
    }


def _error(members: Sequence[Event]) -> str | None:
    for event in members:
        if isinstance(event.payload, RunFailed):
            return event.payload.error_type
    for event in members:
        if event.status == "error":
            return event.type
    return None


def _chat(event: Event, content: bool, redactor: Redactor | None) -> SpanRecord:
    payload = event.payload
    assert isinstance(payload, ModelResponded)
    attributes: dict[str, AttributeValue] = {
        **_common([event]),
        "gen_ai.operation.name": "chat",
        "gen_ai.request.model": payload.model_id,
        "gen_ai.response.model": payload.model_id,
        "gen_ai.provider.name": payload.provider,
        "loom.cost_usd": payload.cost_usd,
        "loom.latency_ms": payload.latency_ms,
        "loom.stop_reason": payload.stop_reason,
        "loom.attempts": payload.attempts,
        "loom.request_hash": payload.request_hash,
        **_usage(payload.usage),
    }
    if payload.judge is not None:
        attributes["loom.judge"] = payload.judge
    if payload.call_id is not None:
        attributes["gen_ai.tool.call.id"] = payload.call_id
    return SpanRecord(
        trace_id=event.root_run_id,
        span_id=event.event_id,
        parent_span_id=event.span_id,
        name=f"chat {payload.model_id}",
        kind="chat",
        start=_started(event),
        end=event.ts,
        attributes=attributes,
        events=(_event(event, content, redactor),),
    )


def _event(event: Event, content: bool, redactor: Redactor | None) -> SpanEvent:
    attributes: dict[str, AttributeValue] = {
        "loom.event_id": event.event_id,
        "loom.seq": event.seq,
        "loom.status": event.status,
    }
    for name, value in event.facets.items():
        if value is not None:
            attributes[f"{PREFIX}{name}"] = value
    # Les corps bruts d'un échange ne partent jamais (6.1b) : trop gros pour un
    # attribut, et le journal est leur place. Leurs tailles sont des facettes.
    if content and event.payload.export_content:
        for path, value in contents(event).items():
            attributes[f"{CONTENT_PREFIX}{path}"] = _text(value, redactor)
    return SpanEvent(name=event.type, at=event.ts, attributes=attributes)


def _text(value: JsonValue, redactor: Redactor | None) -> str:
    """Le contenu d'un champ, en texte : une chaîne telle quelle, le reste en JSON."""
    masked = value if redactor is None else redactor.json(value)
    if isinstance(masked, str):
        return masked
    return json.dumps(masked, ensure_ascii=False, sort_keys=True)
