# SPDX-License-Identifier: Apache-2.0
"""De la config aux objets qui tournent (M1, A9).

``build_agent`` assemble les clients de modèle, l'exécuteur d'outils (outils
Python et rôles délégués), le prompt système et le ``RunContext``. Les trois
points d'accès partent tous d'ici.

Un client de modèle est ouvert par modèle utilisé : ``main`` et un rôle sur le
même modèle le partagent. Tout est vérifié avant d'ouvrir le premier client.

Serveurs MCP (#19) : chaque référence d'un agent devient une source d'outils,
ouverte au début de chaque run. Les connexions de portée ``shared`` vivent
dans un ``McpPool``, celui de l'instance ``Loom`` ou, à défaut, celui de
l'agent. Le SDK ``mcp`` n'est importé que si la config déclare des serveurs.

Stockage d'artefacts (G2) : un seul par instance, partagé par ses agents. Il
suit le journal par défaut (dossier ``.artifacts`` d'un journal JSONL,
mémoire sinon).
"""

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from loom_ia.adapters.artifacts import InMemoryArtifactStore, LocalArtifactStore
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
from loom_ia.core.ports import (
    ArtifactStore,
    ChunkCallback,
    EventStore,
    ModelClient,
    Tool,
    ToolSource,
)
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

if TYPE_CHECKING:
    from loom_ia.adapters.mcp import McpPool


class _Closable(Protocol):
    async def aclose(self) -> None: ...


@dataclass(frozen=True, slots=True)
class Agent:
    """Un agent prêt à tourner, et de quoi refermer ce qu'il a ouvert."""

    spec: AgentSpec
    context: RunContext
    # Clients de modèle de l'agent (``main`` et rôles), un par modèle.
    clients: tuple[ModelClient, ...] = ()
    # Ressources ouvertes pour cet agent seul (connexions MCP partagées sans pool fourni).
    owned: tuple[_Closable, ...] = ()

    async def aclose(self) -> None:
        """Ferme les clients de modèle et ce que l'agent possède ; le journal reste à l'appelant."""
        for client in self.clients or (self.context.model,):
            await client.aclose()
        for resource in self.owned:
            await resource.aclose()


def create_mcp_pool(config: LoomConfig, environ: Mapping[str, str] | None = None) -> McpPool | None:
    """Pool des connexions MCP partagées, ou None si la config ne déclare aucun serveur."""
    if not config.mcp_servers:
        return None
    try:
        from loom_ia.adapters.mcp import McpPool, session_factory
    except ImportError as exc:
        raise _missing_mcp("la config déclare des serveurs MCP") from exc
    env = os.environ if environ is None else environ
    return McpPool(lambda spec: session_factory(spec, environ=env))


def create_event_store(config: LoomConfig) -> EventStore:
    """Journal déclaré dans ``storage.events``."""
    events = config.storage.events
    if events.backend == "jsonl" and events.path is not None:
        return JsonlEventStore(events.path)
    return InMemoryEventStore()


def create_artifact_store(config: LoomConfig) -> ArtifactStore:
    """Stockage d'artefacts déclaré dans ``storage.artifacts``, ou celui qui suit le journal."""
    storage = config.storage
    path = storage.artifacts_path
    if storage.artifacts_backend == "local" and path is not None:
        return LocalArtifactStore(path)
    return InMemoryArtifactStore()


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
    mcp_pool: McpPool | None = None,
    artifacts: ArtifactStore | None = None,
) -> Agent:
    """Assemble l'agent ``name`` de la config.

    ``mcp_pool`` porte les connexions MCP partagées ; sans lui, l'agent ouvre
    le sien et le ferme avec ``aclose``. ``artifacts`` est le stockage des
    fichiers ; sans lui, les pièces jointes sont refusées et les gros
    résultats tronqués.
    """
    spec = AgentRegistry.from_config(config).get(name)
    known = registry if registry is not None else load_registry(config)
    tools = [_tool(declared, known, config.base_dir) for declared in spec.python_tools]
    roles = [role_definition(role) for role in spec.roles]
    _check_names(spec, [tool.spec.name for tool in tools])
    sources, owned = _mcp_sources(config, spec, environ, mcp_pool)

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
            sources=sources,
            default_timeout=execution.timeout,
            validate_arguments=execution.validate_arguments,
            artifacts=artifacts,
            offload_over=execution.offload_over,
        ),
        system=system_prompt(spec),
        max_iterations=spec.max_iterations,
        max_tokens=llm.max_tokens,
        params=llm.params,
        on_chunk=on_chunk,
        attachments=config.execution.attachments,
    )
    return Agent(spec=spec, context=context, clients=tuple(clients.values()), owned=owned)


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
        case "user_input" | "caller_context" | "attachments":
            return item


def _check_names(spec: AgentSpec, python_tools: list[str]) -> None:
    """Noms d'outils uniques dans l'agent, et ``tool_results`` qui désignent ses outils.

    Les outils MCP ne sont connus qu'au début du run : un nom qui porte le
    préfixe d'un serveur référencé est accepté.
    """
    names = [*python_tools, *(role.name for role in spec.roles)]
    doubles = sorted({name for name in names if names.count(name) > 1})
    if doubles:
        raise ConfigError(
            f"Agent {spec.name!r} : plusieurs outils ou rôles s'appellent {', '.join(doubles)}"
        )
    for role in spec.roles:
        for tool in role.tool_results:
            if tool in names or any(ref.owns(tool) for ref in spec.mcp_tools):
                continue
            prefixes = [f"{ref.prefix}__…" for ref in spec.mcp_tools]
            known = ", ".join([*names, *prefixes]) or "aucun"
            raise ConfigError(
                f"Agent {spec.name!r}, rôle {role.name!r} : tool_results désigne {tool!r}, "
                f"qui n'est pas un outil de l'agent (outils : {known})"
            )


def _mcp_sources(
    config: LoomConfig,
    spec: AgentSpec,
    environ: Mapping[str, str] | None,
    pool: McpPool | None,
) -> tuple[list[ToolSource], tuple[_Closable, ...]]:
    """Sources d'outils des serveurs MCP de l'agent, et le pool créé pour lui s'il en faut un."""
    if not spec.mcp_tools:
        return [], ()
    try:
        from loom_ia.adapters.mcp import (
            McpConfigError,
            McpPool,
            McpSelection,
            McpSource,
            session_factory,
        )
    except ImportError as exc:
        raise _missing_mcp(f"l'agent {spec.name!r} référence des serveurs MCP") from exc
    env = os.environ if environ is None else environ
    owned: tuple[_Closable, ...] = ()
    servers = [config.mcp_server(ref.mcp) for ref in spec.mcp_tools]
    if pool is None and any(server.scope == "shared" for server in servers):
        pool = McpPool(lambda server: session_factory(server, environ=env))
        owned = (pool,)
    sources: list[ToolSource] = []
    for ref, server in zip(spec.mcp_tools, servers, strict=True):
        try:
            factory = session_factory(server, environ=env)
        except McpConfigError as exc:
            raise ConfigError(f"Agent {spec.name!r} : {exc}") from exc
        selection = McpSelection(
            prefix=ref.prefix,
            include=ref.include,
            exclude=ref.exclude,
            required=ref.required,
            tools=ref.tools,
        )
        sources.append(McpSource(server, selection, factory=factory, pool=pool))
    return sources, owned


def _missing_mcp(reason: str) -> ConfigError:
    return ConfigError(
        f"Client MCP indisponible alors que {reason} : le SDK 'mcp' n'est pas "
        "installé (installer l'extra : loom-ia[mcp])"
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
        offload_over=declared.offload_over,
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
