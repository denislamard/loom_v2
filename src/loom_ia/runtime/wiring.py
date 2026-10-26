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
points (``Pause`` : ``before_tool`` seulement, J4.3), un nom unique dans l'agent.

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

import importlib.util
import logging
import os
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Final, Protocol, cast

from pydantic import JsonValue, ValidationError

from loom_ia.adapters.artifacts import (
    InMemoryArtifactStore,
    LocalArtifactStore,
    SealingArtifactStore,
)
from loom_ia.adapters.idempotency import InMemoryIdempotency
from loom_ia.adapters.models import KEY_VARIABLES, ModelConfigError, create_model_client
from loom_ia.adapters.queue import AsyncioTaskQueue, Handler
from loom_ia.adapters.stores import (
    PLAIN,
    InMemoryEventStore,
    JournalCodec,
    JsonlEventStore,
    SealingCodec,
)
from loom_ia.agents.registry import AgentRegistry
from loom_ia.agents.spec import (
    AgentSpec,
    BaseRole,
    JudgeSpec,
    LastTurnsContext,
    McpTools,
    PolicyRef,
    PythonTool,
    RoleSpec,
    SessionStateContext,
    SourceTools,
    StateKey,
    SubAgentRef,
    ToolResultsContext,
    builtin_settings,
    system_text,
)
from loom_ia.agents.spec import ContextItem as DeclaredContext
from loom_ia.agents.spec import RoleContextItem as DeclaredRoleContext
from loom_ia.config.compaction import COMPACTION_AGENT
from loom_ia.config.env_file import DEFAULT_NAME as ENV_FILE_DEFAULT
from loom_ia.config.errors import ConfigError, from_validation
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
from loom_ia.core import SETUP
from loom_ia.core.closing import Closing
from loom_ia.core.model import (
    ALLOWED_DECISIONS,
    DEFAULT_TENANT,
    MCP_PREFIX_SEPARATOR,
    RESERVED_PREFIX,
    Approval,
    ModelSpec,
    OutputContract,
    StreamOutput,
    TenantId,
    ToolOverrides,
)
from loom_ia.core.ports import (
    ArtifactStore,
    AssessedTool,
    ChunkCallback,
    EventBus,
    EventStore,
    IdempotencyStore,
    JobKind,
    Keyring,
    ModelClient,
    Policy,
    SealError,
    SecretProvider,
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
    Recorded,
    RoleContextItem,
    RoleDefinition,
    RoleTool,
    RunContext,
    SessionState,
    StateKeyRule,
    StateRules,
    StateSourceRule,
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
from loom_ia.runtime.sources import PackagedSource, described, source_factory
from loom_ia.telemetry import RunExporter, SpanSink, configure_logging
from loom_ia.tenancy import EnvironmentSecrets, Tenant
from loom_ia.tools import ConfiguredTool, FunctionTool, IdempotentTool, configure
from loom_ia.tools_builtin import (
    BUILTIN_TOOLS,
    BuiltinContext,
    Protected,
    builtin_entry,
    builtin_params,
    builtin_tool,
    is_builtin,
)
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
    # Clients de modèle que l'agent a créés (``main``, rôles, juges), un par
    # modèle ; un client fourni par l'appelant (``provided``) n'en est pas.
    clients: tuple[ModelClient, ...] = ()
    # Ressources ouvertes pour cet agent seul (connexions MCP partagées sans pool fourni).
    owned: tuple[_Closable, ...] = ()

    async def aclose(self) -> None:
        """Ferme les clients de modèle et ce que l'agent possède ; le journal reste à l'appelant.

        Chaque fermeture est tentée, même si une précédente a levé ; l'échec
        est levé une fois tout fermé (``Closing``).
        """
        closing = Closing()
        for client in self.clients:
            with closing:
                await client.aclose()
        for resource in self.owned:
            with closing:
                await resource.aclose()
        closing.raise_if_failed()


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


def create_keyring(config: LoomConfig, secrets: SecretProvider | None = None) -> Keyring | None:
    """Trousseau des clients déclaré dans ``storage.encryption`` ; ``None`` sans sceau.

    Les clés viennent des secrets, jamais de la config : celle-ci ne nomme
    que le secret qui les porte, et chaque client redirige ce nom vers sa
    variable (5.1a).
    """
    declared = config.storage.encryption
    if declared is None:
        return None
    try:
        from loom_ia.adapters.crypto import SecretKeyring
    except ImportError as exc:
        raise ConfigError(
            "Chiffrement : le paquet 'cryptography' n'est pas installé "
            "(installer l'extra : loom-ia[crypto])"
        ) from exc
    table = secrets if secrets is not None else EnvironmentSecrets(config)
    return SecretKeyring(table, declared.keys)


def create_journal_codec(config: LoomConfig, secrets: SecretProvider | None = None) -> JournalCodec:
    """Codec du journal : scellant quand ``storage.encryption`` est déclaré."""
    keyring = create_keyring(config, secrets)
    return PLAIN if keyring is None else SealingCodec(keyring)


def create_event_store(
    config: LoomConfig | StorageConfig, *, codec: JournalCodec | None = None
) -> EventStore:
    """Journal déclaré dans ``storage.events``, d'une config ou d'un client.

    ``codec`` dit comment une ligne est rangée : en clair, ou la charge
    scellée. Sans lui, il est tiré de la config — un journal de client n'en
    déclare pas (le sceau se déclare à la racine), si bien qu'un journal créé
    pour un client reçoit celui de l'instance.
    """
    events = _storage(config).events
    chosen = codec if codec is not None else _own_codec(config)
    if events.backend == "postgres":
        try:
            from loom_ia.adapters.postgres.pool import PoolLimits
            from loom_ia.adapters.stores.postgres import PostgresEventStore
        except ImportError as exc:
            raise _missing_asyncpg("Journal") from exc
        return PostgresEventStore(
            _dsn(events, "Journal"),
            role=events.role,
            codec=chosen,
            limits=PoolLimits(**events.pool.model_dump()),
        )
    if events.path is not None:
        if events.backend == "jsonl":
            return JsonlEventStore(events.path, codec=chosen)
        if events.backend == "sqlite":
            try:
                from loom_ia.adapters.stores.sqlite import SqliteEventStore
            except ImportError as exc:
                raise ConfigError(
                    "Journal 'sqlite' : le paquet 'aiosqlite' n'est pas installé "
                    "(installer l'extra : loom-ia[sqlite])"
                ) from exc
            return SqliteEventStore(events.path, codec=chosen)
    return InMemoryEventStore()


def _own_codec(config: LoomConfig | StorageConfig) -> JournalCodec:
    if isinstance(config, StorageConfig) or config.storage.encryption is None:
        return PLAIN
    return create_journal_codec(config)


def _own_keyring(config: LoomConfig | StorageConfig) -> Keyring | None:
    if isinstance(config, StorageConfig):
        return None
    return create_keyring(config)


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
    return AsyncioTaskQueue(
        handlers,
        shutdown_timeout=config.execution.shutdown_timeout,
        max_running=declared.max_running,
    )


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


def create_run_exporter(
    config: LoomConfig, store: EventStore, environ: Mapping[str, str] | None = None
) -> tuple[RunExporter | None, list[str]]:
    """Les collecteurs de ``telemetry.exporters``, et ce qui empêche d'en monter (6.1a).

    Un collecteur dont la variable d'adresse est vide n'est **pas monté**, et
    c'est dit — un avertissement, une erreur en profil prod : le SDK enverrait
    sinon à son adresse par défaut, ce qu'on n'a pas demandé. Sans collecteur
    monté, pas d'abonné du tout : rien ne coûte rien.
    """
    declared = config.telemetry.exporters
    if not declared:
        return None, []
    try:
        from loom_ia.adapters.telemetry.otel import OtelSpanSink, parse_headers
    except ImportError as exc:
        raise ConfigError(
            "Télémétrie : 'telemetry.exporters' demande OpenTelemetry, qui n'est pas installé "
            "(installer l'extra : loom-ia[otel])"
        ) from exc
    variables = os.environ if environ is None else environ
    sinks: list[SpanSink] = []
    warnings: list[str] = []
    for exporter in declared:
        endpoint = variables.get(exporter.endpoint_env, "")
        if not endpoint:
            warnings.append(_unmounted(exporter.endpoint_env))
            continue
        headers = (
            parse_headers(variables.get(exporter.headers_env))
            if exporter.headers_env is not None
            else {}
        )
        sinks.append(
            OtelSpanSink.otlp(
                endpoint,
                protocol=exporter.protocol,
                headers=headers,
                timeout=exporter.timeout,
                service_name=exporter.service_name,
            )
        )
    if not sinks:
        return None, warnings
    exporter = RunExporter(
        store,
        sinks,
        content_for=lambda tenant_id: config.capture_for(tenant_id).exports == "content",
        redactor=config.telemetry.redaction.redactor(),
    )
    return exporter, warnings


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
    # Le schéma complet de chaque table, migrations et table des versions
    # comprises, comme ``sql.ddl()`` : au test F5 du 19/10, ``EVENTS_DDL`` seul
    # (le schéma de la 2.0) laissait la base sans ``loom_schema`` ; à
    # l'ouverture, loom tentait la migration avec le rôle de connexion, qui
    # n'en a pas le droit (« permission denied for schema public »).
    declared = (
        (
            storage.events,
            sql.EVENTS_TABLE,
            f"{sql.EVENTS_DDL}\n{sql.EVENTS_UPGRADE}",
            False,
        ),
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
    # Lecture seule de la table des versions : de quoi vérifier sans pouvoir migrer.
    for role in sorted(roles):
        blocks.append(f"GRANT SELECT ON {sql.SCHEMA_TABLE} TO {role};")
    return "\n".join(blocks) + "\n"


def create_artifact_store(
    config: LoomConfig | StorageConfig, *, keyring: Keyring | None = None
) -> ArtifactStore:
    """Stockage d'artefacts déclaré dans ``storage.artifacts``, ou celui qui suit le journal.

    Avec un trousseau, les octets sont scellés comme les charges du journal :
    un gros résultat déporté ou une pièce jointe porte du contenu, et le
    laisser en clair ferait du sceau une demi-mesure.
    """
    storage = _storage(config)
    path = storage.artifacts_path
    inner: ArtifactStore = (
        LocalArtifactStore(path)
        if storage.artifacts_backend == "local" and path is not None
        else InMemoryArtifactStore()
    )
    ring = keyring if keyring is not None else _own_keyring(config)
    return inner if ring is None else SealingArtifactStore(inner, ring)


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
            from loom_ia.adapters.postgres.pool import PoolLimits
        except ImportError as exc:
            raise _missing_asyncpg("Magasin d'idempotence") from exc
        return PostgresIdempotency(
            _dsn(declared, "Magasin d'idempotence"),
            role=declared.role,
            limits=PoolLimits(**declared.pool.model_dump()),
        )
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
    models: Callable[[ModelSpec], ModelClient] | None = None,
    provided: Mapping[str, ModelClient] | None = None,
) -> Agent:
    """Assemble l'agent ``name`` de la config.

    ``models`` remplace la fabrique des clients de modèle (J6.2a) : le rejeu
    monte ainsi le même agent, avec des clients qui répondent depuis le journal.
    ``provided`` donne des clients tout faits, par identifiant de modèle (O2,
    6.3c) : ils servent à la place de ceux que la config déclare, et restent à
    l'appelant, qui les ferme.

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
    vaulted = tenant is not None and tenant.provided
    tenant_id = tenant.id if tenant is not None else DEFAULT_TENANT
    variables: Mapping[str, JsonValue] = tenant.variables if tenant is not None else {}
    denied: frozenset[str] = tenant.denied if tenant is not None else frozenset()
    imposed: Mapping[str, Approval] = tenant.approvals if tenant is not None else {}
    redirects: Mapping[str, str] = tenant.spec.secrets if tenant and tenant.spec else {}
    mapped: Mapping[str, str] = tenant.spec.models if tenant and tenant.spec else {}
    provided_context = BuiltinContext(
        base_dir=config.base_dir,
        protected=_protected_paths(config),
        secrets=secrets,
        protected_dsns=_protected_dsns(config),
    )
    tools = [
        _tool(declared, known, config.base_dir, spec.name, provided=provided_context)
        for declared in spec.python_tools
    ]
    # Un outil fourni qui ouvre des connexions (loom.sql) les ferme avec l'agent.
    closing = _builtin_closables(spec.python_tools, tools)
    roles = [role_definition(role, variables, state=spec.state) for role in spec.roles]
    _check_names(spec, [tool.spec.name for tool in tools])
    _check_state(spec, tools)

    clients: dict[str, ModelClient] = {}
    breakers = breakers if breakers is not None else CircuitBreakers()

    # Échanges bruts (6.1b) : ceux de ce client de loom, pour tous ses modèles.
    capture = config.capture_for(tenant_id)
    raw = capture.raw_max_bytes if capture.raw_exchanges else None

    given = provided or {}

    def client(model_id: str) -> ModelClient:
        if model_id in given:
            return given[model_id]
        if model_id not in clients:
            spec = config.model_spec(model_id)
            if models is not None:
                clients[model_id] = models(spec)
                return clients[model_id]
            try:
                made = create_model_client(spec, environ=secrets, record=raw is not None)
            except ModelConfigError as exc:
                read = secrets if secrets is not None else os.environ
                said = _missing_secret(tenant_id, redirects, read, [spec.api_key_env])
                if said is None and vaulted and _absent(read, spec.api_key_env):
                    # Au test M3 du 22/10, un secret absent du gestionnaire se disait
                    # « variable d'environnement » : il ne vient pas de là.
                    raise ModelConfigError(
                        f"Modèle {model_id!r} : {_unprovided(tenant_id, [spec.api_key_env])}"
                    ) from exc
                if said is None:
                    raise
                model = f"{spec.model} pour ce client" if model_id in mapped else spec.model
                raise ModelConfigError(f"{said} — modèle {model_id!r} ({model})") from exc
            clients[model_id] = made if raw is None else Recorded(made, raw)
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
        *judge_warnings(config, spec, tenant=tenant),
        *budget_warnings(config, spec),
        *fallback_warnings(config, spec),
    ]
    announce(config, warnings)
    # Un double référencement peut être voulu : il avertit, en prod comme ailleurs.
    announce(config, overlap_warnings(spec), refuse=False)
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
    owned: tuple[_Closable, ...] = ()
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
    _check_durable_journal(config, spec, [*tools, *delegated], policies, imposed, tenant)
    _check_shared_idempotency(config, spec, [*tools, *delegated])
    # Les sources et le pool MCP se créent après les contrôles : un montage refusé n'en laisse pas.
    sources, opened = _mcp_sources(
        config, spec, secrets, mcp_pool, tenant_id, redirects, vaulted=vaulted
    )
    packaged = _packaged_sources(config, spec, secrets)
    sources = [*sources, *packaged]
    owned = (*opened, *packaged, *closing, *owned)
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
            circuits={
                **{server.name: server.circuit_breaker for server in config.mcp_servers},
                # Pas de disjoncteur pour une source de paquet : à elle de
                # dire qu'elle ne répond pas (``SourceUnavailable``).
                **{declared.name: None for declared in config.tool_sources},
            },
            approval=spec.approval,
            idempotency=idempotency,
            denied=denied,
            imposed=imposed,
            state_rules=state_rules(config, spec),
        ),
        system=system_prompt(spec, variables),
        max_iterations=spec.max_iterations,
        timeout=spec.timeout,
        max_tokens=llm.max_tokens,
        params=llm.params,
        on_chunk=on_chunk,
        attachments=config.execution.attachments,
        keep_images=config.sessions.media.keep_images,
        policies=policies,
        output=spec.output,
        stream_output=stream_output(spec, policies),
        fallbacks=links(spec.main.fallbacks),
        breakers=breakers,
        approval=spec.approval,
        state_keys=spec.main.state_keys,
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


def state_rules(config: LoomConfig, spec: AgentSpec) -> StateRules:
    """Clés du state que déclare l'agent, telles que le moteur les applique, et leurs limites."""
    limits = config.sessions.state
    return StateRules(
        {
            name: StateKeyRule(
                schema=key.json_schema,
                description=key.description,
                sources={
                    source.tool: StateSourceRule(
                        path=source.path, value=source.value, on_missing=source.on_missing
                    )
                    for source in key.sources
                },
                required=key.required,
                default=key.default,
            )
            for name, key in spec.state.items()
        },
        max_value_bytes=limits.max_value_bytes,
        max_total_bytes=limits.max_total_bytes,
    )


def stream_output(spec: AgentSpec, policies: Policies) -> StreamOutput:
    """Diffusion de la réponse finale : celle déclarée, sinon selon ses contrôles (#11)."""
    if spec.stream_output is not None:
        return spec.stream_output
    guarded = bool(policies.at("on_output")) or any(
        role.terminal and (role.output is not None or role.judge is not None) for role in spec.roles
    )
    return "after_guards" if guarded else "live"


def _has_contracts(config: LoomConfig, spec: AgentSpec) -> bool:
    """Vrai si un contrat s'applique à l'agent, y compris par un serveur MCP ou une source."""
    servers = [config.mcp_server(ref.mcp) for ref in spec.mcp_tools]
    packaged = [config.tool_source(ref.source) for ref in spec.source_tools]
    return (
        spec.contracts
        or any(o.output is not None for s in servers for o in s.tools.values())
        or any(o.output is not None for s in packaged for o in s.tools.values())
    )


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
    role: RoleSpec,
    variables: Mapping[str, JsonValue] | None = None,
    *,
    state: Mapping[str, StateKey] | None = None,
) -> RoleDefinition:
    """Rôle délégué tel que le moteur l'exécute ; ``state`` : les clés que déclare l'agent."""
    template = Template.parse(role.input_template) if role.input_template is not None else None
    return RoleDefinition(
        name=role.name,
        description=role.description,
        system=prompt_text(role, variables),
        input_schema=role.input_schema,
        template=template,
        context=tuple(_role_context_item(item, state or {}) for item in role.context),
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
    tenant: Tenant | None = None,
) -> None:
    """Un agent qui peut se mettre en pause exige un journal durable (#28).

    En pause, le run n'existe plus que dans le journal : le process peut
    s'arrêter, l'approbateur prendre son temps, un autre worker reprendre. Un
    journal en mémoire perdrait le run à la première fermeture, et
    l'approbation n'aurait rien à reprendre. Une erreur, donc, et non un
    avertissement — sauf en profil ``dev`` (5.5a), où perdre un run en pause à
    la fermeture est le prix d'un essai.

    Le journal jugé est celui du client pour qui l'agent est monté : son
    ``storage`` s'il en déclare un, sinon celui de la racine. Les outils des
    serveurs MCP et des paquets ne sont connus qu'à la connexion ; la garde juge
    donc ce que la **configuration** en dit (``tools`` du serveur ou de la
    source, de la référence de l'agent, approbations imposées par le client).
    Elle ne voit pas l'``approval: always`` qu'un outil de paquet porterait en
    lui-même.
    """
    own = tenant.storage if tenant is not None else None
    storage = own if own is not None else config.storage
    backend = storage.events.backend
    if backend not in DURABLE_BACKENDS:
        forced = imposed or {}
        obligatoires = [
            t.spec.name for t in tools if forced.get(t.spec.name, t.spec.approval) == "always"
        ]
        obligatoires += [
            name
            for name in _sources_asking_approval(config, spec, forced)
            if name not in obligatoires
        ]
        pausing = sorted({b.name for b in policies.bound if "pause" in b.policy.decisions})
        # Un outil qui évalue ses appels n'en approuve que certains : le dire.
        by_call = {
            t.spec.name for t in tools if _assesses_approval(t) and t.spec.name not in forced
        }
        causes = [
            *(
                f"outil {name!r} "
                + (
                    "dont certains appels attendent une approbation"
                    if name in by_call
                    else "en approval: always"
                )
                for name in obligatoires
            ),
            *(f"politique {name!r} qui peut rendre Pause" for name in pausing),
        ]
        if causes:
            # Assoupli en dev seulement : sur une machine, perdre un run en
            # pause à la fermeture est le prix d'un essai (M4).
            whose = (
                f", celui du client {tenant.id!r}" if tenant is not None and own is not None else ""
            )
            # Dit une fois par client monté : au test M4 du 23/10, la même ligne
            # revenait sans dire pour quel client.
            agent = f"Agent {spec.name!r}" + (
                f" (client {tenant.id!r})" if tenant is not None else ""
            )
            announce(
                config,
                [
                    f"{agent} : {', '.join(causes)} — une approbation exige un "
                    f"journal durable ({' ou '.join(DURABLE_BACKENDS)}), "
                    f"pas {backend!r}{whose} (#28)"
                ],
                refuse=not config.lax,
            )


def _assesses_approval(tool: object) -> bool:
    """Vrai si l'outil décide de l'approbation appel par appel, sans que la config l'ait fixée."""
    if isinstance(tool, ConfiguredTool):
        return "approval" not in tool.fixed and _assesses_approval(tool.tool)
    return isinstance(tool, AssessedTool)


def _sources_asking_approval(
    config: LoomConfig, spec: AgentSpec, imposed: Mapping[str, Approval]
) -> list[str]:
    """Outils MCP ou de paquet que la configuration met en ``approval: always``.

    Sans connexion : la liste des outils d'un serveur ou d'une source n'est
    connue qu'à l'ouverture. On lit ce que la config en déclare — ``tools`` du
    serveur ou de la source, puis ceux de la référence de l'agent, qui l'emportent
    —, puis l'approbation que le client impose, qui l'emporte sur tout. Une
    approbation imposée à un nom que la config ne cite pas compte aussi, dès
    qu'il porte le préfixe d'une source de l'agent et passe son ``include`` ou
    son ``exclude`` : le client l'a voulue, et on ne sait pas si l'outil existe.
    """
    refs: list[tuple[McpTools | SourceTools, Mapping[str, ToolOverrides]]] = [
        *((ref, config.mcp_server(ref.mcp).tools) for ref in spec.mcp_tools),
        *((ref, config.tool_source(ref.source).tools) for ref in spec.source_tools),
    ]
    asking: list[str] = []
    for ref, declared in refs:
        prefix = f"{ref.prefix}{MCP_PREFIX_SEPARATOR}"
        named = [*declared, *(short for short in ref.tools if short not in declared)]
        for short in named:
            approval = next(
                (
                    found.approval
                    for found in (ref.tools.get(short), declared.get(short))
                    if found is not None and found.approval is not None
                ),
                None,
            )
            name = f"{prefix}{short}"
            if _selects(ref, short) and imposed.get(name, approval) == "always":
                asking.append(name)
        asking += [
            name
            for name, approval in imposed.items()
            if approval == "always"
            and name.startswith(prefix)
            and name[len(prefix) :] not in named
            and _selects(ref, name[len(prefix) :])
        ]
    return asking


def _selects(ref: McpTools | SourceTools, short: str) -> bool:
    """Vrai si la référence expose l'outil ``short`` (``include`` ou ``exclude``)."""
    return (ref.include is None or short in ref.include) and (
        ref.exclude is None or short not in ref.exclude
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
        logger.warning(warning, extra={SETUP: True})


def exporter_warnings(config: LoomConfig, environ: Mapping[str, str] | None = None) -> list[str]:
    """Les collecteurs que leur variable d'adresse, vide, laisse non montés (6.1a).

    Le même texte que ``create_run_exporter`` dit au montage : ``loom validate``
    l'affiche à sa place.
    """
    variables = os.environ if environ is None else environ
    return [
        _unmounted(exporter.endpoint_env)
        for exporter in config.telemetry.exporters
        if not variables.get(exporter.endpoint_env, "")
    ]


def _unmounted(variable: str) -> str:
    return (
        f"Télémétrie : la variable {variable!r} est vide ou absente — "
        "ce collecteur n'est pas monté, aucune trace n'en partira"
    )


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


def model_warnings(config: LoomConfig) -> list[str]:
    """Ce qu'un modèle gagnerait à déclarer, et que loom ne peut pas deviner.

    Un modèle ``sdk: anthropic`` sur l'API officielle (sans ``base_url``) qui
    voit les images peut les recevoir **dans** le résultat d'un outil
    (``tool_result_media: true``). Sans cette capacité, une image rendue par un
    outil part dans un message utilisateur placé après les résultats, et le
    modèle la prend pour une image envoyée par l'utilisateur (test D8 du 18/10).
    Ce n'est pas un défaut que loom choisit seul : ``sdk: anthropic`` désigne
    un protocole, que des fournisseurs compatibles (MiniMax…) reprennent sans
    forcément accepter une image dans un résultat d'outil. Un avertissement
    donc, dans tous les profils, et seulement pour l'adresse officielle.
    """
    return [
        f"Modèle {spec.id!r} : sdk anthropic avec vision, mais sans "
        "capabilities.tool_result_media — les images rendues par un outil partiront dans un "
        "message utilisateur à part ; l'API Anthropic les accepte dans le résultat de l'outil "
        "(déclarer tool_result_media: true)"
        for spec in config.models
        if spec.sdk == "anthropic"
        and spec.base_url is None
        and spec.capabilities.vision
        and not spec.capabilities.tool_result_media
    ]


def key_warnings(config: LoomConfig) -> list[str]:
    """Modèles de l'API officielle déclarés sans ``api_key_env`` : aucune clé n'est envoyée.

    Sans ``api_key_env``, loom envoie une clé de remplacement, prévue pour un
    serveur local (vLLM, Ollama) ou compatible qui n'en demande pas. Vers
    l'API officielle (``sdk: anthropic`` ou ``openai`` sans ``base_url``),
    chaque appel est donc refusé — au 20/10, en « 401 invalid x-api-key », sans
    que rien n'ait signalé l'oubli. Un avertissement, une erreur en profil
    ``prod`` : un service qui ne peut appeler aucun de ses modèles ne part pas.
    """
    return [
        f"Modèle {spec.id!r} : sdk {spec.sdk} vers l'API officielle sans api_key_env — "
        "aucune clé d'API n'est envoyée, chaque appel sera refusé (401) ; déclarer "
        f"api_key_env: {KEY_VARIABLES.get(spec.sdk, 'NOM_DE_LA_VARIABLE')}"
        for spec in config.models
        if spec.sdk in KEY_VARIABLES and spec.base_url is None and spec.api_key_env is None
    ]


def encryption_warnings(config: LoomConfig, keyring: Keyring | None) -> list[str]:
    """Ce que le trousseau dit des clients déclarés (5.5b).

    Deux choses valent d'être dites au chargement. Un client **sans clé** :
    son journal reste illisible et ses runs seront refusés à leur premier
    événement. C'est l'effet voulu d'un effacement de clé, et la config ne
    peut pas le distinguer d'une variable oubliée — donc un avertissement, pas
    un refus, même en profil prod : refuser de démarrer parce qu'un client a
    été effacé arrêterait le service de tous les autres.

    Deux clients qui **partagent une clé** : c'est un montage possible, mais
    effacer cette clé efface le contenu des deux, et c'est le genre de chose
    qu'on découvre au mauvais moment.
    """
    if keyring is None:
        return []
    warnings: list[str] = []
    sealing: dict[str, list[TenantId]] = {}
    for tenant_id in config.tenant_ids:
        try:
            ciphers = keyring.ciphers(tenant_id)
        except SealError as error:
            warnings.append(f"Chiffrement : {error}")
            continue
        sealing.setdefault(ciphers[0].key_id, []).append(tenant_id)
    for key_id, shared in sealing.items():
        if len(shared) > 1:
            named = ", ".join(repr(str(tenant_id)) for tenant_id in shared)
            warnings.append(
                f"Chiffrement : les clients {named} scellent avec la même clé ({key_id}) — "
                "l'effacer rendrait leurs journaux illisibles ensemble ; une clé par client "
                "demande une redirection 'secrets' par client"
            )
    return warnings


def budget_warnings(config: LoomConfig, spec: AgentSpec) -> list[str]:
    """Budget en dollars sur un agent dont un modèle n'a pas de tarif (backlog #010).

    Ses appels comptent 0 $ : le plafond ne les voit pas. En profil prod,
    ``announce`` en fait une erreur (5.5a) ; un budget en tokens reste efficace.
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


def judge_warnings(
    config: LoomConfig, spec: AgentSpec, *, tenant: Tenant | None = None
) -> list[str]:
    """Avertissements sur les juges de l'agent (E6, #21) ; des erreurs en profil prod (5.5a).

    ``tenant`` : le client pour qui l'agent est monté, sa correspondance des
    modèles déjà appliquée à ``config``. Un client déclaré est nommé, et un
    modèle qu'il remplace est dit avec sa cible : au test E6 du 19/10, « (OPUS
    et HAIKU) » pour un client chez qui OPUS devient HAIKU ne disait ni le
    client, ni que les deux sont le même modèle.
    """
    warnings: list[str] = []
    declared = tenant is not None and tenant.spec is not None
    mapped: Mapping[str, str] = tenant.spec.models if tenant is not None and tenant.spec else {}

    def said(model: str) -> str:
        target = mapped.get(model)
        return model if target is None else f"{model} → {target}"

    for name, role, judge in spec.judges:
        label = f"Agent {spec.name!r}, juge {name!r}"
        if declared and tenant is not None:
            label = f"Client {tenant.id!r}, agent {spec.name!r}, juge {name!r}"
        # Chaînes de secours comprises (#10) : un secours peut rendre le juge corrélé.
        evaluated = role.chain if role is not None else spec.main.chain
        pairs = [
            (mine, theirs)
            for mine in judge.chain
            for theirs in evaluated
            if correlated(config.model_spec(mine), config.model_spec(theirs))
        ]
        if pairs:
            same = " ; ".join(
                said(mine) if mine == theirs else f"juge {said(mine)}, sortie {said(theirs)}"
                for mine, theirs in pairs
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


def overlap_warnings(spec: AgentSpec) -> list[str]:
    """Un serveur MCP ou une source référencés deux fois, et qui peuvent exposer les mêmes outils.

    Deux références à un même serveur, sous deux préfixes, sont légitimes si
    chacune en expose une part : ``fs_lecture`` pour lire, ``fs_ecriture``
    pour écrire, avec une approbation. Elles le sont moins quand elles se
    recouvrent : le modèle voit deux fois les mêmes outils et les appelle
    tous les deux (test D3 du 18/10, ``fs__list_roots`` et ``f__list_roots``).
    Seuls deux ``include`` disjoints garantissent l'absence de recouvrement :
    avec ``exclude``, ou sans filtre, la liste dépend de ce que le serveur
    annonce, que la config ne connaît pas.
    """
    refs: dict[tuple[str, str], list[McpTools | SourceTools]] = {}
    for ref in (*spec.mcp_tools, *spec.source_tools):
        key = ("serveur MCP", ref.mcp) if isinstance(ref, McpTools) else ("source", ref.source)
        refs.setdefault(key, []).append(ref)
    warnings: list[str] = []
    for (kind, name), found in refs.items():
        overlapping = [
            (a, b)
            for i, a in enumerate(found)
            for b in found[i + 1 :]
            if a.include is None or b.include is None or set(a.include) & set(b.include)
        ]
        for a, b in overlapping:
            warnings.append(
                f"Agent {spec.name!r} : {kind} {name!r} référencé deux fois "
                f"(préfixes {a.alias or name}__ et {b.alias or name}__) avec des outils qui "
                "peuvent se recouvrir — le modèle les verra en double ; séparer par des "
                "'include' disjoints, ou garder une seule référence"
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


def _role_context_item(item: DeclaredRoleContext, state: Mapping[str, StateKey]) -> RoleContextItem:
    match item:
        case SessionStateContext(session_state=keys):
            described = tuple((key, state[key].description) for key in keys if key in state)
            return SessionState(keys=keys, described=described)
        case "session_state":
            return SessionState(described=tuple((k, key.description) for k, key in state.items()))
        case _:
            return _context_item(item)


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
        closing = Closing()
        for built in self._built.values():
            with closing:
                await built.aclose()
        self._built.clear()
        closing.raise_if_failed()


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
            if any(ref.owns(tool) for ref in spec.source_tools):
                continue
            prefixes = [f"{ref.prefix}__…" for ref in (*spec.mcp_tools, *spec.source_tools)]
            known = ", ".join([*names, *prefixes]) or "aucun"
            raise ConfigError(
                f"Agent {spec.name!r}, rôle {role.name!r} : tool_results désigne {tool!r}, "
                f"qui n'est pas un outil de l'agent (outils : {known})"
            )


def _check_state(spec: AgentSpec, tools: Sequence[Tool]) -> None:
    """Sources ``from`` du state : des outils de l'agent, avec des données pour ``path`` (D12).

    ``path`` se lit dans les données du résultat (``data``), après les
    politiques ``after_tool`` : un contrat avec schéma en donne à un outil ou à
    un rôle qui rend du texte. Un sous-agent n'en a jamais. Les outils MCP et
    ceux d'une source ne sont connus qu'au début du run : un nom qui porte leur
    préfixe est accepté ici.
    """
    python = {tool.spec.name: tool for tool in tools}
    roles = {role.name: role for role in spec.roles}
    subagents = {ref.tool_name for ref in spec.subagents}
    prefixed = (*spec.mcp_tools, *spec.source_tools)
    for key, declared in spec.state.items():
        label = f"Agent {spec.name!r}, state {key!r}"
        for source in declared.sources:
            name, wants_data = source.tool, source.path is not None
            if name in python:
                tool = python[name]
                if wants_data and not (_returns_data(tool) or _has_schema(tool.spec.output)):
                    raise ConfigError(
                        f"{label} : l'outil {name!r} ne rend pas de données (sa signature "
                        "annonce str, None ou Image) : 'path' impossible, donner une 'value' "
                        "ou un contrat de sortie avec schéma"
                    )
            elif name in roles:
                if wants_data and not _has_schema(roles[name].output):
                    raise ConfigError(
                        f"{label} : le rôle {name!r} rend du texte : 'path' impossible, "
                        "donner une 'value' ou un contrat de sortie avec schéma"
                    )
            elif name in subagents:
                if wants_data:
                    raise ConfigError(
                        f"{label} : le sous-agent {name!r} rend du texte : 'path' impossible, "
                        "donner une 'value'"
                    )
            elif not any(ref.owns(name) for ref in prefixed):
                names = [*python, *roles, *subagents]
                prefixes = [f"{ref.prefix}__…" for ref in prefixed]
                known = ", ".join([*names, *prefixes]) or "aucun"
                raise ConfigError(
                    f"{label} : from désigne {name!r}, qui n'est pas un outil de l'agent "
                    f"(outils : {known})"
                )


def _returns_data(tool: Tool) -> bool:
    """Faux si l'outil est une fonction dont la signature exclut toute donnée."""
    inner = tool
    while isinstance(inner, ConfiguredTool):
        inner = inner.tool
    if isinstance(inner, FunctionTool | IdempotentTool):
        return inner.returns_data
    return True


def _has_schema(contract: OutputContract | None) -> bool:
    """Vrai pour un contrat qui valide un objet JSON : il le range dans ``data``.

    Un ``schema_file`` compte une fois lu au chargement, qui le range dans ``schema`` :
    c'est ce champ que le guard applique.
    """
    return contract is not None and contract.json_schema is not None


def _absent(secrets: Mapping[str, str], variable: str | None) -> bool:
    return variable is not None and not secrets.get(variable, "").strip()


def _unprovided(tenant_id: TenantId, names: Iterable[str | None]) -> str:
    """Le refus d'un secret que le gestionnaire de l'application ne rend pas (M3)."""
    named = [name for name in dict.fromkeys(names) if name is not None]
    which = (
        f"le secret {named[0]} est absent ou vide"
        if len(named) == 1
        else f"les secrets {', '.join(named)} sont absents ou vides"
    )
    return f"{which} dans le gestionnaire de secrets (client {tenant_id!r})"


def _missing_secret(
    tenant_id: TenantId,
    redirects: Mapping[str, str],
    secrets: Mapping[str, str],
    variables: Iterable[str | None],
) -> str | None:
    """Le refus d'un secret manquant, redit avec la variable **du client** (L2).

    Un client redirige ``ANTHROPIC_API_KEY`` vers ``DUPONT_ANTHROPIC_KEY`` ;
    l'adaptateur, qui ne connaît que la config, disait alors « la variable
    ANTHROPIC_API_KEY est absente ou vide » alors qu'elle est posée — c'est
    la variable du client qui manque (test L2 du 22/10). Rend ``None`` quand
    aucune variable redirigée ne manque : le message d'origine dit juste.
    """
    missing = [
        (name, redirects[name])
        for name in dict.fromkeys(v for v in variables if v is not None)
        if name in redirects and not secrets.get(name, "").strip()
    ]
    if not missing:
        return None
    named = ", ".join(f"{target} (redirection de {name})" for name, target in missing)
    verb = "est absente ou vide" if len(missing) == 1 else "sont absentes ou vides"
    plural = "la variable" if len(missing) == 1 else "les variables"
    return f"Client {tenant_id!r} : {plural} {named} {verb}"


def _mcp_sources(
    config: LoomConfig,
    spec: AgentSpec,
    environ: Mapping[str, str] | None,
    pool: McpPool | None,
    tenant_id: TenantId = DEFAULT_TENANT,
    redirects: Mapping[str, str] | None = None,
    vaulted: bool = False,
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
            named = [*server.env_from.values(), *server.headers_env.values()]
            said = _missing_secret(tenant_id, redirects or {}, env, named)
            if said is None and vaulted:
                absent = [name for name in named if _absent(env, name)]
                said = _unprovided(tenant_id, absent) if absent else None
            if said is not None:
                raise ConfigError(
                    f"Agent {spec.name!r} : {said} — serveur MCP {server.name!r}"
                ) from exc
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


def _packaged_sources(
    config: LoomConfig, spec: AgentSpec, environ: Mapping[str, str] | None
) -> list[PackagedSource]:
    """Sources d'outils des paquets que l'agent référence (J6.4b).

    Le paquet de chaque source est importé ici, et sa fabrique appelée avec
    ce que la config déclare : des paramètres refusés refusent le montage.
    Chaque source appartient à l'agent monté, qui la ferme avec lui. La table
    des secrets part en lecture seule : une fabrique tierce ne modifie ni
    l'environnement du process ni la table du client.
    """
    secrets = MappingProxyType(os.environ if environ is None else environ)
    base_dir = config.base_dir if config.base_dir is not None else Path.cwd()
    sources: list[PackagedSource] = []
    for ref in spec.source_tools:
        declared = config.tool_source(ref.source)
        make = source_factory(declared.entry_point)
        told = f"Agent {spec.name!r}, source {declared.name!r} ({described(declared.entry_point)})"
        try:
            made: object = make(
                name=declared.name, params=declared.params, secrets=secrets, base_dir=base_dir
            )
        except Exception as exc:
            raise ConfigError(f"{told} : refusée par sa fabrique — {exc}") from exc
        if not callable(getattr(made, "open", None)):
            raise ConfigError(
                f"{told} : la fabrique n'a pas rendu une source d'outils ({type(made).__name__}, "
                "sans méthode open)"
            )
        sources.append(
            PackagedSource(
                made,
                name=declared.name,
                prefix=ref.prefix,
                include=ref.include,
                exclude=ref.exclude,
                required=ref.required,
                declared=declared.tools,
                chosen=ref.tools,
            )
        )
    return sources


def _missing_mcp(reason: str) -> ConfigError:
    return ConfigError(
        f"Client MCP indisponible alors que {reason} : le SDK 'mcp' n'est pas "
        "installé (installer l'extra : loom-ia[mcp])"
    )


def _tool(
    declared: PythonTool,
    registry: Registry,
    base_dir: Path | None,
    agent: str,
    *,
    provided: BuiltinContext | None = None,
) -> Tool:
    try:
        if is_builtin(declared.python):
            found: object = _builtin_tool(declared, provided or BuiltinContext(base_dir=base_dir))
        else:
            found = resolve(declared.python, registry, base_dir=base_dir)
        tool = found if isinstance(found, Tool) else _from_function(declared, found)
    except ConfigError as exc:
        # La référence ne dit pas d'où elle vient : l'agent et l'outil, si
        # (test M5 du 23/10, « Référence 'prix_ht' introuvable » sans plus).
        raise ConfigError(f"Agent {agent!r}, outil {declared.python!r} : {exc.reason}") from exc
    return configure(
        tool,
        timeout=declared.timeout,
        side_effects=declared.side_effects,
        approval=declared.approval,
        idempotent=declared.idempotent,
        offload_over=declared.offload_over,
        output=declared.output,
    )


def _builtin_tool(declared: PythonTool, context: BuiltinContext) -> Tool:
    """Outil fourni par loom-ia (préfixe ``loom.``) : son module n'est importé qu'ici.

    Un outil à paramètres reçoit ceux de sa référence, validés par son modèle,
    et le contexte du montage : le dossier de la config et les chemins de loom
    qu'il ne doit pas exposer.
    """
    reference = declared.python
    try:
        model = builtin_params(reference)
        params = None
        if model is not None:
            try:
                params = model.model_validate(builtin_settings(declared, model))
            except ValidationError as exc:
                # Le nom de l'agent et de l'outil est ajouté par ``_tool``.
                said = from_validation(exc, root=None).reason.replace("\n", " ; ")
                raise ConfigError(said) from None
        return builtin_tool(reference, params, context=context)
    except KeyError:
        provided = ", ".join(BUILTIN_TOOLS) or "aucun"
        raise ConfigError(f"Outil fourni {reference!r} inconnu (fournis : {provided})") from None
    except ImportError as exc:
        missing = exc.name or str(exc)
        extra = builtin_entry(reference).extra
        hint = f" (installer l'extra : loom-ia[{extra}])" if extra else ""
        raise ConfigError(
            f"Outil fourni {reference!r} : dépendance {missing!r} absente{hint}"
        ) from exc
    except ValueError as exc:
        # La fabrique refuse ses paramètres : un dossier absent, un chemin protégé…
        raise ConfigError(str(exc)) from exc


def _builtin_closables(
    declared: Sequence[PythonTool], tools: Sequence[Tool]
) -> tuple[_Closable, ...]:
    """Les outils fournis qui ont un ``aclose`` : ceux qu'un agent ferme avec lui.

    Seulement les outils fournis : un outil à soi qui aurait un ``aclose`` n'était
    pas fermé par loom jusqu'ici, et ne l'est toujours pas.
    """
    found: list[_Closable] = []
    for reference, tool in zip(declared, tools, strict=True):
        if not is_builtin(reference.python):
            continue
        inner = tool.tool if isinstance(tool, ConfiguredTool) else tool
        closer = getattr(inner, "aclose", None)
        if callable(closer):
            found.append(cast(_Closable, inner))
    return tuple(found)


def _protected_dsns(config: LoomConfig) -> tuple[str, ...]:
    """Les variables des DSN du stockage de loom, à la racine et pour chaque client."""
    names: list[str] = []
    storages = [config.storage, *(t.storage for t in config.tenants if t.storage is not None)]
    for storage in storages:
        for env in (storage.events.dsn_env, storage.idempotency.dsn_env, storage.bus.dsn_env):
            if env is not None and env not in names:
                names.append(env)
    return tuple(names)


def _protected_paths(config: LoomConfig) -> tuple[Protected, ...]:
    """Ce qu'un outil fourni ne doit jamais exposer au modèle.

    La config et ce qui la compose (agents, prompts), le code que loom charge
    ou lance depuis son dossier (modules, serveurs MCP), le fichier
    d'environnement, et le stockage de la racine et de **tous** les clients :
    un modèle qui y accéderait lirait des secrets ou les journaux d'un autre
    client, ou réécrirait le code qui le fait tourner.
    """
    found: list[Protected] = []
    base = config.base_dir
    names = _code_names(config)
    if base is not None:
        # Le dossier de la config : on peut en exposer un sous-dossier, pas lui.
        found.append(Protected(base, "le dossier de la config"))
        found.append(Protected(base / ENV_FILE_DEFAULT, "le fichier d'environnement"))
        for top in sorted(names):
            found.append(Protected(base / f"{top}.py", f"le module Python {top!r}"))
            found.append(Protected(base / top, f"le module Python {top!r}", subtree=True))
        found += _code_beside(base)
    found += _code_found(names)
    found += _mcp_code(config, base)
    if config.env_file is not None:
        found.append(Protected(config.env_file, "le fichier d'environnement"))
    found.append(Protected(config.agents_dir, "le dossier des agents", subtree=True))
    found.append(Protected(config.prompts_dir, "le dossier des prompts", subtree=True))
    storages = [config.storage]
    storages += [spec.storage for spec in config.tenants if spec.storage is not None]
    for storage in storages:
        events = storage.events
        if events.path is not None:
            found.append(Protected(events.path, "le journal", subtree=events.backend == "jsonl"))
        if storage.artifacts_path is not None:
            found.append(
                Protected(storage.artifacts_path, "le dossier des artefacts", subtree=True)
            )
        if storage.idempotency.path is not None:
            found.append(Protected(storage.idempotency.path, "le magasin d'idempotence"))
    return tuple(found)


# Ce qu'un serveur MCP exécute : un script, pas une donnée qu'il sert.
_SCRIPT_SUFFIXES: Final = frozenset(
    {".py", ".js", ".mjs", ".cjs", ".ts", ".mts", ".sh", ".bash", ".rb", ".pl", ".php"}
)


def _code_names(config: LoomConfig) -> set[str]:
    """Premiers noms des modules que charge la config : ``imports``, références ``module:attr``."""
    names = {name.split(".")[0] for name in config.imports}
    for agent in config.agents:
        references = [tool.python for tool in agent.python_tools]
        references += [policy.hook for policy in agent.policies]
        references += [judge.when.condition or "" for _, _, judge in agent.judges]
        names |= {ref.partition(":")[0].split(".")[0] for ref in references if ":" in ref}
    return {name for name in names if name}


def _code_found(names: set[str]) -> list[Protected]:
    """Où Python trouve ces modules : un paquet installé ailleurs (``src/``) compte aussi.

    ``find_spec`` d'un nom de premier niveau cherche sans rien exécuter.
    """
    found: list[Protected] = []
    for name in sorted(names):
        try:
            spec = importlib.util.find_spec(name)
        except ImportError, ValueError:
            continue
        if spec is None:
            continue
        what = f"le module Python {name!r}"
        if spec.origin is not None and spec.has_location:
            found.append(Protected(Path(spec.origin), what))
        for location in spec.submodule_search_locations or ():
            found.append(Protected(Path(location), what, subtree=True))
    return found


def _code_beside(base: Path) -> list[Protected]:
    """Le code posé à côté de la config, même sans référence : module ou dossier de modules."""
    found: list[Protected] = []
    try:
        entries = sorted(base.iterdir())
    except OSError:
        return found
    for entry in entries:
        if entry.name.startswith("."):
            continue
        if entry.suffix == ".py" and entry.is_file():
            found.append(Protected(entry, f"le module Python {entry.stem!r}"))
        elif entry.is_dir() and any(entry.glob("*.py")):
            found.append(Protected(entry, f"du code Python ({entry.name}/)", subtree=True))
    return found


def _mcp_code(config: LoomConfig, base: Path | None) -> list[Protected]:
    """Le code que lance un serveur MCP stdio : ses scripts, et son dossier s'il est à côté.

    Seuls comptent les **scripts** que nomment sa commande et ses arguments, et
    leur dossier : un argument qui est un dossier (la racine d'un serveur
    filesystem, un dépôt git) ou un fichier de données (une base SQLite) est ce
    que le serveur sert, pas son code. Son dossier de lancement compte s'il est
    un sous-dossier de celui de la config.
    """
    found: list[Protected] = []
    for server in config.mcp_servers:
        if server.transport != "stdio" or server.cwd is None:
            continue
        cwd = server.cwd.resolve()
        what = f"le code du serveur MCP {server.name!r}"
        if base is not None and cwd != base.resolve() and cwd.is_relative_to(base.resolve()):
            found.append(Protected(cwd, what, subtree=True))
        for token in (server.command or "", *server.args):
            if not token or token.startswith("-"):
                continue
            path = (cwd / token).resolve()
            if path.suffix.lower() not in _SCRIPT_SUFFIXES or not path.is_file():
                continue
            found.append(Protected(path, what))
            if path.parent != cwd and (base is None or path.parent != base.resolve()):
                found.append(Protected(path.parent, what, subtree=True))
    return found


def _from_function(declared: PythonTool, found: object) -> Tool:
    if not callable(found):
        raise ConfigError(
            f"Référence {declared.python!r} : ni un outil ni une fonction ({type(found).__name__})"
        )
    try:
        return FunctionTool(found)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"Référence {declared.python!r} : {exc}") from exc
