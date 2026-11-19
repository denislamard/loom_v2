# loom.yaml : les clés de premier niveau

La configuration d'une instance : `loom.yaml` et les agents de `agents_dir`.

Modèle Pydantic : `loom_ia.config.models.LoomConfig`.

- `version` (type `int` ; obligatoire) : Version du schéma ; 1.
- `profile` (type `Profile | null` ; défaut : `null`) : Profil actif (fonction M4) : `dev` assouplit, `prod` durcit, absent ne change rien. Posé au chargement d'après l'amorce, où l'option et la variable d'environnement l'emportent (décision A-2) ; refusé dans le fichier de config. Hors du schéma publié.
- `profiles` (type `dict[Profile, dict[str, JsonValue]]` ; défaut : `{}`) : Surcharges partielles par profil, fusionnées au chargement : les objets en profondeur, les listes remplacées.
- `workpath` (type `Path | null` ; défaut : `null`) : Dossier de travail de l'instance (décisions A-4 et A-5), obligatoire dans la config lue par l'amorce, quelle que soit la source : le journal, les artefacts, l'audit, les données des outils, le `cwd` des serveurs MCP et les modules Python voisins (`imports`, `module:attr`) s'y rapportent. Un chemin relatif se rapporte au dossier de l'amorce ; en conteneur, un chemin absolu monté depuis l'hôte (`/loom-data`). Rendu absolu au chargement. Facultatif pour une config construite en Python : le dossier courant.
- `boot_file` (type `Path | null` ; défaut : `null`) : L'amorce lue (`loom-boot.yaml`), posée au chargement ; `None` pour une config construite en Python. Hors du schéma publié.
- `config_file` (type `Path | null` ; défaut : `null`) : Le fichier de config que l'amorce désigne, posé au chargement : son dossier est la racine des documents (`agents_dir`, `resources_dir`, `schema_file`). Hors du schéma publié.
- `env_file` (type `Path | null` ; défaut : `null`) : Fichier d'environnement du projet, posé au chargement d'après l'amorce (décision A-2) ; refusé dans le fichier de config. Hors du schéma publié.
- `imports` (type `tuple[str, ...]` ; défaut : `[]`) : Modules chargés au démarrage, qui enregistrent leurs outils et politiques (point 50).
- `agents_dir` (type `Path` ; défaut : `"agents"`) : Dossier des fichiers d'agents (`agents` par défaut), un `<nom>.yaml` par agent.
- `resources_dir` (type `Path` ; défaut : `"prompts"`) : Dossier des prompts et skills (`prompts` par défaut), base des `system_file` et `skill_file`.
- `models` (type `tuple[ModelSpec, ...]` ; défaut : `[]`) : Modèles, référencés par leur `id`.
- `mcp_servers` (type `tuple[McpServerSpec, ...]` ; défaut : `[]`) : Serveurs MCP, référencés par les agents (point 19).
- `tool_sources` (type `tuple[ToolSourceSpec, ...]` ; défaut : `[]`) : Sources d'outils fournies par des paquets installés (jalon J6.4b).
- `storage` (type `StorageConfig` ; défaut : les défauts de la section (`events.backend: "memory"`, `events.role: "loom_app"`, `events.pool.min_size: 1`, `events.pool.max_size: 10`, `events.pool.command_timeout: 60.0`, `events.pool.lock_timeout: 10.0`, `events.pool.acquire_timeout: 30.0`, `idempotency.backend: "journal"`, `idempotency.role: "loom_app"`, `idempotency.pool.min_size: 1`, `idempotency.pool.max_size: 10`, `idempotency.pool.command_timeout: 60.0`, `idempotency.pool.lock_timeout: 10.0`, `idempotency.pool.acquire_timeout: 30.0`, `queue.backend: "asyncio"`, `queue.max_running: 16`, `bus.backend: "memory"`)) : Stockages de la racine.
- `sessions` (type `SessionsConfig` ; défaut : les défauts de la section (`snapshot_every: 50`, `media.keep_images: "all"`, `state.max_value_bytes: 1024`, `state.max_total_bytes: 4096`)) : Snapshots d'historique, compaction, médias et state ; l'agent interne `_compaction` en sort (voir `all_agents`).
- `execution` (type `ExecutionConfig` ; défaut : les défauts de la section (`tools.timeout: 30.0`, `tools.validate_arguments: true`, `tools.offload_over: 50000`, `tools.order: "effects"`, `tools.threads: 16`, `tools.grace: 30.0`, `attachments.max_bytes: 5242880`, `attachments.types: ["image/jpeg", "image/png", "image/gif", "image/webp"]`, `attachments.max_files: 10`, `shutdown_timeout: 30.0`, `lease: 60.0`)) : Exécution des runs et des outils.
- `budgets` (type `Budgets` ; défaut : les défauts de la section (`on_exceed: "stop"`)) : Budgets par défaut des agents ; un agent les surcharge par son `budget` (fonction J4).
- `telemetry` (type `TelemetryConfig` ; défaut : les défauts de la section (`logging.level: "INFO"`, `logging.format: "console"`, `capture.exports: "metadata"`, `capture.raw_exchanges: false`, `capture.raw_max_bytes: 262144`, `redaction.patterns: ["secret", "email", "phone", "iban"]`, `exporters: []`)) : Logs, capture, masquage, collecteurs.
- `tenants` (type `tuple[TenantSpec, ...]` ; défaut : `[]`) : Clients (fonction L1, point 33) : sans cette liste, seul `default` existe ; avec elle, la liste est fermée et un client inconnu est refusé.
- `security` (type `SecurityConfig` ; défaut : les défauts de la section (`roles: []`, `users: []`, `api_keys: []`)) : Rôles d'accès, utilisateurs, clés d'API, fichier des refus.
- `server` (type `ServerConfig` ; défaut : les défauts de la section (`http.host: "127.0.0.1"`, `http.port: 8000`, `http.base_path: ""`, `http.max_body_bytes: 134217728`, `mcp.file_roots: []`, `mcp.http: false`, `mcp.allowed_origins: []`, `mcp.allowed_hosts: []`)) : Serveurs HTTP et MCP.
- `triggers` (type `tuple[TriggerSpec, ...]` ; défaut : `[]`) : Portes d'entrée déclarées (fonction H6) : ce qu'un appel extérieur ouvre.
- `agents` (type `tuple[AgentSpec, ...]` ; défaut : `[]`) : Les agents : remplis depuis `agents_dir` au chargement, ou donnés directement en Python.
