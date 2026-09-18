# SPDX-License-Identifier: Apache-2.0
"""De la config aux objets qui tournent (M1, A9).

``build_agent`` assemble ce que la 1.4 et la 1.3 fournissent déjà : client de
modèle, exécuteur d'outils, prompt système et ``RunContext``. Les trois points
d'accès du jalon 1.6 partiront tous d'ici.
"""

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from loom_ia.adapters.models import create_model_client
from loom_ia.adapters.stores import InMemoryEventStore, JsonlEventStore
from loom_ia.agents.registry import AgentRegistry
from loom_ia.agents.spec import AgentSpec, PythonTool
from loom_ia.config.errors import ConfigError
from loom_ia.config.models import LoomConfig
from loom_ia.config.references import Registry, import_modules, resolve
from loom_ia.core.ports import ChunkCallback, EventStore, Tool
from loom_ia.engine import RunContext, ToolExecutor
from loom_ia.telemetry import configure_logging
from loom_ia.tools import FunctionTool, configure


@dataclass(frozen=True, slots=True)
class Agent:
    """Un agent prêt à tourner, et de quoi refermer ce qu'il a ouvert."""

    spec: AgentSpec
    context: RunContext

    async def aclose(self) -> None:
        """Ferme le client de modèle ; le journal appartient à l'appelant."""
        await self.context.model.aclose()


def create_event_store(config: LoomConfig) -> EventStore:
    """Journal déclaré dans ``storage.events``."""
    events = config.storage.events
    if events.backend == "jsonl" and events.path is not None:
        return JsonlEventStore(events.path)
    return InMemoryEventStore()


def load_registry(config: LoomConfig) -> Registry:
    """Charge les modules de ``imports`` et enregistre leurs outils."""
    return import_modules(config.imports, base_dir=config.base_dir)


def apply_logging(config: LoomConfig) -> logging.Handler:
    """Installe les logs demandés par ``telemetry.logging``."""
    settings = config.telemetry.logging
    return configure_logging(settings.level.upper(), format=settings.format)


def build_agent(
    config: LoomConfig,
    name: str,
    store: EventStore,
    *,
    registry: Registry | None = None,
    environ: Mapping[str, str] | None = None,
    on_chunk: ChunkCallback | None = None,
) -> Agent:
    """Assemble l'agent ``name`` de la config."""
    spec = AgentRegistry.from_config(config).get(name)
    model_spec = config.model_spec(spec.main.model)
    known = registry if registry is not None else load_registry(config)
    tools = [_tool(declared, known, config.base_dir) for declared in spec.tools]
    execution = config.execution.tools
    context = RunContext(
        agent=spec.name,
        store=store,
        model=create_model_client(model_spec, environ=environ),
        model_spec=model_spec,
        tools=ToolExecutor(
            tools,
            default_timeout=execution.timeout,
            validate_arguments=execution.validate_arguments,
        ),
        system=system_prompt(spec),
        max_iterations=spec.max_iterations,
        on_chunk=on_chunk,
    )
    return Agent(spec=spec, context=context)


def system_prompt(spec: AgentSpec) -> str:
    """Prompt système de l'agent : texte en ligne ou fichier."""
    if spec.main.system_file is None:
        return spec.main.system
    return spec.main.system_file.read_text(encoding="utf-8")


def _tool(declared: PythonTool, registry: Registry, base_dir: Path | None) -> Tool:
    found = resolve(declared.python, registry, base_dir=base_dir)
    tool = found if isinstance(found, Tool) else _from_function(declared, found)
    return configure(
        tool,
        timeout=declared.timeout,
        side_effects=declared.side_effects,
        approval=declared.approval,
        idempotent=declared.idempotent,
    )


def _from_function(declared: PythonTool, found: object) -> Tool:
    if not callable(found):
        raise ConfigError(
            f"Référence {declared.python!r} : ni un outil ni une fonction ({type(found).__name__})"
        )
    try:
        return FunctionTool(found)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"Référence {declared.python!r} : {exc}") from exc
