# Questions de référence pour le RAG de loom_v2

Ce jeu sert à mesurer le RAG avant et après le chantier de documentation. Pour chaque question, la réponse attendue liste les **faits clés** qu'une bonne réponse doit contenir. Une réponse est juste si elle les donne tous, sans erreur. Les sources indiquent où vérifier.

Les réponses ont été établies le 17/11/2026 sur loom_v2 2.0.22 (branche `main`, commit `817e789`). Si le code change, il faut les revoir.

Pour `loom memory eval`, ce jeu deviendra un fichier JSONL (`query`, `doc_id`) une fois les pages générées et indexées : chaque question y sera reliée au document qui doit la trouver.

Catégories :

- **U** : usage et configuration ;
- **O** : outils fournis ;
- **C** : comportement du moteur ;
- **A** : accès (CLI, REST, MCP, Python) ;
- **P** : pourquoi, c'est-à-dire les choix de conception.

## U. Usage et configuration

**U01. Quelles sont les clés de premier niveau de `loom.yaml` ?**
Réponse : `version` et `workpath` (obligatoires), `profiles`, `imports`, `agents_dir` (défaut `agents`), `resources_dir` (défaut `prompts`), `models`, `mcp_servers`, `tool_sources`, `storage`, `sessions`, `execution`, `budgets`, `telemetry`, `tenants`, `security`, `server`, `triggers`, `agents`.
`profile` et `env_file` n'y sont pas : ils se déclarent dans l'amorce `loom-boot.yaml` (voir U14), et sont refusés dans `loom.yaml`.
Sources : `config/models.py` (`LoomConfig`).

**U02. Quelles clés peut contenir la définition d'un agent ?**
Réponse : `name` et `main` (obligatoires), `description`, `expose` (REST et MCP, vrais par défaut), `max_iterations` (10 par défaut), `timeout`, `approval`, `access`, `output`, `judge`, `budget`, `stream_output`, `tools`, `roles`, `subagents`, `policies`, `max_depth` (1 par défaut), `state`, `execution`.
Sources : `agents/spec.py` (`AgentSpec`).

**U03. Comment loom trouve-t-il le fichier `.env` ?**
Réponse : du plus fort au plus faible : `--env-file`, puis `env_file:` de l'amorce `loom-boot.yaml` (relatif à son dossier), puis `.env` du dossier de l'amorce. Un fichier demandé et absent est une erreur ; le `.env` par défaut absent n'en est pas une. Jamais le dossier courant ni un parent. Une variable déjà définie, même vide, l'emporte. Pas d'interpolation. `--no-env-file` n'en lit aucun. La librairie ne lit rien d'elle-même : il faut appeler `load_env_file("loom-boot.yaml")`.
Sources : `config/env_file.py`.

**U04. Quel délai a un outil par défaut, et comment le changer ?**
Réponse : 30 secondes (`execution.tools.timeout` dans `loom.yaml`). `null` retire la limite. Un outil peut avoir son propre `timeout`.
Sources : `config/models.py` (`ToolsExecution`), `core/model/tooling.py` (`ToolSpec.timeout`).

**U05. À partir de quelle taille un résultat d'outil est-il déporté hors du journal ?**
Réponse : au-delà de 50 000 caractères (`execution.tools.offload_over`), le résultat va dans le stockage d'artefacts et le journal garde une référence. `null` désactive le déport. Un outil peut avoir son propre `offload_over`.
Sources : `config/models.py`, `engine/offload.py`.

**U06. Quels stockages loom utilise-t-il par défaut ?**
Réponse : journal en mémoire (`storage.events.backend: memory`), idempotence dans le journal (`journal`), file asyncio (`queue: asyncio`, 16 tâches au plus), bus en mémoire, pas de stockage d'artefacts déclaré, ni chiffrement, ni rétention.
Sources : `config/models.py` (`StorageConfig`).

**U07. Quelles pièces jointes un run accepte-t-il par défaut ?**
Réponse : images JPEG, PNG, GIF et WebP, reconnues à leur signature binaire ; 5 Mio par fichier, 10 fichiers au plus (`execution.attachments`).
Sources : `config/models.py` (`AttachmentPolicy`).

**U08. Quelles sont les limites du state de session ?**
Réponse : 1 Kio par valeur et 4 Kio pour tout le state, en JSON compact (octets UTF-8), réglables par `sessions.state` (`max_value_bytes`, `max_total_bytes`).
Sources : `config/models.py` (`StateLimits`), décision D7 du state de session.

**U09. Comment régler la compaction de l'historique d'une session ?**
Réponse : `sessions.compaction` : `model` (obligatoire), `over_tokens` (12 000), `hard_tokens` (150 000), `keep_last` (6 derniers tours gardés), `fidelity_check` (vrai), `blocking` (faux : en tâche de fond après le run ; vrai : bloquante à l'ouverture du run suivant), `system_file`. Sans `compaction`, il n'y en a pas.
Sources : `config/models.py` (`CompactionConfig`), `config/compaction.py`.

**U10. Quelles limites de budget peut-on poser ?**
Réponse : par run (`max_cost`, `max_tokens`, `max_calls`), par session (`max_cost`, `max_tokens`), par client et par période (`max_cost_per_day`, `max_tokens_per_day`, `max_cost_per_month`, `max_tokens_per_month`). `on_exceed` vaut `stop` par défaut. Une limite atteinte écrit `budget.exceeded`, puis un `policy.decided` qui arrête le run.
Sources : `core/model/budget.py`, `core/events/payloads.py`.

**U11. Quelles permissions peut porter un rôle d'accès ?**
Réponse : `run`, `answer`, `cancel`, `approve`, `self_approve`, `amend`, `read`, `read_content`, `admin`, `act_as`. `self_approve` exige `approve` dans le même rôle. Un rôle peut être borné à des agents.
Sources : `config/models.py` (`PERMISSIONS`, `AccessRole`), `docs/loom_role_user.md`.

**U12. Sur quelle adresse et quel port `loom serve` écoute-t-il par défaut ?**
Réponse : `127.0.0.1:8000` (`server.http.host` et `port`). Corps de requête limité à 128 Mio (`max_body_bytes`). `server.http.user`, qui fait agir un serveur local sans clé au nom d'un utilisateur, n'est permis que sur la boucle locale.
Sources : `config/models.py` (`HttpServer`, `LOOPBACK_HOSTS`).

**U13. Comment déclarer un modèle de secours ?**
Réponse : sur l'emplacement (`main`, un rôle, un juge) : `model: <id>` et `fallbacks: [<id>, …]`, dans l'ordre.
Sources : `engine/fallback.py`.

**U14. Qu'est-ce que l'amorce `loom-boot.yaml`, et que contient-elle ?**
Réponse : le premier fichier que loom lit, toujours local ; `--config` et `Loom.from_config()` la désignent (`./loom-boot.yaml` par défaut). Elle porte `version` (1), `profile` (`dev` ou `prod` ; `--profile` et `LOOM_PROFILE` l'emportent), `env_file` (relatif à son dossier) et le bloc `config`, obligatoire, qui dit où lire la config : `source: file` (seule source disponible ; une autre est refusée), `path` (dossier de `loom.yaml`, relatif à l'amorce, `.` par défaut) et `file` (nom du fichier, `loom.yaml` par défaut). Toute autre clé y est refusée. Un projet sans amorce est refusé, et le message donne l'amorce à écrire.
Sources : `config/models.py` (`BootConfig`, `FileConfigSource`), `config/loader.py` (`read_boot`).

**U15. Qu'est-ce que `workpath`, et à quoi se rapportent les chemins relatifs ?**
Réponse : le dossier de travail de l'instance, obligatoire dans `loom.yaml`, relatif au dossier de l'amorce (absolu en conteneur : `workpath: /loom-data`). S'y rapportent le journal, les artefacts, l'idempotence, `security.audit.path`, le stockage des clients, les données des outils fournis, le `cwd` des serveurs MCP, `server.mcp.file_roots` et les modules Python voisins (`imports: [tools.Wireframe]` se lit `<workpath>/tools/Wireframe`). Les documents de la config, eux, se rapportent au dossier de `loom.yaml` : `agents_dir`, `resources_dir`, `schema_file`.
Sources : `config/loader.py` (`load_config`, `_workpath`), `config/models.py` (`LoomConfig.workpath`).

## O. Outils fournis

**O01. Comment active-t-on un outil fourni par loom ?**
Réponse : dans les `tools` de l'agent : `- python: loom.<outil>`. Aucun n'est actif d'office. Les paramètres de l'outil se donnent sur la même entrée. Les outils fournis sont `loom.current_datetime`, `loom.fetch_page`, `loom.local_files`, `loom.excel`, `loom.word`, `loom.pdf`, `loom.sql`, `loom.memory`, `loom.state` et `loom.ask`.
Sources : `tools_builtin/__init__.py` (`BUILTIN_TOOLS`).

**O02. Quels sont les paramètres de `loom.sql` ?**
Réponse : `databases` (au moins une) et `timezone` (défaut `Europe/Paris`). Pour chaque base : `name`, `postgres` (`dsn_env`, `schema`, défaut `public`) ou `sqlite` (`path`, fichier existant), `tables` (droits `select`, `insert`, `update`, `delete`), `describe`, `functions`, `statement_timeout` (10 s, 300 au plus), `max_rows` (200, 1 000 au plus), `max_affected_rows` (100, 100 000 au plus), `allow_full_table` (faux).
Sources : `tools_builtin/sql_params.py`.

**O03. Quelles approbations `loom.sql` demande-t-il par défaut ?**
Réponse : `select` et `insert` : jamais ; `update` et `delete` : toujours. On les change par table, en objet : `{rights: […], approval: {update: never}}`. Les réglages communs `approval`, `side_effects` et `idempotent` sont refusés sur la référence de l'outil.
Sources : `tools_builtin/sql_params.py`, `tools_builtin/__init__.py`.

**O04. Pourquoi ma config `loom.sql` est-elle refusée avec `update` sans `select` ?**
Réponse : `update` et `delete` exigent `select`, car la base lit les colonnes du `WHERE`.
Sources : `tools_builtin/sql_params.py` (`DatabaseSpec._check`).

**O05. Comment `loom.sql` reçoit-il le mot de passe d'une base Postgres ?**
Réponse : jamais par la config : `dsn_env` nomme la variable d'environnement qui porte le DSN. Un client peut rediriger ce nom vers sa propre variable (`secrets`).
Sources : `tools_builtin/sql_params.py` (`secret_names`).

**O06. Quels droits gère `loom.local_files`, et avec quelles approbations par défaut ?**
Réponse : `read`, `write`, `overwrite`, `delete`, accordés dossier par dossier (`folders[].rights`) ; un droit absent est refusé. Approbation par défaut : `read` et `write` jamais ; `overwrite` et `delete` toujours. On la change par dossier (`folders[].approval`).
Sources : `tools_builtin/local_files_params.py`.

**O07. Quels sont les paramètres de `loom.excel` ?**
Réponse : `folders` (obligatoire), `timezone` (`Europe/Paris`), `recalc` (faux : recalcul par LibreOffice), `soffice` (chemin de LibreOffice), `recalc_timeout` (60 s), `charts` (faux : graphiques).
Sources : `tools_builtin/excel_params.py`.

**O08. Quels sont les paramètres de `loom.word` ?**
Réponse : `folders` (obligatoire), `timezone` (`Europe/Paris`), `blocks` (blocs facultatifs activés : `chart`, `image`, `signature`, `toc`, `checkboxes`, `fields`, `code`, `invoice_totals`, `attendance` ; chacun coûte des tokens à chaque appel, il n'est dans le schéma que s'il est nommé), `templates` (modèles de document), `style` (fichier de style).
Sources : `tools_builtin/word_params.py`, `tools_builtin/_office_params.py`.

**O09. Quelles actions propose `loom.memory`, et lesquelles demandent une approbation ?**
Réponse : lectures `search`, `get`, `list_docs`, `projects` (jamais d'approbation par défaut) ; écritures `add_text`, `add_url`, `add_file`, `update`, `delete` (approbation par défaut). Les actions permises se déclarent sous `actions`, en liste ou en objet par action.
Sources : `tools_builtin/memory_params.py`.

**O10. Quels modèles `loom.memory` utilise-t-il ?**
Réponse : BGE-M3 (`BAAI/bge-m3`) pour les vecteurs et `BAAI/bge-reranker-v2-m3` pour le reclassement (`models.kind: bge-m3`), sur `device: auto`, en fp16. `models.server` les fait calculer par le service `loom-mem-gpu`.
Sources : `tools_builtin/memory_params.py` (`ModelsSpec`), `docs/loom-mem-gpu.md`.

**O11. Que fait `loom.ask`, et que se passe-t-il sans réponse ?**
Réponse : le modèle pose une question à l'utilisateur et le run attend la réponse (`question.asked`, puis `question.answered`). Par défaut, il attend indéfiniment. Avec `expires_in` (en secondes), la question expire (`question.expired`) et le modèle l'apprend. L'outil refuse `approval`, `side_effects`, `idempotent`, `timeout` et `output`.
Sources : `tools_builtin/ask_params.py`, `tools_builtin/__init__.py`.

**O12. Que renvoie `loom.fetch_page`, et quelles sont ses limites ?**
Réponse : l'adresse finale, le titre, le texte sans HTML, puis la liste des liens de la page. Pas de JavaScript, de connexion à un compte, de PDF ni d'image. Outil sans effet, délai de 45 s, résultat déporté au-delà de 20 000 caractères.
Sources : `tools_builtin/fetch_page.py`.

## C. Comportement du moteur

**C01. Quand la chaîne de secours passe-t-elle au modèle suivant ?**
Réponse : `transient` et `overloaded` : une fois les nouvelles tentatives épuisées ; `quota_exhausted` : aussitôt ; `context_overflow` : vers le premier secours dont la fenêtre déclarée est plus grande ; `auth`, `invalid_request`, `content_filtered` : jamais, l'appel échoue. Un modèle dont le disjoncteur est ouvert est sauté. Chaque bascule écrit `model.fell_back`.
Sources : `engine/fallback.py`.

**C02. Un run qui a basculé sur un secours y reste-t-il ?**
Réponse : oui (adhérence) : l'emplacement garde le secours pour la suite du run.
Sources : `engine/fallback.py` (docstring du module, `ModelChain.position`).

**C03. Dans quel ordre s'exécutent les appels d'outils d'un même tour ?**
Réponse : en parallèle, mais avec `execution.tools.order: effects` (défaut depuis 2.0.20), un appel à effet attend ceux qui le précèdent, et ceux qui le suivent l'attendent. Avec `free`, tous partent ensemble.
Sources : `config/models.py` (`ToolsExecution.order`), fonction A3.

**C04. Que se passe-t-il quand un outil lève `ToolError` ?**
Réponse : l'exécuteur la rattrape ; l'appel se termine en erreur et le message est rendu au modèle pour qu'il corrige son appel. Le run continue.
Sources : `core/ports/tool.py`, `engine/executor.py`.

**C05. Quelles sont les valeurs possibles d'`approval` sur un outil ?**
Réponse : `never` (défaut), `always`, `policy`. Une politique `before_tool` peut exiger une approbation sur tout outil, mais ne peut la lever que si l'outil n'est pas en `always`.
Sources : `core/model/tooling.py`, `docs/fonctions.md` point 17.

**C06. Que devient une demande d'approbation sans réponse ?**
Réponse : elle expire après 86 400 s (24 h) par défaut (`approval.expires_in` de l'agent), et `on_expiry: deny` la traite comme un refus ; `fail` fait échouer le run. Un `approval.expired` est écrit.
Sources : `core/model/tooling.py` (`ApprovalSettings`), `core/events/payloads.py`.

**C07. Que fait loom d'un appel à effet repris après un plantage, dont on ne sait pas s'il a eu lieu ?**
Réponse : l'outil le déclare par `on_unknown` : `error` (défaut) rend une erreur au modèle ; `pause` suspend le run pour qu'un humain vérifie avant de refaire. C'est l'exception `UnknownEffect`.
Sources : `core/model/tooling.py`, `core/ports/tool.py`.

**C08. Quels types d'événements le journal peut-il contenir ?**
Réponse : `run.*` (`started`, `transitioned`, `completed`, `failed`, `cancelled`, `claimed`), `step.started`/`completed`, `message.user`, `model.*` (`responded`, `retried`, `exchanged`, `fell_back`), `circuit.opened`, `tool.called`/`completed`/`source_unavailable`, `policy.decided`, `guard.checked`, `budget.exceeded`, `judge.evaluated`, `approval.*` (`requested`, `granted`, `rejected`, `expired`), `question.*` (`asked`, `answered`, `expired`), `idempotency.recorded`/`reused`, `artifact.stored`, `session.*` (`snapshot`, `compacted`, `trimmed`).
Sources : `core/events/payloads.py`.

**C09. Où passe le state de session dans la requête envoyée au modèle ?**
Réponse : dans un bloc `<session_state>` en fin de requête de l'orchestrateur (s'il déclare `main.context`), en JSON compact aux clés triées, envoyé à chaque appel, même vide. Ce message n'est jamais journalisé comme message. La consigne qui l'explique est dans le prompt système.
Sources : `engine/session_state.py`, `core/model/messages.py`, décision D4 du state de session.

**C10. Un sous-agent reçoit-il le state de session ?**
Réponse : non : un sous-run ne le reçoit pas et n'y écrit pas (ses écritures sont retirées sans bruit) ; un run de compaction non plus.
Sources : `engine/session_state.py`, décision D3 du state de session.

**C11. Deux outils d'un même tour écrivent la même clé du state : laquelle gagne ?**
Réponse : l'ordre des appels décide, pas l'ordre où ils finissent, en direct comme au rejeu.
Sources : `engine/session_state.py`, décision D9 du state de session.

**C12. Quelle profondeur de sous-agents est permise par défaut ?**
Réponse : `max_depth: 1` sur l'agent.
Sources : `agents/spec.py`.

**C13. Combien d'itérations fait un agent au plus par défaut, et que se passe-t-il ensuite ?**
Réponse : 10 (`max_iterations`). Une fois la limite atteinte, le run passe en `FINALIZING` et le modèle donne une réponse forcée, sans outils.
Sources : `agents/spec.py`, `engine/loop.py`, fonction A4.

**C14. D'où vient la description d'un outil Python déclaré avec `@tool` ?**
Réponse : de la docstring de la fonction (`inspect.getdoc`), sauf si `description=` est donné. Une docstring vide est refusée. Le schéma des arguments vient de la signature.
Sources : `tools/python.py`.

## A. Accès

**A01. Quelles commandes offre la CLI `loom` ?**
Réponse : `validate`, `run`, `state`, `resume`, `replay`, `inspect`, `eval`, `approve`, `reject`, `answer`, `serve`, `mcp`, `keys create`, `report`, `sessions` (`list`, `show`, `files`, `export`, `delete`), `worker`, `retention`, `storage sql`, `memory` (`export`, `import`, `reindex`, `eval`), `schema`.
Sources : `access/cli/main.py` (`build_parser`).

**A02. Quelles options communes ont toutes les commandes `loom` ?**
Réponse : `--config` (l'amorce, `./loom-boot.yaml` par défaut), `--profile`, `--env-file`, `--no-env-file`.
Sources : `access/cli/main.py` (`_global_options`).

**A03. Comment approuver un appel en attente depuis la ligne de commande ?**
Réponse : `loom approve <run_id>` avec `--call`, `--reason`, `--arguments` (pour corriger les arguments), `--session`, `--no-wait`, `--user`, `--tenant`. Le refus est `loom reject`.
Sources : `access/cli/main.py`.

**A04. Comment poser du state à l'ouverture d'un run ?**
Réponse : par l'API Python (`run`, `stream`, `submit`) ou par la CLI (`loom run --state`). REST et MCP ne le permettent pas encore : un champ `state` dans le corps d'un lancement REST est refusé (422).
Sources : décision D5 du state de session, `docs/conception.md`.

**A05. Quelles routes expose l'API REST ?**
Réponse : sous `/v1` : `GET /agents`, `POST /agents/{name}/runs`, `POST /hooks/{name}`, `GET /runs`, `GET /runs/{run_id}`, `GET /runs/{run_id}/events` (SSE), `POST /runs/{run_id}/approve`, `/reject`, `/answer`, `/cancel`, `GET /events`, `GET /traces/{run_id}`, `GET /sessions`, `GET /sessions/{session_id}`, `/events` (JSONL), `/files`, `/files/{file}`, `/report`, `DELETE /sessions/{session_id}`.
Sources : `access/http/app.py`.

**A06. Comment suivre un run en direct par HTTP ?**
Réponse : `GET /v1/runs/{run_id}/events`, en SSE. La reprise après coupure se fait par `Last-Event-ID`.
Sources : `access/http/app.py`, point 38.

**A07. Comment loom expose-t-il un agent en MCP ?**
Réponse : chaque agent devient un outil MCP, par `loom mcp` (stdio) ou monté en HTTP avec l'API REST (`server.mcp.http`). Des ressources `loom://` en lecture seule donnent les runs, sessions, traces et fichiers.
Sources : `access/mcp_server/server.py`, `access/resources.py`.

**A08. Quelles ressources MCP `loom://` existent ?**
Réponse : `loom://runs` et `loom://sessions` (index) ; `loom://runs/{run_id}{?session_id}` et `…/events` ; `loom://traces/{run_id}{?session_id}` ; `loom://sessions/{session_id}` et `…/events` ; `loom://artifacts/{client}/{session}/{fichier}`.
Sources : `access/resources.py`.

**A09. Comment supprimer toutes les données d'une session (RGPD) ?**
Réponse : `loom sessions delete <session_id>` (avec `--yes`), ou `DELETE /v1/sessions/{session_id}` : journal, fichiers et clés.
Sources : `access/cli/main.py`, `access/http/app.py`.

**A10. Comment rejouer un run avec un autre modèle ?**
Réponse : `loom replay <run_id> --mode variant --model <id>` (ou `Loom.replay` en Python). Le mode par défaut rejoue à l'identique à partir des réponses enregistrées.
Sources : `access/cli/main.py`, `replay/`, phase 6.2b.

## P. Choix de conception

**P01. Pourquoi le journal d'événements fait-il foi ?**
Réponse : on y écrit au fil de l'eau des événements immuables, et tout le reste en est déduit par des projections : l'historique envoyé au modèle, le `RunState` (reprise, pause), les traces, la consommation et le rejeu.
Sources : `docs/fonctions.md` point 22.

**P02. Pourquoi loom utilise-t-il les SDK officiels des fournisseurs ?**
Réponse : le champ `sdk` (`anthropic` ou `openai`) choisit l'adaptateur ; le fournisseur n'est qu'une affaire de config (`base_url`, clé, capacités). MiniMax passe par `sdk: anthropic` ; Together, vLLM et Ollama par `sdk: openai`.
Sources : `docs/fonctions.md` point 9.

**P03. Pourquoi SSE et pas WebSocket ?**
Réponse : un flux à sens unique suffit, en HTTP simple, compatible avec les proxys et Cloud Run, avec reprise par `Last-Event-ID`. Les actions (`approve`, `cancel`) sont de simples `POST`. La voix n'a pas besoin de WebSocket côté loom : Pipecat gère l'audio et appelle loom en Python ou en SSE.
Sources : `docs/fonctions.md` point 38.

**P04. Pourquoi FastAPI ?**
Réponse : Pydantic natif, OpenAPI généré (client de l'interface), SSE facile, et le serveur MCP HTTP (Starlette) se monte dans la même application. Il est dans l'extra `loom-ia[http]`.
Sources : `docs/fonctions.md` point 37.

**P05. loom_v2 lit-il la config de loom V1 ?**
Réponse : non. Aucune compatibilité avec le `settings.json` de V1, et aucun outil de migration.
Sources : `docs/fonctions.md` point 36.

**P06. Pourquoi publier un événement sur le bus après l'avoir écrit au journal ?**
Réponse : le journal fait foi, le bus sert à notifier vite. Un événement notifié existe donc toujours dans le journal, et un abonné qui a raté des nouvelles se resynchronise par `seq`.
Sources : `docs/fonctions.md` point 5.

**P07. Pourquoi les outils fournis à paramètres refusent-ils `approval` sur leur référence ?**
Réponse : leurs paramètres expriment déjà l'approbation, plus finement (par dossier, par table, par action). Deux endroits pour une même décision finiraient par se contredire.
Sources : `tools_builtin/__init__.py` (`Builtin.refused_because`).

**P08. Pourquoi les docstrings des modèles d'arguments de `loom.word` n'apparaissent-elles pas dans le schéma envoyé au modèle ?**
Réponse : `input_schema` retire les titres, les docstrings des modèles (écrites pour qui lit le code) et les `default: null`, parce que le schéma part à chaque appel et coûte des tokens.
Sources : `tools_builtin/_document/arguments.py` (`input_schema`, `_light`).
