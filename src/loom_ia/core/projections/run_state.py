# SPDX-License-Identifier: Apache-2.0
"""Projection de l'état d'un run (#3).

``apply`` enregistre des faits : il ne décide rien. Les décisions (appeler
un outil, changer d'état) sont prises par le moteur et arrivent ici sous
forme d'événements, dont ``run.transitioned``.
"""

from collections.abc import Iterable

from loom_ia.core.events import (
    Event,
    ModelResponded,
    RunCompleted,
    RunFailed,
    RunStarted,
    RunTransitioned,
    StepCompleted,
    StepStarted,
    ToolCalled,
    ToolCompleted,
    UserMessage,
)
from loom_ia.core.model import (
    Message,
    PendingCall,
    RunId,
    RunState,
    RunStatus,
    ToolResultBlock,
)


class ProjectionError(Exception):
    """Le journal est incohérent avec l'état reconstruit (divergence)."""


def apply(state: RunState | None, event: Event) -> RunState:
    """État après application d'un événement du même run."""
    payload = event.payload
    if state is None:
        if not isinstance(payload, RunStarted):
            raise ProjectionError(
                f"Run {event.run_id} : {event.type} (seq {event.seq}) avant run.started"
            )
        return RunState(
            run_id=event.run_id,
            session_id=event.session_id,
            root_run_id=event.root_run_id,
            span_id=event.span_id,
            parent_span_id=event.parent_span_id,
            parent_run_id=payload.parent_run_id,
            parent_call_id=payload.parent_call_id,
            depth=payload.depth,
            agent=event.agent or "",
            context=payload.context,
            last_seq=event.seq,
        )

    if event.run_id != state.run_id:
        raise ProjectionError(f"Événement du run {event.run_id} appliqué au run {state.run_id}")
    if state.finished or (state.status.is_terminal and not _closes(state.status, event)):
        raise ProjectionError(
            f"Run {state.run_id} : {event.type} (seq {event.seq}) après l'état {state.status}"
        )

    update: dict[str, object] = {"last_seq": event.seq}
    match payload:
        case RunStarted():
            raise ProjectionError(f"Run {state.run_id} : run.started en double (seq {event.seq})")
        case UserMessage(message=message):
            update["messages"] = (*state.messages, message)
        case ModelResponded(message=message, usage=usage, cost_usd=cost):
            update |= {
                "messages": (*state.messages, message),
                "usage": state.usage + usage,
                "cost_usd": state.cost_usd + cost,
                "iterations": state.iterations + 1,
                "pending_calls": tuple(
                    PendingCall(call_id=c.call_id, name=c.name, arguments=c.arguments)
                    for c in message.tool_calls
                ),
            }
        case ToolCalled(call_id=call_id):
            update["pending_calls"] = tuple(
                c.model_copy(update={"started": True}) if c.call_id == call_id else c
                for c in _require_pending(state, call_id, event)
            )
        case ToolCompleted(call_id=call_id, output=output):
            remaining = tuple(
                c for c in _require_pending(state, call_id, event) if c.call_id != call_id
            )
            result = Message(role="tool", blocks=(ToolResultBlock(call_id=call_id, output=output),))
            update |= {"pending_calls": remaining, "messages": (*state.messages, result)}
        case StepStarted(step_no=step_no):
            update["step"] = step_no
        case StepCompleted():
            pass
        case RunTransitioned(from_state=from_state, to_state=to_state):
            if from_state != state.status:
                raise ProjectionError(
                    f"Run {state.run_id} : transition depuis {from_state} "
                    f"alors que l'état reconstruit est {state.status} (seq {event.seq})"
                )
            update["status"] = to_state
        case RunCompleted(output=output):
            update |= {"status": RunStatus.COMPLETED, "output": output, "finished": True}
        case RunFailed(error_type=error_type, error=error):
            update |= {
                "status": RunStatus.FAILED,
                "error": f"{error_type}: {error}",
                "finished": True,
            }
    return state.model_copy(update=update)


def _closes(status: RunStatus, event: Event) -> bool:
    """Vrai pour l'événement de clôture qui suit la transition vers un état final."""
    closing = {RunStatus.COMPLETED: RunCompleted, RunStatus.FAILED: RunFailed}.get(status)
    return closing is not None and isinstance(event.payload, closing)


def _require_pending(state: RunState, call_id: str, event: Event) -> tuple[PendingCall, ...]:
    if state.pending(call_id) is None:
        raise ProjectionError(
            f"Run {state.run_id} : {event.type} pour l'appel inconnu {call_id!r} (seq {event.seq})"
        )
    return state.pending_calls


def fold(events: Iterable[Event], run_id: RunId) -> RunState:
    """Reconstruit l'état d'un run à partir du journal (autres runs ignorés)."""
    state: RunState | None = None
    for event in events:
        if event.run_id == run_id:
            state = apply(state, event)
    if state is None:
        raise ProjectionError(f"Aucun événement pour le run {run_id}")
    return state


def fold_all(events: Iterable[Event]) -> dict[RunId, RunState]:
    """États de tous les runs d'un journal, dans l'ordre de leur démarrage."""
    states: dict[RunId, RunState] = {}
    for event in events:
        states[event.run_id] = apply(states.get(event.run_id), event)
    return states
