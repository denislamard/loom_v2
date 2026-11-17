# loom.yaml : les clés de premier niveau

La configuration d'une instance : `loom.yaml` et les agents de `agents_dir`.

Modèle Pydantic : `loom_ia.config.models.LoomConfig`.

- `version` (type `int` ; obligatoire) : Version du schéma ; 1.
- `profile` (type `Profile | null` ; défaut : `null`) : Profil actif (fonction M4) : `dev` assouplit, `prod` durcit, absent ne change rien. Posé au chargement, où l'option et la variable d'environnement l'emportent sur le fichier.
- `profiles` (type `dict[Profile, dict[str, JsonValue]]` ; défaut : `{}`) : Surcharges partielles par profil, fusionnées au chargement : les objets en profondeur, les listes remplacées.
- `base_dir` (type `Path | null` ; défaut : `null`) : Dossier du fichier de config, posé au chargement : les modules voisins sont importables et les chemins relatifs s'y rapportent. Hors du schéma publié (`loom schema`) : on ne l'écrit pas (essai de la fonction M1, 21/10).
- `env_file` (type `Path | null` ; défaut : `null`) : Fichier d'environnement du projet, relatif au dossier de la config : lu par `loom` avant la config elle-même (`config/env_file.py`), au lieu du `.env` voisin. À la racine seulement, car il peut donner le profil.
- `imports` (type `tuple[str, ...]` ; défaut : `[]`) : Modules chargés au démarrage, qui enregistrent leurs outils et politiques (point 50).
- `agents_dir` (type `Path` ; défaut : `"agents"`) : Dossier des fichiers d'agents (`agents` par défaut), un `<nom>.yaml` par agent.
- `resources_dir` (type `Path` ; défaut : `"prompts"`) : Dossier des prompts et skills (`prompts` par défaut), base des `system_file` et `skill_file`.
- `models` (type `tuple[ModelSpec, ...]` ; défaut : `[]`) : Modèles, référencés par leur `id`.
- `mcp_servers` (type `tuple[McpServerSpec, ...]` ; défaut : `[]`) : Serveurs MCP, référencés par les agents (point 19).
- `tool_sources` (type `tuple[ToolSourceSpec, ...]` ; défaut : `[]`) : Sources d'outils fournies par des paquets installés (jalon J6.4b).
- `storage` (type `StorageConfig` ; défaut : les défauts de la section) : Stockages de la racine.
- `sessions` (type `SessionsConfig` ; défaut : les défauts de la section) : Snapshots d'historique, compaction, médias et state ; l'agent interne `_compaction` en sort (voir `all_agents`).
- `execution` (type `ExecutionConfig` ; défaut : les défauts de la section) : Exécution des runs et des outils.
- `budgets` (type `Budgets` ; défaut : les défauts de la section) : Budgets par défaut des agents ; un agent les surcharge par son `budget` (fonction J4).
- `telemetry` (type `TelemetryConfig` ; défaut : les défauts de la section) : Logs, capture, masquage, collecteurs.
- `tenants` (type `tuple[TenantSpec, ...]` ; défaut : `[]`) : Clients (fonction L1, point 33) : sans cette liste, seul `default` existe ; avec elle, la liste est fermée et un client inconnu est refusé.
- `security` (type `SecurityConfig` ; défaut : les défauts de la section) : Rôles d'accès, utilisateurs, clés d'API, fichier des refus.
- `server` (type `ServerConfig` ; défaut : les défauts de la section) : Serveurs HTTP et MCP.
- `triggers` (type `tuple[TriggerSpec, ...]` ; défaut : `[]`) : Portes d'entrée déclarées (fonction H6) : ce qu'un appel extérieur ouvre.
- `agents` (type `tuple[AgentSpec, ...]` ; défaut : `[]`) : Les agents : remplis depuis `agents_dir` au chargement, ou donnés directement en Python.
