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

State de la session : le résultat réussi d'un outil peut porter des
écritures (``output.state``). Elles sont retirées du résultat que lit le
modèle et rangées dans ``state_writes``, dans l'ordre des appels ;
``session_state`` en tire le state de la session.

Les événements de catégorie ``session`` (J4.1) sont ignorés : ils décrivent
la session, pas le run, et s'écrivent après sa clôture. Une concession
(``run.claimed``) écrite après l'état final est absorbée de même : elle
décrit le pilote, pas le run, et rend sa lecture possible aux journaux de la
2.0.0 qui en portent une.
"""

from collections.abc import Iterable

from pydantic import JsonValue

from loom_ia.core.events import (
    ApprovalExpired,
    ApprovalGranted,
    ApprovalRejected,
    ApprovalRequested,
    ArtifactStored,
    BudgetExceeded,
    CircuitOpened,
    Event,
    GuardChecked,
    IdempotencyRecorded,
    IdempotencyReused,
    JudgeEvaluated,
    ModelExchanged,
    ModelFellBack,
    ModelResponded,
    ModelRetried,
    PolicyDecided,
    RunCancelled,
    RunClaimed,
    RunCompleted,
    RunFailed,
    RunStarted,
    RunTransitioned,
    SessionCompacted,
    SessionSnapshot,
    SessionTrimmed,
    StepCompleted,
    StepStarted,
    ToolCalled,
    ToolCompleted,
    ToolSourceUnavailable,
    UserMessage,
)
from loom_ia.core.model import (
    Amendment,
    ApprovalOutcome,
    Message,
    PendingApproval,
    PendingCall,
    PendingRepair,
    RunClaim,
    RunId,
    RunState,
    RunStatus,
    StateWrite,
    TextBlock,
    ToolCallBlock,
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
            posed_state=payload.state,
            last_seq=event.seq,
        )

    if event.run_id != state.run_id:
        raise ProjectionError(f"Événement du run {event.run_id} appliqué au run {state.run_id}")
    if isinstance(payload, SessionSnapshot | SessionCompacted | SessionTrimmed):
        # Marqueur de session : il parle de la session, pas du run qui l'écrit,
        # et arrive après sa clôture. Seule la position lue avance.
        return state.model_copy(update={"last_seq": event.seq})
    if isinstance(payload, RunClaimed) and (state.finished or state.status.is_terminal):
        # Concession écrite pendant la clôture : le minuteur de renouvellement
        # court tant que le pilote n'est pas sorti, et un journal de la 2.0.0
        # peut porter celle d'une reprise. Elle parle du pilote, pas du run :
        # elle ne rouvre rien et ne rend pas le journal illisible.
        claim = RunClaim(worker_id=payload.worker_id, lease_until=payload.lease_until)
        return state.model_copy(update={"last_seq": event.seq, "claim": claim})
    if isinstance(payload, ApprovalGranted | ApprovalRejected) and (
        state.finished or state.status.is_terminal
    ):
        # Décision écrite après l'état final : le run était déjà fini (annulé pendant que
        # l'approbateur décidait, ou journal d'avant ce contrôle), ou sa clôture reste à
        # écrire (plantage entre les deux écritures). Elle ne rouvre rien et ne rend pas
        # le run illisible ; seule la position lue avance.
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
            update["approvals"] = _launched(state, call_id)
            update["amendments"] = _amended(state, call_id)
        case ToolCompleted(call_id=call_id, output=output, usage=usage, cost_usd=cost):
            remaining = tuple(
                c for c in _require_pending(state, call_id, event) if c.call_id != call_id
            )
            # Le state écrit n'est pas montré au modèle : le résultat qu'il lit
            # reste celui d'un outil qui n'en écrit pas.
            shown = output.model_copy(update={"state": {}}) if output.state else output
            result = Message(role="tool", blocks=(ToolResultBlock(call_id=call_id, output=shown),))
            update |= {"pending_calls": remaining, "messages": (*state.messages, result)}
            if output.state and not output.is_error:
                update["state_writes"] = _written(state, call_id, output.state)
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
        case GuardChecked(target="output", resolution="fallback"):
            update["fallback"] = True
        case GuardChecked() | JudgeEvaluated():
            pass
        case IdempotencyRecorded() | IdempotencyReused():
            # Comptabilité d'un effet déjà appliqué par ailleurs : l'état
            # du run n'en dépend pas. C'est le magasin qui le relit.
            pass
        case BudgetExceeded() as exceeded:
            if exceeded.key not in state.exceeded:
                update["exceeded"] = (*state.exceeded, exceeded.key)
        case ModelFellBack(slot=slot, to_model=to_model):
            update["models"] = {**state.models, slot: to_model}
        case StepCompleted(duration_ms=elapsed):
            # Temps de pilotage cumulé : c'est lui que borne le délai (A6).
            update["active_ms"] = state.active_ms + elapsed
        case ModelRetried() | ModelExchanged() | ToolSourceUnavailable() | CircuitOpened():
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
                "fallback": completed.fallback or result.output.fallback,
                "terminal_call_id": result.call_id,
                "finished": True,
            }
        case RunCompleted(output=output) as completed:
            update |= {
                "status": RunStatus.COMPLETED,
                "output": output,
                "output_data": completed.data,
                "unverified": completed.unverified or state.unverified,
                "fallback": completed.fallback or state.fallback,
                "finished": True,
            }
        case RunFailed(error_type=error_type, error=error):
            update |= {
                "status": RunStatus.FAILED,
                "error_type": error_type,
                "error": error,
                "finished": True,
            }
        case RunClaimed(worker_id=worker, lease_until=until):
            update["claim"] = RunClaim(worker_id=worker, lease_until=until)
        case ApprovalRequested() as asked:
            update["approvals"] = (*state.approvals, asked.as_pending(state.run_id))
        case ApprovalGranted(call_id=call_id) as granted:
            corriges = granted.arguments
            if corriges is not None:
                # L'approbateur a corrigé l'appel : la conversation doit dire ce
                # qui part, sans quoi le résultat contredira l'appel du modèle.
                update["messages"] = _restated(state.messages, call_id, corriges)
            update["approvals"] = _settled(
                state,
                call_id,
                event,
                ApprovalOutcome(
                    verdict="granted",
                    by=granted.by,
                    reason=granted.reason,
                    arguments=corriges,
                ),
            )
        case ApprovalRejected(call_id=call_id) as refused:
            update["approvals"] = _settled(
                state,
                call_id,
                event,
                ApprovalOutcome(verdict="rejected", by=refused.by, reason=refused.reason),
            )
        case ApprovalExpired(call_id=call_id, expire_at=expire_at):
            update["approvals"] = _settled(
                state,
                call_id,
                event,
                ApprovalOutcome(
                    verdict="expired",
                    reason=f"sans réponse avant {expire_at.isoformat(timespec='seconds')}",
                ),
            )
        case RunCancelled(reason=reason):
            update |= {
                "status": RunStatus.CANCELLED,
                "cancelled": reason,
                "finished": True,
            }
    return state.model_copy(update=update)


def _restated(
    messages: tuple[Message, ...], call_id: str, arguments: dict[str, JsonValue]
) -> tuple[Message, ...]:
    """Réécrit un appel de la conversation avec les arguments qui vont vraiment partir.

    Le modèle doit lire ce qui a eu lieu, et non ce qu'il avait demandé :
    sinon le résultat de l'outil contredit son propre appel, et il en conclut
    à une panne. Vu au run réel du 21/09 — l'approbateur avait corrigé le
    destinataire d'un e-mail, et MiniMax, voyant partir une adresse qu'il
    n'avait pas écrite, a proposé de renvoyer l'e-mail déjà parti.

    Le journal ne bouge pas : ``tool.called`` garde les arguments du modèle,
    et la décision qui les a corrigés dit qui a voulu quoi.
    """
    return tuple(
        message.model_copy(update={"blocks": blocks})
        if (
            blocks := tuple(
                block.model_copy(update={"arguments": arguments})
                if isinstance(block, ToolCallBlock) and block.call_id == call_id
                else block
                for block in message.blocks
            )
        )
        != message.blocks
        else message
        for message in messages
    )


def _settled(
    state: RunState, call_id: str, event: Event, outcome: ApprovalOutcome
) -> tuple[PendingApproval, ...]:
    """Demande refermée par sa décision ; les autres sont laissées telles quelles.

    Une décision qui ne désigne aucune demande est une incohérence du journal,
    pas un cas à absorber : deux décisions pour un même appel s'annuleraient
    en silence.
    """
    asked = state.approval(call_id)
    if asked is None:
        raise ProjectionError(
            f"Run {state.run_id} : {event.type} pour l'appel {call_id!r}, "
            f"sans demande d'approbation (seq {event.seq})"
        )
    if asked.outcome is not None:
        raise ProjectionError(
            f"Run {state.run_id} : {event.type} pour l'appel {call_id!r}, "
            f"déjà tranché ({asked.outcome.verdict}) (seq {event.seq})"
        )
    return tuple(
        a.model_copy(update={"outcome": outcome}) if a is asked else a for a in state.approvals
    )


def _launched(state: RunState, call_id: str) -> tuple[PendingApproval, ...]:
    """Accords de cet appel marqués comme ayant servi : l'appel part sur eux.

    Un accord vaut pour **un** lancement. Si le run plante ensuite, l'effet de
    ce lancement est d'état inconnu, et l'accord ne couvre pas un second : la
    reprise le lit à ce marquage, sans que le journal ait rien de plus à dire.
    """
    return tuple(
        a.model_copy(update={"launched": True})
        if a.call_id == call_id and not a.launched and _granted(a)
        else a
        for a in state.approvals
    )


def _amended(state: RunState, call_id: str) -> tuple[Amendment, ...]:
    """Ce qui a changé les arguments de cet appel avant son lancement, s'il y a lieu.

    Une politique ``before_tool`` passe d'abord, l'approbateur ensuite : chacun
    est comparé à ce qu'il a reçu. Un appel relancé après une interruption
    remplace sa trace au lieu de la doubler.
    """
    kept = tuple(a for a in state.amendments if a.call_id != call_id)
    call = state.pending(call_id)
    found: list[Amendment] = []
    if call is not None and call.replaced_arguments is not None:
        fields = _changed(call.arguments, call.replaced_arguments)
        if fields:
            found.append(
                Amendment(
                    call_id=call_id,
                    origin="policy",
                    fields=fields,
                    by=", ".join(dict.fromkeys(call.replaced_by)) or None,
                    reason=" ; ".join(call.replaced_reasons),
                )
            )
    asked = state.approval(call_id)
    outcome = asked.outcome if asked is not None else None
    if asked is not None and outcome is not None and outcome.arguments is not None:
        fields = _changed(asked.arguments, outcome.arguments)
        if fields:
            found.append(
                Amendment(
                    call_id=call_id,
                    origin="approval",
                    fields=fields,
                    by=outcome.by,
                    reason=outcome.reason,
                )
            )
    return (*kept, *found)


def _changed(before: dict[str, JsonValue], after: dict[str, JsonValue]) -> tuple[str, ...]:
    """Champs dont la valeur diffère : ceux d'après dans leur ordre, puis ceux retirés."""
    changed = [name for name, value in after.items() if before.get(name, _ABSENT) != value]
    return (*changed, *(name for name in before if name not in after))


_ABSENT: object = object()


def _granted(approval: PendingApproval) -> bool:
    return approval.outcome is not None and approval.outcome.verdict == "granted"


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
            decision="replace",
            point="before_tool",
            call_id=str() as call_id,
            arguments=dict() as remplaces,
        ):
            update["pending_calls"] = tuple(
                c.model_copy(
                    update={
                        "replaced_arguments": remplaces,
                        "replaced_by": (*c.replaced_by, decided.policy),
                        "replaced_reasons": (
                            (*c.replaced_reasons, decided.reason)
                            if decided.reason
                            else c.replaced_reasons
                        ),
                    }
                )
                if c.call_id == call_id
                else c
                for c in _require_pending(state, call_id, event)
            )
            # Comme pour une approbation : le modèle lit l'appel qui part.
            update["messages"] = _restated(state.messages, call_id, remplaces)
        case PolicyDecided(decision="replace", point="on_output", output=Message() as output):
            update["replaced_output"] = output
        case _:
            pass
    return update


def _closes(status: RunStatus, event: Event) -> bool:
    """Vrai pour l'événement de clôture qui suit la transition vers un état final."""
    closing = {
        RunStatus.COMPLETED: RunCompleted,
        RunStatus.FAILED: RunFailed,
        RunStatus.CANCELLED: RunCancelled,
    }.get(status)
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


def _written(state: RunState, call_id: str, values: dict[str, JsonValue]) -> tuple[StateWrite, ...]:
    """Écritures du run, celles de cet appel rangées à leur place dans l'ordre des appels.

    Les résultats d'un lot arrivent dans l'ordre où les outils finissent : il
    change d'une exécution à l'autre, et au rejeu, où ils sont instantanés.
    L'ordre des appels ne dépend que de la réponse du modèle, comme celui des
    résultats dans une requête (``in_call_order``) : c'est lui qui départage
    deux écritures d'une même clé (D9).
    """
    turn, rank = len(state.messages), 0
    for position in range(len(state.messages) - 1, -1, -1):
        calls = [c.call_id for c in state.messages[position].tool_calls]
        if call_id in calls:
            turn, rank = position, calls.index(call_id)
            break
    write = StateWrite(call_id=call_id, turn=turn, rank=rank, values=values)
    return tuple(sorted((*state.state_writes, write), key=lambda w: (w.turn, w.rank)))


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


def awaiting_in_session(events: Iterable[Event]) -> tuple[PendingApproval, ...]:
    """Demandes d'approbation sans réponse des runs racine encore ouverts d'un journal.

    Celles du run racine et celles de ses sous-runs (un sous-agent en pause
    arrête son parent), dans l'ordre du journal. Un run racine fini n'attend
    plus rien, même si un sous-run est resté en pause (journaux d'avant leur
    fermeture avec le parent) ; un run de compaction n'entre pas en compte.
    """
    states = fold_all(events)

    def root(state: RunState) -> RunState:
        while state.parent_run_id is not None and state.parent_run_id in states:
            state = states[state.parent_run_id]
        return state

    return tuple(
        approval
        for state in states.values()
        if state.awaiting
        and (top := root(state)).kind == "normal"
        and not (top.finished or top.status.is_terminal)
        for approval in state.awaiting
    )


def _consumed(state: RunState, usage: Usage, cost: float) -> dict[str, object]:
    """Un appel de modèle du run : usage, coût et compte des appels."""
    return {
        "usage": state.usage + usage,
        "cost_usd": state.cost_usd + cost,
        "model_calls": state.model_calls + 1,
    }
