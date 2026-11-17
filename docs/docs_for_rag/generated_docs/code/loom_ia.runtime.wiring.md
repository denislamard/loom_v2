# loom_ia.runtime.wiring

Module `loom_ia.runtime.wiring` de loom_v2 (fichier `src/loom_ia/runtime/wiring.py`).

De la config aux objets qui tournent (fonctions M1 et A9).

`build_agent` assemble les clients de modèle, l'exécuteur d'outils (outils
Python et rôles délégués), le prompt système et le `RunContext`. Les trois
points d'accès partent tous d'ici.

Un client de modèle est ouvert par modèle utilisé : `main` et un rôle sur le
même modèle le partagent. Tout est vérifié avant d'ouvrir le premier client.

Serveurs MCP (point 19) : chaque référence d'un agent devient une source d'outils,
ouverte au début de chaque run. Les connexions de portée `shared` vivent
dans un `McpPool`, celui de l'instance `Loom` ou, à défaut, celui de
l'agent. Le SDK `mcp` n'est importé que si la config déclare des serveurs.

Stockage d'artefacts (fonction G2) : un seul par instance, partagé par ses agents. Il
suit le journal par défaut (dossier `.artifacts` d'un journal JSONL,
mémoire sinon).

Clients (fonction L1, point 34) : `tenant` dit pour qui l'agent est monté. Il apporte la
table des secrets à lire, les outils retirés, les approbations imposées et
les valeurs des variables citées par les prompts ; la correspondance des
modèles, elle, est déjà dans la config qu'il porte. Sans lui, l'agent est
monté pour `default`, qui ne surcharge rien (point 33).

Politiques (point 2) : chaque référence est résolue (politique fournie `loom.…`,
nom enregistré ou `module:attr`), puis contrôlée avant le premier run : ses
points parmi ceux qu'elle déclare, ses décisions permises à chacun de ses
points (`Pause` : `before_tool` seulement, J4.3), un nom unique dans l'agent.

Contrats de sortie (point 20) : si l'agent en déclare un (réponse finale, rôle,
outil Python ou MCP), le guard `loom.contract` est placé en tête de ses
politiques. La diffusion de la réponse finale (`stream_output`) vaut par
défaut `after_guards` quand elle est contrôlée, `live` sinon.

Secours (fonction B4, point 10) : `main`, chaque rôle et chaque juge reçoivent leur
chaîne (modèle, puis `fallbacks`), avec un client par modèle. Les
disjoncteurs des modèles et des serveurs MCP sont communs aux agents d'une
instance `Loom` (`breakers`) ; sans elle, à l'agent et à ses sous-agents.

Sous-agents (fonction C5) : l'agent appelé n'est monté qu'au premier appel, par
`agents` (`Loom.context` dans une instance). Deux agents peuvent ainsi
s'appeler l'un l'autre sans que le montage boucle ; la profondeur borne les
appels. Sans `agents`, l'agent monte lui-même ses sous-agents à la demande,
et les referme avec lui.

## Constantes de `loom_ia.runtime.wiring`

- `_SCRIPT_SUFFIXES = frozenset({'.py', '.js', '.mjs', '.cjs', '.ts', '.mts', '.sh', '.bash', '.rb', '.pl', '.php'})` : Ce qu'un serveur MCP exécute : un script, pas une donnée qu'il sert.

## Classe `loom_ia.runtime.wiring._Closable`

```python
class _Closable(Protocol)
```

Ressource qu'un agent ferme avec lui (`aclose`).

### Méthode `loom_ia.runtime.wiring._Closable.aclose`

```python
async def aclose(self) -> None
```

Ferme la ressource.

## Classe `loom_ia.runtime.wiring.Agent`

```python
@dataclass(frozen=True, slots=True)
class Agent
```

Un agent prêt à tourner, et de quoi refermer ce qu'il a ouvert.

**Attributs :**

- `spec` : Définition de l'agent.
- `context` : Son contexte d'exécution, prêt pour `begin_run` et `drive`.
- `clients` : Clients de modèle que l'agent a créés (`main`, rôles, juges), un par modèle ; un client fourni par l'appelant (`provided`) n'en est pas.
- `owned` : Ressources ouvertes pour cet agent seul (connexions MCP partagées sans pool fourni, sources de paquets, outils fournis qui ferment des connexions, sous-agents).

### Méthode `loom_ia.runtime.wiring.Agent.aclose`

```python
async def aclose(self) -> None
```

Ferme les clients de modèle et ce que l'agent possède ; le journal reste à l'appelant.

Chaque fermeture est tentée, même si une précédente a levé ; l'échec
est levé une fois tout fermé (`Closing`).

## Fonction `loom_ia.runtime.wiring.create_mcp_pool`

```python
def create_mcp_pool(config: LoomConfig, environ: Mapping[str, str] | None=None) -> McpPool | None
```

Pool des connexions MCP partagées, ou None si la config ne déclare aucun serveur.

**Arguments :**

- `config` : La configuration chargée.
- `environ` : Environnement où lire les secrets des serveurs ; `os.environ` par défaut.

**Lève :**

- `ConfigError` : La config déclare des serveurs et le SDK `mcp` n'est pas installé (extra `loom-ia[mcp]`).

## Fonction `loom_ia.runtime.wiring._dsn`

```python
def _dsn(declared: EventsStorage | IdempotencyStorage, what: str) -> str
```

DSN lu dans la variable que la config nomme : elle ne porte pas le secret (conception §16.3).

## Fonction `loom_ia.runtime.wiring.create_keyring`

```python
def create_keyring(config: LoomConfig, secrets: SecretProvider | None=None) -> Keyring | None
```

Trousseau des clients déclaré dans `storage.encryption` ; `None` sans sceau.

Les clés viennent des secrets, jamais de la config : celle-ci ne nomme
que le secret qui les porte, et chaque client redirige ce nom vers sa
variable (jalon J5.1a).

**Arguments :**

- `config` : La configuration chargée.
- `secrets` : Gestionnaire de secrets de l'application ; à défaut, l'environnement, redirigé par client.

**Lève :**

- `ConfigError` : Le paquet `cryptography` n'est pas installé (extra `loom-ia[crypto]`).

## Fonction `loom_ia.runtime.wiring.create_journal_codec`

```python
def create_journal_codec(config: LoomConfig, secrets: SecretProvider | None=None) -> JournalCodec
```

Codec du journal : scellant quand `storage.encryption` est déclaré.

**Arguments :**

- `config` : La configuration chargée.
- `secrets` : Gestionnaire de secrets de l'application.

**Renvoie :**

Le codec : en clair sans `storage.encryption`, scellant sinon.

## Fonction `loom_ia.runtime.wiring.create_event_store`

```python
def create_event_store(config: LoomConfig | StorageConfig, *, codec: JournalCodec | None=None) -> EventStore
```

Journal déclaré dans `storage.events`, d'une config ou d'un client.

`codec` dit comment une ligne est rangée : en clair, ou la charge
scellée. Sans lui, il est tiré de la config — un journal de client n'en
déclare pas (le sceau se déclare à la racine), si bien qu'un journal créé
pour un client reçoit celui de l'instance.

**Arguments :**

- `config` : La configuration, ou le bloc `storage` d'un client.
- `codec` : Rangement des lignes ; tiré de la config à défaut.

**Renvoie :**

Le journal : `postgres`, `jsonl` ou `sqlite` (avec `path`),
sinon en mémoire.

**Lève :**

- `ConfigError` : Variable du DSN absente, ou paquet non installé (`asyncpg` pour `postgres`, `aiosqlite` pour `sqlite`).

## Fonction `loom_ia.runtime.wiring.create_task_queue`

```python
def create_task_queue(config: LoomConfig, handlers: Mapping[JobKind, Handler]) -> TaskQueue
```

File déclarée dans `storage.queue`.

`asyncio` exécute les tâches ici même ; `rabbitmq` les publie, et
c'est `loom worker` qui les mène. Les traitements sont passés dans les
deux cas : c'est le même objet qui sert de file dans un process qui
publie et de consommateur dans un worker.

**Arguments :**

- `config` : La configuration chargée.
- `handlers` : Traitement de chaque genre de tâche (`run`, `compaction`…).

**Renvoie :**

`rabbitmq` (URL dans `url_env`) ou `asyncio` (dans ce process,
`max_running` tâches à la fois).

**Lève :**

- `ConfigError` : `aio-pika` non installé, ou variable de l'URL vide.

## Fonction `loom_ia.runtime.wiring.create_bus`

```python
def create_bus(config: LoomConfig) -> EventBus | None
```

Bus déclaré dans `storage.bus`, ou `None` s'il n'y a rien à traverser.

`memory` rend `None` : dans un seul process, le journal remet déjà ses
écritures à ses abonnés, et un bus de plus ne ferait que les recopier.

**Arguments :**

- `config` : La configuration chargée.

**Renvoie :**

Le bus `postgres` ou `redis` ; `None` pour `memory`.

**Lève :**

- `ConfigError` : Variable d'adresse absente, ou bibliothèque (`asyncpg`, `redis`) non installée.

## Fonction `loom_ia.runtime.wiring._variable`

```python
def _variable(declared: BusStorage, what: str, porte: str) -> str
```

Valeur de la variable d'environnement que la config nomme (conception §16.3).

## Fonction `loom_ia.runtime.wiring.create_run_exporter`

```python
def create_run_exporter(config: LoomConfig, store: EventStore, environ: Mapping[str, str] | None=None) -> tuple[RunExporter | None, list[str]]
```

Les collecteurs de `telemetry.exporters`, et ce qui empêche d'en monter (jalon J6.1a).

Un collecteur dont la variable d'adresse est vide n'est **pas monté**, et
c'est dit — un avertissement, une erreur en profil prod : le SDK enverrait
sinon à son adresse par défaut, ce qu'on n'a pas demandé. Sans collecteur
monté, pas d'abonné du tout : rien ne coûte rien.

**Arguments :**

- `config` : La configuration chargée.
- `store` : Le journal, dont l'exporteur lit les runs.
- `environ` : Environnement où lire les adresses des collecteurs.

**Renvoie :**

L'exporteur (`None` sans collecteur monté) et les avertissements.

**Lève :**

- `ConfigError` : OpenTelemetry non installé (extra `loom-ia[otel]`).

## Fonction `loom_ia.runtime.wiring.postgres_ddl`

```python
def postgres_ddl(config: LoomConfig | StorageConfig) -> str
```

Le SQL du stockage Postgres déclaré par la config (`loom storage sql`).

Une config qui n'en déclare aucun rend le SQL complet avec le rôle par
défaut : de quoi préparer une base avant de la déclarer.

**Arguments :**

- `config` : La configuration, ou le bloc `storage` d'un client.

**Renvoie :**

Le SQL des tables `postgres` déclarées (journal, idempotence),
rôles et droits compris ; sans aucune, le SQL complet.

## Fonction `loom_ia.runtime.wiring.create_artifact_store`

```python
def create_artifact_store(config: LoomConfig | StorageConfig, *, keyring: Keyring | None=None) -> ArtifactStore
```

Stockage d'artefacts déclaré dans `storage.artifacts`, ou celui qui suit le journal.

Avec un trousseau, les octets sont scellés comme les charges du journal :
un gros résultat déporté ou une pièce jointe porte du contenu, et le
laisser en clair ferait du sceau une demi-mesure.

**Arguments :**

- `config` : La configuration, ou le bloc `storage` d'un client.
- `keyring` : Trousseau ; avec lui, les octets sont scellés.

**Renvoie :**

Le stockage `local` (avec un dossier) ou en mémoire, scellé avec un
trousseau.

## Fonction `loom_ia.runtime.wiring.create_idempotency_store`

```python
def create_idempotency_store(config: LoomConfig) -> IdempotencyStore | None
```

Magasin d'idempotence déclaré dans `storage.idempotency`.

`journal` ne rend rien : il n'y a pas de magasin commun à monter, chaque
run écrit ses enregistrements dans son journal et l'exécuteur le lui donne
au moment de l'appel.

**Arguments :**

- `config` : La configuration chargée.

**Renvoie :**

Le magasin `memory`, `postgres`, `redis` ou `sqlite` ;
`None` pour `journal`.

**Lève :**

- `ConfigError` : Variable de l'URL vide, ou bibliothèque non installée (`asyncpg`, `redis`, `aiosqlite`).

## Fonction `loom_ia.runtime.wiring.load_registry`

```python
def load_registry(config: LoomConfig) -> Registry
```

Charge les modules de `imports` et enregistre leurs outils.

**Arguments :**

- `config` : La configuration chargée.

**Renvoie :**

Le registre des outils et politiques enregistrés par les modules
de `imports`.

## Fonction `loom_ia.runtime.wiring.apply_logging`

```python
def apply_logging(config: LoomConfig) -> logging.Handler
```

Installe les logs demandés par `telemetry.logging`.

**Arguments :**

- `config` : La configuration chargée.

**Renvoie :**

Le gestionnaire installé (niveau et format de `telemetry.logging`).

## Fonction `loom_ia.runtime.wiring.build_agent`

```python
def build_agent(config: LoomConfig, name: str, store: EventStore, *, registry: Registry | None=None, environ: Mapping[str, str] | None=None, on_chunk: ChunkCallback | None=None, mcp_pool: McpPool | None=None, artifacts: ArtifactStore | None=None, idempotency: IdempotencyStore | None=None, agents: AgentResolver | None=None, breakers: CircuitBreakers | None=None, tenant: Tenant | None=None, models: Callable[[ModelSpec], ModelClient] | None=None, provided: Mapping[str, ModelClient] | None=None, threads: ToolThreads | None=None) -> Agent
```

Assemble l'agent `name` de la config.

`models` remplace la fabrique des clients de modèle (jalon J6.2a) : le rejeu
monte ainsi le même agent, avec des clients qui répondent depuis le journal.
`provided` donne des clients tout faits, par identifiant de modèle
(fonction O2, jalon J6.3c) : ils servent à la place de ceux que la config déclare, et restent à
l'appelant, qui les ferme.

`mcp_pool` porte les connexions MCP partagées ; sans lui, l'agent ouvre
le sien et le ferme avec `aclose`. `artifacts` est le stockage des
fichiers ; sans lui, les pièces jointes sont refusées et les gros
résultats tronqués. `agents` donne le contexte d'un sous-agent par son
nom ; sans lui, l'agent monte ses sous-agents lui-même, au premier appel.
`breakers` : disjoncteurs partagés (ceux de l'instance) ; sans eux,
l'agent a les siens, communs à ses sous-agents. `idempotency` : magasin
partagé (point 49) ; sans lui, chaque appel reçoit celui de son journal.
`tenant` : le client pour qui l'agent est monté (fonction L1) ; ses secrets
remplacent alors `environ`. `threads` : le pool des outils synchrones de
l'instance (décisions B-2 et D-4 du délai et des threads) ; sans lui, l'agent a le sien, commun à ses sous-agents.

**Arguments :**

- `config` : La configuration chargée.
- `name` : Nom de l'agent.
- `store` : Le journal.
- `registry` : Registre des outils et politiques ; chargé depuis `imports` à défaut.
- `environ` : Environnement où lire les secrets, sans client.
- `on_chunk` : Diffusion en direct de la réponse.
- `mcp_pool` : Connexions MCP partagées de l'instance.
- `artifacts` : Stockage des fichiers.
- `idempotency` : Magasin d'idempotence partagé.
- `agents` : Contexte d'un sous-agent par son nom.
- `breakers` : Disjoncteurs partagés de l'instance.
- `tenant` : Client pour qui l'agent est monté.
- `models` : Fabrique des clients de modèle (rejeu).
- `provided` : Clients tout faits, par identifiant de modèle.
- `threads` : Pool des outils synchrones de l'instance.

**Renvoie :**

L'agent : sa définition, son `RunContext` et ce qu'il ferme.

**Lève :**

- `UnknownAgent` : Aucun agent de ce nom.
- `ConfigError` : Outil, politique, juge ou source introuvable ; nom en double ; secret manquant ; agent qui exige un journal durable ou un magasin d'idempotence partagé.

## Fonction `loom_ia.runtime.wiring.build_policies`

```python
def build_policies(spec: AgentSpec, registry: Registry, base_dir: Path | None=None, *, contracts: bool | None=None, judges: Sequence[JudgeGuard]=(), budget: BudgetGuard | None=None, fidelity: FidelityGuard | None=None) -> Policies
```

Politiques de l'agent, résolues et contrôlées, dans l'ordre déclaré.

Les politiques fournies passent en tête : le guard des contrats quand
l'agent en déclare (`contracts`, déduit de l'agent si absent), puis les
juges (`judges`) — la forme d'une sortie est contrôlée avant son fond —,
puis le budget (`budget`). `fidelity` n'est branché que sur l'agent
interne de compaction (point 23).

**Arguments :**

- `spec` : L'agent.
- `registry` : Registre des politiques enregistrées.
- `base_dir` : Dossier de la config, pour les références `module:attr`.
- `contracts` : Brancher le guard des contrats ; déduit de l'agent si `None`.
- `judges` : Les guards des juges.
- `budget` : La politique de budget.
- `fidelity` : Le contrôle de fidélité d'un résumé.

**Renvoie :**

Les politiques, prêtes pour le moteur.

**Lève :**

- `ConfigError` : Politique introuvable, point non déclaré par elle, décision non permise à un point, nom en double.

## Fonction `loom_ia.runtime.wiring.model_keys`

```python
def model_keys(spec: AgentSpec) -> tuple[ModelKey, ...]
```

Clés du state que le modèle tient à jour (`writers: [model]`), pour `loom.state`.

**Arguments :**

- `spec` : L'agent.

**Renvoie :**

Pour chaque clé tenue par le modèle : nom, description, schéma,
règle `extract`, `required` et `clears`.

## Fonction `loom_ia.runtime.wiring._model_tool`

```python
def _model_tool(spec: AgentSpec, tools: Sequence[Tool]) -> str | None
```

Nom sous lequel `loom.state` est monté : ses écritures sont celles du modèle (décision B4 du state v2).

## Fonction `loom_ia.runtime.wiring.state_rules`

```python
def state_rules(config: LoomConfig, spec: AgentSpec, *, model_tool: str | None=None) -> StateRules
```

Clés du state que déclare l'agent, telles que le moteur les applique, et leurs limites.

`model_tool` : le nom sous lequel `loom.state` est monté (décisions S1
et B4 du state v2).

**Arguments :**

- `config` : La configuration chargée. (limites `sessions.state`).
- `spec` : L'agent.
- `model_tool` : Nom de `loom.state` dans l'agent.

**Renvoie :**

Les règles du state ; vides si l'agent ne déclare pas de clés.

## Fonction `loom_ia.runtime.wiring.stream_output`

```python
def stream_output(spec: AgentSpec, policies: Policies) -> StreamOutput
```

Diffusion de la réponse finale : celle déclarée, sinon selon ses contrôles (point 11).

**Arguments :**

- `spec` : L'agent.
- `policies` : Ses politiques montées.

**Renvoie :**

`live` ou `after_guards`.

## Fonction `loom_ia.runtime.wiring._has_contracts`

```python
def _has_contracts(config: LoomConfig, spec: AgentSpec) -> bool
```

Vrai si un contrat s'applique à l'agent, y compris par un serveur MCP ou une source.

## Fonction `loom_ia.runtime.wiring._policy`

```python
def _policy(spec: AgentSpec, ref: PolicyRef, registry: Registry, base_dir: Path | None) -> Policy
```

Politique désignée par une référence : fournie par loom-ia, enregistrée ou importée.

## Fonction `loom_ia.runtime.wiring.system_prompt`

```python
def system_prompt(spec: AgentSpec, variables: Mapping[str, JsonValue] | None=None) -> str
```

Prompt système de l'orchestrateur, ses variables rendues.

**Arguments :**

- `spec` : L'agent.
- `variables` : Valeurs des variables du client.

## Fonction `loom_ia.runtime.wiring.prompt_text`

```python
def prompt_text(role: BaseRole, variables: Mapping[str, JsonValue] | None=None) -> str
```

Prompt système d'un rôle : texte en ligne ou fichier, ses variables rendues.

Un prompt appartient à la configuration : il est le même pour tout le
monde (conception §6). Ce qu'un client y change, ce sont les valeurs qu'il cite —
`{{ entreprise }}` —, et le chargement a déjà vérifié qu'aucune ne
manque à aucun client (fonction M5). Un prompt sans `{{ }}` traverse ce rendu
sans y laisser de trace.

**Arguments :**

- `role` : Le rôle (`main` ou délégué).
- `variables` : Valeurs des variables du client.

**Renvoie :**

Le prompt rendu.

## Fonction `loom_ia.runtime.wiring.role_definition`

```python
def role_definition(role: RoleSpec, variables: Mapping[str, JsonValue] | None=None, *, state: Mapping[str, StateKey] | None=None) -> RoleDefinition
```

Rôle délégué tel que le moteur l'exécute ; `state` : les clés que déclare l'agent.

**Arguments :**

- `role` : Le rôle déclaré.
- `variables` : Valeurs des variables du client.
- `state` : Les clés du state de l'agent, pour décrire celles que le rôle reçoit.

## Fonction `loom_ia.runtime.wiring.judge_definition`

```python
def judge_definition(spec: AgentSpec, judged: tuple[str, RoleSpec | None, JudgeSpec], registry: Registry, base_dir: Path | None=None, profile: str | None=None) -> JudgeDefinition
```

Juge tel que le moteur l'exécute : condition résolue, contexte converti.

**Arguments :**

- `spec` : L'agent.
- `judged` : Nom du juge, rôle jugé (`None` pour la réponse finale) et définition.
- `registry` : Registre, pour résoudre `when.condition`.
- `base_dir` : Dossier de la config.
- `profile` : Profil courant, pour `when.profiles`.

**Lève :**

- `ConfigError` : Condition introuvable ou non appelable.

## Fonction `loom_ia.runtime.wiring._check_durable_journal`

```python
def _check_durable_journal(config: LoomConfig, spec: AgentSpec, tools: Sequence[AnyTool], policies: Policies, imposed: Mapping[str, Approval] | None=None, tenant: Tenant | None=None) -> None
```

Un agent qui peut se mettre en pause exige un journal durable (point 28).

En pause, le run n'existe plus que dans le journal : le process peut
s'arrêter, l'approbateur prendre son temps, un autre worker reprendre. Un
journal en mémoire perdrait le run à la première fermeture, et
l'approbation n'aurait rien à reprendre. Une erreur, donc, et non un
avertissement — sauf en profil `dev` (jalon J5.5a), où perdre un run en pause à
la fermeture est le prix d'un essai.

Le journal jugé est celui du client pour qui l'agent est monté : son
`storage` s'il en déclare un, sinon celui de la racine. Les outils des
serveurs MCP et des paquets ne sont connus qu'à la connexion ; la garde juge
donc ce que la **configuration** en dit (`tools` du serveur ou de la
source, de la référence de l'agent, approbations imposées par le client).
Elle ne voit pas l'`approval: always` qu'un outil de paquet porterait en
lui-même.

## Fonction `loom_ia.runtime.wiring._assesses_approval`

```python
def _assesses_approval(tool: object) -> bool
```

Vrai si l'outil décide de l'approbation appel par appel, sans que la config l'ait fixée.

## Fonction `loom_ia.runtime.wiring._sources_asking_approval`

```python
def _sources_asking_approval(config: LoomConfig, spec: AgentSpec, imposed: Mapping[str, Approval]) -> list[str]
```

Outils MCP ou de paquet que la configuration met en `approval: always`.

Sans connexion : la liste des outils d'un serveur ou d'une source n'est
connue qu'à l'ouverture. On lit ce que la config en déclare — `tools` du
serveur ou de la source, puis ceux de la référence de l'agent, qui l'emportent
—, puis l'approbation que le client impose, qui l'emporte sur tout. Une
approbation imposée à un nom que la config ne cite pas compte aussi, dès
qu'il porte le préfixe d'une source de l'agent et passe son `include` ou
son `exclude` : le client l'a voulue, et on ne sait pas si l'outil existe.

## Fonction `loom_ia.runtime.wiring._selects`

```python
def _selects(ref: McpTools | SourceTools, short: str) -> bool
```

Vrai si la référence expose l'outil `short` (`include` ou `exclude`).

## Fonction `loom_ia.runtime.wiring._check_shared_idempotency`

```python
def _check_shared_idempotency(config: LoomConfig, spec: AgentSpec, tools: Sequence[AnyTool]) -> None
```

Une clé métier exige un magasin partagé et durable (point 49).

Une clé métier dit « cette relance est déjà partie » à qui la demande,
d'un run à l'autre et d'une conversation à l'autre. Le magasin `journal`
ne voit que son run et `memory` que son process : la promesse serait
tenue là où elle ne sert à rien, et rompue partout ailleurs. Une erreur,
donc — un doublon silencieux se remarque trop tard.

## Fonction `loom_ia.runtime.wiring.announce`

```python
def announce(config: LoomConfig, warnings: Sequence[str], *, refuse: bool | None=None) -> None
```

Dit ce qui ne va pas — un avertissement, ou une erreur en profil `prod` (fonction M4).

C'est ici que les profils prennent leur sens : la même config charge sur
une machine et refuse de partir en production. `refuse` tranche à la
place du profil quand la règle a son propre arbitrage.

**Arguments :**

- `config` : La configuration chargée.
- `warnings` : Les messages.
- `refuse` : Vrai pour refuser, faux pour avertir ; le profil `prod` décide si `None`.

**Lève :**

- `ConfigError` : Il y a des messages et l'on refuse.

## Fonction `loom_ia.runtime.wiring.exporter_warnings`

```python
def exporter_warnings(config: LoomConfig, environ: Mapping[str, str] | None=None) -> list[str]
```

Les collecteurs que leur variable d'adresse, vide, laisse non montés (jalon J6.1a).

Le même texte que `create_run_exporter` dit au montage : `loom validate`
l'affiche à sa place.

**Arguments :**

- `config` : La configuration chargée.
- `environ` : Environnement où lire les adresses.

## Fonction `loom_ia.runtime.wiring.storage_warnings`

```python
def storage_warnings(config: LoomConfig) -> list[str]
```

Ce qui ne va pas ensemble dans un service à plusieurs process (jalon J5.3).

Un tel service partage son journal et ses nouvelles, mais **pas forcément
ses fichiers** : les artefacts `local` exigent un dossier commun aux
process, et `memory` ne sort pas de celui qui écrit. Un run repris
ailleurs (jalon J5.3b) ne retrouverait alors ni ses pièces jointes ni ses
résultats déportés.

Un avertissement, pas une erreur : un volume partagé est un montage
parfaitement légitime, que la config ne peut pas distinguer d'un dossier
propre à chaque machine, et un agent qui ne produit aucun fichier n'est de
toute façon pas concerné. Le stockage partagé (GCS) est reporté avec le jalon J5.3d.

**Arguments :**

- `config` : La configuration chargée.

## Fonction `loom_ia.runtime.wiring.model_warnings`

```python
def model_warnings(config: LoomConfig) -> list[str]
```

Ce qu'un modèle gagnerait à déclarer, et que loom ne peut pas deviner.

Un modèle `sdk: anthropic` sur l'API officielle (sans `base_url`) qui
voit les images peut les recevoir **dans** le résultat d'un outil
(`tool_result_media: true`). Sans cette capacité, une image rendue par un
outil part dans un message utilisateur placé après les résultats, et le
modèle la prend pour une image envoyée par l'utilisateur (essai de la fonction D8, 18/10).
Ce n'est pas un défaut que loom choisit seul : `sdk: anthropic` désigne
un protocole, que des fournisseurs compatibles (MiniMax…) reprennent sans
forcément accepter une image dans un résultat d'outil. Un avertissement
donc, dans tous les profils, et seulement pour l'adresse officielle.

**Arguments :**

- `config` : La configuration chargée.

## Fonction `loom_ia.runtime.wiring.key_warnings`

```python
def key_warnings(config: LoomConfig) -> list[str]
```

Modèles de l'API officielle déclarés sans `api_key_env` : aucune clé n'est envoyée.

Sans `api_key_env`, loom envoie une clé de remplacement, prévue pour un
serveur local (vLLM, Ollama) ou compatible qui n'en demande pas. Vers
l'API officielle (`sdk: anthropic` ou `openai` sans `base_url`),
chaque appel est donc refusé — au 20/10, en « 401 invalid x-api-key », sans
que rien n'ait signalé l'oubli. Un avertissement, une erreur en profil
`prod` : un service qui ne peut appeler aucun de ses modèles ne part pas.

**Arguments :**

- `config` : La configuration chargée.

## Fonction `loom_ia.runtime.wiring.encryption_warnings`

```python
def encryption_warnings(config: LoomConfig, keyring: Keyring | None) -> list[str]
```

Ce que le trousseau dit des clients déclarés (jalon J5.5b).

Deux choses valent d'être dites au chargement. Un client **sans clé** :
son journal reste illisible et ses runs seront refusés à leur premier
événement. C'est l'effet voulu d'un effacement de clé, et la config ne
peut pas le distinguer d'une variable oubliée — donc un avertissement, pas
un refus, même en profil prod : refuser de démarrer parce qu'un client a
été effacé arrêterait le service de tous les autres.

Deux clients qui **partagent une clé** : c'est un montage possible, mais
effacer cette clé efface le contenu des deux, et c'est le genre de chose
qu'on découvre au mauvais moment.

**Arguments :**

- `config` : La configuration chargée.
- `keyring` : Le trousseau, s'il y en a un.

## Fonction `loom_ia.runtime.wiring.budget_warnings`

```python
def budget_warnings(config: LoomConfig, spec: AgentSpec) -> list[str]
```

Budget en dollars sur un agent dont un modèle n'a pas de tarif (backlog #010).

Ses appels comptent 0 $ : le plafond ne les voit pas. En profil prod,
`announce` en fait une erreur (jalon J5.5a) ; un budget en tokens reste efficace.

**Arguments :**

- `config` : La configuration chargée.
- `spec` : L'agent.

## Fonction `loom_ia.runtime.wiring.judge_warnings`

```python
def judge_warnings(config: LoomConfig, spec: AgentSpec, *, tenant: Tenant | None=None) -> list[str]
```

Avertissements sur les juges de l'agent (fonction E6, point 21) ; des erreurs en profil prod (jalon J5.5a).

`tenant` : le client pour qui l'agent est monté, sa correspondance des
modèles déjà appliquée à `config`. Un client déclaré est nommé, et un
modèle qu'il remplace est dit avec sa cible : à l'essai de la fonction E6 du 19/10, « (OPUS
et HAIKU) » pour un client chez qui OPUS devient HAIKU ne disait ni le
client, ni que les deux sont le même modèle.

**Arguments :**

- `config` : La configuration chargée.
- `spec` : L'agent.
- `tenant` : Le client.

## Fonction `loom_ia.runtime.wiring._source_refs`

```python
def _source_refs(config: LoomConfig, spec: AgentSpec) -> list[tuple[McpTools | SourceTools, Mapping[str, ToolOverrides]]]
```

Chaque référence MCP ou de paquet de l'agent, avec les surcharges de son serveur.

## Fonction `loom_ia.runtime.wiring.declared_skills`

```python
def declared_skills(config: LoomConfig, spec: AgentSpec) -> list[tuple[str, str]]
```

Le skill de chaque outil MCP ou de paquet que l'agent expose, sous le nom que voit le modèle.

Sans connexion : les outils d'un serveur ne sont connus qu'à son ouverture.
On lit ce que la config déclare, comme `_sources_asking_approval` : la
surcharge de l'agent l'emporte sur celle du serveur ou de la source, et un
outil qu'`include` ou `exclude` écarte ne compte pas.

**Arguments :**

- `config` : La configuration chargée.
- `spec` : L'agent.

**Renvoie :**

Nom de l'outil et texte du skill.

## Fonction `loom_ia.runtime.wiring._check_skills_read`

```python
def _check_skills_read(config: LoomConfig, spec: AgentSpec) -> None
```

Un `skill_file` que le chargement n'a pas lu arrête le montage.

Une config construite en Python ne passe pas par `load_config` : sans ce
contrôle, un outil MCP ne le dirait qu'à l'ouverture de son serveur, en plein run.

## Fonction `loom_ia.runtime.wiring.skill_warnings`

```python
def skill_warnings(config: LoomConfig, spec: AgentSpec) -> list[str]
```

Les skills plus longs que `SKILL_WARN_CHARS` : ils partent à chaque appel de modèle.

**Arguments :**

- `config` : La configuration chargée.
- `spec` : L'agent.

## Fonction `loom_ia.runtime.wiring.overlap_warnings`

```python
def overlap_warnings(spec: AgentSpec) -> list[str]
```

Un serveur MCP ou une source référencés deux fois, et qui peuvent exposer les mêmes outils.

Deux références à un même serveur, sous deux préfixes, sont légitimes si
chacune en expose une part : `fs_lecture` pour lire, `fs_ecriture`
pour écrire, avec une approbation. Elles le sont moins quand elles se
recouvrent : le modèle voit deux fois les mêmes outils et les appelle
tous les deux (essai de la fonction D3, 18/10, `fs__list_roots` et `f__list_roots`).
Seuls deux `include` disjoints garantissent l'absence de recouvrement :
avec `exclude`, ou sans filtre, la liste dépend de ce que le serveur
annonce, que la config ne connaît pas.

**Arguments :**

- `spec` : L'agent.

## Fonction `loom_ia.runtime.wiring.fallback_warnings`

```python
def fallback_warnings(config: LoomConfig, spec: AgentSpec) -> list[str]
```

Secours à la fenêtre de contexte déclarée plus petite que celle du modèle (point 10).

Une requête qui tient dans la fenêtre du modèle peut déborder de celle du
secours : la bascule échouera alors en `context_overflow`.

**Arguments :**

- `config` : La configuration chargée.
- `spec` : L'agent.

## Fonction `loom_ia.runtime.wiring._subagent_definition`

```python
def _subagent_definition(config: LoomConfig, spec: AgentSpec, ref: SubAgentRef) -> SubAgentDefinition
```

Sous-agent tel que le moteur l'appelle : nom et description de l'agent par défaut.

## Classe `loom_ia.runtime.wiring._SubAgents`

```python
class _SubAgents
```

Sous-agents d'un agent monté hors d'une instance `Loom` : montés au premier appel.

### Méthode `loom_ia.runtime.wiring._SubAgents.__init__`

```python
def __init__(self, config: LoomConfig, store: EventStore, *, registry: Registry, environ: Mapping[str, str] | None, mcp_pool: McpPool | None, artifacts: ArtifactStore | None, idempotency: IdempotencyStore | None, breakers: CircuitBreakers, tenant: Tenant | None=None, threads: ToolThreads | None=None) -> None
```

Garde de quoi monter les sous-agents ; les arguments sont ceux de `build_agent`.

### Méthode `loom_ia.runtime.wiring._SubAgents.__call__`

```python
def __call__(self, name: str) -> RunContext
```

Contexte du sous-agent `name`, monté au premier appel puis gardé.

### Méthode `loom_ia.runtime.wiring._SubAgents.aclose`

```python
async def aclose(self) -> None
```

Ferme les sous-agents montés.

## Fonction `loom_ia.runtime.wiring._check_names`

```python
def _check_names(spec: AgentSpec, python_tools: list[str]) -> None
```

Noms d'outils uniques dans l'agent, et `tool_results` qui désignent ses outils.

Les outils MCP ne sont connus qu'au début du run : un nom qui porte le
préfixe d'un serveur référencé est accepté.

## Fonction `loom_ia.runtime.wiring._check_state`

```python
def _check_state(spec: AgentSpec, tools: Sequence[Tool]) -> None
```

Sources `from` du state : des outils de l'agent, avec des données pour `path` (décision D12 du state de session).

`path` se lit dans les données du résultat (`data`), après les
politiques `after_tool` : un contrat avec schéma en donne à un outil ou à
un rôle qui rend du texte. Un sous-agent n'en a jamais. Les outils MCP et
ceux d'une source ne sont connus qu'au début du run : un nom qui porte leur
préfixe est accepté ici.

## Fonction `loom_ia.runtime.wiring._returns_data`

```python
def _returns_data(tool: Tool) -> bool
```

Faux si l'outil est une fonction dont la signature exclut toute donnée.

## Fonction `loom_ia.runtime.wiring._has_schema`

```python
def _has_schema(contract: OutputContract | None) -> bool
```

Vrai pour un contrat qui valide un objet JSON : il le range dans `data`.

Un `schema_file` compte une fois lu au chargement, qui le range dans `schema` :
c'est ce champ que le guard applique.

## Fonction `loom_ia.runtime.wiring._unprovided`

```python
def _unprovided(tenant_id: TenantId, names: Iterable[str | None]) -> str
```

Le refus d'un secret que le gestionnaire de l'application ne rend pas (fonction M3).

## Fonction `loom_ia.runtime.wiring._missing_secret`

```python
def _missing_secret(tenant_id: TenantId, redirects: Mapping[str, str], secrets: Mapping[str, str], variables: Iterable[str | None]) -> str | None
```

Le refus d'un secret manquant, redit avec la variable **du client** (fonction L2).

Un client redirige `ANTHROPIC_API_KEY` vers `DUPONT_ANTHROPIC_KEY` ;
l'adaptateur, qui ne connaît que la config, disait alors « la variable
ANTHROPIC_API_KEY est absente ou vide » alors qu'elle est posée — c'est
la variable du client qui manque (essai de la fonction L2, 22/10). Rend `None` quand
aucune variable redirigée ne manque : le message d'origine dit juste.

## Fonction `loom_ia.runtime.wiring._mcp_sources`

```python
def _mcp_sources(config: LoomConfig, spec: AgentSpec, environ: Mapping[str, str] | None, pool: McpPool | None, tenant_id: TenantId=DEFAULT_TENANT, redirects: Mapping[str, str] | None=None, vaulted: bool=False) -> tuple[list[ToolSource], tuple[_Closable, ...]]
```

Sources d'outils des serveurs MCP de l'agent, et le pool créé pour lui s'il en faut un.

Portée `tenant` (point 34) : la connexion vit dans le pool comme une
`shared`, mais sous une clé qui nomme le client — deux clients du même
serveur ont chacun la leur, ouverte avec ses identifiants.

## Fonction `loom_ia.runtime.wiring._packaged_sources`

```python
def _packaged_sources(config: LoomConfig, spec: AgentSpec, environ: Mapping[str, str] | None) -> list[PackagedSource]
```

Sources d'outils des paquets que l'agent référence (jalon J6.4b).

Le paquet de chaque source est importé ici, et sa fabrique appelée avec
ce que la config déclare : des paramètres refusés refusent le montage.
Chaque source appartient à l'agent monté, qui la ferme avec lui. La table
des secrets part en lecture seule : une fabrique tierce ne modifie ni
l'environnement du process ni la table du client.

## Fonction `loom_ia.runtime.wiring._tool`

```python
def _tool(declared: PythonTool, registry: Registry, base_dir: Path | None, agent: str, *, provided: BuiltinContext | None=None) -> Tool
```

Outil Python d'un agent : fourni, enregistré ou importé, avec les réglages de sa référence.

**Lève :**

- `ConfigError` : `skill_file` non lu, ou outil introuvable ; le message nomme l'agent et l'outil.

## Fonction `loom_ia.runtime.wiring._builtin_tool`

```python
def _builtin_tool(declared: PythonTool, context: BuiltinContext) -> Tool
```

Outil fourni par loom-ia (préfixe `loom.`) : son module n'est importé qu'ici.

Un outil à paramètres reçoit ceux de sa référence, validés par son modèle,
et le contexte du montage : le dossier de la config et les chemins de loom
qu'il ne doit pas exposer.

## Fonction `loom_ia.runtime.wiring._builtin_closables`

```python
def _builtin_closables(declared: Sequence[PythonTool], tools: Sequence[Tool]) -> tuple[_Closable, ...]
```

Les outils fournis qui ont un `aclose` : ceux qu'un agent ferme avec lui.

Seulement les outils fournis : un outil à soi qui aurait un `aclose` n'était
pas fermé par loom jusqu'ici, et ne l'est toujours pas.

## Fonction `loom_ia.runtime.wiring._protected_dsns`

```python
def _protected_dsns(config: LoomConfig) -> tuple[str, ...]
```

Les variables des DSN du stockage de loom, à la racine et pour chaque client.

## Fonction `loom_ia.runtime.wiring._protected_paths`

```python
def _protected_paths(config: LoomConfig) -> tuple[Protected, ...]
```

Ce qu'un outil fourni ne doit jamais exposer au modèle.

La config et ce qui la compose (agents, prompts), le code que loom charge
ou lance depuis son dossier (modules, serveurs MCP), le fichier
d'environnement, et le stockage de la racine et de **tous** les clients :
un modèle qui y accéderait lirait des secrets ou les journaux d'un autre
client, ou réécrirait le code qui le fait tourner.

## Fonction `loom_ia.runtime.wiring._code_names`

```python
def _code_names(config: LoomConfig) -> set[str]
```

Premiers noms des modules que charge la config : `imports`, références `module:attr`.

## Fonction `loom_ia.runtime.wiring._code_found`

```python
def _code_found(names: set[str]) -> list[Protected]
```

Où Python trouve ces modules : un paquet installé ailleurs (`src/`) compte aussi.

`find_spec` d'un nom de premier niveau cherche sans rien exécuter.

## Fonction `loom_ia.runtime.wiring._code_beside`

```python
def _code_beside(base: Path) -> list[Protected]
```

Le code posé à côté de la config, même sans référence : module ou dossier de modules.

## Fonction `loom_ia.runtime.wiring._mcp_code`

```python
def _mcp_code(config: LoomConfig, base: Path | None) -> list[Protected]
```

Le code que lance un serveur MCP stdio : ses scripts, et son dossier s'il est à côté.

Seuls comptent les **scripts** que nomment sa commande et ses arguments, et
leur dossier : un argument qui est un dossier (la racine d'un serveur
filesystem, un dépôt git) ou un fichier de données (une base SQLite) est ce
que le serveur sert, pas son code. Son dossier de lancement compte s'il est
un sous-dossier de celui de la config.
