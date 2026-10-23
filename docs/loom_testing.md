# loom_v2 — Suivi des tests des fonctions

Agents d'essai : `agent-01` (`/home/denis/temp/agent-01`, loom-ia ≥ 2.0.9, modèle `HAIKU`, outil `loom.fetch_page`) jusqu'au 18/10 ; `agent_test` les 19/10 (catégories E et F) ; `agent-test` (`/home/denis/temp/agent-test`, modèles `HAIKU` et `OPUS`, outils `loom.fetch_page`, `prix_ttc`, `etiquette_devis`, serveur MCP `notes` (loom-notes), journal JSONL) à partir du 20/10 (catégories G à I). Chaque fiche nomme l'agent et les sessions de ses essais.
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

**Testée** : oui : les 6 cas vus (réponse obtenue, `max_iterations`, réponse forcée ici ; rôle terminal en C3 ; pause d'approbation en H4 ; plafond de budget en J4, 21/10)

**Note** : réponse obtenue : A1 à A3. `max_iterations: 1` (17/10, session `session-A4`, question de A2) → 1 `fetch_page`, puis `awaiting_tools → finalizing` (cause `tool.completed`) et un dernier appel avec `tool_choice: none` ; les outils restent déclarés dans la requête (le cache des outils est donc gardé) et la consigne de réponse forcée termine le dernier message, sans être journalisée comme message. Réponse forcée honnête : 3.15.0 tirée de la page d'accueil, et l'agent dit qu'il n'a pas ouvert la page de la version, en donnant son lien. Faits vérifiés dans la page lue. `run.completed.iterations` vaut 2 : la réponse forcée compte comme un appel. 0,00088 $. Rôle terminal (C3, `terminal: true` sur `synthese`) : au retour du rôle, `awaiting_tools → completed` (cause `tool.completed`), sans dernier appel à l'orchestrateur. Pause d'approbation (H4, D10) : `approval.requested`, `awaiting_tools → paused`, code de sortie 1, reprise par `approve`/`reject` ou approbateur en ligne. Plafond de budget (J4) : `budget.exceeded` puis `ready_for_model → finalizing` (cause `policy.decided`) et réponse forcée sans outils ; en `warn`, le run continue.

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

Un fichier rendu par un outil est rangé et listé dans `RunResult.artifacts` ; `Loom.artifact(uri)` en Python, ressource `loom://artifacts/…` en MCP ; en REST, `GET /v1/sessions/{id}/files/{file}` (portée `read_content`, vu en N2).

**Testée** : oui en CLI et en Python (20/10, agent `agent-test`, série 3, `session-G3a`, `session-G3b`) ; ressource MCP vue en N5 (23/10, `session-N5c`), téléchargement REST en N2 (`session-N2e`)

**Note** : outil `etiquette_devis` (rend un `Image` PNG 120×40 bleu, fabriqué en stdlib, cf. guide 03, ex. 18.2) ajouté à `tools/utils.py` et à l'agent ; HAIKU `vision` et `tool_result_media`. Run 1 (« Fais l'étiquette du devis D-2026-042. ») : 2 appels ; `artifact.stored` `image/png`, 157 octets, `name: etiquette-D-2026-042.png`, `origin: tool_output`, `call_id` de l'appel ; le `tool.completed` ne porte que la référence `artifact://default/session-G3a/3344912e….png` ; dans la requête suivante, l'image part **dans** le `tool_result` (bloc `image`, capture « [fichier image/png, 157 octets, sha256 3344912e…] »). L'empreinte est celle du guide (même PNG, adressage par contenu). Haiku décrit juste (« un simple aplat bleu, sans numéro visible ») et conseille de vérifier avant d'imprimer. `loom sessions files session-G3a --origin tool_output --out data/output_files` écrit `etiquette-D-2026-042.png` sous son vrai nom : sha256 `3344912e…` = celui de l'URI, identique octet pour octet à l'artefact, en-tête PNG, IHDR 120×40. Run 2 (« De quelle couleur est l'étiquette que tu viens de faire ? ») : le `tool_result` du run 1 revient avec la mention « [image etiquette-D-2026-042.png (image/png, 157 octets), rendue par l'outil à un tour précédent : plus montrée] » (correctif F4 du 20/10) ; **écart** : Haiku répond « l'image ne m'a pas été montrée […] Ma description « aplat bleu, sans numéro » était donc inventée, et je m'en excuse » — il désavoue une description juste, la mention se lisant comme « jamais montrée » (voir F4). Python (`session-G3b`, script `uv run python -` avec `load_env_file("loom.yaml")` puis `Loom.from_config`) : réponse juste (« un simple bloc bleu, sans texte visible ») ; `r.artifacts` et `r.produced` donnent la même ligne : `tool_output image/png 157 etiquette-D-2026-043.png artifact://default/session-G3b/3344912e….png` (même empreinte que G3a, autre session) ; `await loom.artifact(uri)` rend 157 octets, `b'\x89PNG\r\n\x1a\n'`. Sans `load_env_file`, le même script lève `ModelConfigError` (« la variable d'environnement ANTHROPIC_API_KEY est absente ou vide ») : la librairie ne lit pas `.env`, c'est voulu (`config/env_file.py`) ; ergonomie : le message ne le rappelle pas, alors que `loom run` lit le `.env` du projet.

---

## H. Exécution durable

N5 (23/10, MCP stdio, `session-N5c`) : « Fais l'étiquette du devis D-2026-050. » → `completed`, `artifacts` : `artifact://default/session-N5c/3344912e….png`, `image/png`, 157 octets, `name: etiquette-D-2026-050.png`, `origin: tool_output`, `call_id` ; le résultat MCP ne porte que du texte (l'image n'y est pas jointe, seulement référencée). `resources/read` de cette URI → `image/png`, 157 octets, signature PNG ; une URI absente → `McpError` « … : introuvable » ; en HTTP, la clé d'un autre client (`cle-dupont`) reçoit le même « introuvable ». Ergonomie : l'URI publiée est `artifact://…` alors que le gabarit annoncé est `loom://artifacts/{client}/{session}/{fichier}` ; le serveur lit les deux, mais un client qui ne connaît que le gabarit ne devine pas la correspondance.

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
Corrigé le 20/10 (CHANGELOG 2.0.10, « Déclencheurs ») : pour une porte avec `session` et `delivery_header`, l'identifiant est cherché dans tout le journal du client, et refusé (422, `DeliveryRefused`) s'il est déjà pris dans une autre session. Revalidé (`serve` relancé, `crm-0002`) : devis D-2026-081 → `202` ; même identifiant, devis D-2026-082 → `422` « Identifiant de livraison 'crm-0002' déjà utilisé par un run du déclencheur 'devis-signe' (session devis-D-2026-081) : une même livraison doit donner la même session, et celle-ci donnerait devis-D-2026-082 », aucune session `devis-D-2026-082` ; charge d'origine renvoyée → `200`, `repeated: true`, `status: completed`. Même trou, hors H6, à voir en A et N2 : un `run_id` imposé par l'appelant (`--run-id`, `run_id` en REST) n'est contrôlé que dans sa session. Confirmé et corrigé en N2 le 23/10 (409 « Le run … existe déjà dans la session … »).

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

**Testée** : oui (21/10, agent `agent-test`, série 1, `session-I1a`, `session-I1b`)

**Note** : `Loom.stream()` sur « Calcule le TTC de 420 € HT… puis explique » : 20 événements, `seq` 1 à 20 sans trou, identiques au journal, dans l'ordre `run.started`, `message.user`, `run.claimed`, `step.started`, `model.exchanged`, `model.responded`, `step.completed`, `run.transitioned`, `step.started`, `tool.called`, `guard.checked` (contrat de `prix_ttc`), `tool.completed`, `step.completed`, `run.transitioned`, second appel au modèle, `run.transitioned`, `run.completed` ; chacun avec sa catégorie (`run`, `message`, `model`, `tool`, `guard`) et son statut (`ok`). Hiérarchie des spans (relue au journal, les 8 premiers caractères des UUIDv7 étant ceux de l'horodatage) : span du run (sans parent) pour `run.*`, `message.user` et les transitions ; un span par étape, enfant du run ; l'appel d'outil a son propre span, enfant de l'étape, qui porte aussi le `guard.checked` de son contrat ; `role: main` sur les événements du modèle. `loom run --stream` : déroulé lisible (« · prix_ttc(montant_ht=90, taux_tva=5.5) », « · contrôle contract tool:prix_ttc : conforme », « · prix_ttc : fait »), puis la réponse (94,95 € TTC), code 0.

### I2 — Consommation en Python (itérateur async) et en HTTP (SSE)

**Catégorie** : Streaming et événements

`Loom.stream()` / `Loom.follow()` en Python ; `GET /v1/runs/{id}/events` en SSE (`after_seq`, `subruns`).

**Testée** : oui (21/10, agent `agent-test`, séries 1 et 2, `session-I1a`, `session-I2a`)

**Note** : `Loom.stream()` : itérateur async qui mêle les événements du journal et les morceaux du modèle dans l'ordre d'arrivée (30 morceaux, le premier un `ToolCallStarted` à 3,90 s, avant le `model.exchanged` et le `model.responded` de l'étape à 4,00 s) ; il s'arrête après `run.completed`. `Loom.follow(run_id, session_id=…, after_seq=10)` sur le run terminé : rend `seq` 11 à 20 puis s'arrête de lui-même sur `run.completed`. SSE (`loom serve` sans clé) : run lancé en arrière-plan (`background: true`), `GET /v1/runs/{id}/events?session_id=…` ouvert aussitôt → `id: 1 / event: run.started` … `id: 20 / event: run.completed`, puis le flux se ferme (la commande suivante s'est enchaînée ; délai de fermeture non chronométré). Reprise : en-tête `Last-Event-ID: 15` → reprend à `id: 16` ; `after_seq=18` → reprend à `id: 19` ; les deux se ferment sur `run.completed`. Format : `id: <seq>`, `event: <type>`, `data: <événement JSON complet>` (enveloppe : `event_id`, `ts`, `schema_version`, client, session, run, spans, catégorie, statut, `facets`, `payload`, `seq`), ligne vide. Run inconnu → `404`, « Run inconnu inconnu ». Non vu : `subruns` (pas de sous-agent dans cet agent).

### I3 — Latence adaptée à la voix

**Catégorie** : Streaming et événements

Premier token rapide, délais `first_token` et `idle` sur le streaming. Mesurable avec un vrai modèle (jamais mesuré en réel).

**Testée** : oui (21/10, agent `agent-test`, séries 1 et 3, `session-I1a`, `session-I3a`, `session-I3b`)

**Note** : loom n'enregistre pas le délai du premier token (aucun champ d'événement) : mesuré côté client dans `stream()`. HAIKU, réseau domestique : premier morceau à 3,90 s du lancement, dont 1,74 s avant l'appel (démarrage de loom-notes : `step.started` à 2,55 s, `run.claimed` à 0,81 s) — soit ≈ 1,35 s entre le début de l'étape et le premier morceau ; fin du run à 6,00 s (2 appels au modèle et un outil). Pour la voix, le démarrage des serveurs MCP par run pèse plus que le modèle : une instance gardée ouverte (`serve`, worker) ne le paie qu'une fois. Délais (HAIKU, `timeouts` posés le temps du test) : `first_token: 0.01` (`session-I3a`) → 3 tentatives, logs « Tentative 1/3 du modèle HAIKU ratée (transient) : nouvel essai dans 1.0 s », puis 1.3 s, 2 `model.retried` (« Délai dépassé : aucun premier morceau en 0.01 s »), aucun `model.exchanged` (rien reçu), `run.failed` `model.transient`, code 1. `first_token: 30, idle: 0.001` (`session-I3b`, `--stream`) : chaque tentative reçoit son premier morceau puis coupe (« Délai dépassé : aucun nouveau morceau en 0.001 s ») ; 3 `model.exchanged` (statut 200, 1,3 s puis 0,7 s), 2 `model.retried`, `run.failed`. Console : le début de la réponse (« La TVA (») s'affiche, la tentative suivante est annoncée par « · réponse reprise » avant de repartir du début — la reprise est dite, pas mêlée. Constat pour J1/J2 : les tentatives coupées en plein flux ont été facturées par le fournisseur (≈ 1 900 tokens d'entrée chacune, plus les premiers tokens de sortie), mais `run.failed` porte un usage nul et 0,0000 $ : l'usage d'un flux interrompu n'est pas compté.

### I4 — Bus d'événements entre process

**Catégorie** : Streaming et événements

`storage.bus` : mémoire, Redis ou Postgres ; un client SSE servi par un process suit un run mené par un autre, resynchronisé par `seq`.

**Testée** : oui (21/10, agent `agent-test`, série 4, `session-I4a` à `session-I4c`) : bus `memory` conforme ; bus `postgres` : écart (`loom serve` n'écoutait pas le bus), corrigé et revalidé ; bus `redis` non essayé (extra absent)

**Note** : un `loom run` (process A, `--run-id`) mène un run lent (`fetch_page` sur httpbin `delay/10`), `loom serve` (process B) le sert en SSE, ouvert 4 s après le lancement. Bus `memory` (`session-I4a`) : le flux rend les événements déjà écrits (`id` 1 à 4, jusqu'à `step.started`), puis plus rien ; `curl` coupé par son `-m 40` (40,0 s), sans `run.completed` — comportement documenté. Bus `postgres` (`session-I4b`, `storage.bus: {backend: postgres, dsn_env: LOOM_BUS_DSN}` vers le conteneur `loom-pg`, `validate` → « Bus : postgres (LOOM_BUS_DSN : renseignée) », `serve` relancé) : **même blocage** (`id` 1 à 4, 40,0 s), alors que le run s'est terminé dans A en 15 s (19 événements, `run.completed` à 08:29:04). Cause : `loom serve` construit son instance sans l'ouvrir (`serve(Loom(config))` dans `cmd_serve`, `Loom(config)` dans le chemin `--reload`), et le lifespan de `create_app` ne l'ouvre pas non plus ; or c'est `Loom.__aenter__` qui lance l'écoute du bus (tâche `loom-bus`) — sa docstring le dit : « Une instance utilisée sans `async with` … ne verra pas ce que les autres process écrivent ». `loom mcp` et `loom worker` ouvrent bien leur instance (`async with`). Le test d'intégration (`test_sse_follows_a_run_piloted_in_another_process`) passe parce qu'il ouvre lui-même l'instance avant `create_app`. Corrigé le 21/10 (CHANGELOG 2.0.10, « Accès REST ») : `create_app(…, own=True)` ouvre l'instance au démarrage du serveur. Revalidé (`session-I4c`, `serve` relancé, bus `postgres`) : le flux ouvert 4 s après le lancement rend `id` 1 à 19 au fil du run, `tool.completed` compris, jusqu'à `run.completed`, puis se ferme seul — 13,5 s au lieu de 40.

---

## J. Coûts et budgets

### J1 — Comptage des tokens

**Catégorie** : Coûts et budgets

Entrée, sortie, cache (lecture/écriture), raisonnement, relevés sur chaque `model.responded`.

**Testée** : oui (21/10, agent `agent-test`, séries 1 et 2, `session-J1a`, `session-J2b`, `session-J2c`, `session-J2c1`) : appels aboutis ; écart sur l'usage des tentatives coupées, corrigé et revalidé

**Note** : Run « TTC de 1 000 € HT à 20 %, puis de 700 € HT à 10 % » (2 appels HAIKU, les deux `prix_ttc` dans le même lot, le premier refusé par le contrat ≤ 1000). Chaque `model.responded` porte les 5 compteurs : appel 1 `input 18, output 156, cache_read 2 028, cache_write 0, reasoning 0` ; appel 2 `input 32, output 387, cache_read 2 421, reasoning 259` (le raisonnement est **compris** dans `output_tokens`, Anthropic le facturant comme sortie : pas de double compte) ; facettes `cost_usd`, `tokens` (somme entrée + sortie + cache, 2 202 puis 2 840), `latency_ms`, `stop_reason`, `model`. `run.completed` : somme exacte (`50 / 543 / 4 449 / 0 / 259`). Ergonomie (déjà vue en G1) : la console de `loom run` affiche « tokens : 50/543 » (entrée hors cache), le rapport « 4499/543 » (entrée cache lu compris) — deux lectures différentes du même usage, aucune ne nomme le cache. **Écart** (constaté en I3b, confirmé `session-J2b`, `timeouts: {first_token: 30, idle: 0.001}` le temps du test) : 3 tentatives coupées en plein flux, `run.failed` avec un usage nul et 0,0000 $ ; or la capture brute de chaque tentative (`model.exchanged`) montre le `message_start` d'Anthropic avec l'usage facturé — tentative 1 : `input_tokens 18, cache_creation_input_tokens 93, cache_read_input_tokens 1908, output_tokens 5` ; tentatives 2 et 3 : `18 / 0 / 2001 / 5` — soit ≈ 84 µ$ (plus les quelques tokens de sortie reçus avant la coupure) non comptés, pour un run réussi comparable à ≈ 300 µ$. Cause : l'adaptateur Anthropic garde l'usage du `message_start` pour lui et n'émet `UsageDelta` qu'au `message_stop` ; une tentative coupée (délai, erreur réseau, annulation) ne transmet donc aucun usage, et ni `model.retried` ni le ledger n'en portent. Une tentative ratée suivie d'une réussie sous-compte aussi un run `completed`, et les budgets (J4) en tiennent compte de même. Corrigé le 21/10 (CHANGELOG 2.0.10, « Événements et rapports », ⚠) : l'usage du `message_start` part dès l'ouverture du flux, `model.retried` porte `usage`, `cost_usd` et `final`, les tentatives facturées comptent dans le run, le ledger, les budgets et les rapports (pas comme des appels). Revalidé : chemin normal (`session-J2c`, sans délai) → `18 / 224 / 0 / 2 001`, 363,925 µ$ = `cost_usd` journalisé, pas de double compte ; flux coupés (`session-J2c1`, `idle: 0.001`) → 3 `model.retried`, chacun avec l'usage exact de son `message_start` capturé (`18 / 5 / 2 001 / 0`, 24,31 µ$), le 3ᵉ `final: true` (journalisé désormais) ; `run.failed` = leur somme (`54 / 15 / 6 003`, 72,93 µ$) ; console « tokens : 54/15 · coût : 0.0001 $ » au lieu de 0. `loom report` : « Total 0 appel · 6057/15 tokens · 0,00007 $ », de même par rôle et par modèle, puis « Dont, facturé sans réponse : 3 tentatives ratées · 6057/15 tokens · 0,00007 $ », code 0. Limite : la sortie comptée est celle du `message_start` (5 tokens) ; les quelques tokens de texte reçus avant la coupure ne sont annoncés par Anthropic qu'au `message_delta`, jamais reçu.

### J2 — Coût calculé par grille tarifaire

**Catégorie** : Coûts et budgets

`pricing:` du modèle (agent-01 : input 0.1, output 0.5, cache_read 0.01, cache_write 0.125) appliqué aux tokens.

**Testée** : oui (21/10, agent `agent-test`, séries 1 et 2, `session-J1a`, `session-J2a`)

**Note** : Grille HAIKU (`input 0.1, output 0.5, cache_read 0.01, cache_write 0.125` $/M). Recalcul à la main : appel 1 = 18×0,1 + 156×0,5 + 2 028×0,01 = 100,08 µ$ → `cost_usd 0.00010008` ; appel 2 = 32×0,1 + 387×0,5 + 2 421×0,01 = 220,91 µ$ → `0.00022091` ; total 0,00032099 $, identique dans `run.completed`, `loom report` (texte « 0,00032 $ », JSON `cost 0.00032099`) et la console (« 0.0003 $ »). Paliers (`session-J2a`, `tiers: [{above: 1000, input: 1.0, output: 5.0}]` sur HAIKU le temps du test) : le seuil porte sur toute l'entrée de l'appel (`prompt_tokens` = entrée + cache lu + cache écrit, ≈ 2 020 ici), le palier s'applique donc ; appel 1 (`18 / 79 / 1 908 / 96`) = 18×1,0 + 79×5,0 + 1 908×0,01 + 96×0,125 = 444,08 µ$ → `0.00044408` ; appel 2 (`32 / 44 / 2 004 / 127`) = 287,915 µ$ → `0.000287915` : entrée et sortie au prix du palier, cache lu et cache écrit au tarif de base (le palier ne les redéfinit pas). Total 0,000732 $ (« 0.0007 $ »), ≈ 2,3 fois la série 1 pour un run comparable.

### J3 — Ventilation par run, rôle, modèle, session, client

**Catégorie** : Coûts et budgets

Le ledger, projection du journal, répartit la consommation selon ces axes.

**Testée** : oui (21/10, agent `agent-test`, série 3, `session-J3a`, `session-J3b`, rôle `relecteur` sur OPUS et client `dupont` ajoutés le temps du test)

**Note** : Run « TTC de 450 € HT, puis relecture par le relecteur » : 3 appels HAIKU (`main`) et 1 appel OPUS (`relecteur`). `loom report <run>` : total `4 appels · 6920/559 tokens · 0,00477 $` ; par rôle `main 3 appels · 6812/486 · 0,00287 $` et `relecteur 1 appel · 108/73 · 0,00189 $` ; par modèle `HAIKU (claude-haiku-5-5)` et `OPUS (claude-opus-5-5)`, mêmes chiffres : les parts font le total (6 812 + 108, 486 + 73). OPUS coûte 66 % du run pour 1,6 % des tokens. Session (`loom report --session session-J3a`) : `6 appels · 11861/681 · 0,00554 $`, « Par run » avec les deux runs (`4 appels … 0,00477 $`, `2 appels · 4941/122 · 0,00078 $`), sommes exactes. Client : le run de `dupont` est rangé dans `data/dupont/` et n'apparaît que dans le rapport de `dupont` (`--period day --tenant dupont` : 1 run, 0,000697 $, 4 301 tokens, identique au recalcul).

### J4 — Plafonds par run, session, client et période

**Catégorie** : Coûts et budgets

`budget:` (`max_cost`, `max_tokens`, `max_calls`) avec action avertir ou arrêter ; quotas par client sur le jour ou le mois. Contrôlé avant chaque appel de l'orchestrateur.

**Testée** : oui (21/10, agent `agent-test`, série 4, `session-J4a` à `session-J4f`, `session-J3a`, plafonds posés un par un dans `loom.yaml`)

**Note** : `budgets: {run: {max_cost: 0.00005}}` (`session-J4a`) : 1er appel 1 020 µ$ (les deux `prix_ttc` dans le même lot), puis, avant l'appel suivant, `budget.exceeded` (`scope: run, limit: max_cost, value: 5e-05, spent: 0.00102025, action: stop, policy: loom.budget`), `ready_for_model → finalizing` (cause `policy.decided`), réponse forcée sans outils (bloc texte seul), `completed`, 0,001537 $ — le plafond arrête le run, la réponse forcée s'y ajoute. Même plafond en `on_exceed: warn` (`session-J4b`) : `budget.exceeded` `action: warn`, puis le run continue sans passer par `finalizing`. `run: {max_calls: 1}` (`session-J4c`) : 1 appel, `budget.exceeded` (`max_calls`, `value: 1, spent: 1`), réponse forcée. `session: {max_cost: 0.005}` sur `session-J3a` (déjà 0,005542 $) : `budget.exceeded` (`scope: session`) **avant le premier appel** du nouveau run, réponse forcée immédiate, honnête (« Je ne peux plus appeler l'outil de calcul, donc je ne peux pas donner le TTC pour 100 € HT »). Client `dupont`, `tenants[].budgets: {tenant: {max_cost_per_day: 0.0005}}` (0,000697 $ dépensés ce jour) : run refusé avant d'ouvrir — « Client 'dupont' : budget du client atteint pour la journée : 0,00070 $ (plafond 0,00050 $) — réessayer dans 10 h 49 min », code 2, aucun journal `session-J4e` ; `loom report --period day --tenant dupont` → « plafond max_cost : 0,00050 $ — reste 0,00000 $ » ; un run de `default` passe (`session-J4f`). Le bloc `tenants` mal placé (sous `mcp_servers`) est refusé au chargement avec les clés en cause. Ergonomie : ni la console de `loom run` ni son résumé ne disent qu'un plafond a été atteint (`stop` ou `warn`) — seul le journal le montre ; le refus du client est dit deux fois (log WARNING et ligne d'erreur).

### J5 — Rapport de consommation

**Catégorie** : Coûts et budgets

`loom report [run_id] [--session] [--period day|month] [--json]`, `GET /v1/sessions/{id}/report`, outil MCP `run_report`, `Loom.report()`.

**Testée** : oui (21/10, agent `agent-test`, séries 3 et 3b, `session-J3a`, `session-J3b`) : CLI (run, session, période), REST, MCP, Python ; écart sur la période corrigé et revalidé

**Note** : `loom report <run>`, `--session`, `--period day|month` (`--tenant`), `--json` : voir J3. Période : « Client : default / Période : journée en cours, depuis 2026-10-21 00:00 UTC / Runs : 16 / Dépense : 0.010410 $, 64951 tokens / aucun plafond sur cette période / Remise à 0 : 2026-10-22 00:00 UTC » ; JSON du mois : `period: month:2026-10`, `since`, `resets_at`, `runs`, `cost_usd`, `tokens`, `limits`, `left`. Recalcul : la période rattache chaque **appel** au jour où il a eu lieu (`EventQuery(since=…)`), pas le run à sa date de départ — d'où 16 runs (un run lancé la veille et repris ce jour compte) ; chiffres identiques au recalcul sur les seuls `model.responded`. **Écart** (du correctif J1 du même jour) : `_from_journal` (`tenancy/usage.py`) ne lit que `model.responded` ; les tentatives facturées (`model.retried` avec `usage`, ici `session-J2c1`, 72,93 µ$ et 6 072 tokens) manquent au rapport de période **et au compteur des plafonds de client** (`tenant.max_cost_per_day`…), alors que le run, la session et les budgets de run et de session les comptent. Avec elles : 17 runs, 0,010483 $, 71 023 tokens. Corrigé le 21/10 (CHANGELOG 2.0.10, entrée J1 rectifiée) : la lecture des périodes prend aussi les `model.retried`. Revalidé : `loom report --period day` → « Runs : 17 / Dépense : 0.010483 $, 71023 tokens », exactement le recalcul. Série 3b, mêmes chiffres partout que la CLI : REST `GET /v1/sessions/session-J3a/report` → `UsageReport` complet (total 6 appels, `252 / 681 / 8 566 / 3 043`, raisonnement 262, 0,005542035 $, détail par run) ; session inconnue → `404` « Session inconnue inconnue » ; le rapport du run est porté par `GET /v1/runs/{id}` (`report` : 4 appels, 0,004765815 $, `HAIKU` 0,002873815 $ et `OPUS` 0,001892 $). MCP stdio, outil `run_report` : avec `run_id` + `session_id` → `isError: False`, résultat structuré (total du run, 1 run) et texte identique à `loom report` ; avec `session_id` seul → total de la session, 2 runs. Python : `Loom.report(run_id, session_id=…)` → 4 appels, rôles `main` (3) et `relecteur` (1) ; `report(session_id=…)` → 6 appels, 2 runs ; `report()` sans argument → `ValueError` « report() demande un run_id ou un session_id ». Pas de route REST ni d'outil MCP pour la consommation d'un client sur une période : CLI (`--period`) et Python (`Loom.consumption()`) seulement.

---

## K. Observabilité et rejeu

### K1 — Trace hiérarchique

**Catégorie** : Observabilité et rejeu

Arbre run → étape → appel modèle / outil / validation / sous-run, chaque nœud avec parent, durée, statut, coût : `loom inspect <run_id> [--full] [--json]`, `GET /v1/traces/{run_id}`.

**Testée** : oui (21/10, agent `agent-test`, série 1, `session-K1a`, `session-K1b`) : arbre run → étape → modèle / outil / contrôle ; sous-run et juge non vus (ni sous-agent ni juge dans agent-test, comme I2)

**Note** : Run « Client Dupont : notes, TTC de 1 200 et 800 € HT, étiquette D-42 » (`session-K1a`, 2 appels HAIKU, 4 outils dans un même lot, grille HAIKU sans paliers remise avant le test). `loom inspect` : en-tête (statut, itérations, usage, coût, 15,1 s de pilotage), arbre `run agent — completed, 17.0 s, 0.000740 $` → `étape 1` (appel modèle 1,9 s, `1878 → 318` tokens dont 1 860 écrits en cache, 0,000393 $, les 4 appels demandés) → `étape 2` (les 4 outils avec durée, arguments, résultat ; `prix_ttc` 1 200 € dit `(erreur)`, avec « contrôle contract : failed → fail » et « politique loom.contract (after_tool) : replace » ; `etiquette_devis` avec « fichier rangé : artifact://… ») → `étape 3` (appel modèle 3,0 s, 0,000347 $, réponse), puis la réponse finale entière et le bilan « 2 appel(s) de modèle, 4 appel(s) d'outil ». `--full` : mêmes lignes, arguments, résultats et réponse du modèle sans coupe. `--json` (la trace de `GET /v1/traces/{id}`) : 10 spans — `run invoke_agent agent`, 3 `step n` enfants du run, un `chat claude-haiku-5-5` par appel de modèle dans son étape, 4 `execute_tool …` dans l'étape 2 —, chacun avec parent, durée, `open`, `error` et ses événements ; le `prix_ttc` refusé porte `error: guard.checked` (le type, jamais le message). Vérifié au journal (30 événements) : parents conformes aux `span_id`/`parent_span_id` écrits ; coûts des spans `chat` recalculés avec la grille (18×0,1 + 318×0,5 + 1 860×0,125 = 393,3 µ$ ; 32×0,1 + 456×0,5 + 1 860×0,01 + 779×0,125 = 347,175 µ$), leur somme = `loom.cost_usd` du span `run` = `run.completed` (0,000740475 $) ; tokens : 1 878 + 2 671 = 4 549 de l'en-tête. L'étape 2 dure 10 278 ms, la durée de `notes__search` (10 274 ms) et non la somme des 4 outils : exécution parallèle visible dans l'arbre. Le contrôle (validation) est un événement du span de l'outil, pas un span à part. Run inachevé (`session-K1b`, `add_text` en approbation, run `paused`) : « Statut : paused — run inachevé : durées provisoires », `run agent — inachevé, 3.8 s (ouvert)`, `étape 2` → « approbation pour notes__add_text — en attente », bilan « 0 appel(s) d'outil, 1 approbation(s) » ; run laissé en pause pour K6. Ergonomie : `etiquette_devis` affiche « résultat : null » (le résultat est une image, dite seulement par la ligne du fichier) ; l'en-tête compte l'entrée cache comprise (4 549) alors que les attributs `gen_ai.usage.input_tokens` du span `run` la laissent à part (50, cache dans `loom.usage.cache_*`), même double lecture que la console (J1).

### K2 — Schéma d'événements stable et versionné

**Catégorie** : Observabilité et rejeu

`schema_version` sur chaque événement (1 aujourd'hui) : le contrat pour une interface visuelle.

**Testée** : oui (21/10, agent `agent-test`, série 1, tous les journaux) : écart (JSON Schema publié fermé, contraire à la règle d'évolution) corrigé et revalidé

**Note** : Script sur les 62 journaux d'agent-test (1 455 événements, 23 types sur les 31) : `schema_version` vaut 1 partout ; chaque événement valide le JSON Schema de `event_json_schema()` (jsonschema, Draft 2020-12 : 0 hors schéma) ; enveloppe à 17 champs (`event_id, ts, schema_version, tenant_id, session_id, run_id, root_run_id, span_id, parent_span_id, type, category, status, agent, role, facets, payload, seq`) ; un événement passé en `schema_version: 2` est refusé : « Version de schéma d'événement non prise en charge : 2 (attendue : 1) ». Aller-retour `Event.model_validate` → `model_dump_json` identique sauf 6 `model.retried` écrits avant le correctif J1 (`session-I3a`, `I3b`, `J2b`) : la relecture leur ajoute `usage`, `cost_usd` et `final` par défaut — ajout de champs optionnels sans changement de version, conforme à la règle de `conception.md` (« ajouter un champ optionnel ne change pas la version : un code plus ancien ignore à la lecture les champs qu'il ne connaît pas »), et loom l'applique depuis 2.0.4. **Écart** : le JSON Schema publié, présenté comme le contrat d'une interface (« à donner à un validateur », howto chap. 25), est **fermé** : `additionalProperties: false` sur l'enveloppe et sur 46 des 75 définitions, dont `ModelRetried`. Un consommateur qui valide avec le schéma de 2.0.9 rejette donc les `model.retried` de 2.0.10 (et la facette `model` ajoutée en 2.0.10), à `schema_version` égale — le contrat publié contredit la règle d'évolution que loom s'applique à lui-même. Corrigé le 21/10 (CHANGELOG 2.0.10, « Événements et rapports », ⚠) : `event_json_schema()` rend un schéma ouvert, l'écriture restant stricte (Pydantic). Revalidé : 0 `additionalProperties: false` dans le schéma ; un événement de `session-K7a` auquel on ajoute `payload.champ_futur` le valide.

### K3 — Capture des contenus et masquage

**Catégorie** : Observabilité et rejeu

`telemetry.capture` règle ce qui est gardé (`raw_exchanges: true` dans agent-01 garde les requêtes brutes) ; masquage des données sensibles ; contenu masqué en REST sans la portée `read_content`.

**Testée** : oui (21/10, agent `agent-test`, séries 4 et 5, `session-K3a`, `dupont/session-K3b`, `session-K1b`, `session-K4a` à `session-K4c`) : capture au journal, masquage par la portée de la clé, masquage par motifs à l'export (voir K4)

**Note** : Le temps du test : `capture: {raw_exchanges: true, raw_max_bytes: 4000}`, client `dupont` avec `telemetry.capture.raw_exchanges: false`, deux clés d'API (`lecteur` : `run, read` ; `complet` : `run, read, read_content`). **Journal** (`session-K3a`, message avec nom, e-mail, téléphone et IBAN) : 2 `model.exchanged` ; `x-api-key: [retiré]` sur les deux, aucune clé `sk-ant-…` dans le fichier ; compteurs `anthropic-ratelimit-*` gardés (diagnostic). Coupe : requête 1 de 3 939 octets non coupée ; appel 2, requête de 4 312 et réponse de 4 047 octets coupées à exactement 4 000 (`*_truncated: true`), `*_bytes` et `*_sha256` décrivant le corps entier. Le contenu reste au journal (« Durand » 8 fois). `dupont/session-K3b` : aucun `model.exchanged`, le reste du journal complet — surcharge par client effective. **REST** (`loom serve` avec les clés) : sans clé ou clé fausse → 401. Clé `lecteur` : 0 occurrence du nom, du téléphone, de l'IBAN et de `x-api-key` sur `GET /v1/runs/{id}`, `/sessions/{id}/events` (JSONL), `/events`, `/traces/{id}` et le flux SSE `/runs/{id}/events` (20 événements, le `session.snapshot` hors de l'arbre) ; chaque charge masquée nomme ce qu'elle a perdu : `message.user` → `redacted: [message]`, `model.exchanged` → `redacted: [request_headers, request_body, response_headers, response_body]`, en gardant tailles, empreintes, statut, durée, URL ; trace : `content: false`, `output: null`, `content: null` sur chaque événement ; fiche `session-K1b` : approbation `notes__add_text` avec `arguments = {}` et motif vide. Clé `complet` : tout en clair (nom, téléphone, IBAN dans le journal et `message.user` ; arguments et motif de l'approbation : « M. Dupont préfère être appelé le matin. », « outil déclaré à approbation obligatoire (effets : irreversible) ») ; `x-api-key` n'apparaît qu'au journal, valant `[retiré]` ; la trace garde ses `data` sans contenu (`redacted`) et le contenu à part, et ne porte jamais les corps de `model.exchanged`, même avec `read_content`. **Motifs** (exports OTLP seulement, série 5, détail en K4) : en `capture.exports: content`, e-mail → `[email]`, téléphone → `[phone]`, IBAN → `[iban]`, « mot de passe : … » et une clé `sk-test-…` → `[secret]`, et un motif à soi (`{name: devis, regex: 'D-[0-9]+'}`) → `[devis]` ; aucun en clair ; un nom propre passe (aucun motif ne vise les noms, limite annoncée) ; en `metadata` (défaut, ou surcharge d'un client) aucun contenu ne part.

### K4 — Exports : JSONL, OpenTelemetry

**Catégorie** : Observabilité et rejeu

Journal JSONL exportable (`sessions export`, `replay --export`), export des spans en OTLP (extra `otel`). DuckDB : non couvert.

**Testée** : oui (21/10, agent `agent-test`, série 5, `session-K3a`, `dupont/session-K3b`, `session-K4a` à `session-K4e`, collecteur Jaeger local) : JSONL et OTLP ; écart (`gen_ai.usage.input_tokens` hors cache, contraire à la convention GenAI d'OpenTelemetry) corrigé et revalidé ; `replay --export` vu en K6 ; DuckDB non couvert

**Note** : Prérequis : extra `otel` ajouté à agent-test (`opentelemetry-sdk` 1.45.1), conteneur `loom-jaeger` (`jaegertracing/all-in-one:1.62.0`, OTLP HTTP 127.0.0.1:4318, API 16686), `telemetry.exporters: [{type: otel}]`, `OTEL_EXPORTER_OTLP_ENDPOINT` posée dans le shell. **JSONL** : `loom sessions export session-K3a` identique octet pour octet au journal (`diff` vide), de même `session-K3b --tenant dupont` ; la même session sans `--tenant` → « Session session-K3b inconnue », code 1. **Sans adresse** : `loom validate` avertit « Télémétrie : la variable 'OTEL_EXPORTER_OTLP_ENDPOINT' est vide ou absente — ce collecteur n'est pas monté, aucune trace n'en partira », code 0 ; avec la variable, plus d'avertissement. Un `exporters` posé à la racine au lieu de `telemetry` est refusé au chargement (« exporters — Extra inputs are not permitted »). **OTLP, métadonnées** (`session-K4a2`, défaut `capture.exports: metadata`) : trace reçue par Jaeger sous l'identifiant du run (`run_id` sans tirets), service `loom-ia`, 7 spans : `invoke_agent agent` → `step 1` → `chat claude-haiku-5-5`, `step 2` → `execute_tool prix_ttc`, `step 3` → `chat …` ; coûts et tokens identiques au journal (4,72e-05 + 5,056e-05 = 9,776e-05 $, `gen_ai.request.model`) ; aucun attribut `loom.content.*`, ni nom, ni e-mail, ni téléphone, ni numéro de devis dans la trace. **OTLP, contenu** (`session-K4b`, `capture.exports: content` et motif `devis`) : contenu sur le run, les `chat` et l'outil ; e-mail, téléphone, IBAN, mot de passe, clé `sk-test-…` et `D-88` absents en clair, remplacés par `[email]` (1), `[phone]` (1), `[iban]` (3), `[secret]` (2), `[devis]` (3) ; `Lefèvre` en clair (aucun motif pour les noms) ; aucun corps ni en-tête de `model.exchanged` (`x-api-key` : 0). Un premier run (`session-K4a`) fait avec deux clés `capture` dans le bloc `telemetry` a tourné en `content` (le YAML garde la dernière, écart M1) : même résultat pour e-mail, téléphone et devis. Client `dupont` surchargé en `metadata` (`session-K4c`, racine en `content`) : aucun contenu sur les 7 spans. **Collecteur en panne** (`session-K4d`, Jaeger arrêté) : run `completed`, code 0, journal complet ; le SDK OpenTelemetry réessaie 3 fois (« Transient error … Connection refused … retrying in 1.12s / 1.97s / 4.37s ») puis « Failed to export spans batch due to timeout, max retries or shutdown. » — trace perdue, comme documenté (le journal a tout). Ergonomie : ces lignes viennent des loggers du SDK (`opentelemetry.*`), en anglais, hors du format de loom et hors de `telemetry.logging` ; loom ne dit pas lui-même quel run n'a pas été remis (le processeur du SDK absorbe l'échec, `RunExporter` ne le voit pas). Ergonomie aussi : l'avertissement « collecteur non monté » se répète à chaque commande ; `loom validate` ne montre ni l'exporteur, ni la capture, ni les motifs, et dit « client dupont : rien de surchargé » alors qu'il surcharge sa capture. **Écart** : la convention GenAI d'OpenTelemetry (registre `model/gen-ai/registry.yaml` du dépôt `semantic-conventions-genai`) dit de `gen_ai.usage.input_tokens` « This value SHOULD include all types of input tokens, including cached tokens », et définit `gen_ai.usage.cache_read.input_tokens` et `gen_ai.usage.cache_write.input_tokens`, chacun « SHOULD be included in gen_ai.usage.input_tokens ». loom (`telemetry/spans.py`, `_usage`) y met l'entrée hors cache et range le cache sous `loom.usage.cache_read_tokens` / `loom.usage.cache_write_tokens` : sur `session-K4b`, Jaeger reçoit 50 tokens d'entrée pour un run qui en a consommé ≈ 3 900. Un outil qui suit la convention sous-compte l'entrée d'un facteur 40 à 80 dès que le cache sert, et ne voit pas le cache. Les coûts (`loom.cost_usd`) sont justes. Corrigé le 21/10 (CHANGELOG 2.0.10, « Événements et rapports », ⚠) : `gen_ai.usage.input_tokens` = entrée cache comprise, `gen_ai.usage.cache_read.input_tokens` et `gen_ai.usage.cache_write.input_tokens` à la place des `loom.usage.cache_*` ; `loom.usage.reasoning_tokens` inchangé. Revalidé (`session-K4e`) : dans Jaeger, `chat` 1 → `input_tokens 1812` (dont `cache_write 1794`), `chat` 2 → `1922` (`cache_read 1794`, `cache_write 96`), `invoke_agent` → `3734` (`cache_read 1794`, `cache_write 1890`), sommes exactes, plus aucun `loom.usage.cache_*` ; `loom inspect` inchangé (« 1812 → 54 tokens (dont 1794 écrits en cache) », « 1922 → 152 tokens (dont 1794 lus et 96 écrits en cache) », en-tête « 3734 → 206 tokens »). Reste la console de `loom run` (« tokens : 50/206 », entrée hors cache), déjà notée en J1.

### K5 — API de lecture des traces

**Catégorie** : Observabilité et rejeu

`GET /v1/runs` (filtres `agent`, `status`, `since`, `until`, `limit`), `GET /v1/runs/{id}`, `GET /v1/events` (filtres par type, outil, modèle…), `Loom.runs()`, `Loom.query()`, ressources `loom://runs…`.

**Testée** : oui (21/10, agent `agent-test`, série 3, runs de `session-K1a`, `session-K1b`, `session-K7a` à `session-K7c`) : REST, Python et ressources MCP stdio ; deux écarts (`since` ne bornait pas la lecture des runs ; `after` non valide accepté) corrigés et revalidés

**Note** : `loom serve` sans clé (instance ouverte, client `default`, 66 sessions au journal). `GET /v1/runs?limit=5` : les 5 runs les plus récents (2 de `session-K7c`, K7b, K7a, puis K1b `paused`), `scanned 4`, `truncated True` (il en reste derrière : exact). `?status=paused` : K1b seul, `scanned 50, truncated True` (borne `sessions` de 50) ; `&sessions=200` : `scanned 66, truncated False`, toujours K1b seul. `?since=…13:30Z&until=…13:51Z` : K7a, K1b, K1a (exact : `since`/`until` sur la dernière écriture du run). `?agent=inconnu` : liste vide. `limit=0` et `limit=201` → 422. `GET /v1/runs/{id}?session_id=session-K1a` : `completed`, 2 itérations, 0,000740475 $, texte, `report.total` (2 appels, usage complet) ; sans `session_id` → 404 (la session cherchée par défaut est celle qui porte le nom du run). `GET /v1/events` : `run_id` + `type=tool.completed` → les 4 du run, avec facettes `tool_name`, `latency_ms`, `size`, `is_error` ; `tool_name=prix_ttc&type=tool.completed&status=error` → les 3 refus du contrat de tout le journal (`session-H5b`, `session-J1a`, `session-K1a`) ; `category=guard&session_id=session-K1a` → les 2 `guard.checked` (`passed`, `failed`) ; `model=HAIKU&type=model.responded&since=…13:50Z&limit=2` → les 2 de K7a (8 au journal depuis 13:50, recompté). `GET /v1/traces/{id}?session_id=…` : 10 spans, 0,000740475 $, `content: true`, mêmes spans que `loom inspect --json` ; run inconnu → 404 « Run inconnu inconnu ». Python : `Loom.runs(status=[PAUSED], sessions=200)` → `scanned 66, truncated False`, K1b ; `runs(limit=3)` → les 3 plus récents ; `runs(limit=500)` → `ValueError` « limit : de 1 à 200, reçu 500 » ; `Loom.query(EventQuery(run_id=…, types=("tool.called",)))` → les 4 appels (seq 10 à 13, facettes `tool_name`, `tool_kind`). MCP stdio (`loom mcp`) : ressources `loom://runs`, `loom://sessions` ; 6 gabarits (`loom://runs/{run_id}{?session_id}`, `…/events{?session_id}`, `loom://traces/{run_id}{?session_id}`, `loom://sessions/{session_id}`, `…/events`, `loom://artifacts/{client}/{session}/{fichier}`) ; `loom://runs` → 50 runs, `truncated True` ; trace → 10 spans, 0,000740475 $ ; événements du run → 29 (le 30ᵉ, `session.snapshot`, est un marqueur de session, hors de l'arbre du run par conception) ; run inconnu → `McpError` « Run inconnu inconnu : introuvable ». Pagination : `…&limit=2&after=<dernier de la page 1>` → les 2 `model.responded` de `session-K7b` (seq 6 et 17), sans doublon ni trou, dans l'ordre des identifiants ; un premier essai avec `after=<dernier>` laissé tel quel a rendu une page vide (écart 2). **Écart 1** : `since` ne borne pas la lecture de `/v1/runs` (`Loom.runs`). Les sessions sont lues de la plus récemment écrite à la plus ancienne, et un run n'est jamais plus récent que la dernière écriture de sa session ; dès qu'une session est plus ancienne que `since`, aucune des suivantes ne peut rien rendre. La boucle continue pourtant jusqu'à la borne `sessions` : `?since=…13:30Z` ouvre 50 journaux alors que 5 seulement ont été écrits depuis 13:30, et répond `truncated: true` alors qu'il ne peut rien y avoir d'autre — un client qui pagine croit la liste incomplète. **Écart 2** : `after` n'est pas vérifié (`EventId` est une chaîne) : une valeur qui n'est pas un identifiant d'événement (`<dernier>`) est acceptée et compare comme un identifiant inventé, d'où une page vide en 200 au lieu d'un 422. Corrigés le 21/10 (CHANGELOG 2.0.10, « Événements et rapports ») : la lecture des runs s'arrête à la première session écrite avant `since`, sans `truncated` ; `after` doit être un identifiant d'événement tel que le journal l'écrit (UUID en minuscules, avec ses tirets), sinon 422 en REST et `ValidationError` en Python (⚠). Revalidé (`loom serve` relancé) : `?since=…13:30Z&until=…13:51Z` → `scanned 5, truncated False`, 3 runs ; `after=<dernier>` → 422 (`string_pattern_mismatch` sur `query.after`) ; `after` en majuscules → 422 ; pagination avec un vrai identifiant → `session-K7b` seq 6 et 17. Ergonomie : le 422 REST porte le message anglais de FastAPI (« String should match pattern '^[0-9a-f]{8}-…' »), pas le message français de `EventQuery`, qui ne sert qu'en Python.

### K6 — Rejeu exact ou en variante

**Catégorie** : Observabilité et rejeu

`loom replay <run_id> [--mode exact|variant] [--model ETAPE=MODELE] [--double OUTIL=REF] [--journal FICHIER] [--export]` : à l'identique sans appel (divergence détectée par `request_hash`), ou avec un autre modèle/prompt ; les outils à effet de bord ne sont jamais réexécutés.

**Testée** : oui (21/10, agent `agent-test`, série 6, runs de `session-K1a`, `session-K1b`, `session-K3a`, `session-K7c`) : exact, divergence, export et `--journal`, variante (modèle, outil à effets de bord, doublure)

**Note** : **Exact** : `loom replay <run> --session session-K1a` (recherche MCP, contrat refusé, image, 4 outils) → « Identique », 2 réponses de modèle et 4 résultats d'outils servis, code 0, journal inchangé (30 lignes). **Divergence** (une ligne « Sois poli. » ajoutée à `prompts/system.md` le temps du test, `session-K3a`) : arrêt au premier appel de modèle, « la requête a changé — ce qui diffère : le prompt système », empreintes `5a9f4ef6c4fc` au journal et `4b50f9032c15` au rejeu, plus rien de servi ensuite (0 modèle, 0 outil), issue `failed` au rejeu, code 1 ; `--json` : `divergence {kind: model, parts: [system], expected_hash, actual_hash, rank: 1}`. Prompt rétabli → « Identique », code 0. **Export et journal exporté** : `--export /tmp/rejeu-K1a.jsonl` → « Identique », 23 événements écrits, journal de la session intact ; `loom replay --journal /tmp/rejeu-K1a.jsonl` → « 1 run(s) à rejouer », « Bilan : 1/1 run(s) identique(s) » ; `--journal <(loom sessions export session-K7c)` (lu par un tube) → 2 runs, « Bilan : 2/2 », code 0. **Variante, autre modèle** (`--mode variant --model main=OPUS`, `session-K3a`) : écart nommé dès l'appel n°1 (« le modèle, les réglages »), 2 appels partis pour de vrai, `prix_ttc` (mêmes arguments) lu au journal, rapport côte à côte (issue, appels, tokens, coût, durée, réponses) ; coût exact (OPUS sans cache : 3 864 × 4 + 169 × 20 = 18 836 µ$, « dépensé pour de vrai ») ; journal intact (21 lignes) ; `--model main=INCONNU` → « Rejeu impossible : Variante : modèle(s) non déclaré(s) dans la config : INCONNU », code 2. **Approbation et effets de bord** (`session-K1b`, approuvée pour finir le run, `add_text` écrit alors dans la mémoire de test d'agent-test) : rejeu exact « Identique », décision d'approbation et résultat de `add_text` repris du journal. Variante OPUS : le modèle redemande `add_text` avec d'autres arguments (titre, projet « Clients ») → « 1 non exécuté : effets de bord », approbation accordée par le rejeu avec le motif « appel non exécuté : effets de bord : rien ne part », `tool.completed` en erreur, que le modèle répète honnêtement ; dossier `notes` identique avant et après (empreinte de ses 5 fichiers inchangée). Durée de la variante = somme de ses étapes (3 349 + 4 + 2 556 ms → 5,9 s). Doublure `--double notes__add_text=builtins:dict` (aucun fichier créé) : « 1 remplacé par sa doublure », approbation « appel remplacé par sa doublure : rien ne part », résultat = les arguments d'OPUS, non en erreur ; `notes` toujours inchangé ; `--double notes__add_text=builtins:introuvable` → « Rejeu impossible : Référence 'builtins:introuvable' : 'introuvable' absent du module 'builtins' », code 2. Ergonomie : le rejeu démarre les serveurs MCP de l'agent (`notes`, `required: true`) même quand tout est servi par le journal — avec un worker en route, le verrou Qdrant local pourrait faire échouer un rejeu qui n'en a pas besoin (non essayé) ; la divergence est dite deux fois (log WARNING et rapport).

### K7 — Logs techniques séparés des traces

**Catégorie** : Observabilité et rejeu

`telemetry.logging` (niveau `WARNING` dans agent-01) : `logging` de la stdlib, distinct du journal d'événements.

**Testée** : oui (21/10, agent `agent-test`, série 2, `session-K7a` à `session-K7c`) : CLI (formats `json` et `console`), librairie

**Note** : `telemetry.logging: {level: INFO, format: json}` le temps du test. `loom run … 2>&1 1>/dev/null` (`session-K7a`, message « TTC de 300 € HT pour M. Durand, joignable au 06 12 34 56 78 ? ») : sur stderr, une ligne JSON par fait technique — connexion du serveur MCP (« Serveur MCP notes connecté », sans contexte de run), chaque appel de modèle (« Modèle HAIKU (rôle main) : 1.69 s, 1889 tokens, 0.00026 $ »), chaque outil (« Outil prix_ttc : 1 ms »), chaque transition —, avec `ts`, `level`, `logger` (`loom_ia.engine.loop`, `loom_ia.adapters.mcp.server`), `message`, et `run_id`, `span_id`, `tenant_id` pour ce qui touche un run. Recoupé au journal : mêmes `span_id` (étape pour un appel de modèle, span de l'outil pour un outil, span du run pour une transition), mêmes tokens (1 889 et 2 065, cache compris) et coûts arrondis. Aucun contenu dans les logs (ni « Durand » ni le numéro, ni la réponse du modèle), alors que le journal les porte ; aucune ligne de log n'entre au journal (types du journal inchangés). Les lignes `[notes]` sont le stderr du serveur MCP, relayé à part, hors du format de loom. Retour à `WARNING` : `loom run … 2>/dev/null` (`session-K7b`) ne laisse sur stdout que la réponse. Librairie (`session-K7c`) : le logger `loom_ia` n'a qu'un `NullHandler` et `Loom.from_config` n'applique pas `telemetry.logging` — premier run sans aucune ligne ; après `configure_logging("INFO")` décidé par l'application, le second run écrit ses lignes au format `console`, suffixées `[run_id=… span_id=… tenant_id=default]`. Ergonomie (déjà notée en J1) : le log compte les tokens cache compris (1 889) quand la console de `loom run` dit `50/174`.

---

## L. Multi-clients

### L1 — Espace isolé par client

**Catégorie** : Multi-clients

`tenants:` : par client, surcharges (liste fermée), outils, sessions, budgets, traces ; `--tenant` à la CLI, client tiré de la clé d'API en REST.

**Testée** : oui (22/10, agent `agent-test`, séries 1 à 3, `session-L1a` à `session-L1d`, clients `default` et `dupont`) : liste fermée, sessions et journaux séparés, surcharges (modèles, outils retirés, approbations), client tiré de la clé en REST et de `--tenant` en MCP stdio ; `agents:` non vu (un seul agent), `variables` → M7, stockage propre à un client non essayé

**Note** : **Liste fermée** : `--tenant inconnu` → « Client 'inconnu' non déclaré (clients : default, dupont) », code 2, aucun journal. **Sessions** : même nom `session-L1a` chez `default` (« TTC de 100 € HT ») et chez `dupont` (« rappelle-moi le dernier montant ») → deux journaux disjoints (`data/default/…` 21 événements, `data/dupont/…` 9 événements, `tenant_id` constant dans chacun) ; la requête de `dupont` à Anthropic n'a qu'un message, sans « 100 € », `session_state` vide (`default` : `{"dernier_ttc": 120.0}`) ; le modèle dit qu'aucun calcul n'a été fait ; deux fiches `sessions show` distinctes. **Surcharges** (`dupont` : `models: {HAIKU: OPUS}`, `tools_deny: [etiquette_devis, notes__add_text]`, `approvals: {prix_ttc: always}`) : `loom validate` les liste (« modèles : HAIKU → OPUS », « outils retirés : … », « approbations : prix_ttc : always », « agent : modèle HAIKU (OPUS pour ce client) », compaction comprise, MCP réduit à `notes__search, notes__get`). Run `dupont` (`session-L1b`) : requête vers `claude-opus-5-5`, `max_tokens` 2048, coût au tarif OPUS (1 415 × 4 + 170 × 20 = 9 060 µ$) ; outils proposés `fetch_page, prix_ttc, notes__search, notes__get` (les deux retirés absents de ce que voit le modèle) ; `prix_ttc` en approbation, motif « approbation imposée par la configuration pour le client dupont (effets : none) », `paused`, code 1. Même demande chez `default` (`session-L1c`) : HAIKU, 6 outils, pas d'approbation, étiquette produite. **REST** (clé `cle-dupont`, `tenant: dupont`) : `GET /v1/runs` → les 5 sessions de `data/dupont` et rien d'autre ; `GET /v1/sessions/session-L1a` → fiche de `dupont` (9 événements) avec `cle-dupont`, de `default` (21 événements) avec une clé `default` ; session, run et événements de `default` lus avec `cle-dupont` → 404 « Session session-L1c inconnue », 404 « Run … inconnu », `[]` en 200 ; un corps portant `"tenant_id":"default"` → 422 `extra_forbidden`, aucun run ; un run lancé par `cle-dupont` (`session-L1d`) → 201 `paused` avec l'approbation imposée de `dupont`, journal dans `data/dupont` seulement. **MCP stdio** : `loom mcp --tenant dupont`, ressource `loom://runs` → 6 runs, ses sessions seulement (sans `session-L1c`) ; `--tenant default` → 50 runs (borne), dont `session-L1c`. Ergonomie : dans le journal de `dupont`, `model.responded` porte `model: HAIKU` et `model_id: claude-opus-5-5` (voulu : l'identifiant désigne le modèle choisi par le client), d'où « HAIKU (claude-opus-5-5) » dans ses rapports et `inspect` ; `loom validate` ne montre pas une surcharge de `telemetry` ou de `retention` d'un client (« rien de surchargé », vu en K4).

### L2 — Secrets par client, jamais dans les logs

**Catégorie** : Multi-clients

Chaque client a sa table de secrets (clés de modèle, de source d'outils) ; les valeurs ne sortent ni dans les logs ni dans les erreurs.

**Testée** : oui (22/10, agent `agent-test`, série 4, `session-L2a` à `session-L2d`, client `dupont` avec `secrets: {ANTHROPIC_API_KEY: DUPONT_ANTHROPIC_KEY}`) : isolation et non-fuite ; écart (le refus nommait la variable de la config, pas celle du client) corrigé et revalidé ; `env_from`/`headers_env` d'un serveur MCP couverts par les tests unitaires seulement (`notes` n'en a pas)

**Note** : **Redirection vers une variable absente** (`DUPONT_ANTHROPIC_KEY` non posée) : pas de repli sur la clé commune — run de `dupont` refusé avant tout appel, « Demande refusée : Modèle 'HAIKU' : la variable d'environnement ANTHROPIC_API_KEY est absente ou vide », code 2, aucun journal ; `loom validate` refuse de même (code 2) ; le même run chez `default` passe avec `ANTHROPIC_API_KEY` (`session-L2b`, 84 € TTC). `validate` liste « secrets : ANTHROPIC_API_KEY → DUPONT_ANTHROPIC_KEY ». **Fausse clé pour `dupont`** (`DUPONT_ANTHROPIC_KEY=sk-ant-api03-FAUXdupont…`, `session-L2c`) : l'appel part avec la clé de `dupont` vers `claude-opus-5-5` → 401 `authentication_error` (`model.auth`), sans réessai, `run.failed`, code 1 — la clé commune, valide, n'est donc pas partie. La fausse clé n'apparaît ni sur la sortie ni dans les logs (0), ni au journal (0) ; `model.exchanged` porte `x-api-key: [retiré]`. **Table de secrets** (`Loom.tenant(...).secrets`) : chez `default` comme chez `dupont`, la seule clé est `ANTHROPIC_API_KEY`, la seule variable que la config nomme — ni `M3_API_KEY`, ni `TOGETHER_API_KEY`, ni `AMQP_URL`, ni `LOOM_BUS_DSN`, pourtant lues dans `.env` ; vide chez `dupont` (redirection vers une variable absente), remplie chez `default` ; écriture refusée (`TypeError`, table en lecture seule). **Écart** : le message d'une clé absente nomme `ANTHROPIC_API_KEY`, qui est bien posée, et non `DUPONT_ANTHROPIC_KEY`, qui manque ; et il dit « Modèle 'HAIKU' » quand ce client appelle `claude-opus-5-5`. Cause : `_api_key()` (`adapters/models/factory.py`) — et `_secret()` pour un serveur MCP (`adapters/mcp/transports.py`) — reçoit la table du client mais ne connaît pas sa redirection. Un exploitant irait vérifier une variable qui est en place. Corrigé le 22/10 (CHANGELOG 2.0.10, « Multi-clients ») : au montage de l'agent d'un client, une variable manquante qu'il redirige est nommée avec sa redirection, le modèle avec son nom chez ce client (et de même pour un serveur MCP). Revalidé (`session-L2d`, `DUPONT_ANTHROPIC_KEY` absente) : `loom run --tenant dupont` et `loom validate` → « Demande refusée : Client 'dupont' : la variable DUPONT_ANTHROPIC_KEY (redirection de ANTHROPIC_API_KEY) est absente ou vide — modèle 'HAIKU' (claude-opus-5-5 pour ce client) », code 2. Ergonomie : l'échec 401 est dit trois fois (log ERROR, ligne « Erreur », ligne brute) ; « Demande refusée » en tête du refus de `validate`.

### L3 — Quotas et limitation de débit par client

**Catégorie** : Multi-clients

Quotas de consommation par période et `--rate-limit` par clé d'API (requêtes par minute).

**Testée** : oui (22/10, agent `agent-test`, série 5, `session-L3a` à `session-L3c`, `quotas: {runs_per_minute: 2}` sur `default`, clé `cle-debit` à 3 req/min) : quota et débit conformes dans un process qui dure ; écart de documentation (quota tenu par instance) et message du refus de débit corrigés et revalidés ; budgets par période vus en J4

**Note** : `loom validate` : « quota : 2 run(s) par minute » dans la fiche de `default`, « cle-debit : client default, portées run, read, débit 3/min ». **CLI** (`session-L3a`) : trois `loom run` successifs passent tous (code 0) — la fenêtre est tenue en mémoire de l'instance `Loom`, et chaque commande est un process neuf (« Piège 1 » du howto 03). **Python, un process** (`session-L3b`) : runs 1 et 2 `completed`, run 3 refusé avant d'ouvrir (`QuotaExceeded` « Client 'default' : 2 par minute dépassé, réessayer dans 53.3 s », `retry_after` = 60 s moins les 6,7 s écoulées depuis l'admission du run 1 : fenêtre glissante) ; run 4 pour `dupont` non compté avec `default` (fenêtre par client), parti puis en pause sur son approbation imposée ; 2 `run.started` au journal, le refus n'a laissé aucune trace. **REST** (`loom serve` relancé, clé `complet`) : runs 1 et 2 → 201, run 3 → 429 avec `Retry-After: 53` et « Client 'default' : 2 par minute dépassé, réessayer dans 53.0 s » ; 2 `run.started` dans `session-L3c`. **Débit d'une clé** (`cle-debit`, `rate_limit: {per_minute: 3}`, lectures `GET /v1/runs` seulement) : lectures 1 à 3 → 200, 4 et 5 → 429 avec `Retry-After: 60` — le débit compte aussi les lectures ; la clé `complet` passe pendant ce temps (200, fenêtre par clé) ; un premier passage fait sur un serveur dont la fenêtre n'était pas vide (5 × 429, `Retry-After: 28` constant) montre qu'une requête refusée ne décale pas l'attente. **Écart de documentation** : le howto 03 dit que `runs_per_minute` « vaut pour tous les accès (Python, REST, MCP, ligne de commande) », puis, deux lignes plus bas, qu'une commande `loom run` ne peut jamais l'atteindre (Piège 1) ; `conception.md` (« par les quatre accès »), `fonctions.md` (« valable par les quatre accès »), `configuration.md` et le code (`core/model/quota.py`, « tous accès confondus ») le présentent sans dire qu'il est **par instance** : deux workers ou deux `loom serve` accordent chacun le quota entier, la CLI ne le voit jamais. Même chose pour le débit d'une clé (fenêtre de l'application REST). Ergonomie : le refus de débit d'une clé dit « Clé 'cle-debit' : 3 requêtes par minute dépassé » — sans « réessayer dans … s » (le quota de client le dit) et avec un accord à reprendre ; l'attente n'est que dans `Retry-After`. Corrigé le 22/10 (CHANGELOG 2.0.10, « Multi-clients ») : la documentation (`conception.md`, `fonctions.md`, `configuration.md`, howto 03, docstrings) dit le quota tenu par instance (la CLI ne l'atteint pas, N process l'accordent N fois ; de même le débit d'une clé), sans changement de comportement ; le refus de débit dit « … requêtes par minute dépassées, réessayer dans N s » (⚠). Revalidé (`loom serve` relancé) : lectures 1 à 3 → 200, 4 → 429, `Retry-After: 60` et « Clé 'cle-debit' : 3 requêtes par minute dépassées, réessayer dans 60 s ». Non vérifié, signalé pour N : le budget de période d'un client repose sur un compteur en mémoire réchauffé depuis le journal une fois par fenêtre — deux `loom serve` qui durent ne verraient ensuite que leurs propres runs.

---

## M. Configuration

### M1 — Configuration déclarative validée au démarrage

**Catégorie** : Configuration

`loom.yaml` + `agents/*.yaml`, validés par Pydantic avec des erreurs qui nomment la clé ; `loom validate [--no-keys]` ; `loom schema` imprime le JSON Schema pour l'éditeur.

**Testée** : oui (22/10, agent `agent-test`, série 1, étapes 1 à 3) : validation, erreurs nommées, `--no-keys`, `loom schema` ; écart (clé en double acceptée en silence) corrigé et revalidé

**Note** : État de référence : `loom validate` → code 0 ; `--no-keys` → « clients de modèle non créés (--no-keys) : clés d'API et SDK non vérifiés », « 2 agent(s) monté(s) sans erreur », code 0 ; `loom schema` → JSON Schema `LoomConfig` sur la sortie standard, 21 clés racine, 98 définitions. Clé inconnue (`max_token` dans HAIKU) → « Configuration : …/loom.yaml: models.0.max_token — Extra inputs are not permitted », code 2 ; type faux (`shutdown_timeout: deux minutes`) → « execution.shutdown_timeout — Input should be a valid number, unable to parse string as a number », code 2. Ergonomie : un modèle est nommé par son indice (`models.0`), pas par son `id` ; le schéma publie `base_dir`, champ que le chargeur calcule lui-même. **Clé en double, 22/10** : deux `logging:` sous `telemetry` (`WARNING` puis `DEBUG`) → `validate` code 0, sans rien dire, et la seconde s'applique (une ligne INFO « Serveur MCP notes connecté » apparaît, invisible en `WARNING`) ; deux `max_iterations` dans `agents/agent.yaml` (5 puis 1) → code 0, sans rien dire. Déjà vu en K4 (deux `capture` : la seconde, `exports: content`, s'appliquait). Constat du 17/10 : **clé en double acceptée sans erreur** : PANNE portait deux `retry` dans `loom.yaml` ; `yaml.safe_load` garde la dernière, et ni le chargement ni `loom validate` ne le signalent. Une faute de frappe peut ainsi annuler un réglage en silence. Piste : un chargeur YAML qui refuse les clés en double, en donnant la ligne. Corrigé le 22/10 (CHANGELOG 2.0.10, « Configuration », ⚠) : un lecteur YAML commun (`config/yaml_source.py`) refuse une clé écrite deux fois dans le même objet — `loom.yaml`, agents, `schema_file`, suites d'évals —, la fusion `<<: *ancre` restant permise. Revalidé : second `logging` placé après `capture` → « …/loom.yaml: clé 'logging' donnée deux fois dans le même objet (lignes 60 et 62) : la seconde écraserait la première en silence », code 2 ; second `max_iterations` dans `agents/agent.yaml` → « …/agents/agent.yaml: clé 'max_iterations' … (lignes 5 et 7) … », code 2 — les lignes réelles, même non contiguës. Vu en réel le 23/10 (M6) : un second bloc `dev:` ajouté par mégarde dans `profiles` → « clé 'dev' donnée deux fois dans le même objet (lignes 65 et 69) », code 2 — auparavant, le premier bloc aurait été ignoré en silence.

### M2 — Construction entièrement en Python

**Catégorie** : Configuration

Construire `LoomConfig` et les agents en code, sans fichier, puis `Loom.from_config()` ; `Loom.register()` pour un outil.

**Testée** : oui (22/10, agent `agent-test`, série 2, script Python sans fichier, journal en mémoire, faux modèle)

**Note** : `LoomConfig(version=1, models=(ModelSpec(id="SIMULE", sdk="fake", …, params={"script": …}),), storage=StorageConfig(events=EventsStorage(backend="memory")), agents=(AgentSpec(name="devis", main=MainRole(…), tools=(PythonTool(python="prix_ttc"),)),))`, puis `Loom(config)` et `loom.register("prix_ttc", prix_ttc)` (l'outil d'agent-test, importé de `tools.utils`) : run `completed` en 2 itérations, « 250 € HT font 300 € TTC. », résultat de l'outil réellement calculé (`{'montant_ht': 250.0, 'tva': 50.0, 'montant_ttc': 300.0}`) ; `loom.events()` relit le run en mémoire. Sans `register` : `ConfigError` « Référence 'prix_ttc' introuvable : aucun objet de ce nom (enregistrés : aucun). Utiliser 'imports' ou un chemin 'module:attr' », avant tout appel. La config construite en code passe les mêmes contrôles que le YAML : sans le modèle, `ValidationError` « Agent 'devis' : modèle 'SIMULE' non déclaré (modèles connus : aucun) ». Rien d'écrit sur le disque (aucun fichier de `data/` modifié après le lancement).

### M3 — Secrets par l'environnement ou un gestionnaire

**Catégorie** : Configuration

La config ne nomme que la variable (`api_key_env: ANTHROPIC_API_KEY`) ; `SecretProvider` pour un gestionnaire de secrets.

**Testée** : oui (22/10, agent `agent-test`, série 3, `session-M3a`, `SecretProvider` simulé dans un script)

**Note** : Environnement par défaut : vu en L2 (tables de secrets d'un client, redirections). Gestionnaire : un `SecretProvider` à soi (`secrets(tenant_id) -> Mapping[str, str]`) passé à `Loom(load_config("loom.yaml"), secrets=…)`, qui rend `ANTHROPIC_API_KEY` à `default` depuis une autre variable (`MA_CLE_GESTIONNAIRE`, copiée de `.env` sans affichage) et rien à `dupont` ; `ANTHROPIC_API_KEY` absente de l'environnement du process (pas de `load_env_file`). `default` : run `completed`, « 40 € HT correspondent à 48 € TTC » — la clé est venue du gestionnaire seul ; `dupont` : `ModelConfigError` « Modèle 'HAIKU' : la variable d'environnement ANTHROPIC_API_KEY est absente ou vide », avant tout appel, sans repli ; gestionnaire interrogé pour `default` et `dupont` (tables résolues à la construction de `Loom`) ; ni la clé ni le nom de la variable du gestionnaire au journal (0). Ergonomie : avec un gestionnaire, le refus parle encore de « variable d'environnement », alors que le secret ne vient pas de l'environnement.

### M4 — Profils dev/prod et surcharges

**Catégorie** : Configuration

`profile:` et `profiles: {dev, prod}` fusionnés au chargement ; `--profile` ou `LOOM_PROFILE` l'emportent. `prod` durcit : `serve --reload` et `judges: skip` refusés ; erreurs au lieu d'avertissements pour une approbation sans journal durable, un budget en dollars sur un modèle sans tarif, un juge corrélé ou bloquant échantillonné. `dev` assouplit (approbation avec un journal en mémoire : simple avertissement).

**Testée** : oui (22/10, agent `agent-test`, série 4, `session-M4b`, bloc `profiles` temporaire)

**Note** : `profiles: {dev: {telemetry: {logging: {level: INFO}}}, prod: {telemetry: {logging: {level: ERROR}}}}`. Choix : sans rien, « Profil : aucun (ni --profile, ni LOOM_PROFILE, ni 'profile:') ; surcharges : dev, prod » ; `profile: dev` dans le fichier → « dev (par config) — assoupli » ; `LOOM_PROFILE=prod` → « prod (par LOOM_PROFILE) — les avertissements sont des erreurs », et l'emporte sur `profile:` du fichier ; `loom --profile dev` → « dev (par option) », et l'emporte sur `LOOM_PROFILE=prod`. Profil inconnu : à l'option, refusé par argparse (« argument --profile: invalid choice: 'test' (choose from 'dev', 'prod') », code 2) ; par `LOOM_PROFILE`, refusé par loom (« Profil 'test' inconnu (LOOM_PROFILE) : attendu dev ou prod », code 2). Fusion profonde (`load_config(profile=…)`) : niveau de log `WARNING` / `INFO` / `ERROR` selon le profil, `capture.raw_exchanges` voisin gardé dans les trois cas ; en `dev`, les logs INFO apparaissent bien à l'usage. **Durcissement** — journal `memory` (le temps du test) avec `add_text` en `approval: always` : sans profil, erreur « Agent 'agent' : outil 'notes__add_text' en approval: always — une approbation exige un journal durable (jsonl ou sqlite ou postgres), pas 'memory' (#28) », code 2 ; en `dev`, le même texte en avertissement, code 0 ; en `prod`, « Profil prod : Agent 'agent' : … », code 2. `loom --profile prod serve --reload` → « loom serve --reload est refusé en profil prod : un service ne se relance pas sur une modification de fichier. », code 2 ; `loom --profile prod run … --judges skip` → « Profil prod : judges='skip' retire un contrôle et n'y est pas permis », code 2, aucun journal ; en `dev`, `--judges skip` passe (`session-M4b`, code 0). En `prod`, chaque commande avertit aussi « variables lues dans …/.env (…) — en prod, préférer l'environnement du service » (voir M6). Ergonomie : `--profile` et `--config` sont des options globales, à placer **avant** la sous-commande (`loom --profile prod validate`), alors que `--tenant` et `--session` se placent après — placé après `validate`, `--profile` est refusé par argparse ; en `dev`, l'avertissement du journal non durable sort une fois par client monté, sans nommer le client.

### M5 — Contrôles de cohérence

**Catégorie** : Configuration

Rôle inconnu, modèle absent, outil introuvable, juge corrélé, variable de prompt non déclarée, source `from` invalide : refusés au chargement (code 2).

**Testée** : oui (23/10, agent `agent-test`, série 5) : modèle inconnu, outil introuvable, source `from` invalide, juge corrélé ; écart (lieu de l'erreur faux ou absent) corrigé et revalidé ; variable de prompt non déclarée → M7

**Note** : Modifications temporaires de `agents/agent.yaml`, une à la fois, chacune refusée par `loom validate` avec le code 2. **Modèle inconnu** (`main.model: SONNET`) : « …/loom.yaml: (racine) — Agent 'agent' : modèle 'SONNET' non déclaré (modèles connus : HAIKU, OPUS) » — **attribué à la racine de `loom.yaml`** alors que la faute est dans `agents/agent.yaml`. **Outil introuvable** (`- python: prix_ht`) : « Référence 'prix_ht' introuvable : aucun objet de ce nom (enregistrés : etiquette_devis, prix_ttc). Utiliser 'imports' ou un chemin 'module:attr' » — **ni fichier, ni agent, ni outil**. **Source `from` invalide** (`tool: prix_tc` sous `state.dernier_ttc`) : « Agent 'agent', state 'dernier_ttc' : from désigne 'prix_tc', qui n'est pas un outil de l'agent (outils : fetch_page, prix_ttc, etiquette_devis, notes__…) » — agent et clé nommés. **Écart** : un fichier d'agent est validé seul ; ses références croisées (modèle, serveur MCP, source, sous-agent, capacités) ne l'étaient qu'avec la config entière, attribuées à `loom.yaml` (« (racine) ») ; une référence Python n'est résolue qu'au montage (`_tool`), et l'erreur remontait sans contexte. Corrigé le 23/10 (CHANGELOG 2.0.10, « Configuration ») : quand toutes les erreurs de la config entière nomment le même agent venu d'un fichier, elles sont rapportées à ce fichier, sans « (racine) » (une faute d'ensemble reste à `loom.yaml`) ; un outil introuvable nomme son agent et l'outil. Revalidé : « Configuration : …/agents/agent.yaml: Agent 'agent' : modèle 'SONNET' non déclaré (modèles connus : HAIKU, OPUS) », code 2 ; « Configuration : Agent 'agent', outil 'prix_ht' : Référence 'prix_ht' introuvable : … (enregistrés : etiquette_devis, prix_ttc) … », code 2. Effet assumé : une erreur de capacité (un rôle qui reçoit des images, un modèle sans `vision`) est rapportée au fichier de l'agent, même quand on vient de modifier le modèle dans `loom.yaml` ; le message nomme le modèle et la capacité. **Juge corrélé** (bloc `judge: {model: HAIKU, criteria: [{name: exact, …}]}` ajouté le temps du test, l'orchestrateur étant sur HAIKU) : sans profil, avertissement « Client 'default', agent 'agent', juge 'output' : même modèle que la sortie qu'il évalue (HAIKU) : juge corrélé » (et de même pour `dupont`), code 0 ; en `prod`, « Profil prod : Client 'default', agent 'agent', juge 'output' : … juge corrélé », code 2. Le juge sur HAIKU (`forced_tool_choice: false`) est accepté au montage.

### M6 — Fichier `.env` lu au démarrage

**Catégorie** : Configuration

`loom` lit, du plus fort au plus faible : `--env-file`, `env_file:` de `loom.yaml`, `.env` voisin de `loom.yaml` ; `--no-env-file` n'en lit aucun. Une variable déjà définie (même vide) l'emporte ; pas d'interpolation ; les valeurs n'apparaissent jamais. La librairie ne lit rien seule (`load_env_file()`).

**Testée** : oui (23/10, agent `agent-test`, série 6, fichier `/tmp/autre.env` créé pour le test)

**Note** : **Par défaut** : « Env : …/agent-test/.env (par défaut) — lues : ANTHROPIC_API_KEY, M3_API_KEY, TOGETHER_API_KEY, AMQP_URL, LOOM_BUS_DSN » ; une variable déjà posée l'emporte (`M3_API_KEY=deja-la` → « déjà définies, gardées : M3_API_KEY ») ; aucune valeur affichée (0 début de clé dans la sortie). `loom --no-env-file` → « Env : non lu (--no-env-file) », puis refus faute d'`ANTHROPIC_API_KEY` (code 2). `loom --env-file /tmp/autre.env` (deux variables inoffensives) → « Env : /tmp/autre.env (par option) — lues : LOOM_ESSAI, LOOM_ESSAI_B », seul fichier lu (le `.env` du projet ne l'est pas : refus faute de clé, code 2) ; fichier demandé absent → « Fichier d'environnement introuvable (--env-file) : /tmp/absent.env », code 2. `env_file: /tmp/autre.env` à la racine de `loom.yaml` → « (par config) », à la place du `.env` voisin ; `--env-file .env` l'emporte sur la config (« (par option) », code 0). `env_file` dans `profiles.dev` → « profiles.dev.env_file — refusé : le fichier d'environnement est lu avant le choix du profil ; déclarer 'env_file' à la racine », code 2, sans profil actif (tous les profils sont vérifiés). Pas d'interpolation : `LOOM_ESSAI_B=${HOME}/x` reste littéral. Librairie : rien de lu seule (M3 : `ANTHROPIC_API_KEY` absente sans `load_env_file`) ; `load_env_file("loom.yaml", env_file=…)` rend le même rapport et ne pose que les variables du fichier. En `prod`, avertissement « variables lues dans …/.env (…) — en prod, préférer l'environnement du service » (vu en M4). Ergonomie : `--env-file`/`--no-env-file` sont des options globales, avant la sous-commande (comme `--profile`) ; dans un tube, le refus (stderr) sort avant la ligne « Env » (stdout mis en tampon).

### M7 — Variables `{{ }}` dans les prompts

**Catégorie** : Configuration

`{{ entreprise }}` dans un prompt, valeur fournie à part (par client) ; une variable non déclarée est une erreur de chargement ; le prompt rendu se vérifie dans les échanges bruts.

**Testée** : oui (23/10, agent `agent-test`, série 7, `session-M7a`, `session-M7b`, ligne `{{ entreprise }}` et `tenants[].variables` le temps du test)

**Note** : `prompts/system.md` cite `{{ entreprise }}` ; `default` : `variables: {entreprise: Plomberie Martin}`, `dupont` : `{entreprise: Dupont Chauffage}`. `loom validate` : « variables : entreprise » dans les deux fiches de client. Runs « Pour quelle entreprise travailles-tu ? » : « Je travaille pour l'entreprise Plomberie Martin. » (`default`), « … Dupont Chauffage. » (`dupont`) ; dans `model.exchanged`, le prompt système envoyé porte la valeur de chaque client (« Tu travailles pour l'entreprise Plomberie Martin. »), sans accolade restante. Variable retirée chez `dupont` seulement : « Configuration : …/loom.yaml: Agent 'agent', main.system — {{ entreprise }} : variable non définie pour le client 'dupont' (tenants[].variables) », code 2, à `validate` comme à un run de `default` (aucun journal `session-M7c`) : contrôle de démarrage, la config entière est refusée. Ergonomie : le message nomme `main.system`, pas le fichier du prompt (`prompts/system.md`) ; un client mal configuré empêche les autres de démarrer (voulu : une config incohérente ne démarre pas).

---

## N. Accès

### N1 — Librairie Python async et typée

**Catégorie** : Accès

`Loom.from_config()`, puis `run`, `stream`, `submit`, `resume`, `cancel`, `approve`, `reject`, `replay`, `evaluate`, `trace`, `report`, `sessions`, `session_state`, `aclose`…

**Testée** : oui (23/10, agent `agent-test`, groupe 1, étapes 1 à 1c, `session-N1a` à `session-N1d-*`) ; déjà vus : `run`, `events`, `trace`, `report`, `runs`, `query`, `tenant`, `register`, construction en code, `SecretProvider` (K, L, M)

**Note** : **`stream`** (`session-N1a`) : texte au fil de l'eau (`TextDelta`, « 72 € ») et 20 événements dont `run.completed`. **`submit` + `follow`** (`session-N1b`) : `submit` rend tout de suite le `run_id` (`ready_for_model`), `follow` suit jusqu'à `run.completed`, `result` → `completed`, 84 €. **`cancel`** (`session-N1c`, `fetch_page` sur httpbin `delay/10`) : appelé pendant l'outil, rend `True` aussitôt ; journal : `tool.called` puis `awaiting_tools → cancelled` et `run.cancelled {by: denis, reason: requested}`, ni résultat d'outil ni appel de modèle après ; `cancel` d'un run clos → `False`, sans erreur. **Approbations** (`approvals: {prix_ttc: always}` sur `default` le temps du groupe) : `approve(run_id, by=…)` → l'identifiant de l'appel accordé, puis `completed` (96 €) ; `approve(…, call_id=…, arguments={montant_ht: 500})` → `approval.granted` porte la correction, l'outil s'exécute avec 500 (600 €), `tool.called` garde les arguments du modèle (voulu, H4), la requête suivante prévient le modèle (« … dis-lui que denis a corrigé l'appel ») ; sans `call_id`, la correction est refusée (`ValueError` « arguments corrige un appel désigné : indiquer call_id. ») avant toute décision ; `reject(…, reason=…)` → `approval.rejected {by, reason}`, l'outil rend « Appel à prix_ttc refusé à l'approbation par denis (motif : …). C'est une décision, pas une panne… », run `completed`, réponse honnête ; `approve` d'un run fini → tuple vide, sans erreur (documenté). **Sessions** (renvoi F7, côté Python) : `sessions()` (87 journaux, le plus récent en tête), `session()` (`session-K1a` : 30 événements, 1 run, 0,00074 $), `session_files()` (`etiquette-D-42.png`, `image/png`, 157 octets), `session_state()` (`{'dernier_ttc': 600.0}`, tiré de l'argument corrigé), `export_session()` (20 événements), `delete_session()` (`events=20 artifacts=0 keys=0`, fichier effacé, puis `UnknownSession`). Paquet typé : `py.typed` présent. Un `session_id` accentué (`session-N1d-approuvé`) est refusé avant toute écriture (`UnusableId` « Session inutilisable comme nom de fichier », documenté pour JSONL). Ergonomie : en Python, ce refus sort en trace complète, là où REST rend un 422.

### N2 — Serveur HTTP REST

**Catégorie** : Accès

`loom serve [--host] [--port]` (extra `http`) : 16 routes `/v1/…` (agents, runs, hooks, events, traces, sessions, rapports), documentation sur `/docs`.

**Testée** : oui

**Note** : 23/10, `loom serve` sur agent-test, clés `lecteur` (run, read), `approbateur` (+ read_content, approve), `admin` (+ read_content, admin) ; `approvals: { prix_ttc: always }` sur `default`. Les routes sont sous `/v1` (un premier passage sans le préfixe : 404 FastAPI partout, non compté). Lectures, SSE, événements et traces vus en K3/K5, portes en H6. **Arrière-plan** : `background: true` → 202 `{run_id, session_id, status: ready_for_model}` ; `GET /v1/runs/{id}?session_id=session-N2a` → `completed` (1 itération, ≈1,1 s). Avec `lecteur` (sans `read_content`) `text` vaut `""`, avec `approbateur` le texte est en clair (« Bonjour ! Je suis l'assistant de devis… ») : masquage par portée. **Approbation** (`session-N2b`, 100 € HT) : run `paused`, `pending_approvals` avec `call_id`, `montant_ht: 100`, motif « approbation imposée par la configuration pour le client default », échéance 24 h ; `POST /v1/runs/{id}/approve` `{call_id, reason}` → 200 `calls: [toolu_…]`, le run reprend seul → `completed`, « 120 € (TVA à 20 % : 20 €) » ; journal : `approval.granted` `by: approbateur` (signé par la clé), `reason: test N2`, puis `tool.called`/`tool.completed`. **Refus** (`session-N2c`, 250 € HT) : `reject` → 200, `prix_ttc` non exécuté, l'agent dit le refus et son motif (« montant à vérifier »), n'invente pas de montant, `completed`. **Annulation** (`session-N2d`) : `paused → cancelled` (cause `cancel`), `run.cancelled` `reason: requested`, `by: approbateur`, aucun `tool.called`. **Pièces jointes** (multipart, champ `attachments`) : `chat.jpg` → `completed`, description juste ; `ment.png` (JPEG annoncé PNG) → 422 « ment.png : annoncé image/png, mais le contenu est image/jpeg », aucune session `session-N2f`. **Fichiers de session** : `GET /v1/sessions/session-N2e/files` → 1 fichier (nom = sha256, 168 786 octets, `origin: attachment`, `run_id`) ; `name` `null` pour `lecteur`, `chat.jpg` pour `approbateur` (masquage) ; téléchargement `lecteur` → 403, `approbateur` → 200, octets identiques à l'original. **Suppression** : `DELETE /v1/sessions/session-N2e` par `approbateur` → 403 « Clé sans la portée 'admin' » ; par `admin` → 200 `{events: 10, artifacts: 1, keys: 0}` ; relecture et seconde suppression → 404 ; plus rien sur disque (journal ni `.artifacts`). **`/docs` et `/openapi.json`** : 200 sans clé, même avec des clés déclarées (documentation publique ; choix à confirmer, non traité). L'OpenAPI décrit **17 chemins** `/v1/…` (18 opérations : `/v1/sessions/{id}` porte `GET` et `DELETE`), et non 16 comme le dit la description de cette fiche ; schémas de sécurité `HTTPBearer` et `APIKeyHeader`, d'où le bouton « Authorize » de `/docs`. Le chapitre « Référence » du howto 04 est resté sur l'état de 2.0.0 (« La documentation du dépôt annonce 17 routes ; l'OpenAPI en décrit 16 », écart n° 6) : à mettre à jour. **Écart 1** : un même `run_id` imposé (`run-N2-impose`) dans deux sessions du même client (`session-N2g`, `session-N2h`) ouvrait deux runs (seule la même session donnait 409) : spans dans une même trace OTel (identifiant tiré du `run_id` et du client), `GET /v1/events?run_id=…` qui mêle les deux. **Écart 2** : `POST /v1/runs/{id}/cancel` sans corps → 422 anglais de FastAPI (« Field required », `loc: [body]`), alors que son seul champ (`by`) est facultatif. Corrigés le 23/10 (CHANGELOG 2.0.10, « Accès REST ») : un `run_id` choisi par l'appelant (REST, `--run-id`, `Loom.run`/`stream`/`submit`) déjà pris dans une autre session du client est refusé avant toute écriture (`RunExists`, 409) en nommant la session qui le détient (⚠) ; `/cancel` accepte un POST sans corps (la clé signe). `approve`/`reject` gardent un corps exigé : sans `call_id`, ils tranchent tout. Revalidé (`serve` relancé ; un premier essai sur l'ancien process avait encore accepté le doublon dans `session-N2i`) : `run-N2-impose` demandé dans `session-N2k` → 409 « Le run run-N2-impose existe déjà dans la session session-N2g », `session-N2k` → 404 ; `cancel` sans corps sur le run en pause de `session-N2j` → 200 `cancelled: true`, `run.cancelled` `by: approbateur`. Limite : deux demandes simultanées du même `run_id` ne sont pas départagées. Ergonomie : un champ masqué (`text: ""`, `name: null`) ne se distingue pas d'une valeur vraiment vide ; un indicateur de masquage serait plus honnête. Audit : l'`approval.requested` d'un run annulé reste sans événement de clôture dans le journal (il ne sort des attentes que parce que le run est fini).

### N3 — Authentification par clé d'API

**Catégorie** : Accès

`loom keys create <id> [--scope run|read|read_content|approve|admin] [--agent] [--tenant] [--expires] [--rate-limit]` fabrique une clé et son empreinte à déclarer ; sans clé valide, 401.

**Testée** : en partie

**Note** : 23/10, 7 clés créées par `loom keys create` et déclarées dans `security.api_keys` ; `loom validate` les liste avec client, portées, débit, et `perimee` (`--expires 2026-10-01`) marquée « EXPIRÉE ». Clé client (`cle-dupont`) vue en L1, débit (`cle-debit`, 3/min) en L3. Sans aucune ressource réelle (identifiants inexistants) : `perimee` → 401 « Clé 'perimee' expirée le 2026-10-01 00:00 UTC » ; `lecteur` : `DELETE /v1/sessions/…` → 403 « Clé sans la portée 'admin' », `approve` → 403 « Clé sans la portée 'approve' », run avec `judges: skip` → 403 « Clé sans la portée 'admin' », refusé avant tout run (aucun journal écrit) ; `admin` et `approbateur` passent la portée et tombent sur 404 « Session … inconnue » / « Run … inconnu ». `read_content` : texte, nom de fichier et téléchargement réservés (voir N2). Non testé : `--agent` (un seul agent dans agent-test).

### N4 — CLI

**Catégorie** : Accès

`validate`, `run`, `state`, `resume`, `approve`, `reject`, `replay`, `inspect`, `eval`, `report`, `sessions`, `serve`, `mcp`, `keys`, `worker`, `retention`, `storage sql`, `schema` ; codes de sortie 0 / 1 / 2.

**Testée** : oui (23/10, agent `agent-test`, groupe 1, étape 1d, `session-N4a`) ; déjà vus : `validate`, `run`, `inspect`, `replay`, `report`, `sessions show/export`, `keys create`, `schema`, `approve` (A à M) ; `serve`, `mcp` → groupes 2 et 3, `eval` → O1

**Note** : Codes de sortie : run arrêté sur une approbation → 1 ; `loom reject <run> --by denis --reason …` → reprise jusqu'à `completed` (le modèle dit l'appel refusé), 0 ; `loom state session-N4a` → `{}` (le calcul refusé n'a rien écrit), 0 ; `loom state inconnue` → « Session inconnue inconnue », 1 ; `loom resume` d'un run fini → son résultat, 0 ; `loom sessions list` → la plus récente d'abord, 0 ; `loom sessions files session-K1a` → « etiquette-D-42.png (image/png, 157 octets, tool_output, outil etiquette_devis) », 0 ; `loom sessions delete session-N4a --yes` → « Session session-N4a supprimée : 27 événement(s), 0 fichier(s), 0 clé(s). », 0 ; `loom retention` (à blanc) → « default : aucune règle », « … Essai à blanc : rien n'a été supprimé. Ajouter --yes pour effacer. », 0 ; `loom storage sql` → le SQL Postgres (tables, rôle, politiques), 0 ; `loom worker` avec la file `asyncio` → refus « Un worker demande une file servie par un courtier ('storage.queue.backend: rabbitmq') … », 2 ; `loom run inconnu` → « Agent 'inconnu' inconnu (agents : agent, _compaction) », 2. Ergonomie : la liste des agents d'un refus montre l'agent interne `_compaction` ; `storage sql` imprime le SQL même quand aucun stockage Postgres n'est déclaré (l'aide dit « du stockage Postgres déclaré »), sans le dire ; le refus de `worker` sort sur stderr avant les lignes de stdout dans un tube.

### N5 — Serveur MCP (stdio et HTTP)

**Catégorie** : Accès

`loom mcp` (stdio) ou `server.mcp.http` monté dans `serve` : un outil par agent, plus `run_status`, `run_report`, `cancel` ; ressources `loom://…`. À brancher sur Claude Desktop.

**Testée** : oui (23/10, agent `agent-test`, groupe 3, séries 1 à 3, `session-N5a` à `session-N5e`, client SDK `mcp` en Python sans fichier) : stdio et HTTP ; écart (`/mcp` redirigé vers `/mcp/` avant la clé) corrigé et revalidé ; Claude Desktop non branché

**Note** : **stdio** (`loom mcp`, client `default`, aucune clé) : `initialize` → `loom 2.0.10`, instructions « Agents loom disponibles : agent. » ; 4 outils — `agent` (description de l'agent, entrées `message`, `session_id`, `attachments`, pas de `run_id` : MCP le choisit toujours), `run_status`, `run_report`, `cancel` (`run_id`, `session_id`, `by`) ; ressources `loom://runs`, `loom://sessions` ; 6 gabarits (`loom://runs/{run_id}{?session_id}`, `…/events`, `loom://traces/…`, `loom://sessions/{session_id}`, `…/events`, `loom://artifacts/{client}/{session}/{fichier}`). `agent` (`session-N5a`) → `completed`, `isError` faux, texte en `content` et en `structuredContent` ; `run_status` → même statut, texte et coût ; `run_report` sur la session → texte de `loom report` (1 appel HAIKU, 1812/74 tokens, 0,00026 $) et `total` structuré. Le stderr du serveur d'outils `notes` passe préfixé `[notes]`. **Pause** (`session-N5b`, `prix_ttc` à approbation) : `isError` faux, `status: paused`, texte « Run … en attente d'approbation : prix_ttc (toolu_…). Un humain doit trancher (API REST ou « loom approve »), puis run_status relit le run (run_id=…, session_id=…) », `pending_approvals` complet (`montant_ht: 40`, motif, échéance) ; `cancel` (`by: denis-mcp`) → « arrêté », `cancelled: true`, journal `run.cancelled` `by: denis-mcp` ; `run_status` → `cancelled`, plus d'attente ; second `cancel` → « déjà terminé, rien à arrêter », `cancelled: false`. **Artefact** : voir G3. **HTTP** (`server: {mcp: {http: true}}` le temps du test, `loom serve`) : `validate` → « MCP HTTP : monté sous /mcp, origines : aucune déclarée ». Sur `/mcp/` : sans clé → 401 « Clé d'API absente : en-tête 'Authorization: Bearer …' ou 'X-API-Key' », clé expirée → 401 « Clé 'perimee' expirée le … », clé fausse → 401 « Clé d'API refusée ». `lecteur` et `cle-dupont` : instructions « Agents loom : ceux que la clé d'API de la requête peut lancer (outil par agent). », 4 outils, 2 ressources. **Cloisonnement** : `cle-dupont` lance `session-N5d` → `completed` ; `loom://runs` → exactement les 10 sessions de `data/dupont/` ; `run_status` d'un run de `default` → `isError`, « Run … inconnu » ; artefact et session de `default` → « introuvable ». **Masquage** : `lecteur` (sans `read_content`) relit N5a → `completed`, `text: ''` ; son propre run (`session-N5e`) lui revient en clair ; `approbateur` relit N5a en clair. Refus de lancement par portée non éprouvé en MCP (toutes les clés de test ont `run` ; même contrôle `caller.may` qu'en REST, vu en N3). **Écart** : `server.mcp.http` montait le serveur sous `…/mcp/` seulement ; un POST sur `/mcp`, l'adresse de toute la documentation, recevait un 307 avant toute vérification de clé (`curl` sans clé et avec la clé expirée → 307) ; le SDK Python suivait la redirection en silence, d'autres clients ne la suivent pas sur un POST, un client HTTP la suit sans l'en-tête `Authorization` si l'hôte change, et derrière un proxy TLS la redirection pouvait renvoyer en `http://`. Limite connue, écrite seulement dans `Loom v2 avancement.md`. Corrigé le 23/10 (CHANGELOG 2.0.10, « Accès REST ») : `/mcp` et `/mcp/` mènent au même serveur sans redirection. Revalidé (`serve` relancé) : `/mcp` et `/mcp/`, sans clé et clé expirée → 401 ; plus aucun 307.

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
