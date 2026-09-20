# SPDX-License-Identifier: Apache-2.0
"""Exécution des appels d'outils d'un tour (#12, #15, #18, D1, D2, D4, D5).

Chaîne d'un appel : outil connu, reprise sans risque, arguments lisibles,
références ``$ref`` résolues, arguments conformes au schéma, refus éventuel
d'un outil délégué, puis exécution avec timeout. Tout échec devient un
résultat d'erreur destiné au modèle ; seule l'annulation interrompt le lot.

``tool.called`` n'est écrit que pour un appel réellement lancé : c'est la
marque qu'un effet de bord a peut-être eu lieu. Tous les ``tool.called`` du
lot sont écrits avant la première exécution, puis chaque ``tool.completed``
dès que son outil répond.

Un outil délégué (rôle) produit aussi des événements pendant son appel. Ils
passent par la même file que les résultats : ils précèdent toujours le
``tool.completed`` de leur appel. Le délai par défaut des outils ne
s'applique pas à eux : les délais et le retry de leur modèle les bornent.
Un sous-agent écrit son propre run, par l'écrivain de la session ; son
``tool.called`` nomme ce run enfant, et son ``tool.completed`` porte sa
consommation.

Fichiers (#15, #16) : avant d'écrire un résultat, les octets qu'il porte
(image renvoyée par un outil) sont rangés dans le stockage d'artefacts et
remplacés par leur référence, puis un résultat trop long est déporté. Chaque
fichier rangé donne un ``artifact.stored``, écrit avant le ``tool.completed``
de l'appel. Un outil délégué indisponible dans le run (rôle vision sans pièce
jointe, ``artifact_read`` sans déport) n'est ni montré au modèle ni appelable.

Politiques (#2) : ``before_tool`` s'applique à un appel accepté, avant tout
lancement (arguments remplacés puis validés de nouveau, appel refusé, échec
du run) ; ``after_tool`` au résultat, avant ses fichiers et son déport
(résultat remplacé, refusé ou échec du run). Chaque décision part dans la file
du lot avant l'événement qu'elle concerne. Un appel déjà lancé et repris
après une interruption n'est pas réévalué : ses arguments remplacés sont
repris du journal. Un résultat refusé (``Retry``) est réparé par son auteur :
un rôle par son propre modèle, dans la même conversation (``repair``),
tant que la politique le demande ; les réparations se comptent par appel.
Un outil qui ne se répare pas renvoie son résultat à l'orchestrateur en
erreur, avec le diagnostic : c'est lui qui a écrit l'appel.

Diffusion (backlog #009) : un rôle terminal seul dans son lot diffuse sa
sortie en direct si l'agent diffuse en ``live`` (``on_chunk``).
"""

import asyncio
import logging
import time
from collections.abc import AsyncGenerator, Callable, Iterable, Mapping
from contextlib import AsyncExitStack, aclosing, asynccontextmanager
from dataclasses import dataclass, replace
from typing import Final

from jsonschema import Draft202012Validator, SchemaError
from jsonschema.protocols import Validator
from jsonschema.validators import validator_for
from pydantic import JsonValue

from loom_ia.core.events import (
    ArtifactStored,
    GuardChecked,
    PolicyDecided,
    ToolCalled,
    ToolCompleted,
    ToolSourceUnavailable,
)
from loom_ia.core.model import (
    CONTINUE,
    INVALID_JSON_KEY,
    AfterTool,
    ArtifactRefBlock,
    BeforeTool,
    Deny,
    Fail,
    InlineDataBlock,
    OutputBlock,
    PendingCall,
    Retry,
    RunId,
    RunState,
    SpanId,
    TextBlock,
    ToolDefinition,
    ToolOutput,
    ToolSpec,
    artifact_uri,
    extension,
    has_inline_data,
    sniff,
)
from loom_ia.core.ports import (
    ArtifactStore,
    ChunkCallback,
    SourceContext,
    SourceUnavailable,
    Tool,
    ToolContext,
    ToolError,
    ToolSource,
)
from loom_ia.engine.delegated import (
    Consumption,
    DelegatedPayload,
    DelegatedTool,
    Exchange,
    RunView,
)
from loom_ia.engine.hooks import Policies, Verdict
from loom_ia.engine.media import size_label
from loom_ia.engine.offload import (
    DEFAULT_OFFLOAD_OVER,
    ArtifactReadTool,
    full_content,
    offloaded,
    truncated,
    visible_text,
)
from loom_ia.engine.refs import RefError
from loom_ia.engine.writer import SessionWriter

logger = logging.getLogger(__name__)

DEFAULT_TOOL_TIMEOUT: Final = 30.0

UNKNOWN_STATE: Final = (
    "État inconnu : l'exécution de cet outil a été interrompue et il a peut-être "
    "produit son effet. Il n'a pas été relancé automatiquement ; vérifie avant de "
    "le rappeler."
)

type AnyTool = Tool | DelegatedTool


@dataclass(frozen=True, slots=True)
class Delegated:
    """Événement produit par un outil délégué pendant son appel."""

    call_id: str
    # Rôle qui a produit l'événement, recopié dans l'enveloppe.
    role: str
    payload: DelegatedPayload


@dataclass(frozen=True, slots=True)
class Stored:
    """Fichier rangé pendant un appel : sortie d'outil ou résultat déporté."""

    call_id: str
    payload: ArtifactStored


@dataclass(frozen=True, slots=True)
class Decided:
    """Décision ou contrôle d'une politique d'outil, à écrire avant l'événement concerné."""

    call_id: str
    payload: PolicyDecided | GuardChecked


type ToolEvent = ToolCalled | ToolCompleted | Stored | Delegated | Decided


@dataclass(frozen=True, slots=True)
class _Ready:
    """Appel accepté, prêt à partir."""

    call: PendingCall
    tool: AnyTool
    # Arguments après résolution des références.
    arguments: dict[str, JsonValue]
    refs: tuple[str, ...]
    # Run enfant d'un sous-agent.
    child_run_id: RunId | None = None


@dataclass(frozen=True, slots=True)
class OpenedTools:
    """Outils d'un run : ceux de l'agent, plus ceux de ses sources disponibles."""

    tools: ToolExecutor
    # Sources injoignables à l'ouverture, à journaliser.
    unavailable: tuple[ToolSourceUnavailable, ...] = ()


@dataclass(frozen=True, slots=True)
class _Crashed:
    """Exception sortie d'une tâche d'exécution : elle interrompt le lot."""

    error: BaseException


class ToolExecutor:
    """Outils disponibles pour un run, et exécution de leurs appels.

    ``artifacts`` est le stockage des fichiers du run : pièces jointes, sorties
    d'outils, résultats déportés. Avec lui, l'outil intégré ``artifact_read``
    est déclaré. ``offload_over`` est le seuil de déport des outils qui n'en
    fixent pas ; ``None`` le désactive.
    """

    def __init__(
        self,
        tools: Iterable[AnyTool] = (),
        *,
        sources: Iterable[ToolSource] = (),
        default_timeout: float | None = DEFAULT_TOOL_TIMEOUT,
        validate_arguments: bool = True,
        artifacts: ArtifactStore | None = None,
        offload_over: int | None = DEFAULT_OFFLOAD_OVER,
    ) -> None:
        self.default_timeout = default_timeout
        self.validate_arguments = validate_arguments
        self.artifacts = artifacts
        self.offload_over = offload_over
        # Sources ouvertes au début de chaque run (serveurs MCP, #19).
        self.sources: tuple[ToolSource, ...] = tuple(sources)
        self._tools: dict[str, AnyTool] = {}
        self._validators: dict[str, Validator] = {}
        for tool in tools:
            self.add(tool)
        if artifacts is not None:
            self.add(ArtifactReadTool())

    @asynccontextmanager
    async def opened(self, context: SourceContext) -> AsyncGenerator[OpenedTools]:
        """Outils du run, jusqu'à la sortie du contexte.

        Chaque source est ouverte dans l'ordre de déclaration. Une source
        injoignable est écartée et signalée, les autres restent utilisables.
        Un outil de source dont le nom ou le schéma pose problème est écarté,
        avec un avertissement.
        """
        if not self.sources:
            yield OpenedTools(tools=self)
            return
        async with AsyncExitStack() as stack:
            tools = self._copy()
            unavailable: list[ToolSourceUnavailable] = []
            for source in self.sources:
                try:
                    provided = await stack.enter_async_context(source.open(context))
                except Exception as exc:
                    message = exc.message if isinstance(exc, SourceUnavailable) else repr(exc)
                    logger.warning(
                        "Source d'outils %s indisponible : %s",
                        source.name,
                        message,
                        exc_info=not isinstance(exc, SourceUnavailable),
                        extra={"run_id": context.run_id},
                    )
                    unavailable.append(
                        ToolSourceUnavailable(
                            source=source.name, error=message, required=source.required
                        )
                    )
                    continue
                for tool in provided:
                    try:
                        tools.add(tool)
                    except (ValueError, SchemaError) as exc:
                        logger.warning(
                            "Outil %s de la source %s écarté : %s",
                            tool.spec.name,
                            source.name,
                            exc,
                            extra={"run_id": context.run_id},
                        )
            yield OpenedTools(tools=tools, unavailable=tuple(unavailable))

    def _copy(self) -> ToolExecutor:
        """Mêmes outils et réglages, sans les sources : base des outils d'un run."""
        copy = ToolExecutor(
            default_timeout=self.default_timeout,
            validate_arguments=self.validate_arguments,
            artifacts=self.artifacts,
            offload_over=self.offload_over,
        )
        copy._tools = dict(self._tools)
        copy._validators = dict(self._validators)
        return copy

    def add(self, tool: AnyTool) -> None:
        spec = tool.spec
        if spec.name in self._tools:
            raise ValueError(f"Outil {spec.name!r} déjà déclaré")
        cls = validator_for(spec.input_schema, default=Draft202012Validator)
        cls.check_schema(spec.input_schema)
        self._tools[spec.name] = tool
        self._validators[spec.name] = cls(spec.input_schema)

    def get(self, name: str) -> AnyTool | None:
        return self._tools.get(name)

    def shows_refs(self, run: RunView) -> bool:
        """Vrai si les références ``$ref`` sont montrées : un rôle ou un sous-agent est proposé.

        Un outil masqué (rôle vision sans pièce jointe, sous-agent au-delà de la
        profondeur permise) ne compte pas.
        """
        return any(
            tool.spec.kind in {"role", "agent"} and _offered(tool, run)
            for tool in self._tools.values()
        )

    @property
    def specs(self) -> tuple[ToolSpec, ...]:
        return tuple(tool.spec for tool in self._tools.values())

    def view(
        self,
        state: RunState,
        *,
        writer: SessionWriter | None = None,
        spans: Mapping[str, SpanId] | None = None,
    ) -> RunView:
        """Ce que les outils délégués voient du run."""
        return RunView.of(state, self.artifacts, writer=writer, spans=spans)

    def definitions(self, run: RunView | None = None) -> tuple[ToolDefinition, ...]:
        """Ce que le modèle voit des outils, dans l'ordre de déclaration.

        Avec ``run``, les outils délégués indisponibles dans ce run sont écartés.
        """
        return tuple(
            tool.spec.definition()
            for tool in self._tools.values()
            if run is None or _offered(tool, run)
        )

    async def run_batch(
        self,
        state: RunState,
        *,
        writer: SessionWriter | None = None,
        spans: Mapping[str, SpanId] | None = None,
        policies: Policies | None = None,
        on_chunk: ChunkCallback | None = None,
    ) -> AsyncGenerator[ToolEvent]:
        """Traite les appels en attente du run et émet leurs événements.

        ``writer`` et ``spans`` servent aux sous-agents : le journal où écrire
        leur run, et le span de leur appel. ``policies`` : celles de l'agent.
        Une décision ``Fail`` à ``before_tool`` arrête le lot avant tout
        lancement. ``on_chunk`` : diffusion en direct, pour un rôle terminal
        seul dans son lot.
        """
        policies = policies or Policies()
        view = self.view(state, writer=writer, spans=spans)
        if on_chunk is not None and len(state.pending_calls) == 1:
            alone = self._tools.get(state.pending_calls[0].name)
            if isinstance(alone, DelegatedTool) and alone.spec.terminal:
                view = replace(view, on_chunk=on_chunk)
        ready: list[_Ready] = []
        for call in state.pending_calls:
            tool = self._tools.get(call.name)
            prepared = (
                self._unknown_tool(call.name, view)
                if tool is None or not _offered(tool, view)
                else await self._prepare(call, tool, view)
            )
            if isinstance(prepared, _Ready):
                verdict = await self._before_tool(prepared, view, policies)
                for decided in verdict.decided:
                    yield Decided(call_id=call.call_id, payload=decided)
                match verdict.decision:
                    case Fail():
                        return
                    case Deny(reason=reason):
                        text = f"Appel refusé ({verdict.by}) : {reason}"
                        yield _completed(call, ToolOutput.error(text), started=None)
                        continue
                    case _:
                        pass
                if isinstance(verdict.subject, BeforeTool):
                    prepared = replace(prepared, arguments=verdict.subject.arguments)
                ready.append(prepared)
            else:
                yield _completed(call, ToolOutput.error(prepared), started=None)

        for item in ready:
            yield ToolCalled(
                call_id=item.call.call_id,
                tool_name=item.call.name,
                tool_kind=item.tool.spec.kind,
                arguments=item.call.arguments,
                refs=item.refs,
                resumed=item.call.started,
                child_run_id=item.child_run_id,
            )
        children = {i.call.call_id: i.child_run_id for i in ready if i.child_run_id is not None}
        if children:
            view = replace(view, children=children)

        queue: asyncio.Queue[ToolEvent | _Crashed] = asyncio.Queue()

        def crashed(task: asyncio.Task[None]) -> None:
            if not task.cancelled() and (error := task.exception()) is not None:
                queue.put_nowait(_Crashed(error))

        tasks = [
            asyncio.create_task(self._execute(item, view, queue.put_nowait, policies))
            for item in ready
        ]
        for task in tasks:
            task.add_done_callback(crashed)
        try:
            remaining = len(tasks)
            while remaining:
                event = await queue.get()
                if isinstance(event, _Crashed):
                    raise event.error
                if isinstance(event, ToolCompleted):
                    remaining -= 1
                yield event
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    # --- Interne ---------------------------------------------------------

    def _unknown_tool(self, name: str, view: RunView) -> str:
        offered = [tool.spec.name for tool in self._tools.values() if _offered(tool, view)]
        return f"Outil inconnu : {name!r}. Outils disponibles : {', '.join(offered) or 'aucun'}."

    async def _prepare(self, call: PendingCall, tool: AnyTool, view: RunView) -> _Ready | str:
        """Appel prêt à partir, ou motif de refus destiné au modèle."""
        if call.started and not tool.spec.safe_to_retry:
            return UNKNOWN_STATE
        if set(call.arguments) == {INVALID_JSON_KEY}:
            raw = str(call.arguments[INVALID_JSON_KEY])
            return f"Arguments illisibles : ce n'est pas un objet JSON valide.\nReçu : {raw[:500]}"
        try:
            arguments, refs = await view.results.resolve(call.arguments)
        except RefError as exc:
            return exc.message
        if self.validate_arguments:
            problem = self._schema_errors(call.name, arguments)
            if problem is not None:
                return problem
        child: RunId | None = None
        if isinstance(tool, DelegatedTool):
            problem = await tool.check(arguments, view)
            if problem is not None:
                return problem
            child = tool.child_run_id(call)
        return _Ready(call=call, tool=tool, arguments=arguments, refs=refs, child_run_id=child)

    async def _before_tool(self, item: _Ready, view: RunView, policies: Policies) -> Verdict:
        """Politiques ``before_tool`` d'un appel accepté.

        Un appel repris après une interruption n'est pas réévalué : ses
        arguments remplacés sont repris tels qu'ils ont été journalisés.
        """
        call = item.call
        subject = BeforeTool(
            state=view.state, call=call, spec=item.tool.spec, arguments=item.arguments
        )
        if call.started:
            if call.replaced_arguments is not None:
                subject = BeforeTool(
                    state=view.state,
                    call=call,
                    spec=item.tool.spec,
                    arguments=call.replaced_arguments,
                )
            return Verdict(CONTINUE, subject)
        return await policies.run(
            subject,
            call_id=call.call_id,
            check_arguments=lambda arguments: self._schema_errors(call.name, arguments),
        )

    def _schema_errors(self, name: str, arguments: dict[str, JsonValue]) -> str | None:
        errors = sorted(
            self._validators[name].iter_errors(arguments),
            key=lambda e: [str(p) for p in e.absolute_path],
        )
        if not errors:
            return None
        lines = ["Arguments non conformes au schéma de l'outil :"]
        for error in errors:
            location = ".".join(str(p) for p in error.absolute_path) or "(racine)"
            lines.append(f"- {location} : {error.message}")
        return "\n".join(lines)

    async def _execute(
        self,
        item: _Ready,
        view: RunView,
        emit: Callable[[ToolEvent], None],
        policies: Policies | None = None,
    ) -> None:
        """Exécute un appel ; ses événements et son résultat partent dans la file du lot."""
        tool, call, state = item.tool, item.call, view.state
        spec = tool.spec
        delegated = isinstance(tool, DelegatedTool)
        timeout = spec.timeout if spec.timeout is not None or delegated else self.default_timeout
        context = ToolContext(
            tenant_id=state.context.tenant_id,
            session_id=state.session_id,
            run_id=state.run_id,
            call_id=call.call_id,
            agent=state.agent,
            caller=state.context,
        )
        started = time.perf_counter()
        consumption: Consumption | None = None
        exchange: Exchange | None = None
        if isinstance(tool, DelegatedTool):
            produced = tool.run(item.arguments, context, view)
            output, consumption, exchange = await _delegated(
                produced, tool, context, emit, timeout, state
            )
        else:
            output = await _invoked(tool, item.arguments, context, timeout, state)
        if policies:
            output = await self._after_tool(item, output, exchange, context, view, emit, policies)
        output = await self._settle(output, spec, call.call_id, view, emit)
        emit(_completed(call, output, started=started, consumption=consumption))

    async def _after_tool(
        self,
        item: _Ready,
        output: ToolOutput,
        exchange: Exchange | None,
        context: ToolContext,
        view: RunView,
        emit: Callable[[ToolEvent], None],
        policies: Policies,
    ) -> ToolOutput:
        """Politiques ``after_tool`` : résultat gardé, remplacé, réparé ou refusé.

        Un rôle refusé (``Retry``) est réparé par son modèle, puis contrôlé de
        nouveau ; ses réparations se comptent dans l'appel. Un ``Fail`` laisse
        le résultat tel quel : le run échouera à la fin du lot.
        """
        call, tool = item.call, item.tool
        attempts: dict[str, int] = {}
        while True:
            verdict = await policies.run(
                AfterTool(
                    state=view.state,
                    call=call,
                    spec=tool.spec,
                    arguments=item.arguments,
                    output=output,
                ),
                call_id=call.call_id,
                attempts=attempts,
            )
            for event in verdict.events:
                emit(Decided(call_id=call.call_id, payload=event))
            match verdict.decision:
                case Retry(feedback=feedback) if verdict.by is not None:
                    repaired = (
                        tool.repair(
                            exchange, feedback, policy=verdict.by, context=context, run=view
                        )
                        if isinstance(tool, DelegatedTool) and exchange is not None
                        else None
                    )
                    if repaired is None or not isinstance(tool, DelegatedTool):
                        refused = TextBlock(text=f"Résultat refusé ({verdict.by}) : {feedback}")
                        return output.model_copy(
                            update={
                                "blocks": (refused, *output.blocks),
                                "is_error": True,
                                "data": None,
                            }
                        )
                    attempts[verdict.by] = attempts.get(verdict.by, 0) + 1
                    output, _, exchange = await _delegated(
                        repaired, tool, context, emit, tool.spec.timeout, view.state
                    )
                    continue
                case Fail():
                    return output
                case _:
                    pass
            if isinstance(verdict.subject, AfterTool):
                return verdict.subject.output
            return output

    async def _settle(
        self,
        output: ToolOutput,
        spec: ToolSpec,
        call_id: str,
        view: RunView,
        emit: Callable[[ToolEvent], None],
    ) -> ToolOutput:
        """Résultat prêt pour le journal : fichiers rangés, contenu trop long déporté."""
        state = view.state
        try:
            if has_inline_data(output.blocks):
                output = await self._store_files(output, call_id, state, emit)
            limit = spec.offload_over or self.offload_over
            if (
                limit is not None
                and not spec.terminal
                and spec.kind != "builtin"
                and len(visible_text(output)) > limit
            ):
                output = await self._offload(output, limit, spec, call_id, view, emit)
        except Exception as exc:
            logger.warning(
                "Résultat de l'outil %s non conservé",
                spec.name,
                exc_info=exc,
                extra={"run_id": state.run_id},
            )
            return ToolOutput.error(
                f"Résultat de l'outil {spec.name} perdu : échec du stockage d'artefacts "
                f"({type(exc).__name__}: {exc})."
            )
        return output

    async def _store_files(
        self,
        output: ToolOutput,
        call_id: str,
        state: RunState,
        emit: Callable[[ToolEvent], None],
    ) -> ToolOutput:
        """Octets des blocs rangés dans le stockage, remplacés par leur référence (G3)."""
        blocks: list[OutputBlock] = []
        uris = list(output.artifacts)
        for block in output.blocks:
            if not isinstance(block, InlineDataBlock):
                blocks.append(block)
                continue
            size = len(block.data)
            if self.artifacts is None:
                blocks.append(
                    TextBlock(
                        text=f"[fichier {block.media_type} de {size_label(size)} non conservé : "
                        "aucun stockage d'artefacts]"
                    )
                )
                continue
            media_type = sniff(block.data) or block.media_type
            uri = artifact_uri(state.context.tenant_id, state.session_id, block.data, media_type)
            await self.artifacts.put(uri, block.data)
            stored = ArtifactStored(
                uri=uri,
                media_type=media_type,
                size=size,
                name=block.name,
                origin="tool_output",
                call_id=call_id,
            )
            emit(Stored(call_id=call_id, payload=stored))
            blocks.append(
                ArtifactRefBlock(uri=uri, media_type=media_type, size=size, name=block.name)
            )
            if uri not in uris:
                uris.append(uri)
        return output.model_copy(update={"blocks": tuple(blocks), "artifacts": tuple(uris)})

    async def _offload(
        self,
        output: ToolOutput,
        limit: int,
        spec: ToolSpec,
        call_id: str,
        view: RunView,
        emit: Callable[[ToolEvent], None],
    ) -> ToolOutput:
        """Contenu complet rangé à part, aperçu à la place (#16) ; tronqué sans stockage."""
        if self.artifacts is None:
            return truncated(output, limit)
        state = view.state
        content, media_type = full_content(output)
        data = content.encode()
        uri = artifact_uri(state.context.tenant_id, state.session_id, data, media_type)
        await self.artifacts.put(uri, data)
        stored = ArtifactStored(
            uri=uri,
            media_type=media_type,
            size=len(data),
            name=f"{spec.name}.{extension(media_type)}",
            origin="offload",
            call_id=call_id,
        )
        emit(Stored(call_id=call_id, payload=stored))
        return offloaded(output, uri=uri, content=content, refs=self.shows_refs(view))


async def _invoked(
    tool: Tool,
    arguments: dict[str, JsonValue],
    context: ToolContext,
    limit: float | None,
    state: RunState,
) -> ToolOutput:
    """Exécute un outil ordinaire avec son délai ; toute erreur devient un résultat d'erreur."""
    scope = asyncio.timeout(limit)
    try:
        async with scope:
            return await tool.invoke(arguments, context)
    except TimeoutError as exc:
        if scope.expired():
            return ToolOutput.error(f"Délai dépassé : pas de réponse en {limit:g} s.")
        # TimeoutError levée par l'outil lui-même.
        return _unexpected(tool.spec.name, exc, state)
    except ToolError as exc:
        return ToolOutput.error(exc.message)
    except Exception as exc:
        return _unexpected(tool.spec.name, exc, state)


async def _delegated(
    produced: AsyncGenerator[DelegatedPayload | Consumption | Exchange | ToolOutput],
    tool: DelegatedTool,
    context: ToolContext,
    emit: Callable[[ToolEvent], None],
    limit: float | None,
    state: RunState,
) -> tuple[ToolOutput, Consumption | None, Exchange | None]:
    """Déroule un outil délégué (appel ou réparation) avec son délai.

    Ses événements sont émis ; son résultat, sa consommation et son échange
    sont renvoyés. Toute erreur devient un résultat d'erreur.
    """
    output: ToolOutput | None = None
    consumption: Consumption | None = None
    exchange: Exchange | None = None
    scope = asyncio.timeout(limit)
    try:
        async with scope, aclosing(produced) as items:
            async for item in items:
                match item:
                    case ToolOutput():
                        output = item
                    case Consumption():
                        consumption = item
                    case Exchange():
                        exchange = item
                    case _:
                        emit(Delegated(call_id=context.call_id, role=tool.spec.name, payload=item))
    except TimeoutError as exc:
        if scope.expired():
            output = ToolOutput.error(f"Délai dépassé : pas de réponse en {limit:g} s.")
        else:
            output = _unexpected(tool.spec.name, exc, state)
    except ToolError as exc:
        output = ToolOutput.error(exc.message)
    except Exception as exc:
        output = _unexpected(tool.spec.name, exc, state)
    if output is None:
        output = _unexpected(
            tool.spec.name,
            RuntimeError(f"Outil délégué {tool.spec.name} terminé sans résultat"),
            state,
        )
    return output, consumption, exchange


def _offered(tool: AnyTool, run: RunView) -> bool:
    """Vrai si l'outil est montré au modèle et appelable dans ce run."""
    return not isinstance(tool, DelegatedTool) or tool.available(run)


def _unexpected(name: str, exc: Exception, state: RunState) -> ToolOutput:
    logger.warning("Échec de l'outil %s", name, exc_info=exc, extra={"run_id": state.run_id})
    return ToolOutput.error(f"Erreur de l'outil {name} : {type(exc).__name__}: {exc}")


def _completed(
    call: PendingCall,
    output: ToolOutput,
    *,
    started: float | None,
    consumption: Consumption | None = None,
) -> ToolCompleted:
    latency = 0.0 if started is None else (time.perf_counter() - started) * 1000
    return ToolCompleted(
        call_id=call.call_id,
        tool_name=call.name,
        output=output,
        latency_ms=latency,
        size=len(output.model_dump_json().encode()),
        usage=consumption.usage if consumption is not None else None,
        cost_usd=consumption.cost_usd if consumption is not None else 0.0,
    )
