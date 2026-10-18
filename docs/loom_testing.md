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

**Note** : serveur `loom-fs-mcp` (FastMCP, stdio) déclaré `mcp_servers: [{name: fs, transport: stdio, command: …/.venv/bin/loom-fs-mcp, args: [--config, …/roots.toml]}]`. Les outils arrivent préfixés `fs__…`, avec la description et le schéma du serveur (`additionalProperties: false` ajouté) ; `tool_kind: mcp`. `fs__list_roots` rend les 5 racines. Trois appels dans le même lot (`list_roots`, `read_file` de `agent-01/loom.yaml`, `read_file` de `agent-01/.env`) : la lecture réussit, le `.env` revient en résultat d'erreur avec le message du serveur (« matches deny pattern '.env' »), le run continue et le modèle en rend compte. `alias: f` (`session-D3c`) : outils `f__…` ; l'ancienne référence `- mcp: fs` étant restée, le même serveur est exposé deux fois (`fs__…` et `f__…`, 8 outils), accepté sans avertissement, et le modèle appelle les deux `list_roots`. Serveur `casse` (`command: /bin/false`) : `required: false` → `tool.source_unavailable` (`error: BrokenResourceError`, peu parlant : ni code de sortie ni stderr) et le run se termine normalement ; `required: true` (`session-D3d`) → `run.failed`, « source casse requise et indisponible : Connection closed », code de sortie 1. Reconnexion sous `loom serve` (`session-D3e`) : après `pkill` du serveur MCP, log « Serveur MCP fs : contrôle de santé raté (ClosedResourceError()) » puis relance du serveur au run suivant ; les deux runs ont toutefois échoué sur `casse`, resté `required: true`. Reconnexion confirmée (`session-D3f`, `loom serve`, `casse` retiré) : run 1 réussi, `pkill` du serveur MCP, run 2 → `fs__read_file` de `agent-01/pyproject.toml` réussi sur la connexion rouverte (contrôle de santé raté, serveur relancé), réponse juste. Le double référencement `fs` / `f` était encore là (deux `list_roots` au run 1).

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

**Testée** : non

**Note** :

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

**Testée** : non

**Note** :

### D13 — Outils fournis : `loom.fetch_page`

**Catégorie** : Outils

Outils livrés avec loom-ia, inactifs par défaut, activés par `- python: loom.<outil>` (agent-01 active `loom.fetch_page`). `fetch_page` lit une page web publique (http/https, ports 80/443, adresses publiques seulement, 5 redirections, 5 Mo max) et rend URL finale, titre, texte et liens (50 max). Ni JavaScript, ni PDF, ni image.

**Testée** : oui en partie (17/10, CLI et REST, sessions `session-A5`, `session-A6`, `session-C3` et suivantes) : usage normal oui ; limites (JavaScript, PDF, adresses privées, redirections, 5 Mo) non éprouvées

**Note** : activé par `- python: loom.fetch_page` dans agent-01, vu par le modèle sous le nom `fetch_page` (sans le préfixe `loom.`). Pages publiques de python.org lues en A5/A6 (6 à 25 Ko) : URL finale, titre, texte et liens rendus ; la page de 24 804 caractères a été déportée (voir D6) ; pages lues ensuite par `synthese` via `context: [{tool_results: [fetch_page]}]` (C3). Délai propre de l'outil (45 s) jamais atteint.

---

## E. Fiabilité des sorties

### E1 — Contrat de sortie : schéma, regex, longueur, non vide

**Catégorie** : Fiabilité des sorties

`output:` sur un rôle, un outil ou `main` : schéma JSON, motif regex (borné en temps), longueurs min/max, sortie non vide ; résultat dans `guard.checked`.

**Testée** : non

**Note** :

### E2 — Réparation par le modèle auteur

**Catégorie** : Fiabilité des sorties

Une sortie qui ne tient pas le contrat est renvoyée à son modèle avec la sortie fautive et le diagnostic, jusqu'à `max_attempts`.

**Testée** : oui (17/10, session `session-A7`)

**Note** : 1re réponse en prose → `guard.checked` (`failed`, « ce n'est pas un JSON valide », `attempt: 1`, `resolution: retry`), `policy.decided` `retry`, puis un `message.user` de `kind: repair` : « Réponse refusée par un contrôle (loom.contract) : … Schéma JSON attendu : {…} ». 2e réponse conforme mais dans un bloc ```json``` → normalisation (`normalized: true`, `policy.decided` `replace` « sortie normalisée ») → `guard.checked` `passed` (`attempt: 2`). Coût : un appel de modèle de plus (3 itérations au lieu de 2).

### E3 — Juge LLM : critères, seuils, bloquant ou non, échantillonnage

**Catégorie** : Fiabilité des sorties

`judge:` : un modèle note la sortie sur des critères ; `when` (échantillonnage), `repair`, bloquant ou non ; `--judges auto|force|skip` à la CLI. Événement `judge.evaluated`.

**Testée** : non

**Note** :

### E4 — Politique d'échec : lever une erreur ou avertir

**Catégorie** : Fiabilité des sorties

`on_failure: fail|unverified|fallback` : quand la réparation n'a pas suffi, le run échoue, la sortie passe marquée `unverified`, ou elle est remplacée par `fallback_message` (obligatoire avec `fallback`).

**Testée** : oui, cas `fail` (17/10, REST, session `session-A8`)

**Note** : contrat d'A7 resté actif (`repair.max_attempts: 2`, `on_failure` par défaut `fail`) sur une question sans rapport. 3 réponses refusées (`guard.checked` attempt 1 et 2 → `retry`, attempt 3 → `fail`) puis `run.failed`, `error_type: guard.contract`, `data: null`, `output: null`. Bon comportement du modèle : il refuse d'inventer une version et finit par un JSON hors schéma qui le dit. Ergonomie : le message d'erreur répète trois fois « propriété requise absente : version, date_sortie, source ». Restent à voir : `unverified` et `fallback`.

### E5 — Contrats et juges sur n'importe quel rôle, outil ou réponse finale

**Catégorie** : Fiabilité des sorties

Les mêmes contrats et juges s'attachent à `main`, à un rôle ou à un outil Python (pas de juge sur un outil ni sur la référence d'un sous-agent).

**Testée** : non

**Note** :

### E6 — Alerte si le juge utilise le modèle qu'il évalue

**Catégorie** : Fiabilité des sorties

Contrôle de cohérence au chargement : un juge sur le même modèle (ou un secours commun) que la sortie jugée donne l'avertissement « juge corrélé » ; un juge bloquant échantillonné (`sample < 1`) est signalé aussi. En profil `prod`, ces avertissements sont des erreurs : le chargement s'arrête.

**Testée** : non

**Note** :

### E7 — Politiques aux points d'accroche

**Catégorie** : Fiabilité des sorties

`policies:` : fonctions Python appelées à `before_model`, `after_model`, `before_tool`, `after_tool`, `on_output`, qui rendent une décision typée (laisser, remplacer, refuser, réessayer, pause) ; `timeout`, `on_error`, `max_attempts` ; politiques fournies comme `loom.require_tool`. Événement `policy.decided`.

**Testée** : non

**Note** :

---

## F. Mémoire et sessions

### F1 — Sessions persistantes

**Catégorie** : Mémoire et sessions

`--session <id>` (ou `session_id`) rattache plusieurs runs au même journal : le run suivant reprend la conversation.

**Testée** : non

**Note** :

### F2 — Historique structuré, sans aplatissement

**Catégorie** : Mémoire et sessions

L'historique LLM est une projection du journal d'événements (blocs neutres, appels et résultats d'outils), indépendante du fournisseur ; snapshots tous les `snapshot_every` événements.

**Testée** : non

**Note** :

### F3 — Compaction automatique

**Catégorie** : Mémoire et sessions

Au-delà de `over_tokens` (50 000 dans agent-01), un résumé par un modèle dédié (`HAIKU`) remplace l'ancien segment, les `keep_last` (2) derniers tours restent ; `blocking: true` l'attend en fin de run. Événement `session.compacted`, contrôle de fidélité lexical.

**Testée** : non

**Note** :

### F4 — Nettoyage : thinking retiré, médias en références

**Catégorie** : Mémoire et sessions

Dans l'historique renvoyé au modèle, les blocs de raisonnement anciens sont retirés et les images remplacées par leur URI.

**Testée** : non

**Note** :

### F5 — Backends de session : mémoire, JSONL, SQLite, Postgres

**Catégorie** : Mémoire et sessions

`storage.events.backend` : `memory`, `jsonl` (agent-01, dossier `data/`), `sqlite`, `postgres`. Firestore : non réalisé.

**Testée** : non

**Note** :

### F6 — Mémoire long terme / RAG via `loom-notes`

**Catégorie** : Mémoire et sessions

Le serveur MCP `loom-notes` branché comme source d'outils : l'agent cherche et ajoute des notes.

**Testée** : non

**Note** :

### F7 — Gestion des sessions : lister, exporter, supprimer (RGPD)

**Catégorie** : Mémoire et sessions

`loom sessions list|export|delete`, `GET/DELETE /v1/sessions…`, `Loom.sessions()`, `export_session()`, `delete_session()`. La suppression retire journal, fichiers et clés.

**Testée** : non

**Note** :

### F8 — State de session

**Catégorie** : Mémoire et sessions

Clés typées déclarées dans `state:` de l'agent (schéma, `from` d'un outil avec `path`/`value`/`on_missing`, `required`, `default`), posées par `loom run --state CLE=VALEUR` ou par un résultat d'outil, montrées à `main` par `context: [session_state]`, relues par `loom state <session>`. Limites 1 Kio / 4 Kio ; un state refusé à l'ouverture refuse le run (code 2).

**Testée** : non

**Note** :

---

## G. Pièces jointes et artefacts

### G1 — Entrées validées : images

**Catégorie** : Pièces jointes et artefacts

`loom run --attach FICHIER` (répétable), envoi multipart en REST : format reconnu à la signature binaire (jamais au nom), taille (`execution.attachments.max_bytes`, 5 Mio) et nombre (`max_files`, 10) contrôlés avant le run. Seules les images jpeg, png, gif et webp sont acceptées ; PDF, audio et autres fichiers sont refusés (non réalisé).

**Testée** : oui pour une image valide (17/10, sessions `session-B9`, `session-B9b`)

**Note** : `--attach chat.jpg` (127 540 octets) accepté, type `image/jpeg` reconnu. Restent à voir les refus : fichier trop gros (> 5 Mio), type non image (un PDF), plus de 10 pièces jointes, et un fichier dont l'extension ment.

### G2 — Stockage d'artefacts référencés par URI

**Catégorie** : Pièces jointes et artefacts

`storage.artifacts` (mémoire ou local) : les fichiers vivent hors du journal, qui n'en garde que l'URI.

**Testée** : oui (17/10, sessions `session-A6`, `session-B9`)

**Note** : pièce jointe rangée dans `data/.artifacts/default/session-B9/<sha256>.jpg` (backend `local` déduit du journal `jsonl`), `artifact.stored` (`origin: attachment`) au journal, et le message utilisateur ne porte que l'URI `artifact://default/session-B9/<sha256>.jpg`. Même mécanisme pour un résultat déporté en A6 (`origin` offload). Adressage par contenu : même fichier, même empreinte d'une session à l'autre.

### G3 — Artefacts produits par les outils, récupérables

**Catégorie** : Pièces jointes et artefacts

Un fichier rendu par un outil est rangé et listé dans `RunResult.artifacts` ; `Loom.artifact(uri)` en Python, ressource `loom://artifacts/…` en MCP (pas de téléchargement par REST).

**Testée** : non

**Note** :

---

## H. Exécution durable

### H1 — État du run sérialisable

**Catégorie** : Exécution durable

Le `RunState` se reconstruit entièrement en relisant le journal (`Loom.state()`).

**Testée** : non

**Note** :

### H2 — Sauvegarde à chaque étape

**Catégorie** : Exécution durable

Chaque transition est écrite au journal avant de passer à la suivante : un arrêt brutal ne perd que l'étape en cours.

**Testée** : non

**Note** :

### H3 — Reprise après plantage

**Catégorie** : Exécution durable

`loom resume <run_id>` (ou `Loom.resume()`, `recover()` au démarrage d'un worker) reprend un run interrompu là où il en était. Test : tuer le process pendant un `fetch_page`.

**Testée** : non

**Note** :

### H4 — Validation humaine : pause, approuver, modifier, refuser

**Catégorie** : Exécution durable

Un appel en attente met le run en pause (code de sortie 1) ; `loom approve|reject <run_id> [--call] [--by] [--reason] [--arguments JSON]`, `POST /v1/runs/{id}/approve|reject`, élicitation en MCP, approbateur en ligne en Python.

**Testée** : en partie (18/10, CLI, sessions `session-D10`, `session-H4a`, `session-H4b`) : CLI oui (accord, refus, modification, `--by`, `--reason`) ; REST, MCP, approbateur en ligne à voir

**Note** : `loom approve <run_id> --call <call_id> --session session-D10` 81 s après la pause : `approval.granted` (`by: null`, `reason: ""` faute d'options), un nouveau worker reprend le run (`paused → awaiting_tools`), l'outil s'exécute avec les arguments d'origine, puis le modèle conclut (`completed`, 3 itérations au total) ; la commande affiche « Accordé : <call_id> » et la réponse finale. Premier essai de refus (`session-D10b`) sans pause : `lire_devis` (bouchon) donne le client Dupont pour tout numéro, le modèle constate l'écart avec « Martin » et refuse d'envoyer de lui-même. Refus (`session-H4a`) : `loom reject … --by denis --reason "Client déjà relancé par téléphone"` → `approval.rejected` (by, reason journalisés), l'outil n'est pas exécuté (pas de `tool.called`), le modèle reçoit « Appel à envoyer_relance refusé : Client déjà relancé par téléphone », n'essaie pas de contourner et propose des suites. La demande porte `reason` (« outil déclaré à approbation obligatoire (effets : irreversible) »), `scope: approve` et `expire_at` (+24 h, défaut de `approval.expires_in`). Modification (`session-H4b`) : `loom approve … --arguments '{…}'` → `approval.granted` porte les arguments corrigés, l'outil s'exécute avec eux (69 caractères au lieu du message du modèle). Écarts relevés, puis traités (CHANGELOG 2.0.10) : (1) `tool.called` garde les arguments du modèle — c'est voulu (le rejeu compare à eux, `approval.granted` porte la correction) ; le vrai défaut était dans les évals (`received_arguments` ignorait la correction de l'approbateur), corrigé ; (2) l'appel réécrit dans l'historique n'était pas annoncé, et le modèle s'attribuait la correction (« Denis » « inventé ») : le résultat est désormais précédé, dans la requête seulement, d'une « Note du système » (idem pour une politique `before_tool`). Texte mis au point en trois essais réels avec Haiku : « Appel modifié par denis… » (`session-H4c`) → il s'attribue encore la signature ; « L'appel ci-dessus n'est pas celui que tu as écrit… » (`session-H4d`) → il croit qu'une autre version, invisible, est partie ; « denis a remplacé les arguments de ton appel… L'appel affiché ci-dessus porte déjà les arguments de denis : c'est exactement ce qui a été exécuté. Tes arguments d'origine n'ont pas été utilisés. » (`session-H4e`) → réponse juste : « le texte ci-dessus est bien celui qui est parti », plus d'auto-attribution. Code de sortie à la pause : 1, conforme à la doc. `loom sessions show` (ajoutée le 18/10, avec `PendingApproval.run_id`) : `session-H4a` → un run `completed`, « En attente : rien » ; `session-H4f` → le run `paused`, la demande `envoyer_relance` avec son échéance (+24 h) et la commande `loom approve … --session session-H4f` prête. Constat : un second run lancé dans `session-H4f` pendant la pause ne voit pas le run en attente (l'historique ne reprend que les runs racine terminés, choix documenté dans `history.py`) ; le modèle propose alors d'« envoyer une relance » déjà en attente d'approbation. Traité le 18/10 (CHANGELOG 2.0.10) : la requête d'un nouveau tour reçoit une « Note du système » qui nomme l'appel en attente et ses arguments. Vérifié en réel : « Relance Dupont pour le devis D-0048. » dans `session-H4f` → aucun appel d'outil, réponse « déjà en attente d'une décision humaine. Je ne l'envoie pas une seconde fois pour éviter un doublon ».

### H5 — Runs en arrière-plan

**Catégorie** : Exécution durable

`Loom.submit()` ou `background: true` en REST (réponse 202) : le run tourne à part ; statut par `GET /v1/runs/{id}`, outil MCP `run_status`.

**Testée** : non

**Note** :

### H6 — Déclencheurs : webhook, planification

**Catégorie** : Exécution durable

`triggers:` (`name`, `agent`, gabarit `message`, gabarit `session`, `delivery_header`) : `POST /v1/hooks/{name}` ouvre un run (202, ou 200 si la livraison est déjà reçue). Un planificateur externe passe par la même porte.

**Testée** : non

**Note** :

### H7 — Worker de file de tâches

**Catégorie** : Exécution durable

`loom worker [--jobs N]` consomme la file (`storage.queue` : asyncio ou RabbitMQ) : runs de fond, reprises, résumés de compaction. Demande RabbitMQ pour le cas multi-process.

**Testée** : non

**Note** :

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
