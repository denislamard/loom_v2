# SPDX-License-Identifier: Apache-2.0
"""De la config aux objets qui tournent (M1, A9).

``build_agent`` assemble les clients de modèle, l'exécuteur d'outils (outils
Python et rôles délégués), le prompt système et le ``RunContext``. Les trois
points d'accès partent tous d'ici.

Un client de modèle est ouvert par modèle utilisé : ``main`` et un rôle sur le
même modèle le partagent. Tout est vérifié avant d'ouvrir le premier client.
"""

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from loom_ia.adapters.models import create_model_client
from loom_ia.adapters.stores import InMemoryEventStore, JsonlEventStore
from loom_ia.agents.registry import AgentRegistry
from loom_ia.agents.spec import (
    AgentSpec,
    BaseRole,
    PythonTool,
    RoleSpec,
    ToolResultsContext,
)
from loom_ia.agents.spec import ContextItem as DeclaredContext
from loom_ia.config.errors import ConfigError
from loom_ia.config.models import LoomConfig
from loom_ia.config.references import Registry, import_modules, resolve
from loom_ia.core.ports import ChunkCallback, EventStore, ModelClient, Tool
from loom_ia.core.template import Template
from loom_ia.engine import (
    AnyTool,
    ContextItem,
    RoleDefinition,
    RoleTool,
    RunContext,
    ToolExecutor,
    ToolResults,
)
from loom_ia.telemetry import configure_logging
from loom_ia.tools import FunctionTool, configure


@dataclass(frozen=True, slots=True)
class Agent:
    """Un agent prêt à tourner, et de quoi refermer ce qu'il a ouvert."""

    spec: AgentSpec
    context: RunContext
    # Clients de modèle de l'agent (``main`` et rôles), un par modèle.
    clients: tuple[ModelClient, ...] = ()

    async def aclose(self) -> None:
        """Ferme les clients de modèle ; le journal appartient à l'appelant."""
        for client in self.clients or (self.context.model,):
            await client.aclose()


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
    known = registry if registry is not None else load_registry(config)
    tools = [_tool(declared, known, config.base_dir) for declared in spec.tools]
    roles = [role_definition(role) for role in spec.roles]
    _check_names(spec, [tool.spec.name for tool in tools])

    clients: dict[str, ModelClient] = {}

    def client(model_id: str) -> ModelClient:
        if model_id not in clients:
            clients[model_id] = create_model_client(config.model_spec(model_id), environ=environ)
        return clients[model_id]

    delegated: list[AnyTool] = [
        RoleTool(definition, client(role.model), config.model_spec(role.model))
        for role, definition in zip(spec.roles, roles, strict=True)
    ]
    execution = config.execution.tools
    llm = spec.main.llm
    context = RunContext(
        agent=spec.name,
        store=store,
        model=client(spec.main.model),
        model_spec=config.model_spec(spec.main.model),
        tools=ToolExecutor(
            [*tools, *delegated],
            default_timeout=execution.timeout,
            validate_arguments=execution.validate_arguments,
        ),
        system=system_prompt(spec),
        max_iterations=spec.max_iterations,
        max_tokens=llm.max_tokens,
        params=llm.params,
        on_chunk=on_chunk,
    )
    return Agent(spec=spec, context=context, clients=tuple(clients.values()))


def system_prompt(spec: AgentSpec) -> str:
    """Prompt système de l'orchestrateur : texte en ligne ou fichier."""
    return prompt_text(spec.main)


def prompt_text(role: BaseRole) -> str:
    """Prompt système d'un rôle : texte en ligne ou fichier."""
    if role.system_file is None:
        return role.system
    return role.system_file.read_text(encoding="utf-8")


def role_definition(role: RoleSpec) -> RoleDefinition:
    """Rôle délégué tel que le moteur l'exécute."""
    template = Template.parse(role.input_template) if role.input_template is not None else None
    return RoleDefinition(
        name=role.name,
        description=role.description,
        system=prompt_text(role),
        input_schema=role.input_schema,
        template=template,
        context=tuple(_context_item(item) for item in role.context),
        terminal=role.terminal,
        timeout=role.timeout,
        max_tokens=role.llm.max_tokens,
        params=role.llm.params,
    )


def _context_item(item: DeclaredContext) -> ContextItem:
    match item:
        case ToolResultsContext(tool_results=tools):
            return ToolResults(tools=tools)
        case "user_input" | "caller_context":
            return item


def _check_names(spec: AgentSpec, python_tools: list[str]) -> None:
    """Noms d'outils uniques dans l'agent, et ``tool_results`` qui désignent ses outils."""
    names = [*python_tools, *(role.name for role in spec.roles)]
    doubles = sorted({name for name in names if names.count(name) > 1})
    if doubles:
        raise ConfigError(
            f"Agent {spec.name!r} : plusieurs outils ou rôles s'appellent {', '.join(doubles)}"
        )
    for role in spec.roles:
        for tool in role.tool_results:
            if tool not in names:
                raise ConfigError(
                    f"Agent {spec.name!r}, rôle {role.name!r} : tool_results désigne {tool!r}, "
                    f"qui n'est pas un outil de l'agent (outils : {', '.join(names) or 'aucun'})"
                )


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
