# loom_v2 — Suivi des tests des fonctions

Agent d'essai : `agent-01` (`/home/denis/temp/agent-01`, loom-ia ≥ 2.0.9, modèle `HAIKU`, outil `loom.fetch_page`).
Base : la liste des fonctions de `docs/fonctions.md` (A1…P3), mêmes identifiants.

## Catégories

| Code | Catégorie | Fonctions |
|---|---|---|
| A | Exécution d'un run | 9 |
| B | Modèles | 9 |
| C | Rôles LLM | 6 |
| D | Outils | 13 |
| E | Fiabilité des sorties | 7 |
| F | Mémoire et sessions | 8 |
| G | Pièces jointes et artefacts | 3 |
| H | Exécution durable | 7 |
| I | Streaming et événements | 4 |
| J | Coûts et budgets | 5 |
| K | Observabilité et rejeu | 7 |
| L | Multi-clients | 3 |
| M | Configuration | 7 |
| N | Accès | 6 |
| O | Qualité | 3 |
| P | Données sensibles | 3 |

---

## A. Exécution d'un run

### A1 — Lancer un run : une demande, une réponse finale

**Catégorie** : Exécution d'un run

Lancer un run à partir d'un message texte (et de pièces jointes) et obtenir la réponse finale. Par la CLI `loom run <agent> "<message>"`, par `Loom.run()` en Python, ou par `POST /v1/agents/{name}/runs`. Le texte sort sur stdout, `Statut`/`Run` sur stderr ; `--json` met tout sur stdout.

**Testée** : oui (17/10, CLI, session `session-A1`)

**Note** : `loom run agent "Bonjour, qui es-tu ?" --session session-A1` → `run.completed` en 1 itération, 1,8 s, réponse en français conforme au prompt, 0,000286 $ (4 tokens d'entrée, 223 de sortie, 1 393 écrits en cache : calcul vérifié avec la grille). `fetch_page` proposé au modèle mais non appelé, à raison. Vu au passage : écriture de cache dès le 1er appel (B5), `x-api-key` remplacé par `[retiré]` dans `model.exchanged` (K3). À revoir dans le prompt : l'agent dit « Je ne garde pas de souvenir d'une conversation à l'autre », faux dans une session (F1).

### A2 — Boucle agentique : l'orchestrateur enchaîne les outils jusqu'à répondre

**Catégorie** : Exécution d'un run

Le modèle `main` choisit un outil, reçoit le résultat, recommence, jusqu'à produire une réponse sans appel d'outil. Avec agent-01 : une question qui oblige à lire une page (`fetch_page`) puis à en suivre un lien.

**Testée** : oui (17/10, CLI, session `session-A2`)

**Note** : question sur la dernière version stable de Python → 3 itérations, 5,1 s, 0,00164 $ : `fetch_page` sur la page d'accueil (9 153 car., 76 ms), puis sur le lien de la version 3.15.0 trouvé dans la page (12 454 car., 46 ms), puis réponse. Transitions attendues (`ready_for_model → awaiting_tools → ready_for_model` ×2, puis `completed`). Réponse juste et sourcée : chaque fait (3.15.0 du 9 oct. 2026, 3.16.0a1 préversion, installeur 64 bits 43,3 Mo, `py install 3.15`, avertissement macOS 27 / IDLE) est dans les pages lues. Un peu bavarde (détails Windows/macOS non demandés). Cache relu à chaque appel (B5) : 1 307, 1 440 puis 5 341 tokens lus. Pas de déport (résultats sous 20 000 car.).

### A3 — Appels d'outils en parallèle dans un même tour

**Catégorie** : Exécution d'un run

Quand le modèle demande plusieurs outils dans une même réponse, ils s'exécutent ensemble (un « lot ») et les résultats repartent dans l'ordre des appels. À vérifier dans la trace (`loom inspect`) : plusieurs `tool.called` dans la même étape.

**Testée** : oui (17/10, CLI, session `session-A3`)

**Note** : python.org et rust-lang.org demandés « en même temps » → une seule réponse du modèle avec deux appels `fetch_page`, exécutés dans le même lot (step 2) : `tool.called` à 2 ms d'écart, deux spans distincts, fins à 12:31:00.683 et .708 (88 et 111 ms) ; le lot entier a pris 0,12 s au lieu de la somme. Les deux résultats repartent au modèle dans l'ordre des appels (`tool_result` python puis rust). 2 itérations, 3,4 s, 0,00105 $. Réponse exacte et sourcée ; pour Python, l'agent signale lui-même qu'il n'y a pas de slogan officiel et cite une phrase réellement présente sur la page.

### A4 — Arrêts : réponse, max d'itérations, budget, outil terminal, pause, réponse forcée

**Catégorie** : Exécution d'un run

Le run s'arrête sur une réponse obtenue, sur `max_iterations` (5 dans agent-01), sur un plafond de budget, sur un rôle terminal ou sur une pause d'approbation. Quand il atteint une limite, une dernière génération **sans outils** force une réponse.

**Testée** : non — en cours : 3 cas sur 6 vus (réponse obtenue, `max_iterations`, réponse forcée) ; restent budget (J4), rôle terminal (C3), pause d'approbation (H4)

**Note** : réponse obtenue : A1 à A3. `max_iterations: 1` (17/10, session `session-A4`, question de A2) → 1 `fetch_page`, puis `awaiting_tools → finalizing` (cause `tool.completed`) et un dernier appel avec `tool_choice: none` ; les outils restent déclarés dans la requête (le cache des outils est donc gardé) et la consigne de réponse forcée termine le dernier message, sans être journalisée comme message. Réponse forcée honnête : 3.15.0 tirée de la page d'accueil, et l'agent dit qu'il n'a pas ouvert la page de la version, en donnant son lien. Faits vérifiés dans la page lue. `run.completed.iterations` vaut 2 : la réponse forcée compte comme un appel. 0,00088 $.

### A5 — Annuler un run en cours

**Catégorie** : Exécution d'un run

Arrêter un run qui tourne : `Loom.cancel()`, `POST /v1/runs/{id}/cancel` (corps : `by`), outil MCP `cancel`. Le journal reçoit `run.cancelled`.

**Testée** : oui (17/10, REST, session `session-A5b`)

**Note** : `POST /v1/agents/agent/runs` avec `background: true` → réponse immédiate `{run_id, session_id, status: ready_for_model}` ; `POST /v1/runs/{id}/cancel?session_id=session-A5b` 3 s après → `{"cancelled": true}`. Journal : arrêt pendant l'appel modèle de l'étape 3 (`finalizing → cancelled`, cause `cancel`), l'étape en vol n'a pas de `step.completed`, puis `run.cancelled` (statut `warning`, `reason: requested`, `by: denis`), usage et coût (0,000123 $) de ce qui était fini ; aucune réponse finale. **Piège** : sans `?session_id=`, la route répond 404 « Run … inconnu » alors que le serveur vient de lancer ce run (la session nommée diffère du `run_id`) — idem pour `GET /runs/{id}`, `approve`, `reject`. Piste : un message qui suggère le `session_id`, ou retrouver la session d'un run lancé par ce process. (Essai fait avec `max_iterations: 1` resté en place : le run est passé en réponse forcée avant l'annulation, ce qui ne change pas le constat.)

### A6 — Timeout global du run

**Catégorie** : Exécution d'un run

`timeout:` dans l'agent borne la durée totale du run, quelles que soient les étapes en cours.

**Testée** : oui (17/10, CLI, session `session-A6b`, `timeout: 3`)

**Note** : étape 1 (modèle, 2 353 ms) + étape 2 (4 `fetch_page` en parallèle, 149 ms) = 2,5 s ; l'étape 3 (appel modèle) est interrompue en vol à 3,0 s exactement après `run.started` → `ready_for_model → failed` (cause `timeout`), `run.failed` avec `error_type: timeout` et un message clair (« l'étape 3 a été interrompue en cours (2.5 s d'étapes déjà terminées) »), usage et coût de ce qui était fini (0,00022 $). La CLI affiche l'erreur et le statut `failed`. Le délai compte bien le temps de pilotage cumulé des étapes. Note : un premier essai (`session-A6`) a tourné sans délai, la ligne `timeout` n'ayant pas été ajoutée. Reprise essayée avec `timeout: 120` : `loom resume <run> --session session-A6b` rend l'échec tel quel, rien n'est écrit au journal — `run.failed` est une clôture. Écart déjà connu (guide 03 §19, annexe D n° 8 du guide 04), le commentaire de `timeout` dans `src/loom_ia/agents/spec.py`, qui disait « le run reste reprenable — avec un délai relevé », est corrigé le 17/10. Ergonomie : `loom resume` sur un run clos réaffiche son résultat (code 1) au lieu de dire qu'il n'y a rien à reprendre.

### A7 — Réponse finale structurée selon un schéma

**Catégorie** : Exécution d'un run

Un contrat `output` à schéma JSON sur `main` : la réponse finale est un objet validé (schéma natif du fournisseur transmis quand l'appel ne peut pas appeler d'outil).

**Testée** : oui (17/10, CLI `--json`, session `session-A7`)

**Note** : contrat `output.schema` (objet `{version, date_sortie, source}`, `additionalProperties: false`, motif sur `version`) ajouté temporairement à l'agent. `run.completed.data` = `{"version": "3.15.0", "date_sortie": "2026-10-09", "source": "https://www.python.org"}`, `unverified: false`. Le message de sortie garde le même JSON en texte (`output.blocks[].text`, chaîne échappée en `--json`) : l'objet utilisable est `data`. Haiku sans `native_json` et agent avec outils : le schéma n'est pas transmis au départ, le modèle l'apprend par la réparation (voir E2).

### A8 — Contexte de l'appelant transmis aux outils et aux traces

**Catégorie** : Exécution d'un run

`tenant_id`, `user_id`, `metadata` fournis à l'ouverture du run, visibles dans `run.started.context`, dans `ToolContext` côté outil et dans les traces. Par Python et REST seulement : la CLI n'a pas d'option `user_id`/`metadata`.

**Testée** : oui, en partie (17/10, REST, session `session-A8`)

**Note** : `POST /v1/agents/agent/runs` avec `user_id: denis` et `metadata: {canal: essai, ticket: 42}` → journal : `run.started.context = {tenant_id: default, user_id: denis, metadata: {canal: essai, ticket: 42}}` (types gardés : `ticket` reste un entier). **Écart** : la fonction dit « transmis aux traces », mais ni `GET /v1/runs/{id}` ni `GET /v1/traces/{id}` ne montrent `user_id` ou `metadata` (les spans ne portent que `loom.tenant_id`, `loom.session_id`, `loom.run_id`) ; seul le journal (`run.started`, `GET /v1/events`, `sessions export`) les garde. Non vérifiable avec agent-01 : la remise aux outils (`ToolContext.caller`, `fetch_page` l'ignore) ; au modèle, seulement via un rôle `context: [caller_context]` (C2). Le run a échoué sur le contrat d'A7 resté en place (voir E4), sans effet sur le contexte enregistré.

### A9 — Plusieurs agents nommés dans une même instance

**Catégorie** : Exécution d'un run

Le registre d'agents : tous les fichiers de `agents_dir` sont montés ; `loom validate` les liste, `GET /v1/agents` aussi. Demande d'ajouter un second agent à agent-01.

**Testée** : oui (17/10, CLI et REST, sessions `session-A9` et `session-A9b`)

**Note** : second fichier `agents/traducteur.yaml` (`traducteur` : sans outil, prompt en ligne `system`, `max_iterations: 1`, `expose.mcp: false`). Les deux agents sont montés par la même instance et listés par `GET /v1/agents`. Chacun garde sa config : requête de `traducteur` sans outil et avec son propre prompt, traduction seule (« The quote is valid for thirty days from its issue date. ») ; requête d'`agent` avec `fetch_page` et `system.md`. `traducteur` resservira pour N5 (`expose.mcp: false`) et C5 (sous-agent).

---

## B. Modèles

### B1 — Plusieurs fournisseurs : Anthropic, OpenAI-compatible, modèles locaux

**Catégorie** : Modèles

Champ `sdk:` du modèle : `anthropic`, `openai` (API Chat ou `api: responses`), compatible vLLM/Ollama via `base_url`, et `fake` pour les tests. agent-01 utilise `anthropic` (claude-haiku-5-5).

**Testée** : oui, sauf modèles locaux (17/10, CLI, agent `traducteur`, sessions `session-A9`, `session-B1-m3`, `session-B1-glm`)

**Note** : même agent, même phrase, trois fournisseurs, réponses équivalentes. Anthropic (HAIKU, `sdk: anthropic`) : « The quote is valid for thirty days from its issue date. » ; MiniMax-M3 (`sdk: anthropic` + `base_url: https://api.minimax.io/anthropic`, appel `/v1/messages`, 1,1 s) : « … from the date of issue. », 0,0000512 $ ; GLM-5.3-Flash chez Together (`sdk: openai`, `api: chat`, appel `/v1/chat/completions`, 2,3 s) : « … from its date of issue. », 0,000116 $. Coûts recalculés avec chaque grille : justes. MiniMax compte 128 tokens en lecture de cache sans point de cache demandé ; GLM produit 194 tokens de raisonnement, comptés dans la sortie et non facturés deux fois. Non testé : modèle local (vLLM, Ollama).

### B2 — Format de message neutre, traduit à l'appel du fournisseur

**Catégorie** : Modèles

Les messages sont stockés en blocs neutres (texte, image, appel d'outil, raisonnement, `provider_meta`) et traduits seulement par l'adaptateur. Vérifiable en changeant de fournisseur sur une même session.

**Testée** : oui (17/10, sessions `session-B1-m3` et `session-B1-glm`)

**Note** : même agent, deux traductions de requête : API Anthropic (MiniMax) → clés `max_tokens, messages, model, stream, system`, prompt système à part, messages `[user]` ; API Chat (Together) → `model, stream, messages, max_tokens, stream_options`, prompt système en tête des messages `[system, user]`. Retour : le raisonnement de GLM (`reasoning_content` du flux) est rangé en bloc neutre `reasoning`, avec `provider_meta.openai.reasoning_field = reasoning_content` pour pouvoir le renvoyer au même fournisseur. Pas encore vu : une même session qui change de fournisseur en cours de route.

### B3 — Retry avec backoff, timeout, erreurs transitoires ou définitives

**Catégorie** : Modèles

Une erreur transitoire (429, 5xx, réseau) est réessayée avec backoff (`model.retried` au journal) ; une erreur définitive (clé invalide, 400) échoue tout de suite. Timeouts réglables (`first_token`, `idle`…).

**Testée** : oui, sauf les délais `timeouts` (17/10, sessions `session-B3` et `session-B3b`)

**Note** : PANNE (`base_url` sur le port 9 fermé, `retry: {max_attempts: 3, initial_delay: 1, multiplier: 2}`) avec secours HAIKU, session `session-B3`. 3 tentatives, chacune `ConnectError` classée `transient` ; 2 `model.retried` avec des délais de 0,879 s puis 1,109 s (fourchettes attendues [0,5 ; 1] et [1 ; 2] après tirage), délais réellement observés entre les échanges ; bascule `PANNE → HAIKU` seulement après la 3e tentative ; run `completed` en 3 s. Erreur définitive : fausse clé (`ANTHROPIC_API_KEY=sk-ant-fausse`) → un seul échange, réponse 401 `authentication_error`, aucun `model.retried`, `run.failed` immédiat avec `error_type: model.auth` ; la fausse clé elle-même est `[retiré]` dans l'échange brut. Non vus : `first_token`, `idle`, `total` (il faudrait un serveur qui répond lentement).

### B4 — Modèle de secours et disjoncteur

**Catégorie** : Modèles

`fallbacks:` sur un rôle ou sur `main` : si le modèle principal est en panne, bascule (`model.fell_back`) ; un disjoncteur écarte le modèle un moment. Le disjoncteur vit en mémoire de l'instance.

**Testée** : oui pour le secours, non pour le disjoncteur (17/10, CLI, agent `traducteur`, session `session-B4`)

**Note** : modèle `PANNE` (`base_url: http://127.0.0.1:9`, `retry.max_attempts: 1`) avec `fallbacks: [HAIKU]`. Connexion refusée en 8 ms (`model.exchanged` : `ConnectError`, statut `warning`), classée `transient`, puis `model.fell_back` `PANNE → HAIKU` et réponse normale de Haiku 0,9 s après ; run `completed`, sans erreur visible pour l'appelant. Confirmé : `model.exchanged` et `model.responded` portent le nom du modèle chez le fournisseur (`claude-haiku-5-5` pour les deux), pas l'`id` de la config ; seuls l'adresse appelée et `model.fell_back` distinguent PANNE de HAIKU (reste connu de 3.5a). **Corrigé le 17/10 (2.0.10, non publiée)** : ces événements et `judge.evaluated` portent aussi `model` (l'`id` de la config), le rapport ventile par `id` (« HAIKU (claude-haiku-5-5) »), filtre `model` dans `GET /v1/events` ; à revérifier sur agent-01 une fois loom-ia 2.0.10 installé (agent-01 utilise la 2.0.9 de PyPI). Disjoncteur non vu : il faut 5 échecs de suite (`circuit_breaker.failures`) dans une même instance, donc plusieurs runs dans `loom serve`.

### B5 — Cache de prompt

**Catégorie** : Modèles

`cache: {system, tools, messages}` (activé dans agent-01) : points de cache Anthropic ; les tokens `cache_read`/`cache_write` apparaissent dans l'usage et le rapport. Un prompt sous le minimum du modèle n'est pas caché, sans erreur.

**Testée** : oui (17/10, sessions A1 à A6)

**Note** : prompt système d'agent-01 enrichi (≈ 2 Ko) pour que outils + système dépassent le minimum de 512 tokens. Écriture au 1er appel (A1 : 1 393 tokens), relecture aux appels suivants et d'un run à l'autre, même d'une autre session (A2 : 1 307, 1 440, 5 341 lus). Le point `messages` écrit à chaque appel ce qui est nouveau (résultats d'outils) : A2 écrit 9 759 et lit 8 088 tokens ; bilan ≈ −27 % sur l'entrée par rapport à sans cache, malgré l'écriture du dernier appel, perdue si rien ne suit dans les 5 min. **Trouvé** : quand un résultat est déporté, `artifact_read` est ajouté aux outils en cours de run (A6, appel 2 : empreinte `tools` changée) ; les outils étant en tête du préfixe, tout le cache est perdu à cet appel (0 lu, 12 213 écrits). Piste : déclarer `artifact_read` dès le départ quand un outil peut déporter, ou le placer après le point de cache des outils.

### B6 — Réglages par appel (max_tokens, température, thinking)

**Catégorie** : Modèles

`max_tokens` et `params` du modèle, surchargés par `llm:` d'un rôle ; la fusion se fait par bloc entier (un bloc `thinking` remplace l'autre, il ne se mélange pas).

**Testée** : oui (17/10, agent `traducteur`, session `session-B6`)

**Note** : `main.llm: {max_tokens: 200, params: {temperature: 0}}` → requête envoyée `{max_tokens: 200, temperature: 0, …}` : le `max_tokens` du rôle remplace les 20 000 du modèle, `params` part tel quel. Claude Haiku 5.5 refuse `temperature` (400 « `temperature` is deprecated for this model ») : erreur classée `model.invalid_request`, non réessayée, run `failed`. loom ne connaît pas les paramètres d'un modèle : il transmet, le fournisseur juge. Relancé avec `params: {metadata: {user_id: denis}}` (session `session-B6b`) : requête `{max_tokens: 200, metadata: {user_id: denis}, …}`, réponse 200, run `completed`. La fusion clé par clé avec les `params` du modèle n'est pas vue ici (HAIKU n'en déclare pas).

### B7 — Vérification de la fenêtre de contexte avant l'appel

**Catégorie** : Modèles

Estimation des tokens (caractères/4) avant l'envoi ; si la session dépasse, le filet `ensure_fits` la taille (`session.trimmed`) au lieu d'échouer chez le fournisseur.

**Testée** : oui pour le refus avant appel (17/10, agent `traducteur`, sessions `session-B7` et `session-B7b`)

**Note** : règle : entrée estimée (caractères ÷ 4 du système, des messages et des outils, + 1 600 par image) + `max_tokens` > `capabilities.context_window` → refus. Fenêtre 500 : 103 + 200 = 303, l'appel part (run `completed`). Fenêtre 250 : `run.failed` immédiat, `error_type: model.context_overflow`, « Requête estimée à 103 tokens, plus 200 en sortie : dépasse la fenêtre de 250 tokens du modèle HAIKU », **aucun `model.exchanged`** : rien n'est envoyé. Estimation réelle juste : Anthropic a compté 110 tokens d'entrée pour 103 estimés. Non vus : la bascule vers un secours à plus grande fenêtre (`context_overflow` ne bascule que si les deux fenêtres sont déclarées) et le filet `ensure_fits` d'une session trop longue (F3).

### B8 — Streaming des tokens

**Catégorie** : Modèles

Le texte du modèle arrive au fil de l'eau : `loom run --stream`, `Loom.stream()` (itérateur async, `TextDelta`). SSE et MCP ne diffusent pas les morceaux, seulement les événements.

**Testée** : oui (17/10, CLI `--stream`, sessions `session-B8` (HAIKU) et `session-B8_OPUS` (OPUS))

**Note** : texte affiché au fil de l'eau dans le terminal. Côté fournisseur, réponse en flux SSE (`stream: true`) : 344 événements `content_block_delta` pour Haiku, 310 pour Opus (les morceaux eux-mêmes ne sont pas journalisés, seulement l'échange brut et la réponse complète). Comparaison sur la même question : Haiku 4,3 s, 721 tokens, 0,00054 $ ; Opus 9,2 s, 877 tokens, 0,0246 $ (≈ 46 fois plus cher, 2 fois plus lent), coûts recalculés justes. Le temps du premier token n'est pas relevé dans le journal (seulement la latence totale) : à voir avec I3.

### B9 — Capacités déclarées par modèle (vision, outils, thinking, JSON natif)

**Catégorie** : Modèles

Les capacités du modèle décident de ce qui lui est envoyé : images seulement à un modèle vision, schéma JSON natif seulement s'il le gère, raisonnement seulement s'il est `thinking`.

**Testée** : oui pour `vision` (17/10, `loom run agent … --attach chat.jpg`, sessions `session-B9` et `session-B9b`)

**Note** : HAIKU sans `vision` : l'image n'est pas envoyée ; à sa place, le texte « [image jointe chat.jpg (image/jpeg, 125 Ko) : non visible par ce modèle] », et l'agent répond honnêtement qu'il ne la voit pas. Avec `capabilities: {vision: true}` : bloc `image` en base64 dans la requête, description juste (chat tigré gris et brun aux yeux verts, fond flou ; vérifiée sur l'image), 0,000215 $. Dans `model.exchanged`, les octets de l'image sont remplacés par « [fichier image/jpeg, 127540 octets, sha256 …] » (corps de 3 Ko au journal). `tools` vu indirectement (contrôlé au chargement), `native_json` vu en A7 (schéma non transmis sans la capacité) ; `thinking` non testé.

---

## C. Rôles LLM

### C1 — Déclarer un rôle

**Catégorie** : Rôles LLM

Section `roles:` d'un agent : `name`, `description`, `model`, `system`/`system_file`, `input_schema`, `input_template`, `context`, `llm`, `timeout`, `output`, `judge`.

**Testée** : oui (17/10, agent `agent`, session `session-C1`)

**Note** : rôle `synthese` (SONNET, `system` en ligne, `input_schema {consigne}`, `context: [{tool_results: [fetch_page]}]`) chargé sans erreur. Proposé à l'orchestrateur comme un outil : sa description, complétée par loom de « Reçoit déjà, inutile de les transmettre : les résultats de fetch_page. », et son schéma, auquel loom ajoute `additionalProperties: false`.

### C2 — L'orchestrateur appelle un rôle comme un outil

**Catégorie** : Rôles LLM

Le rôle apparaît au modèle `main` comme un outil ; il ne reçoit que ses arguments et le contexte déclaré (`tool_results`, `session_state`…), pas toute la conversation — c'est l'économie de tokens. Les `$ref` dans les arguments transmettent un résultat d'outil sans le recopier.

**Testée** : oui (17/10, session `session-C1`)

**Note** : Haiku lit la page (`fetch_page`, 12 454 car.), puis appelle `synthese` (`tool_kind: role`) avec la seule `consigne`. Requête du rôle : modèle `claude-sonnet-5-5`, son propre prompt, **aucun outil, un seul message** contenant `<tool_result tool="fetch_page">` (la page) puis `<arguments>` : ni la conversation ni le prompt de l'agent. Synthèse fidèle : les 15 PEP et les pourcentages du JIT cités figurent tous dans la page. Coûts : rôle 0,0391 $ (2 429 tokens de sortie, 738 de raisonnement), orchestrateur 0,0022 $ en 3 appels ; total ≈ 0,0413 $. Constats : (1) Haiku réécrit ensuite toute la synthèse dans sa réponse (1 441 tokens de sortie) — c'est ce qu'évite un rôle terminal (C3) ; (2) le rôle, appelé une fois, écrit 5 902 tokens en cache (+25 % sur son entrée, ≈ 0,003 $), relus seulement si le même rôle reçoit la même page dans les 5 minutes : c'est arrivé par hasard (2e run lancé dans `session-C1`, Sonnet a relu 5 902 tokens et coûté 0,0218 $ au lieu de 0,039 $) ; le cache est réglé par modèle, pas par rôle.

### C3 — Rôle terminal : sa sortie devient la réponse finale

**Catégorie** : Rôles LLM

`terminal: true` : la sortie du rôle est rendue telle quelle comme réponse du run, sans repasser par l'orchestrateur. Un écart est signalé dans l'annexe D du guide 04.

**Testée** : oui (17/10, session `session-C3` ; même résultat sur le 2e run de `session-C1`)

**Note** : `terminal: true` sur `synthese`. loom ajoute à sa description « À appeler seul : sa sortie est la réponse finale, transmise telle quelle. ». Haiku lit la page puis appelle le rôle ; au retour, `awaiting_tools → completed` (cause `tool.completed`), **sans dernier appel à l'orchestrateur** : 2 itérations au lieu de 3. `run.completed` ne recopie pas la réponse : `output_event_id` pointe sur le `tool.completed` du rôle, dont le texte est la réponse finale mot pour mot (3 599 car.). 0,0373 $ contre 0,0413 $ en C1 : Haiku ne réécrit plus la synthèse (≈ 1 400 tokens de sortie en moins), l'essentiel du coût reste Sonnet. Écart connu non rencontré ici : un rôle terminal clôt le run, les autres outils de son lot ne s'exécutent pas (annexe D du guide 04).

### C4 — Rôle vision : reçoit les pièces jointes, masqué sans elles

**Catégorie** : Rôles LLM

Un rôle sur un modèle vision reçoit les images du run ; quand le run n'a pas de pièce jointe, l'orchestrateur ne le voit pas dans ses outils.

**Testée** : oui (17/10, sessions `session-C4a` et `session-C4b`)

**Note** : rôle `regard` (HAIKU, `context: [attachments]`). Sans pièce jointe (C4a) : outils proposés `fetch_page, synthese`, **`regard` masqué**. Avec `chat.jpg` (C4b) : `regard` proposé, appelé avec une `question`, et sa requête contient le texte puis le bloc `image` (base64), sans outil. Description juste (assis sur un sol en lames de bois, regard de côté, oreilles dressées, vigilance). 2 itérations, ≈ 0,0013 $. Constat : HAIKU étant déclaré `vision`, l'orchestrateur reçoit aussi l'image à chacun de ses appels (blocs `text, image` dès l'appel 1) ; le rôle vision n'économise donc rien ici — il a du sens quand l'orchestrateur n'est pas `vision` (ou pour confier l'image à un modèle plus fort). Reste connu de la doc : « un orchestrateur vision renvoie les images à chaque appel ».

### C5 — Sous-agent : un agent complet appelé comme un outil

**Catégorie** : Rôles LLM

`subagents:` : un autre agent, avec ses outils, sa boucle, son propre run enfant ; `budget_share`, `max_depth`. L'enfant ne reçoit pas les pièces jointes du parent ni le state de session.

**Testée** : oui, cas simple (17/10, session `session-C5`, `subagents: [{agent: traducteur}]`)

**Note** : `traducteur` proposé à l'orchestrateur avec un seul argument `message` (`minLength: 1`), et une description complétée par loom (« Sous-agent : il ne reçoit q[ue]… »). Haiku lit python.org puis appelle `traducteur` (`tool_kind: agent`, `child_run_id`) ; un **run enfant** s'ouvre dans la même session (`agent: traducteur`, `depth: 1`, `parent_run_id` et `parent_call_id` renseignés), avec sa propre requête (son modèle, son prompt, aucun outil, le seul message reçu) ; sa réponse revient comme résultat de l'outil. Coût du run racine (0,001195 $) = ses 3 appels + celui de l'enfant (0,00004 $) : la consommation de l'enfant est bien comptée dans la racine. `expose.mcp: false` n'empêche pas l'usage en sous-agent. Délégation spontanée (prompt système de l'agent complété, sessions `session-C5b`, `session-C5c`) : « Traduis en anglais : … » → `traducteur` appelé sans être nommé ; « Résume la page … puis traduis ce résumé » → `fetch_page`, résumé rédigé par l'orchestrateur (le rôle terminal `synthese` n'est **pas** appelé, sinon le run s'arrêtait avant la traduction), puis `traducteur` (963 tokens de sortie pour un plafond de 1 024). Non vus : `budget_share`, `max_depth`, un enfant qui a lui-même des outils.

### C6 — L'orchestrateur est lui-même un rôle (`main`)

**Catégorie** : Rôles LLM

`main:` se déclare comme un rôle (modèle, prompt, `fallbacks`, `llm`, `context: [session_state]`, contrat `output`, juge).

**Testée** : oui en partie (17/10, sessions A1 à C1)

**Note** : `main` déclaré comme un rôle (`model`, `system_file`, `fallbacks` en B4, `llm` en B6, contrat `output` en A7) ; ses appels portent `role: main` dans le journal et le rapport, ceux du rôle `role: synthese`. `main.context: [session_state]` sera vu avec F8.

---

## D. Outils

### D1 — Contrat unique d'outil

**Catégorie** : Outils

Tout outil (Python, MCP, rôle, source) a nom, description, schéma d'entrée et capacités déclarées (`side_effects`, `approval`, `idempotent`, `timeout`, `offload_over`), surchargeables dans `tools:` de l'agent.

**Testée** : oui (18/10, CLI, session `session-D2a`)

**Note** : les 4 outils de `tools/utils.py` partent au modèle avec leur nom (celui de la fonction), leur description (la docstring) et leur schéma tiré de la signature : `Field(ge=0, le=100)` devient `minimum`/`maximum`, `taux_tva` a son défaut 20.0 et n'est pas requis, `additionalProperties: false` partout. Les capacités (`timeout=3`, `approval="always"`) restent côté loom, absentes de la requête. Surcharges `tools:` de l'agent pas encore essayées.

### D2 — Outils Python, synchrones ou asynchrones

**Catégorie** : Outils

Fonction décorée `@tool` dans un module de `imports:`, référencée `- python: <nom>` ou `module:fonction`. Le schéma vient de la signature.

**Testée** : oui (18/10, CLI, sessions `session-D2a2`, `session-D2b`)

**Note** : module `tools/utils.py` (paquet implicite, `imports: [tools.utils]`), outils référencés `- python: <nom>`. Sans consigne, Haiku calcule la TVA lui-même (`session-D2a`) ; après ajout au prompt système d'une règle générale « quand un outil correspond à la tâche, appelle-le », il appelle `prix_ttc` (1 250 € HT à 10 % → `{montant_ht: 1250.0, tva: 125.0, montant_ttc: 1375.0}`, 0,7 ms). Un `dict` rendu devient un bloc `json` et le champ `data` du résultat. Asynchrone : `attendre` (`async def`) avec 1 s, `tool.completed` en 1 001 ms.

### D3 — Outils MCP (stdio, HTTP), découverte et reconnexion

**Catégorie** : Outils

Serveurs MCP déclarés dans la config, branchés par `- mcp: <nom>` (`alias`, `include`/`exclude`, `required`) ; outils préfixés `<nom>__<outil>` ; reconnexion automatique ; portée `run` ou partagée.

**Testée** : en partie (18/10, CLI, sessions `session-D3a`, `session-D3b`) : stdio, découverte, préfixe, erreurs, `alias`, `required`, reconnexion oui ; transport HTTP, portées `tenant`/`run`, `idle_timeout` non éprouvés

**Note** : serveur `loom-fs-mcp` (FastMCP, stdio) déclaré `mcp_servers: [{name: fs, transport: stdio, command: …/.venv/bin/loom-fs-mcp, args: [--config, …/roots.toml]}]`. Les outils arrivent préfixés `fs__…`, avec la description et le schéma du serveur (`additionalProperties: false` ajouté) ; `tool_kind: mcp`. `fs__list_roots` rend les 5 racines. Trois appels dans le même lot (`list_roots`, `read_file` de `agent-01/loom.yaml`, `read_file` de `agent-01/.env`) : la lecture réussit, le `.env` revient en résultat d'erreur avec le message du serveur (« matches deny pattern '.env' »), le run continue et le modèle en rend compte. `alias: f` (`session-D3c`) : outils `f__…` ; l'ancienne référence `- mcp: fs` étant restée, le même serveur est exposé deux fois (`fs__…` et `f__…`, 8 outils), accepté sans avertissement, et le modèle appelle les deux `list_roots`. Serveur `casse` (`command: /bin/false`) : `required: false` → `tool.source_unavailable` (`error: BrokenResourceError`, peu parlant : ni code de sortie ni stderr) et le run se termine normalement ; `required: true` (`session-D3d`) → `run.failed`, « source casse requise et indisponible : Connection closed », code de sortie 1. Reconnexion sous `loom serve` (`session-D3e`) : après `pkill` du serveur MCP, log « Serveur MCP fs : contrôle de santé raté (ClosedResourceError()) » puis relance du serveur au run suivant ; les deux runs ont toutefois échoué sur `casse`, resté `required: true`. Reconnexion confirmée (`session-D3f`, `loom serve`, `casse` retiré) : run 1 réussi, `pkill` du serveur MCP, run 2 → `fs__read_file` de `agent-01/pyproject.toml` réussi sur la connexion rouverte (contrôle de santé raté, serveur relancé), réponse juste. Le double référencement `fs` / `f` était encore là (deux `list_roots` au run 1). Après correctif (CHANGELOG 2.0.10, « Outils MCP »), serveur `casse` = loom-fs avec `--config absent.toml` : « Source d'outils casse indisponible : le serveur s'est arrêté ou a refusé l'ouverture (Connection closed) ; fin de son stderr : File ".../pathlib/__init__.py", line 771, in open | return io.open(…) | FileNotFoundError: … absent.toml », la trace complète restant sur la console ; double référencement `fs`/`f` signalé par `loom validate` et au lancement.

### D4 — Validation des arguments par schéma, erreur renvoyée au LLM

**Catégorie** : Outils

Des arguments hors schéma ne lancent pas l'outil : le diagnostic part au modèle qui corrige au tour suivant.

**Testée** : oui (18/10, CLI, sessions `session-D2a2`, `session-D4`)

**Note** : cas survenu sans le provoquer : dans la même réponse, Haiku émet un second `tool_use` `prix_ttc` vide (`{}`, vérifié dans le flux brut : ce n'est pas un artefact de loom). loom le rejette sans l'exécuter (aucun `tool.called`, seulement `tool.completed` en erreur : « propriété requise absente : montant_ht »), exécute l'appel valide du même lot, et le modèle répond normalement. Bornes (`session-D4`) : `prix_ttc(montant_ht=-500, taux_tva=150)` refusé sans exécution, les deux violations listées en français (« doit être supérieur ou égal à 0 », « doit être inférieur ou égal à 100 ») ; le modèle les rapporte fidèlement.

### D5 — Timeout par outil, erreurs isolées

**Catégorie** : Outils

Chaque outil a son délai (`fetch_page` : 45 s) ; une exception ou un dépassement devient un résultat d'erreur pour cet appel seul, le run continue.

**Testée** : oui (18/10, CLI, sessions `session-D5a`, `session-D5b`)

**Note** : délai : `attendre(5)` avec `@tool(timeout=3)` → `tool.completed` en erreur à 3 004 ms, « Délai dépassé : pas de réponse en 3 s. », le run se termine normalement (`completed`). Exception : `lire_devis("1234")` lève `ValueError` → résultat d'erreur « Erreur de l'outil lire_devis : ValueError: Numéro de devis invalide : 1234 » ; `prix_ttc`, appelé dans le même lot, s'exécute (960 € TTC) ; le modèle rend le calcul et demande le bon format. Une exception autre que `ToolError` est journalisée en WARNING avec sa trace (affichée sur la console du CLI) ; une `ToolError` donne le même résultat d'erreur, sans trace ni préfixe. Reste à noter : pour un outil synchrone (exécuté dans un thread), le délai rend la main au run mais Python ne peut pas interrompre le thread, qui finit son travail en arrière-plan.

### D6 — Gros résultats tronqués puis déportés en artefact

**Catégorie** : Outils

Au-delà de `offload_over` (20 000 caractères pour `fetch_page`), le résultat est rangé dans le stockage d'artefacts ; le modèle reçoit un extrait et une référence, relisible par `artifact_read`.

**Testée** : oui (17/10, CLI, session `session-A6`)

**Note** : la page Downloads de python.org (24 804 car.) dépasse `offload_over` de `fetch_page` (20 000) : résultat rangé en `artifact://default/session-A6/<sha256>.txt` (fichier dans `data/.artifacts/default/session-A6/`), le modèle reçoit un aperçu de 2 268 car. avec la référence. Les trois autres pages (6 à 9 Ko) restent en ligne. L'outil `artifact_read` apparaît alors dans la requête suivante ; le modèle l'appelle de lui-même (`offset: 1500, limit: 5000`) et reçoit « Caractères 1500 à 6500 sur 24804 ; suite : offset=6500 ». Adressage par contenu vérifié : le même fichier (même empreinte) existe dans `session-A5`. Effet de bord : voir B5 (l'ajout d'`artifact_read` casse le cache).

### D7 — Choix des outils exposés à chaque run

**Catégorie** : Outils

Filtrage des outils par client (surcharges de tenant), par capacité du modèle, par `include`/`exclude` d'une source.

**Testée** : oui (18/10, CLI, sessions `session-D7a` à `session-D7e`, clients `default` et `dupont`)

**Note** : `- mcp: fs` avec `include: [list_roots, list_directory, read_file, search_files]` : la requête ne propose que ces 4 outils (ni `write_file`, ni `edit_file`, ni `delete`, que le serveur expose pourtant). Demande de création de fichier : le modèle constate qu'il n'a pas d'outil d'écriture et le dit, sans contournement. `exclude: [write_file, edit_file, move_file, delete, create_directory]` (`session-D7b`) : les mêmes 4 outils `f__…` qu'avec `include`. Client `dupont` (`tenants: [{id: default}, {id: dupont, tools_deny: [f__read_file, traducteur, graphique_devis], approvals: {prix_ttc: always}}]`) : journal rangé dans `data/dupont/` ; la requête de `session-D7c` n'a ni `f__read_file`, ni `traducteur`, ni `graphique_devis` (outil MCP, sous-agent et outil Python retirés) ; le modèle contourne en lisant la ligne voulue par `f__search_files` avec `contains` — `tools_deny` retire un outil, pas une capacité : la frontière reste au serveur. `session-D7d` : `prix_ttc` passe en approbation (`approval.requested`, run `paused`, code 1) ; témoin `default` (`session-D7e`) : `prix_ttc` s'exécute sans pause (1 080 € TTC). Écarts : (1) le motif de la demande dit « outil déclaré à approbation obligatoire (effets : none) », alors que l'approbation vient de la règle du client ; (2) la commande proposée par le CLI (`loom approve <run> --call …`) omet `--session` et `--tenant`, indispensables ici.

### D8 — Résultats riches : texte, JSON, images, fichiers

**Catégorie** : Outils

Un outil peut rendre un `ToolOutput` en blocs : texte, données JSON, image, fichiers produits (artefacts).

**Testée** : oui (18/10, CLI, sessions `session-D8a`, `session-D8b`)

**Note** : outils `graphique_devis` (rend `Image`, PNG construit en stdlib) et `exporter_devis` (rend un `ToolOutput` : texte, `JsonBlock` + `data`, `InlineDataBlock` CSV). Image : `artifact.stored` (`image/png`, 404 o, origine `tool_output`), le résultat ne porte que la référence ; HAIKU (vision) reçoit la mention « [image graphique_devis.png … : dans le message qui suit] » et l'image dans un message utilisateur placé après les résultats (`tool_result_media` vaut `false` par défaut) ; il la décrit juste (trois barres, la 2ᵉ la plus basse, la 3ᵉ la plus haute) mais ajoute « pas une image que tu m'aurais envoyée ». Fichier : CSV rangé en artefact (`text/csv`, 113 o), le modèle lit le texte, le JSON et la mention du fichier, et rend le total (990 € HT). Écart : le CSV est rangé sous l'extension `.bin` (`text/csv` absent de `_EXTENSIONS`, `core/model/media.py`). Le modèle a aussi appelé `lire_devis` de lui-même, dans le même lot. Relance avec `capabilities: {vision: true, tool_result_media: true}` sur HAIKU (`session-D8a2`) : l'image part **dans** le `tool_result` (`[result:1]` puis le bloc `image`), plus de message utilisateur ajouté ; la description est juste et la phrase parasite a disparu. Écart corrigé le même jour : extension `.bin` (CHANGELOG 2.0.10, « Fichiers ») ; ajout de `loom sessions files` / `Loom.session_files()` / `GET /v1/sessions/{id}/files`.

### D9 — Exécution de code en sandbox Firecracker (`forge`)

**Catégorie** : Outils

Source d'outils `forge` : l'agent forge des outils exécutés dans une microVM (`firecracker/`, `execd`). Demande KVM, l'image `make_vm.sh` et le jailer sur le poste.

**Testée** : oui (19/10, VM `/home/denis/dev/vm_loomv2/forge` lancée par `run.sh`, sans jailer ; `limits: {wall_ms: 9000}`, `catalog_dir: catalogue`)

**Note** :
- D9a : `forge__forge` démarre la VM au premier appel, exécute les 2 exemples (3 → 9, -4 → 16) et accepte `carre` en 1,8 s démarrage compris ; `forge__call` rend 144 en 115 ms (VM déjà démarrée). Catalogue `catalogue/default/carre/` (`carre.py`, `outil.json`). La VM est arrêtée à la fin du process (`console.log` : `exit_code=0`, plus de process firecracker).
- D9b : au run suivant, `forge__carre` est un outil à part entière, appelé directement (49, 1,6 s démarrage compris).
- D9c1 : exemple faux → « Outil double refusé — exemple 1 {"n": 3} : attendu 7, obtenu 6 », rien au catalogue ; Haiku ne corrige pas l'exemple de lui-même et demande.
- D9c2 : pas de réseau dans la VM → `tool_raised`, `Temporary failure in name resolution` avec la trace Python ; outil refusé, Haiku attribue bien l'échec au réseau absent.
- D9d : `while True: pass` avec l'exemple 1 → 1 : Haiku refuse de forger (exemple impossible), limite non atteinte. Refait (D9d2) : `compte(n)` forgé (exemple 1000 → 1000), puis `forge__call` avec n = 10¹⁰ → « Échec dans la VM (timeout) : depassement de wall_ms (9000 ms) {"limit": "wall_ms", "elapsed_ms": 9021} » à 9 027 ms ; le run se termine, la VM survit. D9d3 : nouveau run, `forge__carre(5)` → 25.
- D9e : client `dupont` → seulement `forge__forge` et `forge__call` : catalogue par client (`catalogue/dupont/`, absent).
- Détail : le message de délai vient d'execd, dans la VM, en ASCII (« depassement »).

### D10 — Outils sensibles « approbation requise »

**Catégorie** : Outils

`approval:` sur un outil : l'appel est mis en attente ; le reste du lot s'exécute, puis le run se met en pause (voir H4).

**Testée** : oui (18/10, CLI, session `session-D10`)

**Note** : `envoyer_relance` déclaré `@tool(side_effects="irreversible", approval="always")`. Le modèle lit d'abord le devis (`lire_devis`, exécuté sans attente), puis appelle `envoyer_relance` : `approval.requested` (arguments complets journalisés), transition `awaiting_tools → paused`, bail du worker rendu. Le CLI affiche `Statut : paused`, une réponse vide (« — ») et la commande `loom approve <run_id> --call <call_id>` toute prête. Rien n'est exécuté avant la décision.

### D11 — Idempotence des outils à effet de bord à la reprise

**Catégorie** : Outils

Un outil `side_effects` + `idempotent` reçoit une clé d'idempotence ; à la reprise, un appel déjà fait n'est pas refait. Magasins : mémoire, SQLite, Postgres, Redis.

**Testée** : oui (18/10, CLI, sessions `session-D11a3b`, `session-D11a4`, `session-D11b`, `session-D11c`) : magasin SQLite ; mémoire, Postgres, Redis non éprouvés

**Note** : outils de test dans `tools/utils.py`, effet visible = une ligne dans `factures.log` ; `storage.idempotency: {backend: sqlite, path: data/idempotence.db}`. Plantage simulé par `pkill -9` pendant l'attente de 60 s de l'outil, reprise par `loom resume` après expiration du bail (60 s). **Sans idempotence** (`facturer_lent`, irréversible, `session-D11b`) : à la reprise, l'appel n'est pas relancé (pas de nouveau `tool.called`), `tool.completed` en erreur « État inconnu : l'exécution de cet outil a été interrompue… n'a pas été relancé automatiquement » ; le modèle relit le devis et refuse de refacturer (risque de doublon) ; une seule ligne pour ce run. **Avec `@idempotent(reservation=20)`** (`facturer_lent_idem`, `session-D11c`) : l'appel est relancé (`tool.called` `resumed: true`), le décorateur trouve la réservation périmée et rend « État inconnu : une exécution précédente… » sans refaire l'effet ; une seule ligne. **Clé métier** (`facturer_acompte`, clé `acompte:<devis>`) : `session-D11a2` émet la facture (une ligne) ; `session-D11a1` n'a pas appelé l'outil (Haiku a demandé confirmation, le devis n'étant pas « accepté ») : `session-D11a3b` a refait l'effet : la trace de `session-D11a2` n'était pas dans `data/idempotence.db` (cause probable : magasin pas encore pointé vers ce fichier au moment de D11a2 ; le mécanisme est reproduit sans écart en simulé : 3 sessions, 1 effet). `session-D11a4`, autre session, même devis : `idempotency.reused` (clé `default:facturer_acompte:acompte:D-0050`), réponse « facture émise », **aucune nouvelle ligne** dans `factures.log`.

### D12 — Sources d'outils fournies par un paquet (`tool_sources`)

**Catégorie** : Outils

`tool_sources:` dans `loom.yaml` : un point d'entrée du groupe `loom_ia.tools` (`entry_point`, `params`), branché dans l'agent par `- source: <nom>` ; le paquet n'est importé que si un agent le référence.

**Testée** : oui (18/10, avec la source `forge` de loom-ia, sans démarrer la VM)

**Note** :
- D12a : `entry_point: forgee` déclaré mais non référencé → `validate` et le run passent ; `validate` affiche `Source : forge → point d'entrée forgee` et `Paquets : forge (loom-ia 2.0.10)` sans « utilisé ».
- D12b : bon point d'entrée, `vm_dir` faux, non référencé → tout passe, `forge (loom-ia 2.0.10, utilisé)` : la fabrique n'est pas appelée tant qu'aucun agent n'a la source.
- D12c : référencée par `- source: forge`, la fabrique est appelée au montage ; erreurs de configuration avant tout run (rien au journal), qui nomment agent, source, paquet, version et cible : `'vm_dir' : …/absent/vm.env introuvable`, `params inconnus : inconnu (vm_dir, catalog_dir, limits)`, `Point d'entrée 'forgee' absent du groupe loom_ia.tools … (installés : forge)`.
- D12d1 : vrai `vm_dir` → le modèle voit `forge__forge` et `forge__call` (préfixe = nom de la source) ; catalogue absent accepté, rien créé.
- D12d2 : `alias: fg`, `include: [call]` → `fg__call` seul.
- D12d3 : `tool_sources[].tools: {forge: {approval: always}}` → pause sur `forge__forge` (« outil déclaré à approbation obligatoire »), refus → résultat d'erreur « Appel à forge__forge refusé : test D12, pas de VM » ; VM non démarrée (console.log inchangé), `catalogue/` non créé.
- Écart D12d3 : après le refus, Haiku a redemandé **le même appel, mêmes arguments**, dans le même run → seconde pause. Le texte du refus ne dit ni qu'un humain a refusé, ni de ne pas relancer ; après le second refus, Haiku conclut que « le service de forge a refusé l'appel … le problème vient de l'environnement d'exécution ». Traité le 18/10 (CHANGELOG 2.0.10) : le texte devient « Appel à X refusé à l'approbation par denis (motif : …). C'est une décision, pas une panne : ne refais pas cet appel ; dis à l'utilisateur ce qui a été refusé et pourquoi. », et un appel identique (même outil, mêmes arguments) à un appel refusé dans le même run est conclu en erreur sans nouvelle demande. Vérifié en réel (`session-D12e`) : un seul `approval.requested`, refus `--by denis`, Haiku ne relance pas et répond « La création a été refusée à l'approbation par denis, avec le motif « test D12, pas de VM ». Je ne réessaie pas. » ; VM non démarrée. Le garde-fou n'a donc pas servi en réel (couvert par `tests/unit/test_refus.py`).
- `required` non retesté : même mécanisme que MCP (D3), et une fabrique qui refuse arrête déjà le montage.

### D13 — Outils fournis : `loom.fetch_page`

**Catégorie** : Outils

Outils livrés avec loom-ia, inactifs par défaut, activés par `- python: loom.<outil>` (agent-01 active `loom.fetch_page`). `fetch_page` lit une page web publique (http/https, ports 80/443, adresses publiques seulement, 5 redirections, 5 Mo max) et rend URL finale, titre, texte et liens (50 max). Ni JavaScript, ni PDF, ni image.

**Testée** : oui en partie (17/10, CLI et REST, sessions `session-A5`, `session-A6`, `session-C3` et suivantes) : usage normal oui ; limites (JavaScript, PDF, adresses privées, redirections, 5 Mo) non éprouvées

**Note** : activé par `- python: loom.fetch_page` dans agent-01, vu par le modèle sous le nom `fetch_page` (sans le préfixe `loom.`). Pages publiques de python.org lues en A5/A6 (6 à 25 Ko) : URL finale, titre, texte et liens rendus ; la page de 24 804 caractères a été déportée (voir D6) ; pages lues ensuite par `synthese` via `context: [{tool_results: [fetch_page]}]` (C3). Délai propre de l'outil (45 s) jamais atteint.

---

## E. Fiabilité des sorties

### E1 — Contrat de sortie : schéma, regex, longueur, non vide

**Catégorie** : Fiabilité des sorties

`output:` sur un rôle, un outil ou `main` : schéma JSON, motif regex (borné en temps), longueur max (`max_chars` ; pas de longueur min), sortie non vide ; résultat dans `guard.checked`.

**Testée** : oui (19/10, agent `agent_test`, loom 2.0.10 éditable, sessions `session-E1a` à `session-E1e`)

**Note** : contrat sur l'agent : `must_match: '\d{2}/\d{2}/\d{4}'`, `must_not_match: '(?i)sk-ant-'`, `max_chars: 400`, `repair.max_attempts: 0`, `on_failure: unverified` (diagnostic brut, sans réparation). `validate` montre `loom.contract : after_tool, on_output` (le point `on_output` n'apparaît qu'avec un contrat de réponse finale) mais pas le contenu du contrat (corrigé en 2.0.10 : une ligne « contrat (réponse finale | rôle X | outil Y) : règles ; réparations N, échec → … » par contrat). E1a conforme (« … le 14/07/1789. ») → `guard.checked` `passed`, `normalized: false`, pas de `policy.decided`, `run.completed` `unverified: false`. E1b (1019 caractères, sans date) → un seul `guard.checked` `failed`, les deux problèmes dans la raison (« motif attendu absent : \d{2}/\d{2}/\d{4}; 1019 caractères, au-delà de 400 »), `resolution: unverified`, `run.completed` statut `warning`, `unverified: true`, texte gardé. E1c → « motif interdit présent : (?i)sk-ant- (position 11, longueur 7) » : le secret n'est pas recopié dans la raison (il reste dans `message.user`, `model.exchanged`, `model.responded` et la sortie gardée, normal en `unverified`). E1d : Haiku rend « ```\n``` » → normalisé en texte vide → « la sortie est vide; motif attendu absent : … », `normalized: true`, `policy.decided` `replace` « sortie normalisée » ; la réponse finale livrée est vide (`""`), marquée `unverified`. Remarque : en `unverified`, c'est la sortie normalisée qui est gardée, alors que la docstring de `core/model/contract.py` dit « gardée telle quelle ». Coût des 4 runs : 0,0012 $. E1e, contrat sur l'outil Python `prix_ttc` (`schema` : `montant_ttc` ≤ 1000) : deux appels en parallèle ; 500 € → `guard.checked` `tool:prix_ttc` `passed`, objet JSON gardé tel quel (pas de `policy.decided`) ; 2000 € → `failed` « montant_ttc : doit être inférieur ou égal à 1000 », `resolution: fail`, `policy.decided` `replace` « contrat non respecté », `tool.completed` `is_error: true` avec le diagnostic seul (pas la sortie), aucune réparation par loom. **Défaut** : le texte rendu à l'orchestrateur est le diagnostic de réparation destiné à l'auteur, qui finit par « Réponds de nouveau avec la sortie corrigée, et seulement elle. » ; Haiku l'a lu comme « une erreur de format, je le relance » et a refait l'appel identique (même refus, une itération de plus : 3 au lieu de 2), puis a donné 2 400 € de son propre calcul en le signalant. Corrigé en 2.0.10 (option A, `refusal()` dans `guards/contract.py`) : l'orchestrateur reçoit « Résultat de prix_ttc refusé par son contrat de sortie (une règle de la configuration, pas une panne) : - montant_ttc : … Le même appel avec les mêmes arguments sera refusé de la même façon : ne le refais pas à l'identique. Dis à l'utilisateur que ce résultat n'a pas pu être validé. », sans schéma ni consigne de réparation (pour un rôle : « Sortie du rôle X refusée … après N réparations par son modèle »). Revérifié en réel (session `session-E1e2`) : même refus, aucun appel relancé, 2 itérations au lieu de 3 (0,00053 $), et Haiku dit « Un nouvel appel identique donnerait le même refus, donc je ne l'ai pas relancé » puis donne 2 400 € en précisant qu'ils ne sont pas vérifiés par l'outil. Réponse finale ensuite contrôlée par le contrat de l'agent (`unverified`). Coût : 0,00067 $.

### E2 — Réparation par le modèle auteur

**Catégorie** : Fiabilité des sorties

Une sortie qui ne tient pas le contrat est renvoyée à son modèle avec la sortie fautive et le diagnostic, jusqu'à `max_attempts`.

**Testée** : oui (17/10, session `session-A7`)

**Note** : 1re réponse en prose → `guard.checked` (`failed`, « ce n'est pas un JSON valide », `attempt: 1`, `resolution: retry`), `policy.decided` `retry`, puis un `message.user` de `kind: repair` : « Réponse refusée par un contrôle (loom.contract) : … Schéma JSON attendu : {…} ». 2e réponse conforme mais dans un bloc ```json``` → normalisation (`normalized: true`, `policy.decided` `replace` « sortie normalisée ») → `guard.checked` `passed` (`attempt: 2`). Coût : un appel de modèle de plus (3 itérations au lieu de 2).

### E3 — Juge LLM : critères, seuils, bloquant ou non, échantillonnage

**Catégorie** : Fiabilité des sorties

`judge:` : un modèle note la sortie sur des critères ; `when` (échantillonnage), `repair`, bloquant ou non ; `--judges auto|force|skip` à la CLI. Événement `judge.evaluated`.

**Testée** : oui (19/10, agent `agent_test`, sessions `session-E3a2` à `session-E3e`, juge OPUS sur HAIKU)

**Note** : 19/10, agent `agent_test`, session `session-E3a`, juge `qualite` sur OPUS (Claude Opus 5.5), `context: [user_input]`, 3 critères. **Défaut** : l'appel du juge part avec `tool_choice: {type: any}` (verdict imposé) et Opus 5.5 le refuse : 400 « tool_choice: type "tool" and "any" are not supported for this model. » (Opus 5.5 et Fable 5.1 n'acceptent plus d'outil imposé : seulement `auto` et `none`). `loom validate` ne l'a pas vu (seul `params.thinking` est contrôlé pour un juge). Effet : `policy.decided` `fail` (`error: true`), `run.failed` `policy.loom.judge.qualite` — c'est bien `on_error: block` par défaut. Corrigé en 2.0.10 : capacité `capabilities.forced_tool_choice` (défaut `true`) ; à `false`, la requête qui impose un outil part en `auto` (`sendable()`, `engine/fallback.py`, secours compris), `loom.require_tool` est refusée au chargement sur un tel modèle, et le 400 d'Anthropic dit la clé à déclarer. Revérifié (session `session-E3a2`, OPUS déclaré `forced_tool_choice: false`) : requête du juge en `tool_choice: auto`, Opus appelle bien `verdict` ; `judge.evaluated` : `repond` 1,0, `concise` 1,0, `source` 0,0 (non bloquant) → `passed: false`, `blocked: false` ; `guard.checked` `passed` « non bloquant : source (0,00 < 0,80) : Aucune source n'est citée … » ; `run.completed` sans `unverified`. Coût : Haiku 0,0001 $, juge Opus 0,0090 $ (≈ 85 fois la réponse jugée). E3b (« Explique en détail l'histoire du canal de Bourgogne », critères volontairement en conflit) : 1re réponse 5977 caractères → `concise` 0,00 bloquant → `guard.checked` `failed` `retry`, `policy.decided` `retry` (`tools: true` : l'orchestrateur garde ses outils), `message.user` `kind: repair` « Réponse refusée par un contrôle (loom.judge.qualite) : Le juge qualite refuse la sortie : - concise (note 0,00, seuil 0,80) : … Corrige la sortie sur ces points et donne-la de nouveau en entier. » ; 2e réponse 359 caractères → `concise` 1,00 mais `repond` 0,50 (« pas en détail ») → `fail` → `run.failed` `guard.judge`, coût 0,043 $ (deux appels du juge : 0,028 + 0,012). **Remarque** : le diagnostic de réparation ne cite que les critères échoués ; Haiku a raccourci sans savoir que `repond` restait exigé, d'où une bascule d'un critère à l'autre. Corrigé en 2.0.10 : le diagnostic ajoute « Critères déjà respectés, à garder dans la sortie corrigée : - repond : La réponse traite la demande de l'utilisateur. » (règle du critère, pas le motif ; ni les non bloquants). Revérifié (session `session-E3f`, « Résume l'histoire du canal de Bourgogne », mêmes critères) : 1re réponse 1488 caractères → `concise` 0,00 → réparation avec le rappel de `repond` → 2e réponse 418 caractères, trois phrases qui gardent l'essentiel (tracé, dates, tunnel, déclin, tourisme) → `repond` 1,00, `concise` 1,00 → acceptée ; coût 0,034 $ (juge 0,019 + 0,013). E3c `--judges skip` → `guard.checked` `skipped` `caller_skip`, aucun appel du juge, réponse longue livrée (0,0019 $). E3d `when: {sample: 0}` → `skipped` `sampled_out`, aucun appel du juge ; `validate` et chaque run journalisent l'avertissement « juge 'qualite' : bloquant, mais ne juge qu'une partie des runs (sample 0) » (E6) ; `validate` montre « juge qualite (réponse finale) : modèle OPUS, 3 critère(s), sample 0 ». E3e `--judges force` → jugé malgré `sample: 0`, `passed` (« non bloquant : source … »). Ergonomie de la CLI : un juge sauté (E3c, E3d) n'avait aucune ligne « Juge », et la ligne d'un verdict ne distinguait pas un critère non bloquant. Corrigé en 2.0.10 : `RunResult.skipped_judges` et ligne « Juge : … — non jugée : <motif en clair> » ; « (seuil 0,80, non bloquant) » vu en E3f ; « Juge : qualite (réponse finale) — non jugée : sauté à la demande de l'appelant (judges: skip) » vu en E3g (session `session-E3g`).

### E4 — Politique d'échec : lever une erreur ou avertir

**Catégorie** : Fiabilité des sorties

`on_failure: fail|unverified|fallback` : quand la réparation n'a pas suffi, le run échoue, la sortie passe marquée `unverified`, ou elle est remplacée par `fallback_message` (obligatoire avec `fallback`).

**Testée** : oui : `fail` (17/10, REST, session `session-A8`), `unverified` et `fallback` (19/10, agent `agent_test`, sessions `session-E4a` et `session-E4b`)

**Note** : contrat d'A7 resté actif (`repair.max_attempts: 2`, `on_failure` par défaut `fail`) sur une question sans rapport. 3 réponses refusées (`guard.checked` attempt 1 et 2 → `retry`, attempt 3 → `fail`) puis `run.failed`, `error_type: guard.contract`, `data: null`, `output: null`. Bon comportement du modèle : il refuse d'inventer une version et finit par un JSON hors schéma qui le dit. Ergonomie : le message d'erreur répète trois fois « propriété requise absente : version, date_sortie, source ». Cas `unverified` vu en E1 (19/10, sessions `session-E1b` à `session-E1d`, sans réparation) : `guard.checked` `resolution: unverified`, sortie gardée (normalisée si elle l'a été), `run.completed` statut `warning` et `unverified: true`. Réparations épuisées (19/10, contrat volontairement impossible : `must_match: 'ZZZ'` et `must_not_match: 'ZZZ'`, `repair.max_attempts: 1`, `--judges skip`) : 1re réponse → « motif attendu absent : ZZZ » `retry`, réparation ; Haiku ajoute « ZZZ » → « motif interdit présent : ZZZ (position 41, longueur 3) » (attempt 2). E4a `unverified` : réponse « … Canberra. ZZZ » gardée, `run.completed` statut `warning`, `unverified: true`, 2 itérations. E4b `fallback` (`fallback_message: "Réponse indisponible pour le moment."`) : `resolution: fallback`, `policy.decided` `replace` « message de repli », réponse livrée = le message de repli, `run.completed` statut `ok`, `unverified: false`. **Constats** : (1) le diagnostic de réparation d'un contrat ne cite que les règles violées, pas les autres (« motif interdit » inconnu de Haiku quand il ajoute ZZZ) — même mécanisme que le défaut du juge corrigé en E3 ; (2) un repli n'est signalé nulle part hors du journal : `run.completed` et le résultat ne le distinguent pas d'une vraie réponse. Corrigés en 2.0.10 : (1) le diagnostic d'un contrat ajoute « Le contrat exige aussi, et la sortie corrigée doit le garder : … » (règles `must_match`, `must_not_match`, `max_chars` non violées) ; (2) `run.completed.fallback` (statut `warning`, facette `fallback`), `RunResult.fallback`, REST et MCP (second texte « Réponse de repli : … »), ligne CLI « Repli : oui (…) » ; au passage, clôture d'un rôle terminal `unverified` corrigée et `skipped_judges` ajouté au résultat MCP. Revérifié (19/10, même contrat pour les deux runs : `must_match` et `must_not_match: 'ZZZ'`, `max_chars: 300`, `repair.max_attempts: 1`, `on_failure: fallback`) : E4b2 (capitale de l'Australie) — la réparation rappelle « aucun passage ne doit correspondre au motif ZZZ » et « 300 caractères au plus » ; Haiku signale la contradiction (« Je ne peux pas satisfaire les deux exigences à la fois… ») mais en citant « ZZZ » dans sa réponse, donc motif interdit → repli ; console « Repli : oui (…) », `run.completed` `warning`, `fallback: true`. E4c (Bastille en deux phrases, 414 caractères) — réparation avec les deux problèmes et le rappel du motif interdit ; 2e réponse à 270 caractères, sans « ZZZ » : longueur corrigée sans casser la règle tenue, seul reste le motif impossible → repli, mêmes marques.

### E5 — Contrats et juges sur n'importe quel rôle, outil ou réponse finale

**Catégorie** : Fiabilité des sorties

Les mêmes contrats et juges s'attachent à `main`, à un rôle ou à un outil Python (pas de juge sur un outil ni sur la référence d'un sous-agent).

**Testée** : oui (19/10, agent `agent_test`, sessions `session-E5a` et `session-E5b`, plus un `validate` refusé) ; contrat sur outil vu en E1e, juge sur la réponse finale en E3

**Note** : rôle `resumer` (HAIKU) avec `output: {max_chars, must_not_match}` et juge `fidelite` (OPUS, `repair.max_attempts: 0`, `on_failure: fail`). E5a (`max_chars: 300`, critère `fidele`) : `guard.checked` `role:resumer` `passed` (contrat), puis appel du juge (span du rôle, `call_id` du rôle), `judge.evaluated` `fidele` 1,0, `guard.checked` `passed` ; l'orchestrateur répond ; 0,0085 $. E5b (`max_chars: 60`, critère `court` « cinq mots au plus ») : contrat refusé (130 caractères) → réparé **par le modèle du rôle** (même `call_id`, diagnostic avec le rappel du motif interdit) → 47 caractères, `passed` ; puis juge : `court` 0,00 → `fail` → l'orchestrateur reçoit en erreur « Sortie refusée par le juge fidelite. Le juge fidelite refuse la sortie : … Critères déjà respectés, à garder … Corrige la sortie sur ces points et donne-la de nouveau en entier. ». **Défaut** (même mécanisme qu'en E1e pour le contrat) : l'orchestrateur, qui n'est pas l'auteur, reçoit la consigne de réparation ; Haiku rappelle le rôle avec les mêmes arguments (2e chaîne rôle + contrat + juge, refusée de même, +0,009 $), puis rédige le résumé lui-même, hors de tout contrôle (« Voici donc un résumé que j'ai rédigé moi-même »). 3 itérations, 0,021 $. Juge sur un outil (`tools[].judge`) : refusé au chargement, mais par « tools.1.python.judge — Extra inputs are not permitted », message peu explicite. Juge sur la référence d'un sous-agent : non testé (pas de second agent). Corrigés en 2.0.10 : (1) le refus du juge d'un rôle est dit à l'orchestrateur comme une règle (`refusal(..., by="son juge <nom>")`, sans consigne de réparation ni rappel des critères tenus) ; (2) `judge:` sur un outil Python, un serveur MCP, une source ou la référence d'un sous-agent est refusé par « … n'a pas de juge : un juge porte sur la réponse finale (judge: de l'agent) ou sur un rôle (roles[].judge) ; le résultat d'un outil se contrôle par son contrat (output:) ». Revérifié (session `session-E5b2`) : l'orchestrateur reçoit « Sortie du rôle resumer refusée par son juge fidelite (une règle de la configuration, pas une panne) : - court (0,00 < 0,80) : … ne rappelle pas ce rôle à l'identique. Dis à l'utilisateur que cette sortie n'a pas pu être validée. » ; il ne rappelle plus le rôle : 2 itérations au lieu de 3, un seul appel du juge, 0,0126 $ au lieu de 0,0213 $. Il le dit à l'utilisateur (« L'outil de résumé n'a pas pu valider sa sortie… »), mais donne ensuite un résumé rédigé lui-même, hors du juge. **Limite de conception, pas un défaut** : un juge sur un rôle contrôle la sortie du rôle, pas la réponse finale ; pour que la garantie tienne jusqu'à l'utilisateur, il faut un rôle `terminal` ou un juge (ou un contrat) sur la réponse finale.

### E6 — Alerte si le juge utilise le modèle qu'il évalue

**Catégorie** : Fiabilité des sorties

Contrôle de cohérence au chargement : un juge sur le même modèle (ou un secours commun) que la sortie jugée donne l'avertissement « juge corrélé » ; un juge bloquant échantillonné (`sample < 1`) est signalé aussi. En profil `prod`, ces avertissements sont des erreurs : le chargement s'arrête.

**Testée** : oui (19/10, agent `agent_test`, `loom validate` seulement : juge `fidelite` sur le rôle `resumer` HAIKU)

**Note** : E6a, juge HAIKU sur rôle HAIKU → WARNING « Agent 'agent', juge 'fidelite' : même modèle que la sortie qu'il évalue (HAIKU) : juge corrélé », agent monté ; avec `--profile prod` → « Configuration : Profil prod : Agent 'agent', juge 'fidelite' : même modèle … : juge corrélé », chargement refusé. E6b, juge OPUS avec `fallbacks: [HAIKU]` → même avertissement, venu du secours ; `validate` montre « modèle OPUS → HAIKU ». E6c, `when: {sample: 0.5}` → « … bloquant, mais ne juge qu'une partie des runs (sample 0.5) » (la variante `prod` n'a pas été lancée ; vue en profil normal en E3d). E6d, `tenants: dupont` avec `models: {OPUS: HAIKU}` → rien pour `default`, avertissement pour `dupont` seulement : la vérification se fait bien par client. **Constat** : le message ne nomme pas le client (« Agent 'agent', juge 'fidelite' : même modèle que la sortie qu'il évalue (OPUS et HAIKU) ») et « OPUS et HAIKU » se lit comme deux modèles différents alors que, pour ce client, OPUS est HAIKU ; dans `validate` le contexte le dit, mais pas dans les logs d'un serveur ni dans l'erreur `prod`. Corrigé en 2.0.10 : `judge_warnings(..., tenant=)` nomme un client déclaré et écrit la correspondance. Revérifié : `validate` → « Client 'dupont', agent 'agent', juge 'fidelite' : même modèle que la sortie qu'il évalue (juge OPUS → HAIKU, sortie HAIKU) : juge corrélé » sous « client dupont », rien pour `default` ; `--profile prod` → « Configuration : Profil prod : Client 'dupont', … juge corrélé », code de sortie 2. Ergonomie restante, mineure : sous « client dupont », la ligne du juge affiche « modèle OPUS » sans « → HAIKU » (la correspondance n'est dite qu'une ligne plus haut, « modèles : OPUS → HAIKU »). Corrigé en 2.0.10 : sous un client, « modèle OPUS (HAIKU pour ce client) », pour le modèle principal, les rôles et les juges ; « → » reste réservé aux secours.

### E7 — Politiques aux points d'accroche

**Catégorie** : Fiabilité des sorties

`policies:` : fonctions Python appelées à `before_model`, `after_model`, `before_tool`, `after_tool`, `on_output`, qui rendent une décision typée (laisser, remplacer, refuser, réessayer, pause) ; `timeout`, `on_error`, `max_attempts` ; politiques fournies comme `loom.require_tool`. Événement `policy.decided`.

**Testée** : oui (19/10, agent `agent_test`, sessions `session-E7a` à `session-E7f`)

**Note** : 19/10, agent `agent_test`, module de politiques (`plafond_ttc` before_tool replace/deny, `outils_max` before_model stop, `pas_de_cle` after_model fail, `sans_gras` on_output retry) ; `validate` les liste (« Politiques : … ») et les montre branchées. E7a (500 € HT à 19,6 %) : `policy.decided` `plafond_ttc` `replace` « taux de 19,6 % abrogé, remplacé par 20 % », `tool.called` garde 19,6 et l'outil reçoit 20 → 600 € ; le modèle lit « [Note du système : une politique a remplacé les arguments de ton appel avant exécution (champs : taux_tva). …] » **sans le motif** ; Haiku croit à son erreur (« J'ai utilisé 20 % au lieu de 19,6 % »), refait l'appel identique (remplacé de même), puis `outils_max` `stop` (2 appels) → réponse forcée (`tool_choice: none`) → `sans_gras` `retry` → réponse finale qui calcule 598 € à la main et dit que le résultat de l'outil « ne doit pas être utilisé » : l'inverse de l'intention de la politique ; 4 itérations. E7b (5000 €) : `deny`, résultat d'erreur « Appel refusé (plafond_ttc) : montant HT au-delà du plafond de 2000 € : devis à faire valider », outil non exécuté ; Haiku le dit puis calcule à la main (même limite qu'en E5) ; `sans_gras` `retry` puis réponse acceptée. E7c (trois devis) : trois appels parallèles, puis `outils_max` `stop` (3 appels ≥ 2) → `finalizing`, réponse forcée en tableau, 2 itérations. E7d : `pas_de_cle` `fail` → `run.failed` `policy.pas_de_cle` « réponse du modèle contenant une clé d'API ». **Défaut** : le motif d'un remplacement d'arguments par une politique n'est pas dit au modèle (il l'est pour une approbation) — corrigé en 2.0.10 (option A) : la note nomme la politique et son motif, et ajoute « un nouvel appel avec eux serait remplacé de même » ; journal de non-régression j3 réenregistré. Revérifié (session `session-E7a2`) : la note est bien « [Note du système : la politique plafond_ttc a remplacé les arguments de ton appel avant exécution (champs : taux_tva ; motif : taux de 19,6 % abrogé, remplacé par 20 %). … un nouvel appel avec eux serait remplacé de même.] », **mais le comportement de Haiku ne change pas** : il s'attribue encore le 20 (« parce que j'ai saisi 20 dans l'appel »), refait l'appel identique, est arrêté par `outils_max`, puis calcule 598 € à la main ; il cite cette fois la règle (« le taux de 19,6 % avait été remplacé par 20 % par une règle, mais je ne peux pas vérifier cette information ») et rappelle que 19,6 % n'est plus le taux normal depuis 2014. 4 itérations, 0,0013 $. Lecture : la demande explicite de l'utilisateur (19,6 %) l'emporte pour le modèle sur la politique ; loom transmet désormais l'information, le reste relève du modèle et de la conception de la politique (un changement de fond contraire à la demande se dit mieux par `Deny` avec motif que par `Replace`). Étape 2 (`policies: [loom.require_tool, en_panne]`, « Quel est le TTC de 100 € HT ? ») : `loom.require_tool` → `policy.decided` `replace` « aucun outil appelé dans le run : appel d'outil imposé », requête en `tool_choice: {type: any}` (HAIKU l'accepte), `prix_ttc` appelé au premier tour, puis `auto` au second. E7e (`on_error: block` par défaut) : `en_panne` lève `RuntimeError` à `on_output` → `policy.decided` `fail` statut `error`, `error: true` → `run.failed` `policy.en_panne` « politique en_panne en erreur : RuntimeError: panne simulée ». E7f (`on_error: allow`) : `policy.decided` `continue` statut `warning`, `error: true`, log WARNING, `run.completed` statut `ok` avec la réponse (« 120 € »).

---

## F. Mémoire et sessions

### F1 — Sessions persistantes

**Catégorie** : Mémoire et sessions

`--session <id>` (ou `session_id`) rattache plusieurs runs au même journal : le run suivant reprend la conversation.

**Testée** : oui (19/10, agent `agent_test`, série 1 (session `session-F1`, trois runs ; F1, F2, F4 et F8 vus dans le même journal))

**Note** : Run 1 « Calcule le TTC d'un devis de 800 € HT à 20 % » → 960 € ; run 2 « Et si on applique une remise de 10 % sur le HT ? », qui n'a de sens qu'avec le run 1 → Haiku reprend les 800 € HT, appelle `prix_ttc` sur 720 € → 864 € « (contre 960 € sans remise) » ; run 3 « Rappelle-moi le client et le dernier TTC… » → « Dupont » et « 864 € », 1 itération, sans outil. La requête du run 2 porte les 4 messages du run 1 (demande, appel, résultat, réponse) avant la nouvelle demande ; celle du run 3, les 8 des deux runs précédents.

### F2 — Historique structuré, sans aplatissement

**Catégorie** : Mémoire et sessions

L'historique LLM est une projection du journal d'événements (blocs neutres, appels et résultats d'outils), indépendante du fournisseur ; snapshots tous les `snapshot_every` événements.

**Testée** : oui (19/10, agent `agent_test`, série 1 (session `session-F1`, trois runs ; F1, F2, F4 et F8 vus dans le même journal), `sessions.snapshot_every: 20`)

**Note** : Historique envoyé en blocs, sans aplatissement : `user` (texte), `assistant` (`tool_use`), `user` (`tool_result`), `assistant` (texte), et ainsi de suite, reconstruits depuis le journal. Deux `session.snapshot` écrits en fin de run : `up_to_seq: 20` (4 messages, 233 tokens estimés) puis `up_to_seq: 41` (8 messages, 498 tokens). Le state de session (F8) n'entre pas dans l'historique : il est ajouté en fin de dernier message, à chaque requête.

### F3 — Compaction automatique

**Catégorie** : Mémoire et sessions

Au-delà de `over_tokens` (50 000 dans agent-01), un résumé par un modèle dédié (`HAIKU`) remplace l'ancien segment, les `keep_last` (2) derniers tours restent ; `blocking: true` l'attend en fin de run. Événement `session.compacted`, contrôle de fidélité lexical.

**Testée** : oui (19/10, agent `agent_test`, série 2 : session `session-F1` reprise avec `over_tokens: 400`, `keep_last: 2`, `blocking: true`)

**Note** : À l'ouverture de chaque run, un run `_compaction` (même session, `kind: compaction`, modèle HAIKU, prompt « Tu résumes les échanges anciens… ») reçoit le segment ancien mis en texte (« Utilisateur : … Agent appelle prix_ttc : {…} … »), puis `guard.checked` `fidelity` `passed` et `session.compacted`. 1er run de la série : `up_to_seq: 21` (run 1), `kept: 2`, 233 → 105 tokens, `fidelity: ok`, 0,00014 $. 2e run : `up_to_seq: 42` (runs 1 et 2), résumé glissant qui reprend le précédent (« Le devis concerne le client Dupont. Pour 800 € HT … 960 € … remise de 10 % … 720 € … 864 € »), 498 → 119 tokens, 0,00025 $. La requête de l'agent commence par « [Résumé des échanges précédents de cette conversation.] … » fusionné avec la demande du premier tour gardé, puis les 2 derniers tours intacts, puis la nouvelle demande. Réponses justes : TVA 10 % sur 720 € → 792 € ; récapitulatif complet (960, 864, 792, client Dupont) à partir du résumé, des tours gardés et du state. Le state n'est pas touché (`dernier_ttc: 792.0`). Ergonomie : rien ne dit qu'une compaction a eu lieu, ni la console de `loom run`, ni le déroulé `--stream` (`access/progress.py` n'a pas de ligne pour `session.compacted`) ; seul le journal le montre. Corrigé en 2.0.10 : `loom run` ajoute « Compaction : X → Y tokens estimés (jusqu'à l'événement N, K tour(s) gardé(s), fidélité vérifiée) », `--stream` « · session résumée : … » avant le premier tour (le flux livre le `session.compacted` de l'ouverture, pas le run de compaction) ; au passage, le résumé du filet `hard_tokens` et celui d'un run lancé sans `run_id` portent `triggered_by`. Non vus : `hard_tokens`, mode non bloquant (`blocking: false`).

### F4 — Nettoyage : thinking retiré, médias en références

**Catégorie** : Mémoire et sessions

Dans l'historique renvoyé au modèle, les blocs de raisonnement anciens sont retirés et les images remplacées par leur URI.

**Testée** : oui : raisonnement (19/10, agent `agent_test`, série 1 (session `session-F1`, trois runs ; F1, F2, F4 et F8 vus dans le même journal)) ; images (20/10, agent `agent-test`, séries G2 et G3 : références au journal oui ; un modèle vision revoit les images de toute la session par défaut — `sessions.media.keep_images: all` ; `last_run` les remplace par une note, testé, avec une limite pour les images d'outil)

**Note** : Run 2 : Haiku produit `reasoning` + `tool_call` ; dans la requête suivante du même run (boucle d'outils), le bloc `thinking` est renvoyé avant le `tool_use`, comme l'exige l'API. Run 3 : les tours du run 2 reviennent sans aucun `thinking` (`assistant` : `tool_use` seul, puis texte seul), y compris la réponse finale du run 2 qui portait `reasoning` + `text`. Le raisonnement ancien est donc retiré, celui de la boucle en cours gardé. Médias en références : non testés ici, faute d'image dans la session ; repris en G1. Images (`session-G2a`, HAIKU `vision`) : run 1 avec `chat.jpg`, run 2 sans pièce jointe (« De quelle couleur sont ses yeux ? ») → « Verts. ». Au journal, `message.user` du run 1 ne porte que l'URI (`artifact`), pas d'octets ; le run 2 n'a aucun `artifact.stored`. Mais la requête du run 2 reprend le tour 1 **avec le bloc `image` en base64** (capture : « [fichier image/jpeg, 168786 octets, sha256 7be78252…] »), puis la réponse du run 1, puis la nouvelle demande : `engine/media.py` résout toutes les références juste avant l'appel, tours anciens compris, pour un modèle `vision`. Le coût est ici amorti par le cache (run 2 : `cache_read` 3 040, `cache_write` 92, 0,0000 $), pas après l'expiration du cache (5 min) ni quand les images s'accumulent. La fiche (« images remplacées par leur URI dans l'historique renvoyé au modèle ») n'est donc vraie que pour l'historique stocké ; c'est la limite déjà écrite dans l'avancement (« un orchestrateur vision renvoie les images à chaque appel », cf. C4). Corollaire : l'image d'un tour ancien est relue dans le stockage à chaque run ; si elle a disparu, `_load` lève `ModelError invalid_request` (« Image … introuvable dans le stockage »). Corrigé le 20/10 (CHANGELOG 2.0.10, « Fichiers », ⚠ rejeu) : `sessions.media.keep_images`, `last_run` par défaut, remplace chaque image d'un tour précédent par une mention, sans relire le stockage ; `all` garde l'ancien comportement. Revérifié dans `session-G2a` : 3ᵉ run (« De quelle couleur sont ses yeux ? ») → la requête porte « [image chat.jpg (image/jpeg, 165 Ko), jointe à un tour précédent : plus montrée] » à la place du bloc `image`, aucun octet d'image ; « Verts. », repris de sa réponse du run 2 (qui avait vu l'image) ; 1 956 tokens mis en cache au lieu de 3 040. 4ᵉ run, fichier de l'artefact mis de côté (`mv`) : réussit (code 0), « Gris (tigré), d'après ma description précédente. La photo ne m'est plus affichée, donc je ne peux pas la revérifier. » — le modèle dit honnêtement d'où vient sa réponse ; 0,0002 $ (`cache_read` 1 956). Fichier remis en place après le test. Les deux réponses portent un bloc `reasoning` (Haiku, ≈ 240 tokens de raisonnement pour un mot), retiré de l'historique comme prévu. **Écart du correctif** (G3, `session-G3a`, 20/10) : pour une image rendue par un outil, la mention « … rendue par l'outil à un tour précédent : plus montrée » a fait dire à Haiku que l'image ne lui avait jamais été montrée et que sa description (juste) était inventée ; la mention ne dit pas que le modèle l'a vue à ce tour-là. 2ᵉ texte (« … rendue par l'outil à un tour précédent : elle n'est plus jointe à la conversation ; si tu l'as décrite alors, ta description reste valable », `session-G3c`, session neuve) : même désaveu — « l'image ne m'est pas accessible, donc ma description précédente (« aplat bleu ») était inventée ». La requête montre la cause : la mention **remplace le contenu du `tool_result`** ; vu du modèle, l'outil a rendu ce texte et rien d'autre, puis il a décrit une image — il ne lève la contradiction qu'en s'accusant d'avoir inventé. Le texte doit dire que ce résultat contenait l'image à ce tour-là et qu'elle a été retirée ensuite. 3ᵉ texte, note du système (« [Note du système : ce résultat contenait l'image etiquette-D-2026-045.png (image/png, 157 octets). Elle a été retirée de l'historique après ce tour pour alléger les requêtes ; si tu l'as décrite à ce tour-là, ta description venait de l'image elle-même et reste valable.] ») : pièce jointe (`session-G2b`) → « Chat. », juste ; image d'outil (`session-G3d`, note bien présente dans le `tool_result`) → « je n'ai pas vu l'image, et ma description précédente (aplat bleu) était inventée ». Trois textes, même désaveu pour une image rendue par un outil : décision (option 3 convenue) → `keep_images: all` redevient le défaut (comportement d'origine, rejeu inchangé), `last_run` reste au choix avec cette limite écrite dans la doc.

### F5 — Backends de session : mémoire, JSONL, SQLite, Postgres

**Catégorie** : Mémoire et sessions

`storage.events.backend` : `memory`, `jsonl` (agent-01, dossier `data/`), `sqlite`, `postgres`. Firestore : non réalisé.

**Testée** : oui

**Note** : série 3 (19/10), même agent (`agent`, Haiku, outil `prix_ttc`), un backend à la fois dans `storage.events`.

- a) **JSONL** (`data/`) : `sessions list` (29 sessions), `sessions show session-F1` (105 événements, 7 runs dont 2 `_compaction`), `sessions export … --out` (105 lignes = `data/default/session-F1.jsonl`), run sur `session-jetable` puis `sessions delete` avec confirmation [o/N] → « 9 événement(s), 0 fichier(s), 0 clé(s) », `sessions show` ensuite → « Session session-jetable inconnue », code 1 ; plus de fichier sur disque. OK.
- b) **SQLite** (`data/journal.db`, artefacts par défaut `data/.artifacts`) : run 100 € HT → 120 €, 21 événements, `export` sur stdout, `delete --yes`, `list` → « Aucune session. » ; table `events` vide vérifiée dans le fichier. OK.
- c) **Postgres** (conteneur `loom-pg`, port 15432 ; rôles `dba` propriétaire, `loom_login` connexion, `loom_app` applicatif ; artefacts `data-pg/artefacts`). `validate` : « Journal : postgres (DSN dans LOOM_PG : renseignée, rôle : loom_app) ». **Défaut trouvé et corrigé** : la sortie de `loom storage sql` (config en `postgres`) était le schéma 2.0 — `postgres_ddl()` (`runtime/wiring.py`) n'émettait que `EVENTS_DDL`, sans `loom_schema`, sans les migrations 2 et 3 (index `event_id` unique, colonne `ingested`), sans `GRANT SELECT ON loom_schema`. Base préparée ainsi par `dba` : au premier run, loom ne trouvait pas de version, tentait la migration avec `loom_login` → `PostgresNotPrepared` « permission denied for schema public », sorti deux fois en trace Python (compaction d'avant run en WARNING, puis `loom run`). Correctif (option A) : `loom storage sql` donne le schéma entier, identique à `ddl()` (test unitaire d'égalité), plus `GRANT SELECT ON loom_schema` au rôle applicatif ; `StorageNotReady` (`core/ports`) parent de `PostgresNotPrepared`, `PostgresMigrationFailed`, `RoleUnavailable` ; la compaction la laisse passer ; la CLI imprime « Stockage : … », code 2, sans trace. Vérifié sur Postgres 16 local : ancien SQL → l'erreur ; nouveau SQL → run OK ; base faite avec l'ancien SQL réparée en lui appliquant le nouveau (version 3). Chapitre 24 de `howto/03-production.md`, `fonctions.md` (journal en version 3) et CHANGELOG mis à jour. Relance après correctif (base recréée — `dba` n'a pas `CREATEDB`, recréation par le superutilisateur du conteneur —, `loom storage sql | psql -U dba`) : run 200 € HT → 240 €, sans WARNING ; `sessions list` 21 événements ; `loom_schema` events = 3 ; `UPDATE loom_events` en `loom_app` → « permission denied for table loom_events » (journal immuable par les droits) ; `delete --yes` → 21 événements supprimés ; `list` → « Aucune session. ». OK.
- d) **Mémoire** : run « Bonjour » sur `session-F5-mem` OK ; `sessions list` dans un autre process → « Aucune session. » : attendu, le journal mémoire vit et meurt avec le process (pas de persistance entre deux commandes `loom`). Ergonomie : rien ne le disait. Corrigé en 2.0.10 : `sessions list|show|files|export|delete` écrivent d'abord sur stderr « Journal en mémoire : il ne vit que le temps d'un process ; cette commande ne voit pas les runs des autres commandes loom. » (journal du client de la commande ; codes de sortie et stdout inchangés).
- Firestore : non réalisé (hors périmètre de la fiche).

### F6 — Mémoire long terme / RAG via `loom-notes`

**Catégorie** : Mémoire et sessions

Le serveur MCP `loom-notes` branché comme source d'outils : l'agent cherche et ajoute des notes.

**Testée** : oui (côté loom) ; défaut trouvé dans loom-notes, corrigé le 19/10

**Note** : série 4 (19/10). Serveur `notes` dans `loom.yaml` (stdio, `/home/denis/dev/loom-notes/.venv/bin/loom-notes-mcp`, `LOOM_NOTES_DATA_DIR: /home/denis/temp/agent_test/notes` — Qdrant embarqué dédié, la mémoire réelle du conteneur `qdrant` n'est pas touchée —, `connect_timeout: 30`, `search`/`add_text` à 180 s) ; agent : `- mcp: notes`, `include: [search, get, add_text]`, `required: true`. Chaque `loom run` démarre son propre serveur.

- F6a (`session-F6a`) : `notes__add_text` {texte, titre « Tarif déplacement Dupont », projet `agent_test`} → `doc_id` f22a472d…, 1 chunk, 5,2 s.
- F6b (`session-F6b`, session neuve, sans historique) : `notes__search` « déplacement Dupont montant » → la note, score 0,9876 ; `prix_ttc` {montant_ht: 45} → « 45 € HT … TTC de 54 € », en citant la note. La mémoire passe d'une session à l'autre par le RAG.
- F6c (`session-F6c`) : « tarif ramonage », « ramonage prix » → aucun résultat ; l'agent le dit, n'invente pas de tarif, propose d'ajouter la note « si vous me le demandez explicitement ».
- Côté loom : une erreur d'outil MCP revient au modèle comme erreur, le modèle réessaie puis le dit ; le stderr du serveur est recopié sur la console (voulu depuis D3). Rien à corriger.
- **Défaut loom-notes** (dépôt séparé) : le préchargement des modèles (`warmup_on_start`, en tâche de fond) et le premier appel d'outil, arrivé 2 s après le démarrage, chargent BGE-M3 en même temps (deux lignes « loading existing colbert_linear and sparse_linear ») ; `_load()` (`embed/bge_m3.py`, `embed/reranker.py`) n'a pas de verrou, et le chargement de transformers n'est pas sûr entre threads. Effets : « Cannot copy out of meta tensor » (F6a, premier essai : modèle cassé gardé en cache, les deux `add_text` échouent ; F6b : premier `search`), « type object 'tqdm' has no attribute '_lock' » (F6c : premier `search`). L'appel suivant passe quand le chargement de l'autre thread a gagné. Indépendant du device (vu sur GPU et sur CPU). Second défaut : `resolve_device("auto")` choisit `cuda` dès que `torch.cuda.is_available()`, même pour une GTX 1050 Ti (sm_61) que `torch 2.14+cu130` ne gère pas (≥ sm_75) ; contourné ici par `LOOM_NOTES_DEVICE: cpu`. Corrigé dans loom-notes (option B, avec le traitement des erreurs) : `LazyModel` (`embed/base.py`) charge sous verrou, une seule fois, fait tourner le modèle une première fois et ne le garde que si ce passage réussit ; l'usage passe aussi sous le verrou ; un échec devient `ModelLoadError` (« Chargement du modèle … impossible sur … : <cause>. Réessayer ; … »), rendue au client en `ToolError` sans trace, préchargement logué en une ligne, et l'appel suivant recharge. `resolve_device("auto")` compare la capacité de la carte aux architectures du build (règle de torch) et passe sur le CPU avec une ligne d'avertissement. 13 tests (`tests/test_models.py`), README (configuration, dépannage).
- Relance après correctif : `session-F6b2` (`LOOM_NOTES_DEVICE: cpu`) — un seul chargement, premier `notes__search` en `ok` (6,0 s : il attend le préchargement), note trouvée (0,99), 54 € ; `session-F6c2` — premier `search` en `ok` (6,3 s), rien trouvé, dit ; `session-F6d` (`LOOM_NOTES_DEVICE` retiré, `auto`) — une ligne « WARNING loom_notes: GPU NVIDIA GeForce GTX 1050 Ti (sm_61) non pris en charge par ce torch (2.14.0+cu130, architectures : sm_75, …) : modèles sur cpu (LOOM_NOTES_DEVICE pour forcer) », sans les avertissements CUDA de torch, recherche réussie du premier coup, 45 € HT. Plus aucune erreur « meta tensor » ni « tqdm _lock ».
- Suite (19/10, revue de loom-notes et de loom_v2) : écriture d'un document sans trou possible (chunks, puis chunks en trop, puis document), déduplication par projet, erreurs Qdrant et de calcul rendues sans trace (loom-notes) ; stderr d'un serveur stdio préfixé de son nom (loom_v2, CHANGELOG 2.0.10). Vérifié : `session-F6e`, même texte que F6a mais projet `test-dedup` → nouveau document 4d5bc598…, 1 chunk, `duplicate_of: null` (refusé avant le correctif, la note existant dans `agent_test`) ; `session-F6f`, même texte, même projet, titre « Doublon » → `duplicate_of` 4d5bc598…, 0 chunk, rien écrit (9 ms : l'empreinte est vérifiée avant tout calcul) ; lignes du serveur affichées `[notes] …`.

### F7 — Gestion des sessions : lister, exporter, supprimer (RGPD)

**Catégorie** : Mémoire et sessions

`loom sessions list|export|delete`, `GET/DELETE /v1/sessions…`, `Loom.sessions()`, `export_session()`, `delete_session()`. La suppression retire journal, fichiers et clés.

**Testée** : oui (CLI) ; non (REST, API Python)

**Note** : couverte par la série 3 de F5 (19/10), en CLI sur les trois backends durables. `sessions list` (nombre d'événements, dernière date), `sessions show` (runs, agent, statut, itérations, coût, « En attente »), `sessions export` (stdout ou `--out`, une ligne JSON par événement, nombre exact), `sessions delete` (confirmation [o/N], ou `--yes`) qui annonce événements, fichiers et clés d'idempotence supprimés ; après suppression, `show` → « Session … inconnue », code 1, et rien ne reste (fichier JSONL absent, table SQLite vide, `list` Postgres vide). Sous Postgres, `DELETE` reste permis à `loom_app` (effacement RGPD) quand `UPDATE` est refusé. Pas couvert : suppression d'une session **avec fichiers** et **avec clés d'idempotence** (les trois suppressions ont donné 0 et 0), `GET/DELETE /v1/sessions…` (REST) et `Loom.sessions()`, `export_session()`, `delete_session()` en Python — à faire avec les tests REST (catégorie N).

### F8 — State de session

**Catégorie** : Mémoire et sessions

Clés typées déclarées dans `state:` de l'agent (schéma, `from` d'un outil avec `path`/`value`/`on_missing`, `required`, `default`), posées par `loom run --state CLE=VALEUR` ou par un résultat d'outil, montrées à `main` par `context: [session_state]`, relues par `loom state <session>`. Limites 1 Kio / 4 Kio ; un state refusé à l'ouverture refuse le run (code 2).

**Testée** : oui (19/10, agent `agent_test`, série 1 (session `session-F1`, trois runs ; F1, F2, F4 et F8 vus dans le même journal), puis deux runs refusés en `session-F1b`)

**Note** : Agent : `state: {client: {schema: string, maxLength 80}, dernier_ttc: {schema: number, from: [{tool: prix_ttc, path: /montant_ttc}]}}`, `main.context: [session_state]`. `--state client=Dupont` au run 1 → `run.started.state` ; `prix_ttc` pose `dernier_ttc` (960, puis 864) dans `tool.completed.output.state`. Chaque requête finit par `<session_state> {"client":"Dupont","dernier_ttc":…} </session_state>` à jour (après le résultat d'outil dans la boucle). Run 3 répond depuis le state, sans outil. `loom state session-F1` → `{"client": "Dupont", "dernier_ttc": 864.0}`. Refus à l'ouverture, code 2, sans rien écrire (pas de journal `session-F1b`) : `--state inconnu=1` → « State refusé : inconnu : clé non déclarée dans 'state' » ; `--state 'dernier_ttc="abc"'` → « dernier_ttc : ne suit pas son schéma — doit être un nombre (reçu : du texte) ». Non vus : `required`, `default`, `value`, `on_missing`, limites de taille (couverts par les tests unitaires).

---

## G. Pièces jointes et artefacts

### G1 — Entrées validées : images

**Catégorie** : Pièces jointes et artefacts

`loom run --attach FICHIER` (répétable), envoi multipart en REST : format reconnu à la signature binaire (jamais au nom), taille (`execution.attachments.max_bytes`, 5 Mio) et nombre (`max_files`, 10) contrôlés avant le run. Seules les images jpeg, png, gif et webp sont acceptées ; PDF, audio et autres fichiers sont refusés (non réalisé).

**Testée** : en partie : image valide (17/10, `session-B9`, `session-B9b`) ; refus en CLI avec la politique par défaut et une politique restreinte (20/10, agent `agent-test`, séries 1 et 1b, `session-G1a` à `session-G1k`) ; envoi multipart REST et refus « annoncé ≠ contenu » renvoyés à N2

**Note** : `--attach chat.jpg` (127 540 octets) accepté, type `image/jpeg` reconnu (B9). Série 1 (fichiers dans `data/input_files/`, politique par défaut : 5 Mio, 10 fichiers, jpeg/png/gif/webp), tous refusés avec le code 2 et une ligne sur stderr : `grosse.jpg` (photo + 6 Mo de zéros) → « grosse.jpg : 6168786 octets, au-delà de la limite de 5242880 » ; `devis.pdf` → « format non reconnu (acceptés : image/jpeg, image/png, image/gif, image/webp) » (`sniff` ne connaît que les images : pas de message propre au PDF) ; `faux.png` (300 octets aléatoires) → même message, le nom n'est pas regardé ; 11 × `chat.jpg` → « 11 pièces jointes, au-delà de la limite de 10 » ; `vide.png` → « fichier vide » ; fichier absent → « Pièce jointe illisible : [Errno 2] … ». Les refus ont lieu avant tout écrit : aucun `session-G1a…f.jsonl`, aucun dossier d'artefacts ; et avant le montage de l'agent : pas de ligne `[notes]`, 1,6 s réel (serveur MCP non démarré). Extension qui ment (`ment.png`, copie de `chat.jpg`, `session-G1g`) : acceptée, `artifact.stored` `image/jpeg`, `name: ment.png`, rangée en `<sha256>.jpg` (même empreinte que `chat.jpg`), le message utilisateur ne porte que l'URI ; la requête envoie le texte, le bloc `image` (`image/jpeg`, point de cache), puis `<session_state>` ; description juste (chat tigré gris, de face). Dans la capture brute (`model.exchanged`), le base64 est remplacé par « [fichier image/jpeg, 168786 octets, sha256 7be78252…] ». Le CLI ne transmet pas de type annoncé : le refus « annoncé X, mais le contenu est Y » ne se voit qu'en REST/Python. Ergonomie (pour J1) : la console affiche « tokens : 19/43 » alors que l'usage compte aussi 3 046 tokens `cache_write` (l'image et le prompt mis en cache) ; le coût (0,0004 $) les inclut bien. Série 1b, `execution.attachments: {max_bytes: 200000, types: [image/png], max_files: 2}` (200 000 et non 100 000 : la taille est contrôlée avant le type, `chat.jpg` fait 168 786 octets) : `chat.jpg` → « image/jpeg non accepté (acceptés : image/png) » ; `grosse.png` (PNG 64×64 + 250 000 zéros) → « 250178 octets, au-delà de la limite de 200000 » ; 3 × `petit.png` → « 3 pièces jointes, au-delà de la limite de 2 » ; tous en code 2, sans journal (`session-G1h…j` absents). Témoin `petit.png` (PNG rouge 64×64, 178 octets, `session-G1k`) : accepté, « Rouge. », `artifact.stored` `image/png`. `types: [application/pdf]` → `loom validate` : « execution.attachments — types : application/pdf non pris en charge (images reconnues : …) », code 2 : `types` restreint, n'étend pas.

### G2 — Stockage d'artefacts référencés par URI

**Catégorie** : Pièces jointes et artefacts

`storage.artifacts` (mémoire ou local) : les fichiers vivent hors du journal, qui n'en garde que l'URI.

**Testée** : oui (17/10, sessions `session-A6`, `session-B9` ; 20/10, agent `agent-test`, `session-G1g`, `session-G1k`, `session-G2a`)

**Note** : pièce jointe rangée dans `data/.artifacts/default/session-B9/<sha256>.jpg` (backend `local` déduit du journal `jsonl`), `artifact.stored` (`origin: attachment`) au journal, et le message utilisateur ne porte que l'URI `artifact://default/session-B9/<sha256>.jpg`. Même mécanisme pour un résultat déporté en A6 (`origin` offload). Adressage par contenu : même fichier, même empreinte d'une session à l'autre. 20/10 : `chat.jpg` et sa copie `ment.png` donnent la même empreinte `7be78252…` dans `session-G1g` et `session-G2a`, rangées en `.jpg` (type lu, pas le nom) ; `petit.png` en `.png`. Le journal ne garde que l'URI, et la capture brute remplace le base64 par « [fichier image/jpeg, 168786 octets, sha256 …] ». `loom sessions files session-G2a` : « chat.jpg (image/jpeg, 165 Ko, attachment, run 01a15887-760d…) 7be78252….jpg », code 0. Depuis le correctif du 20/10 (`sessions.media.keep_images: last_run`), un run suivant de la session ne relit plus le fichier : vérifié avec l'artefact mis de côté, le run réussit (voir F4).

### G3 — Artefacts produits par les outils, récupérables

**Catégorie** : Pièces jointes et artefacts

Un fichier rendu par un outil est rangé et listé dans `RunResult.artifacts` ; `Loom.artifact(uri)` en Python, ressource `loom://artifacts/…` en MCP (pas de téléchargement par REST).

**Testée** : oui en CLI et en Python (20/10, agent `agent-test`, série 3, `session-G3a`, `session-G3b`) ; ressource MCP `loom://artifacts/…` renvoyée à N5

**Note** : outil `etiquette_devis` (rend un `Image` PNG 120×40 bleu, fabriqué en stdlib, cf. guide 03, ex. 18.2) ajouté à `tools/utils.py` et à l'agent ; HAIKU `vision` et `tool_result_media`. Run 1 (« Fais l'étiquette du devis D-2026-042. ») : 2 appels ; `artifact.stored` `image/png`, 157 octets, `name: etiquette-D-2026-042.png`, `origin: tool_output`, `call_id` de l'appel ; le `tool.completed` ne porte que la référence `artifact://default/session-G3a/3344912e….png` ; dans la requête suivante, l'image part **dans** le `tool_result` (bloc `image`, capture « [fichier image/png, 157 octets, sha256 3344912e…] »). L'empreinte est celle du guide (même PNG, adressage par contenu). Haiku décrit juste (« un simple aplat bleu, sans numéro visible ») et conseille de vérifier avant d'imprimer. `loom sessions files session-G3a --origin tool_output --out data/output_files` écrit `etiquette-D-2026-042.png` sous son vrai nom : sha256 `3344912e…` = celui de l'URI, identique octet pour octet à l'artefact, en-tête PNG, IHDR 120×40. Run 2 (« De quelle couleur est l'étiquette que tu viens de faire ? ») : le `tool_result` du run 1 revient avec la mention « [image etiquette-D-2026-042.png (image/png, 157 octets), rendue par l'outil à un tour précédent : plus montrée] » (correctif F4 du 20/10) ; **écart** : Haiku répond « l'image ne m'a pas été montrée […] Ma description « aplat bleu, sans numéro » était donc inventée, et je m'en excuse » — il désavoue une description juste, la mention se lisant comme « jamais montrée » (voir F4). Python (`session-G3b`, script `uv run python -` avec `load_env_file("loom.yaml")` puis `Loom.from_config`) : réponse juste (« un simple bloc bleu, sans texte visible ») ; `r.artifacts` et `r.produced` donnent la même ligne : `tool_output image/png 157 etiquette-D-2026-043.png artifact://default/session-G3b/3344912e….png` (même empreinte que G3a, autre session) ; `await loom.artifact(uri)` rend 157 octets, `b'\x89PNG\r\n\x1a\n'`. Sans `load_env_file`, le même script lève `ModelConfigError` (« la variable d'environnement ANTHROPIC_API_KEY est absente ou vide ») : la librairie ne lit pas `.env`, c'est voulu (`config/env_file.py`) ; ergonomie : le message ne le rappelle pas, alors que `loom run` lit le `.env` du projet.

---

## H. Exécution durable

### H1 — État du run sérialisable

**Catégorie** : Exécution durable

Le `RunState` se reconstruit entièrement en relisant le journal (`Loom.state()`).

**Testée** : oui (20/10, agent `agent-test`, série 1, `session-H3a`, `session-H3b`)

**Note** : Après un `kill -9` du process en plein `fetch_page`, tout l'état se relit dans le journal seul : `loom sessions show session-H3a` → le run `awaiting_tools`, 1 itération, 0,0001 $, « En attente : rien » ; `loom inspect` → « run inachevé : durées provisoires », étape 1 (HAIKU, 1 837 → 61 tokens dont 1 819 lus en cache, appel `fetch_page({"url": "https://httpbin.org/delay/10"})`), étape 2 ouverte (« outil fetch_page — 0 ms (ouvert) », arguments). En Python, `Loom.state(run_id, session_id=…)` après la reprise : `completed`, 2 itérations. Ergonomie : ni `sessions show` ni `inspect` ne disent qu'un run `awaiting_tools` sans pilote est à reprendre, ni ne donnent la commande `loom resume … --session …` (ils le font pour une approbation).

### H2 — Sauvegarde à chaque étape

**Catégorie** : Exécution durable

Chaque transition est écrite au journal avant de passer à la suivante : un arrêt brutal ne perd que l'étape en cours.

**Testée** : oui (20/10, agent `agent-test`, série 1, `session-H3a`, `session-H3b`)

**Note** : Journal de `session-H3a` au moment du `kill -9` (7 s après le lancement) : `run.started`, `message.user`, `run.claimed` (concession jusqu'à +60 s), `step.started` 1, `model.exchanged`, `model.responded`, `step.completed` 1, `run.transitioned ready_for_model → awaiting_tools`, `step.started` 2, `tool.called fetch_page` (seq 10) — puis rien. Seule l'étape en cours (l'appel de l'outil, sans `tool.completed`) est perdue ; l'appel au modèle, payé, est gardé et n'est pas refait à la reprise. Même chose dans `session-H3b`.

### H3 — Reprise après plantage

**Catégorie** : Exécution durable

`loom resume <run_id>` (ou `Loom.resume()`, `recover()` au démarrage d'un worker) reprend un run interrompu là où il en était. Test : tuer le process pendant un `fetch_page`.

**Testée** : oui (20/10, agent `agent-test`, série 1, `session-H3a`, `session-H3b`, `session-H3c`)

**Note** : Process tué (`kill -9`) pendant `fetch_page` (`https://httpbin.org/delay/10`, 10 s de réponse). `loom resume <run> --session session-H3a` 3 min après : nouveau `run.claimed` (autre `worker_id`), `step.started` 3, `tool.called` du **même** `call_id` (l'appel interrompu est refait : effet `none`), `tool.completed` 10,5 s plus tard, puis l'appel au modèle et `completed` ; 2 itérations au total (pas de second appel au modèle pour l'étape 1), réponse juste (JSON d'écho de httpbin), code 0. Un second `resume` sur le run terminé n'écrit rien (journal inchangé) et réaffiche le résultat, code 0. `session-H3b` : même crash, puis `Loom.recover(session_id="session-H3b")` 77 s après → `('01a15998-…',)` remis en file (file `asyncio`, piloté dans le process), `drain()`, `state` → `completed`, 2 itérations, même déroulé au journal (nouveau `run.claimed`, `tool.called` du même `call_id`). Refus pendant la concession (`session-H3c`, `resume` quelques secondes après le crash) : « Run 01a159ab-… : piloté par worker-44da3a84b82b, concession valable jusqu'à 2026-10-20T14:17:19+00:00 — ce run est mené ailleurs : réessayer après l'échéance », code 2, rien d'écrit (journal toujours à 10 événements, dernier `tool.called`). Ergonomie : l'échéance est donnée en UTC (14:17:19+00:00) alors que la console est à l'heure de Paris (16:17). Serveur MCP stdio du process tué : encore visible par `pgrep` juste après le `kill -9`, disparu 30 s après (et aucun reste des crashs précédents) — il s'arrête de lui-même à la fermeture de son entrée standard, pas d'orphelin durable.

### H4 — Validation humaine : pause, approuver, modifier, refuser

**Catégorie** : Exécution durable

Un appel en attente met le run en pause (code de sortie 1) ; `loom approve|reject <run_id> [--call] [--by] [--reason] [--arguments JSON]`, `POST /v1/runs/{id}/approve|reject`, élicitation en MCP, approbateur en ligne en Python.

**Testée** : oui (18/10, CLI, sessions `session-D10`, `session-H4a`, `session-H4b` ; 20/10, REST, MCP stdio, approbateur en ligne, sessions `session-H4r1` à `H4r3`, `H4m1`, `H4m2`, `H4p1`, `H4p2`) : CLI, REST, MCP (avec et sans elicitation) et approbateur en ligne oui ; correction d'arguments contraire à la demande et expiration : écarts, corrigés et revalidés

**Note** : `loom approve <run_id> --call <call_id> --session session-D10` 81 s après la pause : `approval.granted` (`by: null`, `reason: ""` faute d'options), un nouveau worker reprend le run (`paused → awaiting_tools`), l'outil s'exécute avec les arguments d'origine, puis le modèle conclut (`completed`, 3 itérations au total) ; la commande affiche « Accordé : <call_id> » et la réponse finale. Premier essai de refus (`session-D10b`) sans pause : `lire_devis` (bouchon) donne le client Dupont pour tout numéro, le modèle constate l'écart avec « Martin » et refuse d'envoyer de lui-même. Refus (`session-H4a`) : `loom reject … --by denis --reason "Client déjà relancé par téléphone"` → `approval.rejected` (by, reason journalisés), l'outil n'est pas exécuté (pas de `tool.called`), le modèle reçoit « Appel à envoyer_relance refusé : Client déjà relancé par téléphone », n'essaie pas de contourner et propose des suites. La demande porte `reason` (« outil déclaré à approbation obligatoire (effets : irreversible) »), `scope: approve` et `expire_at` (+24 h, défaut de `approval.expires_in`). Modification (`session-H4b`) : `loom approve … --arguments '{…}'` → `approval.granted` porte les arguments corrigés, l'outil s'exécute avec eux (69 caractères au lieu du message du modèle). Écarts relevés, puis traités (CHANGELOG 2.0.10) : (1) `tool.called` garde les arguments du modèle — c'est voulu (le rejeu compare à eux, `approval.granted` porte la correction) ; le vrai défaut était dans les évals (`received_arguments` ignorait la correction de l'approbateur), corrigé ; (2) l'appel réécrit dans l'historique n'était pas annoncé, et le modèle s'attribuait la correction (« Denis » « inventé ») : le résultat est désormais précédé, dans la requête seulement, d'une « Note du système » (idem pour une politique `before_tool`). Texte mis au point en trois essais réels avec Haiku : « Appel modifié par denis… » (`session-H4c`) → il s'attribue encore la signature ; « L'appel ci-dessus n'est pas celui que tu as écrit… » (`session-H4d`) → il croit qu'une autre version, invisible, est partie ; « denis a remplacé les arguments de ton appel… L'appel affiché ci-dessus porte déjà les arguments de denis : c'est exactement ce qui a été exécuté. Tes arguments d'origine n'ont pas été utilisés. » (`session-H4e`) → réponse juste : « le texte ci-dessus est bien celui qui est parti », plus d'auto-attribution. Code de sortie à la pause : 1, conforme à la doc. `loom sessions show` (ajoutée le 18/10, avec `PendingApproval.run_id`) : `session-H4a` → un run `completed`, « En attente : rien » ; `session-H4f` → le run `paused`, la demande `envoyer_relance` avec son échéance (+24 h) et la commande `loom approve … --session session-H4f` prête. Constat : un second run lancé dans `session-H4f` pendant la pause ne voit pas le run en attente (l'historique ne reprend que les runs racine terminés, choix documenté dans `history.py`) ; le modèle propose alors d'« envoyer une relance » déjà en attente d'approbation. Traité le 18/10 (CHANGELOG 2.0.10) : la requête d'un nouveau tour reçoit une « Note du système » qui nomme l'appel en attente et ses arguments. Vérifié en réel : « Relance Dupont pour le devis D-0048. » dans `session-H4f` → aucun appel d'outil, réponse « déjà en attente d'une décision humaine. Je ne l'envoie pas une seconde fois pour éviter un doublon ».

Outil MCP soumis à approbation par la config (20/10, agent `agent-test`, sessions `session-P6a` à `session-P6c`) : `- mcp: notes` avec `tools: { add_text: { approval: always } }`, `search` et `get` libres. P6a : run `paused` sur `notes__add_text`, ligne « En attente » avec la commande prête (`--session` compris) ; `approval.requested` (`warning`, motif « outil déclaré à approbation obligatoire (effets : irreversible) », arguments complets visibles) ; `loom approve … --by denis` → `approval.granted` `by: denis`, puis seulement `tool.called` et `tool.completed` (document écrit, 1 chunk), `completed`. P6b : `loom reject … --by denis --reason "tarif pas encore validé"` → `approval.rejected` `by: denis` avec le motif, aucun `tool.called`, le modèle reçoit « Appel à notes__add_text refusé à l'approbation par denis (motif : tarif pas encore validé). C'est une décision, pas une panne : ne refais pas cet appel ; … » et le dit sans réessayer. P6c : `notes__search` sans approbation, trouve la note de P6a. Ergonomie : un run en pause affiche `—` sur stdout (pas de réponse encore) ; `approve`/`reject` remontent l'agent et donc démarrent loom-notes (environ 6 s, modèles chargés par le préchargement) même pour un refus.

REST (20/10, agent `agent-test`, série 2a, `loom serve` sans clé d'API, sessions `session-H4r1` à `session-H4r3`) : `POST /v1/agents/agent/runs` (`message`, `session_id`) attend la fin du run et rend `paused` avec `pending_approvals` (`notes__add_text`). Accord (`H4r1`) : `POST /v1/runs/{id}/approve?session_id=…` `{"by":"denis","reason":"tarif validé"}` → `{"run_id":…,"calls":["toolu_…"]}` ; au journal `approval.granted` `by: denis`, motif, puis le run reprend **dans le process `serve`** (même `worker_id`), `tool.called`, `tool.completed` (document écrit), `completed` ; `GET /v1/runs/{id}` → « C'est noté : le document « Tarif horaire » est enregistré… ». Refus (`H4r2`) : `/reject` → `approval.rejected` (`by`, motif), aucun `tool.called`, résultat d'erreur « Appel à notes__add_text refusé à l'approbation… », réponse « Denis a refusé l'ajout, au motif que la majoration de 50 % le samedi n'est pas encore décidée. Je ne retenterai pas l'opération. » Correction sans `call_id` (`H4r3`) : 422 « arguments corrige un appel désigné : indiquer call_id », rien d'écrit, le run reste `paused` — refus voulu et clair. Avec `call_id` : `approval.granted` porte les arguments corrigés (95 €), `tool.called` garde ceux du modèle (90 €, voulu), la note est écrite à 95 €. **Écart** : la requête suivante réécrit l'appel (95 €) et place la « [Note du système : denis a remplacé les arguments de ton appel avant exécution (champs : text ; motif : tarif corrigé). L'appel affiché ci-dessus porte déjà les arguments de denis : c'est exactement ce qui a été exécuté. Tes arguments d'origine n'ont pas été utilisés.] » **en tête du `tool_result`** ; Haiku répond « le texte stocké indique 95 € HT et non 90 € HT comme demandé. L'erreur vient de mon appel, et la réponse de l'outil mentionne une modification faite par « denis » que je ne peux pas vérifier » et propose d'ajouter une note à 90 € — il défait la correction de l'approbateur. Au 18/10 (`session-H4e`, CLI) le même texte avait marché, mais la correction (une signature) ne contredisait pas la demande ; ici elle la contredit, et une note placée dans le résultat d'un outil se lit comme une donnée de l'outil, dont le modèle se méfie. Corrigé le 20/10 (CHANGELOG 2.0.10, « Approbations et politiques », ⚠ rejeu, `tests/regression/j3` réenregistré) : la note part dans un message `user` à elle, après les résultats du tour, nomme l'outil, et ajoute pour une approbation « C'est une décision de denis, qui valide les appels de cette application : ne propose pas de revenir à tes arguments d'origine ; si elle diffère de ce que l'utilisateur avait demandé, dis-lui que denis a corrigé l'appel. ». Revalidé en CLI : correction contraire à la demande (`session-H4r4`, 80 € demandés, 85 € accordés) → « Vous aviez demandé 80 € HT. Denis a corrigé l'appel avant son exécution, et c'est ce texte-là qui a été enregistré. », ni excuse ni proposition de revenir à 80 € ; correction qui précise la demande (`session-H4r5`, « pièces et main-d'œuvre » ajouté) → « Denis a corrigé l'appel avant exécution : le texte enregistré est […], ce qui ajoute une précision à votre formulation initiale. », sans s'attribuer l'ajout. Requêtes : `tool_result` intact (le JSON de l'outil seul), puis le texte de la note, puis `<session_state>`.

MCP stdio (20/10, série 2b, client `mcp` 1.30 lancé par `uv run python -`, `uv run loom mcp` en sous-process) : outils publiés `agent, run_status, run_report, cancel` (pas d'`approve`). Avec elicitation (`session-H4m1`) : formulaire reçu « Approbation demandée pour l'outil notes__add_text. / Arguments : {…} / Motif : outil déclaré à approbation obligatoire (effets : irreversible) », réponse `accorder` + motif → `approval.granted` `by: mcp:mcp` (nom du client du SDK), motif « validé dans le formulaire » ; le run **ne passe pas par `paused`** (transitions `ready_for_model → awaiting_tools → ready_for_model → completed`), note écrite, `isError: False`. Sans elicitation (`session-H4m2`) : l'outil rend la main aussitôt, sans erreur, texte « Run … en attente d'approbation : notes__add_text (toolu_…). Un humain doit trancher (API REST ou « loom approve »), puis run_status relit le run (run_id=…, session_id=…) », résultat structuré `status: paused` + `pending_approvals` complet (arguments, motif, `expire_at`, `run_id`) ; `run_status` relit le même texte ; `loom reject … --by denis --reason "test MCP"` → `approval.rejected`, aucun `tool.called`, réponse « Le refus vient de l'approbation de l'écriture, par denis, avec le motif « test MCP ». Je ne réessaie pas. »

Approbateur en ligne Python (20/10, série 2c, `loom.run(…, approver=fn)`) : la fonction reçoit le `PendingApproval` (outil, arguments) ; `Approved(by="script", reason=…)` (`session-H4p1`) → `approval.granted` `by: script`, note écrite ; `Rejected(by="script", reason=…)` (`session-H4p2`) → `approval.rejected`, aucun `tool.called` ; dans les deux cas pas de `paused`, `pending_approvals` vide, `completed`. Nuance : en H4p2, Haiku dit « je n'ai pas retenté » mais propose « Si tu veux, je peux réessayer » — il ne le fait pas de lui-même, la consigne « ne refais pas cet appel » tient.

Expiration (20/10, série 2d, `approval: {expires_in: 60}` dans `agents/agent.yaml`, `on_expiry: deny` par défaut) : `session-H4x1` lancée avant l'ajout de la ligne (échéance +24 h, accord à +100 s normal, rien de neuf). `session-H4x2` : `approval.requested` `expire_at` = 15:10:21Z (+60 s) ; `loom approve … --by denis` à 15:11:06Z, **45 s après l'échéance** → « Accordé », `approval.granted`, l'outil s'exécute (note « Tarif vitrerie » écrite), `completed`, code 0. **Écart** : une demande périmée reste approuvable. Cause : `Loom._decided` (`access/api.py`, commun à `approve`/`reject`, CLI et REST) écrit la décision sans regarder `PendingApproval.stale()` ; l'expiration n'est appliquée que par le pilote (`_resumed`, `engine/loop.py`) quand il regarde un run en pause, et le réveil différé (`expire_approval`) a été perdu avec le process `loom run` (file `asyncio`).
Corrigé le 20/10 (CHANGELOG 2.0.10, « Approbations et politiques ») : `approve`/`reject` refusent une demande périmée (`ApprovalStale`, code 2 en CLI, 409 en REST) sans rien écrire. Revalidé (`session-H4x5`, `expire_at` à +60 s, `approve` à +75 s) : « Demande refusée : Run … : la demande d'approbation de notes__add_text (appel toolu_…) a expiré le 2026-10-20 à 17:39:22 (CEST) ; elle ne peut plus être tranchée. Reprendre le run (loom resume … --session session-H4x5, Loom.resume ou recover()) le conclut selon approval.on_expiry », code 2, avant même le montage de l'agent (pas de ligne `[notes]`), rien d'écrit ; `loom resume` → `approval.expired` (`action: deny`), résultat d'erreur sans `tool.called`, `completed`, réponse « L'ajout n'a pas été approuvé à temps, et je ne le relance pas sans votre accord. » (note non écrite). (`session-H4x3` et `session-H4x4` : lancés sans `expires_in`, échéance +24 h, accords normaux.)

### H5 — Runs en arrière-plan

**Catégorie** : Exécution durable

`Loom.submit()` ou `background: true` en REST (réponse 202) : le run tourne à part ; statut par `GET /v1/runs/{id}`, outil MCP `run_status`.

**Testée** : oui (20/10, agent `agent-test`, série 3, `session-H5a`, `session-H5b`)

**Note** : REST (`loom serve`, sans clé) : `POST /v1/agents/agent/runs` avec `background: true` → `HTTP/1.1 202 Accepted`, corps `{"run_id":…,"session_id":"session-H5a","status":"ready_for_model"}` ; le run est au journal dès la réponse : `GET /v1/runs/{id}?session_id=…` aussitôt → `ready_for_model`, 10 s après → `completed`, « le montant TTC est de 960 € (TVA : 160 €) » ; `GET /v1/runs?session_id=…` le liste. Python : `Loom.submit("agent", …, session_id="session-H5b")` rend l'identifiant tout de suite, `state()` aussitôt → `ready_for_model` (run inscrit avant le retour), `drain()` puis `result()` → `completed` (1 200 € HT à 10 % = 1 320 € TTC, refusé par le contrat de sortie de `prix_ttc`, `montant_ttc ≤ 1000` — choix du montant malheureux, comportement du contrat conforme à E1). MCP : `run_status` (stdio, sans elicitation) sur le run REST → `isError: False`, texte = réponse du run, `status: completed`.

### H6 — Déclencheurs : webhook, planification

**Catégorie** : Exécution durable

`triggers:` (`name`, `agent`, gabarit `message`, gabarit `session`, `delivery_header`) : `POST /v1/hooks/{name}` ouvre un run (202, ou 200 si la livraison est déjà reçue). Un planificateur externe passe par la même porte.

**Testée** : oui (20/10, agent `agent-test`, série 4, `loom serve` sans clé, sessions `devis-D-2026-077` à `devis-D-2026-081`) : livraison, doublon, tournée datée, refus ; écart sur un identifiant de livraison réutilisé avec une autre charge, corrigé et revalidé

**Note** : déclencheur `devis-signe` (`agent`, message « Le devis {{ payload.numero }} du client {{ payload.client }} est signé : calcule son TTC pour {{ payload.montant_ht }} € HT. », session « devis-{{ payload.numero }} », `delivery_header: X-Delivery-Id`). Livraison `X-Delivery-Id: crm-0001` → `202 Accepted`, `{"trigger":"devis-signe","run_id":"crm-0001","session_id":"devis-D-2026-077","status":"ready_for_model","repeated":false}` : l'identifiant de livraison devient le `run_id`, la session est rendue depuis la charge. Même livraison renvoyée → `200 OK`, même run, `repeated: true`, un seul `run.started` au journal. Run lancé en arrière-plan, `completed` : « Le devis D-2026-077 (client Martin) s'élève à 720,00 € TTC pour 600,00 € HT ». Au journal : `run.started` avec `trigger: devis-signe` (aussi en facette), `message.user` = le gabarit rendu (« Le devis D-2026-077 du client Martin est signé : calcule son TTC pour 600 € HT. »). Tournée « cron » (`X-Delivery-Id: tournee-$(date +%F)`) : `202` puis `200`, run `tournee-2026-10-20` dans `devis-D-2026-078`. Déclencheur inconnu → `404`, « Déclencheur 'inconnu' non déclaré (déclencheurs : devis-signe) ».
**Écart** : même `X-Delivery-Id: crm-0001` avec une autre charge (`numero: D-2026-079`) → `202`, `repeated: false`, second run `crm-0001` ouvert dans `devis-D-2026-079` (exécuté). Le doublon n'est cherché que dans la session rendue (`Loom._already`, `access/api.py`) : deux runs portent le même identifiant, et `GET /v1/runs/crm-0001` sans `session_id` répond « Run crm-0001 inconnu ». Une plateforme qui renvoie une livraison corrigée, ou qui recycle ses identifiants, déclenche donc l'action deux fois.
Corrigé le 20/10 (CHANGELOG 2.0.10, « Déclencheurs ») : pour une porte avec `session` et `delivery_header`, l'identifiant est cherché dans tout le journal du client, et refusé (422, `DeliveryRefused`) s'il est déjà pris dans une autre session. Revalidé (`serve` relancé, `crm-0002`) : devis D-2026-081 → `202` ; même identifiant, devis D-2026-082 → `422` « Identifiant de livraison 'crm-0002' déjà utilisé par un run du déclencheur 'devis-signe' (session devis-D-2026-081) : une même livraison doit donner la même session, et celle-ci donnerait devis-D-2026-082 », aucune session `devis-D-2026-082` ; charge d'origine renvoyée → `200`, `repeated: true`, `status: completed`. Même trou, hors H6, à voir en A et N2 : un `run_id` imposé par l'appelant (`--run-id`, `run_id` en REST) n'est contrôlé que dans sa session.

### H7 — Worker de file de tâches

**Catégorie** : Exécution durable

`loom worker [--jobs N]` consomme la file (`storage.queue` : asyncio ou RabbitMQ) : runs de fond, reprises, résumés de compaction. Demande RabbitMQ pour le cas multi-process.

**Testée** : oui (21/10, agent `agent-test`, série 5, RabbitMQ 4 en conteneur, `storage.queue: {backend: rabbitmq, url_env: AMQP_URL}`, `loom worker --jobs 2`, sessions `session-H3c`, `session-H7a` à `session-H7e`) : runs de fond, reprise au démarrage, worker tué puis relancé, reprise après approbation ; écart d'affichage de `approve` corrigé et revalidé

**Note** : démarrage : `File : rabbitmq (URL dans AMQP_URL : renseignée)`, rappel du `consumer_timeout` du courtier (30 min), `Reprise : 1 run(s) remis en file` — le run de `session-H3c`, tué la veille et jamais repris : nouveau `run.claimed` (worker du jour), `fetch_page` refait (même `call_id`), `completed`. Run soumis depuis un autre process (`session-H7a`, `Loom.submit` puis fin du process) : `soumis : … | ready_for_model`, puis mené par le worker (`run.claimed` de son `worker_id`), `completed` en 2 s. Worker tué (`kill -9`) pendant `fetch_page` (`session-H7c`) et relancé aussitôt : `Reprise : 1 run(s) remis en file` ; le run n'est repris qu'**à l'échéance de la concession** de l'ancien worker (`run.claimed` à 05:47:57 pour une concession valable jusqu'à 05:47:56), par le nouveau worker seul, `fetch_page` refait une fois (même `call_id`), `completed` — la redélivrance RabbitMQ et le `recover()` du démarrage n'ont pas donné deux pilotes. Reprise après approbation (`session-H7b`) : le `loom run` lancé à côté du worker a échoué à l'ouverture (`tool.source_unavailable`, 0 itération) : loom-notes en Qdrant local (`/home/denis/temp/agent-test/notes/qdrant`) ne s'ouvre que dans un process à la fois (« Storage folder … is already accessed by another instance of Qdrant client. If you require concurrent access, use Qdrant server instead. »), et le worker tient déjà le sien — limite de loom-notes en stockage local, pas de loom ; le message de loom (source requise indisponible, fin du stderr du serveur) est clair. Refait sans loom-notes (`session-H7d`, bloc `- mcp: notes` commenté, `prix_ttc` en `approval: always`, worker relancé) : `loom run` → `paused` ; `loom approve … --by denis` → `approval.granted`, puis la reprise est menée **par le worker** (`run.claimed` d'un autre `worker_id` 0,7 s après l'accord), `prix_ttc`, `completed` (« le montant TTC est de 360 € »). **Écart d'affichage** : la commande `approve` affiche « Accordé : toolu_… », puis « — », « Statut : paused » et sort en **code 1** — elle attend la reprise dans son propre process (`loom.drain()`), alors qu'avec un courtier la reprise est partie au worker : `drain()` rend la main aussitôt et le résultat relu est encore celui de la pause (`cmd_decide`, `access/cli/main.py`). Corrigé le 21/10 (CHANGELOG 2.0.10, « Ligne de commande ») : avec un courtier, `approve`/`reject` n'attendent plus et disent à qui la reprise est confiée. Revalidé (`session-H7e`, worker relancé) : « Accordé : toolu_… » puis « Reprise confiée au worker (file rabbitmq) : suivre avec loom sessions show session-H7e », code 0, aucune ligne « Statut » ; 5 s après, `completed` (reprise menée par le worker). Ergonomie : avec une file servie par un courtier et des artefacts `local`, **chaque** commande `loom` (même `sessions show`) répète l'avertissement « Stockage : file servie par un courtier et artefacts 'local' … » sur stderr.

---

## I. Streaming et événements

### I1 — Flux d'événements typés pendant le run

**Catégorie** : Streaming et événements

`run.started`, `message.user`, `model.responded`, `tool.called`, `tool.completed`, `guard.checked`, `run.completed`… avec `seq`, `span_id`, catégorie, statut.

**Testée** : non

**Note** :

### I2 — Consommation en Python (itérateur async) et en HTTP (SSE)

**Catégorie** : Streaming et événements

`Loom.stream()` / `Loom.follow()` en Python ; `GET /v1/runs/{id}/events` en SSE (`after_seq`, `subruns`).

**Testée** : non

**Note** :

### I3 — Latence adaptée à la voix

**Catégorie** : Streaming et événements

Premier token rapide, délais `first_token` et `idle` sur le streaming. Mesurable avec un vrai modèle (jamais mesuré en réel).

**Testée** : non

**Note** :

### I4 — Bus d'événements entre process

**Catégorie** : Streaming et événements

`storage.bus` : mémoire, Redis ou Postgres ; un client SSE servi par un process suit un run mené par un autre, resynchronisé par `seq`.

**Testée** : non

**Note** :

---

## J. Coûts et budgets

### J1 — Comptage des tokens

**Catégorie** : Coûts et budgets

Entrée, sortie, cache (lecture/écriture), raisonnement, relevés sur chaque `model.responded`.

**Testée** : non

**Note** :

### J2 — Coût calculé par grille tarifaire

**Catégorie** : Coûts et budgets

`pricing:` du modèle (agent-01 : input 0.1, output 0.5, cache_read 0.01, cache_write 0.125) appliqué aux tokens.

**Testée** : non

**Note** :

### J3 — Ventilation par run, rôle, modèle, session, client

**Catégorie** : Coûts et budgets

Le ledger, projection du journal, répartit la consommation selon ces axes.

**Testée** : non

**Note** :

### J4 — Plafonds par run, session, client et période

**Catégorie** : Coûts et budgets

`budget:` (`max_cost`, `max_tokens`, `max_calls`) avec action avertir ou arrêter ; quotas par client sur le jour ou le mois. Contrôlé avant chaque appel de l'orchestrateur.

**Testée** : non

**Note** :

### J5 — Rapport de consommation

**Catégorie** : Coûts et budgets

`loom report [run_id] [--session] [--period day|month] [--json]`, `GET /v1/sessions/{id}/report`, outil MCP `run_report`, `Loom.report()`.

**Testée** : non

**Note** :

---

## K. Observabilité et rejeu

### K1 — Trace hiérarchique

**Catégorie** : Observabilité et rejeu

Arbre run → étape → appel modèle / outil / validation / sous-run, chaque nœud avec parent, durée, statut, coût : `loom inspect <run_id> [--full] [--json]`, `GET /v1/traces/{run_id}`.

**Testée** : non

**Note** :

### K2 — Schéma d'événements stable et versionné

**Catégorie** : Observabilité et rejeu

`schema_version` sur chaque événement (1 aujourd'hui) : le contrat pour une interface visuelle.

**Testée** : non

**Note** :

### K3 — Capture des contenus et masquage

**Catégorie** : Observabilité et rejeu

`telemetry.capture` règle ce qui est gardé (`raw_exchanges: true` dans agent-01 garde les requêtes brutes) ; masquage des données sensibles ; contenu masqué en REST sans la portée `read_content`.

**Testée** : non

**Note** :

### K4 — Exports : JSONL, OpenTelemetry

**Catégorie** : Observabilité et rejeu

Journal JSONL exportable (`sessions export`, `replay --export`), export des spans en OTLP (extra `otel`). DuckDB : non couvert.

**Testée** : non

**Note** :

### K5 — API de lecture des traces

**Catégorie** : Observabilité et rejeu

`GET /v1/runs` (filtres `agent`, `status`, `since`, `until`, `limit`), `GET /v1/runs/{id}`, `GET /v1/events` (filtres par type, outil, modèle…), `Loom.runs()`, `Loom.query()`, ressources `loom://runs…`.

**Testée** : non

**Note** :

### K6 — Rejeu exact ou en variante

**Catégorie** : Observabilité et rejeu

`loom replay <run_id> [--mode exact|variant] [--model ETAPE=MODELE] [--double OUTIL=REF] [--journal FICHIER] [--export]` : à l'identique sans appel (divergence détectée par `request_hash`), ou avec un autre modèle/prompt ; les outils à effet de bord ne sont jamais réexécutés.

**Testée** : non

**Note** :

### K7 — Logs techniques séparés des traces

**Catégorie** : Observabilité et rejeu

`telemetry.logging` (niveau `WARNING` dans agent-01) : `logging` de la stdlib, distinct du journal d'événements.

**Testée** : non

**Note** :

---

## L. Multi-clients

### L1 — Espace isolé par client

**Catégorie** : Multi-clients

`tenants:` : par client, surcharges (liste fermée), outils, sessions, budgets, traces ; `--tenant` à la CLI, client tiré de la clé d'API en REST.

**Testée** : non

**Note** :

### L2 — Secrets par client, jamais dans les logs

**Catégorie** : Multi-clients

Chaque client a sa table de secrets (clés de modèle, de source d'outils) ; les valeurs ne sortent ni dans les logs ni dans les erreurs.

**Testée** : non

**Note** :

### L3 — Quotas et limitation de débit par client

**Catégorie** : Multi-clients

Quotas de consommation par période et `--rate-limit` par clé d'API (requêtes par minute).

**Testée** : non

**Note** :

---

## M. Configuration

### M1 — Configuration déclarative validée au démarrage

**Catégorie** : Configuration

`loom.yaml` + `agents/*.yaml`, validés par Pydantic avec des erreurs qui nomment la clé ; `loom validate [--no-keys]` ; `loom schema` imprime le JSON Schema pour l'éditeur.

**Testée** : non — écart trouvé le 17/10

**Note** : **clé en double acceptée sans erreur** : PANNE portait deux `retry` dans `loom.yaml` ; `yaml.safe_load` garde la dernière, et ni le chargement ni `loom validate` ne le signalent. Une faute de frappe peut ainsi annuler un réglage en silence. Piste : un chargeur YAML qui refuse les clés en double, en donnant la ligne.

### M2 — Construction entièrement en Python

**Catégorie** : Configuration

Construire `LoomConfig` et les agents en code, sans fichier, puis `Loom.from_config()` ; `Loom.register()` pour un outil.

**Testée** : non

**Note** :

### M3 — Secrets par l'environnement ou un gestionnaire

**Catégorie** : Configuration

La config ne nomme que la variable (`api_key_env: ANTHROPIC_API_KEY`) ; `SecretProvider` pour un gestionnaire de secrets.

**Testée** : non

**Note** :

### M4 — Profils dev/prod et surcharges

**Catégorie** : Configuration

`profile:` et `profiles: {dev, prod}` fusionnés au chargement ; `--profile` ou `LOOM_PROFILE` l'emportent. `prod` durcit : `serve --reload` et `judges: skip` refusés ; erreurs au lieu d'avertissements pour une approbation sans journal durable, un budget en dollars sur un modèle sans tarif, un juge corrélé ou bloquant échantillonné. `dev` assouplit (approbation avec un journal en mémoire : simple avertissement).

**Testée** : non

**Note** :

### M5 — Contrôles de cohérence

**Catégorie** : Configuration

Rôle inconnu, modèle absent, outil introuvable, juge corrélé, variable de prompt non déclarée, source `from` invalide : refusés au chargement (code 2).

**Testée** : non

**Note** :

### M6 — Fichier `.env` lu au démarrage

**Catégorie** : Configuration

`loom` lit, du plus fort au plus faible : `--env-file`, `env_file:` de `loom.yaml`, `.env` voisin de `loom.yaml` ; `--no-env-file` n'en lit aucun. Une variable déjà définie (même vide) l'emporte ; pas d'interpolation ; les valeurs n'apparaissent jamais. La librairie ne lit rien seule (`load_env_file()`).

**Testée** : non

**Note** :

### M7 — Variables `{{ }}` dans les prompts

**Catégorie** : Configuration

`{{ entreprise }}` dans un prompt, valeur fournie à part (par client) ; une variable non déclarée est une erreur de chargement ; le prompt rendu se vérifie dans les échanges bruts.

**Testée** : non

**Note** :

---

## N. Accès

### N1 — Librairie Python async et typée

**Catégorie** : Accès

`Loom.from_config()`, puis `run`, `stream`, `submit`, `resume`, `cancel`, `approve`, `reject`, `replay`, `evaluate`, `trace`, `report`, `sessions`, `session_state`, `aclose`…

**Testée** : non

**Note** :

### N2 — Serveur HTTP REST

**Catégorie** : Accès

`loom serve [--host] [--port]` (extra `http`) : 16 routes `/v1/…` (agents, runs, hooks, events, traces, sessions, rapports), documentation sur `/docs`.

**Testée** : non

**Note** :

### N3 — Authentification par clé d'API

**Catégorie** : Accès

`loom keys create <id> [--scope run|read|read_content|approve|admin] [--agent] [--tenant] [--expires] [--rate-limit]` fabrique une clé et son empreinte à déclarer ; sans clé valide, 401.

**Testée** : non

**Note** :

### N4 — CLI

**Catégorie** : Accès

`validate`, `run`, `state`, `resume`, `approve`, `reject`, `replay`, `inspect`, `eval`, `report`, `sessions`, `serve`, `mcp`, `keys`, `worker`, `retention`, `storage sql`, `schema` ; codes de sortie 0 / 1 / 2.

**Testée** : non

**Note** :

### N5 — Serveur MCP (stdio et HTTP)

**Catégorie** : Accès

`loom mcp` (stdio) ou `server.mcp.http` monté dans `serve` : un outil par agent, plus `run_status`, `run_report`, `cancel` ; ressources `loom://…`. À brancher sur Claude Desktop.

**Testée** : non

**Note** :

### N6 — Rechargement du serveur en développement

**Catégorie** : Accès

`loom serve --reload` relance le serveur dans un nouveau process quand un fichier de la config change ; rien ne se recharge dans le process vivant. Si la nouvelle config ne se charge pas, l'ancien process continue de servir. Refusé en profil `prod` (code 2).

**Testée** : non

**Note** :

---

## O. Qualité

### O1 — Runner d'évals

**Catégorie** : Qualité

`loom eval <suite.yaml> [--case] [--variant] [--export]` : cas, attendus (dont `expect.state`), variantes de modèle ou de config, rapport comparatif.

**Testée** : non

**Note** :

### O2 — Faux modèles et faux outils

**Catégorie** : Qualité

`sdk: fake`, `loom_ia.testing` (`Bench`, faux modèle scripté, doublures d'outils) pour des tests déterministes sans appel réel.

**Testée** : non

**Note** :

### O3 — Non-régression depuis des traces enregistrées

**Catégorie** : Qualité

Les journaux JSONL exportés servent de corpus : rejeu exact et jeu en simulé (`tests/regression/`), divergence = échec.

**Testée** : non

**Note** :

---

## P. Données sensibles

### P1 — Chiffrement du journal et des fichiers

**Catégorie** : Données sensibles

`storage.encryption` (extra `crypto`, AES-GCM) : contenus scellés, une clé par client ; masqués aussi pour qui n'a pas la clé.

**Testée** : non

**Note** :

### P2 — Rétention : effacement des sessions dormantes

**Catégorie** : Données sensibles

`storage.retention` déclare la borne ; `loom retention [--tenant] [--yes] [--json]` liste ce qui serait effacé, et n'efface qu'avec `--yes`.

**Testée** : non

**Note** :

### P3 — Isolation des clients en base Postgres

**Catégorie** : Données sensibles

Journal Postgres avec rôle et politiques par client ; `loom storage sql` imprime le SQL (tables, rôle, politiques) à appliquer. Demande un serveur Postgres.

**Testée** : non

**Note** :
