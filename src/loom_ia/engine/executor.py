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
"""

import asyncio
import logging
import time
from collections.abc import AsyncGenerator, Callable, Iterable
from contextlib import aclosing
from dataclasses import dataclass
from typing import Final

from jsonschema import Draft202012Validator
from jsonschema.protocols import Validator
from jsonschema.validators import validator_for
from pydantic import JsonValue

from loom_ia.core.events import ToolCalled, ToolCompleted
from loom_ia.core.model import (
    INVALID_JSON_KEY,
    PendingCall,
    RunState,
    ToolDefinition,
    ToolOutput,
    ToolSpec,
)
from loom_ia.core.ports import Tool, ToolContext, ToolError
from loom_ia.engine.delegated import DelegatedPayload, DelegatedTool, RunView
from loom_ia.engine.refs import RefError

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


type ToolEvent = ToolCalled | ToolCompleted | Delegated


@dataclass(frozen=True, slots=True)
class _Ready:
    """Appel accepté, prêt à partir."""

    call: PendingCall
    tool: AnyTool
    # Arguments après résolution des références.
    arguments: dict[str, JsonValue]
    refs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _Crashed:
    """Exception sortie d'une tâche d'exécution : elle interrompt le lot."""

    error: BaseException


class ToolExecutor:
    """Outils disponibles pour un run, et exécution de leurs appels."""

    def __init__(
        self,
        tools: Iterable[AnyTool] = (),
        *,
        default_timeout: float | None = DEFAULT_TOOL_TIMEOUT,
        validate_arguments: bool = True,
    ) -> None:
        self.default_timeout = default_timeout
        self.validate_arguments = validate_arguments
        self._tools: dict[str, AnyTool] = {}
        self._validators: dict[str, Validator] = {}
        for tool in tools:
            self.add(tool)

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

    @property
    def has_delegated(self) -> bool:
        """Vrai si l'agent délègue à des rôles : les références ``$ref`` lui sont montrées."""
        return any(isinstance(tool, DelegatedTool) for tool in self._tools.values())

    @property
    def specs(self) -> tuple[ToolSpec, ...]:
        return tuple(tool.spec for tool in self._tools.values())

    def definitions(self) -> tuple[ToolDefinition, ...]:
        """Ce que le modèle voit des outils, dans l'ordre de déclaration."""
        return tuple(spec.definition() for spec in self.specs)

    async def run_batch(self, state: RunState) -> AsyncGenerator[ToolEvent]:
        """Traite les appels en attente du run et émet leurs événements."""
        view = RunView.of(state)
        ready: list[_Ready] = []
        for call in state.pending_calls:
            tool = self._tools.get(call.name)
            prepared = (
                self._unknown_tool(call.name) if tool is None else self._prepare(call, tool, view)
            )
            if isinstance(prepared, _Ready):
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
            )

        queue: asyncio.Queue[ToolEvent | _Crashed] = asyncio.Queue()

        def crashed(task: asyncio.Task[None]) -> None:
            if not task.cancelled() and (error := task.exception()) is not None:
                queue.put_nowait(_Crashed(error))

        tasks = [asyncio.create_task(self._execute(item, view, queue.put_nowait)) for item in ready]
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

    def _unknown_tool(self, name: str) -> str:
        available = ", ".join(self._tools) or "aucun"
        return f"Outil inconnu : {name!r}. Outils disponibles : {available}."

    def _prepare(self, call: PendingCall, tool: AnyTool, view: RunView) -> _Ready | str:
        """Appel prêt à partir, ou motif de refus destiné au modèle."""
        if call.started and not tool.spec.safe_to_retry:
            return UNKNOWN_STATE
        if set(call.arguments) == {INVALID_JSON_KEY}:
            raw = str(call.arguments[INVALID_JSON_KEY])
            return f"Arguments illisibles : ce n'est pas un objet JSON valide.\nReçu : {raw[:500]}"
        try:
            arguments, refs = view.results.resolve(call.arguments)
        except RefError as exc:
            return exc.message
        if self.validate_arguments:
            problem = self._schema_errors(call.name, arguments)
            if problem is not None:
                return problem
        if isinstance(tool, DelegatedTool):
            problem = tool.check(arguments, view)
            if problem is not None:
                return problem
        return _Ready(call=call, tool=tool, arguments=arguments, refs=refs)

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
        self, item: _Ready, view: RunView, emit: Callable[[ToolEvent], None]
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
        scope = asyncio.timeout(timeout)
        try:
            async with scope:
                if isinstance(tool, DelegatedTool):
                    output = await _delegate(tool, item.arguments, context, view, emit)
                else:
                    output = await tool.invoke(item.arguments, context)
        except TimeoutError as exc:
            if scope.expired():
                output = ToolOutput.error(f"Délai dépassé : pas de réponse en {timeout:g} s.")
            else:
                # TimeoutError levée par l'outil lui-même.
                output = _unexpected(spec.name, exc, state)
        except ToolError as exc:
            output = ToolOutput.error(exc.message)
        except Exception as exc:
            output = _unexpected(spec.name, exc, state)
        emit(_completed(call, output, started=started))


async def _delegate(
    tool: DelegatedTool,
    arguments: dict[str, JsonValue],
    context: ToolContext,
    view: RunView,
    emit: Callable[[ToolEvent], None],
) -> ToolOutput:
    """Déroule un outil délégué : ses événements sont émis, son résultat renvoyé."""
    output: ToolOutput | None = None
    async with aclosing(tool.run(arguments, context, view)) as produced:
        async for item in produced:
            if isinstance(item, ToolOutput):
                output = item
            else:
                emit(Delegated(call_id=context.call_id, role=tool.spec.name, payload=item))
    if output is None:
        raise RuntimeError(f"Outil délégué {tool.spec.name} terminé sans résultat")
    return output


def _unexpected(name: str, exc: Exception, state: RunState) -> ToolOutput:
    logger.warning("Échec de l'outil %s", name, exc_info=exc, extra={"run_id": state.run_id})
    return ToolOutput.error(f"Erreur de l'outil {name} : {type(exc).__name__}: {exc}")


def _completed(call: PendingCall, output: ToolOutput, *, started: float | None) -> ToolCompleted:
    latency = 0.0 if started is None else (time.perf_counter() - started) * 1000
    return ToolCompleted(
        call_id=call.call_id,
        tool_name=call.name,
        output=output,
        latency_ms=latency,
        size=len(output.model_dump_json().encode()),
    )
