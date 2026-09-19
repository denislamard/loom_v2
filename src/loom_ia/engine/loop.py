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

Rôles et sous-agents : si l'un d'eux est proposé dans le run, chaque résultat
montré au modèle porte sa référence (``[result:3]``) et le prompt système
explique ``$ref`` (#12). Dans la requête, les résultats d'un tour suivent
l'ordre des appels, quel que soit leur ordre d'arrivée dans le journal.

Pièces jointes (G1) : validées avant tout écrit (signature binaire, type,
taille), rangées dans le stockage d'artefacts, annoncées par un
``artifact.stored`` chacune, puis jointes au message de l'utilisateur en
références. Chaque appel de modèle les résout selon ses capacités (#14).
"""

import logging
import time
from collections.abc import AsyncGenerator, Mapping, Sequence
from contextlib import aclosing
from dataclasses import dataclass, field, replace
from typing import Final

from pydantic import JsonValue

from loom_ia.core.events import (
    ArtifactStored,
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
    ToolSourceUnavailable,
    UserMessage,
)
from loom_ia.core.model import (
    DEFAULT_TENANT,
    MAIN_ROLE,
    ArtifactRefBlock,
    Attachment,
    AttachmentPolicy,
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
    artifact_uri,
    new_run_id,
    new_span_id,
)
from loom_ia.core.ports import (
    ArtifactStore,
    ChunkCallback,
    EventStore,
    ModelClient,
    ModelError,
    SourceContext,
)
from loom_ia.core.projections import apply, fold, history
from loom_ia.engine.executor import Delegated, Stored, ToolExecutor
from loom_ia.engine.model_call import ModelCall, responded
from loom_ia.engine.refs import REFS_HINT, ResultIndex, in_call_order, mark_results
from loom_ia.engine.writer import SessionWriter

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
    # Pièces jointes acceptées à l'entrée d'un run (G1).
    attachments: AttachmentPolicy = field(default_factory=AttachmentPolicy)
    # Écrivain du journal, posé par ``drive`` pour le run en cours : les
    # sous-agents écrivent leur run avec lui.
    writer: SessionWriter | None = None

    @property
    def artifacts(self) -> ArtifactStore | None:
        """Stockage des fichiers du run : celui de l'exécuteur d'outils."""
        return self.tools.artifacts


@dataclass(frozen=True, slots=True, kw_only=True)
class ParentRun:
    """Run parent d'un sous-run, et l'appel qui l'a lancé (#4)."""

    run_id: RunId
    root_run_id: RunId
    call_id: str
    # Profondeur du parent ; l'enfant est à ``depth + 1``.
    depth: int
    # Span de l'appel dans le parent : parent du span racine de l'enfant.
    span_id: SpanId | None = None


async def begin_run(
    ctx: RunContext,
    prompt: str | Message,
    *,
    attachments: Sequence[Attachment] = (),
    session_id: SessionId | None = None,
    context: CallerContext | None = None,
    run_id: RunId | None = None,
    parent: ParentRun | None = None,
    writer: SessionWriter | None = None,
) -> RunState:
    """Écrit le démarrage d'un run et la demande de l'utilisateur.

    Un ``run_id`` fourni par l'appelant ne doit pas déjà exister dans le journal.
    Une pièce jointe refusée lève ``AttachmentError`` avant tout écrit.

    Un sous-run (``parent``) s'écrit dans le journal de son parent, avec
    l'écrivain de la session (``writer``) : il hérite de sa racine, et sa
    profondeur est celle du parent plus un.
    """
    message = Message.user(prompt) if isinstance(prompt, str) else prompt
    if message.role != "user":
        raise ValueError(f"La demande doit être un message 'user', pas {message.role!r}")
    checked = [(attachment, attachment.checked(ctx.attachments)) for attachment in attachments]
    store = ctx.artifacts
    if checked and store is None:
        raise ValueError("Pièces jointes refusées : aucun stockage d'artefacts n'est configuré")
    context = context or CallerContext()
    chosen = run_id is not None
    run_id = run_id or new_run_id()
    scope = RunScope(
        tenant_id=context.tenant_id,
        session_id=session_id or SessionId(run_id),
        run_id=run_id,
        root_run_id=parent.root_run_id if parent is not None else run_id,
        agent=ctx.agent,
        parent_span_id=parent.span_id if parent is not None else None,
    )
    started = RunStarted(context=context)
    if parent is not None:
        started = RunStarted(
            context=context,
            parent_run_id=parent.run_id,
            parent_call_id=parent.call_id,
            depth=parent.depth + 1,
        )
    if chosen and await ctx.store.read(scope.tenant_id, scope.session_id, run_id=run_id):
        raise ValueError(f"Le run {run_id} existe déjà")
    stored: list[ArtifactStored] = []
    if store is not None:
        stored = [await _store_attachment(store, scope, *item) for item in checked]
    if stored:
        files = tuple(
            ArtifactRefBlock(uri=s.uri, media_type=s.media_type, size=s.size, name=s.name)
            for s in stored
        )
        message = message.model_copy(update={"blocks": (*message.blocks, *files)})
    if writer is None:
        writer = await SessionWriter.open(ctx.store, scope.tenant_id, scope.session_id)
    events = await writer.append(
        [
            scope.draft(started),
            *(scope.draft(payload) for payload in stored),
            scope.draft(UserMessage(message=message)),
        ]
    )
    return fold(events, run_id)


async def _store_attachment(
    store: ArtifactStore, scope: RunScope, attachment: Attachment, media_type: str
) -> ArtifactStored:
    """Range une pièce jointe validée ; son ``artifact.stored`` précède le message."""
    uri = artifact_uri(scope.tenant_id, scope.session_id, attachment.data, media_type)
    await store.put(uri, attachment.data)
    return ArtifactStored(
        uri=uri,
        media_type=media_type,
        size=len(attachment.data),
        name=attachment.name,
        origin="attachment",
    )


async def drive(
    ctx: RunContext,
    run_id: RunId,
    *,
    session_id: SessionId | None = None,
    tenant_id: TenantId | None = None,
    writer: SessionWriter | None = None,
) -> RunState:
    """Fait avancer le run jusqu'à un état où il ne peut plus avancer seul.

    Sans ``session_id``, le run est son propre journal (``session_id = run_id``).

    Les sources d'outils de l'agent (serveurs MCP) sont ouvertes au début et
    refermées à la fin : leurs outils restent fixes pendant tout le ``drive``.
    Une source injoignable est journalisée (``tool.source_unavailable``) ; si
    le run ne peut pas s'en passer, il échoue.

    Un sous-run reçoit l'écrivain de son parent (``writer``) : ils écrivent
    dans le même journal. Il n'a pas d'historique de session.
    """
    tenant = tenant_id or DEFAULT_TENANT
    session = session_id or SessionId(run_id)
    events = await ctx.store.read(tenant, session)
    own = [e for e in events if e.run_id == run_id]
    state = fold(own, run_id)
    if state.agent != ctx.agent:
        raise ValueError(f"Le run {run_id} appartient à l'agent {state.agent!r}, pas {ctx.agent!r}")
    if state.finished or state.status not in _ACTIONABLE:
        return state
    previous = (
        history(e for e in events if e.seq < own[0].seq) if state.parent_run_id is None else []
    )
    cause = next((e for e in reversed(own) if e.category in {"model", "tool"}), None)
    if writer is None:
        writer = SessionWriter(ctx.store, tenant, session, events[-1].seq)
    journal = writer

    async def write(draft: EventDraft) -> Event:
        nonlocal state, cause
        [event] = await journal.append([draft])
        if event.category in {"model", "tool"}:
            cause = event
        if isinstance(event.payload, RunTransitioned):
            _log_transition(event, event.payload)
        state = apply(state, event)
        return event

    sources = SourceContext(
        tenant_id=state.context.tenant_id,
        session_id=state.session_id,
        run_id=run_id,
        agent=state.agent,
    )
    async with ctx.tools.opened(sources) as opened:
        scope = _scope(state)
        blocking: tuple[Event, ToolSourceUnavailable] | None = None
        for payload in opened.unavailable:
            event = await write(scope.draft(payload))
            if payload.required and blocking is None:
                blocking = event, payload
        if blocking is not None:
            event, payload = blocking
            await write(_transition(state, scope, RunStatus.FAILED, event))
            reason = f"source {payload.source} requise et indisponible : {payload.error}"
            await write(scope.draft(_failed(state, payload.type, reason)))
            return state

        run_ctx = replace(ctx, tools=opened.tools, writer=journal)
        while not state.finished and state.status in _ACTIONABLE:
            emitted = 0
            async with aclosing(step(state, run_ctx, previous, cause=cause)) as drafts:
                async for draft in drafts:
                    await write(draft)
                    emitted += 1
            if emitted == 0:
                raise RuntimeError(
                    f"Run {run_id} : aucune progression depuis l'état {state.status}"
                )
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
    view = ctx.tools.view(state)
    system, messages = ctx.system, in_call_order(state.messages)
    if ctx.tools.shows_refs(view):
        messages = mark_results(messages, ResultIndex(messages))
        system = f"{system}\n\n{REFS_HINT}" if system else REFS_HINT
    try:
        request = ModelRequest(
            model_id=spec.model,
            system=system,
            messages=(*in_call_order(previous), *messages),
            tools=ctx.tools.definitions(view),
            tool_choice="none" if forced else "auto",
            max_tokens=ctx.max_tokens or spec.max_tokens,
            params={**spec.params, **ctx.params},
        )
        call = ModelCall(ctx.model, spec, on_chunk=ctx.on_chunk, artifacts=ctx.artifacts)
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
        # Une erreur classée du fournisseur tient en une ligne ; une autre
        # exception est inattendue, et garde sa pile d'appels.
        expected = isinstance(exc, ModelError)
        logger.error(
            "Échec de l'appel au modèle %s : %s — %s",
            spec.id,
            failure,
            exc.message if isinstance(exc, ModelError) else exc,
            exc_info=None if expected else exc,
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
    # Un span par appel d'outil, choisi d'avance : un sous-agent y rattache
    # son run. Les appels de modèle d'un rôle ont leur span, en dessous.
    spans: dict[str, SpanId] = {call.call_id: new_span_id() for call in state.pending_calls}
    model_spans: dict[str, SpanId] = {}
    emitted = 0
    batch = ctx.tools.run_batch(state, writer=ctx.writer, spans=spans)
    async with aclosing(batch) as events:
        async for item in events:
            span = spans.setdefault(item.call_id, new_span_id())
            if isinstance(item, Delegated):
                inner = model_spans.setdefault(item.call_id, new_span_id())
                yield scope.draft(item.payload, span_id=inner, parent_span_id=span, role=item.role)
            elif isinstance(item, Stored):
                yield scope.draft(item.payload, span_id=span, parent_span_id=current.span)
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
