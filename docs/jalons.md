# loom-ia V2 — Jalons

> Ordre de réalisation (#47). Les renvois pointent vers `fonctions.md` (fonctions A1…O3, points `#n`) et `conception.md` (§).

## Principes

- **Premier jalon : fondations.** Les jalons suivants sont regroupés par fonctions et découpés en phases.
- **Chaque jalon est testable et exécutable.** Il a un scénario, des commandes d'exécution, des tests automatisés et un critère de sortie.
- **Les trois accès dès J1 :** J1 livre une version minimale de l'API Python, de l'API REST et de l'outil MCP. Chaque jalon suivant expose ses nouveautés sur les trois, et un même scénario de bout en bout tourne en CI par chaque accès.
- **Tests déterministes :** l'adaptateur `sdk: fake` (modèle scripté, package `testing`) remplace les vrais modèles en CI. Des exemples avec de vrais modèles s'exécutent à la main.
- **Rejeu préparé dès J1 :** contenus complets et `request_hash` sont journalisés dès le départ, pour que les runs de J1 soient rejouables en J6.

**Critères de fin communs à tous les jalons :**

- tests unitaires et d'intégration (faux modèles, respx) ;
- scénario de bout en bout sur les trois accès ;
- pyright strict, ruff et `import-linter` au vert ;
- CI en deux passes (noyau seul, tous les extras) ;
- un exemple exécutable avec de vrais modèles, dans `examples/jN/`.

Les commandes ci-dessous sont indicatives.

## Vue d'ensemble

| Jalon | Thème | Démonstration exécutable |
|---|---|---|
| **J1** | Fondations | Un agent avec un outil Python, lancé par les trois accès, et une reprise manuelle après un plantage simulé |
| **J2** | Délégation et outils | Un orchestrateur avec des rôles, plusieurs serveurs MCP, un rôle vision et un sous-agent |
| **J3** | Fiabilité et coûts | Une relance rédigée, contrôlée par un juge, avec budget et modèle de secours |
| **J4** | Sessions et exécution durable | La relance de devis complète : conversation, approbation, `kill -9` puis reprise |
| **J5** | Service et multi-clients | Deux clients, Postgres, plusieurs workers, via docker-compose |
| **J6** | Observabilité, rejeu et qualité | Rejeu d'un run de J4, variante avec un autre modèle, traces dans OTel |

---

## J1 — Fondations

### Phases

| Phase | Contenu | Fonctions |
|---|---|---|
| 1.1 Socle | Dépôt, uv, Python 3.14, `pyproject` et extras, ruff, pyright strict, `import-linter`, CI en deux passes, en-têtes Apache, `logging` | #41–#46 |
| 1.2 Domaine et journal | Messages, blocs, `RunState`, `Usage`, enveloppe et premiers événements ; `EventStore` en mémoire et JSONL ; projections état et historique | B2, H1, #22 |
| 1.3 Moteur minimal | `apply` / `step` / `drive` ; états `READY_FOR_MODEL`, `AWAITING_TOOLS`, `FINALIZING` (max_iterations), `COMPLETED`, `FAILED` ; `step.*` et `run.transitioned` ; outils Python, exécution parallèle, validation, timeout, `ToolOutput` simple | A2–A4, D1, D2, D4, D5 |
| 1.4 Modèles | Port `stream()`, adaptateurs `anthropic` et `openai` (`chat`), classement des erreurs, retry, `sdk: fake` | B1, B3, B6–B8 |
| 1.5 Config et agents | Schéma Pydantic (sous-ensemble), YAML, `system_file`, `imports`, références Python, `AgentRegistry`, JSON Schema, premiers contrôles | A9, M1–M3, #50 |
| 1.6 Trois accès minimaux | Python `run` / `stream` ; REST : agents, lancement synchrone, statut, SSE, clé API simple ; MCP stdio : agent = outil ; CLI `serve`, `mcp`, `run`, `resume`, `validate` | A1, I1, I2, N1–N5 (partiel) |

### Test et exécution

**Scénario :** l'agent `demo` (orchestrateur et outil Python `calculer`) répond à « Combien font 12 × 7 + 3 ? ». Variante : le process est tué pendant l'appel d'outil, puis le run est repris.

| Accès | Exécution |
|---|---|
| Python | `uv run python examples/j1/run.py` (`loom.run()` puis `loom.stream()`) |
| CLI | `uv run loom run demo "Combien font 12 × 7 + 3 ?"` · `uv run loom resume <run_id>` · `uv run loom validate` |
| REST | `uv run loom serve`, puis `curl -X POST …/v1/agents/demo/runs` et `curl -N …/v1/runs/<id>/events` |
| MCP | `uv run loom mcp`, testé avec MCP Inspector ou `claude mcp add loom -- uv run loom mcp` |

**Tests automatisés :** `apply` (unitaires) ; `step` et `drive` avec le faux modèle ; adaptateurs `anthropic` et `openai` (respx) ; config valide et invalide ; scénario de bout en bout via les trois accès (client MCP en process, client HTTP de test, API Python) ; reprise après une interruption simulée entre `tool.called` et `tool.completed`.

**Critère de sortie :** même réponse et même séquence d'événements par les trois accès ; le run repris se termine sans réexécuter l'outil déjà terminé.

---

## J2 — Délégation et outils

### Phases

| Phase | Contenu | Fonctions |
|---|---|---|
| 2.1 Rôles délégués | Rôles LLM, `main` comme rôle, contexte déclaré, `input_template`, `$ref`, règle de l'outil terminal | C1–C3, C6, #12, #13 |
| 2.2 Client MCP | Déclaration des serveurs, portées `shared` et `run`, plusieurs serveurs par agent, préfixes, annotations, reconnexion | D3, #19 |
| 2.3 Artefacts et vision | Stockage local, validation des pièces jointes, références d'images résolues selon les capacités, rôle vision, déport et `artifact_read`, `ToolOutput` riche | G1–G3, C4, D6, D8, #14–#16 |
| 2.4 Sous-agents | `AgentTool`, `RunState` enfant, `root_run_id`, `max_depth`, annulation propagée | C5, #4 |
| 2.5 Accès | Pièces jointes par REST (multipart) et par MCP (image ou lien de ressource) ; arbre des sous-runs visible dans les événements | — |

### Test et exécution

**Scénario :** l'orchestrateur reformule un texte via un rôle (autre modèle), lit l'heure via un serveur MCP `time`, calcule via un serveur MCP `math`, et délègue une vérification à un sous-agent. Variante : une photo jointe, analysée par un rôle vision.

| Accès | Exécution |
|---|---|
| Python | `examples/j2/run.py` (avec et sans image) |
| CLI | `loom run assistant "…" --attach photo.jpg` |
| REST | `POST …/runs` en multipart avec l'image ; SSE qui montre les sous-runs |
| MCP | Outil `assistant` avec une image en contenu |

**Tests automatisés :** résolution des `$ref` ; contexte déclaré et `input_template` ; règle de l'outil terminal ; plusieurs serveurs MCP de test (préfixes, `include` / `exclude`, serveur indisponible, `required`) ; déport et `artifact_read` ; rôle vision masqué sans image ; arbre parent-enfant et annulation propagée ; scénario de bout en bout via les trois accès.

**Critère de sortie :** au moins deux modèles sollicités dans le run ; outils préfixés par serveur ; sous-run visible dans les événements par les trois accès.

---

## J3 — Fiabilité et coûts

### Phases

| Phase | Contenu | Fonctions |
|---|---|---|
| 3.1 Hooks | Points d'accroche, décisions typées, matrice, composition, `policy.decided`, timeouts, `on_error` | #1, #2 |
| 3.2 Contrats et réparation | Contrats, normalisation, réparation par le modèle auteur, `on_failure`, `stream_output: after_guards` | E1, E2, E4, E5, #20 |
| 3.3 Juge | Critères, `when` (échantillon, condition, filtres), `outcome: skipped`, juge corrélé, forçage | E3, E6, #21 |
| 3.4 Coûts et budgets | Ledger, tarifs, ventilation, budgets run et session en hooks → `FINALIZING`, part de budget des sous-agents, rapport | J1–J5 (hors client et période) |
| 3.5 Modèles avancés | Chaînes de secours, disjoncteur, `model.retried` / `fell_back`, contrôle des capacités, adaptateur `openai` (`responses`), raisonnement, cache | B4, B5, B9, #7, #8, #10 |
| 3.6 Accès | Indicateur `unverified` et coûts dans les réponses REST et MCP ; erreurs de guard lisibles | — |

### Test et exécution

**Scénario :** le rôle `rediger_relance` produit un JSON, contrôlé par un contrat et un juge bloquant. Variantes :

- **(a)** sortie mal formée, réparée par normalisation ;
- **(b)** montant inventé, refusé par le juge puis corrigé ;
- **(c)** budget dépassé, qui mène à une réponse forcée ; modèle principal en panne simulée, qui mène à une bascule vers le secours.

| Accès | Exécution |
|---|---|
| Python | `examples/j3/run.py --variante a\|b\|c` et rapport de coûts |
| CLI | `loom run relance "…"`, qui affiche le coût |
| REST | La réponse contient `unverified`, les coûts et la ventilation |
| MCP | Erreur lisible en cas d'échec d'un guard ; coût dans le résultat |

**Tests automatisés :** matrice des décisions par point d'accroche (y compris les refus au démarrage) ; composition des hooks et `policy.decided` ; normalisation et réparation bornée ; `when` du juge (échantillonnage déterministe, condition, filtres, `skipped`) ; juge corrélé ; budgets menant à `FINALIZING` ; chaîne de secours et disjoncteur ; adaptateur `responses` et raisonnement.

**Critère de sortie :** les trois variantes produisent le résultat attendu, de façon identique par les trois accès ; le journal explique chaque décision.

---

## J4 — Sessions et exécution durable

### Phases

| Phase | Contenu | Fonctions |
|---|---|---|
| 4.1 Sessions | `session_id`, snapshots, `expected_seq`, compaction (`_compaction`, fidélité, `ensure_fits`), export et suppression RGPD ; `EventStore` SQLite | F1–F5, F7, #23, #24 |
| 4.2 Exécution durable | `TaskQueue` asyncio, runs en arrière-plan, `recover()`, concession, annulation, timeout global | A5, A6, H2, H3, H5, #25–#27 |
| 4.3 Approbations | `side_effects` et `approval`, pause, approbation, refus, expiration, approbateur en ligne, `WAITING_CHILD` | D10, H4, #17, #28 |
| 4.4 Idempotence | `IdempotencyStore` (`journal`, `memory`, `sqlite`), `@idempotent`, clés métier, règles de reprise | D11, #18, #49 |
| 4.5 Accès | REST : `approve`, `cancel`, runs en arrière-plan, sessions. MCP : elicitation, ou pause avec `run_status`. Python : `approve()` | — |

### Test et exécution

**Scénario :** la relance de devis complète (`conception.md` §3.2) :

1. conversation sur deux runs ;
2. outil `envoyer_email` soumis à approbation ;
3. `kill -9` du process pendant le run, puis reprise ;
4. compaction d'une longue session.

| Accès | Exécution |
|---|---|
| Python | `examples/j4/run.py`, avec approbation asynchrone puis approbateur en ligne |
| CLI | `loom run relance "…" --session c-42 --background`, puis `loom resume` ou reprise automatique au redémarrage |
| REST | `POST …/runs` en arrière-plan, puis `POST …/runs/<id>/approve`, puis `GET …/sessions/c-42` |
| MCP | Elicitation si le client la prend en charge, sinon pause, `run_status`, puis approbation via REST |

**Tests automatisés :** `expected_seq` (deux runs concurrents sur une session) ; snapshots, compaction et contrôle de fidélité ; `ensure_fits` ; `recover()` et concession (deux workers simulés) ; approbation, refus et expiration ; `@idempotent` avec clés technique et métier ; reprise d'un outil non idempotent interrompu ; suppression RGPD ; `kill -9` réel en test d'intégration (sous-process).

**Critère de sortie :** après `kill -9`, le run reprend, l'e-mail n'est jamais envoyé deux fois, et l'approbation est tracée avec son auteur.

---

## J5 — Service et multi-clients

### Phases

| Phase | Contenu | Fonctions |
|---|---|---|
| 5.1 Multi-clients | Clients dans la config, surcharges, secrets, MCP `scope: tenant`, quotas, budgets par client et par période, `TenantRouter` | L1–L3, J3, J4, #33, #34 |
| 5.2 Sécurité | Clés API complètes (scopes, agents, débit, expiration, `loom keys create`), `read_content`, sécurité MCP HTTP (`Origin`, `localhost`) | N3, #39 |
| 5.3 Stockages de service | `EventStore` Postgres (avec RLS) et Firestore, bus Postgres et Redis, file RabbitMQ, `loom worker`, GCS, idempotence Postgres, Firestore et Redis | F5, H6, #5, #27 |
| 5.4 Accès complets | REST : sessions, traces, `run_summaries`, `EventQuery`, OpenAPI, reprise SSE par `Last-Event-ID`. MCP HTTP monté avec REST, ressources `loom://runs`. Déclencheurs webhook et planification | K5, N2, #32, #38 |
| 5.5 Exploitation | Profils dev/prod, chiffrement par client, rétention | M4, #30 |

### Test et exécution

**Scénario :** `docker compose up` lance Postgres, RabbitMQ, deux workers et le serveur. Deux clients utilisent le même agent avec des modèles, des budgets et des serveurs MCP différents. Un worker est tué pendant un run, et un autre le reprend.

| Accès | Exécution |
|---|---|
| Python | `examples/j5/run.py` avec `tenant=…`, sur le stockage Postgres |
| CLI | `loom worker` (x2), `loom serve`, `loom keys create` |
| REST | Clés API par client, scopes, `run_summaries`, `EventQuery`, reprise SSE par `Last-Event-ID` |
| MCP | MCP HTTP monté avec REST ; ressources `loom://runs/{id}` ; `Origin` refusé si invalide |

**Tests automatisés :** isolation (un client ne lit jamais les données d'un autre, RLS) ; scopes et débit des clés ; quotas et budgets par période ; MCP `scope: tenant` ; adaptateurs Postgres, Redis, RabbitMQ, Firestore et GCS (conteneurs de test ou émulateurs) ; reprise SSE ; sécurité MCP HTTP ; profils dev et prod.

**Critère de sortie :** le scénario docker-compose passe en CI ; aucune fuite entre clients ; reprise sur un autre worker.

---

## J6 — Observabilité, rejeu et qualité

### Phases

| Phase | Contenu | Fonctions |
|---|---|---|
| 6.1 Exports | Export OTel, niveaux de capture, masquage, échanges bruts en opt-in, `configure_logging` | K3, K4, K7, #29, #30 |
| 6.2 Rejeu | Modes identique et variante, détection de divergence, CLI `replay` et `inspect` | K6, #31 |
| 6.3 Qualité | Runner d'évals, non-régression depuis les traces, kit `testing` publié | O1–O3 |
| 6.4 Compléments | `serve --reload`, sandbox Firecracker et mémoire long terme en packages externes (entry points), démo voix via Pipecat, publication PyPI | D9, F6, I3, #48 |

### Test et exécution

**Scénario :**

1. rejouer à l'identique un run enregistré en J4, sans appel LLM ;
2. le rejouer en variante avec un autre modèle et comparer ;
3. voir ses traces dans un collecteur OTel local ;
4. lancer une suite d'évals.

| Accès | Exécution |
|---|---|
| Python | `loom.replay(run_id, mode="exact" \| "variant")` |
| CLI | `loom replay <run_id>`, `loom inspect <run_id>`, `loom eval suite.yaml` |
| REST | `GET …/traces/<run_id>` selon le scope (`read` ou `read_content`) |
| MCP | Ressources de traces en lecture seule |

**Tests automatisés :** rejeu identique sans aucun appel réseau ; détection de divergence (`request_hash`, transitions) ; outils à effet de bord jamais réexécutés en variante ; masquage dans les exports ; export OTel ; non-régression construite à partir de traces de J1 à J5.

**Critère de sortie :** un run de J4 est rejoué à l'identique ; une divergence volontaire est détectée ; les traces apparaissent dans le collecteur.
