# Glossaire des codes cités dans les sources de loom_v2

Ce glossaire donne le sens des codes que les docstrings et les commentaires de `src/loom_ia` citent entre parenthèses, et la façon de les qualifier pendant la réécriture (voir les conventions, section 2.4). Il a été généré à partir des documents de conception ; la colonne « Cité » compte les occurrences dans `src/`.

## 1. Codes ambigus

Plusieurs familles de codes se recouvrent. Pendant la réécriture, chaque occurrence est résolue d'après son contexte, puis qualifiée.

| Code | Sens possibles | Comment trancher |
|---|---|---|
| `D1` à `D14` | **Fonction** de la famille D, Outils (`fonctions.md`). **Décision** D1 à D13 du state de session (`fonctions.md`, point 51, et `docs/state-et-context.md`). **Essai** de la fonction D*n* (`docs/loom_testing.md`), écrit « test D7 du 18/10 ». | Dans un passage sur le state de session (clés, taille, ordre des écritures, message de fin de requête), c'est une décision du state : écrire « (décision D7 du state de session) ». « test D*n* du 18/10 » devient « (essai de la fonction D*n*, 18/10) ». Ailleurs, c'est une fonction : « (fonction D3) ». Si le contexte ne suffit pas (par exemple `(N3, D4)` dans `access/mcp_server/server.py`), demander à Denis. |
| `J1` à `J5` | **Fonction** de la famille J, Coûts et budgets. **Jalon** J1 à J6 (`jalons.md`). | Un passage sur les tokens, les coûts ou les budgets désigne une fonction : « (fonction J4) ». Avec une phase (`J4.1`, `J6.3b`), c'est un jalon : « (jalon J4.1) ». |
| `#n` | **Point** *n* à trancher de `fonctions.md` (1 à 52). **Entrée** `#00n` du backlog. | Sans zéros de tête, c'est un point : « (point 18) ». Avec trois chiffres (`#015`), c'est le backlog : « (backlog #015) ». |
| `M1` | **Fonction** M1, configuration déclarative (`fonctions.md`). **Règle** M1 de l'analyse moteur du state : une réponse écrite avec `loom.state` termine le run (`docs/state_analyse_moteur.md`). | Dans un passage sur `loom.state` ou la fin d'un run (`core/projections/history.py`, `engine/loop.py`), c'est la règle : « (règle M1, `docs/state_analyse_moteur.md`) ». Ailleurs, la fonction. |
| `S1` à `S8`, `E2`, `E4`, `B4` du state v2 | **Décisions** S1 à S8 du state v2 (`docs/state-et-context.md`, tableau des décisions) ; **écarts** E*n* et **blocages** B*n* relevés par son analyse (`docs/state_v2_analyse.md`). `E2` et `B4` sont aussi des fonctions (familles E et B). | Dans un passage sur le state de session (`engine/session_state.py`, `engine/executor.py`, `writers`, `loom.state`) : « (décision S1 du state v2) », « (écart E2 du state v2) », « (décision B4 du state v2) ». Ailleurs, la fonction. |

### Décisions des analyses d'impact (`D-n`, `B-n`)

Les analyses d'impact sous `docs/` numérotent leurs décisions `D-1`, `D-2`… (et `B-n` pour certaines). Une vingtaine de documents le font, avec les mêmes numéros : le code seul ne dit jamais de quel chantier il s'agit. On le résout d'après le module où il est cité, puis on nomme le chantier : « (décision D-4 des utilisateurs et rôles) ».

| Où le code est cité | Chantier le plus probable | Document |
|---|---|---|
| `access/` (`authz`, `caller`, `audit`, `api`, `http`, `mcp_server`, `cli`), `config/models.py` (sécurité, rôles, utilisateurs, `server.http.user`), `config/loader.py` | Utilisateurs et rôles d'accès | `docs/loom_role_user.md` |
| `core/tool_threads.py`, `engine/executor.py` (threads, délais, `grace`), `ToolsExecution.threads`, `adapters/mcp/server.py` (annulation d'un appel, `B-2`, `D-6`) | Délai et threads des outils | `docs/moteur-delai-thread-analyse.md` |
| `engine/executor.py` (ordre des appels d'un tour, `order: effects`) | Ordre des appels | `docs/moteur-ordre-des-appels-analyse.md` |
| `engine/questions.py`, `access/progress.py` (questions, `loom.ask`) | Attente d'une réponse, questions multiples | `docs/wait_answer.md`, `docs/multi-questions.md` |
| `tools_builtin/word`, `tools_builtin/_document` (`B-3`, `B-4`) | Outils Word et PDF | `docs/outil-word-*-analyse.md` (images, styles, modèles, refonte…) |
| `config/references.py`, `tools_builtin/__init__.py` (préfixe `loom.`, registre, `D5`) | Outils fournis par loom-ia | `docs/tools-builtin-analyse.md` |
| `tools_builtin/excel` | loom.excel | `docs/outil-excel-*.md`, `docs/excel_chart.md` |
| `tools_builtin/sql` | loom.sql | `docs/sql_builtin_tool.md` |
| `tools_builtin/local_files`, droits et approbations | Droits et approbation | `docs/rights_approval.md` |
| `adapters/firecracker/forge.py` | Fichiers de la forge | `docs/forge-fichiers-analyse.md` |

Si aucune ligne ne s'applique, ou si deux documents définissent le même code sur le même sujet, on demande à Denis. Les identifiants d'exemple (`D-2026-042`, un numéro de devis ; `C-261`, un client) ne sont pas des codes.

Les décisions sans tiret d'une analyse (`D5` dans `tools_builtin/excel/workbook.py`) se qualifient de la même façon : « (décision D5 de l'analyse loom.excel) ».

## 2. Fonctions (`docs/fonctions.md`)

| Code | Famille | Fonction | Cité |
|---|---|---|---|
| A1 | Exécution d'un run | Lancer un run à partir d'une demande (texte et pièces jointes) et obtenir une réponse finale. | 10 |
| A2 | Exécution d'un run | Boucle agentique : l'orchestrateur choisit les outils et recommence jusqu'à pouvoir répondre. | 2 |
| A3 | Exécution d'un run | Plusieurs appels d'outils en parallèle dans un même tour ; un appel à effet attend ceux qui le précèdent, et ceux qui le suivent l'attendent (2.0.20). |  |
| A4 | Exécution d'un run | Arrêts : réponse obtenue, max d'itérations, budget, outil terminal, pause pour validation humaine. | 7 |
| A5 | Exécution d'un run | `[V2]` Annuler un run en cours. | 11 |
| A6 | Exécution d'un run | `[V2]` Timeout global du run. | 8 |
| A7 | Exécution d'un run | `[V2]` Réponse finale structurée selon un schéma. | 6 |
| A8 | Exécution d'un run | `[V2]` Contexte fourni par l'appelant (client, utilisateur, métadonnées), transmis aux outils et aux traces. | 1 |
| A9 | Exécution d'un run | `[V2]` Plusieurs agents nommés hébergés par une même instance (registre d'agents). | 4 |
| B1 | Modèles | Plusieurs fournisseurs : Anthropic et OpenAI-compatible, plus `[V2]` les modèles locaux (vLLM, Ollama). | 3 |
| B2 | Modèles | `[V2]` Format de message neutre, traduit uniquement à l'appel du fournisseur. | 3 |
| B3 | Modèles | Retry avec backoff, timeout, erreurs classées en transitoires ou définitives. | 2 |
| B4 | Modèles | `[V2]` Modèle de secours si le fournisseur est en panne. | 16 |
| B5 | Modèles | Cache de prompt. | 5 |
| B6 | Modèles | Réglages par appel (max_tokens, température, thinking), fusionnés par bloc entier. | 9 |
| B7 | Modèles | Vérification de la fenêtre de contexte avant l'appel. | 5 |
| B8 | Modèles | `[V2]` Streaming des tokens. | 2 |
| B9 | Modèles | `[V2]` Capacités déclarées par modèle (vision, outils, thinking, JSON natif). | 13 |
| C1 | Rôles LLM (délégation économe en tokens) | Déclarer un rôle : nom, description, modèle, prompt système, schéma d'entrée, réglages. | 3 |
| C2 | Rôles LLM (délégation économe en tokens) | L'orchestrateur appelle un rôle comme un outil. | 3 |
| C3 | Rôles LLM (délégation économe en tokens) | Rôle terminal : sa sortie devient la réponse finale, telle quelle. | 2 |
| C4 | Rôles LLM (délégation économe en tokens) | Rôle vision : il reçoit les pièces jointes du run et reste masqué quand il n'y en a pas. | 3 |
| C5 | Rôles LLM (délégation économe en tokens) | `[V2]` Rôle-agent (sous-agent) : ses propres outils, sa boucle, son budget, avec une profondeur limitée. | 14 |
| C6 | Rôles LLM (délégation économe en tokens) | L'orchestrateur est lui-même un rôle (`main`). | 4 |
| D1 | Outils | Contrat unique d'outil : nom, description, schéma, `[V2]` capacités déclarées. | 3 |
| D2 | Outils | Outils Python, synchrones ou asynchrones. | 4 |
| D3 | Outils | Outils MCP (stdio, http) : découverte, puis `[V2]` reconnexion automatique. | 14 |
| D4 | Outils | Validation des arguments par schéma. | 19 |
| D5 | Outils | Timeout par outil, erreurs isolées. | 6 |
| D6 | Outils | Résultats volumineux : tronqués, puis `[V2]` déplacés vers le stockage d'artefacts. | 10 |
| D7 | Outils | `[V2]` Choix des outils exposés à chaque run (filtre, client, capacité). | 16 |
| D8 | Outils | `[V2]` Résultats riches : texte, JSON, images, fichiers. | 7 |
| D9 | Outils | `[V2]` Exécution de code en sandbox (Firecracker), exposée comme un outil. | 8 |
| D10 | Outils | `[V2]` Outils sensibles marqués « approbation requise ». | 7 |
| D11 | Outils | `[V2]` Idempotence des outils à effet de bord lors d'une reprise. | 1 |
| D12 | Outils | `[V2]` Sources d'outils fournies par un paquet installé (`tool_sources`, point d'entrée du groupe `loom_ia.tools`), branchées par un agent (`- source: <nom>`). | 9 |
| D13 | Outils | `[V2]` Outils fournis par loom-ia, inactifs par défaut, activés par un agent (`- python: loom.<outil>`) ; premier outil : `loom.fetch_page`, lecture d'une page web publique. | 12 |
| D14 | Outils | `[V2]` Mode d'emploi d'un outil donné par la config (`skill` ou `skill_file`, relatif à `resources_dir` et contenu dans ce dossier), ajouté à la suite de sa description, pour tout outil : Python, fourni, MCP ou de… |  |
| E1 | Fiabilité des sorties | Contrat de sortie : schéma JSON, regex, longueur, sortie non vide. | 7 |
| E2 | Fiabilité des sorties | Réparation : on rejoue en montrant au modèle sa sortie fautive et le diagnostic. | 4 |
| E3 | Fiabilité des sorties | Juge LLM : critères, seuils, bloquant ou non, échantillonnage. | 10 |
| E4 | Fiabilité des sorties | Politique d'échec : lever une erreur ou avertir. | 5 |
| E5 | Fiabilité des sorties | `[V2]` Contrats et juge applicables à n'importe quel rôle, outil, ou à la réponse finale. | 10 |
| E6 | Fiabilité des sorties | `[V2]` Alerte si le juge utilise le même modèle que celui qu'il évalue ; erreur en profil `prod`. | 8 |
| E7 | Fiabilité des sorties | `[V2]` Politiques aux points d'accroche (`before_model`, `after_model`, `before_tool`, `after_tool`, `on_output`), qui rendent une décision typée (points 1 et 2). | 2 |
| F1 | Mémoire et sessions | Sessions persistantes. | 2 |
| F2 | Mémoire et sessions | `[V2]` Historique structuré conservé tel quel, sans aplatissement, indépendant du fournisseur. |  |
| F3 | Mémoire et sessions | Compaction automatique : seuil, modèle dédié, N derniers tours conservés ; en tâche de fond après le run, ou bloquante à l'ouverture du run suivant (`blocking: true`, 2.0.9, point 23). | 2 |
| F4 | Mémoire et sessions | Nettoyage : thinking retiré, médias remplacés par des références. | 8 |
| F5 | Mémoire et sessions | Backends : mémoire vive, fichier, `[V2]` Firestore (non réalisé) ou SQL. | 5 |
| F6 | Mémoire et sessions | `[V2]` Mémoire long terme / RAG, exposée comme un outil. | 1 |
| F7 | Mémoire et sessions | `[V2]` Gestion des sessions : lister, supprimer, exporter (RGPD). | 12 |
| F8 | Mémoire et sessions | `[V2]` State de session : quelques valeurs typées et durables (clés déclarées, un JSON Schema chacune), posées par l'application à l'ouverture d'un run ou par le résultat d'un outil, relues par l'orchestrateur, les… |  |
| G1 | Pièces jointes et artefacts | Entrées validées (format reconnu à la signature binaire, taille, nombre) : images jpeg, png, gif, webp (`execution.attachments`). | 8 |
| G2 | Pièces jointes et artefacts | `[V2]` Stockage d'artefacts : fichiers hors historique, référencés par URI. | 7 |
| G3 | Pièces jointes et artefacts | `[V2]` Artefacts produits par les outils, récupérables par l'appelant (`RunResult.produced`, `Loom.session_files()` / `session_file()`, `loom sessions files --out`, `GET /v1/sessions/{id}/files/{fichier}`, ressource MCP… | 9 |
| H1 | Exécution durable `[V2]` | État du run sérialisable. | 3 |
| H2 | Exécution durable `[V2]` | Sauvegarde (checkpoint) à chaque étape. |  |
| H3 | Exécution durable `[V2]` | Reprise après plantage. | 6 |
| H4 | Exécution durable `[V2]` | Validation humaine : pause, puis approuver, modifier ou refuser, puis reprise. | 8 |
| H5 | Exécution durable `[V2]` | Runs en arrière-plan : soumettre, suivre le statut, récupérer le résultat. | 4 |
| H6 | Exécution durable `[V2]` | Déclencheurs : appel direct, file de messages, webhook, planification. | 17 |
| H7 | Exécution durable `[V2]` | `[V2]` Worker de file de tâches (`loom worker`) : runs de fond, reprises et résumés menés hors du process qui les soumet (file asyncio ou RabbitMQ) (point 27). | 3 |
| I1 | Streaming et événements `[V2]` | Flux d'événements typés pendant le run : début, tokens, appel d'outil, résultat, validation, fin. |  |
| I2 | Streaming et événements `[V2]` | Consommable en Python (itérateur async) et en HTTP (SSE). |  |
| I3 | Streaming et événements `[V2]` | Latence adaptée à la voix (premier token rapide). |  |
| I4 | Streaming et événements `[V2]` | `[V2]` Bus d'événements partagé entre process (mémoire, Postgres, Redis), resynchronisé par `seq` (point 5). | 1 |
| J1 | Coûts et budgets | Comptage des tokens : entrée, sortie, cache, raisonnement. | 20 |
| J2 | Coûts et budgets | Coût calculé à partir d'une grille tarifaire par modèle. | 3 |
| J3 | Coûts et budgets | Ventilation par run, rôle, modèle et session, plus `[V2]` par client. | 8 |
| J4 | Coûts et budgets | Plafonds par run et par session, plus `[V2]` par client et par période. | 19 |
| J5 | Coûts et budgets | Rapport de consommation. | 6 |
| K1 | Observabilité et rejeu | `[V2]` Trace hiérarchique, pensée pour l'affichage visuel : run → étape → appel modèle / outil / validation / sous-agent. | 1 |
| K2 | Observabilité et rejeu | `[V2]` Schéma d'événements stable et versionné, qui sert de contrat pour l'interface visuelle. | 2 |
| K3 | Observabilité et rejeu | `[V2]` Capture des contenus (prompts, réponses, arguments) avec un niveau de détail réglable et masquage des données sensibles. | 3 |
| K4 | Observabilité et rejeu | Exports : JSONL et DuckDB, plus `[V2]` OpenTelemetry. | 11 |
| K5 | Observabilité et rejeu | `[V2]` API de lecture des traces : liste des runs, détail d'un run. | 10 |
| K6 | Observabilité et rejeu | `[V2]` Rejeu : à l'identique à partir des réponses enregistrées, ou avec un autre modèle ou prompt pour comparer. | 6 |
| K7 | Observabilité et rejeu | Logs techniques séparés des traces. | 2 |
| L1 | Multi-clients `[V2]` | Espace isolé par client : configuration, clés, outils, sessions, budgets, traces. | 18 |
| L2 | Multi-clients `[V2]` | Secrets par client, jamais dans les logs. | 7 |
| L3 | Multi-clients `[V2]` | Quotas et limitation de débit par client. | 13 |
| M1 | Configuration | Configuration déclarative, validée au démarrage, avec des erreurs explicites. | 17 |
| M2 | Configuration | `[V2]` Construction entièrement en Python, sans fichier. | 2 |
| M3 | Configuration | Secrets via l'environnement ou un gestionnaire de secrets. | 6 |
| M4 | Configuration | `[V2]` Profils (dev, prod) et surcharges. | 9 |
| M5 | Configuration | Contrôles de cohérence : rôle inconnu, modèle absent, juge corrélé. | 8 |
| M6 | Configuration | `[V2]` Fichier d'environnement du projet lu au démarrage de la commande `loom` : `--env-file`, sinon `env_file:` de `loom.yaml`, sinon le `.env` voisin de `loom.yaml` ; `--no-env-file` n'en lit aucun ; une variable déjà… |  |
| M7 | Configuration | Variables `{{ }}` dans les prompts et les templates ; une variable inconnue est une erreur (point 50). | 1 |
| N1 | Accès | Librairie Python async et typée. | 3 |
| N2 | Accès | `[V2]` Serveur HTTP : lancer un run, streamer, reprendre, approuver, gérer les sessions, lire les traces et les coûts. | 11 |
| N3 | Accès | `[V2]` Authentification de l'API par clé client. | 2 |
| N4 | Accès | `[V2]` CLI : lancer, rejouer, inspecter une trace, valider la config. | 6 |
| N5 | Accès | `[V2]` Serveur MCP (stdio et HTTP) : chaque agent est exposé comme un outil MCP. | 6 |
| N6 | Accès | `[V2]` Rechargement du serveur en développement (`loom serve --reload`) quand un fichier de la config change, refusé en profil `prod` (point 48). |  |
| N7 | Accès | `[V2]` Utilisateurs et rôles d'accès déclarés, matrice des droits appliquée par tous les accès : qui lance, répond, arrête, approuve (auto-approbation comprise) ; chaque action signée par un utilisateur au journal. |  |
| O1 | Qualité | Runner d'évals : cas, critères, comparaison entre modèles et configurations. | 3 |
| O2 | Qualité | `[V2]` Faux modèles et faux outils fournis, pour des tests déterministes. | 8 |
| O3 | Qualité | `[V2]` Tests de non-régression construits à partir de traces enregistrées. | 3 |
| P1 | Données sensibles `[V2]` | Chiffrement du journal et des fichiers au repos, une clé par client (point 30). | 1 |
| P2 | Données sensibles `[V2]` | Rétention : effacement des sessions dormantes au-delà d'une borne déclarée (`storage.retention`, `loom retention`) (point 30). |  |
| P3 | Données sensibles `[V2]` | Isolation des clients en base : `TenantRouter`, rôle et politiques Postgres par client (`loom storage sql`) (point 34). |  |

## 3. Points de conception (`docs/fonctions.md`, « Points à trancher »)

Cités `#n` dans les sources ; à qualifier en « (point *n*) ».

| Point | Titre | Cité |
|---|---|---|
| 1 | Budget et guards : politiques en hooks, observation par abonnés | 10 |
| 2 | Décisions des hooks | 19 |
| 3 | Boucle : machine à états pilotée par événements (option B) | 7 |
| 4 | Sous-agent (C5) : même boucle, `RunState` enfant | 11 |
| 5 | Bus d'événements : en mémoire, journal comme source | 8 |
| 6 | Types du domaine : Pydantic | 1 |
| 7 | Raisonnement (thinking), quel que soit le fournisseur | 11 |
| 8 | `provider_meta` typé | 2 |
| 9 | Accès aux modèles : SDK officiels | 5 |
| 10 | Modèle de secours | 31 |
| 11 | Streaming comme primitive de base | 7 |
| 12 | Ce que reçoit un rôle | 14 |
| 13 | Outil terminal | 15 |
| 14 | Images transmises aux rôles vision | 8 |
| 15 | Format des résultats d'outils | 10 |
| 16 | Gros résultats | 12 |
| 17 | Approbation | 46 |
| 18 | Idempotence | 40 |
| 19 | Sessions MCP | 16 |
| 20 | Réparation de la réponse finale | 18 |
| 21 | Juge | 25 |
| 22 | Session = journal d'événements typés (option B) | 17 |
| 23 | Compaction : en tâche de fond après le run | 24 |
| 24 | Lien session ↔ run | 1 |
| 25 | Granularité des sauvegardes |  |
| 26 | Reprise avec des outils en cours au moment du plantage | 2 |
| 27 | Arrière-plan | 26 |
| 28 | Pause humaine en mode librairie | 9 |
| 29 | Spans : modèle maison, export OpenTelemetry | 7 |
| 30 | Capture des contenus et masquage | 10 |
| 31 | Enregistrement pour le rejeu | 10 |
| 32 | Stockage des traces pour l'interface | 5 |
| 33 | Client en mode librairie | 9 |
| 34 | Isolation des clients | 17 |
| 35 | Format de config : YAML, avec un JSON Schema pour l'éditeur | 3 |
| 36 | Pas de migration depuis V1 |  |
| 37 | Framework HTTP : FastAPI |  |
| 38 | Streaming HTTP : SSE |  |
| 39 | Authentification : clés API | 16 |
| 40 | Points d'accès : framework Python, API HTTP REST et MCP | 1 |
| 41 | Un package avec des extras |  |
| 42 | Nom du package : `loom-ia`, nouveau dépôt |  |
| 43 | Python 3.14, géré avec uv | 1 |
| 44 | Dépendances du noyau |  |
| 45 | Logs : `logging` de la stdlib | 2 |
| 46 | Licence : Apache 2.0 |  |
| 47 | Ordre de réalisation |  |
| 48 | Enregistrement des agents | 1 |
| 49 | Implémentations de `IdempotencyStore` | 24 |
| 50 | Schéma de la config | 12 |
| 51 | State de session |  |
| 52 | Utilisateurs et rôles d'accès |  |

## 4. Sections de conception (`docs/conception.md`)

Citées `§n` ; à qualifier en « (conception §n) ».

| Section | Titre | Cité |
|---|---|---|
| §1 | Présentation |  |
| §1.1 | Objet |  |
| §1.2 | Pourquoi une V2 |  |
| §1.3 | Périmètre |  |
| §1.4 | Principes directeurs |  |
| §2 | Concepts |  |
| §3 | Parcours fonctionnels |  |
| §3.1 | Un run |  |
| §3.2 | Exemple : relance de devis |  |
| §3.3 | Conversation |  |
| §3.4 | Validation humaine |  |
| §3.5 | Arrière-plan et reprise |  |
| §3.6 | Suivi et rejeu |  |
| §4 | Capacités |  |
| §5 | Points d'accès |  |
| §6 | Multi-clients | 5 |
| §7 | Architecture |  |
| §7.1 | Couches |  |
| §7.2 | Composants |  |
| §7.3 | Ports et adaptateurs | 4 |
| §8 | Modèle de domaine |  |
| §8.1 | Messages et blocs de contenu |  |
| §8.2 | RunState |  |
| §8.3 | Événements |  |
| §9 | Moteur d'exécution |  |
| §9.1 | Machine à états |  |
| §9.2 | `apply`, `step`, `drive` |  |
| §9.3 | Suivi des transitions |  |
| §9.4 | Politiques (hooks) |  |
| §9.5 | Outils | 1 |
| §9.6 | Rôles et sous-agents |  |
| §9.7 | Fiabilité des sorties |  |
| §10 | Modèles de langage |  |
| §10.1 | Définition d'un LLM |  |
| §10.2 | Streaming |  |
| §10.3 | Erreurs, retry et secours |  |
| §10.4 | Raisonnement et images |  |
| §11 | Journal, sessions et persistance |  |
| §11.1 | `EventStore` |  |
| §11.2 | Projections | 3 |
| §11.3 | Compaction | 2 |
| §11.4 | Artefacts |  |
| §11.5 | RGPD | 9 |
| §12 | Exécution durable |  |
| §12.1 | File de tâches |  |
| §12.2 | Pause en mode librairie |  |
| §13 | Bus d'événements et streaming |  |
| §14 | Observabilité et rejeu |  |
| §14.1 | Traces | 2 |
| §14.2 | Niveaux de capture | 5 |
| §14.3 | Rejeu |  |
| §14.4 | Logs |  |
| §15 | Coûts et budgets | 4 |
| §16 | Sécurité |  |
| §16.1 | Clés API |  |
| §16.2 | Point d'accès MCP |  |
| §16.3 | Isolation et secrets | 8 |
| §16.4 | Utilisateurs et rôles d'accès (2.0.17) |  |
| §17 | Configuration | 1 |
| §17.1 | Principes |  |
| §17.2 | Fichier racine `loom.yaml` |  |
| §17.3 | Modèles |  |
| §17.4 | Agents (`agents/*.yaml`) | 2 |
| §17.5 | Serveurs MCP | 1 |
| §17.6 | Stockages, sessions, exécution | 1 |
| §17.7 | Budgets et télémétrie | 3 |
| §17.8 | Clients, sécurité, serveur | 2 |
| §17.9 | Profils et contrôles | 1 |
| §18 | Accès |  |
| §18.1 | API Python |  |
| §18.2 | HTTP REST |  |
| §18.3 | Serveur MCP |  |
| §18.4 | CLI |  |
| §19 | Projet |  |
| §20 | Questions ouvertes |  |

## 5. Jalons et phases (`docs/jalons.md`)

| Code | Thème | Cité |
|---|---|---|
| J1 | Fondations | 20 |
| J2 | Délégation et outils | 3 |
| J3 | Fiabilité et coûts | 8 |
| J4 | Sessions et exécution durable | 19 |
| J5 | Service et multi-clients | 6 |
| J6 | Observabilité, rejeu et qualité |  |

| Phase | Titre | Contenu | Cité |
|---|---|---|---|
| 1.1 | Socle | Dépôt, uv, Python 3.14, `pyproject` et extras, ruff, pyright strict, `import-linter`, CI en deux passes, en-têtes Apache, `logging` |  |
| 1.2 | Domaine et journal | Messages, blocs, `RunState`, `Usage`, enveloppe et premiers événements ; `EventStore` en mémoire et JSONL ; projections état et historique |  |
| 1.3 | Moteur minimal | `apply` / `step` / `drive` ; états `READY_FOR_MODEL`, `AWAITING_TOOLS`, `FINALIZING` (max_iterations), `COMPLETED`, `FAILED` ; `step.*` et `run.transitioned` ;… |  |
| 1.4 | Modèles | Port `stream()`, adaptateurs `anthropic` et `openai` (`chat`), classement des erreurs, retry, `sdk: fake` |  |
| 1.5 | Config et agents | Schéma Pydantic (sous-ensemble), YAML, `system_file`, `imports`, références Python, `AgentRegistry`, JSON Schema, premiers contrôles |  |
| 1.6 | Trois accès minimaux | Python `run` / `stream` ; REST : agents, lancement synchrone, statut, SSE, clé API simple ; MCP stdio : agent = outil ; CLI `serve`, `mcp`, `run`, `resume`,… | 1 |
| 2.1 | Rôles délégués | Rôles LLM, `main` comme rôle, contexte déclaré, `input_template`, `$ref`, règle de l'outil terminal |  |
| 2.2 | Client MCP | Déclaration des serveurs, portées `shared` et `run`, plusieurs serveurs par agent, préfixes, annotations, reconnexion |  |
| 2.3 | Artefacts et vision | Stockage local, validation des pièces jointes, références d'images résolues selon les capacités, rôle vision, déport et `artifact_read`, `ToolOutput` riche |  |
| 2.4 | Sous-agents | `AgentTool`, `RunState` enfant, `root_run_id`, `max_depth`, annulation propagée |  |
| 2.5 | Accès | Pièces jointes par REST (multipart) et par MCP (image ou lien de ressource) ; arbre des sous-runs visible dans les événements |  |
| 3.1 | Hooks | Points d'accroche, décisions typées, matrice, composition, `policy.decided`, timeouts, `on_error` | 1 |
| 3.2 | Contrats et réparation | Contrats, normalisation, réparation par le modèle auteur, `on_failure`, `stream_output: after_guards` | 1 |
| 3.3 | Juge | Critères, `when` (échantillon, condition, filtres), `outcome: skipped`, juge corrélé, forçage | 1 |
| 3.4 | Coûts et budgets | Ledger, tarifs, ventilation, budgets run et session en hooks → `FINALIZING`, part de budget des sous-agents, rapport | 1 |
| 3.5 | Modèles avancés | Chaînes de secours, disjoncteur, `model.retried` / `fell_back`, contrôle des capacités, adaptateur `openai` (`responses`), raisonnement, cache |  |
| 3.6 | Accès | Indicateur `unverified` et coûts dans les réponses REST et MCP ; erreurs de guard lisibles |  |
| 4.1a | Journal de session | `session_id`, snapshots d'historique, écrivain partagé et reprise sur conflit (`expected_seq`), lister, exporter et supprimer (RGPD) ; `EventStore` SQLite |  |
| 4.1b | Compaction | `TaskQueue` et adaptateur asyncio, agent interne `_compaction`, config `sessions.compaction`, contrôle de fidélité, `ensure_fits`, contextes `session_summary`… | 1 |
| 4.2a | Cycle de vie d'un run | `run.cancelled`, `Loom.cancel()`, délai maximal par agent (`timeout`), temps de pilotage cumulé |  |
| 4.2b | Exécution durable | Job `run` de la `TaskQueue`, `Loom.submit()`, `Loom.recover()`, concession appliquée (`run.claimed`, `execution.lease`, `ClaimConflict`), `kill -9` réel en… | 2 |
| 4.3a | Approbations | `side_effects` et `approval`, `Pause` débloquée, `PAUSED`, lot partiel, `Loom.approve()` et `reject()`, expiration lue au journal, approbateur en ligne,… | 1 |
| 4.3b | Sous-agent en pause | `WAITING_CHILD`, appel délégant laissé en suspens, demandes de l'arbre remontées à la racine, `approve()` depuis n'importe quel run, reprise par rejeu de… |  |
| 4.4a | Idempotence, clé technique | `IdempotencyStore` (`journal`, `memory`), `@idempotent`, `idempotency.recorded`, règle de reprise complétée (`on_unknown` : erreur ou pause),… |  |
| 4.4b | Clés métier | Magasin partagé `sqlite`, clé métier fournie par l'outil (`key=`), contrôle au démarrage (clé métier ⇒ magasin partagé et durable), durée de vie par outil et… | 1 |
| 4.5 | Accès | REST : `background: true`, `approve`, `reject`, `cancel`, routes de session (lister, fiche, export, effacement RGPD sous `admin`), portée `approve` et clé qui… | 1 |
| 5.1a | Clients | Clients dans la config (liste fermée), surcharges (agents, outils, modèles, approbations), secrets par client, variables des prompts, MCP `scope: tenant`,… | 3 |
| 5.1b | Consommation | Budgets par client et par période (`budgets.tenant`, fenêtres calendaires UTC, contrôle au lancement du run), port `UsageCounter` réchauffé depuis le journal,… | 13 |
| 5.2a | Clés et contenu | Expiration d'une clé (401 à l'identification), `read_content` : chaque charge déclare ses `content_fields`, les relectures sont masquées sans la portée ; `loom… | 10 |
| 5.2b | Sécurité MCP HTTP | Serveur MCP en HTTP monté avec REST (`server.mcp.http`), clé dans `Authorization` **à chaque requête** — un serveur, tous les clients —, portées et… | 7 |
| 5.3a | Journal Postgres | `EventStore` Postgres (extra `postgres`), sécurité au niveau des lignes (politique par client posée par transaction, `FORCE`), rôle applicatif sans `UPDATE`,… | 1 |
| 5.3b | File et worker | File RabbitMQ (`storage.queue`), `loom worker`, reprise d'un run sur un autre worker | 6 |
| 5.3c | Bus | Port `EventBus` (nouvelles d'écriture, pas d'événements), adaptateurs Postgres (`LISTEN/NOTIFY`) et Redis, position par abonné, idempotence Redis, SSE entre… | 6 |
| 5.3d | Google — **reportée, sans échéance** | `EventStore` et idempotence Firestore, artefacts GCS. | 1 |
| 5.4a | Lectures REST | `GET /runs` (les résumés, lus au journal et non projetés), `GET /events` (`EventQuery` en paramètres, `after`), document OpenAPI soigné — familles, résumés,… | 1 |
| 5.4b | Ressources MCP | Ressources `loom://` en lecture seule (deux index, cinq gabarits), les octets d'un fichier enfin lisibles, outil `cancel` |  |
| 5.4c | Déclencheurs | Portes déclarées (`triggers`), `POST /v1/hooks/{nom}`, gabarit de message sur la charge reçue, relivraison sans doublon ; planification confiée à la plateforme | 7 |
| 5.5a | Profils | `profile` et `profiles` : trois états (absent, dev, prod), surcharges fusionnées au chargement, provenance imprimée, `when.profiles` d'un juge | 6 |
| 5.5b | Chiffrement | `storage.encryption` : charges et fichiers scellés en AES-256-GCM, clé par client prise dans ses secrets, empreinte de clé et renouvellement, journal listable… | 5 |
| 5.5c | Rétention | `storage.retention` et `tenants[].retention` : les sessions dormantes effacées sur leur dernière écriture, sans lire leur contenu (donc même scellées sans clé)… | 5 |
| 6.1a | Exports | Spans tirés du journal (`run_spans`), export OTel en OTLP (extra `otel`) à la clôture d'un run, par le process qui l'écrit ; capture `metadata` / `content`… | 8 |
| 6.1b | Échanges bruts et logs | `model.exchanged` en opt-in (`capture.raw_exchanges`, racine et client) : chaque requête HTTP au fournisseur et sa réponse, secrets et octets de fichier… | 14 |
| 6.2a | Rejeu identique | `Loom.replay` et `loom replay` : en mémoire, sous le même `run_id`, le monde servi par le journal (modèles par empreinte, outils et sous-agents par `call_id`,… | 10 |
| 6.2b | Variante | `mode="variant"` et `loom replay --mode variant` : autre config, ou autre modèle par étape (`models`, `--model main=…`) ; toute requête que le journal connaît… | 12 |
| 6.2c | Inspect et traces | `Loom.trace`, `GET /v1/traces/{run_id}` (`read` / `read_content`), ressource MCP `loom://traces/{run_id}{?session_id}` : les spans de l'export, run et… | 6 |
| 6.3a | Évals | `Loom.evaluate` et `loom eval suite.yaml` : une suite YAML de cas (demande, contrôles déterministes, critères du juge d'éval) joués `repeat` fois pour chaque… | 5 |
| 6.3b | Non-régression | Un journal exporté rejoué à l'identique avec la config d'aujourd'hui : `Loom.replay_journal` et `loom replay --journal FICHIER` (chaque run racine fini, au nom… | 13 |
| 6.3c | Kit `testing` | `loom_ia.testing.Bench` : un agent de sa config au banc d'essai, monté à part — faux modèles par identifiant (`ScriptedModel`, `Loom(models=…)`), modèle réel… | 5 |
| 6.4 | Compléments | Coupée le 06/10 : **préalable**, l'arrêt écrit par un autre process (défaut trouvé en 6.3c : le run arrêté allait au bout ; corrigé dans l'écrivain de session,… |  |
| 6.4a | Rechargement | `loom serve --reload` : un superviseur garde la socket (partagée : une requête de la relève attend dans sa file) et surveille le dossier de la config, sauf ce… | 1 |
| 6.4b | Points d'entrée | Un paquet installé fournit une **source d'outils** par un point d'entrée du groupe `loom_ia.tools` : sa fabrique (`ToolSourceFactory`) reçoit le nom, les… | 7 |
| 6.4c | Sandbox | La source `forge` : un agent forge un outil (nom, description, schéma, code d'un module, exemples), contrôlé par l'hôte puis dont chaque exemple est exécuté… | 2 |
| 6.4d | Mémoire | La mémoire long terme (F6) : en 6.4d un serveur MCP externe ; depuis 2.0.16 l'outil fourni `loom.memory` — base SQLite, BGE-M3 et reranker sur transformers… |  |

## 6. Décisions du state de session (`docs/fonctions.md`, point 51)

Citées `D1` à `D13` dans les passages sur le state ; à qualifier en « (décision D*n* du state de session) ».

| Décision | Contenu |
|---|---|
| D1 | Aucun type d'événement nouveau (option C) : le state voyage dans `run.started.state` (ce que pose l'application) et dans la sortie de `tool.completed` (`state`, ce que pose un résultat d'outil). |
| D2 | Ce que l'application pose compte dès l'ouverture du run, quel que soit son sort. |
| D3 | Un sous-run ne reçoit pas le state et n'y écrit pas (ses écritures sont retirées sans bruit) ; un run de compaction non plus |
| D4 | Bloc `<session_state>` en fin de requête de l'orchestrateur qui déclare `main.context` (seule la consigne d'une réponse forcée vient après lui), JSON compact aux clés triées, envoyé à chaque appel, même vide (`{}`) — amendé le 14/10, la… |
| D5 | L'application pose du state par l'API Python (`run`, `stream`, `submit`) et la CLI (`loom run --state`) ; REST et MCP plus tard, sous une portée dédiée |
| D6 | Clés déclarées, chacune avec un JSON Schema contrôlé au chargement ; une clé non déclarée est refusée |
| D7 | 1 Kio par valeur, 4 Kio pour tout le state (JSON compact, octets UTF-8), réglables (`sessions.state`) |
| D8 | Écriture d'un outil refusée (clé non déclarée, schéma non tenu, taille, chemin absent du résultat, motif de schéma qui ne finit pas) : rien n'est posé pour cette clé, mais le résultat reste un succès — l'outil a pu produire son effet, et… |
| D9 | Deux outils d'un même lot écrivent la même clé : l'ordre des appels décide, pas l'ordre où ils finissent — en direct comme au rejeu |
| D10 | `null` efface la clé |
| D11 | Un juge ne reçoit pas le state dans cette version : `session_state` dans son `context` est refusé au chargement, en le disant |
| D12 | La valeur écrite par un outil se déclare dans la config (`from:` d'une clé : `path:` dans les données du résultat, ou `value:` fixe) — l'outil n'a pas à changer. |
| D13 | `required: true` refuse d'ouvrir un run tant que la clé n'a pas de valeur ; `default:` donne une valeur à la lecture, jamais écrite ; les deux s'excluent |

## 7. Backlog (`docs/backlog.md`)

| Entrée | Titre |
|---|---|
| #001 | Configurer le contenu écrit dans le journal |
| #002 | Emplacement de la règle sur les dépendances du noyau |
| #003 | Mettre à jour les mentions « jalons à revoir » |
| #004 | Retirer les mentions de respx dans les docs |
| #005 | `model.retried` écrit dès la phase 1.4 |
| #006 | `timeout` d'agent : nommer la phase qui l'apportera |
| #007 | Nom de l'exemple des trois accès |
| #008 | `policy.decided` pour un outil terminal appelé en parallèle |
| #009 | Diffusion en direct de la sortie d'un rôle terminal |
| #010 | Budget sur un agent dont un modèle n'a pas de tarif |
| #011 | Disjoncteur des serveurs MCP |
| #012 | Forcer un appel d'outil (`tool_choice: required`) |
| #013 | Pièces jointes autres que les images (PDF, audio, fichiers) |
| #014 | Images envoyées par URL signée ou `file_id` |
| #015 | gpt-oss appelé avec des outils via l'API `chat` (Together) |
| #016 | Commande qui crée un agent par questions |
| #017 | Le journal d'une session est relu en entier à chaque run |
| #018 | Le contrôle de fidélité ne regarde que dans un sens |
| #019 | Une empreinte de requête peut être commune à deux clients |
| #020 | Un second serveur MCP en HTTP dans le même process ne répond plus |
| #021 | La table d'idempotence est hors de la politique de lignes |
| #022 | Variante : `from:` sans données sur un résultat d'un autre run, déporté |
| #023 | `fetch_page` ignore le proxy sortant |

## 8. Codes cités sans définition trouvée

Aucun : chaque code cité dans `src/` a une entrée ci-dessus.
