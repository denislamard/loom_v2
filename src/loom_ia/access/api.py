# SPDX-License-Identifier: Apache-2.0
"""Accès Python : la façade ``Loom`` (N1).

Une instance rassemble ce qui était jusqu'ici assemblé à la main : la
configuration, les agents, le journal, les clients de modèle. Les deux autres
accès du jalon J1.6 — l'API REST et le serveur MCP — passent par elle, si
bien qu'un run donne le même journal quel que soit le chemin emprunté.

    async with Loom.from_config("loom.yaml") as loom:
        result = await loom.run("demo", "Bonjour")
        print(result.text)

``stream`` mêle les événements du journal et les morceaux du modèle, dans
l'ordre où ils arrivent : c'est la même source pour le direct de la CLI et
pour le flux SSE.

Sous-runs (C5) : ``stream``, ``follow`` et ``events`` rendent par défaut
l'arbre du run — ses événements et ceux de ses sous-runs, dans l'ordre du
journal ; chaque événement dit à quel run il appartient (``run_id``,
``agent``). ``subruns=False`` s'en tient au run lui-même. Les morceaux du
modèle d'un sous-run ne sont pas diffusés : sa réponse arrive avec le
``tool.completed`` de l'appel.

Fichiers (G1 à G3) : ``run`` et ``stream`` acceptent des pièces jointes
(``Attachment``), validées puis rangées dans le stockage d'artefacts de
l'instance ; ``RunResult.artifacts`` liste les fichiers du run, et
``artifact(uri)`` en rend les octets.

    photo = Attachment.from_path("photo.jpg")
    result = await loom.run("assistant", "Que montre la photo ?", attachments=[photo])

Disjoncteurs (#10) : ceux des modèles et des serveurs MCP sont communs à
tous les runs de l'instance. Un modèle écarté après ses échecs l'est pour
tous ses agents, qui passent directement à leur secours.

Résultat (J3) : ``RunResult`` dit, en plus de la réponse, si elle a été
gardée sans respecter son contrat ou son juge (``unverified``), la
consommation ventilée du run et de ses sous-runs (``report`` : par run, par
rôle, par modèle) et les verdicts des juges (``verdicts``). Un échec a un
type (``error_type`` : ``guard.judge``, ``model.auth``…) et un message
lisible (``error``). Les trois accès rendent ce même résultat.
"""

import asyncio
import logging
from collections.abc import AsyncGenerator, Mapping, Sequence
from contextlib import suppress
from dataclasses import replace
from pathlib import Path
from typing import Self

from pydantic import JsonValue, NonNegativeInt, PositiveInt

from loom_ia.adapters.queue import AsyncioTaskQueue
from loom_ia.adapters.stores import NotifyingEventStore
from loom_ia.agents.registry import AgentRegistry
from loom_ia.agents.spec import AgentSpec
from loom_ia.config import LoomConfig, load_config
from loom_ia.config.compaction import COMPACTION_AGENT
from loom_ia.config.references import Registry
from loom_ia.core.events import (
    Event,
    JudgeEvaluated,
    RunCompleted,
    RunFailed,
    SessionCompacted,
)
from loom_ia.core.model import (
    DEFAULT_TENANT,
    ArtifactRecord,
    Attachment,
    CallerContext,
    CriterionScore,
    JudgesMode,
    Message,
    ModelChunk,
    RunId,
    RunState,
    RunStatus,
    SessionId,
    TenantId,
    Usage,
    new_run_id,
)
from loom_ia.core.model.base import DomainModel
from loom_ia.core.ports import ArtifactStore, ChunkCallback, EventStore, Job, SessionRecord
from loom_ia.core.projections import RunTree, fold
from loom_ia.engine import (
    CircuitBreakers,
    RunContext,
    SessionWriter,
    SessionWriters,
    begin_run,
    cancellation,
    drive,
)
from loom_ia.runtime import (
    Agent,
    build_agent,
    create_artifact_store,
    create_event_store,
    create_mcp_pool,
    load_registry,
)
from loom_ia.sessions import CompactionJob, CompactionPlan, write_snapshot
from loom_ia.usage import UsageReport, usage_report

logger = logging.getLogger(__name__)

# Ce qu'un run donne à voir pendant qu'il se déroule.
type StreamItem = Event | ModelChunk


def is_final(event: Event) -> bool:
    """Vrai sur l'événement qui clôt un run."""
    return isinstance(event.payload, RunCompleted | RunFailed)


class UnknownRun(KeyError):
    """Aucun run de cet identifiant dans le journal."""

    def __init__(self, run_id: RunId) -> None:
        super().__init__(f"Run {run_id} inconnu")
        self.run_id = run_id


class UnknownSession(KeyError):
    """Aucune session de cet identifiant dans le journal."""

    def __init__(self, session_id: SessionId) -> None:
        super().__init__(f"Session {session_id} inconnue")
        self.session_id = session_id


class SessionDeletion(DomainModel):
    """Ce qu'a retiré la suppression d'une session (RGPD, F7)."""

    session_id: SessionId
    events: NonNegativeInt = 0
    artifacts: NonNegativeInt = 0


class JudgeVerdict(DomainModel):
    """Verdict d'un juge sur une sortie du run ou d'un de ses sous-runs (``judge.evaluated``)."""

    run_id: RunId
    agent: str
    judge: str
    # ``output`` (réponse finale) ou ``role:<nom>``.
    target: str
    # Appel du rôle jugé (``tool.called``) ; None pour la réponse finale.
    call_id: str | None = None
    model_id: str
    # Tous les critères atteignent leur seuil.
    passed: bool
    # Un critère bloquant est sous son seuil : la sortie a été refusée.
    blocked: bool
    attempt: PositiveInt = 1
    criteria: tuple[CriterionScore, ...] = ()

    @classmethod
    def of(cls, event: Event, verdict: JudgeEvaluated) -> Self:
        return cls(
            run_id=event.run_id,
            agent=event.agent or "",
            judge=verdict.judge,
            target=verdict.target,
            call_id=verdict.call_id,
            model_id=verdict.model_id,
            passed=verdict.passed,
            blocked=verdict.blocked,
            attempt=verdict.attempt,
            criteria=verdict.criteria,
        )


class RunResult(DomainModel):
    """Ce qu'un run a produit, tiré de son état final et de son journal."""

    run_id: RunId
    session_id: SessionId
    agent: str
    status: RunStatus
    text: str = ""
    output: Message | None = None
    # Échec : son type (``guard.judge``, ``guard.contract``, ``model.auth``,
    # ``policy.<nom>``…) et son message, lisible tel quel.
    error_type: str | None = None
    error: str | None = None
    iterations: int = 0
    usage: Usage = Usage()
    cost_usd: float = 0.0
    # Fichiers du run : pièces jointes, fichiers produits par les outils, déports (G3).
    artifacts: tuple[ArtifactRecord, ...] = ()
    # Réponse structurée : l'objet JSON validé par le schéma de sortie (A7).
    data: JsonValue = None
    # Réponse gardée bien qu'elle ne respecte pas son contrat (``on_failure: unverified``).
    unverified: bool = False
    # Consommation ventilée du run et de ses sous-runs : par run, par rôle, par modèle.
    report: UsageReport | None = None
    # Verdicts des juges du run et de ses sous-runs, dans l'ordre du journal.
    verdicts: tuple[JudgeVerdict, ...] = ()

    @classmethod
    def of(cls, state: RunState, events: Sequence[Event] = ()) -> Self:
        """Résultat d'un état final.

        ``events`` : le journal de sa session, d'où viennent le rapport et les verdicts.
        """
        tree = RunTree(state.run_id).select(events)
        return cls(
            run_id=state.run_id,
            session_id=state.session_id,
            agent=state.agent,
            status=state.status,
            text=state.output.text if state.output else "",
            output=state.output,
            error_type=state.error_type,
            error=state.error,
            iterations=state.iterations,
            usage=state.usage,
            cost_usd=state.cost_usd,
            artifacts=state.artifacts,
            data=state.output_data,
            unverified=state.unverified,
            report=usage_report(tree, state.session_id, state.run_id) if tree else None,
            verdicts=tuple(
                JudgeVerdict.of(event, payload)
                for event in tree
                if isinstance(payload := event.payload, JudgeEvaluated)
            ),
        )

    @property
    def ok(self) -> bool:
        return self.status is RunStatus.COMPLETED

    @property
    def produced(self) -> tuple[ArtifactRecord, ...]:
        """Fichiers produits par les outils du run."""
        return tuple(a for a in self.artifacts if a.origin == "tool_output")


class Loom:
    """Une configuration chargée, prête à faire tourner ses agents."""

    def __init__(
        self,
        config: LoomConfig,
        *,
        store: EventStore | None = None,
        registry: Registry | None = None,
        environ: Mapping[str, str] | None = None,
        artifacts: ArtifactStore | None = None,
        breakers: CircuitBreakers | None = None,
    ) -> None:
        self._config = config
        self._registry = registry if registry is not None else load_registry(config)
        self._agents = AgentRegistry.from_config(config)
        inner = store if store is not None else create_event_store(config)
        self._store = (
            inner if isinstance(inner, NotifyingEventStore) else NotifyingEventStore(inner)
        )
        # Un journal fourni par l'appelant reste à lui de fermer.
        self._owns_store = store is None
        # Stockage des fichiers, commun aux agents ; même règle de fermeture.
        self._artifacts = artifacts if artifacts is not None else create_artifact_store(config)
        self._owns_artifacts = artifacts is None
        self._environ = environ
        self._built: dict[str, Agent] = {}
        # Connexions MCP de portée shared, communes à tous les agents.
        self._mcp = create_mcp_pool(config, environ)
        # Disjoncteurs des modèles et des serveurs MCP, communs à tous les runs.
        self._breakers = breakers if breakers is not None else CircuitBreakers()
        # Un écrivain par session : deux runs d'une même session écrivent par
        # le même, et ne se refusent pas l'un l'autre (#22).
        self._writers = SessionWriters()
        # Runs pilotés ici, pour que ``cancel`` puisse les atteindre (A5).
        self._driving: dict[RunId, asyncio.Task[RunState]] = {}
        # Compaction : agent interne et file de tâches, seulement si la config
        # la déclare (#23).
        compaction = config.sessions.compaction
        self._compaction = (
            CompactionJob(
                self.context,
                self._store,
                self._writers,
                CompactionPlan(
                    agent=COMPACTION_AGENT,
                    over_tokens=compaction.over_tokens,
                    hard_tokens=compaction.hard_tokens,
                    keep_last=compaction.keep_last,
                    fidelity_check=compaction.fidelity_check,
                ),
            )
            if compaction is not None
            else None
        )
        self._queue = (
            AsyncioTaskQueue(
                {"compaction": self._compacted},
                shutdown_timeout=config.execution.shutdown_timeout,
            )
            if self._compaction is not None
            else None
        )

    @classmethod
    def from_config(
        cls,
        path: Path | str,
        *,
        environ: Mapping[str, str] | None = None,
    ) -> Self:
        """Charge un fichier de configuration et ouvre l'instance."""
        return cls(load_config(Path(path)), environ=environ)

    # --- Ce que l'instance héberge -------------------------------------------

    @property
    def config(self) -> LoomConfig:
        return self._config

    @property
    def store(self) -> NotifyingEventStore:
        """Journal de l'instance, abonnable pendant qu'un run se déroule."""
        return self._store

    @property
    def artifacts(self) -> ArtifactStore:
        """Stockage des fichiers des runs de l'instance."""
        return self._artifacts

    @property
    def registry(self) -> Registry:
        return self._registry

    @property
    def agents(self) -> tuple[AgentSpec, ...]:
        return tuple(self._agents)

    @property
    def names(self) -> tuple[str, ...]:
        return self._agents.names

    def exposed(self, access: str) -> tuple[AgentSpec, ...]:
        """Agents publiés par un point d'accès (``rest`` ou ``mcp``)."""
        return self._agents.exposed("rest" if access == "rest" else "mcp")

    def register(self, name: str, obj: object) -> None:
        """Rend un objet Python référençable par son nom dans la config.

        À faire avant le premier run de l'agent qui s'en sert : les outils
        sont résolus au premier montage de l'agent, puis gardés.
        """
        self._registry.add(name, obj, source="register()")

    # --- Faire tourner un agent ----------------------------------------------

    async def run(
        self,
        agent: str,
        message: str | Message,
        *,
        attachments: Sequence[Attachment] = (),
        session_id: SessionId | None = None,
        context: CallerContext | None = None,
        run_id: RunId | None = None,
        on_chunk: ChunkCallback | None = None,
        judges: JudgesMode = "auto",
    ) -> RunResult:
        """Fait tourner un run jusqu'au bout et renvoie ce qu'il a produit.

        Une pièce jointe refusée (format, taille) lève ``AttachmentError``
        avant que le run ne commence. ``judges`` (#21) : ``auto``, chaque juge
        selon son ``when`` ; ``force``, tous (audit, évals) ; ``skip``, aucun.
        """
        ctx = self.context(agent, on_chunk=on_chunk)
        state = await self._start(ctx, message, attachments, session_id, context, run_id, judges)
        return await self._result(state)

    async def stream(
        self,
        agent: str,
        message: str | Message,
        *,
        attachments: Sequence[Attachment] = (),
        session_id: SessionId | None = None,
        context: CallerContext | None = None,
        run_id: RunId | None = None,
        subruns: bool = True,
        judges: JudgesMode = "auto",
    ) -> AsyncGenerator[StreamItem]:
        """Événements du journal et morceaux du modèle, dans l'ordre d'arrivée.

        Le run est lancé en tâche de fond ; abandonner l'itération l'annule.
        Son résultat se relit ensuite avec ``result(run_id)``. Avec
        ``subruns``, les événements des sous-runs sont mêlés au flux.
        ``judges`` : comme pour ``run``.
        """
        run_id = run_id or new_run_id()
        items: asyncio.Queue[StreamItem | None] = asyncio.Queue()

        async def on_chunk(chunk: ModelChunk) -> None:
            items.put_nowait(chunk)

        ctx = self.context(agent, on_chunk=on_chunk)
        tree = RunTree(run_id, subruns=subruns)
        # Écoute posée avant le démarrage : l'arbre se reconnaît dans l'ordre d'écriture.
        with self._store.listen(items.put_nowait, accept=tree.admit):
            task = asyncio.create_task(
                self._start(ctx, message, attachments, session_id, context, run_id, judges)
            )
            task.add_done_callback(lambda _: items.put_nowait(None))
            try:
                while (item := await items.get()) is not None:
                    yield item
            finally:
                if not task.done():
                    task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
            # Le run est terminé : une erreur de la boucle ressort ici.
            await task

    async def resume(
        self,
        run_id: RunId,
        *,
        session_id: SessionId | None = None,
        tenant_id: TenantId | None = None,
        on_chunk: ChunkCallback | None = None,
    ) -> RunResult:
        """Reprend un run interrompu, là où son journal s'est arrêté."""
        state = await self.state(run_id, session_id=session_id, tenant_id=tenant_id)
        ctx = self.context(state.agent, on_chunk=on_chunk)
        writer = await self._writer(state.context.tenant_id, state.session_id)
        return await self._result(await self._piloted(ctx, state, writer))

    def context(self, agent: str, *, on_chunk: ChunkCallback | None = None) -> RunContext:
        """Contexte d'exécution d'un agent, monté au premier appel.

        Le client de modèle et les outils sont gardés d'un run à l'autre ; le
        contexte est gelé, ``on_chunk`` en donne donc une copie.
        """
        built = self._built.get(agent)
        if built is None:
            built = build_agent(
                self._config,
                agent,
                self._store,
                registry=self._registry,
                environ=self._environ,
                mcp_pool=self._mcp,
                artifacts=self._artifacts,
                agents=self.context,
                breakers=self._breakers,
            )
            self._built[agent] = built
        if on_chunk is None:
            return built.context
        return replace(built.context, on_chunk=on_chunk)

    # --- Relire un run --------------------------------------------------------

    async def events(
        self,
        run_id: RunId,
        *,
        session_id: SessionId | None = None,
        tenant_id: TenantId | None = None,
        after_seq: int = 0,
        subruns: bool = True,
    ) -> list[Event]:
        """Événements d'un run dans l'ordre du journal, et ceux de ses sous-runs.

        ``subruns=False`` s'en tient au run. ``after_seq`` ne rend que la
        suite ; l'arbre, lui, se reconnaît depuis le début de la session.
        """
        tenant = tenant_id or DEFAULT_TENANT
        session = session_id or SessionId(run_id)
        if not subruns:
            own = RunTree(run_id, subruns=False)
            read = await self._store.read(tenant, session, after_seq=after_seq, run_id=run_id)
            return own.select(read)
        tree = RunTree(run_id)
        return [
            event
            for event in tree.select(await self._store.read(tenant, session))
            if event.seq > after_seq
        ]

    async def follow(
        self,
        run_id: RunId,
        *,
        session_id: SessionId | None = None,
        tenant_id: TenantId | None = None,
        after_seq: int = 0,
        subruns: bool = True,
    ) -> AsyncGenerator[Event]:
        """Événements déjà écrits, puis les suivants si le run tourne encore.

        Avec ``subruns``, ceux de ses sous-runs suivent aussi. L'itération
        s'arrête sur la clôture du run demandé, même si elle précède
        ``after_seq``. Un run inconnu lève ``UnknownRun``.
        """
        tenant = tenant_id or DEFAULT_TENANT
        session = session_id or SessionId(run_id)
        tree = RunTree(run_id, subruns=subruns)

        def in_session(event: Event) -> bool:
            return event.tenant_id == tenant and event.session_id == session

        # Abonnement posé avant la lecture : rien ne se perd entre les deux. Le
        # filtre de l'arbre s'applique ici, dans l'ordre du journal.
        async with self._store.subscribe(accept=in_session) as live:
            written = await self._store.read(tenant, session)
            if not any(event.run_id == run_id for event in written):
                raise UnknownRun(run_id)
            seen = 0
            for event in written:
                seen = event.seq
                if not tree.admit(event):
                    continue
                if event.seq > after_seq:
                    yield event
                if _closes(event, run_id):
                    return
            async for event in live:
                if event.seq <= seen or not tree.admit(event):
                    continue
                yield event
                if _closes(event, run_id):
                    return

    async def state(
        self,
        run_id: RunId,
        *,
        session_id: SessionId | None = None,
        tenant_id: TenantId | None = None,
    ) -> RunState:
        """État d'un run, reconstruit depuis son journal."""
        events = await self.events(
            run_id, session_id=session_id, tenant_id=tenant_id, subruns=False
        )
        if not events:
            raise UnknownRun(run_id)
        return fold(events, run_id)

    async def result(
        self,
        run_id: RunId,
        *,
        session_id: SessionId | None = None,
        tenant_id: TenantId | None = None,
    ) -> RunResult:
        """Ce qu'un run a produit, relu depuis son journal : réponse, coûts, verdicts."""
        tenant = tenant_id or DEFAULT_TENANT
        events = await self._store.read(tenant, session_id or SessionId(run_id))
        if not any(event.run_id == run_id for event in events):
            raise UnknownRun(run_id)
        return RunResult.of(fold(events, run_id), events)

    async def report(
        self,
        run_id: RunId | None = None,
        *,
        session_id: SessionId | None = None,
        tenant_id: TenantId | None = None,
    ) -> UsageReport:
        """Rapport de consommation (J5) : d'un run et de ses sous-runs, ou de toute une session.

        Sans ``run_id``, ``session_id`` est requis.
        """
        if run_id is None and session_id is None:
            raise ValueError("report() demande un run_id ou un session_id")
        tenant = tenant_id or DEFAULT_TENANT
        session = session_id or SessionId(str(run_id))
        events = await self._store.read(tenant, session)
        if run_id is not None and not any(e.run_id == run_id for e in events):
            raise UnknownRun(run_id)
        return usage_report(events, session, run_id)

    async def artifact(self, uri: str) -> bytes:
        """Octets d'un fichier du stockage ; lève ``ArtifactNotFound``."""
        return await self._artifacts.get(uri)

    # --- Sessions (F7) --------------------------------------------------------

    async def sessions(self, *, tenant_id: TenantId | None = None) -> list[SessionRecord]:
        """Sessions du client, de la plus récemment écrite à la plus ancienne."""
        return await self._store.sessions(tenant_id or DEFAULT_TENANT)

    async def export_session(
        self, session_id: SessionId, *, tenant_id: TenantId | None = None
    ) -> list[Event]:
        """Tous les événements d'une session, dans l'ordre du journal.

        Les fichiers n'y sont pas : chaque événement porte leur URI, et
        ``artifact(uri)`` en rend les octets.
        """
        events = await self._store.read(tenant_id or DEFAULT_TENANT, session_id)
        if not events:
            raise UnknownSession(session_id)
        return events

    async def delete_session(
        self, session_id: SessionId, *, tenant_id: TenantId | None = None
    ) -> SessionDeletion:
        """Supprime physiquement une session : ses fichiers, puis son journal (RGPD).

        Les fichiers partent d'abord : tant que le journal est là, on sait ce
        qu'il reste à retirer.
        """
        tenant = tenant_id or DEFAULT_TENANT
        artifacts = await self._artifacts.delete(tenant, session_id)
        events = await self._store.delete(tenant, session_id)
        self._writers.forget(tenant, session_id)
        return SessionDeletion(session_id=session_id, events=events, artifacts=artifacts)

    # --- Cycle de vie ---------------------------------------------------------

    async def aclose(self) -> None:
        """Ferme les clients de modèle, les connexions MCP, et les stockages venus de la config."""
        if self._queue is not None:
            # Les tâches de fond se servent des agents : on les attend d'abord.
            await self._queue.aclose()
        for built in self._built.values():
            await built.aclose()
        if self._mcp is not None:
            await self._mcp.aclose()
        self._built.clear()
        self._writers.clear()
        if self._owns_store:
            await self._store.aclose()
        if self._owns_artifacts:
            await self._artifacts.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def _piloted(
        self, ctx: RunContext, state: RunState, writer: SessionWriter | None
    ) -> RunState:
        """Pilote un run dans une tâche suivie, que ``cancel`` sait retrouver.

        Si l'appelant est annulé, la tâche l'est aussi : elle n'écrit rien de
        plus et le run reste reprenable. Seul ``cancel`` écrit ``run.cancelled``.
        """
        task = asyncio.create_task(
            drive(
                ctx,
                state.run_id,
                session_id=state.session_id,
                tenant_id=state.context.tenant_id,
                writer=writer,
            )
        )
        self._driving[state.run_id] = task
        try:
            return await task
        finally:
            self._driving.pop(state.run_id, None)
            if not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task

    async def cancel(
        self,
        run_id: RunId,
        *,
        session_id: SessionId | None = None,
        tenant_id: TenantId | None = None,
        by: str | None = None,
    ) -> bool:
        """Arrête un run et écrit ``run.cancelled`` (A5) ; faux s'il est déjà fini.

        Le run piloté ici est d'abord interrompu, puis clos au journal. Un run
        que cette instance ne pilote pas — repris ailleurs, ou laissé en plan
        par un plantage — est clos directement.

        Un run annulé est **terminal** : il ne se reprend pas. Un run seulement
        interrompu, lui, ne laisse rien au journal et repart où il en était.
        """
        task = self._driving.get(run_id)
        if task is not None and not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        state = await self.state(run_id, session_id=session_id, tenant_id=tenant_id)
        if state.finished:
            return False
        # ``state.session_id`` vaut le run_id pour un run anonyme : l'écrivain
        # partagé de l'instance vaut donc dans les deux cas.
        writer = await self._writers.open(self._store, state.context.tenant_id, state.session_id)
        await writer.append(cancellation(state, by=by))
        return True

    async def _result(self, state: RunState) -> RunResult:
        """Résultat d'un run qui vient de s'arrêter, avec son rapport et ses verdicts."""
        events = await self._store.read(state.context.tenant_id, state.session_id)
        await self._snapshot(state, events)
        await self._schedule(state, events)
        return RunResult.of(state, events)

    async def compact(
        self, session_id: SessionId, *, tenant_id: TenantId | None = None
    ) -> SessionCompacted | None:
        """Résume maintenant les échanges anciens d'une session (#23).

        Ne tient pas compte du seuil ``over_tokens`` : c'est la compaction à
        la demande. Rend ``None`` si la session n'a rien de neuf à résumer, ou
        si la configuration ne déclare pas de compaction.
        """
        if self._compaction is None:
            return None
        return await self._compaction.compact(tenant_id or DEFAULT_TENANT, session_id, limit=0)

    async def drain(self) -> None:
        """Attend les tâches de fond en cours (compaction)."""
        if self._queue is not None:
            await self._queue.drain()

    async def _compacted(self, job: Job) -> None:
        """Tâche de compaction, sortie de la file."""
        if self._compaction is not None:
            await self._compaction.compact(job.tenant_id, job.session_id, triggered_by=job.run_id)

    async def _schedule(self, state: RunState, events: Sequence[Event]) -> None:
        """Met un résumé en file si la session a dépassé son seuil (#23)."""
        if self._compaction is None or self._queue is None or not state.finished:
            return
        if state.parent_run_id is not None or state.kind != "normal":
            return
        up_to_seq = self._compaction.pending(events)
        if up_to_seq is None:
            return
        await self._queue.submit(
            Job(
                kind="compaction",
                tenant_id=state.context.tenant_id,
                session_id=state.session_id,
                run_id=state.run_id,
            ),
            key=self._compaction.key(state.session_id, up_to_seq),
        )

    async def _snapshot(self, state: RunState, events: Sequence[Event]) -> None:
        """Matérialise l'historique de la session, si le gain le justifie (§11.2).

        Le snapshot n'est qu'une vue : s'il ne peut pas être écrit, le run
        reste juste et la session se relit entièrement.
        """
        if state.parent_run_id is not None or not state.finished:
            return
        if state.session_id == SessionId(state.run_id):
            # Run sans session nommée : personne ne relira ce journal.
            return
        try:
            writer = await self._writers.open(
                self._store, state.context.tenant_id, state.session_id
            )
            await write_snapshot(writer, events, state, every=self._config.sessions.snapshot_every)
        except Exception:
            logger.warning(
                "Session %s : snapshot d'historique non écrit", state.session_id, exc_info=True
            )

    async def _writer(
        self, tenant_id: TenantId, session_id: SessionId | None
    ) -> SessionWriter | None:
        """Écrivain partagé d'une session nommée ; None pour un run anonyme."""
        if session_id is None:
            return None
        return await self._writers.open(self._store, tenant_id, session_id)

    async def _start(
        self,
        ctx: RunContext,
        message: str | Message,
        attachments: Sequence[Attachment],
        session_id: SessionId | None,
        context: CallerContext | None,
        run_id: RunId | None,
        judges: JudgesMode = "auto",
    ) -> RunState:
        tenant = (context or CallerContext()).tenant_id
        if self._compaction is not None and session_id is not None:
            # Filet de sécurité : une session trop longue est résumée avant
            # que le run ne commence (#23).
            await self._compaction.ensure_fits(tenant, session_id)
        writer = await self._writer(tenant, session_id)
        state = await begin_run(
            ctx,
            message,
            attachments=attachments,
            session_id=session_id,
            context=context,
            run_id=run_id,
            judges=judges,
            writer=writer,
        )
        return await self._piloted(ctx, state, writer)


def _closes(event: Event, run_id: RunId) -> bool:
    """Vrai sur la clôture du run suivi (celle d'un sous-run ne compte pas)."""
    return event.run_id == run_id and is_final(event)
