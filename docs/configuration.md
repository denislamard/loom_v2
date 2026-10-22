# loom_v2 — Clés de configuration : `loom.yaml` et `agent.yaml`

Référence relevée dans le schéma Pydantic de loom-ia 2.0.9 (`config/models.py`, `agents/spec.py`, `core/model/*`), identique au paquet publié sur PyPI.

**Règles générales**

- Une clé inconnue est **refusée** au chargement (`loom validate`, code 2), avec le chemin de la clé.
- Une clé **écrite deux fois** dans le même objet est refusée de même, avec ses deux lignes : YAML garderait la seconde en silence. Surcharger une clé fusionnée (`<<: *ancre`) reste permis.
- Les chemins sont relatifs au **dossier de `loom.yaml`** ; un `system_file` est relatif à `prompts_dir`.
- La config ne porte **jamais un secret** : les clés `*_env` nomment la variable d'environnement qui le contient.
- « **obligatoire** » dans la colonne *Défaut* : la clé doit être écrite, sinon le chargement s'arrête.
- `null` dans la colonne *Défaut* : la fonction est inactive tant que la clé n'est pas donnée.

## Rubriques

### `loom.yaml`

| Rubrique | Objet |
|---|---|
| [`version`](#version) | Version du schéma de config |
| [`profile`](#profile) | Profil actif (`dev`, `prod`) |
| [`profiles`](#profiles) | Surcharges par profil |
| [`env_file`](#env_file) | Fichier d'environnement du projet |
| [`imports`](#imports) | Modules Python chargés au démarrage |
| [`agents_dir`](#agents_dir) | Dossier des fichiers d'agents |
| [`prompts_dir`](#prompts_dir) | Dossier des prompts |
| [`models`](#models) | Modèles LLM disponibles |
| [`mcp_servers`](#mcp_servers) | Serveurs MCP utilisables par les agents |
| [`tool_sources`](#tool_sources) | Sources d'outils fournies par des paquets |
| [`storage`](#storage) | Journal, artefacts, idempotence, file, bus, chiffrement, rétention |
| [`sessions`](#sessions) | Snapshots, compaction, taille du state |
| [`execution`](#execution) | Outils, pièces jointes, arrêt, concession |
| [`budgets`](#budgets) | Budgets par défaut des agents |
| [`telemetry`](#telemetry) | Logs, capture, masquage, collecteurs |
| [`tenants`](#tenants) | Clients et leurs surcharges |
| [`security`](#security) | Clés d'API |
| [`server`](#server) | Serveurs HTTP et MCP |
| [`triggers`](#triggers) | Déclencheurs (webhooks) |
| [`agents`](#agents-en-ligne) | Agents écrits directement dans `loom.yaml` |
| [`base_dir`](#base_dir) | Posé par le chargeur, à ne pas écrire |

### `agent.yaml` (un fichier par agent dans `agents_dir`)

| Rubrique | Objet |
|---|---|
| [`name`](#name) | Nom de l'agent |
| [`description`](#description) | Description de l'agent |
| [`expose`](#expose) | Publication en REST et en MCP |
| [`main`](#main) | L'orchestrateur |
| [`max_iterations`](#max_iterations) | Tours de boucle au plus |
| [`timeout`](#timeout) | Délai maximal d'un run |
| [`approval`](#approval) | Règles des approbations |
| [`output`](#output) | Contrat de la réponse finale |
| [`judge`](#judge) | Juge de la réponse finale |
| [`budget`](#budget) | Budgets propres à l'agent |
| [`stream_output`](#stream_output) | Diffusion de la réponse finale |
| [`tools`](#tools) | Outils Python, MCP et de paquet |
| [`roles`](#roles) | Rôles LLM délégués |
| [`subagents`](#subagents) | Sous-agents |
| [`policies`](#policies) | Politiques (hooks) |
| [`max_depth`](#max_depth) | Profondeur des sous-agents |
| [`state`](#state) | Clés du state de session |

Blocs partagés, décrits une fois : [contrat de sortie](#bloc-contrat-de-sortie-output), [surcharges d'outil](#bloc-surcharges-doutil-tools), [réglages `llm`](#bloc-réglages-llm), [budgets](#budgets), [stockage](#storage).

---

# Fichier `loom.yaml`

## `version`

| Clé | Rôle et impact | Valeurs | Défaut | Exemple |
|---|---|---|---|---|
| `version` | Version du schéma de config. Toute autre valeur arrête le chargement. | `1` | **obligatoire** | `version: 1` |

## `profile`

| Clé | Rôle et impact | Valeurs | Défaut | Exemple |
|---|---|---|---|---|
| `profile` | Profil actif. `prod` change les avertissements en erreurs (approbation sans journal durable, budget en dollars sur un modèle sans tarif, juge corrélé, juge bloquant échantillonné…), refuse `serve --reload` et `judges: skip`, et avertit si des variables viennent du `.env`. `dev` change en avertissements certains refus (approbation avec un journal en mémoire). L'option `--profile` puis la variable `LOOM_PROFILE` l'emportent sur le fichier. | `dev`, `prod` | `null` : aucun profil, rien n'est durci ni assoupli | `profile: dev` |

## `profiles`

| Clé | Rôle et impact | Valeurs | Défaut | Exemple |
|---|---|---|---|---|
| `profiles` | Surcharges partielles de `loom.yaml`, appliquées quand le profil est actif. Les objets fusionnent en profondeur, les **listes sont remplacées**, `params` et `llm` sont remplacés en bloc. `env_file` y est refusé. | `{dev: {…}, prod: {…}}`, chaque valeur reprenant des clés de `loom.yaml` | `{}` | voir ci-dessous |

```yaml
profiles:
  prod:
    telemetry: {logging: {level: WARNING, format: json}}
    storage: {events: {backend: postgres, dsn_env: LOOM_DSN}, artifacts: {backend: local, path: /var/loom/fichiers}}
```

## `env_file`

| Clé | Rôle et impact | Valeurs | Défaut | Exemple |
|---|---|---|---|---|
| `env_file` | Fichier de variables lu par la commande `loom` **avant** la config (clés d'API, DSN, `LOOM_PROFILE`). Une variable déjà définie, même vide, l'emporte. Fichier absent : erreur, code 2. Lu sans interpolation. `--env-file` l'emporte, `--no-env-file` n'en lit aucun. La librairie ne lit rien seule (`load_env_file()`). | chemin relatif au dossier de `loom.yaml` | `null` : le `.env` voisin de `loom.yaml` est lu s'il existe | `env_file: secrets/dev.env` |

## `imports`

| Clé | Rôle et impact | Valeurs | Défaut | Exemple |
|---|---|---|---|---|
| `imports` | Modules Python importés au démarrage, qui enregistrent leurs outils (`@tool`) et leurs politiques. Le dossier de `loom.yaml` est importable : un `outils.py` voisin s'écrit `outils`. | liste de noms de modules | `[]` | `imports: [outils, politiques]` |

## `agents_dir`

| Clé | Rôle et impact | Valeurs | Défaut | Exemple |
|---|---|---|---|---|
| `agents_dir` | Dossier dont chaque `*.yaml` / `*.yml` est un agent, monté au démarrage. Dossier absent : erreur. | chemin | `agents` | `agents_dir: agents/` |

## `prompts_dir`

| Clé | Rôle et impact | Valeurs | Défaut | Exemple |
|---|---|---|---|---|
| `prompts_dir` | Base des `system_file` (`main`, rôles, compaction). Un prompt introuvable arrête le chargement. | chemin | `prompts` | `prompts_dir: prompts/` |

## `models`

Liste des modèles. Chaque modèle est cité par son `id` (`main.model`, rôles, juges, `fallbacks`, compaction).

| Clé | Rôle et impact | Valeurs | Défaut | Exemple |
|---|---|---|---|---|
| `id` | Identifiant du modèle dans la config ; unique. | texte | **obligatoire** | `id: HAIKU` |
| `sdk` | Adaptateur utilisé. `fake` : modèle simulé, sans appel réseau (tests). | `anthropic`, `openai`, `fake` | **obligatoire** | `sdk: anthropic` |
| `api` | API OpenAI utilisée ; refusé hors `sdk: openai`. | `chat`, `responses` | `chat` (avec `sdk: openai`) | `api: responses` |
| `model` | Nom du modèle chez le fournisseur. | texte | **obligatoire** | `model: claude-haiku-5-5` |
| `base_url` | Point d'accès : compatible OpenAI (vLLM, Ollama, Together…). Les variables `ANTHROPIC_BASE_URL` / `OPENAI_BASE_URL` ne sont jamais lues. | URL | adresse officielle du fournisseur du SDK | `base_url: http://localhost:11434/v1` |
| `api_key_env` | Variable qui porte la clé d'API ; absente ou vide : erreur à la création du client. Sans `api_key_env`, aucune clé n'est envoyée : vers l'API officielle (`sdk: anthropic` ou `openai` sans `base_url`), `loom validate` et le démarrage le signalent (erreur en profil `prod`), et un refus 401 le rappelle. | nom de variable | `null` : aucune clé envoyée (serveur local, `fake`) | `api_key_env: ANTHROPIC_API_KEY` |
| `max_tokens` | Plafond de sortie par appel ; un rôle le remplace par `llm.max_tokens`. | entier > 0 | `null` : 4096 chez Anthropic (exigé par l'API) ; non envoyé chez OpenAI | `max_tokens: 4096` |
| `params` | Bloc transmis tel quel au fournisseur, qui seul juge ce qu'il accepte (`temperature`, `thinking`…) : il ne part qu'avec les appels à ce modèle, c'est donc ici qu'un paramètre propre à un fournisseur se déclare. Pour `sdk: fake`, `script` décrit les réponses simulées. | objet | `{}` | `params: {temperature: 0.2}` sur un modèle `sdk: openai` ; les Claude 5.5 refusent `temperature` (400) |
| `timeouts.first_token` | Délai avant le premier morceau du flux (s). | nombre > 0, `null` | `60` | `timeouts: {first_token: 20}` |
| `timeouts.idle` | Délai entre deux morceaux (s). | nombre > 0, `null` | `60` | |
| `timeouts.total` | Durée maximale d'un appel complet (s). | nombre > 0, `null` | `300` | |
| `retry.max_attempts` | Tentatives au total sur une erreur transitoire ou une surcharge, la première comprise. | entier > 0 | `3` | `retry: {max_attempts: 5}` |
| `retry.initial_delay` | Attente avant la 2e tentative (s). | nombre ≥ 0 | `1.0` | |
| `retry.multiplier` | Facteur de l'attente d'une tentative à l'autre. | nombre ≥ 1 | `2.0` | |
| `retry.max_delay` | Plafond d'une attente ; un `Retry-After` du fournisseur au-delà fait abandonner. | nombre ≥ 0 | `30.0` | |
| `capabilities.tools` | Le modèle sait appeler des outils ; exigé d'un orchestrateur qui a des outils et d'un juge. | booléen | `true` | |
| `capabilities.thinking` | Il produit un raisonnement, qui lui est renvoyé pendant sa boucle d'outils. | booléen | `false` | |
| `capabilities.native_json` | Il accepte un schéma de sortie : celui du contrat lui est transmis pour un appel sans outils. | booléen | `false` | |
| `capabilities.forced_tool_choice` | Il accepte qu'on lui impose un appel d'outil (`tool_choice: required`). `false` : la requête qui l'impose part en `auto` — le verdict d'un juge n'est plus demandé que par sa consigne (un verdict absent est une erreur du juge, voir `on_error`), et `loom.require_tool` est refusée au chargement sur ce modèle. **À déclarer `false` pour Claude Opus 5.5 et Fable 5.1**, qui refusent l'outil imposé (erreur 400 « tool_choice: type "tool" and "any" are not supported for this model. », que loom complète par la clé à déclarer). | booléen | `true` | `capabilities: {forced_tool_choice: false}` |
| `capabilities.streaming` | `false` : appel non streamé, rejoué en un flux d'un seul morceau. | booléen | `true` | |
| `capabilities.context_window` | Fenêtre en tokens, vérifiée avant l'appel. | entier > 0 | `null` : pas de vérification | `context_window: 200000` |
| `capabilities.vision` | Il reçoit les images ; sans elle, une simple mention textuelle. Exigé d'un rôle vision. | booléen | `false` | `vision: true` |
| `capabilities.image_input` | Formes d'envoi d'image acceptées ; seul `base64` est utilisé aujourd'hui, et il est exigé avec `vision`. | liste de `base64`, `url`, `file_id` | `[base64]` | |
| `capabilities.image_formats` | Formats d'image acceptés. | liste de `jpeg`, `png`, `gif`, `webp` | les quatre | |
| `capabilities.max_image_bytes` | Taille maximale d'une image envoyée (octets). | entier > 0 | `null` | |
| `capabilities.tool_result_media` | Images acceptées dans un résultat d'outil ; sinon elles suivent dans un message utilisateur placé après les résultats, et le modèle peut les prendre pour des images envoyées par l'utilisateur. **À déclarer pour l'API Anthropic** (`sdk: anthropic` sans `base_url`) avec `vision: true` : `loom validate` et le démarrage le rappellent par un avertissement, dans tous les profils. Pas de défaut automatique : un fournisseur compatible Anthropic (`base_url`, MiniMax…) n'accepte pas forcément une image dans un résultat d'outil. | booléen | `false` | `capabilities: {vision: true, tool_result_media: true}` |
| `pricing.input` / `output` / `cache_read` / `cache_write` | Tarif en $ par million de tokens, utilisé pour le coût, les budgets en dollars et les rapports. Sans tarif, les appels comptent 0 $ (erreur en `prod` si un budget est en dollars). | nombre ≥ 0 | `0.0` | `pricing: {input: 1, output: 5}` |
| `pricing.tiers` | Paliers : au-delà de `above` tokens d'entrée par appel, ces prix s'appliquent à tout l'appel ; un prix absent reprend le tarif de base. Seuils strictement croissants. | liste de `{above, input, output, cache_read, cache_write}` | `[]` | `tiers: [{above: 512000, input: 2}]` |
| `circuit_breaker.failures` | Échecs de suite avant d'écarter le modèle (bascule directe sur le secours), pour toute l'instance. | entier > 0 | `5` | |
| `circuit_breaker.cooldown` | Durée d'écart (s), puis un essai. | nombre > 0 | `60` | |
| `circuit_breaker` | `null` retire le disjoncteur. | objet, `null` | `{failures: 5, cooldown: 60}` | `circuit_breaker: null` |
| `cache.system` / `tools` / `messages` | Points de cache de prompt Anthropic : sur le prompt système, sur le dernier outil, sur le dernier bloc de la conversation. Refusé avec `sdk: openai`. | booléens | `false` chacun | `cache: {system: true, tools: true, messages: true}` |
| `cache.ttl` | Durée de vie d'une entrée de cache ; le prix `cache_write` est celui de cette durée. | `5m`, `1h` | `5m` | |
| `cache` | Bloc entier. | objet, `null` | `null` : aucun point de cache | |

```yaml
models:
  - id: HAIKU
    sdk: anthropic
    model: claude-haiku-5-5
    api_key_env: ANTHROPIC_API_KEY
    max_tokens: 4096
    pricing: {input: 0.1, output: 0.5, cache_read: 0.01, cache_write: 0.125}
    cache: {system: true, tools: true, messages: true}
  - id: SIMULE
    sdk: fake
    model: fake-1
    params:
      script:
        - {with_tool: fetch_page, tool_calls: [{name: fetch_page, arguments: {url: "https://example.com"}}]}
        - {text: "Voici le résumé de la page."}
```

Une réponse de `params.script` (`sdk: fake`) : `text`, `tool_calls` (`[{name, arguments}]`), `reasoning`, conditions `with_tool` / `without_tool` / `with_text` / `without_text`, et `forced` (texte rendu quand la requête interdit les outils). Sans `script`, le modèle répond « Écho : <demande> ».

## `mcp_servers`

Serveurs MCP, déclarés une fois et référencés par les agents (`tools: [{mcp: <name>}]`). Leurs outils sont préfixés `<name>__`.

| Clé | Rôle et impact | Valeurs | Défaut | Exemple |
|---|---|---|---|---|
| `name` | Nom du serveur et préfixe de ses outils ; pas de `__`, 40 caractères au plus, unique, distinct des `tool_sources`. | lettres, chiffres, `-`, `_` | **obligatoire** | `name: crm` |
| `transport` | Mode de connexion. | `stdio`, `http` | **obligatoire** | `transport: stdio` |
| `command` | Programme lancé (stdio) ; obligatoire en stdio, refusé en http. | texte | `null` | `command: uv` |
| `args` | Arguments du programme (stdio). | liste de textes | `[]` | `args: [run, serveur_crm.py]` |
| `env` | Variables passées en clair au programme (stdio). | `{VAR: valeur}` | `{}` | `env: {LOG: info}` |
| `env_from` | Variables du serveur lues dans l'environnement de loom (stdio). | `{VAR_DU_SERVEUR: VAR_DE_LOOM}` | `{}` | `env_from: {CRM_TOKEN: MARTIN_CRM}` |
| `cwd` | Dossier de lancement (stdio). | chemin | dossier de `loom.yaml` | `cwd: serveurs/` |
| `url` | Point d'accès (http) ; obligatoire en http, refusé en stdio. | URL | `null` | `url: https://crm.exemple.fr/mcp` |
| `headers_env` | En-têtes HTTP lus dans l'environnement (http). | `{En-tête: VARIABLE}` | `{}` | `headers_env: {Authorization: CRM_AUTH}` |
| `scope` | Portée de la connexion : une pour le process, une par client (avec ses secrets), ou une par run (ouverte et fermée avec lui). | `shared`, `tenant`, `run` | `shared` | `scope: tenant` |
| `connect_timeout` | Délai d'établissement de la connexion, initialisation comprise (s). | nombre > 0 | `10` | |
| `idle_timeout` | Portée `shared` : fermeture après ce délai sans usage (s). | nombre > 0, `null` (jamais fermée) | `300` | |
| `tools` | Surcharges par outil (nom court, sans préfixe) : voir [surcharges d'outil](#bloc-surcharges-doutil-tools). | `{outil: {…}}` | `{}` | `tools: {creer_devis: {approval: always}}` |
| `circuit_breaker` | Connexions ratées de suite avant de déclarer le serveur indisponible, et durée d'écart ; `null` le retire. | `{failures, cooldown}`, `null` | `{failures: 5, cooldown: 60}` | |

## `tool_sources`

Sources d'outils fournies par un paquet installé (point d'entrée du groupe `loom_ia.tools`), référencées par `tools: [{source: <name>}]`. Exemple fourni : `forge`, la sandbox Firecracker.

| Clé | Rôle et impact | Valeurs | Défaut | Exemple |
|---|---|---|---|---|
| `name` | Nom dans la config et préfixe des outils (`<name>__outil`) ; unique, distinct des serveurs MCP. | comme `mcp_servers.name` | **obligatoire** | `name: sandbox` |
| `entry_point` | Point d'entrée du paquet ; le paquet n'est importé que si un agent cite la source. | nom de point d'entrée | **obligatoire** | `entry_point: forge` |
| `params` | Réglages remis tels quels à la fabrique du paquet, qui les vérifie (pas de secret : la fabrique reçoit ceux du client). | objet | `{}` | `params: {vm: firecracker/vms/agent}` |
| `tools` | Surcharges par outil : voir [surcharges d'outil](#bloc-surcharges-doutil-tools). | `{outil: {…}}` | `{}` | |

## `storage`

Le même bloc sert à la racine et dans `tenants[].storage` (stockage propre à un client).

### `storage.events` — journal des sessions

| Clé | Rôle et impact | Valeurs | Défaut | Exemple |
|---|---|---|---|---|
| `backend` | Où vit le journal (sessions, runs, traces). `memory` : perdu à l'arrêt, sans reprise ni approbation durables. | `memory`, `jsonl`, `sqlite`, `postgres` | `memory` | `backend: jsonl` |
| `path` | `jsonl` : dossier des journaux ; `sqlite` : fichier de la base. Obligatoire pour ces deux-là, refusé pour `postgres`. | chemin | `null` | `path: data` |
| `dsn_env` | `postgres` : variable qui porte le DSN ; obligatoire pour lui, refusé ailleurs. | nom de variable | `null` | `dsn_env: LOOM_DSN` |
| `role` | `postgres` : rôle applicatif pris par chaque connexion (politique de lignes, journal non modifiable) ; `null` s'en passe et rend le journal modifiable. | texte, `null` | `loom_app` | |
| `pool.min_size` / `max_size` | `postgres` : taille du pool de connexions ; refusé hors `postgres`. | entiers ≥ 1, min ≤ max | `1` / `10` | `pool: {max_size: 20}` |
| `pool.command_timeout` | Durée maximale d'une instruction SQL (s). | nombre > 0 | `60` | |
| `pool.lock_timeout` | Attente d'un verrou (s). | nombre > 0 | `10` | |
| `pool.acquire_timeout` | Attente d'une connexion libre (s). | nombre > 0 | `30` | |

### `storage.artifacts` — fichiers (pièces jointes, sorties d'outils, résultats déportés)

| Clé | Rôle et impact | Valeurs | Défaut | Exemple |
|---|---|---|---|---|
| `backend` | Stockage des fichiers, hors du journal (référencés par URI). Obligatoire avec un journal `postgres`. | `local`, `memory` | `null` : suit le journal — `local` dans `<journal>/.artifacts` pour `jsonl` et `sqlite`, `memory` sinon | `backend: local` |
| `path` | Dossier du stockage `local` ; obligatoire si le journal n'est pas en fichiers, refusé pour `memory`. | chemin | `null` | `path: fichiers/` |

### `storage.idempotency` — clés d'idempotence des outils à effet de bord

| Clé | Rôle et impact | Valeurs | Défaut | Exemple |
|---|---|---|---|---|
| `backend` | `journal` : chaque run garde ses clés dans son journal (clés techniques) ; `memory` : un process. Une clé **métier** exige `sqlite`, `postgres` ou `redis`. Non surchargeable par un client. | `journal`, `memory`, `sqlite`, `postgres`, `redis` | `journal` | `backend: sqlite` |
| `path` | `sqlite` : fichier de la base (obligatoire), refusé ailleurs. | chemin | `null` | `path: data/idempotence.db` |
| `dsn_env` | `postgres` : variable du DSN (obligatoire). | nom de variable | `null` | `dsn_env: LOOM_DSN` |
| `url_env` | `redis` : variable de l'URL (obligatoire). | nom de variable | `null` | `url_env: REDIS_URL` |
| `role` | `postgres` : rôle applicatif. | texte, `null` | `loom_app` | |
| `pool.*` | `postgres` : comme `storage.events.pool`. | | | |

### `storage.queue` — file des tâches de fond (runs soumis, reprises, résumés)

| Clé | Rôle et impact | Valeurs | Défaut | Exemple |
|---|---|---|---|---|
| `backend` | `asyncio` : les tâches tournent dans le process qui les soumet ; `rabbitmq` : elles sont publiées et tournent dans `loom worker`. | `asyncio`, `rabbitmq` | `asyncio` | `backend: rabbitmq` |
| `url_env` | `rabbitmq` : variable de l'URL du courtier (obligatoire), refusé ailleurs. | nom de variable | `null` | `url_env: AMQP_URL` |
| `max_running` | `asyncio` : tâches menées de front, les suivantes attendent ; refusé avec `rabbitmq` (borné par `loom worker --jobs`). | entier > 0 | `16` | `max_running: 4` |

### `storage.bus` — nouvelles d'écriture entre process

| Clé | Rôle et impact | Valeurs | Défaut | Exemple |
|---|---|---|---|---|
| `backend` | `memory` suffit dans un seul process ; `postgres` ou `redis` dès qu'un `loom serve` suit un run mené par un `loom worker` (SSE). | `memory`, `postgres`, `redis` | `memory` | `backend: redis` |
| `dsn_env` | `postgres` : variable du DSN (obligatoire). | nom de variable | `null` | |
| `url_env` | `redis` : variable de l'URL (obligatoire). | nom de variable | `null` | `url_env: REDIS_URL` |

### `storage.encryption` — chiffrement au repos

| Clé | Rôle et impact | Valeurs | Défaut | Exemple |
|---|---|---|---|---|
| `encryption` | Scelle la charge de chaque événement et les octets de chaque fichier (l'enveloppe reste lisible et filtrable). Refusé avec un journal `memory` et dans `tenants[].storage`. Extra `crypto`. | objet, `null` | `null` : contenus en clair | voir ci-dessous |
| `encryption.keys` | **Noms de secrets** (pas les clés) ; le premier chiffre, tous déchiffrent (rotation). Chaque client redirige le nom vers sa variable (`tenants[].secrets`) : effacer sa variable rend son journal illisible. | liste non vide de noms | **obligatoire** dans le bloc | `keys: [journal_key]` |

### `storage.retention` — durée de vie des sessions dormantes

| Clé | Rôle et impact | Valeurs | Défaut | Exemple |
|---|---|---|---|---|
| `retention.events_days` | Jours depuis la dernière écriture d'une session au-delà desquels `loom retention --yes` l'efface entière (journal, fichiers, clés), même avec un run en attente d'approbation. Rien ne s'efface tout seul. | entier > 0, `null` | `null` : rien n'est effacé | `retention: {events_days: 365}` |

```yaml
storage:
  events: {backend: jsonl, path: data}
  idempotency: {backend: sqlite, path: data/idempotence.db}
  encryption: {keys: [journal_key]}
  retention: {events_days: 365}
```

## `sessions`

| Clé | Rôle et impact | Valeurs | Défaut | Exemple |
|---|---|---|---|---|
| `snapshot_every` | Événements ajoutés depuis le dernier marqueur avant d'écrire un snapshot de l'historique (relire un snapshot coûte moins que rejouer le journal). | entier > 0 | `50` | `snapshot_every: 100` |
| `compaction` | Résumé automatique d'une session longue par l'agent interne `_compaction`. | objet, `null` | `null` : jamais résumée, elle grandit jusqu'à la fenêtre du modèle | voir ci-dessous |
| `compaction.model` | Modèle du résumé (`id` de `models`), en général moins cher. | id de modèle | **obligatoire** dans le bloc | `model: HAIKU` |
| `compaction.over_tokens` | Taille de l'historique au-delà de laquelle un résumé est dû (estimation caractères/4). | entier > 0 | `12000` | `over_tokens: 50000` |
| `compaction.hard_tokens` | Taille au-delà de laquelle le résumé devient bloquant ; doit dépasser `over_tokens`. | entier > 0 | `150000` | |
| `compaction.keep_last` | Tours (runs) laissés intacts en fin d'historique. | entier ≥ 0 | `6` | `keep_last: 2` |
| `compaction.fidelity_check` | Contrôle que les repères du segment (références, e-mails, nombres) sont dans le résumé. | booléen | `true` | |
| `compaction.blocking` | `true` : le résumé dû se fait à l'ouverture du run suivant, qui l'attend (adapté à `loom run`) ; `false` : tâche de fond après le run. | booléen | `false` | `blocking: true` |
| `compaction.system_file` | Prompt du résumé à la place du prompt interne, relatif à `prompts_dir`. | chemin | `null` : prompt interne | `system_file: resume.md` |
| `media.keep_images` | Images des tours précédents dans les requêtes. `all` : un modèle vision revoit toutes les images de la session à chaque appel (coût, et relecture du stockage : une image disparue fait échouer le run). `last_run` : remplacées par une mention (« [Note du système : ce message contenait l'image photo.jpg (image/jpeg, 165 Ko), jointe par l'utilisateur. Elle a été retirée de l'historique après ce tour pour alléger les requêtes ; si tu l'as décrite à ce tour-là, ta description venait de l'image elle-même et reste valable.] », « ce résultat contenait… » pour une image rendue par un outil) ; seules celles du run en cours partent en octets, et une image disparue ne gêne plus. **Limite** : pour une image rendue par un outil, le modèle peut ensuite désavouer la description qu'il en avait faite (« ma description était inventée », Haiku, tests du 20/10) ; une pièce jointe se passe bien. Le journal ne garde que des références dans les deux cas. | `all`, `last_run` | `all` | `media: {keep_images: last_run}` |
| `state.max_value_bytes` | Taille maximale d'une valeur du state (JSON compact) ; au plus `max_total_bytes`. | entier > 0 | `1024` | `state: {max_value_bytes: 2048}` |
| `state.max_total_bytes` | Taille maximale de tout le state d'une session. | entier > 0 | `4096` | |

## `execution`

| Clé | Rôle et impact | Valeurs | Défaut | Exemple |
|---|---|---|---|---|
| `tools.timeout` | Délai par défaut d'un outil (s), sauf délai propre à l'outil. | nombre > 0, `null` (sans limite) | `30` | `tools: {timeout: 60}` |
| `tools.validate_arguments` | Contrôle des arguments par le schéma de l'outil ; une erreur repart au modèle. | booléen | `true` | |
| `tools.offload_over` | Taille d'un résultat (caractères) au-delà de laquelle il est déporté en artefact. | entier > 0, `null` (jamais) | `50000` | |
| `attachments.max_bytes` | Taille maximale d'une pièce jointe. | entier > 0 | `5242880` (5 Mio) | |
| `attachments.types` | Types MIME acceptés en entrée d'un run ; **images seulement**. | liste parmi `image/jpeg`, `image/png`, `image/gif`, `image/webp` | les quatre | `types: [image/png]` |
| `attachments.max_files` | Pièces jointes par run ; borne aussi un envoi REST. | entier > 0 | `10` | |
| `shutdown_timeout` | Délai laissé aux tâches de fond (compaction) à la fermeture de l'instance, puis annulation. | nombre > 0 | `30` | `shutdown_timeout: 120` |
| `lease` | Durée de la concession qu'une instance prend sur le run qu'elle mène (renouvelée au tiers) ; passée, un autre worker peut le reprendre. | nombre > 0 | `60` | |

## `budgets`

Budgets par défaut de tous les agents. Le même bloc sert dans `tenants[].budgets` et dans le `budget` d'un agent : chacun surcharge clé par clé. Vérifiés avant chaque appel de l'orchestrateur (sauf `tenant`, lu à l'ouverture du run).

| Clé | Rôle et impact | Valeurs | Défaut | Exemple |
|---|---|---|---|---|
| `run.max_cost` | Coût maximal d'un run ($). | nombre ≥ 0 | `null` (sans limite) | `run: {max_cost: 0.5}` |
| `run.max_tokens` | Tokens maximaux d'un run (utile sans tarif). | entier ≥ 0 | `null` | |
| `run.max_calls` | Appels de modèle d'un run (orchestrateur, rôles, juges ; pas ceux d'un sous-agent). | entier ≥ 0 | `null` | `max_calls: 20` |
| `session.max_cost` / `max_tokens` | Plafonds d'une session : runs précédents et run en cours. | nombres ≥ 0 | `null` | `session: {max_cost: 5}` |
| `tenant.max_cost_per_day` / `max_tokens_per_day` | Plafond d'un client sur la journée (UTC) : au-delà, un nouveau run est refusé. | nombres ≥ 0 | `null` | `tenant: {max_cost_per_day: 10}` |
| `tenant.max_cost_per_month` / `max_tokens_per_month` | Idem sur le mois. | nombres ≥ 0 | `null` | |
| `on_exceed` | `stop` : `budget.exceeded`, puis réponse forcée sans outils ; `warn` : l'événement seul, le run continue. | `stop`, `warn` | `stop` | `on_exceed: warn` |

## `telemetry`

| Clé | Rôle et impact | Valeurs | Défaut | Exemple |
|---|---|---|---|---|
| `logging.level` | Niveau des logs techniques (distincts du journal). | niveau `logging` (`DEBUG`, `INFO`, `WARNING`, `ERROR`…) | `INFO` | `logging: {level: WARNING}` |
| `logging.format` | Format des logs. | `console`, `json` | `console` | |
| `capture.exports` | Ce qui part vers les collecteurs : l'enveloppe seule (`metadata`), ou aussi le contenu masqué (`content`, refusé sans `exporters`). Le journal garde tout, toujours. | `metadata`, `content` | `metadata` | |
| `capture.raw_exchanges` | Écrit au journal chaque échange HTTP avec le fournisseur (`model.exchanged`) : débogage, vérification du prompt envoyé. | booléen | `false` | `capture: {raw_exchanges: true}` |
| `capture.raw_max_bytes` | Borne de chaque corps brut gardé. | entier > 0 | `262144` (256 Kio) | |
| `redaction.patterns` | Motifs masqués dans le contenu exporté. Les quatre fournis s'appliquent toujours ; la liste en ajoute (`{name, regex}`) ou en réordonne. | noms fournis `secret`, `email`, `phone`, `iban`, ou `{name, regex}` | `[secret, email, phone, iban]` | `patterns: [{name: devis, regex: "D-\\d{4}-\\d+"}]` |
| `exporters` | Collecteurs ; sans eux, rien ne sort du journal que les logs. | liste | `[]` | voir ci-dessous |
| `exporters[].type` | Type de collecteur. | `otel` | **obligatoire** | `type: otel` |
| `exporters[].endpoint_env` | Variable qui porte l'adresse OTLP. | nom de variable | `OTEL_EXPORTER_OTLP_ENDPOINT` | |
| `exporters[].protocol` | Protocole OTLP. | `http/protobuf`, `grpc` | `http/protobuf` | |
| `exporters[].headers_env` | Variable des en-têtes (`clé=valeur,clé=valeur`). | nom de variable | `null` | `headers_env: OTEL_HEADERS` |
| `exporters[].service_name` | Nom du service dans les traces. | texte | `loom-ia` | |
| `exporters[].timeout` | Délai d'un envoi (s). | nombre > 0 | `10` | |

```yaml
telemetry:
  logging: {level: WARNING}
  capture: {raw_exchanges: true}
  exporters: [{type: otel, protocol: grpc}]
```

## `tenants`

Sans cette liste, seul le client `default` existe ; avec elle, la liste est fermée et un client inconnu est refusé. Un client partage agents, prompts et outils ; il ne surcharge que ce qui suit.

| Clé | Rôle et impact | Valeurs | Défaut | Exemple |
|---|---|---|---|---|
| `id` | Identifiant du client (`--tenant`, clé d'API). | texte non vide | **obligatoire** | `id: dupont-plomberie` |
| `agents` | Agents que ce client peut lancer. | liste de noms d'agents | `[]` : tous | `agents: [relance]` |
| `tools_deny` | Outils retirés à ce client, sous le nom vu par le modèle (préfixe compris) : outil, rôle ou sous-agent. | liste de noms | `[]` | `tools_deny: [crm__supprimer]` |
| `models` | Remplacement de modèles, partout (orchestrateur, rôles, juges, secours, compaction) ; les deux doivent être déclarés. | `{id_config: id_client}` | `{}` | `models: {HAIKU: SONNET}` |
| `approvals` | Approbation imposée par outil, quoi qu'en dise sa déclaration. | `{outil: never\|always\|policy}` | `{}` | `approvals: {envoyer_mail: always}` |
| `secrets` | Redirection des noms de secrets vers les variables de ce client (clé d'API, clé de chiffrement…). | `{nom: VARIABLE}` | `{}` : environnement commun | `secrets: {ANTHROPIC_API_KEY: DUPONT_KEY}` |
| `variables` | Valeurs des `{{ variables }}` citées par les prompts ; une variable citée et absente arrête le chargement. | `{nom: valeur}` | `{}` | `variables: {entreprise: Plomberie Dupont}` |
| `budgets` | Budgets du client, sur ceux de la racine, clé par clé (voir [`budgets`](#budgets)). | objet, `null` | `null` | `budgets: {tenant: {max_cost_per_month: 50}}` |
| `quotas.runs_per_minute` | Runs que le client peut lancer par minute, tous accès confondus, par instance (fenêtre en mémoire : `loom run` ne l'atteint pas, N process l'accordent N fois). | entier > 0, `null` | `null` | `quotas: {runs_per_minute: 30}` |
| `storage` | Stockage propre au client (isolation physique) ; doit déclarer `events` ; ni `encryption` ni `idempotency`. Voir [`storage`](#storage). | objet, `null` | `null` : celui de la racine | `storage: {events: {backend: jsonl, path: data/dupont}}` |
| `retention.events_days` | Rétention propre au client ; `null` explicite annule celle de la racine. | entier > 0, `null` | bloc absent : celle de la racine | `retention: {events_days: 90}` |
| `telemetry.capture.exports` / `raw_exchanges` / `raw_max_bytes` | Capture propre au client, clé par clé ; collecteurs et motifs restent communs. | comme `telemetry.capture` | `null` : celle de la racine | `telemetry: {capture: {raw_exchanges: false}}` |

## `security`

| Clé | Rôle et impact | Valeurs | Défaut | Exemple |
|---|---|---|---|---|
| `api_keys` | Clés d'API de l'accès REST et MCP HTTP ; sans clé valide, 401. Obligatoire avec `server.mcp.http: true`. | liste | `[]` | voir ci-dessous |
| `api_keys[].id` | Nom de la clé ; unique. | texte | **obligatoire** | `id: crm` |
| `api_keys[].hash` | Empreinte donnée par `loom keys create` (jamais la clé). | `sha256:` + 64 hexadécimaux minuscules | **obligatoire** | |
| `api_keys[].tenant` | Client au nom duquel la clé agit ; doit être déclaré si `tenants` existe. | id de client | `default` | `tenant: dupont-plomberie` |
| `api_keys[].scopes` | Droits : lancer/annuler/webhooks, lire, lire le contenu, approuver, administrer (suppression, `judges: skip`). | liste de `run`, `read`, `read_content`, `approve`, `admin` | `[run, read]` | `scopes: [run, read, approve]` |
| `api_keys[].agents` | Agents accessibles à cette clé. | liste | `[]` : tous | `agents: [relance]` |
| `api_keys[].rate_limit.per_minute` | Requêtes par minute (REST et MCP HTTP) ; au-delà, 429. | entier > 0 | bloc absent : aucune limite | `rate_limit: {per_minute: 60}` |
| `api_keys[].expires` | Fin de validité ; une clé expirée est refusée à l'appel. | date-heure avec fuseau | `null` : ne périme pas | `expires: 2027-01-01T00:00:00Z` |

```yaml
security:
  api_keys:
    - id: crm
      hash: sha256:<empreinte donnée par loom keys create>
      tenant: dupont-plomberie
      scopes: [run, read]
```

## `server`

| Clé | Rôle et impact | Valeurs | Défaut | Exemple |
|---|---|---|---|---|
| `http.host` | Adresse d'écoute de `loom serve` (`--host` l'emporte). | adresse | `127.0.0.1` | `host: 0.0.0.0` |
| `http.port` | Port d'écoute (`--port` l'emporte). | 1 à 65535 | `8000` | `port: 8080` |
| `http.base_path` | Préfixe commun des routes ; commence par `/`. | chemin | `""` | `base_path: /loom` |
| `http.max_body_bytes` | Plafond d'un corps de requête ; au-delà, 413. Par MCP, le SDK plafonne déjà à 4 Mio. | entier > 0 | `134217728` (128 Mio) | |
| `mcp.http` | Monte le serveur MCP dans l'application REST, sous `<base_path>/mcp` ; exige des clés d'API et l'extra `mcp`. | booléen | `false` | `mcp: {http: true}` |
| `mcp.file_roots` | Dossiers où un lien `file://` joint à un appel MCP peut être lu. | liste de chemins | `[]` : liens `file://` refusés | `file_roots: [partage/]` |
| `mcp.allowed_origins` | Origines acceptées par le transport MCP HTTP (protection DNS rebinding) ; une requête sans `Origin` passe. | liste d'origines | `[]` | `allowed_origins: [https://app.exemple.fr]` |
| `mcp.allowed_hosts` | Hôtes acceptés en plus de l'adresse d'écoute (derrière un domaine ou un proxy). | liste d'hôtes | `[]` | `allowed_hosts: [loom.exemple.fr]` |

## `triggers`

Portes d'entrée pour un appelant qui ne connaît pas l'API de loom (CRM, planificateur…) : `POST /v1/hooks/<name>`, avec une clé d'API qui donne le client.

| Clé | Rôle et impact | Valeurs | Défaut | Exemple |
|---|---|---|---|---|
| `name` | Dernier segment de la route, et facette au journal ; unique. | `^[a-z0-9][a-z0-9_-]{0,63}$` | **obligatoire** | `name: devis-signe` |
| `agent` | Agent lancé (accepté même non publié en REST). | nom d'agent | **obligatoire** | `agent: relance` |
| `message` | Gabarit du message, rendu sur `{payload, trigger, delivery}` ; une variable absente donne une chaîne vide. | gabarit `{{ }}` | **obligatoire** | `message: "Devis {{ payload.numero }} signé"` |
| `session` | Gabarit de la session, pour rattacher les livraisons d'un même sujet. | gabarit `{{ }}` | `null` : une session par run | `session: "devis-{{ payload.numero }}"` |
| `delivery_header` | En-tête porteur de l'identifiant de livraison, qui devient le `run_id` : une livraison en double retrouve le run. Avec un gabarit `session`, le même identifiant qui donnerait une autre session (charge différente) est refusé (422) : un identifiant de livraison, un run par client. | nom d'en-tête | `null` | `delivery_header: X-Delivery-Id` |

## `agents` (en ligne)

| Clé | Rôle et impact | Valeurs | Défaut | Exemple |
|---|---|---|---|---|
| `agents` | Agents écrits directement dans `loom.yaml`, avec le même schéma qu'un [`agent.yaml`](#fichier-agentyaml) ; ajoutés à ceux d'`agents_dir`. Noms uniques ; `_compaction` réservé. Leur `system_file` n'est pas rapporté à `prompts_dir` : y préférer `system`. | liste d'agents | `[]` | `agents: [{name: echo, main: {model: SIMULE}}]` |

## `base_dir`

| Clé | Rôle et impact | Valeurs | Défaut | Exemple |
|---|---|---|---|---|
| `base_dir` | Dossier de `loom.yaml`, **posé par le chargeur** (une valeur écrite est remplacée). Utile seulement à une config construite en Python. | chemin | posé au chargement | — |

---

# Fichier `agent.yaml`

Un fichier par agent dans `agents_dir` (ou un élément de `agents:` dans `loom.yaml`). Exemple complet : celui d'agent-01.

```yaml
name: agent
description: Agent personnel.
max_iterations: 5
main:
  model: HAIKU
  system_file: system.md
tools:
  - python: loom.fetch_page
```

## `name`

| Clé | Rôle et impact | Valeurs | Défaut | Exemple |
|---|---|---|---|---|
| `name` | Nom de l'agent : `loom run <name>`, route `/v1/agents/<name>/runs`, outil MCP du même nom ; unique. | `^[A-Za-z0-9_-]{1,64}$`, pas `_compaction` | **obligatoire** | `name: agent` |

## `description`

| Clé | Rôle et impact | Valeurs | Défaut | Exemple |
|---|---|---|---|---|
| `description` | Description de l'agent : liste des agents, outil MCP, et description de l'outil quand il sert de sous-agent (obligatoire dans ce cas, ici ou dans la référence). | texte | `""` | `description: Agent personnel.` |

## `expose`

| Clé | Rôle et impact | Valeurs | Défaut | Exemple |
|---|---|---|---|---|
| `expose.rest` | Publie l'agent par l'API REST. | booléen | `true` | `expose: {rest: false}` |
| `expose.mcp` | Publie l'agent comme outil du serveur MCP. | booléen | `true` | `expose: {mcp: false}` |

## `main`

L'orchestrateur, qui mène le run.

| Clé | Rôle et impact | Valeurs | Défaut | Exemple |
|---|---|---|---|---|
| `model` | Modèle de l'orchestrateur (`id` de `models`) ; doit savoir appeler des outils si l'agent en a. | id de modèle | **obligatoire** | `model: HAIKU` |
| `fallbacks` | Modèles de secours, dans l'ordre, si le modèle est en panne ou écarté par son disjoncteur ; sans doublon. | liste d'ids | `[]` | `fallbacks: [SONNET]` |
| `system` | Prompt système en ligne ; peut citer des `{{ variables }}` (définies par `tenants[].variables`). Exclusif de `system_file`. | texte | `""` | `system: Tu réponds en français.` |
| `system_file` | Prompt système dans un fichier, relatif à `prompts_dir`, relu à chaque montage. | chemin | `null` | `system_file: system.md` |
| `llm` | Réglages d'appel propres : voir [réglages `llm`](#bloc-réglages-llm). | objet | `{}` | `llm: {max_tokens: 2048}` |
| `context` | Ce que l'orchestrateur reçoit en fin de requête en plus de ses messages : le state de session, entier ou certaines clés. Seul `session_state` est accepté. | `[session_state]` ou `[{session_state: [clés]}]` | `[]` : requête inchangée | `context: [session_state]` |

## `max_iterations`

| Clé | Rôle et impact | Valeurs | Défaut | Exemple |
|---|---|---|---|---|
| `max_iterations` | Tours de boucle au plus ; atteint, une réponse forcée sans outils clôt le run. | entier > 0 | `10` | `max_iterations: 5` |

## `timeout`

| Clé | Rôle et impact | Valeurs | Défaut | Exemple |
|---|---|---|---|---|
| `timeout` | Délai maximal d'un run (s), en temps de pilotage cumulé (l'attente en file, une pause, le temps avant une reprise ne comptent pas). Dépassé : `run.failed` (`error_type: timeout`) ; c'est une clôture, `loom resume` ne le relance pas, même avec un délai relevé. | nombre > 0 | `null` : pas de délai | `timeout: 300` |

## `approval`

| Clé | Rôle et impact | Valeurs | Défaut | Exemple |
|---|---|---|---|---|
| `approval.expires_in` | Délai laissé à l'approbateur (s) ; `null` : attente sans fin. | nombre > 0, `null` | `86400` (24 h) | `approval: {expires_in: 3600}` |
| `approval.on_expiry` | Sort d'une demande périmée : refus de l'appel (`deny`) ou échec du run (`fail`). | `deny`, `fail` | `deny` | |
| `approval.scope` | Portée exigée de la clé d'API qui approuve, côté REST. | nom de portée | `approve` | |

Un outil demande l'approbation par `approval: always` (dans `tools` ou dans les surcharges), ou par une politique qui rend `Pause`. Une approbation exige un journal durable (`jsonl`, `sqlite`, `postgres`) ; en profil `dev`, un simple avertissement.

## `output`

Contrat de la réponse finale, réparée par l'orchestrateur. Voir [contrat de sortie](#bloc-contrat-de-sortie-output).

| Clé | Rôle et impact | Valeurs | Défaut | Exemple |
|---|---|---|---|---|
| `output` | Réponse finale contrôlée (schéma JSON, motifs, longueur, non vide). | objet, `null` | `null` : non contrôlée | `output: {max_chars: 800}` |

## `judge`

Juge de la réponse finale, après son contrat. Le même bloc sert dans `roles[].judge`.

| Clé | Rôle et impact | Valeurs | Défaut | Exemple |
|---|---|---|---|---|
| `model` | Modèle du juge ; doit savoir appeler des outils (verdict par outil imposé) ; refusé avec le raisonnement étendu Anthropic. Même modèle que la sortie jugée : avertissement « juge corrélé », erreur en `prod`. | id de modèle | **obligatoire** | `model: SONNET` |
| `fallbacks` | Secours du juge. | liste d'ids | `[]` | |
| `name` | Nom au journal (`judge:<nom>`) ; unique dans l'agent. | `^[A-Za-z0-9_-]{1,64}$` | `output` (réponse finale), nom du rôle (rôle) | `name: fidelite` |
| `criteria` | Critères notés de 0 à 1. | liste non vide | **obligatoire** | voir ci-dessous |
| `criteria[].name` | Nom du critère ; unique. | `^[A-Za-z0-9_-]{1,64}$` | **obligatoire** | `name: exact` |
| `criteria[].rule` | Règle donnée au juge telle quelle. | texte | **obligatoire** | `rule: "Les montants viennent du devis."` |
| `criteria[].min_score` | Seuil de réussite. | 0 à 1 | `0.8` | `min_score: 0.9` |
| `criteria[].blocking` | Sous son seuil, fait refuser la sortie (réparation puis `on_failure`) ; sinon seulement signalé. | booléen | `true` | |
| `context` | Ce que voit le juge en plus de la sortie : la demande, le contexte appelant, les pièces jointes, le résumé de session, des résultats d'outils, les derniers tours. `session_state` y est refusé. | liste de `user_input`, `caller_context`, `attachments`, `session_summary`, `{tool_results: [outils], scope: run\|session}`, `{last_turns: N}` | `[]` | `context: [user_input, {tool_results: [chercher_devis]}]` |
| `when.sample` | Part des runs jugés (tirage déterministe par run et par juge). | 0 à 1 | `1.0` | `when: {sample: 0.2}` |
| `when.condition` | Prédicat Python `(JudgeInput) -> bool`. | `module:attr` | `null` | `condition: juges:montant_eleve` |
| `when.tenants` | Clients dont les runs sont jugés. | liste non vide | `null` : tous | |
| `when.profiles` | Profils où le juge travaille ; ailleurs, sauté et noté au journal. | liste non vide | `null` : tous | `profiles: [prod]` |
| `repair.max_attempts` / `repair.tools` | Réparations demandées à l'auteur ; voir [contrat de sortie](#bloc-contrat-de-sortie-output). | | `1` / `auto` | |
| `on_failure` / `fallback_message` | Après les réparations : voir [contrat de sortie](#bloc-contrat-de-sortie-output). | `fail`, `unverified`, `fallback` | `fail` | |
| `llm` | Réglages d'appel du juge : voir [réglages `llm`](#bloc-réglages-llm). | objet | `{}` | |
| `timeout` | Délai du juge (s). | nombre > 0 | `null` : ceux du modèle | |
| `on_error` | Erreur du juge (modèle, verdict) : `block` fait échouer le run, `allow` laisse passer. | `block`, `allow` | `block` | |

À l'exécution, `--judges auto|force|skip` (CLI, REST) décide si les juges suivent leur `when`, tournent tous ou sont sautés (`skip` refusé en `prod`).

## `budget`

| Clé | Rôle et impact | Valeurs | Défaut | Exemple |
|---|---|---|---|---|
| `budget` | Budgets de l'agent, sur ceux de la racine, clé par clé ; même bloc que [`budgets`](#budgets) (`run`, `session`, `tenant`, `on_exceed`). | objet, `null` | `null` : ceux de la racine | `budget: {run: {max_cost: 0.2}}` |

## `stream_output`

| Clé | Rôle et impact | Valeurs | Défaut | Exemple |
|---|---|---|---|---|
| `stream_output` | Diffusion de la réponse finale : au fil de l'eau (`live`) ou après ses contrôles (`after_guards`, en un bloc). | `live`, `after_guards` | `after_guards` si la réponse est contrôlée (contrat, juge, politique `on_output`, rôle terminal sous contrat ou jugé), sinon `live` | `stream_output: live` |

## `tools`

Liste d'outils, sous trois formes, dans l'ordre de déclaration.

**Outil Python** — `{python: …}`

| Clé | Rôle et impact | Valeurs | Défaut | Exemple |
|---|---|---|---|---|
| `python` | Outil : nom enregistré (module de `imports`), chemin `module:attr`, ou outil fourni `loom.<outil>` (vu par le modèle sans le préfixe). Unique dans l'agent. | texte | **obligatoire** | `python: loom.fetch_page` |
| `timeout` | Délai de l'outil (s). | nombre > 0 | celui de l'outil, sinon `execution.tools.timeout` | `timeout: 60` |
| `side_effects` | Nature de ses effets ; un outil sans effet ou idempotent est relancé sans risque à la reprise. | `none`, `reversible`, `irreversible` | celui de l'outil, sinon `none` | `side_effects: irreversible` |
| `approval` | Approbation : jamais, toujours (pause du run), ou selon une politique. | `never`, `always`, `policy` | celui de l'outil, sinon `never` | `approval: always` |
| `idempotent` | L'outil reçoit une clé d'idempotence ; un appel déjà fait n'est pas refait à la reprise. | booléen | celui de l'outil, sinon `false` | `idempotent: true` |
| `offload_over` | Seuil de déport du résultat (caractères). | entier > 0 | celui de l'outil, sinon `execution.tools.offload_over` | `offload_over: 20000` |
| `output` | Contrat du résultat ; un résultat non conforme revient à l'orchestrateur en erreur (pas de réparation). Voir [contrat de sortie](#bloc-contrat-de-sortie-output). | objet | `null` | |

**Serveur MCP** — `{mcp: …}` · **Source d'outils** — `{source: …}`

| Clé | Rôle et impact | Valeurs | Défaut | Exemple |
|---|---|---|---|---|
| `mcp` / `source` | Serveur de `mcp_servers` ou source de `tool_sources` dont les outils sont exposés. | nom déclaré | **obligatoire** | `mcp: crm` |
| `alias` | Préfixe plus court que le nom (`alias__outil`) ; préfixes uniques dans l'agent. | comme un nom MCP, 40 caractères | `null` : le nom | `alias: c` |
| `include` | Seuls ces outils (noms courts) sont exposés ; exclusif d'`exclude`. | liste | `null` : tous | `include: [rechercher, lire]` |
| `exclude` | Ces outils sont retirés. | liste | `null` | `exclude: [supprimer]` |
| `required` | Sans ce serveur ou cette source, le run échoue au lieu de continuer sans ses outils. | booléen | `false` | `required: true` |
| `tools` | Surcharges par outil pour cet agent, sur celles du serveur ou de la source : voir [surcharges d'outil](#bloc-surcharges-doutil-tools). | `{outil: {…}}` | `{}` | |

```yaml
tools:
  - python: loom.fetch_page
  - python: outils:chercher_devis
    timeout: 10
  - mcp: crm
    include: [rechercher, creer_devis]
    tools: {creer_devis: {approval: always, side_effects: irreversible}}
```

## `roles`

Rôles LLM délégués, appelés par l'orchestrateur comme des outils ; ils ne reçoivent que leurs arguments et le contexte déclaré.

| Clé | Rôle et impact | Valeurs | Défaut | Exemple |
|---|---|---|---|---|
| `name` | Nom de l'outil vu par l'orchestrateur ; unique ; `main` réservé. | `^[A-Za-z0-9_-]{1,64}$` | **obligatoire** | `name: rediger` |
| `description` | Ce que l'orchestrateur lit du rôle. | texte non vide | **obligatoire** | `description: Rédige la relance.` |
| `model` / `fallbacks` / `system` / `system_file` / `llm` | Comme dans [`main`](#main) ; un rôle vision exige un modèle `vision`. | | `model` **obligatoire** | `model: HAIKU` |
| `input_schema` | Schéma JSON des arguments fournis par l'orchestrateur ; `type: object` exigé. Un rôle sans argument ni contexte est refusé. | JSON Schema | `{type: object, properties: {}}` | voir ci-dessous |
| `input_template` | Construction du message du rôle : `{{ args.<arg> }}`, `{{ context.<élément> }}` ; tout contexte déclaré doit y être cité (sauf `attachments`). | gabarit `{{ }}` | `null` : blocs de contexte puis arguments | `input_template: "Client : {{ args.client }}"` |
| `context` | Contexte ajouté aux arguments. `attachments` en fait un rôle vision (masqué sans pièce jointe). | liste de `user_input`, `caller_context`, `attachments`, `session_summary`, `session_state`, `{tool_results: [outils], scope: run\|session}`, `{last_turns: N}`, `{session_state: [clés]}` | `[]` | `context: [{tool_results: [chercher_devis]}]` |
| `terminal` | La sortie du rôle devient la réponse finale, telle quelle. | booléen | `false` | `terminal: true` |
| `timeout` | Délai de l'appel (s). | nombre > 0 | `null` : ceux du modèle | |
| `output` | Contrat de la sortie, réparé par le modèle du rôle : voir [contrat de sortie](#bloc-contrat-de-sortie-output). | objet | `null` | |
| `judge` | Juge de la sortie : même bloc que [`judge`](#judge). | objet | `null` | |

```yaml
roles:
  - name: rediger
    description: Rédige le texte de la relance d'un devis.
    model: HAIKU
    system_file: rediger.md
    input_schema:
      type: object
      properties: {consignes: {type: string}}
      required: [consignes]
    context: [{tool_results: [chercher_devis]}]
```

## `subagents`

| Clé | Rôle et impact | Valeurs | Défaut | Exemple |
|---|---|---|---|---|
| `agent` | Agent de la config appelé comme un outil (un seul argument, `message`), dans un run enfant avec ses propres outils et sa boucle. | nom d'agent déclaré | **obligatoire** | `agent: recherche` |
| `name` | Nom de l'outil vu par l'orchestrateur ; unique. | `^[A-Za-z0-9_-]{1,64}$` | le nom de l'agent | `name: chercher_web` |
| `description` | Description de l'outil. | texte | celle de l'agent (l'une des deux est exigée) | |
| `budget_share` | Part de ce qui reste au budget du run parent au moment de l'appel ; exige un budget de run. | ]0, 1] | `null` : budget propre de l'agent | `budget_share: 0.3` |

## `policies`

| Clé | Rôle et impact | Valeurs | Défaut | Exemple |
|---|---|---|---|---|
| `hook` | Politique : nom enregistré (module de `imports`), `module:attr`, ou politique fournie (`loom.require_tool`, qui impose un appel d'outil tant que le run n'en a fait aucun). | texte | **obligatoire** | `hook: loom.require_tool` |
| `name` | Nom au journal ; préfixe `loom.` réservé. | `^[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}$` | celui de la politique | `name: garde_montant` |
| `points` | Points où elle est branchée, parmi ceux qu'elle déclare ; l'ordre de la liste `policies` est l'ordre d'exécution à chaque point. | liste de `before_model`, `after_model`, `before_tool`, `after_tool`, `on_output` | `null` : tous ceux qu'elle déclare | `points: [before_tool]` |
| `params` | Paramètres remis à la politique. | objet | `{}` | `params: {plafond: 5000}` |
| `timeout` | Délai de la politique (s) ; `null` le retire. | nombre > 0, `null` | `5` | |
| `on_error` | Erreur de la politique : `block` fait échouer le run, `allow` l'ignore. | `block`, `allow` | `block` | |
| `max_attempts` | Réparations (`Retry`) qu'elle peut demander dans un run. | entier > 0 | `1` | |

## `max_depth`

| Clé | Rôle et impact | Valeurs | Défaut | Exemple |
|---|---|---|---|---|
| `max_depth` | Profondeur à laquelle un run de cet agent peut encore appeler ses sous-agents : à 1, un run racine les appelle, un sous-run de cet agent non. | entier > 0 | `1` | `max_depth: 2` |

## `state`

Clés du state de session : des valeurs typées que la session garde d'un run à l'autre, que la compaction ne touche pas. Posées par l'application (`loom run --state CLE=VALEUR`, `Loom.run(state=…)`) ou par le résultat d'un outil ; relues par `loom state <session>`.

| Clé | Rôle et impact | Valeurs | Défaut | Exemple |
|---|---|---|---|---|
| `<clé>` | Nom de la clé, cité tel quel par les gabarits. Une clé non déclarée est refusée. | `^[A-Za-z_][A-Za-z0-9_]{0,63}$` | — | `devis_courant:` |
| `<clé>.schema` | JSON Schema de la valeur ; une écriture qui ne le tient pas est refusée. | JSON Schema | **obligatoire** | `schema: {type: string}` |
| `<clé>.description` | Ce que la clé désigne, donné avec sa valeur à qui la reçoit. | texte | `""` | `description: Numéro du devis en cours` |
| `<clé>.required` | Run refusé à l'ouverture tant que la clé n'a pas de valeur (CLI : code 2 ; REST : 422). Exclusif de `default`. | booléen | `false` | `required: true` |
| `<clé>.default` | Valeur lue quand la clé est absente, jamais écrite ; doit tenir le schéma et les limites. | valeur JSON | `null` : pas de valeur par défaut | `default: cordial` |
| `<clé>.from` | Outils dont un résultat réussi pose la clé ; un outil cité une fois par clé. | liste | `[]` : seule l'application l'écrit | voir ci-dessous |
| `<clé>.from[].tool` | Outil de l'agent (Python, rôle, sous-agent, ou outil préfixé MCP/source). | nom d'outil | **obligatoire** | `tool: chercher_devis` |
| `<clé>.from[].path` | Pointeur JSON (RFC 6901) dans les données du résultat ; `""` prend tout. Exclusif de `value`. | pointeur JSON | l'un de `path` / `value` est **obligatoire** | `path: /numero` |
| `<clé>.from[].value` | Valeur fixe posée à chaque résultat réussi ; `null` efface la clé. | valeur JSON | | `value: en_cours` |
| `<clé>.from[].on_missing` | `path` absent du résultat : écriture refusée et dite au modèle (`refuse`), ou rien et en silence (`ignore`) ; seulement avec `path`. | `refuse`, `ignore` | `refuse` | `on_missing: ignore` |

```yaml
state:
  signature:
    schema: {type: string, maxLength: 80}
    description: Signature des messages
    required: true
  ton:
    schema: {enum: [cordial, ferme]}
    default: cordial
  devis_courant:
    schema: {type: string}
    from: [{tool: chercher_devis, path: /numero}]
main:
  model: HAIKU
  system_file: relance.md
  context: [session_state]
```

---

# Blocs partagés

## Bloc contrat de sortie (`output`)

Sert dans `output` (agent), `roles[].output`, `tools[].output` et les surcharges d'outil. Une sortie est toujours exigée non vide.

| Clé | Rôle et impact | Valeurs | Défaut | Exemple |
|---|---|---|---|---|
| `schema` | La sortie doit être un JSON conforme à ce schéma. Exclusif de `schema_file`. | JSON Schema | `null` | `schema: {type: object, required: [texte]}` |
| `schema_file` | Schéma lu dans un fichier JSON ou YAML, relatif au dossier de `loom.yaml`. | chemin | `null` | `schema_file: schemas/relance.json` |
| `must_match` | Motif qui doit se trouver dans la sortie (moteur `regex`, avec délai). | expression régulière | `null` | `must_match: "D-\\d{4}-\\d+"` |
| `must_not_match` | Motif interdit. | expression régulière | `null` | `must_not_match: "(?i)lorem"` |
| `max_chars` | Longueur maximale. | entier > 0 | `null` | `max_chars: 800` |
| `normalize` | Normalisation avant contrôle : blocs de code retirés, JSON extrait, espaces nettoyés. | booléen | `true` | |
| `repair.max_attempts` | Réparations demandées à l'auteur (orchestrateur ou modèle du rôle ; un outil ne se répare pas). | entier ≥ 0 | `1` | `repair: {max_attempts: 2}` |
| `repair.tools` | Outils de l'orchestrateur pendant la réparation : retirés pour un échec de forme (`auto`), retirés (`none`), gardés (`allowed`). | `auto`, `none`, `allowed` | `auto` | |
| `on_failure` | Après les réparations : échec (`fail`), sortie gardée marquée non vérifiée (`unverified`), remplacée par `fallback_message` (`fallback`). | `fail`, `unverified`, `fallback` | `fail` | `on_failure: fallback` |
| `fallback_message` | Sortie de remplacement ; obligatoire avec `fallback`. | texte | `null` | `fallback_message: "Réponse indisponible."` |

## Bloc surcharges d'outil (`tools`)

Sert dans `mcp_servers[].tools`, `tool_sources[].tools` et `tools[].tools` (référence MCP ou source d'un agent, qui l'emporte). Chaque clé absente garde la déclaration de l'outil.

| Clé | Rôle et impact | Valeurs | Défaut | Exemple |
|---|---|---|---|---|
| `description` | Ce que le modèle lit de l'outil, à la place de ce que l'outil en dit. | texte non vide | celle de l'outil | `description: Cherche un client par nom.` |
| `side_effects` | Comme pour un outil Python ; pour MCP, les annotations `readOnlyHint` / `destructiveHint` donnent la valeur de départ. | `none`, `reversible`, `irreversible` | celle de l'outil | |
| `approval` | Comme pour un outil Python. | `never`, `always`, `policy` | celle de l'outil | `approval: always` |
| `idempotent` | Comme pour un outil Python ; pour MCP, `idempotentHint` donne la valeur de départ. | booléen | celle de l'outil | |
| `timeout` | Délai de l'outil (s). | nombre > 0 | celui de l'outil | |
| `offload_over` | Seuil de déport (caractères). | entier > 0 | celui de l'outil | |
| `output` | Contrat du résultat : voir [contrat de sortie](#bloc-contrat-de-sortie-output). | objet | `null` | |

## Bloc réglages `llm`

Sert dans `main.llm`, `roles[].llm` et `judge.llm`.

| Clé | Rôle et impact | Valeurs | Défaut | Exemple |
|---|---|---|---|---|
| `max_tokens` | Remplace `max_tokens` du modèle pour ce rôle. | entier > 0 | `null` : celui du modèle | `max_tokens: 1024` |
| `params` | Fusionnés clé par clé sur `params` du modèle (une clé remplace la clé entière du modèle), et cela **pour chaque modèle de la chaîne**, secours compris : n'y mettre que ce que tous les modèles de la chaîne acceptent, sinon la bascule échoue (400). Un réglage propre à un fournisseur va dans `models[].params` ; deux réglages d'un même modèle, dans deux déclarations (deux `id`). | objet | `{}` | — (exemple de bloc : `llm: {max_tokens: 1024}`) |
