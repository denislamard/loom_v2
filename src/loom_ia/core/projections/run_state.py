# SPDX-License-Identifier: Apache-2.0
"""Projection de l'état d'un run (#3).

``apply`` enregistre des faits : il ne décide rien. Les décisions (appeler
un outil, changer d'état) sont prises par le moteur et arrivent ici sous
forme d'événements, dont ``run.transitioned``.

Une réponse de modèle qui sert un appel d'outil (rôle délégué, C2) n'entre
pas dans la conversation de l'orchestrateur : seuls son usage et son coût
s'ajoutent au run. Un sous-agent (C5) a son propre run ; sa consommation
arrive avec le ``tool.completed`` de l'appel.

Politiques (#2) : ``policy.decided`` enregistre ce qui doit survivre à une
reprise — une réparation décidée mais pas encore demandée (``Retry``), les
arguments remplacés d'un appel, la réponse finale remplacée — et compte les
réparations de chaque politique. Le message de réparation (``message.user``
de ``kind: repair``) entre dans la conversation du run, et sa position est
retenue pour l'exclure de l'historique de session.

Les événements de catégorie ``session`` (J4.1) sont ignorés : ils décrivent
la session, pas le run, et s'écrivent après sa clôture.
"""

from collections.abc import Iterable

from loom_ia.core.events import (
    ArtifactStored,
    BudgetExceeded,
    CircuitOpened,
    Event,
    GuardChecked,
    JudgeEvaluated,
    ModelFellBack,
    ModelResponded,
    ModelRetried,
    PolicyDecided,
    RunCompleted,
    RunFailed,
    RunStarted,
    RunTransitioned,
    SessionSnapshot,
    StepCompleted,
    StepStarted,
    ToolCalled,
    ToolCompleted,
    ToolSourceUnavailable,
    UserMessage,
)
from loom_ia.core.model import (
    Message,
    PendingCall,
    PendingRepair,
    RunId,
    RunState,
    RunStatus,
    TextBlock,
    ToolResultBlock,
    Usage,
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
            kind=payload.kind,
            context=payload.context,
            judges=payload.judges,
            budget=payload.budget,
            last_seq=event.seq,
        )

    if event.run_id != state.run_id:
        raise ProjectionError(f"Événement du run {event.run_id} appliqué au run {state.run_id}")
    if isinstance(payload, SessionSnapshot):
        # Marqueur de session : il parle de la session, pas du run qui l'écrit,
        # et arrive après sa clôture. Seule la position lue avance. Les autres
        # marqueurs (compaction) le rejoindront ici.
        return state.model_copy(update={"last_seq": event.seq})
    if state.finished or (state.status.is_terminal and not _closes(state.status, event)):
        raise ProjectionError(
            f"Run {state.run_id} : {event.type} (seq {event.seq}) après l'état {state.status}"
        )

    update: dict[str, object] = {"last_seq": event.seq}
    match payload:
        case RunStarted():
            raise ProjectionError(f"Run {state.run_id} : run.started en double (seq {event.seq})")
        case UserMessage(message=message, kind="repair", tools=tools):
            update |= {
                "messages": (*state.messages, message),
                "repairs": (*state.repairs, len(state.messages)),
                "pending_repair": None,
                "repair_without_tools": not tools,
                "replaced_output": None,
            }
        case UserMessage(message=message):
            update["messages"] = (*state.messages, message)
        case ModelResponded(judge=str(), usage=usage, cost_usd=cost):
            # Appel d'un juge : son coût compte, pas sa réponse ni une itération.
            update |= _consumed(state, usage, cost)
        case ModelResponded(call_id=str() as call_id, usage=usage, cost_usd=cost):
            _require_pending(state, call_id, event)
            update |= _consumed(state, usage, cost)
        case ModelResponded(message=message, usage=usage, cost_usd=cost):
            update |= _consumed(state, usage, cost) | {
                "messages": (*state.messages, message),
                "iterations": state.iterations + 1,
                "pending_calls": tuple(
                    PendingCall(call_id=c.call_id, name=c.name, arguments=c.arguments)
                    for c in message.tool_calls
                ),
                "repair_without_tools": False,
            }
        case ToolCalled(call_id=call_id, child_run_id=child):
            started: dict[str, object] = {"started": True}
            if child is not None:
                started["child_run_id"] = child
            update["pending_calls"] = tuple(
                c.model_copy(update=started) if c.call_id == call_id else c
                for c in _require_pending(state, call_id, event)
            )
        case ToolCompleted(call_id=call_id, output=output, usage=usage, cost_usd=cost):
            remaining = tuple(
                c for c in _require_pending(state, call_id, event) if c.call_id != call_id
            )
            result = Message(role="tool", blocks=(ToolResultBlock(call_id=call_id, output=output),))
            update |= {"pending_calls": remaining, "messages": (*state.messages, result)}
            if usage is not None:
                update |= {"usage": state.usage + usage, "cost_usd": state.cost_usd + cost}
        case ArtifactStored() as stored:
            if state.artifact(stored.uri) is None:
                update["artifacts"] = (*state.artifacts, stored.record)
        case StepStarted(step_no=step_no):
            update["step"] = step_no
        case PolicyDecided() as decided:
            update |= _decided(state, decided, event)
        case GuardChecked(target="output", resolution="unverified"):
            update["unverified"] = True
        case GuardChecked() | JudgeEvaluated():
            pass
        case BudgetExceeded() as exceeded:
            if exceeded.key not in state.exceeded:
                update["exceeded"] = (*state.exceeded, exceeded.key)
        case ModelFellBack(slot=slot, to_model=to_model):
            update["models"] = {**state.models, slot: to_model}
        case StepCompleted() | ModelRetried() | ToolSourceUnavailable() | CircuitOpened():
            pass
        case RunTransitioned(from_state=from_state, to_state=to_state):
            if from_state != state.status:
                raise ProjectionError(
                    f"Run {state.run_id} : transition depuis {from_state} "
                    f"alors que l'état reconstruit est {state.status} (seq {event.seq})"
                )
            update["status"] = to_state
        case RunCompleted(output=replaced, output_event_id=str()) as completed:
            # Sortie d'un outil terminal, éventuellement remplacée par une politique.
            result = _terminal_result(state, event)
            blocks = result.output.blocks or (TextBlock(text=""),)
            output = Message(role="assistant", blocks=blocks)
            data = completed.data if completed.data is not None else result.output.data
            update |= {
                "status": RunStatus.COMPLETED,
                "output": replaced or output,
                "output_data": data,
                "unverified": completed.unverified or result.output.unverified,
                "terminal_call_id": result.call_id,
                "finished": True,
            }
        case RunCompleted(output=output) as completed:
            update |= {
                "status": RunStatus.COMPLETED,
                "output": output,
                "output_data": completed.data,
                "unverified": completed.unverified or state.unverified,
                "finished": True,
            }
        case RunFailed(error_type=error_type, error=error):
            update |= {
                "status": RunStatus.FAILED,
                "error_type": error_type,
                "error": error,
                "finished": True,
            }
    return state.model_copy(update=update)


def _decided(state: RunState, decided: PolicyDecided, event: Event) -> dict[str, object]:
    """Ce qu'une décision de politique change dans l'état."""
    update: dict[str, object] = {}
    match decided:
        case PolicyDecided(decision="retry", policy=policy) if decided.point in {
            "after_model",
            "on_output",
        }:
            # Réparations de l'orchestrateur ; celles d'un rôle se comptent dans son appel.
            update["retries"] = {**state.retries, policy: state.retries.get(policy, 0) + 1}
            update["pending_repair"] = PendingRepair(
                policy=policy,
                point="on_output" if decided.point == "on_output" else "after_model",
                feedback=decided.reason,
                tools=decided.tools is not False,
            )
        case PolicyDecided(
            decision="replace", point="before_tool", call_id=str() as call_id, arguments=dict()
        ):
            update["pending_calls"] = tuple(
                c.model_copy(update={"replaced_arguments": decided.arguments})
                if c.call_id == call_id
                else c
                for c in _require_pending(state, call_id, event)
            )
        case PolicyDecided(decision="replace", point="on_output", output=Message() as output):
            update["replaced_output"] = output
        case _:
            pass
    return update


def _closes(status: RunStatus, event: Event) -> bool:
    """Vrai pour l'événement de clôture qui suit la transition vers un état final."""
    closing = {RunStatus.COMPLETED: RunCompleted, RunStatus.FAILED: RunFailed}.get(status)
    return closing is not None and isinstance(event.payload, closing)


def _terminal_result(state: RunState, event: Event) -> ToolResultBlock:
    """Résultat de l'outil terminal, dernier message du run (#13)."""
    last = state.messages[-1] if state.messages else None
    results = [b for b in last.blocks if isinstance(b, ToolResultBlock)] if last else []
    if len(results) != 1:
        raise ProjectionError(
            f"Run {state.run_id} : run.completed désigne une sortie d'outil, mais le dernier "
            f"message n'est pas un résultat d'outil unique (seq {event.seq})"
        )
    return results[0]


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
        if event.run_id == run_id and event.category != "session":
            state = apply(state, event)
    if state is None:
        raise ProjectionError(f"Aucun événement pour le run {run_id}")
    return state


def fold_all(events: Iterable[Event]) -> dict[RunId, RunState]:
    """États de tous les runs d'un journal, dans l'ordre de leur démarrage.

    Les marqueurs de session n'y entrent pas : ils décrivent la session, et
    celui qui les écrit peut ne pas être dans la fenêtre relue.
    """
    states: dict[RunId, RunState] = {}
    for event in events:
        if event.category != "session":
            states[event.run_id] = apply(states.get(event.run_id), event)
    return states


def _consumed(state: RunState, usage: Usage, cost: float) -> dict[str, object]:
    """Un appel de modèle du run : usage, coût et compte des appels."""
    return {
        "usage": state.usage + usage,
        "cost_usd": state.cost_usd + cost,
        "model_calls": state.model_calls + 1,
    }
