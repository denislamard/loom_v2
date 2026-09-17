# SPDX-License-Identifier: Apache-2.0
"""Exécution des appels d'outils d'un tour (#15, #18, D1, D2, D4, D5).

Chaîne d'un appel : outil connu, reprise sans risque, arguments lisibles et
conformes au schéma, puis exécution avec timeout. Tout échec devient un
résultat d'erreur destiné au modèle ; seule l'annulation interrompt le lot.

``tool.called`` n'est écrit que pour un appel réellement lancé : c'est la
marque qu'un effet de bord a peut-être eu lieu. Tous les ``tool.called`` du
lot sont écrits avant la première exécution, puis chaque ``tool.completed``
dès que son outil répond.
"""

import asyncio
import logging
import time
from collections.abc import AsyncGenerator, Iterable
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

logger = logging.getLogger(__name__)

DEFAULT_TOOL_TIMEOUT: Final = 30.0

UNKNOWN_STATE: Final = (
    "État inconnu : l'exécution de cet outil a été interrompue et il a peut-être "
    "produit son effet. Il n'a pas été relancé automatiquement ; vérifie avant de "
    "le rappeler."
)

type ToolEvent = ToolCalled | ToolCompleted


class ToolExecutor:
    """Outils disponibles pour un run, et exécution de leurs appels."""

    def __init__(
        self,
        tools: Iterable[Tool] = (),
        *,
        default_timeout: float | None = DEFAULT_TOOL_TIMEOUT,
        validate_arguments: bool = True,
    ) -> None:
        self.default_timeout = default_timeout
        self.validate_arguments = validate_arguments
        self._tools: dict[str, Tool] = {}
        self._validators: dict[str, Validator] = {}
        for tool in tools:
            self.add(tool)

    def add(self, tool: Tool) -> None:
        spec = tool.spec
        if spec.name in self._tools:
            raise ValueError(f"Outil {spec.name!r} déjà déclaré")
        cls = validator_for(spec.input_schema, default=Draft202012Validator)
        cls.check_schema(spec.input_schema)
        self._tools[spec.name] = tool
        self._validators[spec.name] = cls(spec.input_schema)

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    @property
    def specs(self) -> tuple[ToolSpec, ...]:
        return tuple(tool.spec for tool in self._tools.values())

    def definitions(self) -> tuple[ToolDefinition, ...]:
        """Ce que le modèle voit des outils, dans l'ordre de déclaration."""
        return tuple(spec.definition() for spec in self.specs)

    async def run_batch(self, state: RunState) -> AsyncGenerator[ToolEvent]:
        """Traite les appels en attente du run et émet leurs événements."""
        ready: list[tuple[PendingCall, Tool]] = []
        for call in state.pending_calls:
            tool = self._tools.get(call.name)
            if tool is None:
                problem = self._unknown_tool(call.name)
            else:
                problem = self._precheck(call, tool)
                if problem is None:
                    ready.append((call, tool))
                    continue
            yield _completed(call, ToolOutput.error(problem), started=None)

        for call, tool in ready:
            yield ToolCalled(
                call_id=call.call_id,
                tool_name=call.name,
                tool_kind=tool.spec.kind,
                arguments=call.arguments,
                resumed=call.started,
            )

        tasks = [asyncio.create_task(self._execute(call, tool, state)) for call, tool in ready]
        try:
            async for task in asyncio.as_completed(tasks):
                yield task.result()
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    # --- Interne ---------------------------------------------------------

    def _unknown_tool(self, name: str) -> str:
        available = ", ".join(self._tools) or "aucun"
        return f"Outil inconnu : {name!r}. Outils disponibles : {available}."

    def _precheck(self, call: PendingCall, tool: Tool) -> str | None:
        """Motif de refus de l'appel, ou None s'il peut être lancé."""
        if call.started and not tool.spec.safe_to_retry:
            return UNKNOWN_STATE
        if set(call.arguments) == {INVALID_JSON_KEY}:
            raw = str(call.arguments[INVALID_JSON_KEY])
            return f"Arguments illisibles : ce n'est pas un objet JSON valide.\nReçu : {raw[:500]}"
        if self.validate_arguments:
            return self._schema_errors(call.name, call.arguments)
        return None

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

    async def _execute(self, call: PendingCall, tool: Tool, state: RunState) -> ToolCompleted:
        spec = tool.spec
        timeout = spec.timeout if spec.timeout is not None else self.default_timeout
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
                output = await tool.invoke(call.arguments, context)
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
        return _completed(call, output, started=started)


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
