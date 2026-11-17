# loom.yaml : la clé mcp_servers

La clé `mcp_servers` de `loom.yaml` et tout ce qu'elle contient.

## `mcp_servers[]`

Un serveur MCP déclaré sous `mcp_servers` dans `loom.yaml`.

Modèle Pydantic : `loom_ia.core.model.mcp_spec.McpServerSpec`.

- `mcp_servers[].name` (type `str` ; obligatoire ; bornes : longueur ≤ 40, motif `^[A-Za-z0-9-]+(_[A-Za-z0-9-]+)*$`) : Nom du serveur, 40 caractères au plus, sans `__` : il préfixe ses outils (`crm__rechercher`).
- `mcp_servers[].transport` (type `McpTransport` ; obligatoire) : `stdio` ou `http`.
- `mcp_servers[].command` (type `str | null` ; défaut : `null`) : stdio : programme lancé (obligatoire en stdio).
- `mcp_servers[].args` (type `tuple[str, ...]` ; défaut : `[]`) : stdio : ses arguments.
- `mcp_servers[].env` (type `dict[str, str]` ; défaut : `{}`) : stdio : variables passées en clair.
- `mcp_servers[].env_from` (type `dict[str, str]` ; défaut : `{}`) : stdio : variables lues dans l'environnement de loom, `{VAR_DU_SERVEUR: VAR_DE_LOOM}` ; un client peut les rediriger vers les siennes.
- `mcp_servers[].cwd` (type `Path | null` ; défaut : `null`) : stdio : dossier de lancement ; par défaut celui du fichier de config.
- `mcp_servers[].url` (type `str | null` ; défaut : `null`) : http : point d'accès (obligatoire en http).
- `mcp_servers[].headers_env` (type `dict[str, str]` ; défaut : `{}`) : http : en-têtes lus dans l'environnement, `{En-tête: VARIABLE}`.
- `mcp_servers[].scope` (type `McpScope` ; défaut : `"shared"`) : `shared` (défaut) : une connexion pour tout le process ; `tenant` : une par client (fonction L1, point 34) ; `run` : une par run.
- `mcp_servers[].connect_timeout` (type `float` ; défaut : `10.0` ; bornes : > 0) : Délai d'établissement de la connexion, initialisation comprise, en secondes. Défaut : 10.
- `mcp_servers[].idle_timeout` (type `float | null` ; défaut : `300.0`) : Portée `shared` : fermeture après ce délai sans utilisation, en secondes ; `None` la garde ouverte. Défaut : 300.
- `mcp_servers[].tools` (type `dict[str, ToolOverrides]` ; défaut : `{}`) : Déclarations par outil (effets, approbation, délai…), qui s'ajoutent à ce que le serveur annonce.
- `mcp_servers[].circuit_breaker` (type `CircuitBreaker | null` ; défaut : les défauts de la section) : Disjoncteur sur les connexions ratées de suite (backlog #011) ; actif par défaut (5 échecs, 60 s), `null` le retire.

## `mcp_servers[].tools.<nom>`

Même forme que `tools[].tools.<nom>` (page « agent.yaml : la clé tools »).

## `mcp_servers[].circuit_breaker`

Même forme que `models[].circuit_breaker` (page « loom.yaml : la clé models »).
