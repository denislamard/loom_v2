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
| Python | `uv run --extra http --extra mcp python examples/j1/acces.py` (`loom.run()` puis `loom.stream()`, puis REST et MCP, journaux comparés) |
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
| Python | `examples/j2/acces.py` (avec image, par les trois accès, arbres comparés) ; sans image : `examples/j2/client_mcp.py` |
| CLI | `loom run assistant "…" --attach photo.jpg --stream` |
| REST | `POST …/runs` en multipart avec l'image ; SSE qui montre les sous-runs |
| MCP | Outil `assistant` avec une image dans `attachments` (base64 ou lien) ; notifications de progression qui montrent les sous-runs |

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
| Python | Un exemple par phase, sur la config `examples/j3/relance/` : `politiques.py` (3.1), `contrats.py` (3.2), `juge.py` (3.3), `budget.py` (3.4), `secours.py` (3.5) ; puis `acces.py` (3.6) : variantes a, b et c par les trois accès, et rapport de coûts |
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
| 4.1a Journal de session | `session_id`, snapshots d'historique, écrivain partagé et reprise sur conflit (`expected_seq`), lister, exporter et supprimer (RGPD) ; `EventStore` SQLite | F1, F2, F5, F7, #22, #24 |
| 4.1b Compaction | `TaskQueue` et adaptateur asyncio, agent interne `_compaction`, config `sessions.compaction`, contrôle de fidélité, `ensure_fits`, contextes `session_summary` et `last_turns`, portée `scope: session` de `tool_results` | F3, F4, #12, #23 |
| 4.2a Cycle de vie d'un run | `run.cancelled`, `Loom.cancel()`, délai maximal par agent (`timeout`), temps de pilotage cumulé | A5, A6, #006 |
| 4.2b Exécution durable | Job `run` de la `TaskQueue`, `Loom.submit()`, `Loom.recover()`, concession appliquée (`run.claimed`, `execution.lease`, `ClaimConflict`), `kill -9` réel en test d'intégration | H2, H3, H5, #25–#27 |
| 4.3a Approbations | `side_effects` et `approval`, `Pause` débloquée, `PAUSED`, lot partiel, `Loom.approve()` et `reject()`, expiration lue au journal, approbateur en ligne, journal durable exigé | D10, H4, #17, #28 |
| 4.3b Sous-agent en pause | `WAITING_CHILD`, appel délégant laissé en suspens, demandes de l'arbre remontées à la racine, `approve()` depuis n'importe quel run, reprise par rejeu de l'appel délégant | C5, H4, #4 |
| 4.4a Idempotence, clé technique | `IdempotencyStore` (`journal`, `memory`), `@idempotent`, `idempotency.recorded`, règle de reprise complétée (`on_unknown` : erreur ou pause), `storage.idempotency` | D11, #18, #49 |
| 4.4b Clés métier | Magasin partagé `sqlite`, clé métier fournie par l'outil (`key=`), contrôle au démarrage (clé métier ⇒ magasin partagé et durable), durée de vie par outil et préfixe par client, `on_unknown` sur une réservation périmée, `idempotency.reused` au journal, oubli des clés avec la session (RGPD) | D11, #49 |
| 4.5 Accès | REST : `background: true`, `approve`, `reject`, `cancel`, routes de session (lister, fiche, export, effacement RGPD sous `admin`), portée `approve` et clé qui signe la décision ; MCP : approbateur en ligne bâti sur l'elicitation, sinon run rendu en pause avec `pending_approvals` et relu par `run_status` ; CLI : `loom approve` et `reject` ; Python : `Loom.session()` | N1, N2, N3, N4, #17, #39, #40 |

### Test et exécution

**Scénario :** la relance de devis complète (`conception.md` §3.2) :

1. conversation sur deux runs ;
2. outil `envoyer_email` soumis à approbation ;
3. `kill -9` du process pendant le run, puis reprise ;
4. compaction d'une longue session.

| Accès | Exécution |
|---|---|
| Python | Un exemple par phase, sur la config `examples/j4/relance/` : `sessions.py` (4.1a), `compaction.py` (4.1b), `durable.py` (4.2), `approbation.py` (4.3), `idempotence.py` (4.4) ; puis `acces.py` (4.5) : le même scénario par les trois accès, avec approbation asynchrone (Python, REST) puis approbateur en ligne (elicitation MCP) |
| CLI | `loom run relance "…" --session c-42`, puis `loom approve <run_id> --session c-42` depuis un autre terminal, qui pilote la reprise et affiche la réponse |
| REST | `POST …/runs` avec `background: true`, puis `POST …/runs/<id>/approve` (portée `approve`), puis `GET …/sessions/c-42` |
| MCP | Elicitation si le client la déclare — la décision tranche dans la boucle ; sinon l'outil rend le run en pause, un humain tranche par REST ou en CLI, et `run_status` relit |

**Tests automatisés :** `expected_seq` (deux runs concurrents sur une session) ; snapshots, compaction et contrôle de fidélité ; `ensure_fits` ; `EventStore` SQLite sur la suite de contrat du port ; lister, exporter et supprimer une session ; `recover()` et concession (deux workers simulés) ; approbation, refus et expiration ; `@idempotent` avec clés technique et métier ; reprise d'un outil non idempotent interrompu ; `kill -9` réel en test d'intégration (sous-process).

**Critère de sortie :** après `kill -9`, le run reprend, l'e-mail n'est jamais envoyé deux fois, et l'approbation est tracée avec son auteur.

---

## J5 — Service et multi-clients

### Phases

| Phase | Contenu | Fonctions |
|---|---|---|
| 5.1a Clients | Clients dans la config (liste fermée), surcharges (agents, outils, modèles, approbations), secrets par client, variables des prompts, MCP `scope: tenant`, `TenantRouter` | L1, L2, #33, #34 |
| 5.1b Consommation | Budgets par client et par période (`budgets.tenant`, fenêtres calendaires UTC, contrôle au lancement du run), port `UsageCounter` réchauffé depuis le journal, quota de runs par minute (fenêtre glissante), débit d'une clé d'API, `loom report --periode` | L3, J3, J4, #39 |
| 5.2a Clés et contenu | Expiration d'une clé (401 à l'identification), `read_content` : chaque charge déclare ses `content_fields`, les relectures sont masquées sans la portée ; `loom keys create --tenant --expires --rate-limit`, état des clés dans `loom validate` | N3, #39 |
| 5.2b Sécurité MCP HTTP | Serveur MCP en HTTP monté avec REST (`server.mcp.http`), clé dans `Authorization` **à chaque requête** — un serveur, tous les clients —, portées et `read_content` comme en REST, `Origin` et `Host` validés, clés exigées | N5, #39 |
| 5.3a Journal Postgres | `EventStore` Postgres (extra `postgres`), sécurité au niveau des lignes (politique par client posée par transaction, `FORCE`), rôle applicatif sans `UPDATE`, schéma créé à la demande et `loom storage sql`, idempotence Postgres | F5, #5 |
| 5.3b File et worker | File RabbitMQ (`storage.queue`), `loom worker`, reprise d'un run sur un autre worker | H6, #27 |
| 5.3c Bus | Port `EventBus` (nouvelles d'écriture, pas d'événements), adaptateurs Postgres (`LISTEN/NOTIFY`) et Redis, position par abonné, idempotence Redis, SSE entre process | H6, #5 |
| 5.3d Google — **reportée, sans échéance** | `EventStore` et idempotence Firestore, artefacts GCS. Rien n'en dépend : Postgres tient le journal et l'idempotence, Redis le bus. Seul manque un stockage de fichiers **partagé** entre process, dont l'absence est dite au chargement (5.3c) | F5, #5 |
| 5.4a Lectures REST | `GET /runs` (les résumés, lus au journal et non projetés), `GET /events` (`EventQuery` en paramètres, `after`), document OpenAPI soigné — familles, résumés, clés déclarées. La reprise SSE par `Last-Event-ID` était déjà là depuis 1.6 | K5, N2, #32 |
| 5.4b Ressources MCP | Ressources `loom://` en lecture seule (deux index, cinq gabarits), les octets d'un fichier enfin lisibles, outil `cancel` | N5, #32 |
| 5.4c Déclencheurs | Portes déclarées (`triggers`), `POST /v1/hooks/{nom}`, gabarit de message sur la charge reçue, relivraison sans doublon ; planification confiée à la plateforme | H6 |
| 5.5a Profils | `profile` et `profiles` : trois états (absent, dev, prod), surcharges fusionnées au chargement, provenance imprimée, `when.profiles` d'un juge | M4 |
| 5.5b Chiffrement | `storage.encryption` : charges et fichiers scellés en AES-256-GCM, clé par client prise dans ses secrets, empreinte de clé et renouvellement, journal listable et supprimable sans la clé (crypto-shredding), extra `crypto` | #30 |
| 5.5c Rétention | `storage.retention` et `tenants[].retention` : les sessions dormantes effacées sur leur dernière écriture, sans lire leur contenu (donc même scellées sans clé) ; `loom retention` (essai à blanc par défaut), planification confiée à la plateforme | #30 |

### Test et exécution

**Scénario :** `docker compose up` lance Postgres, RabbitMQ, deux workers et le serveur. Deux clients utilisent le même agent avec des modèles, des budgets et des serveurs MCP différents. Un worker est tué pendant un run, et un autre le reprend.

| Accès | Exécution |
|---|---|
| Python | Un exemple par phase, sur la config `examples/j5/relance/` : `clients.py` (5.1a), `quotas.py` (5.1b), `securite.py` (5.2a), `serveur_mcp.py` (5.2b), `journal_postgres.py` (5.3a), `file_et_worker.py` (5.3b), `bus_et_sse.py` (5.3c), `lectures_rest.py` (5.4a), `ressources_mcp.py` (5.4b), `declencheurs.py` (5.4c), `profils.py` (5.5a), `chiffrement.py` (5.5b), `retention.py` (5.5c) |
| CLI | `loom worker` (x2), `loom serve`, `loom keys create`, `loom retention` |
| REST | Clés API par client, scopes, `run_summaries`, `EventQuery`, reprise SSE par `Last-Event-ID`, portes `POST /v1/hooks/{nom}` |
| MCP | MCP HTTP monté avec REST ; ressources `loom://` (deux index, cinq gabarits, les octets d'un fichier) ; outil `cancel` ; `Origin` refusé si invalide |

**Tests automatisés :** isolation (un client ne lit jamais les données d'un autre, RLS) ; liste fermée des clients et client inconnu refusé ; correspondance des modèles qui repasse les contrôles de cohérence ; secrets par client, et redirection vers une variable absente qui ne retombe pas sur le secret commun ; `TenantRouter` (un journal propre, un journal commun) ; scopes et débit des clés ; quotas et budgets par période ; MCP `scope: tenant` ; adaptateurs Postgres, Redis, RabbitMQ, Firestore et GCS (conteneurs de test ou émulateurs) ; reprise SSE ; sécurité MCP HTTP ; profils dev et prod ; sceau des contenus (charge fermée, enveloppe en clair, clé par client, sceau qui ne se déplace pas ni ne se redate, journal listable et supprimable sans sa clé) ; rétention (borne lue sur la dernière écriture, borne par client, essai à blanc, journal scellé effacé sans sa clé).

**Critère de sortie :** le scénario docker-compose passe en CI ; aucune fuite entre clients ; reprise sur un autre worker. Firestore et GCS (5.3d) n'en font pas partie : reportés, sans échéance.

**Ce qui le remplit, et ce qui s'en écarte :** le job complet de la CI porte les trois **services** en conteneurs (Postgres 16, RabbitMQ 3.12, Redis 7) et lance la suite avec `--require-services`, si bien que l'isolation par politique de lignes, la reprise d'un run sur un autre worker, le worker tué, le bus et l'idempotence partagée sont éprouvés à chaque passage — et qu'une variable absente y échoue au lieu de se sauter en silence. Le rôle du DSN n'est pas superutilisateur (il contournerait la politique de lignes) mais a `CREATEROLE`, puisque le stockage pose son rôle applicatif à la première requête. **Pas de fichier docker-compose** en revanche : ce que le scénario décrit est déjà couvert par des essais, et un montage de démonstration ne prouverait rien de plus. À écrire le jour où l'on voudra montrer loom en service.

---

## J6 — Observabilité, rejeu et qualité

### Phases

| Phase | Contenu | Fonctions |
|---|---|---|
| 6.1a Exports | Spans tirés du journal (`run_spans`), export OTel en OTLP (extra `otel`) à la clôture d'un run, par le process qui l'écrit ; capture `metadata` / `content` (racine et client), masquage par motifs (e-mail, téléphone, IBAN, motifs à soi) ; `telemetry.bus` renvoyé à `storage.bus` | K3, K4, #29, #30 |
| 6.1b Échanges bruts et logs | `model.exchanged` en opt-in (`capture.raw_exchanges`, racine et client) : chaque requête HTTP au fournisseur et sa réponse, secrets et octets de fichier retirés, corps bornés (`raw_max_bytes`), jamais exportés que par leurs métadonnées ; une ligne INFO par appel de modèle et d'outil ; `configure_logging` existe depuis J1 | K7, #30, #31 |
| 6.2a Rejeu identique | `Loom.replay` et `loom replay` : en mémoire, sous le même `run_id`, le monde servi par le journal (modèles par empreinte, outils et sous-agents par `call_id`, approbations) ; première divergence rapportée avec la partie de la requête qui a changé (`request_parts`) ; `--export` | K6, #31 |
| 6.2b Variante | `mode="variant"` et `loom replay --mode variant` : autre config, ou autre modèle par étape (`models`, `--model main=…`) ; toute requête que le journal connaît est servie, avant comme après la divergence, les autres partent pour de vrai ; outils lus au journal sous les mêmes nom et arguments, sinon doublés (`doubles`, `--double`), sous-agent relancé, outil à effets de bord **jamais** réexécuté (refusé), outil sans effets exécuté ; approbations du journal, accordées si rien ne part, refusées pour une exécution réelle ; rapport comparé (issue, réponse, appels servis et partis, sort des outils, coût et dépense, durée, juges) ; le rejeu dit sa divergence une fois, le moteur ne la crie plus | K6, #31 |
| 6.2c Inspect et traces | `Loom.trace`, `GET /v1/traces/{run_id}` (`read` / `read_content`), ressource MCP `loom://traces/{run_id}{?session_id}` : les spans de l'export, run et sous-runs, avec un en-tête (statut, usage, coût, durée) ; contenu à part, `None` sans `read_content`, sans masquage par motifs ; run inachevé compris ; droit sur chaque agent de l'arbre ; `loom inspect` (arbre lisible, réponse entière, bilan ; `--full`, `--json`) | K5, K6, N4 |
| 6.3a Évals | `Loom.evaluate` et `loom eval suite.yaml` : une suite YAML de cas (demande, contrôles déterministes, critères du juge d'éval) joués `repeat` fois pour chaque variante (autre config, autre modèle par étape) ; chaque variante montée à part (journal temporaire, ni collecteur, ni quotas, ni plafonds de période), un outil à effets de bord **jamais** exécuté (doublure, sinon refus), l'approbation accordée quand rien ne part ; juge d'éval hors du run, coût à part ; plafond `max_cost_usd` ; rapport par variante et par cas, `--json`, `--export` ; codes 0, 1, 2 | O1 |
| 6.3b Non-régression | Un journal exporté rejoué à l'identique avec la config d'aujourd'hui : `Loom.replay_journal` et `loom replay --journal FICHIER` (chaque run racine fini, au nom de son client ; les inachevés nommés) ; un cas de rejeu dans une suite d'évals (`replay: journaux/*.jsonl`, journaux lus avant le premier run, rejoués avec la config de chaque variante) ; `loom_ia.testing.assert_replays` dans un test ; un client par cas ; corpus `tests/regression/` tiré des exemples J1 à J5 | O3 |
| 6.3c Kit `testing` | `loom_ia.testing.Bench` : un agent de sa config au banc d'essai, monté à part — faux modèles par identifiant (`ScriptedModel`, `Loom(models=…)`), modèle réel non remplacé refusé sauf `real_models=True`, faux outils par nom (règle des doublures), `expect` (contrôles d'un cas d'éval, `AssertionError` qui dit ce qui tombe), `calls`, `export` ; `called` lit les arguments reçus par l'outil ; deux configs d'un même process, chacune ses modules voisins de même nom (`imports`, #50) | O2 |
| 6.4 Compléments | Coupée le 06/10 : **préalable**, l'arrêt écrit par un autre process (défaut trouvé en 6.3c : le run arrêté allait au bout ; corrigé dans l'écrivain de session, `RunMoved`) ; **6.4a** `serve --reload` ; **6.4b** points d'entrée (`loom_ia.tools`) ; **6.4c** sandbox Firecracker, sur la plateforme existante de Denis ; **6.4d** mémoire long terme, son serveur `loom-notes` branché en MCP ; **6.4e** démo voix via Pipecat ; **6.4f** publication PyPI. Les packages externes vivent dans `packages/` — jusqu'au 08/10 : la sandbox a rejoint loom-ia (`loom_ia.adapters.firecracker`), sa plateforme dans `firecracker/`, et `packages/` a disparu | D9, F6, I3, #48 |
| 6.4a Rechargement | `loom serve --reload` : un superviseur garde la socket (partagée : une requête de la relève attend dans sa file) et surveille le dossier de la config, sauf ce que loom y écrit et le bruit d'usage ; à chaque changement, un **nouveau** process charge la config et monte chaque agent pour chaque client (clients de modèle jamais appelés) pendant que l'ancien sert ; refusé, il le dit et l'ancien continue ; sinon il prend la main une fois l'ancien sourd, et l'ancien finit ses requêtes et ses runs ; refusé en profil `prod` ; `watchfiles` dans l'extra `http` | #48 |
| 6.4b Points d'entrée | Un paquet installé fournit une **source d'outils** par un point d'entrée du groupe `loom_ia.tools` : sa fabrique (`ToolSourceFactory`) reçoit le nom, les `params`, les secrets du client et le dossier de la config, et rend un `ToolSource` ; la config la déclare (`tool_sources`) et un agent la référence comme un serveur MCP (`source:`, préfixe, `include`/`exclude`, `required`, `tools`) ; rien n'est importé sans être demandé (liste lue dans les métadonnées, seul le paquet d'une source référencée importé au montage) ; absent, doublon, import raté, paramètres refusés : refusés en nommant le paquet ; ouverte et fermée à chaque run, sans disjoncteur ; `loom validate` dit sources, paquets et outils | D9, §7.3 |
| 6.4c Sandbox | La source `forge` : un agent forge un outil (nom, description, schéma, code d'un module, exemples), contrôlé par l'hôte puis dont chaque exemple est exécuté dans une microVM Firecracker par execd ; accepté, il entre au catalogue du client sur l'hôte, appelable tout de suite par `call` et comme outil à part entière au run suivant ; une VM partagée, une session execd par run, la VM démarrée au premier appel exécuté et arrêtée avec la source qui l'a démarrée ; sorties bornées, délais suivant `wall_ms`, schéma et exemples acceptés en texte JSON. Le client hôte (`Vm`, `Session`) dans `loom_ia.adapters.firecracker`, sans extra ; la plateforme (`make_vm.sh`, `jailer-run.sh`, execd) dans `firecracker/` | D9 |
| 6.4d Mémoire | La mémoire long terme (F6) est le serveur MCP de Denis, `loom-notes`, branché comme un autre : écritures en `approval: always` (arrêt avant chacune, refus qui n'écrit rien), lectures sans approbation, un serveur par client (`scope: tenant`, dossier de données lu dans ses secrets), run qui a écrit rejoué sans réécrire ; seul ajout à loom, la clé `description` des réglages par outil, qui réécrit ce que le modèle lit | F6, #19 |

### Test et exécution

**Scénario :**

1. rejouer à l'identique un run enregistré en J4, sans appel LLM ;
2. le rejouer en variante avec un autre modèle et comparer ;
3. voir ses traces dans un collecteur OTel local ;
4. lancer une suite d'évals.

| Accès | Exécution |
|---|---|
| Python | Un exemple par phase : `replay.py` (6.2a : cas `identique`, `divergence`, et `j4` qui rejoue les runs de `examples/j4/acces.py` restés dans ton journal ; 6.2b : cas `variante`, un run de la relance de J4 rejoué avec un autre orchestrateur, l'e-mail lu au journal ou refusé, jamais renvoyé ; 6.2c : cas `inspect`, la trace d'un run affichée comme `loom inspect`, puis relue par REST avec et sans `read_content` et par MCP, comparée à celle de Python) ; `traces.py` (6.1a et 6.1b, sur la config `examples/j5/relance/`, collecteur OTLP dans l'exemple, `--collecteur URL` pour le sien ; cas `otel`, `masquage`, `clients`, `bruts`, `logs`) ; `evals.py` (6.3a : cas `suite`, une suite YAML sur la relance de J4 jouée par `Loom.evaluate` — deux cas, deux variantes comparées, le juge d'éval, qui voit le devis lu par l'agent, l'envoi doublé et la boîte qui ne bouge pas ; en simulé, la variante qui oublie l'envoi est vue, et seule elle ; 6.3b : cas `regression`, la même suite enregistrée puis rejouée par son cas de rejeu — à l'identique par une instance sans clé, puis divergente dès le premier appel après une consigne ajoutée au prompt système, et `assert_replays` qui lève en nommant chaque journal ; 6.3c : cas `banc`, l'agent de la relance au banc — l'orchestrateur en `ScriptedModel` écrit dans l'exemple, dont une réponse lit dans la requête l'e-mail que le rôle a rédigé, le rôle et le juge simulés de la config, l'envoi faussé ; `expect` qui tient puis qui tombe, un modèle réel non remplacé refusé, le journal du banc rejoué) ; `points_entree.py` (6.4b, le paquet `carnet-devis` — module `carnet_devis`, point d'entrée `carnet` — installé dans un dossier temporaire : cas `source`, listé sans être importé, montré par `loom validate`, utilisé par l'agent — fabriqué une fois, ouvert et fermé à chaque run, fermé avec l'instance —, rejoué sans rappeler son outil ; `absent`, un point d'entrée qu'aucun paquet ne déclare, un paquet qui ne s'importe pas, des paramètres refusés ; `doublon`, deux paquets du même nom, refusés sans qu'aucun soit importé) ; `rechargement.py` (6.4a, sur une copie de la config `examples/j4/relance/` dans un dossier temporaire, `loom serve --reload` lancé en sous-process : cas `prompt`, une consigne ajoutée au prompt du rôle qui rédige l'e-mail (terminal : son e-mail est le texte du run), que le run suivant lui transmet — lue au journal par `GET /v1/events`, dans les échanges bruts ; `agent`, un fichier d'agent ajouté puis retiré ; `casse`, un YAML invalide, un outil introuvable, une erreur de syntaxe dans un module voisin, chaque fois refusés en disant pourquoi pendant que l'ancien sert ; `donnees`, le journal et les caches d'import écrits sans rien relancer, puis un prompt touché qui recharge ; `prod`, refusé au lancement et quand la config y passe) ; `forge.py` (6.4c, une vraie VM passée par `--vm` : cas `forger`, `total_ttc` forgé puis appelé par `forge__call` sur D-2026-042, puis appelé directement en `forge__total_ttc` au run suivant sur D-2026-043, la VM démarrée au premier appel exécuté et arrêtée avec l'instance ; `corriger`, un premier jet de `tva_par_taux` refusé sur l'écart d'un exemple, puis le jet corrigé accepté ; `rejeu`, le run qui a forgé rejoué à l'identique sans démarrer la VM ; `bornes`, une sortie de 20 000 caractères bornée et un job qui dépasse son `wall_ms`, sauté en `--reel`) ; `memoire.py` (6.4d, `loom-notes` de PyPI lancé par `uvx`, ou le binaire `loom-notes-mcp` passé par `--serveur`, ses bases dans un dossier temporaire : cas `chercher`, trois notes écrites une à une avec l'accord de l'artisan, puis une question qui appelle `search` sans approbation, les descriptions vues par le modèle étant celles de la config ; `memoriser`, une écriture arrêtée, la mémoire inchangée pendant l'attente, accordée puis présente, et une écriture que personne n'a demandée, refusée et jamais faite ; `rejeu`, la note effacée puis son run rejoué à l'identique, sans qu'elle revienne ; `clients`, `dupont` et `martin` chacun sa base, sauté en `--reel`) ; `loom.replay(run_id, mode="exact" \| "variant")`, `loom.replay_journal(fichier)`, `loom.evaluate(suite)`, `assert_replays(config, *journaux)`, `Bench(config, models=…, tools=…)` |
| CLI | `loom validate` (sources de paquets, points d'entrée installés), `loom serve --reload`, `loom replay <run_id>` (`--mode variant`, `--model ETAPE=MODELE`, `--double OUTIL=REF`, `--journal FICHIER` sans ou avec `run_id`), `loom inspect <run_id>`, `loom eval suite.yaml` (`--case`, `--variant`, `--export DOSSIER`, `--json`) |
| REST | `GET /v1/traces/{run_id}` selon le scope (`read` ou `read_content`) |
| MCP | Ressources de traces en lecture seule (`loom://traces/{run_id}{?session_id}`) |

**Tests automatisés :** rejeu identique sans aucun appel réseau (6.2a : outils, rôle, juge tiré au sort, sous-agent lu, approbation tranchée en ligne, second run de session, rien d'écrit au journal, export) ; détection de divergence (6.2a : prompt, outils, gabarit d'un rôle, partie nommée, journal antérieur, appel d'outil différent, approbation absente, même appels mais autre issue, rien de servi après la première ; transitions sans pauses) ; outils à effet de bord jamais réexécutés en variante (6.2b : lus au journal avec leur décision, doublés ou refusés, jamais exécutés ; outil sans effets exécuté ; exécution réelle jamais approuvée par le rejeu ; requête identique servie après la divergence ; sous-agent relancé ou lu ; dépense des seuls vrais appels ; variante mal dite refusée ; une ligne de log du rejeu, aucune du moteur au-dessus de DEBUG ; commande 0, 1 ou 2) ; masquage dans les exports (6.1a : `metadata` sans aucun contenu, `content` masqué, capture par client, motifs fournis et à soi, numéros métier épargnés) ; export OTel (6.1a : l'arbre du journal, un sous-agent sous son appel, un span `chat` par réponse et sa durée, un export par run à sa clôture, rien pour un run inachevé ni pour ce que rapporte le bus, un collecteur en panne qui ne fait pas échouer le run, jusqu'au réseau avec un collecteur OTLP/HTTP qui décode) ; échanges bruts (6.1b : secrets et octets de fichier retirés, coupe bornée, ordre par tentative, dernière tentative ratée avant l'erreur, un échange qui n'est pas une tentative, flux recopié en lisant, Anthropic et OpenAI derrière un faux transport, capture par client, corps jamais exportés) ; une ligne de log par appel, sans contenu ; trace à relire (6.2c : spans de l'export run par run et sous-runs, en-tête du run, aucun contenu sans le droit et contenu en clair avec, run inachevé et spans ouverts, corps bruts absents, run inconnu ; REST par portée avec le droit sur chaque agent de l'arbre et OpenAPI ; ressource MCP ; `loom inspect` : arbre, réponse entière, bilan, `--json`, run introuvable, extraits coupés en le disant, tokens cache compris qui font l'en-tête, appel refusé avant exécution dit et compté) ; évals (6.3a : suite lue et refusée quand elle n'éprouverait rien, contrôles qui disent ce qu'ils ont trouvé, outil à effets de bord doublé — arguments résolus — ou refusé et jamais exécuté, approbation accordée par l'éval, sous-agent qui tourne et outils interceptés, variantes par modèle et par config, répétitions, juge hors du run et en échec, juge qui voit les résultats des outils nommés — erreurs et absences comprises — et outil du juge inconnu refusé, rien dans le journal ni les quotas de l'instance, configuration isolée, export, plafond, outils et doublures prêtés par l'instance, suite injouable, commande 0, 1 ou 2) ; non-régression construite à partir de traces de J1 à J5 (6.3b : journal exporté rejoué hors du journal de l'instance, run par run, au nom de son client, avec la config d'aujourd'hui qui le fait diverger ; run inachevé nommé et non rejoué ; journal illisible, run introuvable, client inconnu, export de plusieurs runs refusés ; `assert_replays` à part, qui lève avec chaque écart et prête des objets enregistrés ; `loom replay --journal` 0, 1 ou 2 ; cas de rejeu : chaque run rejoué, divergence dite avec la partie et le rang, une variante qui change de modèle qui diverge, journaux lus avant le premier run, fichier illisible, run inachevé et journal d'un autre agent qui font tomber le cas, aucune clé nécessaire, cas mal dit refusé ; client par cas ; corpus `tests/regression/` : chaque cas a son journal, chaque journal se rejoue ; 6.3c : modules voisins de même nom de deux configs, chacune les siens, la première gardant ce qu'elle a pris, un module de la bibliothèque standard jamais remplacé ; `called` sur la référence résolue et sur les arguments d'une politique, la doublure qui les reçoit aussi, le détail qui montre les deux ; `Loom(models=…)` qui sert un client fourni sans le fermer ; le banc : faux modèles et faux outils, rien d'écrit à côté de la config, outil à effets de bord refusé sans faux, modèle réel refusé sauf `real_models`, `expect` qui dit ce qui tombe, banc mal monté refusé, conversation, journal du banc qui se rejoue) ; arrêt écrit par un autre process (6.4, préalable : deux instances sur un même journal JSONL, l'arrêt glissé avant la concession puis en plein pilotage — le run ne repart pas, rien n'est écrit après la clôture, le journal se relit —, un arrêt après la fin qui rend « déjà fini » ; l'écrivain qui refuse de réécrire après une clôture et réécrit pour un autre run ou pour un run qui avance) ; rechargement (6.4a : ce qui est surveillé — dossier de la config, dossiers hors de lui, données de la racine et de chaque client, bases SQLite et leurs fichiers, bruit sous le dossier et non au-dessus —, dossier de données qui contient la config refusé ; changements lus sur le disque, temporaires écartés, liste longue comptée ; trace d'un module voisin réduite à ses lignes, erreur de syntaxe sans pile, erreur de loom entière ; montage d'essai qui refuse un outil introuvable, sans clé d'API, pour chaque client ; la main qui ne passe qu'une fois l'ancien sourd, l'ancien qui cesse d'écouter puis le dit ; `--reload` en prod, sans `watchfiles`, superviseur appelé avec la config ; avec de vrais process : un changement servi par un nouveau process dès la première requête, les données et un fichier passager qui ne relancent rien, l'arrêt qui ne laisse aucun process ; une config cassée puis passée en prod refusées pendant que l'ancien sert, la correction servie ; un run en vol qui s'achève dans l'ancien process pendant que le nouveau sert ; un dossier de prompts déplacé hors de la config, suivi ; un superviseur tué sans préavis, dont les process s'arrêtent et rendent le port ; un premier lancement refusé, code 2) ; points d'entrée (6.4b : source déclarée et référencée, refus de config — source non déclarée, deux de même nom, nom d'un serveur MCP, préfixe pris, `include` avec `exclude` —, contrat par fichier ; points d'entrée listés sans import, absent qui nomme les installés, doublon qui nomme les deux sans import, import raté, cible non appelable ; au run : outils du paquet utilisés, fabrique appelée une fois avec nom, params et secrets, source ouverte et fermée à chaque run puis fermée avec l'instance, seul le paquet référencé importé, choix et déclarations de la config, outil toujours Python, paramètres refusés, fabrique qui ne rend pas de source, source indisponible journalisée — requise ou non —, pas de disjoncteur, rejeu sans rappel de l'outil, doublure d'éval, résultats lus par un rôle, secrets par client ; `loom validate` qui dit sources, paquets et outils, et rien sans eux) ; sandbox (6.4c : `Vm` contre un faux firecracker — `vm.env`, démarrage, refus, course de deux lanceurs, PID gardé par `run.sh`, crash dit avec la console, arrêt de chaque manière, VM lancée ailleurs, vsock ; `Session` contre un serveur scripté puis le vrai execd du dépôt — trames, désynchronisation, version, jobs réussis, en échec ou trop longs, plafonds, gros fichiers, `reset`, `busy`, session fermée en plein job qui libère sa place, annulation ; la source — contrôles de l'hôte, égalité JSON, catalogue, outils forgés avant le run seulement, exemples joués puis enregistrement, refus sur écart, texte JSON et son indice, une session par run, VM indisponible, plafonds, sortie bornée, délais, fabrique ; montée par loom avec le faux firecracker et le vrai execd — préfixe `forge__`, deux runs, VM démarrée puis arrêtée avec l'instance, VM qui tournait laissée en marche, rejeu sans démarrer la VM) ; mémoire (6.4d : contre le vrai `loom-notes`, celui de PyPI lancé par `uvx`, en modèles factices — lecture sans approbation et écriture arrêtée jusqu'à l'accord, écriture refusée jamais faite, run qui a écrit rejoué sans réécrire, variante où `search` part et `add_text` est refusé, deux clients chacun sa base ; `description` du serveur réécrite, celle de l'agent qui l'emporte, vide refusée, et sur une source de paquet).

**Critère de sortie :** un run de J4 est rejoué à l'identique ; une divergence volontaire est détectée ; les traces apparaissent dans le collecteur.
