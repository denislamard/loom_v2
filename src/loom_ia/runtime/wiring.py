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

Clients (L1, #34) : ``tenant`` dit pour qui l'agent est monté. Il apporte la
table des secrets à lire, les outils retirés, les approbations imposées et
les valeurs des variables citées par les prompts ; la correspondance des
modèles, elle, est déjà dans la config qu'il porte. Sans lui, l'agent est
monté pour ``default``, qui ne surcharge rien (#33).

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

from pydantic import JsonValue

from loom_ia.adapters.artifacts import InMemoryArtifactStore, LocalArtifactStore
from loom_ia.adapters.idempotency import InMemoryIdempotency
from loom_ia.adapters.models import create_model_client
from loom_ia.adapters.queue import AsyncioTaskQueue, Handler
from loom_ia.adapters.stores import InMemoryEventStore, JsonlEventStore
from loom_ia.agents.registry import AgentRegistry
from loom_ia.agents.spec import (
    AgentSpec,
    BaseRole,
    JudgeSpec,
    LastTurnsContext,
    PolicyRef,
    PythonTool,
    RoleSpec,
    SubAgentRef,
    ToolResultsContext,
    system_text,
)
from loom_ia.agents.spec import ContextItem as DeclaredContext
from loom_ia.config.compaction import COMPACTION_AGENT
from loom_ia.config.errors import ConfigError
from loom_ia.config.models import (
    DURABLE_BACKENDS,
    SHARED_IDEMPOTENCY,
    BusStorage,
    EventsStorage,
    IdempotencyStorage,
    LoomConfig,
    StorageConfig,
)
from loom_ia.config.references import Registry, import_modules, resolve
from loom_ia.core.model import (
    ALLOWED_DECISIONS,
    DEFAULT_TENANT,
    LATER_DECISIONS,
    RESERVED_PREFIX,
    Approval,
    StreamOutput,
    TenantId,
)
from loom_ia.core.ports import (
    ArtifactStore,
    ChunkCallback,
    EventBus,
    EventStore,
    IdempotencyStore,
    JobKind,
    ModelClient,
    Policy,
    TaskQueue,
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
    LastTurns,
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
    FidelityGuard,
    JudgeDefinition,
    JudgeGuard,
    correlated,
)
from loom_ia.policies import BUILTIN_POLICIES
from loom_ia.telemetry import configure_logging
from loom_ia.tenancy import Tenant
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


def _dsn(declared: EventsStorage | IdempotencyStorage, what: str) -> str:
    """DSN lu dans la variable que la config nomme : elle ne porte pas le secret (§16.3)."""
    variable = declared.dsn_env or ""
    dsn = os.environ.get(variable, "")
    if not dsn:
        raise ConfigError(
            f"{what} {declared.backend!r} : la variable {variable!r} est vide ou absente — "
            "c'est elle qui porte le DSN"
        )
    return dsn


def _missing_asyncpg(what: str) -> ConfigError:
    return ConfigError(
        f"{what} 'postgres' : le paquet 'asyncpg' n'est pas installé "
        "(installer l'extra : loom-ia[postgres])"
    )


def create_event_store(config: LoomConfig | StorageConfig) -> EventStore:
    """Journal déclaré dans ``storage.events``, d'une config ou d'un client."""
    events = _storage(config).events
    if events.backend == "postgres":
        try:
            from loom_ia.adapters.stores.postgres import PostgresEventStore
        except ImportError as exc:
            raise _missing_asyncpg("Journal") from exc
        return PostgresEventStore(_dsn(events, "Journal"), role=events.role)
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


def create_task_queue(config: LoomConfig, handlers: Mapping[JobKind, Handler]) -> TaskQueue:
    """File déclarée dans ``storage.queue``.

    ``asyncio`` exécute les tâches ici même ; ``rabbitmq`` les publie, et
    c'est ``loom worker`` qui les mène. Les traitements sont passés dans les
    deux cas : c'est le même objet qui sert de file dans un process qui
    publie et de consommateur dans un worker.
    """
    declared = config.storage.queue
    if declared.backend == "rabbitmq":
        try:
            from loom_ia.adapters.queue.rabbitmq import RabbitMqTaskQueue
        except ImportError as exc:
            raise ConfigError(
                "File 'rabbitmq' : le paquet 'aio-pika' n'est pas installé "
                "(installer l'extra : loom-ia[rabbitmq])"
            ) from exc
        variable = declared.url_env or ""
        url = os.environ.get(variable, "")
        if not url:
            raise ConfigError(
                f"File 'rabbitmq' : la variable {variable!r} est vide ou absente — "
                "c'est elle qui porte l'URL du courtier"
            )
        return RabbitMqTaskQueue(url, handlers)
    return AsyncioTaskQueue(handlers, shutdown_timeout=config.execution.shutdown_timeout)


def create_bus(config: LoomConfig) -> EventBus | None:
    """Bus déclaré dans ``storage.bus``, ou ``None`` s'il n'y a rien à traverser.

    ``memory`` rend ``None`` : dans un seul process, le journal remet déjà ses
    écritures à ses abonnés, et un bus de plus ne ferait que les recopier.
    """
    declared = config.storage.bus
    if declared.backend == "postgres":
        try:
            from loom_ia.adapters.bus.postgres import PostgresBus
        except ImportError as exc:
            raise _missing_asyncpg("Bus") from exc
        return PostgresBus(_variable(declared, "Bus", "le DSN"))
    if declared.backend == "redis":
        try:
            from loom_ia.adapters.bus.redis import RedisBus
        except ImportError as exc:
            raise _missing_redis("Bus") from exc
        return RedisBus(_variable(declared, "Bus", "l'URL"))
    return None


def _variable(declared: BusStorage, what: str, porte: str) -> str:
    """Valeur de la variable d'environnement que la config nomme (§16.3)."""
    name = declared.variable or ""
    value = os.environ.get(name, "")
    if not value:
        raise ConfigError(
            f"{what} {declared.backend!r} : la variable {name!r} est vide ou absente — "
            f"c'est elle qui porte {porte}"
        )
    return value


def _missing_redis(what: str) -> ConfigError:
    return ConfigError(
        f"{what} 'redis' : le paquet 'redis' n'est pas installé "
        "(installer l'extra : loom-ia[redis])"
    )


def postgres_ddl(config: LoomConfig | StorageConfig) -> str:
    """Le SQL du stockage Postgres déclaré par la config (``loom storage sql``).

    Une config qui n'en déclare aucun rend le SQL complet avec le rôle par
    défaut : de quoi préparer une base avant de la déclarer.
    """
    from loom_ia.adapters.postgres import sql

    storage = _storage(config)
    declared = (
        (storage.events, sql.EVENTS_TABLE, sql.EVENTS_DDL, False),
        (storage.idempotency, sql.IDEMPOTENCY_TABLE, sql.IDEMPOTENCY_DDL, True),
    )
    used = [d for d in declared if d[0].backend == "postgres"]
    if not used:
        return sql.ddl()
    blocks: list[str] = []
    roles: set[str] = set()
    for storage_declared, table, table_ddl, update in used:
        role = storage_declared.role
        if role is not None and role not in roles:
            roles.add(role)
            blocks.extend([sql.role_ddl(role), sql.membership_ddl(role)])
        blocks.append(table_ddl)
        if role is not None:
            blocks.append(sql.grants_ddl(role, table=table, update=update))
    return "\n".join(blocks) + "\n"


def create_artifact_store(config: LoomConfig | StorageConfig) -> ArtifactStore:
    """Stockage d'artefacts déclaré dans ``storage.artifacts``, ou celui qui suit le journal."""
    storage = _storage(config)
    path = storage.artifacts_path
    if storage.artifacts_backend == "local" and path is not None:
        return LocalArtifactStore(path)
    return InMemoryArtifactStore()


def _storage(config: LoomConfig | StorageConfig) -> StorageConfig:
    return config if isinstance(config, StorageConfig) else config.storage


def create_idempotency_store(config: LoomConfig) -> IdempotencyStore | None:
    """Magasin d'idempotence déclaré dans ``storage.idempotency``.

    ``journal`` ne rend rien : il n'y a pas de magasin commun à monter, chaque
    run écrit ses enregistrements dans son journal et l'exécuteur le lui donne
    au moment de l'appel.
    """
    declared = config.storage.idempotency
    if declared.backend == "memory":
        return InMemoryIdempotency()
    if declared.backend == "postgres":
        try:
            from loom_ia.adapters.idempotency.postgres import PostgresIdempotency
        except ImportError as exc:
            raise _missing_asyncpg("Magasin d'idempotence") from exc
        return PostgresIdempotency(_dsn(declared, "Magasin d'idempotence"), role=declared.role)
    if declared.backend == "redis":
        try:
            from loom_ia.adapters.idempotency.redis import RedisIdempotency
        except ImportError as exc:
            raise _missing_redis("Magasin d'idempotence") from exc
        url = os.environ.get(declared.url_env or "", "")
        if not url:
            raise ConfigError(
                f"Magasin d'idempotence 'redis' : la variable {declared.url_env!r} est vide "
                "ou absente — c'est elle qui porte l'URL"
            )
        return RedisIdempotency(url)
    if declared.backend == "sqlite" and declared.path is not None:
        try:
            from loom_ia.adapters.idempotency.sqlite import SqliteIdempotency
        except ImportError as exc:
            raise ConfigError(
                "Magasin d'idempotence 'sqlite' : le paquet 'aiosqlite' n'est pas "
                "installé (installer l'extra : loom-ia[sqlite])"
            ) from exc
        return SqliteIdempotency(declared.path)
    return None


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
    idempotency: IdempotencyStore | None = None,
    agents: AgentResolver | None = None,
    breakers: CircuitBreakers | None = None,
    tenant: Tenant | None = None,
) -> Agent:
    """Assemble l'agent ``name`` de la config.

    ``mcp_pool`` porte les connexions MCP partagées ; sans lui, l'agent ouvre
    le sien et le ferme avec ``aclose``. ``artifacts`` est le stockage des
    fichiers ; sans lui, les pièces jointes sont refusées et les gros
    résultats tronqués. ``agents`` donne le contexte d'un sous-agent par son
    nom ; sans lui, l'agent monte ses sous-agents lui-même, au premier appel.
    ``breakers`` : disjoncteurs partagés (ceux de l'instance) ; sans eux,
    l'agent a les siens, communs à ses sous-agents. ``idempotency`` : magasin
    partagé (#49) ; sans lui, chaque appel reçoit celui de son journal.
    ``tenant`` : le client pour qui l'agent est monté (L1) ; ses secrets
    remplacent alors ``environ``.
    """
    spec = AgentRegistry.from_config(config).get(name)
    known = registry if registry is not None else load_registry(config)
    secrets = tenant.secrets if tenant is not None else environ
    tenant_id = tenant.id if tenant is not None else DEFAULT_TENANT
    variables: Mapping[str, JsonValue] = tenant.variables if tenant is not None else {}
    denied: frozenset[str] = tenant.denied if tenant is not None else frozenset()
    imposed: Mapping[str, Approval] = tenant.approvals if tenant is not None else {}
    tools = [_tool(declared, known, config.base_dir) for declared in spec.python_tools]
    roles = [role_definition(role, variables) for role in spec.roles]
    _check_names(spec, [tool.spec.name for tool in tools])

    clients: dict[str, ModelClient] = {}
    breakers = breakers if breakers is not None else CircuitBreakers()

    def client(model_id: str) -> ModelClient:
        if model_id not in clients:
            clients[model_id] = create_model_client(config.model_spec(model_id), environ=secrets)
        return clients[model_id]

    def links(model_ids: tuple[str, ...]) -> tuple[ModelLink, ...]:
        return tuple(ModelLink(config.model_spec(m), client(m)) for m in model_ids)

    judges = [
        JudgeGuard(
            judge_definition(spec, judged, known, config.base_dir, config.profile),
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
    announce(config, warnings)
    budgets = config.budget_of(spec.name)
    shared = any(
        ref.agent == spec.name and ref.budget_share is not None
        for agent in config.agents
        for ref in agent.subagents
    )
    compaction = config.sessions.compaction
    policies = build_policies(
        spec,
        known,
        config.base_dir,
        contracts=_has_contracts(config, spec),
        judges=judges,
        budget=BudgetGuard(budgets) if budgets.limited or shared else None,
        fidelity=(
            FidelityGuard()
            if spec.name == COMPACTION_AGENT
            and compaction is not None
            and compaction.fidelity_check
            else None
        ),
    )
    sources, owned = _mcp_sources(config, spec, secrets, mcp_pool, tenant_id)
    if spec.subagents and agents is None:
        nested = _SubAgents(
            config,
            store,
            registry=known,
            environ=environ,
            mcp_pool=mcp_pool,
            artifacts=artifacts,
            idempotency=idempotency,
            breakers=breakers,
            tenant=tenant,
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
    _check_durable_journal(config, spec, [*tools, *delegated], policies, imposed)
    _check_shared_idempotency(config, spec, [*tools, *delegated])
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
            approval=spec.approval,
            idempotency=idempotency,
            denied=denied,
            imposed=imposed,
        ),
        system=system_prompt(spec, variables),
        max_iterations=spec.max_iterations,
        timeout=spec.timeout,
        max_tokens=llm.max_tokens,
        params=llm.params,
        on_chunk=on_chunk,
        attachments=config.execution.attachments,
        policies=policies,
        output=spec.output,
        stream_output=stream_output(spec, policies),
        fallbacks=links(spec.main.fallbacks),
        breakers=breakers,
        approval=spec.approval,
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
    fidelity: FidelityGuard | None = None,
) -> Policies:
    """Politiques de l'agent, résolues et contrôlées, dans l'ordre déclaré.

    Les politiques fournies passent en tête : le guard des contrats quand
    l'agent en déclare (``contracts``, déduit de l'agent si absent), puis les
    juges (``judges``) — la forme d'une sortie est contrôlée avant son fond —,
    puis le budget (``budget``). ``fidelity`` n'est branché que sur l'agent
    interne de compaction (#23).
    """
    bound: list[BoundPolicy] = []
    if fidelity is not None:
        # Le guard borne lui-même ses tentatives, puis garde le résumé.
        bound.append(
            BoundPolicy(
                policy=fidelity,
                name=fidelity.name,
                points=fidelity.points,
                max_attempts=None,
            )
        )
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


def system_prompt(spec: AgentSpec, variables: Mapping[str, JsonValue] | None = None) -> str:
    """Prompt système de l'orchestrateur, ses variables rendues."""
    return prompt_text(spec.main, variables)


def prompt_text(role: BaseRole, variables: Mapping[str, JsonValue] | None = None) -> str:
    """Prompt système d'un rôle : texte en ligne ou fichier, ses variables rendues.

    Un prompt appartient à la configuration : il est le même pour tout le
    monde (§6). Ce qu'un client y change, ce sont les valeurs qu'il cite —
    ``{{ entreprise }}`` —, et le chargement a déjà vérifié qu'aucune ne
    manque à aucun client (M5). Un prompt sans ``{{ }}`` traverse ce rendu
    sans y laisser de trace.
    """
    source = system_text(role)
    if "{{" not in source:
        return source
    return Template.parse(source).render(dict(variables or {}))


def role_definition(
    role: RoleSpec, variables: Mapping[str, JsonValue] | None = None
) -> RoleDefinition:
    """Rôle délégué tel que le moteur l'exécute."""
    template = Template.parse(role.input_template) if role.input_template is not None else None
    return RoleDefinition(
        name=role.name,
        description=role.description,
        system=prompt_text(role, variables),
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
    profile: str | None = None,
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
    profiles = judge.when.profiles
    return JudgeDefinition(
        name=name,
        role=role.name if role is not None else None,
        criteria=judge.criteria,
        context=tuple(_context_item(item) for item in judge.context),
        sample=judge.when.sample,
        condition=condition,
        tenants=frozenset(tenants) if tenants is not None else None,
        profiles=frozenset(profiles) if profiles is not None else None,
        profile=profile,
        repair=judge.repair,
        on_failure=judge.on_failure,
        fallback_message=judge.fallback_message,
        max_tokens=judge.llm.max_tokens,
        params=judge.llm.params,
        timeout=judge.timeout,
        on_error=judge.on_error,
    )


def _check_durable_journal(
    config: LoomConfig,
    spec: AgentSpec,
    tools: Sequence[AnyTool],
    policies: Policies,
    imposed: Mapping[str, Approval] | None = None,
) -> None:
    """Un agent qui peut se mettre en pause exige un journal durable (#28).

    En pause, le run n'existe plus que dans le journal : le process peut
    s'arrêter, l'approbateur prendre son temps, un autre worker reprendre. Un
    journal en mémoire perdrait le run à la première fermeture, et
    l'approbation n'aurait rien à reprendre. Une erreur, donc, et non un
    avertissement — sauf en profil ``dev`` (5.5a), où perdre un run en pause à
    la fermeture est le prix d'un essai.
    """
    if config.storage.events.backend not in DURABLE_BACKENDS:
        forced = imposed or {}
        obligatoires = [
            t.spec.name for t in tools if forced.get(t.spec.name, t.spec.approval) == "always"
        ]
        pausing = sorted({b.name for b in policies.bound if "pause" in b.policy.decisions})
        causes = [
            *(f"outil {name!r} en approval: always" for name in obligatoires),
            *(f"politique {name!r} qui peut rendre Pause" for name in pausing),
        ]
        if causes:
            # Assoupli en dev seulement : sur une machine, perdre un run en
            # pause à la fermeture est le prix d'un essai (M4).
            announce(
                config,
                [
                    f"Agent {spec.name!r} : {', '.join(causes)} — une approbation exige un "
                    f"journal durable ({' ou '.join(DURABLE_BACKENDS)}), "
                    f"pas {config.storage.events.backend!r} (#28)"
                ],
                refuse=not config.lax,
            )


def _check_shared_idempotency(
    config: LoomConfig, spec: AgentSpec, tools: Sequence[AnyTool]
) -> None:
    """Une clé métier exige un magasin partagé et durable (#49).

    Une clé métier dit « cette relance est déjà partie » à qui la demande,
    d'un run à l'autre et d'une conversation à l'autre. Le magasin ``journal``
    ne voit que son run et ``memory`` que son process : la promesse serait
    tenue là où elle ne sert à rien, et rompue partout ailleurs. Une erreur,
    donc — un doublon silencieux se remarque trop tard.
    """
    declared = config.storage.idempotency
    if declared.shared:
        return
    metier = [tool.spec.name for tool in tools if tool.spec.business_key]
    if metier:
        raise ConfigError(
            f"Agent {spec.name!r} : outil(s) {', '.join(repr(n) for n in metier)} à clé "
            f"métier — il leur faut un magasin d'idempotence partagé et durable "
            f"({' ou '.join(SHARED_IDEMPOTENCY)}), pas {declared.backend!r} (#49)"
        )


def announce(config: LoomConfig, warnings: Sequence[str], *, refuse: bool | None = None) -> None:
    """Dit ce qui ne va pas — un avertissement, ou une erreur en profil ``prod`` (M4).

    C'est ici que les profils prennent leur sens : la même config charge sur
    une machine et refuse de partir en production. ``refuse`` tranche à la
    place du profil quand la règle a son propre arbitrage.
    """
    if not warnings:
        return
    said = " ; ".join(warnings)
    if config.strict if refuse is None else refuse:
        raise ConfigError(f"Profil prod : {said}" if config.strict else said)
    for warning in warnings:
        logger.warning(warning)


def storage_warnings(config: LoomConfig) -> list[str]:
    """Ce qui ne va pas ensemble dans un service à plusieurs process (5.3).

    Un tel service partage son journal et ses nouvelles, mais **pas forcément
    ses fichiers** : les artefacts ``local`` exigent un dossier commun aux
    process, et ``memory`` ne sort pas de celui qui écrit. Un run repris
    ailleurs (5.3b) ne retrouverait alors ni ses pièces jointes ni ses
    résultats déportés.

    Un avertissement, pas une erreur : un volume partagé est un montage
    parfaitement légitime, que la config ne peut pas distinguer d'un dossier
    propre à chaque machine, et un agent qui ne produit aucun fichier n'est de
    toute façon pas concerné. Le stockage partagé (GCS) est reporté avec 5.3d.
    """
    storage = config.storage
    if not (storage.queue.brokered or storage.bus.shared):
        return []
    eparpille = (
        "file servie par un courtier" if storage.queue.brokered else "bus partagé entre process"
    )
    if storage.artifacts_backend == "local":
        return [
            f"Stockage : {eparpille} et artefacts 'local' ({storage.artifacts_path}) — les "
            "process doivent partager ce dossier, sinon un run repris ailleurs ne retrouvera "
            "ni ses pièces jointes ni ses résultats déportés"
        ]
    if storage.artifacts_backend == "memory":
        return [
            f"Stockage : {eparpille} et artefacts 'memory' — un fichier écrit par un process "
            "est perdu pour les autres et à sa fermeture ; un run repris ailleurs ne le "
            "retrouvera pas"
        ]
    return []


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
        case ToolResultsContext(tool_results=tools, scope=scope):
            return ToolResults(tools=tools, scope=scope)
        case LastTurnsContext(last_turns=count):
            return LastTurns(count=count)
        case "user_input" | "caller_context" | "attachments" | "session_summary":
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
        idempotency: IdempotencyStore | None,
        breakers: CircuitBreakers,
        tenant: Tenant | None = None,
    ) -> None:
        self._config = config
        self._store = store
        self._registry = registry
        self._environ = environ
        self._mcp_pool = mcp_pool
        self._artifacts = artifacts
        self._idempotency = idempotency
        self._breakers = breakers
        self._tenant = tenant
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
                idempotency=self._idempotency,
                agents=self,
                breakers=self._breakers,
                tenant=self._tenant,
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
    tenant_id: TenantId = DEFAULT_TENANT,
) -> tuple[list[ToolSource], tuple[_Closable, ...]]:
    """Sources d'outils des serveurs MCP de l'agent, et le pool créé pour lui s'il en faut un.

    Portée ``tenant`` (#34) : la connexion vit dans le pool comme une
    ``shared``, mais sous une clé qui nomme le client — deux clients du même
    serveur ont chacun la leur, ouverte avec ses identifiants.
    """
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
    if pool is None and any(server.scope in {"shared", "tenant"} for server in servers):
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
        key = f"{server.name}#{tenant_id}" if server.scope == "tenant" else server.name
        sources.append(McpSource(server, selection, factory=factory, pool=pool, pool_key=key))
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
