# SPDX-License-Identifier: Apache-2.0
"""Boucle d'exécution d'un run : ``step`` et ``drive`` (#3, A2, A4).

``step`` fait une seule chose, déduite de l'état reconstruit :

- **décider**, quand l'effet de l'état courant est déjà dans le journal :
  transition vers l'état suivant, ou clôture du run ;
- **sinon exécuter l'effet** de l'état, encadré par ``step.started`` et
  ``step.completed`` : appel du modèle (``READY_FOR_MODEL``), lot d'outils
  (``AWAITING_TOOLS``) ou réponse forcée sans outils (``FINALIZING``).

``drive`` écrit chaque événement dès qu'il est produit, l'applique à l'état,
et recommence tant que le run peut avancer. La reprise après un plantage est
donc le cas normal : l'état relu dit si l'effet a eu lieu, et un appel
d'outil commencé mais non terminé est traité par l'exécuteur (#18).

Plafond d'itérations : au retour des outils, si le modèle a été appelé
``max_iterations`` fois, le run passe en ``FINALIZING`` ; le dépassement est
borné à cette dernière génération.

Outil terminal (#13) : seul dans son tour et sans erreur, sa sortie devient
la réponse finale. ``run.completed`` désigne alors son ``tool.completed``
au lieu de recopier la sortie. Appelé avec d'autres outils, il redevient un
outil ordinaire et l'orchestrateur compose la réponse.

Rôles délégués : si l'agent en a, chaque résultat montré au modèle porte sa
référence (``[result:3]``) et le prompt système explique ``$ref`` (#12).
"""

import logging
import time
from collections.abc import AsyncGenerator, Mapping, Sequence
from contextlib import aclosing
from dataclasses import dataclass, field
from typing import Final

from pydantic import JsonValue

from loom_ia.core.events import (
    Effect,
    Event,
    EventDraft,
    ModelResponded,
    ModelRetried,
    RunCompleted,
    RunFailed,
    RunScope,
    RunStarted,
    RunTransitioned,
    StepCompleted,
    StepStarted,
    ToolCompleted,
    UserMessage,
)
from loom_ia.core.model import (
    DEFAULT_TENANT,
    MAIN_ROLE,
    CallerContext,
    Message,
    ModelRequest,
    ModelResponse,
    ModelSpec,
    RunId,
    RunState,
    RunStatus,
    SessionId,
    SpanId,
    TenantId,
    TextBlock,
    ToolCallBlock,
    ToolResultBlock,
    new_run_id,
    new_span_id,
)
from loom_ia.core.ports import ChunkCallback, EventStore, ModelClient, ModelError
from loom_ia.core.projections import apply, fold, history
from loom_ia.engine.executor import Delegated, ToolExecutor
from loom_ia.engine.model_call import ModelCall, responded
from loom_ia.engine.refs import REFS_HINT, ResultIndex, mark_results

logger = logging.getLogger(__name__)

DEFAULT_MAX_ITERATIONS: Final = 10

# États où ``step`` a quelque chose à faire (effet ou clôture).
_ACTIONABLE: Final = frozenset(
    {
        RunStatus.READY_FOR_MODEL,
        RunStatus.AWAITING_TOOLS,
        RunStatus.FINALIZING,
        RunStatus.COMPLETED,
        RunStatus.FAILED,
    }
)


@dataclass(frozen=True, kw_only=True)
class RunContext:
    """Dépendances et réglages d'un agent ; rien de ce qui est ici n'est journalisé."""

    agent: str
    store: EventStore
    model: ModelClient
    model_spec: ModelSpec
    tools: ToolExecutor = field(default_factory=ToolExecutor)
    system: str = ""
    max_iterations: int = DEFAULT_MAX_ITERATIONS
    # Réglages du rôle ``main`` (B6) : ``max_tokens`` remplace celui du modèle,
    # ``params`` surcharge ``model_spec.params`` clé par clé.
    max_tokens: int | None = None
    params: Mapping[str, JsonValue] = field(default_factory=dict[str, JsonValue])
    # Reçoit les morceaux du flux du modèle, pour la diffusion en direct.
    on_chunk: ChunkCallback | None = None


async def begin_run(
    ctx: RunContext,
    prompt: str | Message,
    *,
    session_id: SessionId | None = None,
    context: CallerContext | None = None,
    run_id: RunId | None = None,
) -> RunState:
    """Écrit le démarrage d'un run et la demande de l'utilisateur.

    Un ``run_id`` fourni par l'appelant ne doit pas déjà exister dans le journal.
    """
    message = Message.user(prompt) if isinstance(prompt, str) else prompt
    if message.role != "user":
        raise ValueError(f"La demande doit être un message 'user', pas {message.role!r}")
    context = context or CallerContext()
    chosen = run_id is not None
    run_id = run_id or new_run_id()
    scope = RunScope(
        tenant_id=context.tenant_id,
        session_id=session_id or SessionId(run_id),
        run_id=run_id,
        root_run_id=run_id,
        agent=ctx.agent,
    )
    if chosen and await ctx.store.read(scope.tenant_id, scope.session_id, run_id=run_id):
        raise ValueError(f"Le run {run_id} existe déjà")
    last = await ctx.store.last_seq(scope.tenant_id, scope.session_id)
    events = await ctx.store.append(
        [scope.draft(RunStarted(context=context)), scope.draft(UserMessage(message=message))],
        expected_seq=last,
    )
    return fold(events, run_id)


async def drive(
    ctx: RunContext,
    run_id: RunId,
    *,
    session_id: SessionId | None = None,
    tenant_id: TenantId | None = None,
) -> RunState:
    """Fait avancer le run jusqu'à un état où il ne peut plus avancer seul.

    Sans ``session_id``, le run est son propre journal (``session_id = run_id``).
    """
    tenant = tenant_id or DEFAULT_TENANT
    session = session_id or SessionId(run_id)
    events = await ctx.store.read(tenant, session)
    own = [e for e in events if e.run_id == run_id]
    state = fold(own, run_id)
    if state.agent != ctx.agent:
        raise ValueError(f"Le run {run_id} appartient à l'agent {state.agent!r}, pas {ctx.agent!r}")
    previous = history(e for e in events if e.seq < own[0].seq)
    cause = next((e for e in reversed(own) if e.category in {"model", "tool"}), None)
    last_seq = events[-1].seq

    while not state.finished and state.status in _ACTIONABLE:
        emitted = 0
        async with aclosing(step(state, ctx, previous, cause=cause)) as drafts:
            async for draft in drafts:
                [event] = await ctx.store.append([draft], expected_seq=last_seq)
                last_seq = event.seq
                emitted += 1
                if event.category in {"model", "tool"}:
                    cause = event
                if isinstance(event.payload, RunTransitioned):
                    _log_transition(event, event.payload)
                state = apply(state, event)
        if emitted == 0:
            raise RuntimeError(f"Run {run_id} : aucune progression depuis l'état {state.status}")
    return state


async def step(
    state: RunState,
    ctx: RunContext,
    previous: Sequence[Message] = (),
    *,
    cause: Event | None = None,
) -> AsyncGenerator[EventDraft]:
    """Événements de la prochaine étape du run.

    ``previous`` est l'historique de la session avant ce run ; ``cause`` le
    dernier événement d'effet, référencé par les transitions.
    """
    scope = _scope(state)
    decision = _decide(state, ctx, scope, cause)
    if decision is not None:
        for draft in decision:
            yield draft
        return
    match state.status:
        case RunStatus.READY_FOR_MODEL:
            effect = _model_step(state, ctx, scope, previous, forced=False)
        case RunStatus.FINALIZING:
            effect = _model_step(state, ctx, scope, previous, forced=True)
        case RunStatus.AWAITING_TOOLS:
            effect = _tool_step(state, ctx, scope)
        case _:
            return
    async with aclosing(effect) as drafts:
        async for draft in drafts:
            yield draft


# --- Décisions ---------------------------------------------------------------


def _decide(
    state: RunState, ctx: RunContext, scope: RunScope, cause: Event | None
) -> list[EventDraft] | None:
    """Transition ou clôture dues à l'état courant, ou None s'il reste un effet à exécuter."""
    last = state.messages[-1] if state.messages else None
    answered = last is not None and last.role == "assistant"
    match state.status:
        case RunStatus.COMPLETED if not state.finished and last is not None and last.role == "tool":
            # Seul un outil terminal mène à COMPLETED sur un résultat d'outil.
            return [scope.draft(_completed(state, None, terminal=_terminal_event(state, cause)))]
        case RunStatus.COMPLETED if not state.finished:
            return [scope.draft(_completed(state, last if answered else None))]
        case RunStatus.FAILED if not state.finished:
            return [
                scope.draft(
                    _failed(state, "Interrupted", "run interrompu après son passage en échec")
                )
            ]
        case RunStatus.READY_FOR_MODEL if answered and state.pending_calls:
            return [_transition(state, scope, RunStatus.AWAITING_TOOLS, cause)]
        case RunStatus.READY_FOR_MODEL | RunStatus.FINALIZING if answered:
            return [
                _transition(state, scope, RunStatus.COMPLETED, cause),
                scope.draft(_completed(state, last)),
            ]
        case RunStatus.AWAITING_TOOLS if not state.pending_calls and _is_terminal(state, ctx):
            return [
                _transition(state, scope, RunStatus.COMPLETED, cause),
                scope.draft(_completed(state, None, terminal=_terminal_event(state, cause))),
            ]
        case RunStatus.AWAITING_TOOLS if not state.pending_calls:
            limit_reached = state.iterations >= ctx.max_iterations
            target = RunStatus.FINALIZING if limit_reached else RunStatus.READY_FOR_MODEL
            return [_transition(state, scope, target, cause)]
        case _:
            return None


def _transition(
    state: RunState, scope: RunScope, target: RunStatus, cause: Event | str | None
) -> EventDraft:
    if isinstance(cause, Event):
        cause_type, cause_id = cause.type, cause.event_id
    else:
        cause_type, cause_id = cause, None
    return scope.draft(
        RunTransitioned(
            from_state=state.status,
            to_state=target,
            step_no=state.step,
            cause_type=cause_type,
            cause_event_id=cause_id,
        )
    )


def _is_terminal(state: RunState, ctx: RunContext) -> bool:
    """Vrai si le lot qui vient de finir fait de sa sortie la réponse finale (#13)."""
    turn = next((m for m in reversed(state.messages) if m.role == "assistant"), None)
    calls = turn.tool_calls if turn is not None else ()
    terminal = [c for c in calls if (tool := ctx.tools.get(c.name)) and tool.spec.terminal]
    if not terminal:
        return False
    if len(calls) > 1:
        logger.warning(
            "Outil terminal %s appelé avec d'autres outils : sa sortie revient à l'orchestrateur",
            ", ".join(c.name for c in terminal),
            extra={"run_id": state.run_id},
        )
        return False
    last = state.messages[-1]
    return all(
        isinstance(block, ToolResultBlock) and not block.output.is_error for block in last.blocks
    )


def _terminal_event(state: RunState, cause: Event | None) -> Event:
    """``tool.completed`` de l'outil terminal : c'est le dernier effet du run."""
    if cause is None or not isinstance(cause.payload, ToolCompleted):
        raise RuntimeError(f"Run {state.run_id} : sortie terminale sans tool.completed")
    return cause


def _completed(
    state: RunState, output: Message | None, *, terminal: Event | None = None
) -> RunCompleted:
    return RunCompleted(
        output=output,
        output_event_id=terminal.event_id if terminal is not None else None,
        iterations=state.iterations,
        usage=state.usage,
        cost_usd=state.cost_usd,
    )


def _failed(state: RunState, error_type: str, error: str) -> RunFailed:
    return RunFailed(
        error_type=error_type,
        error=error,
        iterations=state.iterations,
        usage=state.usage,
        cost_usd=state.cost_usd,
    )


# --- Effets ------------------------------------------------------------------


class _Step:
    """Encadrement d'une étape : numéro, span et chronométrage."""

    def __init__(self, state: RunState, scope: RunScope, effect: Effect) -> None:
        self.no = state.step + 1
        self.span: SpanId = new_span_id()
        self._scope = scope
        self._status = state.status
        self._effect: Effect = effect
        self._started = time.perf_counter()

    def draft(
        self, payload: StepStarted | StepCompleted | ModelResponded | ModelRetried
    ) -> EventDraft:
        # Les appels de modèle de l'orchestrateur sont ceux du rôle ``main`` (C6).
        role = MAIN_ROLE if isinstance(payload, ModelResponded | ModelRetried) else None
        return self._scope.draft(payload, span_id=self.span, role=role)

    def started(self) -> EventDraft:
        return self.draft(StepStarted(step_no=self.no, state=self._status, effect=self._effect))

    def completed(self, emitted: int, *, ok: bool = True) -> EventDraft:
        return self.draft(
            StepCompleted(
                step_no=self.no,
                duration_ms=(time.perf_counter() - self._started) * 1000,
                events_emitted=emitted,
                outcome="ok" if ok else "error",
            )
        )


async def _model_step(
    state: RunState,
    ctx: RunContext,
    scope: RunScope,
    previous: Sequence[Message],
    *,
    forced: bool,
) -> AsyncGenerator[EventDraft]:
    current = _Step(state, scope, "finalize" if forced else "model_call")
    yield current.started()
    spec = ctx.model_spec
    started = time.perf_counter()
    emitted = attempts = 0
    response: ModelResponse | None = None
    system, messages = ctx.system, state.messages
    if ctx.tools.has_delegated:
        messages = mark_results(messages, ResultIndex(messages))
        system = f"{system}\n\n{REFS_HINT}" if system else REFS_HINT
    try:
        request = ModelRequest(
            model_id=spec.model,
            system=system,
            messages=(*previous, *messages),
            tools=ctx.tools.definitions(),
            tool_choice="none" if forced else "auto",
            max_tokens=ctx.max_tokens or spec.max_tokens,
            params={**spec.params, **ctx.params},
        )
        call = ModelCall(ctx.model, spec, on_chunk=ctx.on_chunk)
        async with aclosing(call.run(request)) as outcomes:
            async for outcome in outcomes:
                attempts += 1
                if isinstance(outcome, ModelResponse):
                    response = outcome
                else:
                    yield current.draft(outcome)
                    emitted += 1
        if response is None:
            raise RuntimeError("Appel de modèle terminé sans réponse")
    except Exception as exc:
        failure = f"model.{exc.kind}" if isinstance(exc, ModelError) else type(exc).__name__
        logger.error(
            "Échec de l'appel au modèle %s",
            spec.id,
            exc_info=exc,
            extra={"run_id": state.run_id, "span_id": current.span},
        )
        yield current.completed(emitted, ok=False)
        yield _transition(state, scope, RunStatus.FAILED, failure)
        yield scope.draft(_failed(state, failure, str(exc)))
        return
    message = response.message
    if forced and message.tool_calls:
        logger.warning(
            "Réponse forcée avec appels d'outils : appels ignorés",
            extra={"run_id": state.run_id, "span_id": current.span},
        )
        kept = tuple(b for b in message.blocks if not isinstance(b, ToolCallBlock))
        message = message.model_copy(update={"blocks": kept or (TextBlock(text=""),)})
    yield current.draft(
        responded(
            request,
            response,
            spec,
            attempts=attempts,
            latency_ms=(time.perf_counter() - started) * 1000,
            message=message,
        )
    )
    yield current.completed(emitted + 1)


async def _tool_step(
    state: RunState, ctx: RunContext, scope: RunScope
) -> AsyncGenerator[EventDraft]:
    current = _Step(state, scope, "tool_batch")
    yield current.started()
    # Un span par appel d'outil ; les appels de modèle d'un rôle ont le leur, en dessous.
    spans: dict[str, SpanId] = {}
    model_spans: dict[str, SpanId] = {}
    emitted = 0
    async with aclosing(ctx.tools.run_batch(state)) as events:
        async for item in events:
            span = spans.setdefault(item.call_id, new_span_id())
            if isinstance(item, Delegated):
                inner = model_spans.setdefault(item.call_id, new_span_id())
                yield scope.draft(item.payload, span_id=inner, parent_span_id=span, role=item.role)
            else:
                yield scope.draft(item, span_id=span, parent_span_id=current.span)
            emitted += 1
    yield current.completed(emitted)


# --- Interne -----------------------------------------------------------------


def _scope(state: RunState) -> RunScope:
    return RunScope(
        tenant_id=state.context.tenant_id,
        session_id=state.session_id,
        run_id=state.run_id,
        root_run_id=state.root_run_id,
        agent=state.agent,
        span_id=state.span_id,
        parent_span_id=state.parent_span_id,
    )


def _log_transition(event: Event, payload: RunTransitioned) -> None:
    logger.info(
        "Transition %s → %s (agent %s)",
        payload.from_state,
        payload.to_state,
        event.agent,
        extra={"run_id": event.run_id, "span_id": event.span_id, "tenant_id": event.tenant_id},
    )
