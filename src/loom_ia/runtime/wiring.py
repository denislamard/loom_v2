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

Politiques (#2) : chaque référence est résolue (politique fournie ``loom.…``,
nom enregistré ou ``module:attr``), puis contrôlée avant le premier run : ses
points parmi ceux qu'elle déclare, ses décisions permises à chacun de ses
points (``Pause`` arrive en J4.3), un nom unique dans l'agent.

Contrats de sortie (#20) : si l'agent en déclare un (réponse finale, rôle,
outil Python ou MCP), le guard ``loom.contract`` est placé en tête de ses
politiques. La diffusion de la réponse finale (``stream_output``) vaut par
défaut ``after_guards`` quand elle est contrôlée, ``live`` sinon.

Secours (B4, #10) : ``main``, chaque rôle et chaque juge reçoivent leur
chaîne (modèle, puis ``fallbacks``), avec un client par modèle. Les
disjoncteurs des modèles et des serveurs MCP sont communs aux agents d'une
instance ``Loom`` (``breakers``) ; sans elle, à l'agent et à ses sous-agents.

Sous-agents (C5) : l'agent appelé n'est monté qu'au premier appel, par
``agents`` (``Loom.context`` dans une instance). Deux agents peuvent ainsi
s'appeler l'un l'autre sans que le montage boucle ; la profondeur borne les
appels. Sans ``agents``, l'agent monte lui-même ses sous-agents à la demande,
et les referme avec lui.
"""

import logging
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast

from loom_ia.adapters.artifacts import InMemoryArtifactStore, LocalArtifactStore
from loom_ia.adapters.models import create_model_client
from loom_ia.adapters.stores import InMemoryEventStore, JsonlEventStore
from loom_ia.agents.registry import AgentRegistry
from loom_ia.agents.spec import (
    AgentSpec,
    BaseRole,
    JudgeSpec,
    PolicyRef,
    PythonTool,
    RoleSpec,
    SubAgentRef,
    ToolResultsContext,
)
from loom_ia.agents.spec import ContextItem as DeclaredContext
from loom_ia.config.errors import ConfigError
from loom_ia.config.models import LoomConfig
from loom_ia.config.references import Registry, import_modules, resolve
from loom_ia.core.model import (
    ALLOWED_DECISIONS,
    LATER_DECISIONS,
    RESERVED_PREFIX,
    StreamOutput,
)
from loom_ia.core.ports import (
    ArtifactStore,
    ChunkCallback,
    EventStore,
    ModelClient,
    Policy,
    Tool,
    ToolSource,
)
from loom_ia.core.template import Template
from loom_ia.engine import (
    AgentResolver,
    AgentTool,
    AnyTool,
    BoundPolicy,
    CircuitBreakers,
    ContextItem,
    ModelLink,
    Policies,
    RoleDefinition,
    RoleTool,
    RunContext,
    SubAgentDefinition,
    ToolExecutor,
    ToolResults,
)
from loom_ia.guards import (
    CONTRACT_POLICY,
    Condition,
    ContractGuard,
    JudgeDefinition,
    JudgeGuard,
    correlated,
)
from loom_ia.policies import BUILTIN_POLICIES
from loom_ia.telemetry import configure_logging
from loom_ia.tools import FunctionTool, configure
from loom_ia.usage import BudgetGuard

if TYPE_CHECKING:
    from loom_ia.adapters.mcp import McpPool

logger = logging.getLogger(__name__)


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
    if events.path is not None:
        if events.backend == "jsonl":
            return JsonlEventStore(events.path)
        if events.backend == "sqlite":
            try:
                from loom_ia.adapters.stores.sqlite import SqliteEventStore
            except ImportError as exc:
                raise ConfigError(
                    "Journal 'sqlite' : le paquet 'aiosqlite' n'est pas installé "
                    "(installer l'extra : loom-ia[sqlite])"
                ) from exc
            return SqliteEventStore(events.path)
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
    agents: AgentResolver | None = None,
    breakers: CircuitBreakers | None = None,
) -> Agent:
    """Assemble l'agent ``name`` de la config.

    ``mcp_pool`` porte les connexions MCP partagées ; sans lui, l'agent ouvre
    le sien et le ferme avec ``aclose``. ``artifacts`` est le stockage des
    fichiers ; sans lui, les pièces jointes sont refusées et les gros
    résultats tronqués. ``agents`` donne le contexte d'un sous-agent par son
    nom ; sans lui, l'agent monte ses sous-agents lui-même, au premier appel.
    ``breakers`` : disjoncteurs partagés (ceux de l'instance) ; sans eux,
    l'agent a les siens, communs à ses sous-agents.
    """
    spec = AgentRegistry.from_config(config).get(name)
    known = registry if registry is not None else load_registry(config)
    tools = [_tool(declared, known, config.base_dir) for declared in spec.python_tools]
    roles = [role_definition(role) for role in spec.roles]
    _check_names(spec, [tool.spec.name for tool in tools])

    clients: dict[str, ModelClient] = {}
    breakers = breakers if breakers is not None else CircuitBreakers()

    def client(model_id: str) -> ModelClient:
        if model_id not in clients:
            clients[model_id] = create_model_client(config.model_spec(model_id), environ=environ)
        return clients[model_id]

    def links(model_ids: tuple[str, ...]) -> tuple[ModelLink, ...]:
        return tuple(ModelLink(config.model_spec(m), client(m)) for m in model_ids)

    judges = [
        JudgeGuard(
            judge_definition(spec, judged, known, config.base_dir),
            client(judged[2].model),
            config.model_spec(judged[2].model),
            artifacts=artifacts,
            fallbacks=links(judged[2].fallbacks),
            breakers=breakers,
        )
        for judged in spec.judges
    ]
    warnings = [
        *judge_warnings(config, spec),
        *budget_warnings(config, spec),
        *fallback_warnings(config, spec),
    ]
    for warning in warnings:
        logger.warning(warning)
    budgets = config.budget_of(spec.name)
    shared = any(
        ref.agent == spec.name and ref.budget_share is not None
        for agent in config.agents
        for ref in agent.subagents
    )
    policies = build_policies(
        spec,
        known,
        config.base_dir,
        contracts=_has_contracts(config, spec),
        judges=judges,
        budget=BudgetGuard(budgets) if budgets.limited or shared else None,
    )
    sources, owned = _mcp_sources(config, spec, environ, mcp_pool)
    if spec.subagents and agents is None:
        nested = _SubAgents(
            config,
            store,
            registry=known,
            environ=environ,
            mcp_pool=mcp_pool,
            artifacts=artifacts,
            breakers=breakers,
        )
        agents = nested
        owned = (*owned, nested)

    delegated: list[AnyTool] = [
        RoleTool(
            definition,
            client(role.model),
            config.model_spec(role.model),
            fallbacks=links(role.fallbacks),
            breakers=breakers,
        )
        for role, definition in zip(spec.roles, roles, strict=True)
    ]
    if agents is not None:
        delegated += [
            AgentTool(_subagent_definition(config, spec, ref), agents) for ref in spec.subagents
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
            breakers=breakers,
            circuits={server.name: server.circuit_breaker for server in config.mcp_servers},
        ),
        system=system_prompt(spec),
        max_iterations=spec.max_iterations,
        max_tokens=llm.max_tokens,
        params=llm.params,
        on_chunk=on_chunk,
        attachments=config.execution.attachments,
        policies=policies,
        output=spec.output,
        stream_output=stream_output(spec, policies),
        fallbacks=links(spec.main.fallbacks),
        breakers=breakers,
    )
    return Agent(spec=spec, context=context, clients=tuple(clients.values()), owned=owned)


def build_policies(
    spec: AgentSpec,
    registry: Registry,
    base_dir: Path | None = None,
    *,
    contracts: bool | None = None,
    judges: Sequence[JudgeGuard] = (),
    budget: BudgetGuard | None = None,
) -> Policies:
    """Politiques de l'agent, résolues et contrôlées, dans l'ordre déclaré.

    Les politiques fournies passent en tête : le guard des contrats quand
    l'agent en déclare (``contracts``, déduit de l'agent si absent), puis les
    juges (``judges``) — la forme d'une sortie est contrôlée avant son fond —,
    puis le budget (``budget``).
    """
    bound: list[BoundPolicy] = []
    if contracts if contracts is not None else spec.contracts:
        guard = ContractGuard(spec.output)
        # Le guard borne lui-même ses réparations (repair.max_attempts du contrat).
        bound.append(
            BoundPolicy(policy=guard, name=CONTRACT_POLICY, points=guard.points, max_attempts=None)
        )
    for judge in judges:
        definition = judge.definition
        # Un juge borne lui-même ses réparations (repair.max_attempts du juge).
        bound.append(
            BoundPolicy(
                policy=judge,
                name=judge.name,
                points=judge.points,
                timeout=definition.timeout,
                on_error=definition.on_error,
                max_attempts=None,
            )
        )
    if budget is not None:
        # Une erreur du budget arrête le run (défaut des politiques, #2).
        bound.append(BoundPolicy(policy=budget, name=budget.name, points=budget.points))
    for ref in spec.policies:
        found = _policy(spec, ref, registry, base_dir)
        points = frozenset(ref.points) if ref.points is not None else found.points
        name = ref.name or found.name
        label = f"Agent {spec.name!r}, politique {name!r}"
        outside = sorted(points - found.points)
        if outside:
            raise ConfigError(
                f"{label} : ne s'applique pas à {', '.join(outside)} "
                f"(points déclarés : {', '.join(sorted(found.points))})"
            )
        for kind in sorted(found.decisions):
            if kind in LATER_DECISIONS:
                raise ConfigError(
                    f"{label} : décision {kind!r} prévue pour le jalon {LATER_DECISIONS[kind]}, "
                    "pas encore prise en charge"
                )
        for point in sorted(points):
            refused = sorted(found.decisions - ALLOWED_DECISIONS[point])
            if refused:
                raise ConfigError(
                    f"{label} : décision(s) {', '.join(refused)} non permise(s) au point {point}"
                )
        if any(existing.name == name for existing in bound):
            raise ConfigError(
                f"{label} : déclarée deux fois (donner un 'name' à l'une des références)"
            )
        bound.append(
            BoundPolicy(
                policy=found,
                name=name,
                points=points,
                params=ref.params,
                timeout=ref.timeout,
                on_error=ref.on_error,
                max_attempts=ref.max_attempts,
            )
        )
    return Policies(bound)


def stream_output(spec: AgentSpec, policies: Policies) -> StreamOutput:
    """Diffusion de la réponse finale : celle déclarée, sinon selon ses contrôles (#11)."""
    if spec.stream_output is not None:
        return spec.stream_output
    guarded = bool(policies.at("on_output")) or any(
        role.terminal and (role.output is not None or role.judge is not None) for role in spec.roles
    )
    return "after_guards" if guarded else "live"


def _has_contracts(config: LoomConfig, spec: AgentSpec) -> bool:
    """Vrai si un contrat s'applique à l'agent, y compris par un serveur MCP qu'il référence."""
    servers = [config.mcp_server(ref.mcp) for ref in spec.mcp_tools]
    return spec.contracts or any(o.output is not None for s in servers for o in s.tools.values())


def _policy(spec: AgentSpec, ref: PolicyRef, registry: Registry, base_dir: Path | None) -> Policy:
    """Politique désignée par une référence : fournie par loom-ia, enregistrée ou importée."""
    if ref.hook.startswith(RESERVED_PREFIX):
        builtin = BUILTIN_POLICIES.get(ref.hook)
        if builtin is None:
            raise ConfigError(
                f"Agent {spec.name!r} : politique fournie {ref.hook!r} inconnue "
                f"(fournies : {', '.join(BUILTIN_POLICIES)})"
            )
        return builtin
    found = resolve(ref.hook, registry, base_dir=base_dir)
    if isinstance(found, type) or not isinstance(found, Policy):
        raise ConfigError(
            f"Agent {spec.name!r} : {ref.hook!r} n'est pas une politique "
            "(décorer la fonction avec @policy)"
        )
    return found


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
        output=role.output,
        max_tokens=role.llm.max_tokens,
        params=role.llm.params,
    )


def judge_definition(
    spec: AgentSpec,
    judged: tuple[str, RoleSpec | None, JudgeSpec],
    registry: Registry,
    base_dir: Path | None = None,
) -> JudgeDefinition:
    """Juge tel que le moteur l'exécute : condition résolue, contexte converti."""
    name, role, judge = judged
    condition: Condition | None = None
    reference = judge.when.condition
    if reference is not None:
        found = resolve(reference, registry, base_dir=base_dir)
        if isinstance(found, type) or not callable(found):
            raise ConfigError(
                f"Agent {spec.name!r}, juge {name!r} : la condition {reference!r} "
                "n'est pas une fonction"
            )
        condition = cast(Condition, found)
    tenants = judge.when.tenants
    return JudgeDefinition(
        name=name,
        role=role.name if role is not None else None,
        criteria=judge.criteria,
        context=tuple(_context_item(item) for item in judge.context),
        sample=judge.when.sample,
        condition=condition,
        tenants=frozenset(tenants) if tenants is not None else None,
        repair=judge.repair,
        on_failure=judge.on_failure,
        fallback_message=judge.fallback_message,
        max_tokens=judge.llm.max_tokens,
        params=judge.llm.params,
        timeout=judge.timeout,
        on_error=judge.on_error,
    )


def budget_warnings(config: LoomConfig, spec: AgentSpec) -> list[str]:
    """Budget en dollars sur un agent dont un modèle n'a pas de tarif (backlog #010).

    Ses appels comptent 0 $ : le plafond ne les voit pas. Une erreur en profil
    prod arrivera avec les profils (J5) ; un budget en tokens reste efficace.
    """
    if not config.budget_of(spec.name).in_dollars:
        return []
    used = [*spec.main.chain, *(m for role in spec.roles for m in role.chain)]
    used += [m for _, _, judge in spec.judges for m in judge.chain]
    unpriced = [m for m in dict.fromkeys(used) if not config.model_spec(m).pricing.priced]
    if not unpriced:
        return []
    return [
        f"Agent {spec.name!r} : budget en dollars, mais sans tarif pour "
        f"{', '.join(unpriced)} : leurs appels comptent 0 $"
    ]


def judge_warnings(config: LoomConfig, spec: AgentSpec) -> list[str]:
    """Avertissements sur les juges de l'agent (E6, #21) ; des erreurs en profil prod (J5)."""
    warnings: list[str] = []
    for name, role, judge in spec.judges:
        label = f"Agent {spec.name!r}, juge {name!r}"
        # Chaînes de secours comprises (#10) : un secours peut rendre le juge corrélé.
        evaluated = role.chain if role is not None else spec.main.chain
        pairs = [
            (mine, theirs)
            for mine in judge.chain
            for theirs in evaluated
            if correlated(config.model_spec(mine), config.model_spec(theirs))
        ]
        if pairs:
            same = ", ".join(
                mine if mine == theirs else f"{mine} et {theirs}" for mine, theirs in pairs
            )
            warnings.append(
                f"{label} : même modèle que la sortie qu'il évalue ({same}) : juge corrélé"
            )
        if judge.blocking and judge.when.sample < 1:
            warnings.append(
                f"{label} : bloquant, mais ne juge qu'une partie des runs "
                f"(sample {judge.when.sample:g})"
            )
    return warnings


def fallback_warnings(config: LoomConfig, spec: AgentSpec) -> list[str]:
    """Secours à la fenêtre de contexte déclarée plus petite que celle du modèle (#10).

    Une requête qui tient dans la fenêtre du modèle peut déborder de celle du
    secours : la bascule échouera alors en ``context_overflow``.
    """
    chains = [(f"Agent {spec.name!r}", spec.main.chain)]
    chains += [(f"Agent {spec.name!r}, rôle {role.name!r}", role.chain) for role in spec.roles]
    chains += [
        (f"Agent {spec.name!r}, juge {name!r}", judge.chain) for name, _, judge in spec.judges
    ]
    warnings: list[str] = []
    for label, (model, *fallbacks) in chains:
        window = config.model_spec(model).capabilities.context_window
        if window is None:
            continue
        for fallback in fallbacks:
            smaller = config.model_spec(fallback).capabilities.context_window
            if smaller is not None and smaller < window:
                warnings.append(
                    f"{label} : le secours {fallback} a une fenêtre de contexte plus petite "
                    f"que {model} ({smaller} < {window} tokens)"
                )
    return warnings


def _context_item(item: DeclaredContext) -> ContextItem:
    match item:
        case ToolResultsContext(tool_results=tools):
            return ToolResults(tools=tools)
        case "user_input" | "caller_context" | "attachments":
            return item


def _subagent_definition(
    config: LoomConfig, spec: AgentSpec, ref: SubAgentRef
) -> SubAgentDefinition:
    """Sous-agent tel que le moteur l'appelle : nom et description de l'agent par défaut."""
    target = AgentRegistry.from_config(config).get(ref.agent)
    return SubAgentDefinition(
        name=ref.tool_name,
        agent=ref.agent,
        description=ref.description or target.description,
        max_depth=spec.max_depth,
        budget_share=ref.budget_share,
        parent_budget=config.budget_of(spec.name).run,
    )


class _SubAgents:
    """Sous-agents d'un agent monté hors d'une instance ``Loom`` : montés au premier appel."""

    def __init__(
        self,
        config: LoomConfig,
        store: EventStore,
        *,
        registry: Registry,
        environ: Mapping[str, str] | None,
        mcp_pool: McpPool | None,
        artifacts: ArtifactStore | None,
        breakers: CircuitBreakers,
    ) -> None:
        self._config = config
        self._store = store
        self._registry = registry
        self._environ = environ
        self._mcp_pool = mcp_pool
        self._artifacts = artifacts
        self._breakers = breakers
        self._built: dict[str, Agent] = {}

    def __call__(self, name: str) -> RunContext:
        built = self._built.get(name)
        if built is None:
            built = build_agent(
                self._config,
                name,
                self._store,
                registry=self._registry,
                environ=self._environ,
                mcp_pool=self._mcp_pool,
                artifacts=self._artifacts,
                agents=self,
                breakers=self._breakers,
            )
            self._built[name] = built
        return built.context

    async def aclose(self) -> None:
        for built in self._built.values():
            await built.aclose()
        self._built.clear()


def _check_names(spec: AgentSpec, python_tools: list[str]) -> None:
    """Noms d'outils uniques dans l'agent, et ``tool_results`` qui désignent ses outils.

    Les outils MCP ne sont connus qu'au début du run : un nom qui porte le
    préfixe d'un serveur référencé est accepté.
    """
    names = [
        *python_tools,
        *(role.name for role in spec.roles),
        *(ref.tool_name for ref in spec.subagents),
    ]
    doubles = sorted({name for name in names if names.count(name) > 1})
    if doubles:
        raise ConfigError(
            f"Agent {spec.name!r} : plusieurs outils, rôles ou sous-agents s'appellent "
            f"{', '.join(doubles)}"
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
        output=declared.output,
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
