# loom-ia V2 — Conception fonctionnelle et technique

> Synthèse de la conception validée. Les renvois pointent vers `fonctions.md` : une lettre et un chiffre (A1, K6…) désignent une fonction, `#n` un point tranché.
>
> **Statut :** conception validée, sauf l'ordre de réalisation (#47). Aucun code n'est encore écrit. Le schéma de config (§17) est validé ; les autres exemples de code sont indicatifs.

## Sommaire

**Partie I — Vue fonctionnelle**

1. Présentation
2. Concepts
3. Parcours fonctionnels
4. Capacités
5. Points d'accès
6. Multi-clients

**Partie II — Vue technique**

7. Architecture
8. Modèle de domaine
9. Moteur d'exécution
10. Modèles de langage
11. Journal, sessions et persistance
12. Exécution durable
13. Bus d'événements et streaming
14. Observabilité et rejeu
15. Coûts et budgets
16. Sécurité
17. Configuration
18. Accès : API Python, HTTP, MCP, CLI
19. Projet
20. Questions ouvertes

---

# Partie I — Vue fonctionnelle

## 1. Présentation

### 1.1 Objet

loom-ia est un **moteur d'agents IA** en Python asynchrone. Il sert de socle aux produits métier d'App-Novative pour les artisans et les PME : relance de devis, agent vocal, états des lieux, reporting.

Un agent reçoit une demande. Un LLM orchestrateur appelle des outils (code Python, serveurs MCP, LLM spécialisés, sous-agents) jusqu'à produire une réponse. loom-ia rend cette exécution **fiable, économe en tokens, reprenable après un plantage et entièrement observable**.

loom-ia s'utilise de trois façons, qui partagent le même moteur : comme **librairie Python**, comme **API HTTP REST**, et comme **serveur MCP** (#40).

### 1.2 Pourquoi une V2

| Constat sur V1 | Réponse de V2 |
|---|---|
| La classe `Agent` fait tout (config, logs, providers, outils, boucle, persistance, budget, métriques) | Des composants séparés, organisés en couches (§7) |
| La boucle manipule des messages au format de chaque fournisseur ; l'historique est aplati en texte au stockage | Un modèle de message neutre (§8.1) ; l'historique garde sa structure |
| Des dépendances cachées par ContextVars | Des événements explicites (§8.3) |
| Le contrat d'outil fuit : le contexte d'outil est l'`Agent` lui-même, `vision` et `terminal` sont lus par `getattr` | Des capacités déclarées par l'outil (§9.5) |
| Tout se construit depuis `settings.json`, le rôle `main` est codé en dur | Construction en Python ou en YAML, registre d'agents nommés (§17, §18) |
| Budget, timeout, métriques et troncature sont codés dans la boucle | Des politiques branchées sur des points d'accroche (§9.4) |
| Pas d'état de run explicite, pas de streaming, pas de sous-agents | Machine à états, journal, streaming, sous-agents (§9, §11, §13) |
| Trois stockages (mémoire, usage, métriques) qui peuvent diverger | Un journal d'événements unique qui fait foi (§11) |

**Acquis de V1 conservés :** retry avec backoff et jitter, détection du format d'image par signature binaire, sortie unique en cas d'épuisement (itérations ou budget), outil terminal, budgets, rôles LLM délégués, contrats de sortie et juge, fusion des `params` par bloc entier.

**Pas de compatibilité ni de migration depuis V1** (#36) : loom-ia V2 repart de zéro, dans un nouveau dépôt (#42).

### 1.3 Périmètre

**Dans le périmètre :** orchestration d'agents, rôles LLM délégués, outils, sous-agents, fiabilité des sorties, sessions, exécution durable, validation humaine, streaming, coûts et budgets, observabilité et rejeu, multi-clients, trois points d'accès.

**Hors périmètre :**

- pas d'interface utilisateur (loom-ia fournit en revanche le contrat de données d'une interface de suivi, §14) ;
- pas de logique métier ;
- pas de framework multi-agents générique : la seule délégation est hiérarchique (orchestrateur → rôles et sous-agents).

### 1.4 Principes directeurs

1. **Le noyau ne fait aucune entrée-sortie.** Il ne connaît que des interfaces (ports) ; les implémentations (adaptateurs) sont en périphérie.
2. **Tout passe par des événements, et le journal fait foi.** Historique, état des runs, traces, coûts et rejeu en sont déduits.
3. **Un run est un état.** La boucle fait avancer un `RunState` pas à pas ; reprise, pause et rejeu en découlent.
4. **Le code décide, le LLM propose.** Les politiques, le déclenchement du juge et l'échantillonnage sont déterministes.
5. **Python ne ment pas, le LLM ne calcule pas.** Des contrôles déterministes encadrent les sorties des modèles (normalisation, fidélité des résumés).
6. **Économie de tokens.** Rôles délégués à des modèles moins chers, contexte déclaré, références au lieu de recopies, déport des gros résultats, cache de prompt.

## 2. Concepts

| Concept | Définition |
|---|---|
| **Instance** (`Loom`) | Objet qui héberge les agents, les modèles, les outils et les stockages |
| **Agent** (`AgentSpec`) | Définition nommée : orchestrateur, rôles, outils, politiques, réglages. Une instance en héberge plusieurs (A9) |
| **Orchestrateur** (`main`) | Le rôle qui pilote la boucle et choisit les outils (C6) |
| **Rôle** | Un LLM spécialisé, appelé par l'orchestrateur comme un outil, avec son propre modèle et son prompt (C1–C4) |
| **Sous-agent** | Un rôle qui a ses propres outils et sa propre boucle (C5) |
| **Outil** | Une capacité appelable : fonction Python, outil MCP, rôle, sous-agent, sandbox |
| **Run** | Une exécution d'un agent pour une demande |
| **RunState** | L'état d'un run à un instant donné, déduit du journal |
| **Étape** | Un passage de la boucle, qui exécute un seul effet (appel modèle, lot d'outils…) |
| **Session** | Une conversation : un journal qui contient plusieurs runs (#24) |
| **Journal** | La suite ordonnée et immuable des événements d'une session |
| **Événement** | Un fait typé écrit dans le journal (ou publié seulement, s'il est éphémère) |
| **Projection** | Une vue déduite du journal : historique LLM, état d'un run, traces, coûts |
| **Politique (hook)** | Du code branché sur un point d'accroche, qui peut influencer le run |
| **Guard** | Une politique qui contrôle une sortie : contrat, normalisation, réparation |
| **Juge** | Un LLM qui évalue une sortie selon des critères |
| **Artefact** | Un fichier (image, PDF, gros résultat) stocké hors du journal et désigné par une référence |
| **Client** (`tenant`) | Un espace isolé : config, secrets, sessions, budgets, traces |
| **Approbation** | Une validation humaine demandée avant un outil sensible |

## 3. Parcours fonctionnels

### 3.1 Un run

1. L'appelant envoie une demande (texte et pièces jointes) à un agent, avec éventuellement une session et un client.
2. loom-ia écrit `run.started`, puis le message utilisateur, dans le journal.
3. L'orchestrateur répond : soit une réponse finale, soit des appels d'outils.
4. Les outils s'exécutent en parallèle ; chaque résultat est écrit dès qu'il arrive.
5. L'orchestrateur reçoit les résultats et recommence, jusqu'à la réponse finale ou une limite (itérations, budget).
6. La réponse finale passe les contrôles de sortie, puis le run se termine (`run.completed`).
7. Pendant tout le run, l'appelant peut suivre le flux d'événements (texte généré au fil de l'eau, outils, validations).

### 3.2 Exemple : relance de devis

*Exemple illustratif, pour montrer les mécanismes ensemble.*

L'artisan écrit : « Relance M. Dupont pour le devis 2026-042. »

1. L'orchestrateur appelle l'outil Python `chercher_devis`. Le devis revient en JSON ; s'il est volumineux, il est déporté dans le stockage d'artefacts et l'orchestrateur n'en voit qu'un aperçu (#16).
2. L'orchestrateur appelle le rôle `rediger_relance` (modèle moins cher) en lui passant une **référence** au devis plutôt qu'une recopie (#12). Le rôle reçoit aussi la demande originale de l'artisan, mot pour mot.
3. La sortie du rôle passe son contrat (JSON `objet` + `corps`), puis le juge vérifie qu'aucun montant ni délai n'a été inventé. En cas de refus, le rôle corrige dans sa propre conversation (#20).
4. L'orchestrateur appelle `envoyer_email`, un outil à effet de bord qui exige une approbation. Le run passe en pause (#17).
5. L'artisan valide depuis son application. Le run reprend, éventuellement sur un autre worker (#27), et l'e-mail part avec une clé d'idempotence (#18).
6. L'orchestrateur confirme l'envoi. Le journal contient toute la chronologie : appels, coûts, validations, qui a approuvé et quand.

### 3.3 Conversation

- Avec un identifiant de session, chaque run relit l'historique de la session et y ajoute ses échanges.
- L'historique garde sa structure (appels d'outils compris), indépendamment du fournisseur du modèle.
- Quand il devient long, il est résumé en tâche de fond après le run, sans latence pour l'utilisateur (#23). Les identifiants, montants et dates sont conservés tels quels et vérifiés.
- Une session peut être listée, exportée ou supprimée (RGPD, F7).

### 3.4 Validation humaine

- Un outil déclare s'il a des effets de bord et s'il exige une approbation ; une politique peut en exiger une selon le client, l'agent ou les arguments (#17).
- Le run se met en pause, le process peut s'arrêter. L'approbateur valide (éventuellement en modifiant les arguments), refuse (le motif est renvoyé au modèle), ou laisse expirer la demande.
- En Python, l'approbation est asynchrone par défaut ; un « approbateur en ligne » (callback) sert aux scripts et aux tests (#28).
- Une approbation ne passe jamais par un outil MCP : un LLM client ne peut pas valider lui-même une action sensible (#39).

### 3.5 Arrière-plan et reprise

- Un run peut être soumis en arrière-plan, suivi, puis récupéré (H5). Il peut aussi être déclenché par un webhook, une planification ou une file de messages (H6).
- Après un plantage, le run reprend là où il s'était arrêté : l'état est relu depuis le journal (#3). Un outil déjà terminé n'est pas relancé ; un outil interrompu n'est relancé que s'il est sans risque (#18, #26).

### 3.6 Suivi et rejeu

- Chaque run se lit comme un arbre : run → étapes → appels modèle, outils, validations, sous-agents, avec durées, statuts et coûts (K1).
- L'interface de suivi peut lister et filtrer les runs, afficher la chronologie des états et le temps passé dans chacun (#3, #32).
- Un run peut être rejoué à l'identique (sans appel LLM) pour diagnostiquer, ou en variante (autre modèle, autre prompt) pour comparer (#31).

## 4. Capacités

| Domaine | Ce que loom-ia permet | Fonctions |
|---|---|---|
| Exécution | Lancer, streamer, annuler un run ; timeout global ; réponse structurée ; contexte de l'appelant ; plusieurs agents par instance | A1–A9 |
| Modèles | Anthropic, OpenAI et compatibles (Together, vLLM, Ollama, MiniMax) ; retry, secours, cache, streaming, capacités déclarées | B1–B9 |
| Rôles | LLM délégués, rôle terminal, rôle vision, sous-agents | C1–C6 |
| Outils | Python, MCP, sandbox ; validation, timeout, déport, approbation, idempotence | D1–D11 |
| Fiabilité | Contrats de sortie, réparation, juge, politiques d'échec | E1–E6 |
| Sessions | Historique structuré, compaction, backends, RGPD, mémoire long terme en outil | F1–F7 |
| Artefacts | Pièces jointes validées, stockage par référence, fichiers produits | G1–G3 |
| Exécution durable | Sauvegarde continue, reprise, validation humaine, arrière-plan, déclencheurs | H1–H6 |
| Streaming | Événements typés en direct, en Python et en SSE, compatible voix | I1–I3 |
| Coûts | Tokens, coûts, ventilation, plafonds, rapport | J1–J5 |
| Observabilité | Traces hiérarchiques, schéma versionné, masquage, exports, API de lecture, rejeu | K1–K7 |
| Multi-clients | Isolation, secrets, quotas | L1–L3 |
| Configuration | YAML validé, construction en Python, profils, contrôles de cohérence | M1–M5 |
| Accès | Python, HTTP REST, MCP, CLI, clés API | N1–N5 |
| Qualité | Évals, faux modèles et outils, non-régression par traces | O1–O3 |

## 5. Points d'accès

Les trois points d'accès passent par le même registre d'agents et le même moteur (#40).

| Point d'accès | Pour qui | Particularités |
|---|---|---|
| **API Python** | Un projet Python qui intègre loom-ia | Outils Python disponibles, streaming par itérateur, approbateur en ligne possible |
| **HTTP REST** | Une application, un front, un autre service | Runs synchrones ou en arrière-plan, streaming SSE, approbation et annulation, lecture des sessions et des traces |
| **MCP** | Claude Desktop, Claude Code, un autre agent | Chaque agent est un outil MCP ; runs courts ; progression plutôt que flux complet |

L'API HTTP peut tourner seule ou être montée dans un projet FastAPI existant, qui garde alors ses propres outils Python (§18).

## 6. Multi-clients

- Un client est toujours présent ; en mode librairie, c'est `default`, implicite (#33).
- Chaque client a son espace : sessions, traces, budgets, secrets, identifiants MCP, artefacts (#34).
- Un client peut surcharger une liste fermée de réglages : agents et outils autorisés, correspondance des modèles, budgets et quotas, politiques d'approbation, secrets. Les prompts ne sont pas surchargeables ; seules des variables injectées le sont.
- Les clients sont définis dans la config en V2 ; une API d'administration viendra plus tard.

---

# Partie II — Vue technique

## 7. Architecture

### 7.1 Couches

```
Accès          API Python · HTTP REST · MCP · CLI
Application    Agents · Runner · Sessions · Tenancy · Replay/Evals
Moteur         AgentLoop · ToolExecutor · Roles · Guards · Hooks
Noyau          Modèle de domaine · Ports · Événements
Adaptateurs    Providers · MCP · Stores · Sinks · Queue · Sandbox
Transverse     Config/Builder · Kit de test
```

- Une couche ne dépend que des couches du dessous.
- Les adaptateurs implémentent les ports du noyau ; le noyau et le moteur n'importent jamais un adaptateur ni un SDK.
- Ces règles sont vérifiées en CI par `import-linter` (#41).

### 7.2 Composants

| # | Composant | Responsabilité | Fonctions |
|---|---|---|---|
| 1 | `core.model` | Messages et blocs de contenu, pièces jointes, `RunState`, `Usage`, événements typés | B2, H1, K2, I1 |
| 2 | `core.ports` | Interfaces vers l'extérieur (§7.3) | — |
| 3 | `models` | Adaptateurs par SDK, erreurs, retry, secours, disjoncteur, cache, réglages, fenêtre de contexte, streaming, capacités | B1, B3–B9, I3 |
| 4 | `tools` | Contrat d'outil, `ToolOutput`, outils Python et MCP, `artifact_read`, sandbox, sélection des outils | D1–D3, D7–D9, F6 |
| 5 | `engine.executor` | Exécution des outils : parallélisme, `$ref`, validation, timeout, déport, approbation, idempotence, reprise | A3, D4–D6, D10, D11 |
| 6 | `engine.roles` | Rôles délégués, sous-agents, terminal, vision, `main` ; contexte déclaré, `input_template` | C1–C6 |
| 7 | `engine.loop` | `apply` / `step` / `drive`, arrêts, réponse forcée, réponse structurée | A2, A4, A7 |
| 8 | `engine.hooks` | Points d'accroche, décisions typées, composition, garde-fous | E5, J4, H4, D10 |
| 9 | `guards` | Contrats, normalisation, réparation, juge, politique d'échec, juge corrélé | E1–E6 |
| 10 | `usage` | Ledger (projection du journal), tarifs, ventilation, plafonds, rapport | J1–J5 |
| 11 | `sessions` | Projection de l'historique, snapshots, compaction, nettoyage, RGPD | F1–F5, F7 |
| 12 | `artifacts` | Validation des entrées, stockage par URI, fichiers produits | G1–G3 |
| 13 | `agents` | `AgentSpec`, `AgentRegistry` | A9 |
| 14 | `runtime` | Cycle de vie des runs : lancement, streaming, annulation, timeout, pause et reprise, approbations, arrière-plan, déclencheurs, planification de la compaction | A1, A5, A6, A8, H2–H6 |
| 15 | `telemetry` | Bus, traces, `run_summaries`, niveaux de capture, masquage, exports, `EventQuery`, logs | I1, K1, K3–K5, K7 |
| 16 | `replay` | Rejeu identique ou en variante, détection de divergence, évals, non-régression | K6, O1, O3 |
| 17 | `tenancy` | Contexte client, surcharges, secrets, quotas, `TenantRouter` | L1–L3 |
| 18 | `config` | Schéma Pydantic, chargement YAML, JSON Schema, profils, contrôles de cohérence, Builder | M1–M5 |
| 19 | `access` | API Python, HTTP REST (FastAPI, SSE), MCP (stdio, HTTP), authentification, CLI | N1–N5, I2 |
| 20 | `testing` | Faux modèles et faux outils | O2 |

### 7.3 Ports et adaptateurs

| Port | Rôle | Adaptateurs prévus |
|---|---|---|
| `ModelClient` | Appeler un modèle en streaming | `anthropic` ; `openai` (`chat`, `responses`) |
| `ToolSource` | Fournir des outils | Fonctions Python, client MCP, entry points externes (ex. sandbox Firecracker) |
| `EventStore` | Écrire et interroger le journal | Mémoire, JSONL, SQLite, Postgres, Firestore |
| `ArtifactStore` | Stocker les fichiers hors journal | Fichier local, GCS |
| `EventSink` | Exporter les événements | OpenTelemetry, logs |
| `BusBackend` | Notifier entre workers | Mémoire (défaut), Postgres `LISTEN/NOTIFY`, Redis, RabbitMQ |
| `IdempotencyStore` | Mémoriser les résultats par clé d'idempotence | Journal (défaut), mémoire, SQLite, Postgres, Firestore, Redis (§9.5) |
| `SecretProvider` | Lire les secrets | Variables d'environnement, gestionnaire de secrets |
| `TaskQueue` | Exécuter des jobs en arrière-plan | asyncio (défaut), RabbitMQ |

Les adaptateurs sont chargés à la demande. Si l'extra correspondant n'est pas installé, la validation de la config échoue au démarrage avec l'extra à installer (#41).

## 8. Modèle de domaine

Tous les types du domaine sont des modèles **Pydantic** (#6) : ils se sérialisent dans le journal et en HTTP, et produisent le JSON Schema.

### 8.1 Messages et blocs de contenu

Un message a un rôle et une liste de blocs typés :

| Bloc | Contenu |
|---|---|
| Texte | Texte brut |
| Image, fichier | **Référence** vers le stockage d'artefacts, jamais les octets |
| Appel d'outil | `call_id`, nom, arguments |
| Résultat d'outil | `ToolOutput` (§9.5) |
| `Reasoning` | Raisonnement du modèle, neutre vis-à-vis du fournisseur (#7) |

**`provider_meta`** (#8) : sur chaque bloc, un dictionnaire `provider → Meta`, où chaque `Meta` est un modèle Pydantic typé (par exemple `AnthropicMeta(signature, redacted_data)`, `OpenAIMeta(item_id, encrypted_content)`). L'adaptateur remplit sa propre entrée à la lecture d'une réponse et la relit pour construire la requête suivante ; il ignore celles des autres fournisseurs.

**Notions communes à tous les fournisseurs :** elles restent neutres dans le noyau, par exemple `cache_breakpoint`, que chaque adaptateur traduit.

### 8.2 RunState

```
RunState
  run_id, root_run_id, parent_run_id, parent_call_id, depth
  agent, status, step, messages, pending_calls, usage, budget, context
```

- `status` suit la machine à états (§9.1).
- `pending_calls` : appels d'outils en cours, avec leur éventuel run enfant.
- Les compteurs de tentatives (`Retry`) vivent dans l'état (#2).
- Le `RunState` ne contient que des données sérialisables ; modèles, outils, stores et hooks sont passés à part, dans le `RunContext` (#3).
- Il n'est jamais stocké comme tel : c'est la projection des événements de son `run_id` (#24).

### 8.3 Événements

**Enveloppe commune** (#22) :

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

- Nommage : `<catégorie>.<action au passé>`.
- `event_id` utilise `uuid.uuid7()` de la stdlib Python 3.14 (#43).

**Catalogue des événements durables :**

| Type | Champs principaux |
|---|---|
| `run.started` | agent, kind (`normal`, `compaction`), triggered_by, entrée (réf.), contexte |
| `message.user` | blocs de contenu |
| `model.responded` | model_id, fournisseur, blocs, usage, coût, stop_reason, latence, tentatives, request_hash, call_id (rôle délégué) |
| `model.retried` | tentative, type d'erreur, délai, call_id (rôle délégué) |
| `model.fell_back` | ancien modèle, nouveau modèle, motif |
| `idempotency.recorded` | clé, call_id, résultat |
| `tool.called` | tool_name, tool_kind, call_id, arguments, refs |
| `tool.completed` | tool_name, call_id, is_error, sortie (blocs ou réf.), latence, taille |
| `guard.checked` | guard, cible, outcome (`passed`, `failed`, `skipped`), motif, tentative, normalized |
| `judge.evaluated` | modèle juge, scores par critère, bloquant, réussi |
| `policy.decided` | hook, point, décision, motif |
| `approval.requested` / `.granted` / `.rejected` / `.expired` | tool_name, call_id, arguments, auteur, motif, scope, expire_at |
| `artifact.stored` | uri, type MIME, taille |
| `session.compacted` | résumé, up_to_seq, tokens avant/après |
| `step.started` / `.completed` | step_no, état, effet, durée, statut |
| `run.transitioned` | from, to, step_no, cause |
| `run.paused` / `.resumed` | motif |
| `run.claimed` | worker_id, lease_until |
| `run.completed` / `.failed` / `.cancelled` | itérations, usage total, coût total, erreur |

**Événements éphémères** (publiés sur le bus, jamais écrits) : `model.delta` (texte, raisonnement, arguments partiels) et `model.delta.reset` (effacer le texte partiel après une relance).

**Facettes :** chaque classe d'événement déclare ses champs filtrables (`tool_name`, `model_id`, `is_error`, `cost`, `latency_ms`, `tokens`…). Ils sont copiés dans `facets`, et les adaptateurs les indexent sans connaître les payloads : ajouter un type d'événement ne demande aucun changement de stockage.

**Évolution du schéma :** ajouter un champ optionnel ne change pas la version ; une modification incompatible crée une nouvelle `schema_version`, avec un convertisseur à la lecture. Le JSON Schema généré depuis Pydantic donne les types TypeScript de l'interface (K2).

## 9. Moteur d'exécution

### 9.1 Machine à états

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

| État | Effet exécuté par `step` |
|---|---|
| `READY_FOR_MODEL` | Appel du modèle orchestrateur |
| `AWAITING_TOOLS` | Exécution du lot d'appels d'outils en attente |
| `WAITING_CHILD` | Attente d'un sous-agent en pause |
| `PAUSED` | Aucun : le run attend une décision externe |
| `FINALIZING` | Réponse forcée sans outils (`tool_choice: none`) |
| `COMPLETED`, `FAILED`, `CANCELLED` | États terminaux |

### 9.2 `apply`, `step`, `drive`

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

**Règles :**

1. `RunState` ne contient que des données ; les dépendances passent par `deps` (`RunContext`).
2. `apply` ne décide rien : il enregistre des faits. Les décisions sont prises dans `step` et exprimées par des événements.
3. Les hooks s'exécutent dans `step`, autour de l'effet ; leurs décisions deviennent des événements.

**Conséquences :**

- Reprise : l'état est reconstruit depuis le journal ; seuls les appels sans `tool.completed` sont examinés (§9.5).
- Pause : `PAUSED` n'exécute rien ; un `approval.granted` relance `drive`.
- Annulation et timeout : vérifiés entre deux étapes ; pendant une étape, l'annulation asyncio de l'effet émet `run.cancelled`.
- Arrière-plan : n'importe quel worker peut reprendre `drive(run_id)`.
- Tests : `apply` se teste sans rien simuler, `step` avec un faux modèle.

### 9.3 Suivi des transitions

`drive` écrit `step.started`, `step.completed` et `run.transitioned` (#3) :

- **Plantage :** un `step.started` sans `step.completed` désigne l'effet interrompu ; le dernier `run.transitioned` donne le dernier état sûr.
- **Rejeu :** l'état recalculé est comparé aux transitions enregistrées ; tout écart est signalé comme une divergence.
- **Observabilité :** chronologie des états, temps passé par état, recherche par état (par exemple, les runs en `PAUSED` depuis plus de 24 h).
- **Logs :** chaque transition produit aussi une ligne de log corrélée par `run_id` et `span_id`.

### 9.4 Politiques (hooks)

**Politiques et abonnés** (#1) : une politique décide, dans le flux de `step` ; un abonné observe les événements sur le bus et ne peut pas influencer le run. Budget, contrats, juge, approbation et sélection d'outils sont des politiques ; ledger en direct, SSE, exports et logs sont des abonnés.

**Points d'accroche :**

| Point | Exemples |
|---|---|
| `before_model` | Budget, contexte injecté, filtrage des outils |
| `after_model` | Contrat sur la réponse finale |
| `before_tool` | Approbation, droits du client, validation des arguments |
| `after_tool` | Contrat ou juge d'un rôle, déport des gros résultats |
| `on_output` | Juge sur la réponse finale |

**Décisions** (#2) :

```
Decision = Continue
         | Replace(value)        # valeur du même type que l'entrée
         | Retry(feedback)       # rejouer avec un diagnostic (réparation)
         | Deny(reason)          # outil refusé → résultat d'erreur pour le modèle
         | Pause(reason)         # → PAUSED (approbation)
         | Stop(reason)          # → FINALIZING (budget)
         | Fail(error)           # → FAILED
```

| Point | Continue | Replace | Retry | Deny | Pause | Stop | Fail |
|---|---|---|---|---|---|---|---|
| `before_model` | ✓ | requête | | | | ✓ | ✓ |
| `after_model` | ✓ | | ✓ | | | ✓ | ✓ |
| `before_tool` | ✓ | arguments | | ✓ | ✓ | | ✓ |
| `after_tool` | ✓ | résultat | ✓ | | | | ✓ |
| `on_output` | ✓ | réponse | ✓ | | | | ✓ |

- Une décision non autorisée à un point est une erreur de config détectée au démarrage.
- Les hooks s'exécutent dans l'ordre déclaré ; `Replace` transmet la valeur au suivant ; toute autre décision que `Continue` ou `Replace` arrête la chaîne.
- Toute décision autre que `Continue` écrit `policy.decided`. En rejeu identique, ces décisions sont réutilisées ; en variante, les hooks sont réévalués.
- `Retry` est borné (`max_attempts`) ; chaque hook a un timeout ; en cas d'exception, le comportement est configurable par hook (bloquer par défaut pour le budget et l'approbation) ; les hooks sont asynchrones et déterministes pour un état donné.

### 9.5 Outils

**Déclaration d'un outil :** nom, description, schéma d'entrée, et capacités :

| Déclaration | Valeurs |
|---|---|
| `side_effects` | `none`, `reversible`, `irreversible` |
| `approval` | `never` (défaut), `always`, `policy` |
| `idempotent` | `true`, `false` |
| Capacités | Par exemple le besoin de pièces jointes (remplace le `getattr(vision)` de V1) |

**Résultat** (#15) :

```
ToolOutput
  blocks      Text | Json | ImageRef | FileRef   # ce que voit le modèle
  data        JSON structuré optionnel            # pour $ref, A7, l'interface
  is_error    bool
  artifacts   références produites (G3)
```

- Outils Python : une `str` donne `Text` ; `dict`, `list` ou modèle Pydantic donnent `Json` ; une `Image` est stockée et renvoyée en `ImageRef` ; un `ToolOutput` explicite donne le contrôle total.
- Une exception donne `is_error` ; une `ToolError(message)` porte un message exploitable par le modèle.
- MCP : `text`, `image`, `resource`, `structuredContent` et `isError` se traduisent directement.
- Capacité du modèle `tool_result_media` : si le modèle n'accepte pas d'image dans un résultat d'outil, l'adaptateur la place dans un message utilisateur juste après, ou la remplace par sa référence.

**Chaîne d'exécution d'un appel :**

1. Résolution des références `{"$ref": "result:<n>"}` dans les arguments (#12) ; pour un rôle, contrôle de son contexte déclaré.
2. Validation des arguments par le schéma ; en cas d'erreur, résultat d'erreur actionnable pour le modèle (D4).
3. Politiques `before_tool` : droits, approbation (`Pause`), refus (`Deny`).
4. Exécution avec timeout, et la clé d'idempotence `hash(run_id, call_id)` dans le `ToolContext` (#18).
5. Déport si le résultat dépasse le seuil de l'outil (#16) : contenu complet dans le stockage d'artefacts, aperçu structuré pour le modèle, facette `offloaded`. Un outil intégré `artifact_read(ref, offset, limit)` est exposé seulement si un déport a eu lieu dans le run. La troncature simple ne sert que sans stockage d'artefacts.
6. Politiques `after_tool`.

**Lot parallèle :** tous les appels d'un tour s'exécutent en parallèle ; chaque résultat est écrit dès qu'il arrive. Si certains exigent une approbation, les autres s'exécutent d'abord, puis le run passe en `PAUSED`.

**Approbation** (#17) : une politique peut exiger une approbation sur n'importe quel outil, mais ne peut la lever que si l'outil n'est pas en `always` (seule la config admin le peut). Déroulé :

```
Pause → approval.requested (outil, arguments, motif, scope requis, expire_at)
      → approval.granted  (arguments éventuellement modifiés → Replace)
      | approval.rejected (→ Deny : motif renvoyé au modèle)
      | approval.expired  (→ Deny ou Fail, configurable)
```

L'identité de l'approbateur est enregistrée. Canaux : API REST (scope `approve`), elicitation MCP, API Python ; jamais un outil MCP.

**Reprise d'un appel interrompu** (#18, #26) :

| Cas | Traitement |
|---|---|
| `side_effects: none` ou `idempotent: true` | Réexécution |
| Effet de bord, non idempotent | Pas de réexécution à l'aveugle : erreur « état inconnu après interruption » renvoyée au modèle (défaut), ou pause pour vérification humaine |

Pour les API externes qui acceptent un en-tête `Idempotency-Key`, l'outil leur transmet la clé. Pour MCP, la clé passe dans le champ `_meta` de l'appel (convention loom-ia).

**`IdempotencyStore` et décorateur `@idempotent`** (#49) :

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

**Serveurs MCP** (#19) :

| `scope` | Connexion | Usage |
|---|---|---|
| `shared` (défaut) | Une par process, partagée | Serveurs sans état |
| `tenant` | Une par client, avec ses identifiants | Serveurs qui détiennent des données client |
| `run` | Ouverte et fermée avec le run | Serveurs à état, sandbox |

Connexion à la première utilisation, contrôle de santé, reconnexion avec backoff et disjoncteur, fermeture après inactivité, liste d'outils en cache rafraîchie sur `tools/list_changed`. Les outils sont préfixés par leur serveur (`crm__rechercher`). La sélection des outils par run (D7) tient compte des outils propres à chaque client.

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

### 9.6 Rôles et sous-agents

**Ce que reçoit un rôle** (#12) : ses arguments, plus le contexte qu'il déclare :

| Contexte | Contenu |
|---|---|
| `user_input` | Message original de l'utilisateur, mot pour mot |
| `attachments` | Pièces jointes du run (références) |
| `tool_results: [noms]` | Tous les résultats réussis de ces outils dans le run ; sans aucun, le rôle est refusé |
| `session_summary` | Dernier résumé de compaction |
| `last_turns: N` | N derniers échanges de la session |
| `caller_context` | Métadonnées de l'appelant (A8) |

Le message du rôle est construit par un `input_template` explicite (`{{ args.x }}`, `{{ context.y }}`, #50) ; sans template, le rôle reçoit les blocs de contexte balisés, suivis des arguments en JSON.

**Appel d'un rôle :** un seul appel de modèle, sans historique ni outils, journalisé dans le run de l'orchestrateur entre `tool.called` et `tool.completed` (`model.responded` avec le `call_id` de l'appel, enveloppe au nom du rôle). Une erreur du modèle ou une sortie vide deviennent un résultat d'erreur. Le délai par défaut des outils ne s'applique pas ; ceux du modèle et son retry bornent l'appel.

**Références `$ref`** (#12) : `{"$ref": "result:<n>"}` désigne le n-ième appel d'outil du run. Quand l'agent a des rôles, chaque résultat montré à l'orchestrateur commence par sa référence (`[result:3]`), et son prompt système explique `$ref`.

**Rôle vision :** il déclare le contexte `attachments` et reste masqué quand le run n'a pas de pièce jointe (C4).

**Outil terminal** (#13) : il n'est terminal que s'il est seul dans le tour et n'a pas échoué. Sa sortie passe par les hooks `on_output` et le réglage `stream_output`, et doit respecter le schéma de sortie de l'agent s'il en a un. En cas d'échec, le résultat revient à l'orchestrateur. Sa description reçoit automatiquement la mention « à appeler seul » ; un appel en parallèle ne déclenche pas la règle et est signalé dans les logs (`policy.decided` en 3.1). Dans le journal, `run.completed` référence le `tool.completed` terminal, sans duplication.

**Sous-agent** (#4) : l'outil `AgentTool` crée un `RunState` enfant, sauvegardé séparément.

- L'enfant hérite du contexte (client, utilisateur) et reçoit une part du budget du parent.
- Son historique n'entre jamais dans celui du parent : seule sa sortie finale revient, comme résultat d'outil.
- Sa consommation est ajoutée à celle du parent ; `root_run_id` permet de lire tout l'arbre.
- `depth` est limité par `max_depth`.
- Après un plantage, on reprend l'enfant au lieu de le relancer.
- Si l'enfant attend une validation, le parent passe en `WAITING_CHILD`.
- L'annulation se propage du parent vers ses enfants.
- Un enfant est éphémère par défaut, sans session propre.

### 9.7 Fiabilité des sorties

**Contrats** (E1) : schéma JSON, regex, longueur, sortie non vide. Applicables à n'importe quel rôle, outil, ou à la réponse finale (E5).

**Réparation** (#20) : la réparation est faite par le modèle qui a produit la sortie, dans sa propre conversation.

1. **Normalisation déterministe** d'abord : retrait des blocs de code, extraction du JSON, nettoyage des espaces (`normalized: true` dans `guard.checked`).
2. **Tour de réparation :** pour la réponse finale, l'orchestrateur repasse en `READY_FOR_MODEL` avec un message de diagnostic marqué `kind: repair`.

| Aspect | Règle |
|---|---|
| Outils pendant la réparation | `repair_tools` : `none` par défaut pour un échec de format, `allowed` pour un échec de fond |
| Sortie structurée | Schéma JSON natif si le modèle l'accepte |
| Limites | `max_attempts` ; chaque tentative compte dans les itérations et le budget |
| Épuisement | `on_failure` : `fail`, réponse marquée `unverified`, ou message de repli |
| Streaming | Rien à faire avec `after_guards` ; avec `live`, `model.delta.reset` puis la nouvelle réponse |

Les tentatives refusées restent dans le journal mais sont exclues de l'historique de session. Un appel de réparation dédié à un autre modèle n'est pas prévu en V2 ; la stratégie de réparation reste interchangeable.

**Juge** (#21) :

- Critères avec seuils, bloquants ou non.
- Un juge qui utilise le même modèle que l'évalué (chaînes de secours comprises) déclenche un avertissement, et une erreur en profil prod.
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

## 10. Modèles de langage

### 10.1 Définition d'un LLM

| Champ | Rôle |
|---|---|
| `id` | Identifiant utilisé par les rôles |
| `sdk` | `anthropic` ou `openai` : choisit l'adaptateur (#9) |
| `api` | Pour `sdk: openai` : `chat` (défaut) ou `responses` ; refusé avec `sdk: anthropic` |
| `base_url`, `model` | Point d'accès et modèle du fournisseur |
| `api_key_env` | Nom de la variable qui contient la clé |
| Réglages | `max_tokens`, `params` (fusion par bloc entier, B6) |
| Capacités | Vision, outils, thinking, JSON natif, `image_input` (`base64`, `url`, `file_id`), taille et formats d'image, `tool_result_media`, `streaming`, fenêtre de contexte |
| Tarifs | Prix d'entrée, de sortie, de cache |

```yaml
# Exemple indicatif
models:
  - id: M3_MAIN
    sdk: anthropic
    base_url: https://api.minimax.io/anthropic
    model: MiniMax-M3
    api_key_env: M3_API_KEY
  - id: GPT-OSS-120B
    sdk: openai
    api: chat
    base_url: https://api.together.ai/v1
    model: openai/gpt-oss-120b
    api_key_env: TOGETHER_API_KEY
```

- `api: responses` permet de renvoyer le raisonnement chiffré ; `chat` couvre les fournisseurs compatibles (Together, vLLM, Ollama).
- Les SDK sont des extras (`loom-ia[anthropic]`, `loom-ia[openai]`), importés à la demande, et n'apparaissent que dans leur adaptateur.
- Leurs retries internes sont désactivés (`max_retries=0`) : une seule politique de retry, celle de loom-ia.
- Chaque adaptateur a des tests de contrat avec des réponses HTTP enregistrées (respx).

### 10.2 Streaming

**Port minimal** (#11) : `ModelClient.stream(request) -> AsyncIterator[ModelChunk]`. `complete()` est un utilitaire qui rassemble le flux.

**Morceaux neutres :** `TextDelta`, `ReasoningDelta`, `ToolCallStarted`, `ToolArgsDelta`, `ToolCallEnded`, `UsageDelta`, `Stopped(reason)`. Le `provider_meta` peut arriver en cours de flux.

**Accumulation :** les deltas partent sur le bus (`model.delta`) ; la réponse complète est écrite (`model.responded`). Les arguments d'outils partiels ne sont jamais transmis aux hooks.

| Cas | Traitement |
|---|---|
| Échec après le début du flux | Appel relancé en entier ; `model.delta.reset` |
| Fournisseur sans streaming | Flux simulé d'un seul morceau (`streaming: false`) |
| Usage donné en fin de flux | Géré par l'adaptateur (par exemple `include_usage` chez OpenAI) |
| Délais | Trois timeouts : premier token, silence entre deux morceaux, total |
| Guard sur la réponse finale | `stream_output: live \| after_guards` par agent : `live` par défaut sans guard de sortie, sinon réponse en tampon envoyée après validation |

**Voix :** la voix exige `live` ; les guards d'un agent vocal doivent être légers et compatibles avec le flux, ou ne s'appliquer qu'aux outils.

### 10.3 Erreurs, retry et secours

| Type d'erreur | Retry sur le même modèle | Bascule vers le secours |
|---|---|---|
| `transient` (429, 5xx, timeout, réseau) | Oui, backoff et `Retry-After` | Oui, retries épuisés |
| `overloaded` | Oui | Oui, retries épuisés |
| `quota_exhausted` | Non | Oui, immédiatement |
| `context_overflow` | Non | Optionnel, vers un modèle à plus grande fenêtre |
| `auth` (401, 403) | Non | Non : erreur de config |
| `invalid_request` (400) | Non | Non : bug |
| `content_filtered` | Non | Non, par défaut |

- Chaîne de secours déclarée par rôle : `model: M3_MAIN, fallbacks: [SONNET]`, avec vérification des capacités au démarrage.
- Adhérence : un run qui a basculé reste sur le secours jusqu'à la fin.
- Disjoncteur par modèle : après N échecs, le modèle est écarté pendant T secondes pour tous les runs.
- À la bascule, le cache de prompt est perdu et le budget utilise le tarif du secours.
- Événements : `model.retried`, `model.fell_back`.

### 10.4 Raisonnement et images

**Raisonnement** (#7) : le `RunState` garde un bloc `Reasoning` neutre. L'adaptateur décide quoi renvoyer selon les capacités du modèle : Anthropic exige les blocs signés pendant une boucle d'outils ; l'API Responses d'OpenAI accepte le raisonnement chiffré ; d'autres API compatibles l'ignorent ou le refusent. Après une bascule de modèle, le raisonnement d'un autre fournisseur est écarté. Il est retiré au stockage de la session.

**Images** (#14) : le `RunState` ne garde qu'une référence. L'adaptateur la résout à l'appel selon `image_input` : base64 par défaut, lu depuis le stockage d'artefacts ; URL signée à courte durée de vie seulement si le modèle l'accepte et que la config l'autorise (RGPD). Si le format ou la taille ne conviennent pas : erreur explicite avant l'appel, ou conversion par un hook.

## 11. Journal, sessions et persistance

### 11.1 `EventStore`

Le journal fait foi (#22). Le port `EventStore` offre trois opérations :

| Opération | Rôle |
|---|---|
| `append(events, expected_seq)` | Écriture au fil de l'eau, refusée si la séquence a bougé (verrou optimiste entre deux runs d'une même session) |
| `read(…, after_seq)` | Relecture d'une session ou d'un run à partir d'une position |
| `query(EventQuery)` | Recherche par enveloppe et facettes |

```
EventQuery(tenant, session, run, types, categories, status,
           agent, role, tool_name, model_id, since, until, limit, cursor)
```

- Le client est obligatoire dans chaque requête ; une requête multi-clients exige le scope `admin` (#34).
- Exception au verrou optimiste : `session.compacted` ne couvre que des événements anciens et s'écrit sans `expected_seq` (#23).
- Les payloads ne contiennent que des références pour les images et les gros résultats.

| Backend | Usage | Indexation |
|---|---|---|
| Mémoire | Tests, runs sans pause | — |
| JSONL | Mode librairie, dev | Un fichier par session, requêtes via DuckDB |
| SQLite / Postgres | Mode service | Colonnes pour l'enveloppe et les facettes, payload en JSONB, index composés dont `(session_id, seq)` ; RLS en Postgres |
| Firestore | Mode service | Sous-collection `events` par session, quelques facettes indexées |

### 11.2 Projections

```
                    ┌─▶ Historique LLM   (messages envoyés au modèle)
Journal (append) ───┼─▶ RunState         (reprise, pause, checkpoint)
                    ├─▶ Traces           (arbre de spans pour l'interface)
                    ├─▶ Usage            (tokens, coûts)
                    ├─▶ run_summaries    (liste et filtres de l'interface)
                    └─▶ Rejeu            (réponses modèle et outils enregistrées)
```

- **Historique LLM :** matérialisé en **snapshot** à la fin de chaque run ; le journal n'est relu qu'au-delà. Il repart du dernier `session.compacted`, exclut les tentatives refusées et le raisonnement.
- **Checkpoint :** chaque événement durable en est un (#25).
- **`run_summaries` :** run_id, agent, statut, durées, coût, tokens, nombre d'étapes, erreurs ; mise à jour à chaque `run.transitioned` (#32).

**Snapshot et compaction ne se confondent pas :** le snapshot est une vue matérialisée sans LLM, pour lire vite ; la compaction est un résumé par LLM, pour réduire le contexte.

### 11.3 Compaction

La compaction ne réécrit rien : elle ajoute `session.compacted` (résumé, `up_to_seq`) (#23).

```
runtime (fin du run) ──enqueue──▶ TaskQueue ──▶ worker
                                                  │
                                       sessions.CompactionJob
                                         1. lit la projection d'historique
                                         2. runtime.run("_compaction", segment)
                                            └─ engine + models + guards (fidélité)
                                         3. append session.compacted
```

- **Déclenchement :** à `run.completed`, si l'historique dépasse `compact_over_tokens` ; le `runtime` appelle `sessions.maybe_schedule(session_id)`.
- **Exécution :** agent interne `_compaction` (pas d'outils, un appel modèle, contrôle de fidélité en `on_output`), lancé comme un run système (`kind: compaction`, `triggered_by`). Un seul job par session à la fois (clé `session_id` + `up_to_seq`). Modèle dédié `compact_model`.
- **Résumé glissant :** les `keep_last` derniers tours restent intacts ; le reste est résumé avec le résumé précédent. Gros résultats et images sont remplacés par leur référence ; raisonnement et tentatives refusées sont exclus.
- **Fidélité :** consigne de conserver identifiants, montants, dates, noms et numéros de devis ; contrôle déterministe que les nombres, e-mails et références du segment se retrouvent dans le résumé (une nouvelle tentative, puis avertissement).
- **Filet de sécurité :** au démarrage d'un run, si l'historique dépasse `compact_hard_tokens` sans résumé disponible, `sessions.ensure_fits()` lance `_compaction` en synchrone ; en cas d'échec, les tours les plus anciens sont retirés, avec un avertissement.
- **Exécutant :** tâche asyncio dans le process en mode librairie (`aclose()` attend les compactions en cours, dans la limite d'un timeout) ; tout worker en mode service.
- **Mesure :** tokens avant et après, coût.

### 11.4 Artefacts

- Les pièces jointes sont validées à l'entrée : format par signature binaire, taille (G1).
- Stockage hors journal, désigné par URI et préfixé par client (`tenant/<id>/...`).
- Les outils peuvent produire des artefacts, récupérables par l'appelant (G3).
- En mode librairie, le stockage par défaut est un dossier local ; GCS en mode service.

### 11.5 RGPD

- Suppression physique par `session_id` ou `tenant_id`, artefacts compris : exception assumée à l'immuabilité du journal.
- Option : chiffrement des payloads avec une clé par client ; supprimer la clé rend son journal illisible, sauvegardes comprises (*crypto-shredding*) (#30).
- Sessions listables et exportables (F7). Les résumés de compaction sont supprimés avec la session.

## 12. Exécution durable

### 12.1 File de tâches

Port `TaskQueue` (#27) : `submit(job, key, delay?)`, `status(job_id)`, `cancel(job_id)`.

| Job | Usage |
|---|---|
| `run` | Run en arrière-plan (H5) |
| `resume` | Reprise après une approbation |
| `compaction` | Résumé de session (§11.3) |
| `expire_approval` | Tâche différée qui émet `approval.expired` |
| Déclencheurs planifiés | H6 |

**La durabilité vient du journal, pas de la file :**

- Au démarrage, `runtime.recover()` remet en file les runs restés dans un état actionnable ; une file non durable suffit donc en mode librairie.
- Une livraison en double est sans risque : `drive(run_id)` reconstruit l'état depuis le journal.

**Un seul pilote par run :** le worker prend une concession (`run.claimed`, `worker_id`, `lease_until`) et la renouvelle ; si le worker meurt, elle expire et un autre reprend.

| Mode | File | Remarque |
|---|---|---|
| Librairie | asyncio, dans le process | Récupération au démarrage via `recover()` |
| Service | RabbitMQ | Acquittement en fin de job ; relivraison sans risque |

**Déclencheurs :** webhook (endpoint REST qui crée le run), planification (adaptateur cron qui met en file), file de messages (consommateur qui crée les runs).

### 12.2 Pause en mode librairie

- Un agent qui peut se mettre en pause (outil en `approval: always | policy`, ou politique qui peut renvoyer `Pause`) exige un `EventStore` durable (JSONL au minimum) : erreur de config, avertissement seulement en profil dev (#28).
- Approbation asynchrone (défaut) : `run()` renvoie `RunResult(status=paused, run_id, pending_approvals)` ; plus tard, `await loom.approve(run_id, décision)` écrit la décision et met un `resume` en file.
- Approbateur en ligne : `run(..., approver=callback)` appelle le callback dans le process, sans état `PAUSED` durable (scripts, CLI, tests).
- Un job `expire_approval` est planifié dès la demande.

## 13. Bus d'événements et streaming

Le journal fait foi, le bus sert à notifier vite (#5).

```
drive ──append──▶ EventStore ──publish──▶ Bus ──▶ abonnés
      └──── model.delta (éphémères) ─────▶ Bus
```

- **Ordre :** écriture, puis publication ; un événement notifié existe toujours dans le journal.
- **Publication non bloquante :** le run n'attend jamais un abonné.
- **File bornée par abonné :** en cas de débordement, l'abonné perd les éphémères, reçoit un marqueur de trou et se resynchronise depuis le journal (`after_seq`).
- **Abonnés exhaustifs** (exports OTel, ledger persistant) : ils lisent le journal avec un curseur ; le bus ne leur sert que de signal.
- **Plusieurs workers :** `BusBackend` distribué (Postgres `LISTEN/NOTIFY`, Redis ou RabbitMQ).

**Streaming HTTP** (#38) :

- SSE pour les runs ; reconnexion par `Last-Event-ID` = `seq` : relecture du journal jusqu'au présent, puis bascule sur le direct.
- Actions (`approve`, `cancel`) en `POST` REST.
- Pas de WebSocket en V2. Pour la voix (`artisan-voice`), Pipecat gère le transport audio et appelle loom-ia par l'API Python (`loom.stream()`) ou par SSE.

## 14. Observabilité et rejeu

### 14.1 Traces

- Les spans découlent du journal (`span_id`, `parent_span_id`) : modèle maison pensé pour l'interface (#29).
- OpenTelemetry est un export, via un `EventSink` qui traduit les événements en spans.
- L'API de lecture (`EventQuery`, `run_summaries`) sert l'interface : lister, filtrer, afficher l'arbre et la chronologie d'un run (K5).

### 14.2 Niveaux de capture

| Niveau | Contenus | Protection |
|---|---|---|
| Journal | Complets, toujours | Isolation par client, chiffrement au repos, rétention configurable, suppression RGPD |
| Exports (OTel, logs) | Métadonnées par défaut, contenus en opt-in | Masquage configurable (e-mails, téléphones, IBAN…) |
| API de traces / interface | Selon le scope de la clé (`read` ou `read_content`) | Masquage à l'affichage |

### 14.3 Rejeu

Le rejeu est toujours possible, puisque le journal contient les réponses des modèles et les résultats d'outils (#31).

| Mode | Modèles | Outils |
|---|---|---|
| Identique | Réponses lues dans le journal | Résultats lus dans le journal |
| Variante (autre modèle, prompt ou config) | Appels réels | Lus dans le journal quand l'appel correspond ; jamais réexécutés s'ils ont des effets de bord (doublure ou erreur) |

- **Détection de divergence :** `request_hash` dans `model.responded` (empreinte de la requête) et comparaison des transitions (§9.3).
- **Décisions des politiques :** réutilisées en mode identique, réévaluées en variante.
- **Échanges HTTP bruts :** opt-in, pour le débogage.
- **Usages :** diagnostic, évals comparatives entre modèles et configurations (O1), tests de non-régression à partir de traces (O3).

### 14.4 Logs

- `logging` de la stdlib : `getLogger("loom_ia.…")` et `NullHandler` ; loom-ia ne configure jamais les handlers de l'application hôte (#45).
- `run_id`, `span_id` et `tenant_id` passent dans `extra`.
- `loom_ia.telemetry.configure_logging()` (JSON ou console), optionnel, est utilisé par la CLI et le mode service.
- Les logs restent secondaires : l'observabilité passe d'abord par le journal (K7).

## 15. Coûts et budgets

- **Comptage :** tokens d'entrée, de sortie, de cache et de raisonnement, lus dans `model.responded` (J1).
- **Coût :** calculé avec la grille tarifaire du modèle réellement utilisé, secours compris (J2).
- **Ventilation :** par run, rôle, modèle, session et client (J3). La consommation des sous-agents remonte au parent ; la compaction est comptée comme un run système avec le rôle `compaction`.
- **Plafonds :** par run, session, client et période ; action : avertir ou arrêter (J4). Le budget est une politique `before_model` : `Stop` fait passer le run en `FINALIZING`, qui produit une réponse forcée sans outils (dépassement borné à une génération).
- **Sous-agents :** chacun reçoit une part du budget de son parent.
- **Rapport :** consommation détaillée d'un run ou d'une session (J5).

## 16. Sécurité

### 16.1 Clés API

- Préfixe identifiable (`lk_...`), stockées hachées, jamais dans les logs (#39).
- Rattachées à un client, avec des scopes (`run`, `read`, `read_content`, `approve`, `admin`) et la liste des agents autorisés.
- Rotation, révocation et limitation de débit par clé.
- En V2, les clés sont déclarées hachées dans la config ; `loom keys create` affiche la clé une seule fois et donne le hash (#50).

### 16.2 Point d'accès MCP

- `approve` n'est jamais exposé comme outil MCP ; la validation passe par l'elicitation (un humain répond) ou par l'API REST.
- En HTTP : clé dans l'en-tête `Authorization`, validation de l'en-tête `Origin` (imposée par la spec MCP), écoute sur `localhost` seulement en local.
- En stdio : pas d'authentification, mais un profil local restreint.
- La spec MCP recommande OAuth pour les serveurs distants : il faudra vérifier que les clients visés acceptent une clé dans l'en-tête.
- Les sorties des agents sont renvoyées à un LLM tiers : risque d'injection de prompt à garder en tête.

### 16.3 Isolation et secrets

- Isolation logique par client sur le journal, les artefacts, les connexions MCP (`scope: tenant`), les secrets, les budgets (#34) ; RLS en Postgres ; `TenantRouter` pour une isolation physique.
- Secrets lus via `SecretProvider` ; la config ne contient que des noms de variables (`api_key_env`).
- Chiffrement au repos du journal ; clé par client en option (§11.5).

## 17. Configuration

### 17.1 Principes

- Un seul format : **YAML** (#35), lu avec `safe_load` puis validé strictement par les modèles Pydantic.
- Prompts dans des fichiers à part (`system_file`), relatifs à `prompts_dir`.
- Un fichier racine (`loom.yaml`) et un dossier d'agents (`agents_dir` → `agents/*.yaml`).
- Secrets : uniquement des noms de variables.
- `loom.schema.json` est généré depuis Pydantic et sert à l'éditeur (complétion, erreurs, via `yaml-language-server: $schema=...`) ; loom-ia ne le lit pas.
- La config en Python (M2) utilise les mêmes modèles Pydantic.

**Décisions de schéma** (#50) :

1. **Références vers du code Python** (outils, hooks, conditions) : un nom enregistré en code (`@loom.tool`) ou un chemin d'import `module:attr` ; le nom enregistré est essayé d'abord. `imports:` liste les modules chargés au démarrage.
2. **Variables dans les prompts et les templates :** `{{ var }}`, avec un moteur interne minimal (sans Jinja) ; une variable inconnue est une erreur.
3. **Profils :** fusion profonde des objets, remplacement des listes ; `params` et `llm` sont remplacés en bloc.
4. **Annotations MCP** (`readOnlyHint`, `destructiveHint`, `idempotentHint`) : valeurs par défaut de `side_effects` et `idempotent`, surchargeables par la config.
5. **Clés API en V2 :** stockées hachées dans la config ; `loom keys create` affiche la clé une seule fois et donne le hash.
6. **Version du schéma :** `version: 1` obligatoire.

### 17.2 Fichier racine `loom.yaml`

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

### 17.3 Modèles

```yaml
models:
  - id: HAIKU
    sdk: anthropic                    # anthropic | openai
    api: chat                         # openai seulement : chat | responses
    model: claude-haiku-4-5-20251001
    base_url: https://api.anthropic.com
    api_key_env: ANTHROPIC_API_KEY
    max_tokens: 4096
    params: {temperature: 0}          # bloc transmis tel quel
    timeouts: {first_token: 20, idle: 30, total: 120}
    retry: {max_attempts: 3, max_delay: 30}
    circuit_breaker: {failures: 5, cooldown: 60}
    cache: {system: true}
    capabilities:
      tools: true
      vision: true
      thinking: false
      native_json: true
      streaming: true
      image_input: [base64, url]
      image_formats: [jpeg, png, gif, webp]
      max_image_bytes: 5242880
      tool_result_media: true
      context_window: 200000
    pricing: {input: 1.0, output: 5.0, cache_read: 0.1, cache_write: 1.25}   # $ / M tokens
```

### 17.4 Agents (`agents/*.yaml`)

```yaml
name: relance_devis
description: Relance les devis en attente.
expose: {rest: true, mcp: true}
main:                                 # RoleSpec, sans name
  model: M3_MAIN
  fallbacks: [SONNET]
  system_file: relance_devis/main.md
max_iterations: 10
timeout: 300
max_depth: 2
stream_output: after_guards           # live | after_guards
output: {...}                         # OutputSpec (réponse finale, A7)
judge: {...}                          # JudgeSpec (on_output)
budget: {...}                         # surcharge des défauts
approval: {expires_after: 86400, on_expire: deny, on_unknown_state: error}
tools:
  - python: myapp.tools:chercher_devis
    timeout: 30
    offload_over: 20000
    side_effects: none
    idempotent: true
  - mcp: crm
    include: [rechercher, fiche_client, envoyer_email]
  - mcp: agenda
    exclude: [supprimer_creneau]
roles:
  - name: rediger_relance
    description: Rédige un e-mail de relance de devis.
    model: HAIKU
    system_file: relance_devis/rediger.md
    input_schema: {...}
    input_template: "Devis : {{ context.tool_results.chercher_devis }}\nDemande : {{ context.user_input }}\nTon : {{ args.ton }}"
    context: [user_input, {tool_results: [chercher_devis]}]   # tout contexte déclaré sert au template
    llm: {max_tokens: 1024, params: {temperature: 0}}
    terminal: false
    timeout: 60                       # sinon, délais et retry du modèle
    output: {...}
    judge: {...}
subagents:
  - agent: verifier_devis
    name: verifier
    description: Vérifie la cohérence d'un devis.
    budget_share: 0.3
policies:
  - hook: myapp.policies:plafond_montant
    points: [before_tool]
    params: {max: 5000}
    timeout: 2
    on_error: block                   # block | allow
```

**OutputSpec et JudgeSpec :**

```yaml
output:
  schema: {...}                       # ou schema_file
  must_match: "..."                   # regex
  must_not_match: "```"
  max_chars: 12000
  normalize: true
  repair: {max_attempts: 1, tools: auto}      # auto | none | allowed
  on_failure: fail                            # fail | unverified | fallback
  fallback_message: "..."

judge:
  model: SONNET
  when: {sample: 0.2}
  criteria:
    - {name: engagements, rule: "...", min_score: 0.8, blocking: true}
  repair: {max_attempts: 1}
  on_failure: fail
```

### 17.5 Serveurs MCP

```yaml
mcp_servers:
  - name: crm
    transport: http                   # stdio | http
    url: https://crm.example/mcp
    headers_env: {Authorization: CRM_TOKEN}
    # stdio : command, args, env
    scope: tenant                     # shared | tenant | run
    idle_timeout: 300
    tools:
      envoyer_email: {side_effects: irreversible, approval: always}
```

Un agent peut référencer plusieurs serveurs : voir §9.5.

### 17.6 Stockages, sessions, exécution

```yaml
storage:
  events:      {backend: jsonl, path: data/events}      # memory|jsonl|sqlite|postgres|firestore (+ dsn_env)
  artifacts:   {backend: local, path: data/artifacts}   # local|gcs (+ bucket)
  idempotency: {backend: journal}                       # journal|memory|sqlite|postgres|firestore|redis
  bus:         {backend: memory}                        # memory|postgres|redis|rabbitmq
  queue:       {backend: asyncio}                       # asyncio|rabbitmq (+ url_env)
  encryption:  {per_tenant_keys: false}
  retention:   {events_days: null}

sessions:
  compaction:
    model: HAIKU
    over_tokens: 12000
    hard_tokens: 150000
    keep_last: 6
    fidelity_check: true
    system_file: null                 # surcharge du prompt interne

execution:
  tools: {timeout: 30, validate_arguments: true, offload_over: 50000}
  lease: {ttl: 60, renew_every: 20}
  shutdown_timeout: 30
```

### 17.7 Budgets et télémétrie

```yaml
budgets:
  run:     {max_cost: 0.05, max_calls: 25}
  session: {max_cost: 1.0}
  tenant:  {max_cost_per_day: 10.0}
  on_exceed: stop                     # warn | stop

telemetry:
  logging:   {level: INFO, format: console}             # console | json
  capture:   {exports: metadata, raw_exchanges: false}  # metadata | content
  redaction: {patterns: [email, phone, iban]}
  exporters: [{type: otel, endpoint_env: OTEL_EXPORTER_OTLP_ENDPOINT}]
  bus:       {subscriber_queue: 1000}
```

### 17.8 Clients, sécurité, serveur

```yaml
tenants:
  - id: dupont-plomberie
    agents: [relance_devis]
    tools_deny: []
    models: {M3_MAIN: SONNET}         # correspondance des modèles
    budgets: {tenant: {max_cost_per_day: 5.0}}
    quotas: {runs_per_minute: 30}
    approvals: {envoyer_email: always}
    secrets: {CRM_TOKEN: DUPONT_CRM_TOKEN}
    variables: {entreprise: Dupont Plomberie}
    storage: null                     # isolation physique (TenantRouter)

security:
  api_keys:
    - id: dupont-app
      tenant: dupont-plomberie
      hash: "sha256:…"
      scopes: [run, read, approve]
      agents: [relance_devis]
      rate_limit: {per_minute: 60}
      expires: 2027-01-01

server:
  http: {host: 127.0.0.1, port: 8000, base_path: /loom}
  mcp:  {http: true, allowed_origins: []}
```

### 17.9 Profils et contrôles

- Profils `dev` et `prod` (M4), dans `profiles:`. Fusion profonde des objets, remplacement des listes, `params` et `llm` remplacés en bloc. Différences décidées : juge corrélé et juge bloquant échantillonné en avertissement (dev) ou en erreur (prod) ; stockage non durable pour un agent qui peut se mettre en pause toléré en dev seulement ; `judges="skip"` autorisé en dev seulement.
- **Surcharges par client :** liste fermée (§6).
- **Enregistrement des agents** (#48) : statique, par la config ou le code ; `loom serve --reload` recharge les agents en dev ; enregistrement dynamique plus tard.

**Contrôles au démarrage** (M5) : rôle ou modèle inconnu, extra manquant, décision de politique non autorisée à un point, capacités des modèles de secours, juge corrélé, stockage durable requis, champ `api` incompatible avec le `sdk`, et :

- `version` inconnue ;
- `system` et `system_file` fournis ensemble ;
- référence Python introuvable ;
- variable `{{ }}` non définie ;
- clé métier d'idempotence avec un backend `journal` ou `memory` ;
- juge bloquant échantillonné ;
- `scope: tenant` sans secrets pour un client ;
- serveur MCP référencé mais non déclaré, préfixes MCP en double.

## 18. Accès

### 18.1 API Python

```python
# Exemple indicatif
loom = Loom.from_config("loom.yaml")

loom.register(
    AgentSpec(
        name="relance_devis",
        main=Role(model="M3_MAIN", system="..."),
        roles=[...],
        tools=[FunctionTool(chercher_devis), mcp("crm")],
    )
)

async with loom:
    res = await loom.run("relance_devis", "Relance M. Dupont", session_id="c-42")
    async for ev in loom.stream("relance_devis", "..."):
        ...
    await loom.approve(res.run_id, decision)
```

- `run()` accepte `session_id`, `tenant`, des pièces jointes et un `approver` optionnel ; il renvoie un `RunResult` (statut, réponse, `run_id`, approbations en attente).
- `stream()` renvoie un itérateur d'événements (durables et éphémères).

### 18.2 HTTP REST

Application ASGI (FastAPI, extra `loom-ia[http]`), autonome ou montée dans un projet existant :

```python
app.mount("/loom", loom.asgi_app(rest=True, mcp=True))
```

| Méthode | Route | Rôle |
|---|---|---|
| `GET` | `/v1/agents` | Lister les agents |
| `POST` | `/v1/agents/{name}/runs` | Lancer un run (synchrone ou arrière-plan) |
| `GET` | `/v1/runs/{id}` | Statut et résultat |
| `GET` | `/v1/runs/{id}/events` | Streaming SSE |
| `POST` | `/v1/runs/{id}/approve` · `/cancel` | Validation humaine, annulation |
| `GET` | `/v1/sessions/{id}` · `/v1/traces/...` | Sessions, traces |

OpenAPI est généré, ce qui permet de générer le client de l'interface.

### 18.3 Serveur MCP

- Chaque agent devient un outil MCP ; deux outils de contrôle s'ajoutent : `run_status` et `cancel`.
- Traces et sessions exposées en ressources en lecture seule (`loom://runs/{id}`).
- Transports : stdio (local, `loom mcp`) et HTTP, monté dans la même application que l'API REST.
- Limites : notifications de progression seulement ; validation humaine par elicitation, sinon pause et validation via l'API REST.

### 18.4 CLI

Dans le noyau, avec `argparse`. Commandes mentionnées dans la conception : `loom serve` (extra `http`, `--reload` en dev), `loom worker`, `loom mcp`, et les commandes de la fonction N4 : lancer un run, rejouer, inspecter une trace, valider la config.

## 19. Projet

| Sujet | Décision |
|---|---|
| Package | `loom-ia`, nouveau dépôt ; import `loom_ia` (#42) |
| Licence | Apache 2.0, en-têtes SPDX `Apache-2.0` (#46) |
| Python | 3.14, géré avec uv, en local comme dans l'image Docker (#43) |
| Découpage | Un package avec des extras (#41) |
| Dépendances du noyau | `pydantic`, `pyyaml`, `jsonschema` ; toute nouvelle dépendance justifiée par écrit ; borne haute sur les majeures fragiles ; `uv.lock` versionné (#44) |
| Logs | `logging` de la stdlib (#45) |

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

| Extra | Dépendances |
|---|---|
| `anthropic` | `anthropic` |
| `openai` | `openai` |
| `mcp` | `mcp` (borne `<2`) |
| `http` | `fastapi`, `uvicorn`, `sse-starlette` |
| `sqlite` | `aiosqlite` |
| `postgres` | `asyncpg` |
| `firestore` | `google-cloud-firestore` |
| `gcs` | `google-cloud-storage` |
| `rabbitmq` | `aio-pika` |
| `redis` | `redis` |
| `otel` | `opentelemetry-sdk` et un exporteur |
| `all` | Tous les extras |

- Outils de dev (pytest, respx, ruff, pyright, import-linter) dans `[dependency-groups]`.
- Adaptateurs externes par entry points (`loom_ia.adapters`), par exemple un package `loom-ia-firecracker` pour la sandbox.

**Règles `import-linter` :**

```
couches   : access > runtime > engine > core
interdits : core, engine  ↛  adapters, anthropic, openai, fastapi, mcp
indépendance : adapters.models ↮ adapters.stores ↮ adapters.queue …
```

**Tests :**

- CI noyau seul (`uv sync`, sans extra) et CI complète (`uv sync --all-extras`).
- Tests d'adaptateur ignorés si l'extra manque (`pytest.importorskip`).
- `apply` testé sans simulation ; `step` et `drive` avec les faux modèles et faux outils du package `testing`.
- Tests de contrat des adaptateurs de modèles avec des réponses HTTP enregistrées (respx).

## 20. Questions ouvertes

| Sujet | État |
|---|---|
| Ordre de réalisation (#47) | Jalons à revoir ensemble |

**Évolutions prévues après V2 :** API d'administration et port `TenantStore` ; enregistrement dynamique d'agents déclaratifs ; stratégie de réparation par un modèle dédié ; WebSocket si un besoin apparaît.
