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
- Une décision s'écrit dans le span de la demande qu'elle tranche, quel que soit le canal : une décision venue de l'extérieur n'a pas d'étape à elle, et les deux chemins se lisent de la même façon (4.5).
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

**Réalisation (phase 5.1a)** (détails : `fonctions.md`, points 33 et 34) : section `tenants` ; **sans elle, seul `default` existe, avec elle la liste est fermée** et un client inconnu est refusé. Surcharges réalisées : `agents`, `tools_deny`, `models`, `approvals`, `secrets`, `variables`, `storage`. La correspondance des modèles porte sur la **définition** du modèle, donc elle vaut partout à la fois, et la config d'un client repasse les contrôles de cohérence. Un agent est monté par client. `TenantRouter` choisit le journal et les artefacts d'un client qui déclare son propre `storage`. Budgets et quotas par client : 5.1b.

**Réalisation (phase 5.1b)** (détails : `fonctions.md`, point 34) : `budgets.tenant` (`max_cost_per_day`, `max_tokens_per_day`, et leurs équivalents par mois) et `quotas.runs_per_minute` dans la fiche d'un client, plus `rate_limit` sur une clé d'API. Le budget d'un client est lu **une fois, au lancement du run** — une enveloppe épuisée dit « reviens demain », pas « réponds vite » — et un run refusé n'écrit rien. Le quota et le débit d'une clé sont des fenêtres **glissantes** d'une minute ; les budgets, des fenêtres **calendaires** en UTC.

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
| `EventBus` | Notifier entre workers (nouvelles d'écriture, pas d'événements) | Mémoire (défaut), Postgres `LISTEN/NOTIFY`, Redis |
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
  agent, status, step, messages, pending_calls, usage, budget, context, models
```

- `status` suit la machine à états (§9.1).
- `pending_calls` : appels d'outils en cours, avec leur éventuel run enfant.
- Les compteurs de tentatives (`Retry`) vivent dans l'état (#2).
- `models` : modèle courant de chaque emplacement qui a basculé vers un secours (adhérence, §10.3).
- Le `RunState` ne contient que des données sérialisables ; modèles, outils, stores et hooks sont passés à part, dans le `RunContext` (#3).
- Il n'est jamais stocké comme tel : c'est la projection des événements de son `run_id` (#24).

### 8.3 Événements

**Enveloppe commune** (#22) :

```
Event
  event_id (UUIDv7, triable)   seq   ts   schema_version
  tenant_id  session_id  run_id  root_run_id  span_id  parent_span_id
  type       ex. "tool.completed"
  category   run | message | model | tool | guard | policy | approval | artifact | session | circuit
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
| `run.started` | agent, kind (`normal`, `compaction`), triggered_by, entrée (réf.), contexte, `judges` (`auto`, `force`, `skip`), `budget` (part reçue du parent) |
| `message.user` | blocs de contenu ; `kind` (`request`, `repair`), politique et `tools` d'une réparation |
| `model.responded` | model_id, fournisseur, blocs, usage, coût, stop_reason, latence, tentatives, request_hash, call_id (rôle délégué), `judge` (appel d'un juge) |
| `model.retried` | tentative, type d'erreur, délai, call_id (rôle délégué) |
| `model.fell_back` | emplacement (`main`, rôle, `judge:<nom>`), ancien modèle, nouveau modèle, motif (type d'erreur ou `circuit_open`), erreur, call_id, judge |
| `circuit.opened` | cible (`model`, `mcp`), modèle ou serveur, échecs de suite, pause, dernière erreur |
| `idempotency.recorded` | clé, call_id, résultat |
| `idempotency.reused` | clé, call_id, outil |
| `tool.called` | tool_name, tool_kind, call_id, arguments, refs, child_run_id (sous-agent) |
| `tool.completed` | tool_name, call_id, is_error, sortie (blocs, références de fichiers, aperçu et référence si déportée), latence, taille, consommation d'un sous-agent (usage, coût) ; facette `offloaded` |
| `tool.source_unavailable` | source (serveur MCP), erreur, required |
| `guard.checked` | guard, cible (`output`, `role:<nom>`, `tool:<nom>`), outcome (`passed`, `failed`, `skipped`), motif, tentative, normalized, resolution, politique, call_id |
| `judge.evaluated` | juge, cible, modèle juge, notes par critère (note, seuil, bloquant, motif), `passed`, `blocked`, tentative, politique, call_id |
| `budget.exceeded` | portée (`run`, `session`), limite (`max_cost`, `max_tokens`, `max_calls`), plafond, consommation, action (`warn`, `stop`), politique |
| `policy.decided` | politique, point, décision, motif, call_id, tentative et `tools` (`Retry`), arguments ou réponse remplacés, `error` |
| `approval.requested` / `.granted` / `.rejected` / `.expired` | tool_name, call_id, arguments, auteur, motif, scope, expire_at |
| `artifact.stored` | uri, type MIME, taille, nom, origine (`attachment`, `tool_output`, `offload`), call_id |
| `session.snapshot` | historique matérialisé, up_to_seq, messages, tokens estimés |
| `session.compacted` | résumé, up_to_seq, tours gardés, tokens avant/après, coût, fidélité |
| `session.trimmed` | up_to_seq, messages retirés, motif |
| `step.started` / `.completed` | step_no, état, effet, durée, statut |
| `run.transitioned` | from, to, step_no, cause |
| `run.cancelled` | motif, auteur, itérations, usage, coût |
| `run.paused` / `.resumed` | motif |
| `run.claimed` | worker_id, lease_until |
| `run.completed` / `.failed` / `.cancelled` | itérations, usage total, coût total, erreur ; `data` et `unverified` (réponse finale) |

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
- Annulation et timeout : le délai est contrôlé avant chaque étape et borne celle qui commence ; l'annulation vient de `Loom.cancel`, seule à écrire `run.cancelled` — un run seulement interrompu ne laisse rien et reste reprenable (4.2a).
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

**Réalisation (phase 3.1)** (détails : `fonctions.md`, point 2) :

- Une politique est une fonction décorée par `@policy(points, decisions)` (`loom_ia.policies`) ; elle reçoit le sujet du point (`BeforeModel`, `AfterModel`, `BeforeTool`, `AfterTool`, `OnOutput`) et son `PolicyContext` (nom, `params`, réparations déjà demandées). Port `Policy` dans le noyau ; exécution de la chaîne dans `engine/hooks.py`.
- `before_model` s'exécute dans l'étape d'appel du modèle ; `after_model` et `on_output` au moment de décider la suite d'une réponse (réévalués à la reprise tant que leur décision n'est pas appliquée) ; `before_tool` et `after_tool` dans l'exécuteur.
- `Retry` à `after_model` et `on_output` : réparation par l'orchestrateur (diagnostic en `message.user` de `kind: repair`, exclu de l'historique de session avec la réponse refusée) ; à `after_tool`, un rôle est réparé par son propre modèle (depuis 3.2, §9.7) et le résultat d'un outil revient à l'orchestrateur en erreur avec le diagnostic. `Pause` est refusée au démarrage jusqu'en J4.3.
- Défauts : délai 5 s, `on_error: block`, `max_attempts: 1` (par run à `after_model` et `on_output`, par appel à `after_tool`). Politiques fournies : `loom.require_tool` (`tool_choice: required` tant qu'aucun outil n'a été appelé, jamais en `FINALIZING`) ; `loom.contract` (contrats de sortie, 3.2, §9.7) ; `loom.judge.<nom>` (juges, 3.3, §9.7) ; `loom.budget` (budgets, 3.4, §15).

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
  blocks      Text | Json | ArtifactRef           # ce que voit le modèle
  data        JSON structuré optionnel            # pour $ref, A7, l'interface
  is_error    bool
  artifacts   références produites (G3)
  offloaded   référence du contenu complet, s'il a été déporté (#16)
```

- Outils Python : une `str` donne `Text` ; `dict`, `list` ou modèle Pydantic donnent `Json` ; une `Image` est stockée et renvoyée en `ArtifactRef` ; un `ToolOutput` explicite donne le contrôle total.
- Une exception donne `is_error` ; une `ToolError(message)` porte un message exploitable par le modèle.
- MCP : `text` et `structuredContent` se traduisent directement ; `image`, `audio` et les ressources binaires sont stockés comme artefacts ; un lien de ressource reste une mention.
- Un outil rend ses fichiers en octets (bloc `InlineData`, jamais journalisé) : le moteur les range dans le stockage d'artefacts (`artifact.stored`, avant le `tool.completed`) et les remplace par leur référence. Sans stockage, le modèle reçoit une mention.
- Capacité du modèle `tool_result_media` : si le modèle n'accepte pas d'image dans un résultat d'outil, l'image part dans un message utilisateur placé après les résultats du tour, et le résultat la mentionne.

**Chaîne d'exécution d'un appel :**

1. Résolution des références `{"$ref": "result:<n>"}` dans les arguments (#12) ; pour un rôle, contrôle de son contexte déclaré.
2. Validation des arguments par le schéma ; en cas d'erreur, résultat d'erreur actionnable pour le modèle (D4).
3. Politiques `before_tool` : droits, approbation (`Pause`), refus (`Deny`).
4. Exécution avec timeout, et la clé d'idempotence `hash(run_id, call_id)` dans le `ToolContext` (#18).
5. Fichiers rangés, puis déport si le résultat dépasse le seuil de l'outil (#16, `offload_over`, en caractères de ce que verrait le modèle, 50 000 par défaut) : contenu complet dans le stockage d'artefacts (`data` en JSON s'il existe, sinon le texte), aperçu pour le modèle (début du texte, ou structure du JSON), facette `offloaded`. Un outil intégré `artifact_read(ref, offset, limit)` est exposé seulement si un déport a eu lieu dans le run ; `$ref` et `tool_results` transmettent le contenu complet. Ni un outil terminal ni `artifact_read` ne sont déportés. La troncature simple ne sert que sans stockage d'artefacts.
6. Politiques `after_tool`.

**Lot parallèle :** tous les appels d'un tour s'exécutent en parallèle ; chaque résultat est écrit dès qu'il arrive. Dans la requête suivante, les résultats du tour suivent l'ordre des appels, pas leur ordre d'arrivée : la requête ne dépend pas des durées des outils. Si certains exigent une approbation, les autres s'exécutent d'abord, puis le run passe en `PAUSED`.

**Approbation** (#17) : une politique peut exiger une approbation sur n'importe quel outil, mais ne peut la lever que si l'outil n'est pas en `always` (seule la config admin le peut). Déroulé :

```
Pause → approval.requested (outil, arguments, motif, scope requis, expire_at)
      → approval.granted  (arguments éventuellement modifiés → Replace)
      | approval.rejected (→ Deny : motif renvoyé au modèle)
      | approval.expired  (→ Deny ou Fail, configurable)
```

Réalisation (phase 4.3a) : la demande est écrite **après** le reste du lot —
une décision qui arriverait pendant qu'il tourne se ferait refuser sa reprise
par la concession —, et `drive`, en laissant le run en pause, rend cette
concession (une dernière `run.claimed` expirée à l'écriture) pour que la
reprise soit immédiate. `expire_at` fait foi : une demande dont la date est
passée est expirée à la reprise, qu'un travail différé ait tourné ou non ;
`expire_approval` ne fait que ramener le run à l'échéance, et comme il dort,
il ne retient ni `drain()` ni la fermeture. Un appel refusé n'est **jamais**
appelé : pas de `tool.called`, seulement un `tool.completed` en erreur.

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

**Réalisation (phase 4.4a) :** le port, les magasins `journal` (défaut) et `memory`, le décorateur
`@idempotent` et la règle de reprise complétée. Les clés sont **techniques** — `sha256(run_id:call_id)`,
portée par `ToolContext` ; la clé métier et le magasin partagé viennent en 4.4b, et un magasin déclaré
autrement que `journal` ou `memory` est refusé au chargement.

- `@idempotent` se pose au-dessus de `@tool` : décorer, c'est déclarer (`idempotent: true`), et c'est
  le décorateur qui tient la promesse. La réservation est rendue si l'outil lève — un outil qui échoue
  est réputé n'avoir rien produit.
- Le magasin `journal` n'a pas de stockage propre : `reserve` rend toujours vrai, le `tool.called` de
  l'appel tenant lieu de réservation et la concession (#27) garantissant un seul pilote ;
  `complete` écrit `idempotency.recorded` par l'écrivain de session, **hors de la file du lot**, pour
  couvrir l'accident entre l'effet et le `tool.completed`. Cet événement a sa propre catégorie :
  rangé dans `tool`, il deviendrait la cause d'une transition alors qu'il n'est l'effet de rien.
- Un appel repris dont la reprise n'est pas sûre n'est pas relancé : l'outil déclare `on_unknown` —
  `error` (défaut) rend l'erreur au modèle, `pause` écrit une demande d'approbation et laisse un
  humain trancher, l'accord relançant l'appel.
- Fenêtre résiduelle assumée : l'effet est mémorisé après coup, donc une interruption entre l'effet
  et son enregistrement laisse l'appel rejoué le refaire. Le magasin `journal` réduit la fenêtre à
  une écriture ; il ne la supprime pas.

**Réalisation (phase 4.4b) :** la clé **métier** et le magasin partagé.

- `@idempotent(key=…)` tire la clé des arguments, préfixée par le client : deux appels, deux runs,
  deux sessions, deux process demandent la même chose et n'en font qu'un seul effet.
- Magasin `sqlite`, sa propre base, `path` obligatoire. `reserve` tient en une seule instruction
  (`INSERT … ON CONFLICT DO UPDATE … WHERE expires_at < maintenant`) : c'est SQLite qui arbitre, et
  non un `get` suivi d'un `INSERT`, que deux workers traverseraient. C'est la date qui protège, pas
  le statut ; `get` ne rend pas un résultat hors de sa durée de vie, mais rend une réservation
  périmée, qui est la trace d'un effet d'état inconnu.
- Contrôle au chargement : un outil à clé métier exige un magasin partagé **et** durable —
  `journal` ne voit que son run, `memory` que son process.
- `on_unknown` vaut désormais pour les deux chemins : l'appel repris que le moteur refuse de
  relancer, et la réservation périmée que le magasin rend à l'outil. Une approbation accordée fait
  reprendre la réservation, donc l'appel repart une fois et une seule.

**Réalisation (phase 5.3a) :** magasin `postgres`, même instruction unique, même arbitrage par la
base. Ce qui change est la portée : deux workers sur deux machines partagent leurs clés, là où
`sqlite` demande un fichier commun. Pas de politique de lignes sur cette table — `get(key)` ne
nomme pas de client, et une politique la rendrait invisible ; le cadrage vient du préfixe de la clé
et des colonnes `tenant_id`/`session_id` (backlog #021).
- RGPD : les clés portent la session qui les a créées et partent avec elle
  (`Loom.delete_session`, `SessionDeletion.keys`). Une clé oubliée rend son effet reproductible,
  mais la trace de cet effet a disparu de toute façon.
- Un appel qui rend un effet déjà mémorisé écrit `idempotency.reused` (clé, call_id, outil), sous
  le span de l'appel : le run dit pourquoi il n'a rien fait. L'outil le signale par
  `ToolContext.on_reuse`, le moteur l'écrit — lui seul sait écrire, l'outil seul connaît sa clé.

**Serveurs MCP** (#19) :

| `scope` | Connexion | Usage |
|---|---|---|
| `shared` (défaut) | Une par process, partagée | Serveurs sans état |
| `tenant` | Une par client, avec ses identifiants | Serveurs qui détiennent des données client |
| `run` | Ouverte et fermée avec le run | Serveurs à état, sandbox |

Connexion à la première utilisation, contrôle de santé, reconnexion avec backoff et disjoncteur (3.5), fermeture après inactivité, liste d'outils en cache rafraîchie sur `tools/list_changed`. Les outils sont préfixés par leur serveur (`crm__rechercher`). La sélection des outils par run (D7) tient compte des outils propres à chaque client.

Les outils d'un serveur sont obtenus au début de chaque run (port `ToolSource`) et restent fixes jusqu'à sa fin. Un serveur injoignable est journalisé (`tool.source_unavailable`) et ses outils sont retirés du run ; avec `required: true`, le run échoue. Une connexion perdue pendant un appel n'est rejouée que pour un outil sans risque (#18). Détails de réalisation : `fonctions.md`, point 19.

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
| `tool_results: [noms]` | Tous les résultats réussis de ces outils dans le run ; sans aucun, le rôle est refusé. `scope: session` prend, à défaut, le dernier résultat connu de la session |
| `session_summary` | Dernier résumé de compaction |
| `last_turns: N` | N derniers tours de la session (un tour = un run) |
| `caller_context` | Métadonnées de l'appelant (A8) |

Le message du rôle est construit par un `input_template` explicite (`{{ args.x }}`, `{{ context.y }}`, #50) ; sans template, le rôle reçoit les blocs de contexte balisés, suivis des arguments en JSON.

**Appel d'un rôle :** un seul appel de modèle, sans historique ni outils, journalisé dans le run de l'orchestrateur entre `tool.called` et `tool.completed` (`model.responded` avec le `call_id` de l'appel, enveloppe au nom du rôle). Une erreur du modèle ou une sortie vide deviennent un résultat d'erreur. Le délai par défaut des outils ne s'applique pas ; ceux du modèle et son retry bornent l'appel.

**Arguments d'un rôle :** sauf `additionalProperties` déclaré, les arguments non prévus par `input_schema` sont refusés, comme pour un outil Python. La description que voit l'orchestrateur liste le contexte que le rôle reçoit déjà, pour qu'il ne le transmette pas en arguments.

**Références `$ref`** (#12) : `{"$ref": "result:<n>"}` désigne le n-ième appel d'outil du run ; la même référence sérialisée en chaîne est acceptée. Quand un rôle est proposé dans le run (un rôle vision masqué ne compte pas), chaque résultat montré à l'orchestrateur commence par sa référence (`[result:3]`), et son prompt système explique `$ref`.

**Rôle vision :** il déclare le contexte `attachments` et reste masqué quand le run n'a pas de pièce jointe (C4). Les images suivent le texte de son message, en références résolues à l'appel ; son modèle doit avoir la capacité `vision` (contrôlé au chargement). Un `input_template` peut citer `{{ context.attachments }}` (liste des fichiers : nom, type, taille), sans obligation.

**Outil terminal** (#13) : il n'est terminal que s'il est seul dans le tour et n'a pas échoué. Sa sortie passe par les hooks `on_output` et le réglage `stream_output`, et doit respecter le schéma de sortie de l'agent s'il en a un. En cas d'échec, le résultat revient à l'orchestrateur. Sa description reçoit automatiquement la mention « à appeler seul » ; un appel en parallèle ne déclenche pas la règle et est signalé dans les logs et par un `policy.decided` (règle du moteur `loom.terminal`, décision `continue`, statut `warning`). Dans le journal, `run.completed` référence le `tool.completed` terminal, sans duplication.

**Sous-agent** (#4) : l'outil `AgentTool` crée un `RunState` enfant, sauvegardé séparément.

- L'enfant hérite du contexte (client, utilisateur) et reçoit une part du budget du parent.
- Son historique n'entre jamais dans celui du parent : seule sa sortie finale revient, comme résultat d'outil.
- Sa consommation est ajoutée à celle du parent ; `root_run_id` permet de lire tout l'arbre.
- `depth` est limité par `max_depth`.
- Après un plantage, on reprend l'enfant au lieu de le relancer.
- Si l'enfant attend une validation, le parent passe en `WAITING_CHILD`.
- L'annulation se propage du parent vers ses enfants.
- Un enfant est éphémère par défaut, sans session propre.

Réalisation (phase 2.4) : l'orchestrateur passe un seul argument, `message` ; l'enfant écrit son run dans le journal du parent, par un écrivain de session partagé (plusieurs enfants peuvent tourner en parallèle) ; `tool.called` porte `child_run_id`, `tool.completed` la consommation de l'enfant ; `max_depth` est propre à chaque agent (défaut 1) : un sous-agent n'est proposé que si la profondeur du run appelant est inférieure au `max_depth` de son agent. `run.cancelled` arrive avec le cycle de vie des runs (J4.2). **Réalisation (phase 4.3b) :** un enfant qui se met en pause laisse son appel en suspens — pas de `tool.completed` — et son parent passe en `WAITING_CHILD`, concession rendue. Les demandes de tout l'arbre remontent dans `RunResult.pending_approvals` de la racine, et c'est sur elle qu'on approuve ; elle rejoue alors l'appel délégant, qui reprend l'enfant là où il en était.

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

**Réalisation (phase 3.2)** (détails : `fonctions.md`, point 20) :

- Le contrat (`OutputContract`) se déclare par `output` sur l'agent, un rôle, un outil Python ou un outil MCP (config du serveur ou référence dans l'agent) ; `schema_file` est lu au chargement.
- Il est appliqué par la politique fournie `loom.contract` (`loom_ia.guards`), branchée d'office en tête des politiques de l'agent : `on_output` pour la réponse finale, `after_tool` pour un rôle ou un outil. Chaque contrôle écrit un `guard.checked`, réussi ou non.
- Normalisation d'abord (sortie remplacée si elle devient conforme) ; puis réparation : l'orchestrateur pour la réponse finale (sans outils, sauf `repair.tools: allowed`), le modèle du rôle pour un rôle, à la suite de sa conversation ; un outil n'est jamais réparé. Les tentatives d'un rôle se comptent par appel.
- `on_failure` : pour la réponse finale, `fail` fait échouer le run (`guard.contract`), `unverified` la garde en marquant `run.completed`, `fallback` la remplace ; pour un rôle ou un outil, `fail` rend un résultat d'erreur à l'orchestrateur (le run continue), `unverified` garde la sortie marquée, `fallback` la remplace.
- Avec un schéma, l'objet JSON est dans `run.completed.data` (réponse finale) ou dans le `data` du `tool.completed` terminal ; `RunResult.data` et `RunResult.unverified` côté Python. Schéma natif du fournisseur : 3.5.

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

**Réalisation (phase 3.3)** (détails : `fonctions.md`, point 21) :

- Un juge se déclare par `judge:` sur l'agent (réponse finale) ou sur un rôle ; il devient la politique fournie `loom.judge.<nom>` (`JudgeGuard`), branchée d'office après `loom.contract` : `on_output` ou `after_tool`. Pas de juge sur un outil Python ou MCP.
- Déclenchement : choix de l'appelant (`judges` : `auto`, `force`, `skip`, écrit dans `run.started` et hérité par les sous-runs), puis `tenants`, `sample` (tirage `sha256(run_id:nom)`), `condition` (`module:attr`, reçoit un `JudgeInput`). Motifs d'un juge non déclenché : `caller_skip`, `filtered`, `sampled_out`, `condition_false`.
- Le juge voit ses critères, le contexte qu'il déclare (liste fixe des rôles), les arguments du rôle jugé et la sortie ; il rend son verdict par un outil imposé, `verdict` (`tool_choice: required`), contrôlé par un schéma. Une note par critère, entre 0 et 1 ; un critère bloquant sous son seuil fait refuser la sortie (réparation par l'auteur, puis `on_failure`), un critère non bloquant est seulement signalé.
- Journal : `model.responded` du juge (champ `judge`, rôle `judge:<nom>`, span propre), `judge.evaluated`, `guard.checked`, `policy.decided`. Son coût entre dans `run.completed` et `run.failed`, pas ses itérations. Une erreur du juge (modèle, verdict, délai) suit son `on_error`.
- Juge corrélé et juge bloquant échantillonné : avertissements au montage ; les erreurs en profil prod arrivent avec les profils (J5).

## 10. Modèles de langage

### 10.1 Définition d'un LLM

| Champ | Rôle |
|---|---|
| `id` | Identifiant utilisé par les rôles |
| `sdk` | `anthropic` ou `openai` : choisit l'adaptateur (#9) |
| `api` | Pour `sdk: openai` : `chat` (défaut) ou `responses` ; refusé avec `sdk: anthropic` |
| `base_url`, `model` | Point d'accès et modèle du fournisseur ; sans `base_url`, l'adresse officielle du fournisseur du SDK, jamais une variable d'environnement (`ANTHROPIC_BASE_URL`, `OPENAI_BASE_URL`) |
| `api_key_env` | Nom de la variable qui contient la clé |
| Réglages | `max_tokens`, `params` (fusion par bloc entier, B6) |
| Capacités | Vision, outils (`tools`, vrai par défaut), thinking, JSON natif, `image_input` (`base64`, `url`, `file_id`), taille et formats d'image, `tool_result_media`, `streaming`, fenêtre de contexte |
| Tarifs | Prix d'entrée, de sortie, de cache |
| Disjoncteur | `circuit_breaker: {failures, cooldown}` (5 échecs, 60 s par défaut ; `null` le retire), §10.3 |
| Cache | `cache: {system, tools, messages, ttl}` : points de cache, pour `sdk: anthropic` seulement (B5) |

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

**Réalisation (3.5b) :**

- `api: responses` : adaptateur `openai_responses.py`, sans état chez le fournisseur (`store: false`, raisonnement chiffré renvoyé sans identifiant), testé sur réponses enregistrées.
- Cache de prompt (B5) : `cache` sur un modèle `sdk: anthropic` → `cache_control` sur le prompt système, le dernier outil et le dernier bloc de la conversation, puis sur les blocs `cache_breakpoint` (au plus quatre, jamais sur un raisonnement) ; refusé avec `sdk: openai`, dont les fournisseurs cachent seuls.
- Schéma JSON natif (B9) : `ModelRequest.output_schema` pour un appel sans outils possibles (rôle ; orchestrateur en réponse forcée, en réparation sans outils ou sans outils) ; transmis si `capabilities.native_json` : `response_format` (Chat), `text.format` (Responses), `output_config.format` (Anthropic, schéma adapté par le SDK).

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

**Réalisation (phase 3.2) :** sans réglage, `stream_output` vaut `after_guards` quand la réponse finale est contrôlée (contrat, juge, politique `on_output`, rôle terminal sous contrat ou jugé), `live` sinon. En `after_guards`, le texte d'une réponse qui appelle des outils part à la fin de cette réponse, et la réponse finale une fois le run clos ; en `live`, une réparation envoie d'abord un `StreamReset`. Un rôle terminal seul dans son lot diffuse ses morceaux en `live` (backlog #009).

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

**Réalisation (3.5a) :**

- `fallbacks` sur `main`, les rôles et les juges. `ModelChain` (`engine/fallback.py`) appelle le modèle courant de l'emplacement avec ses tentatives (`ModelCall`), puis le suivant selon l'erreur ; `context_overflow` va au premier secours dont la fenêtre déclarée est plus grande. Requête du secours : identifiant, `max_tokens` et `params` du secours (ceux de l'emplacement par-dessus), sans le raisonnement d'un autre modèle ; `StreamReset` si des morceaux étaient partis.
- Adhérence : `RunState.models`, projeté des `model.fell_back` (emplacement : `main`, nom du rôle, `judge:<nom>`). La réparation d'un rôle va au modèle qui a répondu.
- Disjoncteurs (`engine/circuit.py`) : un par modèle (`model:<id>`) et par serveur MCP (`mcp:<nom>`), partagés par les runs d'une instance `Loom`. Comptent les appels ratés après leurs tentatives (`transient`, `overloaded`, `quota_exhausted`) et les connexions MCP ratées (pas un refus pendant le backoff du serveur) ; une réussite remet à zéro. Au seuil : `circuit.opened`, cible écartée pendant `cooldown`, puis un essai qui la referme ou la rouvre. Toute la fin de la chaîne écartée : erreur `unavailable`.
- Contrôles au démarrage : chaînes déclarées et sans doublon, `tools` pour un orchestrateur qui a des outils et pour un juge, `vision` pour un rôle ou un juge qui reçoit les pièces jointes, juge Anthropic avec `params.thinking` refusé. Avertissements : secours à fenêtre plus petite, juge corrélé chaînes comprises, secours sans tarif sous budget en dollars.

### 10.4 Raisonnement et images

**Raisonnement** (#7) : le `RunState` garde un bloc `Reasoning` neutre. L'adaptateur décide quoi renvoyer selon les capacités du modèle : Anthropic exige les blocs signés pendant une boucle d'outils ; l'API Responses d'OpenAI accepte le raisonnement chiffré ; d'autres API compatibles l'ignorent ou le refusent. Après une bascule de modèle, le raisonnement d'un autre fournisseur est écarté : chaque bloc porte le modèle qui l'a produit (`model_id`, posé par le moteur), et la chaîne de secours retire celui d'un autre modèle (3.5a). Réalisé en 3.5b : l'API Chat renvoie le raisonnement de la boucle d'outils en cours à un modèle déclaré `thinking: true`, dans le champ où il est arrivé (gpt-oss, backlog #015) ; l'API Responses renvoie toujours le raisonnement chiffré. Il est retiré au stockage de la session.

**Images** (#14) : le `RunState` ne garde qu'une référence. Juste avant l'appel, le moteur la résout selon les capacités du modèle : un modèle avec `vision` reçoit les octets, lus dans le stockage d'artefacts et envoyés en base64 par l'adaptateur ; un modèle sans `vision`, ou un fichier qui n'est pas une image, donne une mention textuelle (nom, type, taille). URL signée à courte durée de vie seulement si le modèle l'accepte et que la config l'autorise (RGPD), plus tard : `image_input` doit aujourd'hui contenir `base64`. Si le format (`image_formats`) ou la taille (`max_image_bytes`) ne conviennent pas : erreur explicite avant l'appel (`model.invalid_request`), ou conversion par un hook (3.1). `request_hash` est calculé sur la requête avant résolution, et la fenêtre de contexte compte 1 600 tokens par image envoyée.

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

**Réalisation (phase 4.1a)** (détails : `fonctions.md`, point 22) : le port gagne `sessions(tenant)` (un `SessionRecord` par journal : session, dernier `seq`, dernière écriture) et `delete(tenant, session)` (suppression physique, F7 et §11.5) ; `ArtifactStore` gagne `delete(tenant, session)`. Backends réalisés : mémoire, JSONL et SQLite (extra `sqlite`, `aiosqlite` : une table `events`, clé primaire `(tenant_id, session_id, seq)`, l'événement entier en JSON, les facettes à part et filtrées par `json_extract` ; contrôle de séquence et écriture dans une transaction `BEGIN IMMEDIATE`, WAL et commits synchrones). Une écriture refusée est reprise par l'écrivain de session, qui relit la position et réécrit ; deux runs d'une même session partagent leur écrivain dans l'instance.

**Réalisation (phase 5.3a)** (détails : `fonctions.md`, point 22) : backend `postgres` (extra `postgres`, `asyncpg`). Table `loom_events`, mêmes colonnes et index qu'en SQLite, facettes en `jsonb` filtrées par contenance, index GIN. Le DSN vient de la variable que `storage.events.dsn_env` nomme, jamais de la config. Contrôle de séquence et insertion sous verrou consultatif par `(tenant, session)`, clé primaire en dernier mot (`SequenceConflict`). **Sécurité au niveau des lignes** : politique sur `tenant_id = current_setting('loom.tenant_id')` — posé par transaction —, en lecture et en écriture, `FORCE` pour qu'elle vaille aussi pour le propriétaire ; le réglage absent ne laisse rien passer. **Deux rôles** : le propriétaire pose le schéma, un rôle applicatif (`role`, `loom_app` par défaut) exécute, sans `UPDATE` sur le journal — l'immuabilité devient un privilège. Schéma créé à la première requête si le rôle le peut, sinon `loom storage sql` l'imprime pour un DBA. Un journal Postgres oblige à déclarer `storage.artifacts` : il ne donne pas de dossier.

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

**Réalisation (phase 4.1a) :** le snapshot est un événement du journal, `session.snapshot`, de la nouvelle catégorie `session` : il porte l'historique matérialisé et `up_to_seq`, la position jusqu'à laquelle il le remplace. Il est écrit à la fin d'un run racine terminé d'une session nommée, et seulement si le gain le justifie : au moins `sessions.snapshot_every` événements depuis le dernier marqueur. Sa position s'arrête avant le premier événement d'un run encore en cours. L'historique repart du dernier marqueur écrit et rejoue la suite (4.1a prenait le plus grand `up_to_seq` ; corrigé en 4.1b, §11.3) ; un lecteur qui ignore les marqueurs obtient le même historique, plus lentement. Les marqueurs sont écartés de la projection d'un run et de son arbre : ils décrivent la session, pas le run qui les écrit. La lecture du journal, elle, reste entière à chaque run : le snapshot évite le rejeu, pas l'entrée-sortie (backlog #017).

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

**Réalisation (phase 4.1b)** (détails : `fonctions.md`, point 23) : `sessions.compaction` produit un agent ordinaire, `_compaction` (sans outils, une itération, non publié, nom réservé), monté comme les autres ; le job lit le journal, choisit la coupe sur une frontière de run (`keep_last` tours gardés), rend le segment en texte et fait tourner l'agent — sans l'historique de la session, qui doublerait la requête et déborderait la coupe —, puis écrit `session.compacted` sans contrôle de séquence, suivi d'un snapshot rafraîchi qui contient déjà le résumé. Quand plusieurs marqueurs coexistent, c'est le **dernier écrit** qui fait foi, pas le plus large : sinon un snapshot antérieur mais couvrant un tour de plus masquerait le résumé. Le contrôle de fidélité est la politique fournie `loom.fidelity` (`on_output`, branchée d'office sur cet agent) : elle relève les repères du segment — références, adresses, nombres d'au moins trois chiffres — et demande une réparation s'il en manque, puis garde le résumé avec `fidelity: warning`. `ensure_fits` compacte en synchrone au démarrage d'un run au-delà de `hard_tokens` ; si la compaction échoue, `session.trimmed` retire les tours les plus anciens.

### 11.4 Artefacts

- Les pièces jointes sont validées à l'entrée, avant tout écrit : format par signature binaire (images JPEG, PNG, GIF, WebP au jalon J2), type annoncé cohérent, taille et nombre (`execution.attachments` : 5 Mio et 10 fichiers par défaut) (G1). Chacune donne un `artifact.stored`, puis le message de l'utilisateur les porte en références.
- Stockage hors journal, désigné par une URI adressée par le contenu : `artifact://<client>/<session>/<sha256>.<ext>`. Un même fichier n'est rangé qu'une fois par session, et la suppression d'une session touche un seul dossier.
- Les outils peuvent produire des artefacts, récupérables par l'appelant (G3) : `RunResult.artifacts`, `Loom.artifact(uri)`.
- En mode librairie, le stockage par défaut suit le journal : dossier `.artifacts` sous celui d'un journal JSONL, mémoire pour un journal en mémoire ; GCS en mode service.

### 11.5 RGPD

- Suppression physique par `session_id` ou `tenant_id`, artefacts compris : exception assumée à l'immuabilité du journal.
- Option : chiffrement des payloads avec une clé par client ; supprimer la clé rend son journal illisible, sauvegardes comprises (*crypto-shredding*) (#30).
- Sessions listables et exportables (F7). Les résumés de compaction sont supprimés avec la session.

**Réalisation (phase 4.1a) :** `Loom.sessions()` liste les sessions d'un client (la plus récemment écrite d'abord), `Loom.export_session()` rend tous les événements d'une session dans l'ordre du journal (les fichiers restent désignés par leur URI, `Loom.artifact()` en rend les octets), `Loom.delete_session()` supprime les fichiers puis le journal — dans cet ordre, car tant que le journal est là on sait ce qu'il reste à retirer — et rend le compte de ce qui est parti. En ligne de commande : `loom sessions list`, `loom sessions export <id> [--out]`, `loom sessions delete <id> [--yes]`. Le chiffrement par client (J5) n'est pas là.

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

**Réalisation (phase 4.1b) :** le port est livré avec son adaptateur asyncio (une tâche par travail, dédoublonnage par `key`, `drain` et fermeture bornée par `execution.shutdown_timeout`). Un seul type de tâche est traité, `compaction` ; `run`, `resume` et `expire_approval` sont déclarés et refusés tant que leur phase n'est pas là. Une tâche en échec est journalisée et ne fait jamais échouer le run qui l'a demandée. `Loom.compact(session_id)` résume à la demande, sans tenir compte du seuil ; `Loom.drain()` attend les tâches en cours.

**Réalisation (phase 4.2b) :** le job `run` est traité. `Loom.submit(agent, message)` ouvre le run — inscrit au journal **avant le retour**, donc suivable, interrogeable et annulable aussitôt — et met son pilotage en file sous la clé `run:<run_id>` ; `result(run_id)` relit ce qu'il a produit. `Loom.recover()` balaie les sessions du locataire (ou une seule, avec `session_id`), remet en file les runs racine encore actionnables et rend leurs identifiants ; il est à appeler soi-même, car une instance ne redémarre pas les runs d'un autre process à l'insu de son appelant.

La concession est appliquée : `drive` écrit `run.claimed` avant de piloter et lève `ClaimConflict` si une concession vivante appartient à un autre worker. Le `worker_id` est engendré à la construction de l'instance ; le bail (`execution.lease`, 60 s) se renouvelle par minuteur au tiers de sa durée, et non entre deux étapes — un run bloqué dans une étape plus longue que son bail est vivant, et le perdrait. Un sous-run ne prend pas de concession : il tourne dans l'étape de son parent, qui tient la sienne. Le revers est assumé : un run dont le porteur est mort attend l'expiration du bail avant d'être repris, ce qui évite de doubler un worker simplement lent.

**Réalisation (phase 4.3a) :** les travaux `resume` et `expire_approval` sont traités, par le même pilote que `run`. Un travail **différé** n'est plus considéré comme en cours : `drain()` ne l'attend pas et la fermeture l'abandonne, au lieu de retenir le process pour un réveil à venir. Un run mené par la file reçoit le même traitement qu'un run appelé en direct — snapshot d'historique, compaction mise en file, réveil d'une approbation.

**Réalisation (phase 5.3b)** (détails : `fonctions.md`, point 27) : file `rabbitmq` (extra `rabbitmq`)
et commande `loom worker`. Une instance ne fait alors que publier, les workers consomment ; le port
gagne `drain()` (une file qui publie n'a rien à attendre) et un raffinement `ServedQueue` (`serve`,
`stop`), à quoi `loom worker` s'adresse. Acquittement après exécution, donc livraison **au moins une
fois** ; délai imité par une file d'attente à durée de vie qui retombe dans la file de travail ;
`state` et `cancel` rendent `unknown` et faux. Un travail qui bute sur une concession vivante est
reposé pour l'après-bail : c'est ce qui fait qu'un run passe d'un worker mort à un vivant sans
intervention.

**Réalisation (phase 5.3c)** (détails : `fonctions.md`, point 5) : port `EventBus` (`publish`,
`notices`) et adaptateurs `postgres` (`LISTEN`/`NOTIFY`, connexion dédiée) et `redis` (pub/sub). Une
`Notice` porte le client, la session, l'étendue du lot et la **source** qui l'a écrit ; le contenu
reste au journal, que l'abonné relit — imposé par la borne de 8 000 octets d'un `NOTIFY`, et
souhaitable pour que RLS et le masquage gardent la main. Le journal notifiant publie après écriture
et suit le bus : ce qu'il relit va aux mêmes abonnés, donc SSE et les autres accès ne changent pas.
Chacun garde sa position par journal, ce qui rattrape une nouvelle perdue ; un process sans abonné
ne relit rien. Un bus en panne n'échoue pas une écriture. Magasin d'idempotence `redis` au passage
(réservation par script Lua, appartenances dans un ensemble, oubli automatique à la rétention).

### 12.2 Pause en mode librairie

- Un agent qui peut se mettre en pause (outil en `approval: always | policy`, ou politique qui peut renvoyer `Pause`) exige un `EventStore` durable (JSONL au minimum) : erreur de config, avertissement seulement en profil dev (#28).
- **Réalisation (phase 4.3a) :** le contrôle est une erreur de chargement, les profils arrivant en J5. `Loom.approve()` et `Loom.reject()` écrivent la décision et mettent un travail `resume` en file ; `run(..., approver=…)` décide dans la boucle, sans passer par `PAUSED`, et journalise quand même la demande et sa décision. Réglages sur l'agent : `approval: {expires_in, on_expiry, scope}`, `expires_in` valant 24 h par défaut — rien d'autre ne borne l'attente d'un humain, et `null` la rend explicitement illimitée.
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

**Réalisation (phase 5.2a)** (détails : `fonctions.md`, point 39) : chaque charge d'événement déclare ses `content_fields` — les champs qui portent ce qu'un utilisateur a écrit, ce qu'un modèle a répondu, ce qu'un outil a reçu et rendu —, et `redacted(event)` rend le JSON privé de ces champs, en nommant à côté ce qui est parti. Sans la portée `read_content`, les relectures REST passent par là : statuts, durées, coûts, ventilation et notes des juges restent, la correspondance part. Le **journal garde tout** : c'est un réglage d'accès, pas de stockage. Les niveaux de capture des exports et le masquage fin (e-mails, IBAN) restent à J6.

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
- **Ventilation :** par run, rôle, modèle, session et client (J3). La consommation des sous-agents remonte au parent ; la compaction est comptée comme un run système de l'agent `_compaction` (réalisation 4.1b : c'est l'agent qui la distingue dans le rapport, son appel de modèle restant le rôle `main`).
- **Plafonds :** par run, session, client et période ; action : avertir ou arrêter (J4). Le budget est une politique `before_model` : `Stop` fait passer le run en `FINALIZING`, qui produit une réponse forcée sans outils (dépassement borné à une génération).
- **Sous-agents :** chacun reçoit une part du budget de son parent.
- **Rapport :** consommation détaillée d'un run ou d'une session (J5).

**Réalisation (phase 3.4)** (détails : `fonctions.md`, point 1) :

- Tarifs par palier (`pricing.tiers`), choisis par appel selon ses tokens d'entrée.
- Ledger : projection des `model.responded` (`core/projections/ledger.py`), sans double compte des sous-agents ; ventilation par run, rôle (`main`, rôles, `judge:<nom>`), modèle et session.
- Budgets : `budgets` (racine) et `budget` (agent), fusionnés clé par clé ; `run: {max_cost, max_tokens, max_calls}`, `session: {max_cost, max_tokens}`, `on_exceed: stop | warn`. Politique fournie `loom.budget` à `before_model` : `budget.exceeded` une fois par limite, puis `Stop` avec `stop`. Le budget de session compte les runs précédents de la session (calculé dans le journal par `drive`).
- Sous-agents : `budget_share` = part de ce qui reste au parent au moment de l'appel, écrite dans le `run.started` de l'enfant ; parent épuisé : l'enfant n'est pas lancé.
- Rapport : `Loom.report(run_id | session_id=…)`, `loom report` ; depuis 3.6, dans le résultat de chaque run (`report`) et par REST (`GET /v1/sessions/{id}/report`) et MCP (`run_report`), voir §18. Modèle sans tarif sous budget en dollars : avertissement (backlog #010). Par client et par période : J5.
- Réponse forcée : consigne en dernier message de sa requête (`FINALIZE_HINT`, jamais journalisée) ; `OnOutput.finalizing` ; une réparation de la réponse forcée est une génération de plus que celle qui borne le dépassement.

**Réalisation (phase 5.1b)** (détails : `fonctions.md`, point 34) :

- Budget d'un client : `budgets.tenant` — `max_cost_per_day`, `max_tokens_per_day`, `max_cost_per_month`, `max_tokens_per_month` —, fusionné clé par clé comme les autres budgets. Fenêtres **calendaires, en UTC** : un compteur par fenêtre suffit, il se reconstruit depuis le journal, et la remise à zéro est une date.
- Contrôle **au lancement du run**, une fois, et non avant chaque appel : le run qui franchit le plafond va jusqu'au bout, le suivant est refusé (`BudgetExhausted`, avec les secondes jusqu'à la remise à zéro). Un run refusé n'écrit rien au journal, donc pas de `budget.exceeded` : `BudgetScope` reste `run | session`.
- Compteur : port `UsageCounter` et adaptateur mémoire (Postgres et Redis en 5.3). `record` **pose** ce qu'a coûté un run racine au lieu de l'ajouter, ce qui rend l'enregistrement idempotent et permet au réchauffage depuis le journal de chevaucher la vie courante sans compter deux fois.
- Quota d'un client : `quotas.runs_per_minute`, fenêtre **glissante** de 60 s, vérifiée à la façade (donc par les quatre accès). Débit d'une clé d'API : `rate_limit.per_minute`, même fenêtre, vérifié par l'accès HTTP — c'est la clé qu'il protège, lectures comprises, et non le client.
- Rapport : `Loom.consumption(tenant_id, period=…)` et `loom report --periode jour|mois`, relus dans le journal à chaque appel, donc valables même pour un client sans budget.

## 16. Sécurité

### 16.1 Clés API

- Préfixe identifiable (`lk_...`), stockées hachées, jamais dans les logs (#39).
- Rattachées à un client, avec des scopes (`run`, `read`, `read_content`, `approve`, `admin`) et la liste des agents autorisés.
- Rotation, révocation et limitation de débit par clé.
- En V2, les clés sont déclarées hachées dans la config ; `loom keys create` affiche la clé une seule fois et donne le hash (#50).
- Portées vérifiées : `run` (lancer), `read` (agents, statuts, événements, rapports) et, depuis 3.6, `admin` pour lancer un run sans ses juges (`judges: skip`).

**Réalisation (phase 5.2a) :** `expires` sur une clé — elle est reconnue puis **refusée** (401 « expirée le … »), et rien ne tombe au chargement ; `loom validate` signale une clé périmée ou qui expire sous sept jours. `read_content` est vérifiée : sans elle, une relecture rend l'enveloppe sans le contenu (§14.2). `approve` sans `read_content` fait trancher à l'aveugle, et les deux commandes le disent. Rotation et révocation restent ce qu'elles sont en V2 : deux clés déclarées en même temps, et une clé retirée de la config. `read_content` et `rate_limit` sont des notions REST — Python, la CLI et le MCP en stdio n'ont pas de clé.

### 16.2 Point d'accès MCP

- `approve` n'est jamais exposé comme outil MCP ; la validation passe par l'elicitation (un humain répond) ou par l'API REST.
- En HTTP : clé dans l'en-tête `Authorization`, validation de l'en-tête `Origin` (imposée par la spec MCP), écoute sur `localhost` seulement en local.
- En stdio : pas d'authentification, mais un profil local restreint.
- La spec MCP recommande OAuth pour les serveurs distants : il faudra vérifier que les clients visés acceptent une clé dans l'en-tête.
- Les sorties des agents sont renvoyées à un LLM tiers : risque d'injection de prompt à garder en tête.

**Réalisation (phase 5.2b)** (détails : `fonctions.md`, point 39) : le serveur MCP est monté dans l'application REST sous `<base_path>/mcp` quand `server.mcp.http` est vrai, et **il exige des clés** — le contrôle tombe au chargement. La clé est lue **à chaque requête**, avant le protocole : un 401 ordinaire si elle manque, est inconnue ou a expiré ; sinon elle donne le client et les portées, si bien qu'un **seul serveur sert tous les clients**. Lancer demande `run`, relire demande `read`, et sans `read_content` une relecture est masquée comme en REST. `Origin` absent passe, déclaré passe, inconnu donne 403 ; un `Host` étranger donne 421, la liste des hôtes étant remplie par loom avec son adresse d'écoute. `approve` n'est toujours pas un outil MCP.

### 16.3 Isolation et secrets

- Isolation logique par client sur le journal, les artefacts, les connexions MCP (`scope: tenant`), les secrets, les budgets (#34) ; RLS en Postgres ; `TenantRouter` pour une isolation physique.
- Secrets lus via `SecretProvider` ; la config ne contient que des noms de variables (`api_key_env`).
- Chiffrement au repos du journal ; clé par client en option (§11.5).

**Réalisation (phase 5.1a) :** port `SecretProvider` (`secrets(tenant_id) -> Mapping[str, str]` : la table d'un client, telle qu'elle descend aux adaptateurs) et adaptateur `EnvironmentSecrets`, qui résout la redirection `secrets` d'un client sur l'environnement. Ce qu'un client ne redirige pas y est lu tel quel ; une redirection vers une variable absente vaut **vide**, jamais le secret commun. Le client d'une requête REST vient de sa **clé d'API** et de nulle part ailleurs ; en MCP stdio, où il n'y a pas de clé, un serveur sert un client, choisi à son lancement. Chiffrement : J5.5 ; RLS : faite en 5.3a, avec le journal Postgres (§11.1).

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
    cache: {system: true, tools: true, messages: true, ttl: 5m}   # sdk: anthropic ; 5m | 1h
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
    pricing:                          # $ / M tokens
      input: 1.0
      output: 5.0
      cache_read: 0.1
      cache_write: 1.25
      tiers:                          # au-delà de `above` tokens d'entrée par appel (cache compris)
        - {above: 200000, input: 2.0, output: 10.0}   # un prix absent reprend celui de base
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
stream_output: after_guards           # live | after_guards ; défaut : after_guards si la réponse finale est contrôlée
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
    output: {...}                     # OutputSpec du résultat (jamais réparé)
  - mcp: crm
    include: [rechercher, fiche_client, envoyer_email]
    tools:
      fiche_client: {output: {...}}   # l'emporte sur mcp_servers[].tools
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
  - name: lire_photo                  # rôle vision
    description: Décrit les photos jointes par le client.
    model: HAIKU                      # capabilities.vision: true exigée
    input_schema: {...}
    context: [user_input, attachments]   # masqué quand le run n'a pas de pièce jointe
subagents:                            # outils de kind agent, un argument : message
  - agent: verifier_devis
    name: verifier                    # défaut : le nom de l'agent
    description: Vérifie la cohérence d'un devis.   # défaut : celle de l'agent
    budget_share: 0.3                 # part de ce qui reste au budget du run appelant
policies:
  - hook: loom.require_tool           # politique fournie : un premier appel d'outil imposé
  - hook: myapp.policies:plafond_montant   # nom enregistré (imports) ou module:attr
    name: plafond                     # nom dans le journal ; défaut : celui de la politique
    points: [before_tool]             # défaut : tous ceux que la politique déclare
    params: {max: 5000}
    timeout: 2                        # défaut : 5 s ; null le retire
    on_error: block                   # block (défaut) | allow
    max_attempts: 1                   # réparations (Retry) permises dans un run
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
  fallback_message: "..."                     # exigé par fallback

judge:
  model: SONNET
  fallbacks: [HAIKU]                  # secours, comme pour main et les rôles
  name: engagements                   # défaut : output (réponse finale), nom du rôle (rôle)
  context: [user_input, {tool_results: [chercher_devis]}]   # liste fixe des rôles
  when:                               # toutes les clauses présentes ; absent : toujours
    sample: 0.2                       # tirage déterministe par run
    condition: myapp.judges:montant_eleve   # (JudgeInput) -> bool
    tenants: [dupont-plomberie]
  criteria:
    - {name: engagements, rule: "...", min_score: 0.8, blocking: true}   # défauts : 0.8, true
  repair: {max_attempts: 1, tools: auto}   # auto : l'orchestrateur garde ses outils
  on_failure: fail                    # fail | unverified | fallback
  fallback_message: "..."
  llm: {max_tokens: 1024}
  timeout: 30                         # défaut : délais et retry du modèle
  on_error: block                     # block | allow : modèle en échec, verdict invalide
```

`when.profiles` arrive avec les profils (J5). Un juge ne se déclare pas sur un outil Python ou MCP.

### 17.5 Serveurs MCP

```yaml
mcp_servers:
  - name: crm                         # sans « __ » : il préfixe les outils
    transport: http                   # stdio | http
    url: https://crm.example/mcp
    headers_env: {Authorization: CRM_TOKEN}   # en-tête ← variable d'environnement
    scope: tenant                     # shared | tenant (J5.1) | run
    connect_timeout: 10               # connexion et initialisation
    idle_timeout: 300                 # shared : fermeture après inactivité ; null la garde
    circuit_breaker: {failures: 5, cooldown: 60}   # défaut ; null le retire
    tools:
      envoyer_email: {side_effects: irreversible, approval: always}
  - name: math
    transport: stdio
    command: python
    args: [serveurs/serveur_math.py]
    cwd: .                            # relatif à loom.yaml ; par défaut son dossier
    env: {NIVEAU: "2"}                # valeurs en clair
    env_from: {JETON: MATH_TOKEN}     # variable du serveur ← variable de loom-ia
    scope: run
```

Un agent peut référencer plusieurs serveurs : voir §9.5.

### 17.6 Stockages, sessions, exécution

```yaml
storage:
  events:      {backend: jsonl, path: data/events}      # memory|jsonl|sqlite|postgres|firestore (+ dsn_env)
                                                        # jsonl : dossier ; sqlite : fichier de la base
                                                        # postgres : dsn_env, et role (défaut loom_app, null pour s'en passer)
  artifacts:   {backend: local, path: data/artifacts}   # local|memory|gcs (+ bucket) ; défaut : suit le journal
  idempotency: {backend: journal}                       # journal|memory|sqlite (+ path)|postgres (+ dsn_env)
                                                        # firestore|redis plus tard
  bus:         {backend: memory}                        # memory|postgres (+ dsn_env)|redis (+ url_env)
                                                        # ne porte que des nouvelles ; le contenu reste au journal
  queue:       {backend: asyncio}                       # asyncio|rabbitmq (+ url_env)
                                                        # rabbitmq : les tâches tournent dans `loom worker`
  encryption:  {per_tenant_keys: false}
  retention:   {events_days: null}

sessions:
  snapshot_every: 50                  # événements depuis le dernier marqueur avant un snapshot
  compaction:                         # absent : la session n'est jamais résumée
    model: HAIKU
    over_tokens: 12000
    hard_tokens: 150000
    keep_last: 6
    fidelity_check: true
    system_file: null                 # surcharge du prompt interne

execution:
  shutdown_timeout: 30                # délai laissé aux tâches de fond à la fermeture
  tools: {timeout: 30, validate_arguments: true, offload_over: 50000}
  attachments: {max_bytes: 5242880, max_files: 10, types: [image/jpeg, image/png, image/gif, image/webp]}
  lease: {ttl: 60, renew_every: 20}
  shutdown_timeout: 30
```

### 17.7 Budgets et télémétrie

```yaml
budgets:                              # défauts ; un agent les surcharge par `budget`, clé par clé
  run:     {max_cost: 0.05, max_tokens: 200000, max_calls: 25}   # max_calls : orchestrateur, rôles, juges
  session: {max_cost: 1.0, max_tokens: 2000000}                  # runs précédents + run en cours
  tenant:  {max_cost_per_day: 10.0, max_tokens_per_month: 20000000}   # par client et par période (5.1b)
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
tenants:                              # sans cette section, seul `default` existe (#33)
  - id: dupont-plomberie
    agents: [relance_devis]           # ce qu'il peut lancer ; vide signifie tous
    tools_deny: []                    # outils retirés, sous le nom que voit le modèle
    models: {M3_MAIN: SONNET}         # correspondance des modèles
    budgets: {tenant: {max_cost_per_day: 5.0}}   # fenêtres calendaires, en UTC
    quotas: {runs_per_minute: 30}                # fenêtre glissante de 60 s
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
      rate_limit: {per_minute: 60}      # débit de la clé (5.1b)
      expires: 2027-01-01T00:00:00Z     # fin de validité (5.2a) ; date avec fuseau

server:
  http: {host: 127.0.0.1, port: 8000, base_path: /loom}
  mcp:
    http: true                      # monte le MCP sous <base_path>/mcp (exige des clés)
    allowed_origins: []             # Origin acceptés ; absent = client natif, il passe
    allowed_hosts: []               # en plus de l'adresse d'écoute, que loom ajoute
    file_roots: []                  # dossiers lisibles par un lien file://
```

**Réalisation (phase 5.1a) :** `tenants` et `security.api_keys[].tenant` sont débloqués ; la liste des clients est **fermée** dès qu'elle existe, et une clé dont le client n'est pas déclaré est refusée au chargement. `storage` d'un client accepte `events` et `artifacts` ; `idempotency` y est refusé en nommant 5.3 (le port n'a le client que sur `reserve`). Contrôles au démarrage ajoutés : client en double, agent ou modèle de remplacement inconnu, modèle qui se remplace lui-même, variable `{{ }}` d'un prompt non définie pour un client, clé d'API sur un client non déclaré. `rate_limit` et `expires` d'une clé attendent 5.2, `budgets`/`quotas` d'un client 5.1b.

**Réalisation (phase 5.1b) :** `budgets` et `quotas` d'un client sont débloqués, ainsi que `rate_limit` d'une clé d'API (#39) ; `expires` reste renvoyé à 5.2. `budgets` d'un client surcharge celui de la racine clé par clé et porte en plus `tenant: {max_cost_per_day, max_tokens_per_day, max_cost_per_month, max_tokens_per_month}`.

**Réalisation (phase 5.2a) :** `expires` d'une clé est débloqué — dernière clé de `LATER_API_KEY`, qui se vide. La date doit porter un fuseau ; une clé déjà expirée charge sans erreur et est refusée à l'appel.

**Réalisation (phase 5.2b) :** `server.mcp.http`, `allowed_origins` et `allowed_hosts` sont débloqués — `LATER_MCP_ACCESS` se vide. `http: true` sans `security.api_keys` est une erreur de chargement : le MCP publie des outils, il ne s'ouvre pas sans clé.

### 17.9 Profils et contrôles

- Profils `dev` et `prod` (M4), dans `profiles:`. Fusion profonde des objets, remplacement des listes, `params` et `llm` remplacés en bloc. Différences décidées : juge corrélé et juge bloquant échantillonné en avertissement (dev) ou en erreur (prod ; avertissements seulement depuis 3.3, jusqu'aux profils) ; stockage non durable pour un agent qui peut se mettre en pause toléré en dev seulement ; `judges="skip"` autorisé en dev seulement.
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
- serveur MCP référencé mais non déclaré, préfixes MCP en double ;
- client déclaré deux fois, agent ou modèle de remplacement d'un client inconnu, modèle qui se remplace lui-même, clé d'API sur un client non déclaré, variable `{{ }}` d'un prompt non définie pour un client (5.1a).

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

- `run()` accepte `session_id`, `tenant`, des pièces jointes, `judges` (`auto`, `force`, `skip` ; depuis 3.3) et un `approver` optionnel ; il renvoie un `RunResult` (statut, réponse, `run_id`, approbations en attente). Depuis 3.6, le `RunResult` porte aussi `unverified`, la consommation ventilée du run et de ses sous-runs (`report`), les verdicts des juges (`verdicts`) et, pour un échec, son type (`error_type`) à part de son message (`error`) ; REST et MCP rendent ce même résultat.
- `stream()` renvoie un itérateur d'événements (durables et éphémères).
- `report(run_id)` ou `report(session_id=…)` rend la consommation d'un run (et de ses sous-runs) ou d'une session : total, par run, par rôle, par modèle (depuis 3.4).
- Les disjoncteurs des modèles et des serveurs MCP sont communs aux runs d'une instance ; `Loom(config, breakers=…)` les partage entre instances (depuis 3.5a).
- `stream()`, `follow()` et `events()` rendent l'arbre du run : ses événements et ceux de ses sous-runs, dans l'ordre du journal ; `subruns=False` s'en tient au run.
- `sessions()`, `export_session(session_id)` et `delete_session(session_id)` listent, exportent et suppriment les journaux de session (F7, depuis 4.1a) ; `session(session_id)` en rend la fiche — ses runs, et les approbations qu'il faut trancher pour que la conversation avance (4.5). L'API REST les expose toutes (18.2).
- `compact(session_id)` résume une session à la demande et `drain()` attend les tâches de fond (depuis 4.1b) ; `aclose()` les attend aussi, dans la limite d'`execution.shutdown_timeout`.
- **Consommation (5.1b) :** `consumption(tenant_id, period="day" | "month")` rend ce qu'un client a dépensé sur la fenêtre en cours, ce qu'il lui reste sur chaque plafond et la date de remise à zéro ; elle relit le journal, donc elle vaut aussi pour un client sans budget. `run()`, `stream()` et `submit()` lèvent `BudgetExhausted` (enveloppe de la période épuisée) ou `QuotaExceeded` (runs par minute) **avant** d'ouvrir le run : rien n'est écrit.
- **Client (5.1a) :** `run()`, `stream()` et `submit()` prennent `tenant=`, raccourci de `CallerContext(tenant_id=…)` ; toutes les lectures prennent `tenant_id=`. `tenants` liste les clients de l'instance, `tenant(id)` rend ce qu'un client surcharge, et `context(agent, tenant_id)` monte l'agent **pour ce client**. Un agent fermé à un client lève `AgentNotAllowed` ; un client non déclaré, `UnknownTenant`.

### 18.2 HTTP REST

Application ASGI (FastAPI, extra `loom-ia[http]`), autonome ou montée dans un projet existant :

```python
app.mount("/loom", loom.asgi_app(rest=True, mcp=True))
```

| Méthode | Route | Rôle | Portée |
|---|---|---|---|
| `GET` | `/v1/agents` | Lister les agents | `read` |
| `POST` | `/v1/agents/{name}/runs` | Lancer un run (synchrone, ou `background: true`) | `run` |
| `GET` | `/v1/runs/{id}` | Statut et résultat | `read` |
| `GET` | `/v1/runs/{id}/events` | Streaming SSE | `read` |
| `POST` | `/v1/runs/{id}/approve` · `/reject` | Validation humaine | `approve` |
| `POST` | `/v1/runs/{id}/cancel` | Annulation | `run` |
| `GET` | `/v1/sessions` | Lister les sessions | `read` |
| `GET` | `/v1/sessions/{id}` | Fiche : runs et approbations en attente | `read` |
| `GET` | `/v1/sessions/{id}/events` | Journal entier en JSONL | `read` |
| `GET` | `/v1/sessions/{id}/report` | Consommation de la session | `read` |
| `DELETE` | `/v1/sessions/{id}` | Effacement RGPD | `admin` |

OpenAPI est généré, ce qui permet de générer le client de l'interface.

- **Pièces jointes :** le lancement d'un run accepte le JSON ou `multipart/form-data` (fichiers sous `attachments`). Limites de `execution.attachments` ; envoi trop gros refusé sur son en-tête (413), pièce refusée en 422.
- **Sous-runs :** le flux SSE d'un run contient les événements de ses sous-runs (`?subruns=false` pour le run seul) ; il se ferme sur la clôture du run demandé.
- **Résultat (3.6) :** celui de l'API Python (`RunResult`, voir 18.1), en JSON. Un run échoué garde le code 201 (`status: failed`, `error_type`, `error`). Le champ `judges` du corps règle les juges du run ; `skip` demande la portée `admin`.
- **Rapport de session (3.6) :** `GET /v1/sessions/{id}/report`, la consommation de toute la session (portée `read`, droit sur chaque agent de la session).
- **Arrière-plan (4.5) :** `background: true` rend 202 et `{run_id, session_id, status}` — le run est inscrit au journal avant la réponse, donc lisible et suivable aussitôt.
- **Décisions (4.5) :** `approve` et `reject` prennent un `call_id` optionnel (sans lui, tout ce que le run attend est tranché), un motif, et pour un accord des arguments corrigés. L'approbateur inscrit au journal est **l'identifiant de la clé d'API**, qu'un `by` dans le corps remplace : une passerelle nomme ainsi l'humain qui a tranché.
- **Client (5.1a) :** il vient de la **clé d'API**, et de nulle part ailleurs — rien dans le corps d'une requête ne le change. Toute lecture est donc bornée au client de la clé : la session d'un autre est « introuvable », pas « interdite », puisqu'on n'a aucun moyen d'apprendre qu'elle existe. Un agent publié mais fermé à ce client donne 403, comme pour une clé limitée à certains agents. Sans clé déclarée, l'instance agit pour `default` : si la config nomme ses clients et pas `default`, tout est refusé, et `create_app` le dit au démarrage.
- **Contenu et expiration (5.2a) :** une clé expirée est reconnue puis refusée (401, avec sa date). Sans la portée `read_content`, les **relectures** sont masquées — `GET /runs/{id}`, le SSE d'un run, l'export JSONL d'une session, la fiche d'une session — tandis que la réponse d'un `POST …/runs` ne l'est jamais : ce qu'une clé lance, elle le reçoit. Une clé qui lance en arrière-plan et relit son résultat a donc besoin de `read_content`, et une clé qui approuve aussi, faute de voir ce qu'elle tranche.
- **Débit et budgets (5.1b) :** un client qui a épuisé son enveloppe de la période, ou dépassé ses runs par minute, reçoit **429** avec un `Retry-After` — quelques secondes pour un débit, la bascule de la fenêtre pour une journée épuisée. Le `rate_limit` d'une **clé** donne le même 429, mais il compte toutes ses requêtes, lectures comprises, et vaut avant même que la demande ne soit servie.
- **Sessions (4.5) :** la liste est refusée à une clé limitée à certains agents — elle ne dit pas de quels agents sont les runs d'une session, et la filtrer honnêtement demanderait de lire chaque journal. La fiche et l'export vérifient le droit sur chaque agent rencontré ; l'effacement, irréversible et commun à tous les agents de la session, demande `admin`.

### 18.3 Serveur MCP

- Chaque agent devient un outil MCP ; deux outils de contrôle s'ajoutent : `run_status` et `cancel`.
- Traces et sessions exposées en ressources en lecture seule (`loom://runs/{id}`).
- Transports : stdio (local, `loom mcp`) et HTTP, monté dans la même application que l'API REST.
- Limites : notifications de progression seulement, pas de flux d'événements complet.
- **Approbations (4.5) :** `approve` n'est jamais un outil MCP (16.2). Si le client déclare l'`elicitation`, chaque demande part en formulaire (accorder ou refuser, avec un motif) et la réponse est tranchée **dans la boucle** : le run ne passe pas par `PAUSED`, et le journal garde qui a décidé (`mcp:<client>`, faute d'identité plus précise sur stdio). Un formulaire ne corrige pas les arguments d'un appel : cela reste à l'API. Sinon, le run s'arrête en pause et l'outil **rend la main aussitôt, sans erreur** : le texte dit ce qui attend, le résultat structuré porte `run_id` et `pending_approvals`, un humain tranche ailleurs, et `run_status` relit le run.
- **Pièces jointes :** argument `attachments` de l'outil d'un agent : image en base64, ou lien `artifact://` (fichier déjà rangé, même client) ou `file://` (seulement sous `server.mcp.file_roots`, vide par défaut). Le résultat structuré liste les fichiers du run.
- **Progression :** si le client fournit un `progressToken`, le déroulé du run (appels d'outils, fichiers, sous-agents et leurs appels) lui arrive en notifications de progression.
- **Résultat (3.6) :** le texte est la réponse ; le résultat structuré est celui de l'API Python (`unverified`, usage, coût, ventilation, verdicts). Une réponse non vérifiée est suivie d'un second texte qui le dit. Un run échoué est un résultat d'erreur (`isError`) dont le texte dit en clair ce qui l'a arrêté (« Échec de l'agent … : … ») ; son type est dans `error_type`. Les juges suivent leur `when` (pas de forçage par MCP).
- **Rapport (3.6) :** outil `run_report` (`run_id` ou `session_id`) : consommation d'un run et de ses sous-runs, ou d'une session, en texte et en structuré.
- **Client (5.1a) :** en stdio il n'y a pas de clé, donc rien dans le protocole ne dirait au nom de qui une requête arrive : **un serveur sert un client**, choisi à son lancement (`loom mcp --tenant`, `create_server(loom, tenant=…)`). Tout ce qu'il publie, lance et relit porte ce client-là.
- **HTTP (5.2b) :** monté sous `<base_path>/mcp` dans l'application REST, et il exige des clés. La clé est lue **à chaque requête**, avant le protocole (401 ordinaire si elle manque, est inconnue ou a expiré), si bien qu'un **seul serveur sert tous les clients** : `list_tools` ne publie que les agents ouverts à cette clé-là, `call_tool` exige `run` pour lancer et `read` pour relire, et `run_status` est masqué sans `read_content`. `Origin` et `Host` sont validés (spec MCP) : 403 pour une origine inconnue, 421 pour un hôte étranger.

### 18.4 CLI

Dans le noyau, avec `argparse`. Commandes mentionnées dans la conception : `loom serve` (extra `http`, `--reload` en dev), `loom worker`, `loom mcp`, et les commandes de la fonction N4 : lancer un run, rejouer, inspecter une trace, valider la config. Réalisées : `validate`, `run`, `resume`, `serve`, `mcp`, `keys create`, `schema`, `report` (consommation d'un run ou d'une session, 3.4), `sessions list | export | delete` (F7, 4.1a), `approve` et `reject` (4.5) — la décision met un travail de reprise en file dans l'instance de la commande, qui la pilote et affiche la réponse ; `--no-wait` écrit et sort. Depuis 5.1a, `--tenant` dit au nom de quel client agir (`run`, `resume`, `approve`, `reject`, `mcp`), et `validate` montre les clients avec ce que chacun surcharge, puis monte les agents **par client**. Depuis 5.1b, `report --periode jour|mois` rend la consommation d'un client sur la fenêtre en cours — dépense, plafonds, reste et remise à zéro —, `validate` montre aussi le budget et le quota de chaque client, et `--tenant` s'applique enfin à `report` et aux trois `sessions`. Depuis 5.2a, `keys create` prend `--tenant`, `--expires` (date ISO ou durée : `90j`, `12h`) et `--rate-limit`, et imprime le bloc YAML complet ; `validate` montre une ligne par clé — client, portées, agents, débit, état de l'expiration — et signale une clé qui approuve sans pouvoir lire.

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
