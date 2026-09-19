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

Fichiers (G1 à G3) : ``run`` et ``stream`` acceptent des pièces jointes
(``Attachment``), validées puis rangées dans le stockage d'artefacts de
l'instance ; ``RunResult.artifacts`` liste les fichiers du run, et
``artifact(uri)`` en rend les octets.

    photo = Attachment.from_path("photo.jpg")
    result = await loom.run("assistant", "Que montre la photo ?", attachments=[photo])
"""

import asyncio
from collections.abc import AsyncGenerator, Mapping, Sequence
from contextlib import suppress
from dataclasses import replace
from pathlib import Path
from typing import Self

from loom_ia.adapters.stores import NotifyingEventStore
from loom_ia.agents.registry import AgentRegistry
from loom_ia.agents.spec import AgentSpec
from loom_ia.config import LoomConfig, load_config
from loom_ia.config.references import Registry
from loom_ia.core.events import Event, RunCompleted, RunFailed
from loom_ia.core.model import (
    DEFAULT_TENANT,
    ArtifactRecord,
    Attachment,
    CallerContext,
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
from loom_ia.core.ports import ArtifactStore, ChunkCallback, EventStore
from loom_ia.core.projections import fold
from loom_ia.engine import RunContext, begin_run, drive
from loom_ia.runtime import (
    Agent,
    build_agent,
    create_artifact_store,
    create_event_store,
    create_mcp_pool,
    load_registry,
)

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


class RunResult(DomainModel):
    """Ce qu'un run a produit, tiré de son état final."""

    run_id: RunId
    session_id: SessionId
    agent: str
    status: RunStatus
    text: str = ""
    output: Message | None = None
    error: str | None = None
    iterations: int = 0
    usage: Usage = Usage()
    cost_usd: float = 0.0
    # Fichiers du run : pièces jointes, fichiers produits par les outils, déports (G3).
    artifacts: tuple[ArtifactRecord, ...] = ()

    @classmethod
    def of(cls, state: RunState) -> Self:
        return cls(
            run_id=state.run_id,
            session_id=state.session_id,
            agent=state.agent,
            status=state.status,
            text=state.output.text if state.output else "",
            output=state.output,
            error=state.error,
            iterations=state.iterations,
            usage=state.usage,
            cost_usd=state.cost_usd,
            artifacts=state.artifacts,
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
    ) -> RunResult:
        """Fait tourner un run jusqu'au bout et renvoie ce qu'il a produit.

        Une pièce jointe refusée (format, taille) lève ``AttachmentError``
        avant que le run ne commence.
        """
        ctx = self.context(agent, on_chunk=on_chunk)
        state = await self._start(ctx, message, attachments, session_id, context, run_id)
        return RunResult.of(state)

    async def stream(
        self,
        agent: str,
        message: str | Message,
        *,
        attachments: Sequence[Attachment] = (),
        session_id: SessionId | None = None,
        context: CallerContext | None = None,
        run_id: RunId | None = None,
    ) -> AsyncGenerator[StreamItem]:
        """Événements du journal et morceaux du modèle, dans l'ordre d'arrivée.

        Le run est lancé en tâche de fond ; abandonner l'itération l'annule.
        Son résultat se relit ensuite avec ``result(run_id)``.
        """
        run_id = run_id or new_run_id()
        items: asyncio.Queue[StreamItem | None] = asyncio.Queue()

        async def on_chunk(chunk: ModelChunk) -> None:
            items.put_nowait(chunk)

        ctx = self.context(agent, on_chunk=on_chunk)
        with self._store.listen(items.put_nowait, run_id):
            task = asyncio.create_task(
                self._start(ctx, message, attachments, session_id, context, run_id)
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
        final = await drive(
            ctx,
            run_id,
            session_id=state.session_id,
            tenant_id=state.context.tenant_id,
        )
        return RunResult.of(final)

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
    ) -> list[Event]:
        """Événements d'un run, dans l'ordre du journal."""
        return await self._store.read(
            tenant_id or DEFAULT_TENANT,
            session_id or SessionId(run_id),
            after_seq=after_seq,
            run_id=run_id,
        )

    async def follow(
        self,
        run_id: RunId,
        *,
        session_id: SessionId | None = None,
        tenant_id: TenantId | None = None,
        after_seq: int = 0,
    ) -> AsyncGenerator[Event]:
        """Événements déjà écrits, puis les suivants si le run tourne encore.

        L'itération s'arrête sur la clôture du run. Un run inconnu lève
        ``UnknownRun``.
        """
        tenant = tenant_id or DEFAULT_TENANT
        session = session_id or SessionId(run_id)
        async with self._store.subscribe(run_id) as live:
            written = await self._store.read(tenant, session, after_seq=after_seq, run_id=run_id)
            if not written and not await self._store.read(tenant, session, run_id=run_id):
                raise UnknownRun(run_id)
            seen = after_seq
            for event in written:
                seen = event.seq
                yield event
                if is_final(event):
                    return
            async for event in live:
                if event.seq <= seen:
                    continue
                yield event
                if is_final(event):
                    return

    async def state(
        self,
        run_id: RunId,
        *,
        session_id: SessionId | None = None,
        tenant_id: TenantId | None = None,
    ) -> RunState:
        """État d'un run, reconstruit depuis son journal."""
        events = await self.events(run_id, session_id=session_id, tenant_id=tenant_id)
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
        """Ce qu'un run a produit, relu depuis son journal."""
        state = await self.state(run_id, session_id=session_id, tenant_id=tenant_id)
        return RunResult.of(state)

    async def artifact(self, uri: str) -> bytes:
        """Octets d'un fichier du stockage ; lève ``ArtifactNotFound``."""
        return await self._artifacts.get(uri)

    # --- Cycle de vie ---------------------------------------------------------

    async def aclose(self) -> None:
        """Ferme les clients de modèle, les connexions MCP, et les stockages venus de la config."""
        for built in self._built.values():
            await built.aclose()
        if self._mcp is not None:
            await self._mcp.aclose()
        self._built.clear()
        if self._owns_store:
            await self._store.aclose()
        if self._owns_artifacts:
            await self._artifacts.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def _start(
        self,
        ctx: RunContext,
        message: str | Message,
        attachments: Sequence[Attachment],
        session_id: SessionId | None,
        context: CallerContext | None,
        run_id: RunId | None,
    ) -> RunState:
        state = await begin_run(
            ctx,
            message,
            attachments=attachments,
            session_id=session_id,
            context=context,
            run_id=run_id,
        )
        return await drive(
            ctx,
            state.run_id,
            session_id=state.session_id,
            tenant_id=state.context.tenant_id,
        )
