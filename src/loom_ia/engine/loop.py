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
borné à cette dernière génération (et à ses réparations éventuelles). La
réponse forcée part sans outils, avec une consigne en dernier message de sa
requête (``FINALIZE_HINT``, jamais journalisée) : répondre en texte avec ce
qui est déjà obtenu.

Outil terminal (#13) : seul dans son tour et sans erreur, sa sortie devient
la réponse finale. ``run.completed`` désigne alors son ``tool.completed``
au lieu de recopier la sortie. Appelé avec d'autres outils, il redevient un
outil ordinaire et l'orchestrateur compose la réponse.

Rôles et sous-agents : si l'un d'eux est proposé dans le run, chaque résultat
montré au modèle porte sa référence (``[result:3]``) et le prompt système
explique ``$ref`` (#12). Dans la requête, les résultats d'un tour suivent
l'ordre des appels, quel que soit leur ordre d'arrivée dans le journal.

Politiques (#1, #2) : ``before_model`` s'exécute avant l'appel du modèle
(requête remplacée, arrêt vers ``FINALIZING``, échec) ; ``after_model`` et
``on_output`` au moment de décider de la suite d'une réponse, pour qu'une
reprise après un plantage les évalue aussi. Une réparation (``Retry``) écrit
son ``policy.decided``, ferme les appels d'outils de la réponse refusée,
puis écrit le diagnostic (``message.user`` de ``kind: repair``) : le modèle
répond de nouveau, sans outils si la politique l'a demandé. Les politiques
d'outils sont appliquées par l'exécuteur.

Contrats et diffusion (#20, #11) : les guards sont des politiques ; leurs
contrôles (``guard.checked``) précèdent leurs décisions dans le journal. Une
réponse finale structurée (schéma de sortie de l'agent) porte son objet
JSON dans ``run.completed`` ; une réponse gardée hors contrat y est marquée
``unverified``. Avec ``stream_output: live``, les morceaux du modèle partent
au fil de l'eau, et une réparation envoie d'abord un ``StreamReset`` ; avec
``after_guards``, le texte d'une réponse qui appelle des outils part à la fin
de cette réponse, et la réponse finale seulement une fois ses contrôles
passés, qu'elle vienne de l'orchestrateur ou d'un rôle terminal.

Schéma natif (B9) : un appel de l'orchestrateur qui ne peut pas appeler
d'outil (réponse forcée, réparation sans outils, agent sans outils) porte le
schéma de la réponse finale (``output_schema``) ; l'adaptateur le transmet
au fournisseur si le modèle a la capacité ``native_json``.

Secours (B4, #10) : l'appel de l'orchestrateur passe par sa chaîne
(``RunContext.chain``, ``main`` puis ``fallbacks``) : tentatives, bascules
(``model.fell_back``) et disjoncteur ouvert (``circuit.opened``) sont écrits
dans le span de l'étape. Après une bascule, le run reste sur le secours
(``RunState.models``) ; la requête est construite pour lui, avant les
politiques ``before_model``.

Budgets (J4) : ``before_model`` reçoit la consommation des runs précédents
de la session (``session``), calculée dans le journal par ``drive`` ; un
sous-run reçoit la part de budget de son parent dans son ``run.started``.

Juges (#21) : un juge est une politique fournie qui appelle son modèle ; cet
appel est écrit dans son propre span, au nom de ``judge:<nom>``, avant son
verdict (``judge.evaluated``). Son coût entre dans ``run.completed`` (ou
``run.failed``), écrit dans la même décision ; il ne compte pas dans les
itérations. Le choix de l'appelant (``judges`` : ``auto``, ``force``,
``skip``) est écrit dans ``run.started`` et hérité par les sous-runs.

Pièces jointes (G1) : validées avant tout écrit (signature binaire, type,
taille), rangées dans le stockage d'artefacts, annoncées par un
``artifact.stored`` chacune, puis jointes au message de l'utilisateur en
références. Chaque appel de modèle les résout selon ses capacités (#14).
"""

import asyncio
import json
import logging
import time
from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping, Sequence
from contextlib import aclosing, asynccontextmanager, suppress
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Final

from pydantic import JsonValue

from loom_ia.core.events import (
    ApprovalExpired,
    ApprovalGranted,
    ApprovalRejected,
    ApprovalRequested,
    ArtifactStored,
    CircuitOpened,
    Effect,
    Event,
    EventDraft,
    ModelFellBack,
    ModelResponded,
    ModelRetried,
    PolicyDecided,
    RunCancelled,
    RunClaimed,
    RunCompleted,
    RunFailed,
    RunScope,
    RunStarted,
    RunTransitioned,
    StepCompleted,
    StepStarted,
    ToolCalled,
    ToolCompleted,
    ToolSourceUnavailable,
    UserMessage,
)
from loom_ia.core.model import (
    DEFAULT_TENANT,
    FINALIZE_HINT,
    MAIN_ROLE,
    REPAIR_PREFIX,
    AfterModel,
    ApprovalSettings,
    Approver,
    ArtifactRefBlock,
    Attachment,
    AttachmentPolicy,
    BeforeModel,
    CallerContext,
    CancelReason,
    Fail,
    JudgesMode,
    Message,
    ModelRequest,
    ModelSpec,
    OnOutput,
    OutputContract,
    PendingRepair,
    Retry,
    RunBudget,
    RunClaim,
    RunId,
    RunKind,
    RunState,
    RunStatus,
    SessionId,
    SpanId,
    Spent,
    Stop,
    StreamOutput,
    StreamReset,
    TenantId,
    TextBlock,
    TextDelta,
    ToolCallBlock,
    ToolOutput,
    ToolResultBlock,
    Usage,
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
from loom_ia.core.projections import apply, fold, last_summary, spent, turns
from loom_ia.engine.circuit import CircuitBreakers
from loom_ia.engine.executor import Decided, Delegated, Stored, ToolExecutor
from loom_ia.engine.fallback import Answered, ModelChain, ModelLink
from loom_ia.engine.hooks import Policies, PolicyEvent, Verdict
from loom_ia.engine.model_call import responded
from loom_ia.engine.refs import REFS_HINT, ResultIndex, in_call_order, mark_results
from loom_ia.engine.writer import SessionWriter

logger = logging.getLogger(__name__)

DEFAULT_MAX_ITERATIONS: Final = 10
# Règle du moteur journalisée comme une décision de politique (#13, backlog #008).
TERMINAL_RULE: Final = "loom.terminal"

# Événements d'un appel de modèle, journalisés dans son span.
_CALL_EVENTS: Final = (ModelRetried, ModelFellBack, CircuitOpened, ModelResponded)

# États où ``step`` a quelque chose à faire (effet ou clôture).
DEFAULT_LEASE: Final = 60.0


class ClaimConflict(RuntimeError):
    """Un autre pilote tient la concession de ce run, et elle est vivante (#27)."""

    def __init__(self, run_id: RunId, worker_id: str, until: datetime) -> None:
        self.run_id = run_id
        self.worker_id = worker_id
        self.lease_until = until
        super().__init__(
            f"Run {run_id} : piloté par {worker_id}, concession valable jusqu'à "
            f"{until.isoformat(timespec='seconds')}"
        )


# Événements que l'exécuteur rend tels quels, sans enveloppe d'appel.
_BARE_EVENTS: Final = (
    ToolCalled,
    ToolCompleted,
    ApprovalRequested,
    ApprovalGranted,
    ApprovalRejected,
)

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
    # Politiques de l'agent, dans l'ordre déclaré (#1, #2) ; les guards en tête.
    policies: Policies = field(default_factory=Policies)
    # Historique de la session avant ce run, groupé par tour, et dernier résumé
    # de compaction : posés par ``drive`` pour les contextes déclarés (#12, #23).
    turns: tuple[tuple[Message, ...], ...] = ()
    summary: str | None = None
    # Contrat de la réponse finale : son schéma donne la réponse structurée (A7).
    output: OutputContract | None = None
    # Diffusion de la réponse finale : au fil de l'eau, ou après ses contrôles (#11).
    stream_output: StreamOutput = "live"
    # Secours du modèle ``main``, dans l'ordre (B4, #10).
    fallbacks: tuple[ModelLink, ...] = ()
    # Disjoncteurs des modèles, partagés par les runs d'une instance ``Loom``.
    breakers: CircuitBreakers | None = None
    # Délai maximal du run, en secondes (A6). Il borne le temps de pilotage
    # cumulé (``RunState.active_ms``), pas l'horloge : une reprise garde le
    # budget qui reste. ``None`` : pas de délai.
    timeout: float | None = None
    # Instance qui pilote, pour la concession (#27). ``None`` : pas de
    # concession — un ``drive`` appelé directement, ou un sous-run, que son
    # parent pilote déjà.
    worker_id: str | None = None
    # Durée de la concession, en secondes ; renouvelée au tiers.
    lease: float = DEFAULT_LEASE
    # Approbations de l'agent (#17) : délai, effet d'une expiration, droit exigé.
    approval: ApprovalSettings = field(default_factory=ApprovalSettings)
    # Approbateur en ligne (#28) : il tranche dans la boucle, et le run ne
    # passe jamais par ``PAUSED``. Sans lui, l'approbation est asynchrone.
    approver: Approver | None = None

    @property
    def artifacts(self) -> ArtifactStore | None:
        """Stockage des fichiers du run : celui de l'exécuteur d'outils."""
        return self.tools.artifacts

    def chain(self, *, on_chunk: ChunkCallback | None = None) -> ModelChain:
        """Modèle ``main`` et ses secours, avec les réglages du rôle (B6)."""
        return ModelChain(
            links=(ModelLink(self.model_spec, self.model), *self.fallbacks),
            slot=MAIN_ROLE,
            breakers=self.breakers,
            max_tokens=self.max_tokens,
            params=self.params,
            on_chunk=on_chunk,
            artifacts=self.artifacts,
        )


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
    # Juges choisis pour le parent : l'enfant hérite du même choix (#21).
    judges: JudgesMode = "auto"
    # Part du budget du parent donnée à l'enfant (``budget_share``, J4).
    budget: RunBudget | None = None


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
    judges: JudgesMode = "auto",
    kind: RunKind = "normal",
    triggered_by: RunId | None = None,
) -> RunState:
    """Écrit le démarrage d'un run et la demande de l'utilisateur.

    Un ``run_id`` fourni par l'appelant ne doit pas déjà exister dans le journal.
    Une pièce jointe refusée lève ``AttachmentError`` avant tout écrit.

    Un sous-run (``parent``) s'écrit dans le journal de son parent, avec
    l'écrivain de la session (``writer``) : il hérite de sa racine et du choix
    de ses juges, et sa profondeur est celle du parent plus un.

    ``judges`` (#21) : ``auto``, chaque juge selon son ``when`` ; ``force``,
    tous les juges ; ``skip``, aucun.

    ``kind`` et ``triggered_by`` marquent un run système : la compaction d'une
    session (#23) tourne dans le journal de cette session sans entrer dans son
    historique.
    """
    message = Message.user(prompt) if isinstance(prompt, str) else prompt
    if message.role != "user":
        raise ValueError(f"La demande doit être un message 'user', pas {message.role!r}")
    ctx.attachments.check_count(len(attachments))
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
    started = RunStarted(context=context, judges=judges, kind=kind, triggered_by=triggered_by)
    if parent is not None:
        started = RunStarted(
            context=context,
            parent_run_id=parent.run_id,
            parent_call_id=parent.call_id,
            depth=parent.depth + 1,
            judges=parent.judges,
            budget=parent.budget,
            kind=kind,
            triggered_by=triggered_by,
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
    dans le même journal. Il n'a pas d'historique de session. Un run de
    compaction non plus : son segment est déjà dans sa demande, et lui ajouter
    l'historique le ferait payer deux fois et résumer au-delà de sa coupe.
    """
    tenant = tenant_id or DEFAULT_TENANT
    session = session_id or SessionId(run_id)
    events = await ctx.store.read(tenant, session)
    own = [e for e in events if e.run_id == run_id]
    state = fold(own, run_id)
    if state.agent != ctx.agent:
        raise ValueError(f"Le run {run_id} appartient à l'agent {state.agent!r}, pas {ctx.agent!r}")
    if state.finished or not _actionable(state):
        return state
    rooted = state.parent_run_id is None and state.kind == "normal"
    earlier = [e for e in events if e.seq < own[0].seq] if rooted else []
    session_turns = tuple(turns(earlier))
    previous = [message for turn in session_turns for message in turn]
    # Consommation des runs précédents de la session : budget de session (J4).
    session_spent = spent(earlier)
    cause = next((e for e in reversed(own) if e.category in {"model", "tool"}), None)
    if writer is None:
        writer = SessionWriter(ctx.store, tenant, session, events[-1].seq)
    journal = writer
    scope = run_scope(state)
    claimed = await _claim(state, ctx, journal, scope)

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
    async with ctx.tools.opened(sources) as opened, _renewed(claimed, journal, scope):
        blocking: tuple[Event, ToolSourceUnavailable] | None = None
        for payload in opened.events:
            event = await write(scope.draft(payload))
            if isinstance(payload, ToolSourceUnavailable) and payload.required and not blocking:
                blocking = event, payload
        if blocking is not None:
            event, payload = blocking
            await write(_transition(state, scope, RunStatus.FAILED, event))
            reason = f"source {payload.source} requise et indisponible : {payload.error}"
            await write(scope.draft(_failed(state, payload.type, reason)))
            return state

        run_ctx = replace(
            ctx,
            tools=opened.tools,
            writer=journal,
            turns=session_turns,
            summary=last_summary(earlier),
        )
        while not state.finished and _actionable(state):
            emitted = 0
            left = _remaining(state, run_ctx)
            if left is not None and left <= 0:
                await _expired(write, state, scope, run_ctx, cut=False)
                return state
            try:
                async with asyncio.timeout(left):
                    async with aclosing(
                        step(state, run_ctx, previous, cause=cause, session=session_spent)
                    ) as drafts:
                        async for draft in drafts:
                            await write(draft)
                            emitted += 1
                            if isinstance(draft.payload, RunCompleted):
                                await _release(ctx, state)
            except TimeoutError:
                # L'étape a été interrompue en plein effet : ce qu'elle avait
                # déjà écrit reste au journal, et le run se clôt sur l'échec.
                await _expired(write, state, scope, run_ctx, cut=True)
                return state
            if emitted == 0:
                raise RuntimeError(
                    f"Run {run_id} : aucune progression depuis l'état {state.status}"
                )
        # Le run s'arrête sans être fini — en pause, le temps qu'on l'approuve.
        # Sa concession n'a plus de porteur : la garder vivante ferait refuser
        # la reprise pendant tout ce qu'il reste du bail (jusqu'à 60 s).
        await _handed_back(write, state, scope, claimed)
    return state


def _actionable(state: RunState) -> bool:
    """Le run peut-il avancer par lui-même ?

    Un run en pause ne le peut que si ses demandes d'approbation sont
    tranchées — ou périmées, ce qui est une décision du journal et de personne
    d'autre. Sinon il attend un humain, et ``drive`` n'a rien à y faire.
    """
    if state.status is RunStatus.PAUSED:
        now = datetime.now(UTC)
        return not state.awaiting or any(a.stale(now) for a in state.awaiting)
    return state.status in _ACTIONABLE


async def _handed_back(
    write: Callable[[EventDraft], Awaitable[Event]],
    state: RunState,
    scope: RunScope,
    claim: RunClaim | None,
) -> None:
    """Rend la concession d'un run laissé en plan volontairement (#27, #17).

    Une concession expirée à l'écriture : le premier pilote venu peut
    reprendre le run dès qu'une décision arrive, sans attendre la fin d'un
    bail que plus personne ne renouvelle. Un run fini n'en a pas besoin —
    plus rien ne le reprendra.
    """
    if claim is None or state.finished:
        return
    await write(scope.draft(RunClaimed(worker_id=claim.worker_id, lease_until=datetime.now(UTC))))


async def _claim(
    state: RunState, ctx: RunContext, journal: SessionWriter, scope: RunScope
) -> RunClaim | None:
    """Prend la concession du run, ou lève si un autre pilote la tient (#27).

    Sans ``worker_id`` — un ``drive`` appelé directement, un sous-run que son
    parent pilote déjà —, il n'y a pas de concession à prendre.
    """
    if ctx.worker_id is None or state.parent_run_id is not None:
        return None
    held = state.claim
    now = datetime.now(UTC)
    if held is not None and held.worker_id != ctx.worker_id and held.alive(now):
        raise ClaimConflict(state.run_id, held.worker_id, held.lease_until)
    mine = RunClaim(worker_id=ctx.worker_id, lease_until=now + timedelta(seconds=ctx.lease))
    await journal.append(
        [scope.draft(RunClaimed(worker_id=mine.worker_id, lease_until=mine.lease_until))]
    )
    return mine


@asynccontextmanager
async def _renewed(
    claim: RunClaim | None, journal: SessionWriter, scope: RunScope
) -> AsyncGenerator[None]:
    """Renouvelle la concession au tiers du bail, tant que le run est piloté.

    Un minuteur, et pas un renouvellement entre deux étapes : un run bloqué
    dans une étape plus longue que son bail est bien vivant, et perdrait sa
    concession au profit d'un second pilote.
    """
    if claim is None:
        yield
        return
    period = max((claim.lease_until - datetime.now(UTC)).total_seconds() / 3, 1.0)

    async def renew() -> None:
        while True:
            await asyncio.sleep(period)
            until = datetime.now(UTC) + timedelta(seconds=period * 3)
            draft = scope.draft(RunClaimed(worker_id=claim.worker_id, lease_until=until))
            try:
                await journal.append([draft])
            except Exception:
                logger.warning("Concession du run %s : renouvellement raté", scope.run_id)
                return

    task = asyncio.create_task(renew())
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


def cancellation(
    state: RunState, *, reason: CancelReason = "requested", by: str | None = None
) -> list[EventDraft]:
    """Transition et clôture d'un run arrêté à la demande (A5).

    L'annulation est terminale : un run annulé ne se reprend pas. C'est ce qui
    la distingue d'un run simplement interrompu — plantage, flux abandonné —,
    qui ne laisse rien au journal et repart où il s'était arrêté.
    """
    scope = run_scope(state)
    return [
        _transition(state, scope, RunStatus.CANCELLED, "cancel"),
        scope.draft(
            RunCancelled(
                reason=reason,
                by=by,
                iterations=state.iterations,
                usage=state.usage,
                cost_usd=state.cost_usd,
            )
        ),
    ]


def _remaining(state: RunState, ctx: RunContext) -> float | None:
    """Secondes de pilotage qui restent au run, ou ``None`` s'il n'a pas de délai."""
    if ctx.timeout is None:
        return None
    return ctx.timeout - state.active_ms / 1000


async def _expired(
    write: Callable[[EventDraft], Awaitable[Event]],
    state: RunState,
    scope: RunScope,
    ctx: RunContext,
    *,
    cut: bool,
) -> None:
    """Clôture d'un run qui a dépassé son délai maximal (A6).

    Un dépassement est subi, pas décidé : il s'écrit en ``run.failed``, pas en
    ``run.cancelled``. Comme toute clôture, il ferme le run.

    ``cut`` distingue les deux façons de dépasser : une étape coupée en plein
    effet, ou un budget déjà épuisé avant même de commencer la suivante. Le
    temps de l'étape coupée n'est nulle part — elle n'a pas de
    ``step.completed`` —, donc le message ne le confond pas avec le cumul.
    """
    await write(_transition(state, scope, RunStatus.FAILED, "timeout"))
    done = state.active_ms / 1000
    limit = ctx.timeout or 0.0
    if cut:
        reason = (
            f"délai maximal de {limit:.1f} s dépassé : l'étape {state.step} a été "
            f"interrompue en cours ({done:.1f} s d'étapes déjà terminées)"
        )
    else:
        reason = (
            f"délai maximal de {limit:.1f} s dépassé : {done:.1f} s de pilotage "
            "déjà écoulées avant l'étape suivante"
        )
    await write(scope.draft(_failed(state, "timeout", reason)))


async def _release(ctx: RunContext, state: RunState) -> None:
    """``after_guards`` : la réponse finale, contrôlée, part dans le flux une fois le run clos."""
    if ctx.on_chunk is None or ctx.stream_output != "after_guards" or state.output is None:
        return
    if state.output.text:
        await ctx.on_chunk(TextDelta(text=state.output.text))


async def step(
    state: RunState,
    ctx: RunContext,
    previous: Sequence[Message] = (),
    *,
    cause: Event | None = None,
    session: Spent | None = None,
) -> AsyncGenerator[EventDraft]:
    """Événements de la prochaine étape du run.

    ``previous`` est l'historique de la session avant ce run ; ``cause`` le
    dernier événement d'effet, référencé par les transitions ; ``session`` la
    consommation des runs précédents de la session (budget de session).
    """
    session = session or Spent()
    scope = run_scope(state)
    decision = await _decide(state, ctx, scope, cause)
    if decision is not None:
        for draft in decision:
            yield draft
        return
    match state.status:
        case RunStatus.READY_FOR_MODEL:
            effect = _model_step(state, ctx, scope, previous, forced=False, session=session)
        case RunStatus.FINALIZING:
            effect = _model_step(state, ctx, scope, previous, forced=True, session=session)
        case RunStatus.AWAITING_TOOLS:
            effect = _tool_step(state, ctx, scope)
        case _:
            return
    async with aclosing(effect) as drafts:
        async for draft in drafts:
            yield draft


# --- Décisions ---------------------------------------------------------------


async def _decide(
    state: RunState, ctx: RunContext, scope: RunScope, cause: Event | None
) -> list[EventDraft] | None:
    """Transition ou clôture dues à l'état courant, ou None s'il reste un effet à exécuter.

    Une réponse de l'orchestrateur passe d'abord par les politiques
    ``after_model``, une réponse finale par ``on_output`` : elles décident ici
    de la suite, et une reprise les réévalue si leur décision n'a pas été
    appliquée.
    """
    last = state.messages[-1] if state.messages else None
    answered = last is not None and last.role == "assistant"
    if state.pending_repair is not None and not state.status.is_terminal:
        # Réparation décidée avant une interruption : il reste à la demander.
        return _repair(state, ctx, scope, state.pending_repair, cause)
    match state.status:
        case RunStatus.COMPLETED if not state.finished and last is not None and last.role == "tool":
            # Seul un outil terminal mène à COMPLETED sur un résultat d'outil.
            terminal = _terminal_event(state, cause)
            return [scope.draft(_completed(state, state.replaced_output, terminal=terminal))]
        case RunStatus.COMPLETED if not state.finished:
            output = state.replaced_output or (last if answered else None)
            data = _structured(ctx.output, output) if output is not None else None
            return [scope.draft(_completed(state, output, data=data))]
        case RunStatus.FAILED if not state.finished:
            return [
                scope.draft(
                    _failed(state, "Interrupted", "run interrompu après son passage en échec")
                )
            ]
        case RunStatus.READY_FOR_MODEL | RunStatus.FINALIZING if answered and last is not None:
            return await _review(state, ctx, scope, cause, last)
        case RunStatus.PAUSED:
            return _resumed(state, ctx, scope, cause)
        case RunStatus.AWAITING_TOOLS if state.awaiting:
            # Le reste du lot est passé ; ces appels-là attendent un humain (#17).
            return [_transition(state, scope, RunStatus.PAUSED, cause)]
        case RunStatus.AWAITING_TOOLS if not state.pending_calls:
            terminal, parallel = _terminal(state, ctx)
            if terminal is not None:
                return await _final(state, ctx, scope, cause, _terminal_message(state), terminal)
            drafts = [
                scope.draft(
                    PolicyDecided(
                        policy=TERMINAL_RULE,
                        point="after_tool",
                        decision="continue",
                        reason=(
                            f"Outil terminal {call.name} appelé avec d'autres outils : "
                            "sa sortie revient à l'orchestrateur."
                        ),
                        call_id=call.call_id,
                    )
                )
                for call in parallel
            ]
            return [*drafts, _transition(state, scope, _after_tools(state, ctx), cause)]
        case _:
            return None


def _resumed(
    state: RunState, ctx: RunContext, scope: RunScope, cause: Event | None
) -> list[EventDraft] | None:
    """Ce qu'un run en pause peut faire des décisions qui lui sont parvenues (#17).

    Une demande dont la date est passée expire **ici**, au moment où on la
    regarde : c'est l'``expire_at`` du journal qui fait foi, et non le travail
    différé qui devait la réveiller — perdu dans un redémarrage, il laisserait
    sinon la demande approuvable indéfiniment.

    Le run repart en ``AWAITING_TOOLS`` dès que plus rien n'attend : l'appel
    approuvé est rejoué, l'appel refusé rend son motif au modèle. Si une
    expiration est réglée sur ``fail``, le run échoue au lieu de repartir.
    """
    now = datetime.now(UTC)
    action = ctx.approval.on_expiry
    stale = [a for a in state.awaiting if a.stale(now) and a.expire_at is not None]
    drafts = [
        scope.draft(
            ApprovalExpired(
                call_id=a.call_id,
                tool_name=a.tool_name,
                action=action,
                expire_at=a.expire_at,
            )
        )
        for a in stale
        if a.expire_at is not None
    ]
    if stale and action == "fail":
        names = ", ".join(sorted({a.tool_name for a in stale}))
        return [
            *drafts,
            *_fail(
                state,
                scope,
                "approval.expired",
                f"approbation non obtenue dans le délai imparti ({names})",
                cause,
            ),
        ]
    if len(stale) < len(state.awaiting):
        # Il en reste qui attendent vraiment : le run se rendort.
        return drafts
    return [*drafts, _transition(state, scope, RunStatus.AWAITING_TOOLS, cause)]


async def _review(
    state: RunState, ctx: RunContext, scope: RunScope, cause: Event | None, answer: Message
) -> list[EventDraft]:
    """Suite d'une réponse de l'orchestrateur : politiques ``after_model``, puis outils ou fin."""
    finalizing = state.status is RunStatus.FINALIZING
    verdict = await ctx.policies.run(
        AfterModel(state=state, response=answer, finalizing=finalizing),
        ignore=frozenset({"stop"}) if finalizing else frozenset(),
        turns=ctx.turns,
        summary=ctx.summary,
    )
    drafts = _policy_drafts(scope, verdict.events)
    reason = _stopping(verdict, drafts)
    match verdict.decision:
        case Retry():
            await _reset(ctx, state)
            return [*drafts, *_repair(state, ctx, scope, _pending_repair(verdict), reason)]
        case Fail(error=error):
            return [
                *drafts,
                *_fail(state, scope, _failure_type(verdict), error, reason, spent=verdict.spent),
            ]
        case Stop(reason=why) if state.pending_calls:
            return [
                *drafts,
                *_close_calls(state, scope, f"Non exécuté : run arrêté ({why})."),
                _transition(state, scope, RunStatus.FINALIZING, reason),
            ]
        case _:
            pass
    if state.pending_calls:
        return [*drafts, _transition(state, scope, RunStatus.AWAITING_TOOLS, cause)]
    return [*drafts, *await _final(state, ctx, scope, cause, answer, None)]


async def _final(
    state: RunState,
    ctx: RunContext,
    scope: RunScope,
    cause: Event | None,
    output: Message,
    terminal: ToolCallBlock | None,
) -> list[EventDraft]:
    """Réponse finale : politiques ``on_output``, puis clôture, réparation ou échec.

    ``terminal`` : l'appel de l'outil terminal dont ``output`` est la sortie (#13).
    """
    subject = OnOutput(
        state=state,
        output=output,
        source="model" if terminal is None else "terminal",
        tool=None if terminal is None else terminal.name,
        finalizing=state.status is RunStatus.FINALIZING,
    )
    verdict = await ctx.policies.run(subject, turns=ctx.turns, summary=ctx.summary)
    drafts = _policy_drafts(scope, verdict.events)
    reason = _stopping(verdict, drafts)
    match verdict.decision:
        case Retry():
            await _reset(ctx, state)
            return [*drafts, *_repair(state, ctx, scope, _pending_repair(verdict), reason)]
        case Fail(error=error):
            return [
                *drafts,
                *_fail(state, scope, _failure_type(verdict), error, reason, spent=verdict.spent),
            ]
        case _:
            pass
    final = verdict.subject.output if isinstance(verdict.subject, OnOutput) else output
    replaced = verdict.replaced
    data = _structured(ctx.output, final)
    completion = _transition(state, scope, RunStatus.COMPLETED, cause)
    if terminal is not None:
        closing = _completed(
            state,
            final if replaced else None,
            terminal=_terminal_event(state, cause),
            data=data,
            unverified=verdict.unverified,
            spent=verdict.spent,
        )
    else:
        closing = _completed(
            state, final, data=data, unverified=verdict.unverified, spent=verdict.spent
        )
    return [*drafts, completion, scope.draft(closing)]


async def _reset(ctx: RunContext, state: RunState) -> None:
    """En diffusion ``live``, la réponse refusée a déjà été diffusée : elle doit être effacée."""
    if ctx.on_chunk is not None and ctx.stream_output == "live":
        await ctx.on_chunk(StreamReset(attempt=state.iterations + 1))


def _failure_type(verdict: Verdict) -> str:
    """Type d'erreur d'un échec : celui du guard qui l'a décidé, sinon celui de la politique."""
    failed = next((c for c in verdict.checked if c.resolution == "fail"), None)
    return f"guard.{failed.guard}" if failed is not None else f"policy.{verdict.by}"


def _schema(contract: OutputContract | None) -> dict[str, JsonValue] | None:
    return contract.json_schema if contract is not None else None


def _structured(contract: OutputContract | None, output: Message) -> JsonValue:
    """Objet JSON de la réponse finale quand l'agent déclare un schéma de sortie (A7)."""
    if contract is None or contract.json_schema is None:
        return None
    try:
        return json.loads(output.text)
    except json.JSONDecodeError:
        return None


def _stopping(verdict: Verdict, drafts: list[EventDraft]) -> EventDraft | None:
    """Événement de la décision qui a arrêté la chaîne : cause de la transition qui suit."""
    return drafts[-1] if drafts and verdict.by is not None else None


def _pending_repair(verdict: Verdict) -> PendingRepair:
    decision = verdict.decision
    if not isinstance(decision, Retry) or verdict.by is None:
        raise RuntimeError("Réparation sans décision Retry")
    return PendingRepair(
        policy=verdict.by,
        point="on_output" if verdict.subject.point == "on_output" else "after_model",
        feedback=decision.feedback,
        tools=decision.tools,
    )


def _repair(
    state: RunState,
    ctx: RunContext,
    scope: RunScope,
    repair: PendingRepair,
    cause: Event | EventDraft | None,
) -> list[EventDraft]:
    """Demande de réparation au modèle orchestrateur (#20).

    Les appels d'outils de la réponse refusée sont fermés sans être exécutés,
    puis le diagnostic est écrit. Après une sortie terminale refusée, le run
    revient à l'orchestrateur.
    """
    drafts = _close_calls(
        state, scope, f"Non exécuté : réponse refusée par le contrôle {repair.policy}."
    )
    text = f"{REPAIR_PREFIX} ({repair.policy}) : {repair.feedback}"
    drafts.append(
        scope.draft(
            UserMessage(
                message=Message.user(text), kind="repair", policy=repair.policy, tools=repair.tools
            )
        )
    )
    if state.status is RunStatus.AWAITING_TOOLS:
        drafts.append(_transition(state, scope, _after_tools(state, ctx), cause))
    return drafts


def _close_calls(state: RunState, scope: RunScope, reason: str) -> list[EventDraft]:
    """Résultats d'erreur pour les appels en attente, qui ne seront pas exécutés."""
    return [
        scope.draft(
            ToolCompleted(
                call_id=call.call_id, tool_name=call.name, output=ToolOutput.error(reason)
            ),
            span_id=new_span_id(),
        )
        for call in state.pending_calls
    ]


def _fail(
    state: RunState,
    scope: RunScope,
    error_type: str,
    error: str,
    cause: Event | EventDraft | None,
    *,
    spent: tuple[Usage, float] = (Usage(), 0.0),
) -> list[EventDraft]:
    """Échec du run ; ``spent`` : consommation pas encore appliquée à ``state`` (juges, lot)."""
    return [
        _transition(state, scope, RunStatus.FAILED, cause or error_type),
        scope.draft(_failed(state, error_type, error, spent=spent)),
    ]


def _after_tools(state: RunState, ctx: RunContext) -> RunStatus:
    """État qui suit un lot d'outils : le modèle, ou la réponse forcée si le plafond est atteint."""
    limit_reached = state.iterations >= ctx.max_iterations
    return RunStatus.FINALIZING if limit_reached else RunStatus.READY_FOR_MODEL


def _transition(
    state: RunState,
    scope: RunScope,
    target: RunStatus,
    cause: Event | EventDraft | str | None,
) -> EventDraft:
    if isinstance(cause, Event | EventDraft):
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


def _terminal(
    state: RunState, ctx: RunContext
) -> tuple[ToolCallBlock | None, tuple[ToolCallBlock, ...]]:
    """Appel terminal dont la sortie devient la réponse finale (#13), ou None.

    Le second élément : les outils terminaux appelés avec d'autres, pour qui
    la règle ne s'applique pas.
    """
    turn = next((m for m in reversed(state.messages) if m.role == "assistant"), None)
    calls = turn.tool_calls if turn is not None else ()
    terminal = [c for c in calls if (tool := ctx.tools.get(c.name)) and tool.spec.terminal]
    if not terminal:
        return None, ()
    if len(calls) > 1:
        logger.warning(
            "Outil terminal %s appelé avec d'autres outils : sa sortie revient à l'orchestrateur",
            ", ".join(c.name for c in terminal),
            extra={"run_id": state.run_id},
        )
        return None, tuple(terminal)
    last = state.messages[-1]
    succeeded = all(
        isinstance(block, ToolResultBlock) and not block.output.is_error for block in last.blocks
    )
    return (terminal[0] if succeeded else None), ()


def _terminal_message(state: RunState) -> Message:
    """Sortie de l'outil terminal, en réponse de l'assistant."""
    last = state.messages[-1]
    [result] = [b for b in last.blocks if isinstance(b, ToolResultBlock)]
    return Message(role="assistant", blocks=result.output.blocks or (TextBlock(text=""),))


def _terminal_event(state: RunState, cause: Event | None) -> Event:
    """``tool.completed`` de l'outil terminal : c'est le dernier effet du run."""
    if cause is None or not isinstance(cause.payload, ToolCompleted):
        raise RuntimeError(f"Run {state.run_id} : sortie terminale sans tool.completed")
    return cause


def _completed(
    state: RunState,
    output: Message | None,
    *,
    terminal: Event | None = None,
    data: JsonValue = None,
    unverified: bool = False,
    spent: tuple[Usage, float] = (Usage(), 0.0),
) -> RunCompleted:
    """Clôture ; ``spent`` : consommation des juges de la décision, pas encore appliquée."""
    usage, cost = spent
    return RunCompleted(
        output=output,
        output_event_id=terminal.event_id if terminal is not None else None,
        iterations=state.iterations,
        usage=state.usage + usage,
        cost_usd=state.cost_usd + cost,
        data=data,
        unverified=unverified or state.unverified,
    )


def _failed(
    state: RunState, error_type: str, error: str, *, spent: tuple[Usage, float] = (Usage(), 0.0)
) -> RunFailed:
    usage, cost = spent
    return RunFailed(
        error_type=error_type,
        error=error,
        iterations=state.iterations,
        usage=state.usage + usage,
        cost_usd=state.cost_usd + cost,
    )


def _policy_drafts(
    scope: RunScope,
    events: Sequence[PolicyEvent],
    *,
    span_id: SpanId | None = None,
    parent_span_id: SpanId | None = None,
) -> list[EventDraft]:
    """Événements des politiques d'un point, dans leur span.

    Les appels de modèle d'un juge ont leur propre span, sous celui du point, au
    nom de ``judge:<nom>`` : un span par appel (ses nouvelles tentatives, ses
    bascules, puis sa réponse).
    """
    drafts: list[EventDraft] = []
    judged: SpanId | None = None
    for event in events:
        if isinstance(event, _CALL_EVENTS) and event.judge is not None:
            judged = judged or new_span_id()
            drafts.append(
                scope.draft(
                    event,
                    span_id=judged,
                    parent_span_id=span_id or scope.span_id,
                    role=judge_role(event.judge),
                )
            )
            if isinstance(event, ModelResponded):
                judged = None
        else:
            drafts.append(scope.draft(event, span_id=span_id, parent_span_id=parent_span_id))
    return drafts


def judge_role(judge: str) -> str:
    """Rôle d'un juge dans l'enveloppe des événements : son coût lui est attribué (#21)."""
    return f"judge:{judge}"


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

    def draft(self, payload: StepStarted | StepCompleted | PolicyEvent) -> EventDraft:
        role: str | None = None
        if isinstance(payload, _CALL_EVENTS):
            # Les appels de modèle de l'orchestrateur sont ceux du rôle ``main`` (C6).
            role = judge_role(payload.judge) if payload.judge is not None else MAIN_ROLE
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
    session: Spent,
) -> AsyncGenerator[EventDraft]:
    current = _Step(state, scope, "finalize" if forced else "model_call")
    yield current.started()
    # État vu par les transitions écrites pendant l'étape : son numéro est celui-ci.
    stepped = state.model_copy(update={"step": current.no})
    # En after_guards, rien ne part pendant l'appel : la réponse est d'abord contrôlée.
    live = ctx.on_chunk if ctx.stream_output == "live" else None
    chain = ctx.chain(on_chunk=live)
    # Adhérence (#10) : après une bascule, le run reste sur le secours.
    adhered = state.models.get(MAIN_ROLE)
    spec = chain.link(adhered).spec
    started = time.perf_counter()
    emitted = 0
    answered: Answered | None = None
    view = ctx.tools.view(state)
    system, messages = ctx.system, in_call_order(state.messages)
    if ctx.tools.shows_refs(view):
        messages = mark_results(messages, ResultIndex(messages))
        system = f"{system}\n\n{REFS_HINT}" if system else REFS_HINT
    # Réponse forcée : sa consigne en dernier message, dans la requête seulement.
    hint = (Message.user(FINALIZE_HINT),) if forced else ()
    tools = ctx.tools.definitions(view)
    without_tools = forced or state.repair_without_tools or not tools
    request = ModelRequest(
        model_id=spec.model,
        system=system,
        messages=(*in_call_order(previous), *messages, *hint),
        tools=tools,
        tool_choice="none" if forced or state.repair_without_tools else "auto",
        max_tokens=ctx.max_tokens or spec.max_tokens,
        params={**spec.params, **ctx.params},
        # Schéma natif (B9) : seulement pour un appel qui ne peut pas appeler d'outil.
        output_schema=_schema(ctx.output) if without_tools else None,
    )
    verdict = await ctx.policies.run(
        BeforeModel(state=state, request=request, finalizing=forced, session=session),
        ignore=frozenset({"stop"}) if forced else frozenset(),
        turns=ctx.turns,
        summary=ctx.summary,
    )
    decided = [current.draft(payload) for payload in verdict.events]
    for draft in decided:
        yield draft
    emitted += len(decided)
    reason = _stopping(verdict, decided)
    match verdict.decision:
        case Stop():
            yield current.completed(emitted)
            yield _transition(stepped, scope, RunStatus.FINALIZING, reason)
            return
        case Fail(error=error):
            yield current.completed(emitted, ok=False)
            for draft in _fail(stepped, scope, _failure_type(verdict), error, reason):
                yield draft
            return
        case _:
            pass
    if isinstance(verdict.subject, BeforeModel):
        request = verdict.subject.request
    if forced and request.tool_choice != "none":
        # Garde-fou : la réponse forcée reste sans outils, quoi qu'une politique demande.
        logger.warning(
            "Réponse forcée : tool_choice %r remplacé par 'none'",
            request.tool_choice,
            extra={"run_id": state.run_id, "span_id": current.span},
        )
        request = request.model_copy(update={"tool_choice": "none"})
    try:
        async with aclosing(chain.run(request, current=adhered)) as outcomes:
            async for outcome in outcomes:
                if isinstance(outcome, Answered):
                    answered = outcome
                else:
                    yield current.draft(outcome)
                    emitted += 1
        if answered is None:
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
        yield _transition(stepped, scope, RunStatus.FAILED, failure)
        yield scope.draft(_failed(stepped, failure, str(exc)))
        return
    response = answered.response
    message = response.message
    if forced and message.tool_calls:
        logger.warning(
            "Réponse forcée avec appels d'outils : appels ignorés",
            extra={"run_id": state.run_id, "span_id": current.span},
        )
        kept = tuple(b for b in message.blocks if not isinstance(b, ToolCallBlock))
        message = message.model_copy(update={"blocks": kept or (TextBlock(text=""),)})
    if ctx.on_chunk is not None and live is None and message.tool_calls and message.text:
        # after_guards : le texte d'une réponse qui appelle des outils n'est pas la
        # réponse finale ; il part dès que la réponse est complète.
        await ctx.on_chunk(TextDelta(text=message.text))
    yield current.draft(
        responded(
            answered.request,
            response,
            answered.spec,
            attempts=answered.attempts,
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
    # Appel en cours du juge d'un résultat : son span, sous celui de l'appel d'outil.
    judge_spans: dict[str, SpanId] = {}
    emitted = 0
    # Consommation du lot (rôles, juges, sous-agents), pour la clôture d'un échec.
    usage, cost = Usage(), 0.0
    # Décision ``Fail`` d'une politique d'outil : le run échoue à la fin du lot.
    failure: tuple[EventDraft, PolicyDecided] | None = None
    batch = ctx.tools.run_batch(
        state,
        writer=ctx.writer,
        spans=spans,
        policies=ctx.policies,
        on_chunk=ctx.on_chunk if ctx.stream_output == "live" else None,
        turns=ctx.turns,
        summary=ctx.summary,
        approver=ctx.approver,
    )
    async with aclosing(batch) as events:
        async for item in events:
            span = spans.setdefault(item.call_id, new_span_id())
            seen = item if isinstance(item, _BARE_EVENTS) else item.payload
            if isinstance(seen, ModelResponded):
                usage, cost = usage + seen.usage, cost + seen.cost_usd
            elif isinstance(seen, ToolCompleted) and seen.usage is not None:
                usage, cost = usage + seen.usage, cost + seen.cost_usd
            if isinstance(item, Delegated):
                inner = model_spans.setdefault(item.call_id, new_span_id())
                yield scope.draft(item.payload, span_id=inner, parent_span_id=span, role=item.role)
            elif (
                isinstance(item, Decided)
                and isinstance(item.payload, _CALL_EVENTS)
                and item.payload.judge is not None
            ):
                judged = judge_spans.setdefault(item.call_id, new_span_id())
                yield scope.draft(
                    item.payload,
                    span_id=judged,
                    parent_span_id=span,
                    role=judge_role(item.payload.judge),
                )
                if isinstance(item.payload, ModelResponded):
                    del judge_spans[item.call_id]
            elif isinstance(item, Stored | Decided):
                draft = scope.draft(item.payload, span_id=span, parent_span_id=current.span)
                payload = item.payload
                if (
                    isinstance(payload, PolicyDecided)
                    and payload.decision == "fail"
                    and not failure
                ):
                    failure = draft, payload
                yield draft
            else:
                yield scope.draft(item, span_id=span, parent_span_id=current.span)
            emitted += 1
    if failure is not None:
        draft, decided = failure
        yield current.completed(emitted, ok=False)
        for event in _fail(
            state.model_copy(update={"step": current.no}),
            scope,
            f"policy.{decided.policy}",
            decided.reason,
            draft,
            spent=(usage, cost),
        ):
            yield event
        return
    yield current.completed(emitted)


# --- Interne -----------------------------------------------------------------


def run_scope(state: RunState) -> RunScope:
    """Portée où écrire les événements d'un run : son span, son arbre, son agent."""
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
