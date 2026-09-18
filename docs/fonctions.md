# loom_V2 — Liste des fonctions

`[V2]` = nouveau ou fortement remanié par rapport à loom V1.

## A. Exécution d'un run

- **A1** Lancer un run à partir d'une demande (texte et pièces jointes) et obtenir une réponse finale.
- **A2** Boucle agentique : l'orchestrateur choisit les outils et recommence jusqu'à pouvoir répondre.
- **A3** Plusieurs appels d'outils en parallèle dans un même tour.
- **A4** Arrêts : réponse obtenue, max d'itérations, budget, outil terminal, pause pour validation humaine. En dernier recours, une réponse forcée sans outils.
- **A5** `[V2]` Annuler un run en cours.
- **A6** `[V2]` Timeout global du run.
- **A7** `[V2]` Réponse finale structurée selon un schéma.
- **A8** `[V2]` Contexte fourni par l'appelant (client, utilisateur, métadonnées), transmis aux outils et aux traces.
- **A9** `[V2]` Plusieurs agents nommés hébergés par une même instance (registre d'agents).

## B. Modèles

- **B1** Plusieurs fournisseurs : Anthropic et OpenAI-compatible, plus `[V2]` les modèles locaux (vLLM, Ollama).
- **B2** `[V2]` Format de message neutre, traduit uniquement à l'appel du fournisseur.
- **B3** Retry avec backoff, timeout, erreurs classées en transitoires ou définitives.
- **B4** `[V2]` Modèle de secours si le fournisseur est en panne.
- **B5** Cache de prompt.
- **B6** Réglages par appel (max_tokens, température, thinking), fusionnés par bloc entier.
- **B7** Vérification de la fenêtre de contexte avant l'appel.
- **B8** `[V2]` Streaming des tokens.
- **B9** `[V2]` Capacités déclarées par modèle (vision, outils, thinking, JSON natif).

## C. Rôles LLM (délégation économe en tokens)

- **C1** Déclarer un rôle : nom, description, modèle, prompt système, schéma d'entrée, réglages.
- **C2** L'orchestrateur appelle un rôle comme un outil. Le rôle ne reçoit que ce dont il a besoin, ce qui économise des tokens.
- **C3** Rôle terminal : sa sortie devient la réponse finale, telle quelle.
- **C4** Rôle vision : il reçoit les pièces jointes du run et reste masqué quand il n'y en a pas.
- **C5** `[V2]` Rôle-agent (sous-agent) : ses propres outils, sa boucle, son budget, avec une profondeur limitée.
- **C6** L'orchestrateur est lui-même un rôle (`main`).

## D. Outils

- **D1** Contrat unique d'outil : nom, description, schéma, `[V2]` capacités déclarées.
- **D2** Outils Python, synchrones ou asynchrones.
- **D3** Outils MCP (stdio, http) : découverte, puis `[V2]` reconnexion automatique.
- **D4** Validation des arguments par schéma. Les erreurs sont renvoyées au LLM pour qu'il corrige.
- **D5** Timeout par outil, erreurs isolées.
- **D6** Résultats volumineux : tronqués, puis `[V2]` déplacés vers le stockage d'artefacts.
- **D7** `[V2]` Choix des outils exposés à chaque run (filtre, client, capacité).
- **D8** `[V2]` Résultats riches : texte, JSON, images, fichiers.
- **D9** `[V2]` Exécution de code en sandbox (Firecracker), exposée comme un outil.
- **D10** `[V2]` Outils sensibles marqués « approbation requise ».
- **D11** `[V2]` Idempotence des outils à effet de bord lors d'une reprise.

## E. Fiabilité des sorties

- **E1** Contrat de sortie : schéma JSON, regex, longueur, sortie non vide.
- **E2** Réparation : on rejoue en montrant au modèle sa sortie fautive et le diagnostic.
- **E3** Juge LLM : critères, seuils, bloquant ou non, échantillonnage.
- **E4** Politique d'échec : lever une erreur ou avertir.
- **E5** `[V2]` Contrats et juge applicables à n'importe quel rôle, outil, ou à la réponse finale.
- **E6** `[V2]` Alerte si le juge utilise le même modèle que celui qu'il évalue.

## F. Mémoire et sessions

- **F1** Sessions persistantes.
- **F2** `[V2]` Historique structuré conservé tel quel, sans aplatissement, indépendant du fournisseur.
- **F3** Compaction automatique : seuil, modèle dédié, N derniers tours conservés.
- **F4** Nettoyage : thinking retiré, médias remplacés par des références.
- **F5** Backends : mémoire vive, fichier, `[V2]` Firestore ou SQL.
- **F6** `[V2]` Mémoire long terme / RAG, exposée comme un outil.
- **F7** `[V2]` Gestion des sessions : lister, supprimer, exporter (RGPD).

## G. Pièces jointes et artefacts

- **G1** Entrées validées (format, taille) : images, puis `[V2]` PDF, audio, fichiers.
- **G2** `[V2]` Stockage d'artefacts : fichiers hors historique, référencés par URI.
- **G3** `[V2]` Artefacts produits par les outils, récupérables par l'appelant.

## H. Exécution durable `[V2]`

- **H1** État du run sérialisable.
- **H2** Sauvegarde (checkpoint) à chaque étape.
- **H3** Reprise après plantage.
- **H4** Validation humaine : pause, puis approuver, modifier ou refuser, puis reprise.
- **H5** Runs en arrière-plan : soumettre, suivre le statut, récupérer le résultat.
- **H6** Déclencheurs : appel direct, file de messages, webhook, planification.

## I. Streaming et événements `[V2]`

- **I1** Flux d'événements typés pendant le run : début, tokens, appel d'outil, résultat, validation, fin.
- **I2** Consommable en Python (itérateur async) et en HTTP (SSE).
- **I3** Latence adaptée à la voix (premier token rapide).

## J. Coûts et budgets

- **J1** Comptage des tokens : entrée, sortie, cache, raisonnement.
- **J2** Coût calculé à partir d'une grille tarifaire par modèle.
- **J3** Ventilation par run, rôle, modèle et session, plus `[V2]` par client.
- **J4** Plafonds par run et par session, plus `[V2]` par client et par période. Action : avertir ou arrêter.
- **J5** Rapport de consommation.

## K. Observabilité et rejeu

- **K1** `[V2]` Trace hiérarchique, pensée pour l'affichage visuel : run → étape → appel modèle / outil / validation / sous-agent. Chaque élément a un parent, une durée, un statut et un coût.
- **K2** `[V2]` Schéma d'événements stable et versionné, qui sert de contrat pour l'interface visuelle.
- **K3** `[V2]` Capture des contenus (prompts, réponses, arguments) avec un niveau de détail réglable et masquage des données sensibles.
- **K4** Exports : JSONL et DuckDB, plus `[V2]` OpenTelemetry.
- **K5** `[V2]` API de lecture des traces : liste des runs, détail d'un run.
- **K6** `[V2]` Rejeu : à l'identique à partir des réponses enregistrées, ou avec un autre modèle ou prompt pour comparer.
- **K7** Logs techniques séparés des traces.

## L. Multi-clients `[V2]`

- **L1** Espace isolé par client : configuration, clés, outils, sessions, budgets, traces.
- **L2** Secrets par client, jamais dans les logs.
- **L3** Quotas et limitation de débit par client.

## M. Configuration

- **M1** Configuration déclarative, validée au démarrage, avec des erreurs explicites.
- **M2** `[V2]` Construction entièrement en Python, sans fichier.
- **M3** Secrets via l'environnement ou un gestionnaire de secrets.
- **M4** `[V2]` Profils (dev, prod) et surcharges.
- **M5** Contrôles de cohérence : rôle inconnu, modèle absent, juge corrélé.

## N. Accès

- **N1** Librairie Python async et typée.
- **N2** `[V2]` Serveur HTTP : lancer un run, streamer, reprendre, approuver, gérer les sessions, lire les traces et les coûts.
- **N3** `[V2]` Authentification de l'API par clé client.
- **N4** `[V2]` CLI : lancer, rejouer, inspecter une trace, valider la config.
- **N5** `[V2]` Serveur MCP (stdio et HTTP) : chaque agent est exposé comme un outil MCP.

## O. Qualité

- **O1** Runner d'évals : cas, critères, comparaison entre modèles et configurations.
- **O2** `[V2]` Faux modèles et faux outils fournis, pour des tests déterministes.
- **O3** `[V2]` Tests de non-régression construits à partir de traces enregistrées.

## Hors périmètre

Pas d'interface, pas de logique métier, pas de framework multi-agents générique. La seule délégation est hiérarchique : orchestrateur → rôles et sous-agents.

---

# Découpage en composants

## Principes structurants

1. **Le noyau ne fait aucune entrée-sortie.** Il ne connaît que des interfaces (les « ports »). Les implémentations concrètes (« adaptateurs ») restent en périphérie.
2. **Tout passe par des événements.** Le moteur les émet. Le suivi des coûts, les traces, le streaming et le rejeu les consomment. Pas de ContextVars.
3. **Un run est un état.** La boucle fait avancer un `RunState` pas à pas. La sauvegarde à chaque étape, la reprise, la pause pour validation humaine et le rejeu en découlent directement.

## Couches

```
Accès          API Python · HTTP REST · MCP · CLI
Application    Agents · Runner · Sessions · Tenancy · Replay/Evals
Moteur         AgentLoop · ToolExecutor · Roles · Guards · Hooks
Noyau          Modèle de domaine · Ports · Événements
Adaptateurs    Providers · MCP · Stores · Sinks · Queue · Sandbox
Transverse     Config/Builder · Kit de test
```

Les couches ne dépendent que de celles du dessous. Les adaptateurs implémentent les ports du noyau.

## Composants

| # | Composant | Responsabilité | Fonctions |
|---|---|---|---|
| 1 | **core.model** | Messages et blocs de contenu, pièces jointes, `RunState`, `Usage`, événements typés (enveloppe, payloads, facettes) | B2, H1, K2, I1 (types) |
| 2 | **core.ports** | Interfaces `ModelClient`, `ToolSource`, `EventStore` (journal, requêtes), `ArtifactStore`, `EventSink` (exports), `BusBackend`, `IdempotencyStore`, `SecretProvider`, `TaskQueue` | socle de tout le reste |
| 3 | **models** | Adaptateurs par SDK (`anthropic`, `openai`, champ `sdk` du LLM), classement des erreurs, retry, modèle de secours et disjoncteur, cache, réglages, fenêtre de contexte, `stream()` comme primitive, capacités | B1, B3–B9, I3 |
| 4 | **tools** | Contrat d'outil (`side_effects`, `approval`, `idempotent`), `ToolOutput` en blocs, outils Python et MCP (`scope`, cycle de vie, préfixes), `artifact_read`, sandbox, sélection des outils par run | D1–D3, D7–D9, F6 |
| 5 | **engine.executor** | Exécution des outils : parallélisme, validation, résolution des `$ref`, timeout, déport au-delà d'un seuil, approbation (lot partiel puis pause), clé d'idempotence et reprise | A3, D4–D6, D10, D11 |
| 6 | **engine.roles** | Rôle LLM délégué, sous-agent, rôle terminal, rôle vision, `main` ; contexte déclaré, références `$ref`, `input_template` | C1–C6 |
| 7 | **engine.loop** | Avance le `RunState` d'un pas, gère les arrêts, la réponse forcée et la réponse structurée | A2, A4, A7 |
| 8 | **engine.hooks** | Politiques : points d'accroche (`before_model`, `after_model`, `before_tool`, `after_tool`, `on_output`), décisions typées, composition, garde-fous | support de E5, J4, H4, D10 |
| 9 | **guards** | Contrats de sortie, normalisation déterministe, réparation par le modèle auteur, juge, politique d'échec, détection du juge corrélé | E1–E6 |
| 10 | **usage** | Ledger = projection du journal (`model.responded`), tarifs, ventilation, plafonds, rapport | J1–J5 |
| 11 | **sessions** | Projection de l'historique LLM depuis le journal, snapshots, compaction en tâche de fond via l'agent interne `_compaction` (`session.compacted`, contrôle de fidélité, filet synchrone `ensure_fits`), nettoyage, backends `EventStore` (JSONL, SQLite/Postgres, Firestore), RGPD | F1–F5, F7 |
| 12 | **artifacts** | Validation des entrées, stockage par URI, fichiers produits par les outils | G1–G3 |
| 13 | **agents** | `AgentSpec` et `AgentRegistry` : définition et enregistrement des agents nommés | A9 |
| 14 | **runtime** | Cycle de vie d'un run : lancer, streamer, annuler, timeout, pause et reprise par relecture du journal, approbation (asynchrone ou approbateur en ligne), arrière-plan (`TaskQueue`, `recover()`, concession par run), déclencheurs, contexte appelant, planification de la compaction en fin de run | A1, A5, A6, A8, H2–H6 |
| 15 | **telemetry** | Bus d'événements (durables et éphémères, abonnés, resynchronisation par `seq`), traces = projection du journal en arbre de spans, projection `run_summaries`, niveaux de capture et masquage, exports (`EventSink`, dont OTel), API de lecture (`EventQuery`), logs | I1, K1, K3–K5, K7 |
| 16 | **replay** | Rejeu à partir des réponses modèle et outils du journal, à l'identique ou en variante (outils à effet de bord jamais réexécutés), détection de divergence (`request_hash`, transitions), échanges bruts en opt-in, évals, non-régression | K6, O1, O3 |
| 17 | **tenancy** | Contexte client (`default` implicite), surcharges par client (liste fermée), secrets, quotas et limitation de débit, `TenantRouter` pour l'isolation physique | L1–L3 |
| 18 | **config** | Schéma Pydantic, chargement YAML (`system_file`, `agents_dir`), JSON Schema généré pour l'éditeur, profils, secrets, contrôles de cohérence, Builder | M1–M5 |
| 19 | **access** | Trois points d'accès sur le même registre d'agents : API Python, HTTP REST (FastAPI, SSE), MCP (stdio, HTTP) ; authentification ; CLI | N1–N5, I2 |
| 20 | **testing** | Faux modèles et faux outils | O2 |

---

# Points à trancher

La numérotation est conservée pour pouvoir y faire référence.

## Décidés

### 1. Budget et guards : politiques en hooks, observation par abonnés

Deux mécanismes séparés :

| Mécanisme | Rôle | Peut influencer le run ? | Exemples |
|---|---|---|---|
| **Politiques (hooks)** | Décider, dans le flux de `step` | Oui | budget, contrats, juge, approbation, sélection d'outils |
| **Abonnés (bus)** | Observer les événements | Non | ledger en direct, SSE, exports, logs |

Les métriques et le masquage relèvent de l'observation, pas des hooks.

**Points d'accroche dans `step` :**

| Point | Exemples de politique |
|---|---|
| `before_model` | budget, contexte injecté, filtrage des outils |
| `after_model` | contrat sur la réponse finale |
| `before_tool` | approbation, droits du client, validation des arguments |
| `after_tool` | contrat ou juge d'un rôle, déport des gros résultats |
| `on_output` | juge sur la réponse finale |

### 2. Décisions des hooks

Un hook renvoie une décision typée :

```
Decision = Continue
         | Replace(value)        # valeur du même type que l'entrée
         | Retry(feedback)       # rejouer avec un diagnostic (réparation)
         | Deny(reason)          # outil refusé → résultat d'erreur pour le modèle
         | Pause(reason)         # → PAUSED (approbation)
         | Stop(reason)          # → FINALIZING (budget)
         | Fail(error)           # → FAILED
```

**Décisions autorisées par point :**

| Point | Continue | Replace | Retry | Deny | Pause | Stop | Fail |
|---|---|---|---|---|---|---|---|
| `before_model` | ✓ | requête | | | | ✓ | ✓ |
| `after_model` | ✓ | | ✓ | | | ✓ | ✓ |
| `before_tool` | ✓ | arguments | | ✓ | ✓ | | ✓ |
| `after_tool` | ✓ | résultat | ✓ | | | | ✓ |
| `on_output` | ✓ | réponse | ✓ | | | | ✓ |

Une décision non autorisée à un point donné est une erreur de config, détectée au démarrage.

**Composition :**

- Les hooks s'exécutent dans l'ordre déclaré. `Replace` transmet la valeur modifiée au hook suivant. Toute autre décision que `Continue` ou `Replace` arrête la chaîne.
- Chaque décision autre que `Continue` écrit un événement durable `policy.decided` (hook, point, décision, motif), pour le rejeu et l'audit.
- Rejeu : en mode identique, les décisions enregistrées sont réutilisées ; en mode variante, les hooks sont réévalués.

**Garde-fous :**

- `Retry` est borné (`max_attempts`), et le compteur vit dans le `RunState`.
- Chaque hook a un timeout.
- Si un hook lève une exception, le comportement est configurable par hook : bloquer le run (par défaut pour le budget et l'approbation) ou laisser passer.
- Les hooks sont asynchrones et déterministes pour un état donné (échantillonnage par hash du `run_id`).

### 3. Boucle : machine à états pilotée par événements (option B)

L'état est une donnée explicite. La boucle se réduit à trois pièces :

```
apply(state, event) -> RunState               # pure, aucune I/O
step(state, deps)   -> AsyncIterator[Event]   # exécute UN effet, émet des événements
drive(run_id, deps):                          # le pilote
    state = fold(apply, store.read(run_id))
    while state.status est actionnable:
        async for ev in step(state, deps):
            await store.append(ev)            # écrit au fil de l'eau
            state = apply(state, ev)
```

`step` est un générateur : chaque résultat d'outil est écrit dès qu'il arrive (granularité du point 25).

**États d'un run :**

```
            ┌──────────────── tool results ────────────────┐
            ▼                                              │
  ┌─▶ READY_FOR_MODEL ── model.responded ──▶ AWAITING_TOOLS ┤
  │         │ (pas d'outil)                    │  │          │
  │         ▼                                  │  └─ sous-agent ─▶ WAITING_CHILD
  │     COMPLETED ◀── outil terminal ──────────┘
  │         ▲                                  │
  │         │                          approbation requise
  │     FINALIZING  ◀── budget / max_iter      ▼
  │   (réponse forcée,                       PAUSED ── approval.granted ──┐
  │    sans outils)                                                        │
  └────────────────────────────────────────────────────────────────────────┘
                     (+ FAILED, CANCELLED depuis tout état)
```

Chaque état correspond à un seul type d'effet : appel modèle, lot d'outils, réponse forcée, ou attente d'un sous-agent.

**Ce que ça règle :**

- Reprise (H3, 26) : l'état est reconstruit depuis le journal ; seuls les appels d'outils sans `tool.completed` sont relancés.
- Pause (H4) : `PAUSED` n'exécute rien, le process peut s'arrêter ; un `approval.granted` venu de l'API relance `drive`.
- Annulation et timeout (A5, A6) : vérifiés entre deux étapes ; pendant une étape, l'annulation asyncio de l'effet émet `run.cancelled`.
- Sous-agent (C5) : l'outil lance le `drive` de l'enfant ; le parent passe en `WAITING_CHILD` si l'enfant se met en pause.
- Streaming (I1) : pendant l'appel modèle, `step` publie des `model.delta` éphémères sur le bus.
- Arrière-plan (H5) : n'importe quel worker peut reprendre `drive(run_id)`.
- Tests : `apply` se teste sans rien simuler, `step` avec un faux modèle.

**Règles :**

1. `RunState` ne contient que des données sérialisables. Modèles, outils, stores et hooks sont passés dans `deps` (`RunContext`).
2. `apply` ne décide rien : il enregistre des faits. Les décisions sont prises dans `step` et exprimées par des événements.
3. Les hooks s'exécutent dans `step`, autour de l'effet. Ce qu'ils décident (pause, arrêt, remplacement) devient un événement.

**Suivi de la machine à états journalisé** (rejeu, plantage, observabilité) :

Le pilote `drive` (et non `apply`) écrit trois événements durables :

| Événement | Champs |
|---|---|
| `step.started` | step_no, état, effet (`model_call`, `tool_batch`, `finalize`, `wait_child`), span_id |
| `step.completed` | step_no, durée, nombre d'événements émis, statut |
| `run.transitioned` | from, to, step_no, cause (type et `event_id` de l'événement déclencheur) |

- **Plantage :** un `step.started` sans `step.completed` identifie l'effet interrompu, et la reprise sait quoi relancer. Le dernier `run.transitioned` donne le dernier état sûr.
- **Rejeu :** l'état recalculé par `fold` est comparé aux `run.transitioned` enregistrés. Tout écart est signalé comme une divergence (bug de `apply` ou changement de comportement).
- **Observabilité :** chronologie des états dans l'interface, temps passé par état (attente d'approbation, outils, modèle), facettes `from` et `to` pour les requêtes (par exemple, les runs restés en `PAUSED` plus de 24 h).
- **Logs techniques (K7) :** chaque transition produit aussi une ligne de log corrélée par `run_id` et `span_id`.

**Inconvénients :** plus de structure qu'une simple boucle ; risque de logique dupliquée entre `step` et `apply` (écarté par la règle 2) ; les pauses doivent être durables (point 28).

### 4. Sous-agent (C5) : même boucle, `RunState` enfant

Un appel à un sous-agent crée un `RunState` enfant, sauvegardé séparément et lié à son parent.

```
RunState
  run_id, root_run_id, parent_run_id, parent_call_id, depth
  agent, status, step, messages, pending_calls, usage, budget, context
```

- **Création :** l'outil `AgentTool` du parent crée l'enfant. L'enfant hérite du contexte (client, utilisateur) et reçoit une part du budget du parent.
- **Isolation :** l'historique de l'enfant n'entre jamais dans celui du parent. Seule sa sortie finale revient au parent, comme résultat d'outil.
- **Consommation :** la consommation de l'enfant est ajoutée à celle du parent. Le `root_run_id` permet de lire tout l'arbre des runs.
- **Profondeur :** `depth` est limité par `max_depth`.
- **Reprise :** l'appel en cours du parent garde l'identifiant de son enfant. Après un plantage, on reprend l'enfant au lieu de le relancer.
- **Pause :** si l'enfant attend une validation, le parent passe à l'état `waiting_child`.
- **Annulation :** elle se propage du parent vers ses enfants.
- **Session :** un enfant est éphémère par défaut, sans session propre.

### 5. Bus d'événements : en mémoire, journal comme source

Le journal fait foi, le bus sert à notifier vite.

```
drive ──append──▶ EventStore ──publish──▶ Bus ──▶ abonnés
      └──── model.delta (éphémères) ─────▶ Bus
```

- **Ordre :** écriture dans le journal, puis publication. Un événement notifié existe toujours dans le journal.
- **Publication non bloquante :** le run n'attend jamais un abonné.
- **File par abonné :** chaque abonné a sa propre file bornée. S'il déborde, il perd les événements éphémères, reçoit un marqueur de trou et se resynchronise depuis le journal (`after_seq`).
- **Abonnés qui ont besoin de tout** (exports OTel, ledger persistant) : ils lisent le journal avec un curseur, le bus ne leur sert que de signal.
- **Reconnexion SSE :** `Last-Event-ID` vaut `seq`. Relecture du journal jusqu'au présent, puis bascule sur le direct.
- **Plusieurs workers en mode service :** port `BusBackend`, en mémoire par défaut, avec des adaptateurs Postgres `LISTEN/NOTIFY`, Redis ou RabbitMQ.

### 6. Types du domaine : Pydantic

Imposé par les événements typés (22) : sérialisation du `RunState`, des événements et des échanges HTTP, et génération du JSON Schema.

### 7. Raisonnement (thinking), quel que soit le fournisseur

Le LLM `main` n'est pas forcément Anthropic.

- Le `RunState` conserve un bloc `Reasoning` neutre, quel que soit le fournisseur.
- C'est l'adaptateur qui décide quoi renvoyer au modèle, selon ses capacités (B9) :
  - Anthropic exige les blocs signés pendant une boucle d'outils.
  - L'API Responses d'OpenAI accepte en retour un raisonnement chiffré.
  - D'autres API compatibles OpenAI l'ignorent, voire le refusent.
- Si le modèle change en cours de run (modèle de secours), le raisonnement produit par un autre fournisseur est écarté.
- Le raisonnement est retiré au stockage de la session.

### 8. `provider_meta` typé

- **Structure :** `provider_meta: dict[provider, Meta]`, où chaque `Meta` est un modèle Pydantic typé, par exemple `AnthropicMeta(signature, redacted_data)` ou `OpenAIMeta(item_id, encrypted_content)`.
- **Qui lit quoi :** l'adaptateur remplit sa propre entrée en lisant la réponse, puis la relit pour construire la requête suivante. Il ignore les entrées des autres fournisseurs.
- **Ce qui reste hors de `provider_meta` :** les notions qui existent chez tous les fournisseurs, comme un point de cache, restent neutres dans le noyau (`cache_breakpoint`), et chaque adaptateur les traduit.

### 9. Accès aux modèles : SDK officiels

- **Champ `sdk` dans la définition du LLM :** `anthropic` ou `openai`. Il choisit l'adaptateur ; le fournisseur n'est qu'une affaire de config (`base_url`, clé, capacités).
  - MiniMax : `sdk: anthropic`.
  - Together, vLLM, Ollama : `sdk: openai`.

  Exemple (format de config à définir, point 35) :

  ```
  id: M3_MAIN
  sdk: anthropic
  base_url: https://api.minimax.io/anthropic
  model: MiniMax-M3
  api_key_env: M3_API_KEY
  ```

- **Champ `api` (pour `sdk: openai`) :** `chat` (par défaut) ou `responses`.
  - `chat` : API Chat Completions, pour les fournisseurs compatibles (Together, vLLM, Ollama).
  - `responses` : API Responses, qui permet de renvoyer le raisonnement chiffré (point 7).
  - Avec `sdk: anthropic`, le champ `api` est refusé à la validation de la config.
- **SDK en extras** (`loom-ia[anthropic]`, `loom-ia[openai]`), importés à la demande. Ils n'apparaissent que dans leur adaptateur.
- **Retries internes du SDK désactivés** (`max_retries=0`) : une seule politique de retry, celle de loom, visible dans le journal.
- **Tests de contrat par adaptateur**, avec des réponses HTTP enregistrées (respx).

### 10. Modèle de secours

**Classement des erreurs par l'adaptateur :**

| Type d'erreur | Retry sur le même modèle | Bascule vers le secours |
|---|---|---|
| `transient` (429, 5xx, timeout, réseau) | ✓ avec backoff, en respectant `Retry-After` | ✓ une fois les retries épuisés |
| `overloaded` | ✓ | ✓ une fois les retries épuisés |
| `quota_exhausted` (facturation) | | ✓ immédiatement |
| `context_overflow` | | optionnel, vers un modèle à plus grande fenêtre |
| `auth` (401, 403) | | ✗ erreur de config, run en échec |
| `invalid_request` (400) | | ✗ bug, run en échec |
| `content_filtered` | | ✗ par défaut |

**Règles :**

- Chaîne déclarée par rôle : `model: M3_MAIN, fallbacks: [SONNET]`.
- Compatibilité vérifiée au démarrage : le secours doit avoir les capacités que le rôle exige (outils, vision, sortie structurée, fenêtre de contexte).
- Adhérence : une fois qu'un run a basculé, il reste sur le secours jusqu'à la fin (pas d'allers-retours, pas de perte répétée du raisonnement, point 7).
- Disjoncteur par modèle : après N échecs, le modèle est écarté pendant T secondes pour tous les runs, qui vont directement au secours.
- Coût : le cache de prompt est perdu à la bascule ; le budget utilise le tarif du modèle de secours.
- Juge corrélé (21) : les chaînes de secours sont aussi vérifiées.
- Événements durables : `model.retried` (tentative, type d'erreur, délai) et `model.fell_back` (ancien modèle, nouveau modèle, motif).

### 11. Streaming comme primitive de base

**Port minimal :** `ModelClient.stream(request) -> AsyncIterator[ModelChunk]`. `complete()` est un utilitaire générique qui rassemble le flux : un seul chemin de code.

**Morceaux neutres :** `TextDelta`, `ReasoningDelta`, `ToolCallStarted`, `ToolArgsDelta`, `ToolCallEnded`, `UsageDelta`, `Stopped(reason)`. Le `provider_meta` peut arriver en cours de flux (par exemple la signature du thinking Anthropic, en fin de bloc).

**Accumulation :** les deltas partent sur le bus comme `model.delta` (éphémères) ; la réponse complète est écrite dans le journal comme `model.responded`. Les arguments d'outils partiels ne sont jamais transmis aux hooks.

**Cas délicats :**

| Cas | Traitement |
|---|---|
| Échec après le début du flux | L'appel entier est relancé ; `model.delta.reset` indique à l'interface d'effacer le texte partiel |
| Fournisseur sans streaming | L'adaptateur simule un flux d'un seul morceau (capacité `streaming: false`) |
| Usage donné seulement en fin de flux | Géré par l'adaptateur (par exemple l'option `include_usage` chez OpenAI) |
| Délais | Trois timeouts : premier token, silence entre deux morceaux, total |
| Guard sur la réponse finale | Réglage par agent `stream_output: live \| after_guards` : `live` par défaut sans guard sur la sortie, sinon réponse mise en tampon et envoyée après validation |

**Voix (I3) :** la voix exige `live`. Les guards d'un agent vocal doivent être légers et compatibles avec le flux (règles incrémentales), ou ne s'appliquer qu'aux outils.

### 12. Ce que reçoit un rôle

Par défaut, un rôle ne reçoit que ses arguments. Il peut en plus déclarer du contexte, pris dans une liste fixe, pour que l'orchestrateur n'ait pas à recopier l'information (tokens de sortie coûteux, risque de reformulation) :

| Contexte | Contenu | Gain |
|---|---|---|
| `user_input` | Message original de l'utilisateur, mot pour mot | Plus de recopie par l'orchestrateur |
| `attachments` | Pièces jointes du run (références) | Remplace le cas particulier « vision » |
| `tool_results: [noms]` | Tous les résultats réussis de ces outils dans le run, dans l'ordre des appels | Plus de gros JSON recopié |
| `session_summary` | Dernier résumé de compaction | Contexte sans l'historique complet |
| `last_turns: N` | N derniers échanges de la session | Au cas par cas |
| `caller_context` | Métadonnées de l'appelant (A8) | Client, utilisateur |

- **Contexte manquant :** si un outil de `tool_results` n'a encore rien donné dans le run, le rôle n'est pas appelé ; l'orchestrateur reçoit une erreur qui lui dit d'appeler d'abord cet outil. Seuls comptent les résultats obtenus avant le tour en cours.
- **Références dans les arguments :** l'orchestrateur peut passer `{"$ref": "result:<n>"}` au lieu de recopier un résultat, dans les arguments de n'importe quel outil. `result:3` désigne le 3ᵉ appel d'outil du run, dans l'ordre des demandes du modèle. L'exécuteur la remplace par le `data` du résultat, sinon par son texte, avant de valider les arguments. Une chaîne dont tout le contenu est cet objet (`"{\"$ref\": \"result:3\"}"`, écrit ainsi par MiniMax-M3) est résolue de la même façon. `tool.called` garde les arguments tels que le modèle les a écrits, plus la liste `refs` des références résolues.
- **Numéros visibles par le modèle :** rien ne garantit qu'un modèle voie les `call_id` des fournisseurs. Quand l'agent a des rôles, chaque résultat qu'il reçoit commence donc par sa référence (`[result:3]`) et une consigne sur `$ref` s'ajoute à son prompt système. Ce marquage n'existe que dans la requête, pas dans le journal. Sans rôle, les requêtes sont inchangées.
- **Description vue par l'orchestrateur :** elle est complétée automatiquement par le contexte que le rôle reçoit déjà (« Reçoit déjà, inutile de les transmettre : la demande de l'utilisateur ; les résultats de chercher_devis. »). Sans cette mention, MiniMax-M3 transmettait le devis en argument alors que le rôle l'avait déjà.
- **Arguments d'un rôle :** sauf `additionalProperties` déclaré dans son `input_schema`, un rôle refuse les arguments non prévus, comme un outil Python. Un argument inventé par l'orchestrateur lui revient en erreur au lieu d'être ignoré sans bruit.
- **Construction du message :** le rôle déclare un `input_template` explicite. La règle implicite de V1 (clés `input`, `text`…) disparaît. Sans template, le rôle reçoit les blocs de contexte suivis des arguments en JSON, balisés (`<user_input>`, `<tool_result tool="…" ref="result:n">`, `<caller_context>`, `<arguments>`).
- **Journal d'un appel de rôle :** l'appel de modèle du rôle est journalisé dans le run de l'orchestrateur, entre `tool.called` et `tool.completed`, dans un span sous celui de l'appel : `model.responded` porte le `call_id` de l'appel et l'enveloppe le nom du rôle. Seuls son usage et son coût s'ajoutent au run ; il ne compte pas dans les itérations. Les appels de l'orchestrateur sont attribués au rôle `main`.

### 13. Outil terminal

La règle de V1 est conservée : un outil terminal n'est terminal que s'il est seul dans le tour et qu'il n'a pas échoué. Sa sortie est alors la réponse finale.

1. **Guards :** la sortie terminale passe par les hooks `on_output` et suit le réglage `stream_output`.
2. **Réponse structurée (A7) :** si l'agent déclare un schéma de sortie, la sortie terminale doit le respecter.
3. **Échec :** en cas d'erreur, ou de contrat non satisfait après réparation, le résultat revient à l'orchestrateur comme un résultat normal.
4. **Appel en parallèle :** la description de chaque outil terminal reçoit automatiquement une mention « à appeler seul ». Si l'orchestrateur l'appelle quand même avec d'autres outils, la règle ne s'applique pas et l'orchestrateur compose la réponse. Le cas est signalé par un avertissement dans les logs ; l'événement `policy.decided` viendra avec les hooks (phase 3.1, backlog #008).
5. **Plafond d'itérations :** si l'outil terminal termine le lot qui atteint `max_iterations`, il l'emporte sur `FINALIZING`.

**Journal :** pas de duplication. `run.completed` référence le `tool.completed` terminal ; l'historique LLM affiche la sortie comme réponse finale, avec un marqueur à la place du résultat d'outil.

### 14. Images transmises aux rôles vision

Le modèle vision doit accepter la forme choisie.

- Le `RunState` ne garde qu'une référence vers le stockage d'artefacts.
- L'adaptateur la résout au moment de l'appel, selon les capacités déclarées du modèle : `image_input: base64 | url | file_id`, taille maximale, formats acceptés.
- Par défaut, l'image est envoyée en base64, lue depuis le stockage d'artefacts. Une URL signée à courte durée de vie n'est utilisée que si le modèle l'accepte et que la config l'autorise (RGPD).
- Si le format ou la taille ne conviennent pas au modèle : erreur explicite avant l'appel, ou conversion via un hook.

### 15. Format des résultats d'outils

```
ToolOutput
  blocks      Text | Json | ImageRef | FileRef   # ce que voit le modèle
  data        JSON structuré optionnel            # pour $ref, A7, l'interface
  is_error    bool
  artifacts   références produites (G3)
```

- **Outils Python :** une `str` devient `Text` ; un `dict`, une `list` ou un modèle Pydantic deviennent `Json` ; une `Image` est stockée comme artefact et renvoyée en `ImageRef` ; un `ToolOutput` explicite donne le contrôle total.
- **Erreurs :** une exception quelconque donne `is_error`. Une `ToolError(message)` sert aux messages que le modèle peut exploiter pour se corriger.
- **MCP :** le contenu (`text`, `image`, `resource`…), `structuredContent` et `isError` se traduisent directement.
- **Selon le modèle (B9), capacité `tool_result_media` :** si le modèle n'accepte pas d'image dans un résultat d'outil, l'adaptateur la place dans un message utilisateur juste après, ou la remplace par sa référence.

### 16. Gros résultats

- Seuil par outil, avec une valeur par défaut globale.
- Au-delà du seuil : le contenu complet va dans le stockage d'artefacts (`artifact.stored`) ; le modèle reçoit un aperçu (début du texte, ou pour du JSON la structure : clés, premiers éléments, nombre total), la taille et la référence.
- Accès au contenu complet :
  - par un outil intégré `artifact_read(ref, offset, limit)`, exposé seulement si un déport a eu lieu dans le run ;
  - ou par `$ref` (point 12) : l'orchestrateur transmet la référence à un rôle sans lire le contenu.
- Repli : la troncature simple ne sert que si aucun stockage d'artefacts n'est configuré. En mode librairie, le défaut est un stockage fichier local.
- Journal : `tool.completed` porte l'aperçu, la référence et une facette `offloaded`.

### 17. Approbation

- **L'outil déclare** `side_effects: none | reversible | irreversible` et `approval: never | always | policy` (`never` par défaut).
- **Une politique `before_tool` peut exiger une approbation** sur n'importe quel outil : par client, par agent, ou selon les arguments (montant supérieur à X, destinataire externe…).
- **Elle ne peut la lever que si l'outil n'est pas en `always`.** Pour un outil en `always`, seule la config admin peut la lever.
- **Déroulé :**

  ```
  Pause → approval.requested (outil, arguments, motif, scope requis, expire_at)
        → approval.granted  (arguments éventuellement modifiés → Replace)
        | approval.rejected (→ Deny : motif renvoyé au modèle)
        | approval.expired  (→ Deny ou Fail, configurable)
  ```

- **Lot parallèle :** les appels qui ne demandent pas d'approbation s'exécutent, puis le run passe en `PAUSED` pour les autres.
- **Audit :** l'identité de l'approbateur (clé API ou utilisateur) est enregistrée.
- **Canal :** l'API REST (scope `approve`) ou l'elicitation MCP, jamais un outil MCP (point 39).

### 18. Idempotence

- **Clé stable :** `hash(run_id, call_id)`, transmise dans le `ToolContext`. Elle reste identique après une reprise ou un retry.
- **L'outil déclare** `idempotent: true | false`.
- **Reprise d'un appel sans `tool.completed` :**

| Cas | Traitement |
|---|---|
| `side_effects: none` ou `idempotent: true` | Réexécution |
| Effet de bord, non idempotent | Pas de réexécution à l'aveugle : pause pour vérification humaine, ou erreur « état inconnu après interruption » renvoyée au modèle (configurable, erreur par défaut) |

- **Aides pour les auteurs d'outils :**
  - port `IdempotencyStore` et décorateur `@idempotent`, qui mémorise le résultat par clé (implémentations : point 49) ;
  - clé à transmettre aux API externes qui la prennent en charge (en-tête `Idempotency-Key`), seule vraie protection si le plantage survient entre l'action externe et l'enregistrement du résultat.
- **MCP :** la clé est transmise dans le champ `_meta` de l'appel (convention loom, que le serveur doit exploiter).

### 19. Sessions MCP

| `scope` | Connexion | Usage |
|---|---|---|
| `shared` (défaut) | Une par process, partagée par tous les runs | Serveurs sans état (math, heure) |
| `tenant` | Une par client, avec ses identifiants (via `SecretProvider`) | Serveurs qui détiennent des données client (CRM…) |
| `run` | Ouverte et fermée avec le run | Serveurs à état, sandbox |

- **Cycle de vie :** connexion à la première utilisation ; contrôle de santé et reconnexion avec backoff (D3), disjoncteur ; fermeture après inactivité ; liste d'outils en cache, rafraîchie sur `tools/list_changed`.
- **Espaces de noms :** les outils sont préfixés par le serveur (`crm__rechercher`).
- **Catalogue :** les outils découverts peuvent varier d'un client à l'autre ; la sélection par run (D7) en tient compte.

**Plusieurs serveurs par agent :**

```yaml
tools:
  - python: myapp.tools:chercher_devis
  - mcp: crm
    include: [rechercher, fiche_client, envoyer_email]
  - mcp: agenda
    exclude: [supprimer_creneau]
  - mcp: documents
    alias: docs                       # préfixe plus court : docs__lire
    required: true
    tools:
      archiver: {approval: always}    # surcharge propre à cet agent
```

- Les serveurs sont déclarés une fois dans `mcp_servers` et référencés par un ou plusieurs agents.
- Préfixe systématique (`crm__rechercher`, `agenda__creneaux`) ; `alias` raccourcit le préfixe.
- `include` ou `exclude`, pas les deux ; sans filtre, tous les outils du serveur sont exposés.
- Déclarations des outils (`side_effects`, `approval`, `idempotent`), du plus faible au plus fort : annotations MCP du serveur (`readOnlyHint`, `destructiveHint`, `idempotentHint`), config globale du serveur (`mcp_servers[].tools`), référence dans l'agent. Une politique ne peut jamais lever un `approval: always`.
- Un même agent peut mélanger les portées (`shared`, `tenant`, `run`) ; chaque connexion suit la sienne.
- Démarrage indépendant : un serveur indisponible voit ses outils retirés pour le run (avec un avertissement), les autres restent utilisables ; `required: true` rend son absence bloquante.
- Contrôles au démarrage : serveur référencé mais non déclaré ; outil de `include` ou `exclude` inconnu une fois le serveur connecté (avertissement) ; deux références qui produisent le même préfixe.

### 20. Réparation de la réponse finale

**Principe :** la réparation est faite par le modèle qui a produit la sortie, dans sa propre conversation. Pour un rôle, c'est le modèle du rôle ; pour la réponse finale, c'est l'orchestrateur. (Le découpage « orchestrateur + transcripteur » a échoué dans loom-report-demo.)

**Étape 0, normalisation déterministe :** retrait des blocs de code, extraction du JSON, nettoyage des espaces. Aucun coût LLM ; `guard.checked` le signale avec `normalized: true`.

**Étape 1, tour de réparation de l'orchestrateur** (le run repasse en `READY_FOR_MODEL`) :

| Aspect | Règle |
|---|---|
| Message de diagnostic | Ajouté avec un marqueur `kind: repair`, visible dans le journal et l'interface |
| Outils pendant la réparation | `repair_tools` : `none` par défaut pour un échec de format (`tool_choice: none`), `allowed` pour un échec de fond (juge) |
| Sortie structurée | Schéma JSON natif si le modèle l'accepte (B9) |
| Limites | `max_attempts` du hook ; chaque tentative compte dans les itérations et le budget |
| Épuisement | Politique `on_failure` : `fail`, réponse renvoyée avec un indicateur `unverified`, ou message de repli |
| Streaming | Rien à faire avec `after_guards` (réponse en tampon) ; avec `live`, `model.delta.reset` puis la nouvelle réponse |

**Historique :** les tentatives refusées restent dans le journal (audit), mais sont exclues de l'historique de session relu aux runs suivants. Seule la réponse acceptée y figure.

**Séquence dans le journal :**

```
model.responded → guard.checked (échec, motif, tentative 1)
→ policy.decided (Retry) → run.transitioned (→ READY_FOR_MODEL, cause: repair)
→ model.responded → guard.checked (succès)
→ run.completed
```

**Appel dédié à un autre modèle :** pas prévu en V2. La stratégie de réparation reste un objet interchangeable, pour pouvoir l'ajouter plus tard sans toucher au moteur.

### 21. Juge

- **Juge qui utilise le même modèle que l'évalué :** avertissement, erreur en profil prod.
- **Déclenchement : décidé par le code, jamais par le LLM** (#21).

  ```yaml
  judge:
    model: SONNET
    when:
      sample: 0.2                               # proportion des sorties jugées
      condition: myapp.judges:montant_eleve     # prédicat Python (optionnel)
      tenants: [dupont-plomberie]               # filtres déclaratifs (optionnels)
      profiles: [prod]
  ```

  - Le juge s'exécute si toutes les clauses présentes sont vraies ; `when` absent équivaut à `always`.
  - Échantillonnage déterministe : `hash(run_id + nom du juge) < rate`. Deux juges n'échantillonnent pas les mêmes runs, et le rejeu reproduit la même décision.
  - Prédicat : `(JudgeInput) -> bool`, qui reçoit la sortie, la demande, l'agent, le rôle et le contexte de l'appelant.
  - Juge bloquant avec `sample < 1` : avertissement au démarrage, erreur en prod.
  - Forçage par l'appelant : `run(..., judges="force")` juge tout (audit, évals) ; `skip` n'est autorisé qu'en profil dev et dans les évals.
  - Un juge non déclenché écrit `guard.checked` avec `outcome: skipped` et le motif (`sampled_out`, `condition_false`, `filtered`).
  - Coût attribué au rôle `judge:<nom>`.

### 22. Session = journal d'événements typés (option B)

Le journal fait foi. On y écrit au fil de l'eau des événements immuables, et tout le reste en est déduit par des projections :

```
                    ┌─▶ Historique LLM   (messages envoyés au modèle)
Journal (append) ───┼─▶ RunState         (reprise, pause, checkpoint)
                    ├─▶ Traces           (arbre de spans pour l'interface)
                    ├─▶ Usage            (tokens, coûts)
                    └─▶ Rejeu            (réponses modèle et outils enregistrées)
```

**Enveloppe commune** (champs indexés partout) :

```
Event
  event_id (UUIDv7, triable)   seq   ts   schema_version
  tenant_id  session_id  run_id  root_run_id  span_id  parent_span_id
  type       ex. "tool.completed"
  category   run | message | model | tool | guard | approval | artifact | session
  status     ok | warning | error
  agent  role                (si applicable)
  facets     champs de recherche remontés par le payload
  payload    typé selon `type`
```

Convention de nommage : `<catégorie>.<action au passé>`.

**Payloads typés** : un modèle Pydantic par type, dans une union discriminée par `type`.

| Type | Champs principaux |
|---|---|
| `run.started` | agent, kind (`normal`, `compaction`), triggered_by, entrée (réf.), contexte |
| `message.user` | blocs de contenu (pièces jointes par référence) |
| `model.responded` | model_id, fournisseur, blocs, usage, coût, stop_reason, latence, tentatives, request_hash, call_id (rôle délégué) |
| `tool.called` | tool_name, tool_kind, call_id, arguments, refs |
| `tool.completed` | tool_name, call_id, is_error, sortie (blocs ou réf.), latence, taille |
| `guard.checked` | guard, cible, outcome (`passed`, `failed`, `skipped`), motif, tentative, normalized (voir points 20 et 21) |
| `judge.evaluated` | modèle juge, scores par critère, bloquant, réussi |
| `approval.requested` / `.granted` / `.rejected` / `.expired` | tool_name, call_id, arguments, auteur, motif, scope, expire_at (voir point 17) |
| `artifact.stored` | uri, type MIME, taille |
| `session.compacted` | résumé, up_to_seq, tokens avant/après |
| `run.paused` / `.resumed` | motif |
| `run.claimed` | worker_id, lease_until (voir point 27) |
| `run.transitioned` | from, to, step_no, cause (voir point 3) |
| `step.started` / `.completed` | step_no, état, effet, durée, statut (voir point 3) |
| `policy.decided` | hook, point, décision, motif (voir point 2) |
| `model.retried` | tentative, type d'erreur, délai (voir point 10) |
| `model.fell_back` | ancien modèle, nouveau modèle, motif (voir point 10) |
| `idempotency.recorded` | clé, call_id, résultat (voir point 49) |
| `run.completed` / `.failed` / `.cancelled` | itérations, usage total, coût total, erreur |

Les tokens streamés (`model.delta`) sont des événements éphémères : ils passent sur le bus mais ne sont pas persistés. Seule la réponse complète est écrite.

**Facettes de recherche** : chaque classe d'événement déclare ses champs filtrables (`tool_name`, `model_id`, `is_error`, `cost`, `latency_ms`, `tokens`…). Ils sont copiés dans `facets`, et les adaptateurs de stockage les indexent sans connaître les payloads : ajouter un type d'événement ne demande aucun changement de stockage.

**Requêtes** (port `EventStore`) :

```
EventQuery(tenant, session, run, types, categories, status,
           agent, role, tool_name, model_id, since, until, limit, cursor)
```

Exemples : les échecs d'un outil sur une période, les runs où le juge a bloqué, le coût par agent et par modèle, toutes les validations d'un client.

**Conséquences :**

- Checkpoint (25) : chaque événement durable est un checkpoint. Reprendre un run revient à relire son journal.
- Compaction : on ne réécrit rien. On ajoute `session.compacted` avec le résumé et la position du dernier événement résumé, et l'historique LLM repart de ce résumé.
- Traces (K1) et rejeu (K6) découlent du journal.
- Historique structuré (F2) conservé, rien n'est aplati.
- Audit : on sait qui a validé quoi et quand.

**Difficultés et parades :**

| Difficulté | Parade |
|---|---|
| Relire des milliers d'événements à chaque run | Snapshot : l'historique LLM est matérialisé à la fin de chaque run, et le journal n'est relu qu'au-delà |
| Deux runs qui écrivent en même temps dans une session | `append(events, expected_seq)` : écriture refusée si la séquence a bougé (verrou optimiste) |
| Volume (images, gros résultats) | Les payloads ne contiennent que des références vers le stockage d'artefacts |
| Évolution du schéma | `schema_version` sur chaque événement, convertisseurs à la lecture |
| RGPD : un journal est censé être immuable | Suppression physique par `session_id` ou `tenant_id`, artefacts compris |

**Stockage :**

| Backend | Usage | Indexation |
|---|---|---|
| JSONL | Mode librairie, dev | Un fichier par session, requêtes via DuckDB |
| SQLite / Postgres | Mode service | Colonnes pour l'enveloppe et les facettes, payload en JSONB, index composés dont `(session_id, seq)` |
| Firestore | Mode service | Sous-collection `events` par session, seulement quelques facettes indexées |

**Contrat pour l'interface (K2)** : un JSON Schema est généré depuis les modèles Pydantic, ce qui donne les types TypeScript de l'interface. Ajouter un champ optionnel ne change pas la version ; une modification incompatible crée une nouvelle `schema_version`, avec un convertisseur à la lecture.

### 23. Compaction : en tâche de fond après le run

Compacter ne réécrit rien : on ajoute `session.compacted` (résumé, `up_to_seq`), et l'historique LLM repart de ce résumé.

**Qui la traite :** `sessions` pilote, le moteur exécute. La compaction est un agent interne `_compaction` (pas d'outils, un appel modèle, contrôle de fidélité en hook `on_output`) : elle bénéficie du retry, du modèle de secours, du suivi des coûts, des traces et de la réparation.

```
runtime (fin du run) ──enqueue──▶ TaskQueue ──▶ worker
                                                  │
                                       sessions.CompactionJob
                                         1. lit la projection d'historique
                                         2. runtime.run("_compaction", segment)
                                            └─ engine + models + guards (fidélité)
                                         3. append session.compacted
```

| Composant | Rôle |
|---|---|
| **runtime** | Après `run.completed`, appelle `sessions.maybe_schedule(session_id)` (appel direct, qui ne fait que mettre une tâche en file) |
| **sessions** | Décide si on compacte (seuils), prépare le segment, lance l'agent interne, écrit `session.compacted` |
| **engine / models / guards** | Exécutent `_compaction` comme n'importe quel agent |
| **config** | Génère `_compaction` à partir de `sessions.compaction` (modèle, `keep_last`, seuils, prompt surchargeable) |

| Mode | Exécutant |
|---|---|
| Librairie | Tâche asyncio dans le process de l'instance `Loom` (`TaskQueue` par défaut) ; `aclose()` attend les compactions en cours, dans la limite d'un timeout |
| Service | Tout worker qui consomme la file (`loom worker`, adaptateur RabbitMQ) |

**Déclenchement :** à `run.completed`, si l'historique dépasse `compact_over_tokens` (seuil souple).

**Exécution :**

- Un seul job par session à la fois (dédoublonnage par `session_id` + `up_to_seq`).
- Run système : `run.started(kind: compaction, triggered_by: <run_id>)`, avec son propre `run_id` et le rôle `compaction`.
- Modèle dédié et peu cher : `compact_model`.
- Résumé glissant : les N derniers tours restent intacts (`keep_last`), le reste est résumé avec le résumé précédent.
- Pas de conflit d'écriture : l'événement ne couvre que des événements anciens, il est écrit sans contrôle `expected_seq`.

**Contenu résumé :** échanges utilisateur et assistant, appels d'outils résumés. Gros résultats et images remplacés par leur référence. Raisonnement et tentatives refusées (point 20) exclus.

**Fidélité :** consigne de conserver tels quels identifiants, montants, dates, noms et numéros de devis ; contrôle déterministe que les nombres, e-mails et références du segment d'origine se retrouvent dans le résumé (une nouvelle tentative, puis avertissement).

**Filet de sécurité au démarrage d'un run :** si l'historique dépasse `compact_hard_tokens` (fenêtre de contexte moins une marge) sans résumé disponible, le `runtime` appelle `sessions.ensure_fits()`, qui lance `_compaction` en synchrone. En cas d'échec, les tours les plus anciens sont retirés (déterministe), avec un avertissement dans le journal.

**Mesure :** `session.compacted` contient les tokens avant et après, et le coût.

**À ne pas confondre :** le snapshot (22) est une vue matérialisée sans LLM, pour lire vite ; la compaction est un résumé par LLM, pour réduire le contexte.

### 24. Lien session ↔ run

Une session est un journal qui contient N runs. Un `RunState` est la projection des événements de son `run_id`.

### 25. Granularité des sauvegardes

Réglé par les points 3 et 22 : chaque événement durable est une sauvegarde, et `step` écrit au fil de l'eau. La granularité est donc « après chaque appel modèle et chaque résultat d'outil ».

### 26. Reprise avec des outils en cours au moment du plantage

Réglé par le point 18 : réexécution si l'outil est sans effet de bord ou idempotent ; sinon erreur « état inconnu après interruption » renvoyée au modèle, ou pause pour vérification (configurable).

### 27. Arrière-plan

**Port `TaskQueue` :** `submit(job, key, delay?)`, `status(job_id)`, `cancel(job_id)`.

**Types de jobs :** `run` (H5), `resume` (après approbation), `compaction` (23), `expire_approval` (tâche différée qui émet `approval.expired`), déclencheurs planifiés (H6).

**La durabilité vient du journal, pas de la file :**

- Au démarrage, `runtime.recover()` cherche les runs restés dans un état actionnable et les remet en file. Une file non durable (asyncio) suffit donc en mode librairie.
- Une livraison en double est sans risque : `drive(run_id)` reconstruit l'état depuis le journal.

**Un seul pilote par run :** le worker prend une concession avant `drive` (événement `run.claimed`, avec `worker_id` et `lease_until`) et la renouvelle pendant le run. Si le worker meurt, la concession expire et un autre worker reprend.

| Mode | File | Remarque |
|---|---|---|
| Librairie | asyncio, dans le process | Récupération au démarrage via `recover()` |
| Service | RabbitMQ (adaptateur) | Acquittement en fin de job ; une relivraison est sans risque |

**Déclencheurs (H6) :** webhook (un endpoint REST crée le run), planification (un adaptateur cron met en file), file de messages (un consommateur crée les runs).

### 28. Pause humaine en mode librairie

- **Contrôle au démarrage :** un agent qui peut se mettre en pause (outil en `approval: always | policy`, ou politique qui peut renvoyer `Pause`) exige un `EventStore` durable (JSONL au minimum). Sinon, erreur de config ; en profil dev, avertissement seulement.
- **Deux façons d'approuver en Python :**

| Mode | Usage | Fonctionnement |
|---|---|---|
| Asynchrone (défaut) | Applications, serveurs | `run()` renvoie `RunResult(status=paused, run_id, pending_approvals)` ; plus tard, `await loom.approve(run_id, décision)` écrit la décision et met un `resume` en file |
| Approbateur en ligne | Scripts, CLI, tests | `run(..., approver=callback)` : le callback est appelé dans le process, sans passer par l'état `PAUSED` durable |

- **Expiration :** un job `expire_approval` est planifié dès la demande d'approbation.

### 29. Spans : modèle maison, export OpenTelemetry

Les spans découlent du journal (`span_id`, `parent_span_id`) : modèle maison pensé pour l'interface. OpenTelemetry n'est qu'un export, via un abonné `EventSink` qui traduit les événements en spans OTel.

### 30. Capture des contenus et masquage

Le journal doit contenir les contenus (historique, reprise, rejeu). On distingue trois niveaux :

| Niveau | Contenus | Protection |
|---|---|---|
| Journal | Complets, toujours | Isolation par client, chiffrement au repos, rétention configurable, suppression RGPD |
| Exports (OTel, logs) | Métadonnées par défaut, contenus en opt-in | Masquage configurable (e-mails, téléphones, IBAN…) |
| API de traces / interface | Selon le scope de la clé (`read` ou `read_content`) | Masquage à l'affichage |

**Option RGPD :** chiffrement des payloads avec une clé par client. Supprimer la clé rend tout son journal illisible, sauvegardes comprises (*crypto-shredding*), en complément de la suppression physique (point 22).

### 31. Enregistrement pour le rejeu

Réglé par le journal : réponses des modèles et résultats d'outils sont toujours enregistrés, le rejeu est toujours possible.

- **`request_hash`** dans `model.responded` : empreinte de la requête envoyée au modèle. Au rejeu, une empreinte différente signale une divergence (prompt, outils ou historique modifiés).
- **Échanges HTTP bruts** (équivalent de `log_response` en V1) : en opt-in, pour le débogage.

| Mode de rejeu | Modèles | Outils |
|---|---|---|
| Identique | Réponses lues dans le journal | Résultats lus dans le journal |
| Variante (autre modèle, prompt ou config) | Appels réels | Lus dans le journal quand l'appel correspond ; jamais réexécutés s'ils ont des effets de bord (doublure ou erreur) |

### 32. Stockage des traces pour l'interface

Réglé par le point 22 : les traces sont le journal lui-même, interrogé via `EventQuery` (JSONL et DuckDB en mode librairie, SQLite ou Postgres en mode service).

**Projection `run_summaries`** : run_id, agent, statut, durées, coût, tokens, nombre d'étapes, erreurs. Mise à jour à chaque `run.transitioned`, elle permet à l'interface de lister et filtrer les runs sans parcourir le journal.

### 33. Client en mode librairie

Un client est toujours présent, mais implicite : `tenant_id = "default"`. Le code est le même dans tous les modes, et chaque événement du journal porte un `tenant_id`.

- API Python : `loom.run(..., tenant="dupont-plomberie")` est optionnel.
- Config : la section `tenants` est optionnelle ; sans elle, seul `default` existe.

### 34. Isolation des clients

**Logique par défaut, avec une défense en profondeur :**

| Élément | Isolation |
|---|---|
| Journal | `tenant_id` sur chaque événement. `EventStore` exige le client dans chaque requête ; une requête multi-clients demande le scope `admin`. En Postgres, sécurité au niveau des lignes (RLS) en plus |
| Artefacts | Préfixe par client (`tenant/<id>/...`) |
| Serveurs MCP | `scope: tenant` (point 19) |
| Secrets | Par client via `SecretProvider` |
| Budgets, quotas | Par client (J4, L3) |
| Chiffrement | Clé par client en option (point 30) |

**Isolation physique possible par adaptateur :** un `TenantRouter` choisit le stockage selon le client (fichier SQLite, schéma Postgres, collection Firestore ou bucket dédiés).

**Surcharges par client (liste fermée) :** agents et outils autorisés, correspondance des modèles, budgets et quotas, politiques d'approbation, identifiants des serveurs MCP et secrets. Les prompts ne sont pas surchargeables (logique métier, hors périmètre) ; seules des variables injectées dans les prompts le sont.

**Définition des clients :** statique dans la config en V2 ; plus tard, un port `TenantStore` et une API d'administration.

### 35. Format de config : YAML, avec un JSON Schema pour l'éditeur

Un seul format de config : **YAML**.

- **Lecture sûre :** `safe_load`, puis validation stricte par les modèles Pydantic (le typage implicite de YAML, comme `no` lu comme `false`, est rattrapé).
- **Prompts dans des fichiers à part :** `system_file: prompts/relance.md`. Prompts versionnés en Markdown, config courte, pas de balise YAML exotique.
- **Découpage :** un fichier racine, plus `agents_dir` qui charge `agents/*.yaml`.
- **Secrets :** uniquement des noms de variables (`api_key_env`), jamais de valeurs.
- **Config en Python (M2) :** mêmes modèles Pydantic ; le fichier n'est qu'une façon de les remplir.

| Élément | Rôle | Qui l'écrit |
|---|---|---|
| `loom.yaml`, `agents/*.yaml` | La configuration | L'utilisateur |
| `loom.schema.json` | Description de la structure attendue (champs, types, valeurs permises), lue par l'éditeur pour la complétion et les erreurs (commentaire `yaml-language-server: $schema=...`) | Généré depuis les modèles Pydantic |

loom ne lit pas le JSON Schema : il valide directement avec Pydantic.

### 36. Pas de migration depuis V1

Aucune compatibilité avec le `settings.json` de V1 et aucun outil de migration. loom_V2 repart de zéro.

### 37. Framework HTTP : FastAPI

FastAPI, dans l'extra `loom-ia[http]` : Pydantic natif, OpenAPI généré (client de l'interface). SSE via `StreamingResponse` ou `sse-starlette`. Le serveur HTTP MCP (basé sur Starlette) se monte dans la même application.

### 38. Streaming HTTP : SSE

- **SSE pour les runs :** flux à sens unique, HTTP simple, compatible avec les proxys et Cloud Run ; reconnexion par `Last-Event-ID` (point 5).
- **Actions en REST :** `approve` et `cancel` sont de simples `POST`.
- **Pas de WebSocket en V2 :** la voix n'en a pas besoin côté loom. Dans `artisan-voice`, Pipecat gère le transport audio (Twilio) et appelle loom par l'API Python (`loom.stream()`, même process) ou par SSE.

### 39. Authentification : clés API

**Clés API :**

- préfixe identifiable (`lk_...`), stockées hachées, jamais dans les logs ;
- rattachées à un client, avec des scopes (`run`, `read`, `read_content`, `approve`, `admin`) et la liste des agents autorisés ;
- rotation, révocation et limitation de débit par clé.

**Sécurité du point d'accès MCP (N5) :**

- `approve` n'est jamais exposé comme outil MCP : sinon le LLM client pourrait valider lui-même une action sensible. La validation passe par l'elicitation (un humain répond) ou par l'API REST.
- En HTTP :
  - la clé passe dans l'en-tête `Authorization` ;
  - le serveur valide l'en-tête `Origin` (imposé par la spec MCP) ;
  - en local, il n'écoute que sur `localhost`.
- En stdio : pas d'authentification, mais un profil local restreint.
- Compatibilité : la spec MCP recommande OAuth pour les serveurs distants. Il faudra vérifier que les clients visés acceptent une clé dans l'en-tête.
- Injection : les sorties des agents sont renvoyées à un LLM tiers, ce qui expose à l'injection de prompt.

### 40. Points d'accès : framework Python, API HTTP REST et MCP

Les trois passent par le même registre d'agents et le même `runtime`.

```
               ┌─ API Python
AgentRegistry ─┼─ HTTP REST (SSE)
  + runtime    └─ MCP (stdio ou HTTP)
```

- **API Python :** `Loom`, `AgentSpec`, `register()`, `run()`, `stream()`.
- **HTTP REST :** application ASGI, autonome ou montée dans un projet FastAPI (`app.mount("/loom", loom.asgi_app(rest=True, mcp=True))`).

| Méthode | Route | Rôle |
|---|---|---|
| `GET` | `/v1/agents` | Lister les agents |
| `POST` | `/v1/agents/{name}/runs` | Lancer un run (synchrone ou arrière-plan) |
| `GET` | `/v1/runs/{id}` | Statut et résultat |
| `GET` | `/v1/runs/{id}/events` | Streaming SSE |
| `POST` | `/v1/runs/{id}/approve` · `/cancel` | Validation humaine, annulation |
| `GET` | `/v1/sessions/{id}` · `/v1/traces/...` | Sessions, traces |

- **MCP :**
  - chaque agent devient un outil MCP, et deux outils de contrôle s'y ajoutent (`run_status`, `cancel`) ;
  - les traces et les sessions sont exposées en ressources en lecture seule (`loom://runs/{id}`) ;
  - transports stdio (local) et HTTP, monté dans la même application ASGI que l'API REST.
- **Limites de MCP :** seulement des notifications de progression, pas de flux d'événements complet. La validation humaine passe par l'elicitation ; sinon, le run se met en pause et renvoie un `run_id`, puis la validation se fait via l'API REST.

### 41. Un package avec des extras

**Arborescence :**

```
src/loom_ia/
  core/          model/  events/  ports/
  engine/        loop  executor  roles  hooks
  guards/  usage/  sessions/  artifacts/  agents/
  runtime/  telemetry/  replay/  tenancy/  config/
  adapters/
    models/      anthropic  openai_chat  openai_responses
    stores/      memory  jsonl  sqlite  postgres  firestore
    artifacts/   local  gcs
    bus/         memory  postgres  redis
    queue/       asyncio  rabbitmq
    mcp/         client
    telemetry/   otel
  access/        api (façade Python)  http/  mcp_server/  cli/
  testing/       faux modèles, faux outils
```

**Extras :**

| Extra | Dépendances | Modules concernés |
|---|---|---|
| `anthropic` | `anthropic` | `adapters/models/anthropic` |
| `openai` | `openai` | `openai_chat`, `openai_responses` |
| `mcp` | `mcp` (borne `<2`) | `adapters/mcp`, `access/mcp_server` |
| `http` | `fastapi`, `uvicorn`, `sse-starlette` | `access/http` |
| `sqlite` | `aiosqlite` | `stores/sqlite` |
| `postgres` | `asyncpg` | `stores/postgres`, `bus/postgres` |
| `firestore` | `google-cloud-firestore` | `stores/firestore` |
| `gcs` | `google-cloud-storage` | `artifacts/gcs` |
| `rabbitmq` | `aio-pika` | `queue/rabbitmq` |
| `redis` | `redis` | `bus/redis` |
| `otel` | `opentelemetry-sdk` et un exporteur | `adapters/telemetry/otel` |
| `all` | Tous les extras ci-dessus | — |

- **Outils de dev** (pytest, respx, ruff, pyright, import-linter) : dans `[dependency-groups]` (uv), pas dans les extras.
- **CLI :** dans le noyau, avec `argparse` (stdlib). `loom serve` exige l'extra `http`.

**Chargement des adaptateurs :**

- Import paresseux : `sdk: anthropic` charge `adapters.models.anthropic` seulement à ce moment-là.
- Erreur au démarrage, pas au premier appel : si l'extra manque, la validation de la config échoue avec « installez `loom-ia[anthropic]` ».
- Adaptateurs externes par entry points (`[project.entry-points."loom_ia.adapters"]`), par exemple un `loom-ia-firecracker` pour la sandbox (D9).

**`import-linter` en CI :**

```
couches   : access > runtime > engine > core
interdits : core, engine  ↛  adapters, anthropic, openai, fastapi, mcp
indépendance : adapters.models ↮ adapters.stores ↮ adapters.queue …
```

Une violation fait échouer la CI. Ces règles permettront, si besoin, de découper plus tard en plusieurs packages sans réécriture.

**Tests :**

- CI noyau seul : `uv sync` sans extra ; le noyau doit s'importer et fonctionner seul.
- CI complète : `uv sync --all-extras`.
- Les tests d'un adaptateur sont ignorés si son extra n'est pas installé (`pytest.importorskip`).

### 42. Nom du package : `loom-ia`, nouveau dépôt

Le package s'appelle `loom-ia` et vit dans un nouveau dépôt. Les extras s'écrivent `loom-ia[...]`.

### 43. Python 3.14, géré avec uv

- `uuid.uuid7()` en stdlib : identifiants triables dans le temps pour `event_id`, sans dépendance.
- Annotations évaluées à la demande (PEP 649).
- Support jusqu'en octobre 2030.
- uv installe la version de Python, en local comme dans l'image Docker (Cloud Run).
- Risque à vérifier au choix des dépendances : un extra dont une dépendance ne prendrait pas encore en charge 3.14.

### 44. Dépendances du noyau

- **Noyau :** `pydantic`, `pyyaml`, `jsonschema`. Logs et `asyncio` viennent de la stdlib.
- **Tout le reste passe par les extras** (point 41).
- **Règles :**
  - toute nouvelle dépendance du noyau est justifiée par écrit ;
  - borne haute sur les versions majeures des dépendances fragiles (leçon de `mcp<2`) ;
  - `uv.lock` versionné.

### 45. Logs : `logging` de la stdlib

- **Dans la librairie :** `logging.getLogger("loom_ia.…")` et `NullHandler`. loom-ia ne configure jamais les handlers de l'application hôte.
- **Contexte :** `run_id`, `span_id` et `tenant_id` passent dans `extra`.
- **Configuration :** utilitaire optionnel `loom_ia.telemetry.configure_logging()` (JSON ou console), utilisé par la CLI et le mode service.
- **Priorité :** les logs restent secondaires ; l'observabilité passe d'abord par le journal (K7).

### 46. Licence : Apache 2.0

Les en-têtes SPDX deviennent `Apache-2.0`. Pour reprendre le code de V1 (AGPL-3.0) sous cette licence, il faut en être le seul auteur, ou avoir l'accord des contributeurs.

### 48. Enregistrement des agents

- **V2 :** enregistrement statique uniquement, par la config (`agents/*.yaml`) ou le code (`loom.register()`).
- **Dev :** `loom serve --reload` recharge les agents quand un fichier change.
- **Plus tard :** enregistrement dynamique d'agents déclaratifs (rôles LLM et outils MCP) via une API d'administration (scope `admin`).

### 49. Implémentations de `IdempotencyStore`

**Port :**

```
IdempotencyStore
  get(key)                 -> Record | None     # status, result, expires_at
  reserve(key, ttl)        -> bool              # False si la clé est déjà prise
  complete(key, result)
  release(key)                                  # échec avant tout effet de bord
```

**Décorateur `@idempotent` :** `get`, puis `reserve`, puis l'effet de bord, puis `complete`.

| État trouvé | Traitement |
|---|---|
| `completed` | Le résultat stocké est renvoyé, sans exécution |
| `in_progress` récent | Erreur « exécution en cours » |
| `in_progress` expiré (plantage entre l'effet et `complete`) | Règle du #18 : erreur « état inconnu » ou pause ; exception : `retry_unknown=True` si l'API externe dédoublonne elle-même avec `Idempotency-Key` |

**Deux portées de clé :**

- **Clé technique** `hash(run_id, call_id)` : protège la reprise d'un même appel.
- **Clé métier**, fournie par l'outil, par exemple `@idempotent(key=lambda a: f"relance:{a['devis']}")` : empêche d'envoyer deux fois la même relance, même depuis deux runs différents.

**Implémentations :**

| Implémentation | Portée | Fonctionnement |
|---|---|---|
| `journal` (défaut) | Clés techniques | Événement `idempotency.recorded` dans le journal du run ; pas de stockage supplémentaire, durable dès que le journal l'est |
| `sqlite` / `postgres` | Techniques et métier | Table `(key, tenant_id, status, result, expires_at)`, réservation par `INSERT … ON CONFLICT DO NOTHING` |
| `firestore` | Techniques et métier | Un document par clé, créé dans une transaction s'il n'existe pas |
| `redis` | Métier, courte durée | `SET NX PX` |
| `memory` | Tests | Non durable |

**Règles :**

- Une clé métier exige un backend partagé (ni `journal` ni `memory`) : vérifié au démarrage.
- Chaque clé a une durée de vie (TTL) par outil et est préfixée par le client.
- Les clés sont supprimées avec le client ou la session (RGPD).

### 50. Schéma de la config

Le schéma complet est dans `conception.md` (§17).

**Décisions :**

1. **Références vers du code Python** (outils, hooks, conditions) : un nom enregistré en code (`@loom.tool`) ou un chemin d'import `module:attr` ; le nom enregistré est essayé d'abord. `imports:` liste les modules chargés au démarrage.
2. **Variables dans les prompts et les templates :** `{{ var }}`, avec un moteur interne minimal (sans Jinja) ; une variable inconnue est une erreur.
3. **Profils :** fusion profonde des objets, remplacement des listes ; `params` et `llm` sont remplacés en bloc.
4. **Annotations MCP** (`readOnlyHint`, `destructiveHint`, `idempotentHint`) : valeurs par défaut de `side_effects` et `idempotent`, surchargeables par la config.
5. **Clés API en V2 :** stockées hachées dans la config ; `loom keys create` affiche la clé une seule fois et donne le hash.
6. **Version du schéma :** `version: 1` obligatoire.

**Fichier racine `loom.yaml` :**

```yaml
version: 1
profile: dev                          # dev | prod
imports: [myapp.tools, myapp.policies]
agents_dir: agents/
prompts_dir: prompts/                 # base des *_file

models: [...]                         # ModelSpec
mcp_servers: [...]                    # McpServerSpec
storage: {...}
sessions: {...}
execution: {...}
budgets: {...}                        # défauts globaux
telemetry: {...}
tenants: [...]
security: {...}
server: {...}
profiles: {prod: {...}}               # surcharges partielles
```

**Contrôles au démarrage ajoutés :**

- `version` inconnue ;
- `system` et `system_file` fournis ensemble ;
- référence Python introuvable ;
- variable `{{ }}` non définie ;
- clé métier d'idempotence avec un backend `journal` ou `memory` ;
- juge bloquant échantillonné ;
- `scope: tenant` sans secrets pour un client ;
- serveur MCP référencé mais non déclaré, préfixes MCP en double.

## Ouverts

La recommandation suit la flèche.

**Projet**

47. Ordre de réalisation : jalons à revoir ensemble.
