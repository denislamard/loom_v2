# Journal des modifications

Les versions sont listées de la plus récente à la plus ancienne. Une ligne marquée ⚠ change un comportement visible.

## 2.0.5 - non publiée

### Exécution durable
- ⚠ **Un worker qui a perdu sa concession s'arrête au lieu de piloter le run en même temps que son successeur.** Un worker incapable de renouveler son bail (store injoignable, process gelé) pouvait se faire reprendre le run par un autre, puis continuer : ses écritures étaient rejouées derrière la concession du nouveau pilote. Les deux appelaient le modèle et lançaient des outils, un appel pouvait être soldé deux fois, et la session devenait illisible. Le pilote dépossédé n'écrit désormais plus rien sur le run, sous-runs compris : `Loom.run()`, `Loom.stream()` et `Loom.resume()` peuvent lever `ClaimConflict` en cours de run, et plus seulement au départ (un travail en file est reposé pour l'après-bail, comme avant). Si le run a été mené à son terme entre-temps, l'état du journal est rendu. Un run mis en pause dont la concession a été prise ne la rend plus : sa restitution aurait effacé celle du nouveau pilote (MOT-16).
- Un arrêt écrit par un autre process est vu par le pilote même quand l'écriture d'un autre run de la même session a avancé l'écrivain par-dessus : le pilote n'écrit plus après la clôture.
- `ClaimConflict` est défini dans `loom_ia.engine.writer` ; les imports `loom_ia.engine`, `loom_ia.access` et `loom_ia.engine.loop` restent valables.

### Modèles et client MCP
- ⚠ Un événement de flux complet, ou une réponse non streamée, au JSON invalide devient une erreur `transient` (« Réponse illisible : JSON invalide du fournisseur… ») sur les trois adaptateurs (Anthropic, OpenAI Chat, OpenAI Responses) : nouvelle tentative, modèle de secours et disjoncteur s'appliquent. Avant, `JSONDecodeError` sortait brut et le run échouait avec `error_type="JSONDecodeError"` (MOD-7).
- Client MCP : un run annulé, ou dont le délai tombe, pendant la fermeture d'une connexion perdue n'est plus ignoré ; l'appel s'arrête au lieu de se rejouer sur une connexion neuve (MOD-6).

### Évals, rejeu et kit de test
- ⚠ Un contrôle de texte vide (`contains`, `not_contains` ou `matches` avec `""`) est refusé au chargement de la suite : il passait toujours, ou tombait toujours (REJ-5).
- ⚠ `fields` et les arguments attendus de `called` se comparent en JSON strict : `1` n'est plus `true`, ni `0` `false` ; `2` vaut toujours `2.0`. Un cas qui passait sur cette confusion tombe (REJ-5).
- ⚠ `matches` cherche avec le moteur `regex` et un délai (`REGEX_TIMEOUT`, 1 s) : un motif trop coûteux fait tomber son contrôle en le disant, au lieu de geler `loom eval` et tous ses runs. Un motif valide pour `regex` mais pas pour `re` (`\p{L}`, quantificateurs possessifs) est désormais accepté (REJ-7).
- ⚠ Une doublure déclarée pour un sous-agent (`doubles`, ou un faux du `Bench`) répond à sa place, comme à la variante du rejeu : le sous-agent ne tourne plus. Elle était acceptée puis ignorée, et le sous-agent tournait pour de vrai, avec son coût (REJ-3).
- ⚠ `loom replay --mode variant` : deux échecs de causes différentes ne sont plus « la même » réponse (`same_answer` compare aussi `error_type`) (REJ-4).
- Un cas de rejeu dont le dossier de la suite contient `[`, `*` ou `?` trouve ses journaux : seul le motif du cas est interprété (REJ-6).

### Sécurité et robustesse
- ⚠ **Juges et rôles** : un texte venu d'ailleurs (résultat d'outil, page web, e-mail) ne peut plus fermer sa section ni en ouvrir une autre dans ce qu'ils lisent. Le `<` des balises de contexte (`criteria`, `user_input`, `caller_context`, `attachments`, `session_summary`, `last_turns`, `tool_result`, `arguments`, `output`) y devient `&lt;`, et les valeurs d'attributs sont échappées. Un texte qui ne les contient pas ne change pas (requête et `request_hash` identiques) ; le rejeu strict d'un journal dont un résultat contenait ces balises diverge (MOT-8).
- ⚠ **REST** : un run qu'un autre worker pilote (`ClaimConflict`) donne **409** avec `Retry-After` jusqu'à la fin de son bail, au lieu de 500 (EXP-8).
- ⚠ **`Loom.cancel()`** : si l'appelant est lui-même annulé pendant l'arrêt (un délai, un client REST qui se déconnecte), l'arrêt va au bout (`run.cancelled` est écrit), puis l'annulation de l'appelant remonte (`CancelledError`, ou `TimeoutError` sous `asyncio.timeout`) au lieu d'être avalée ; l'appelant ne reçoit alors pas le booléen. `stream` et le pilotage n'avalent plus non plus une seconde annulation (ACC-8).
- ⚠ **Idempotence Redis** : le nom de l'ensemble d'une session encode le client et la session. L'oubli RGPD du client `a` n'emporte plus les clés du client `a:b` (ni `*` celles de tous), et `(a, b:s)` ne se confond plus avec `(a:b, s)`. Inchangé pour les identifiants faits de lettres, de chiffres et de `_.-~` ; les ensembles d'identifiants à caractères spéciaux écrits avant ne sont plus retrouvés par l'oubli et expirent avec leurs clés. L'oubli est atomique par ensemble et épargne une clé reprise par une autre session (DON-3).
- ⚠ **Redis et bus** : un Redis qui ne répond plus fait échouer l'appel au bout de 5 s (idempotence, publication du bus) au lieu de le suspendre ; une publication sur le bus, quel qu'il soit, est abandonnée au bout de 5 s, l'écriture au journal ayant eu lieu. Redis Cluster n'est pas pris en charge (documenté) (DON-3).
- ⚠ **Masquage des exports (`telemetry.redaction`)** : nouveau motif fourni `secret`, appliqué en premier — clé privée PEM, clés d'API en `sk-`, `sk_`, `rk_` (OpenAI, Anthropic, Stripe, ElevenLabs…), jetons GitHub, AWS, Slack et Google, JWT, en-tête `Bearer`, identifiants d'une URL (l'hôte reste), valeur d'un `password=`, `api_key:` ou `mot de passe :`. Les motifs fournis s'appliquent désormais **toujours** : `patterns` ajoute les siens au lieu de remplacer les fournis, et ne peut plus en retirer un ; les fournis qu'il ne cite pas passent avant lui. Une config qui déclarait son propre motif `secret` est refusée au chargement (renommer le sien). `phone` reconnaît `0033` et `+33 (0)` ; `iban` est insensible à la casse. Deux clés JSON qui se masquent pareil ne s'écrasent plus (`[email]`, `[email]#2`). En capture `content`, des textes qui partaient en clair vers le collecteur partent masqués (GAR-1).
- ⚠ **CLI et logs console** : un caractère de contrôle écrit par un modèle, un outil, un serveur MCP ou un journal reçu se montre (`\x1b`) au lieu de piloter le terminal (presse-papier par OSC 52, écran effacé…). Toujours pour `loom inspect`, `loom eval`, `loom replay`, le déroulé et le bilan sur la sortie d'erreur, `loom sessions list`, les sources de `loom validate` et les logs au format console (aussi en mode service) ; pour la réponse d'un run (`loom run`, `--stream`, « [Réponse retenue] »), seulement si la sortie standard est un terminal — un tube ou un fichier reçoit le texte tel quel. `--json`, `loom sessions export` et les logs JSON ne changent pas. Nouvelle fonction `loom_ia.telemetry.logs.printable` (GAR-2).

### Rapport de consommation
- ⚠ Un run annulé a le statut `cancelled` dans le rapport (`Loom.report()`, `loom report`, `GET /v1/sessions/{id}/report`, le `report` d'un résultat, outil MCP `run_report`). Il restait sans statut, que `loom report` affichait « (en cours) ». Le rapport d'une session chargée croît avec elle : 4 000 runs en 0,1 s au lieu de 2 s, la liste des runs étant refaite à chaque fin de run (GAR-5).

### Journal
- ⚠ **`after` (REST `GET /v1/events`, `EventQuery.after`) suit l'ordre d'écriture, non l'ordre des identifiants.** L'identifiant d'un événement est posé à la création du brouillon, avant l'écriture : un écrivain lent (approbateur REST, sous-agents, autre worker) pouvait écrire après un autre un événement d'identifiant plus petit, que reprendre « après » le dernier id vu ne rendait jamais. Les résultats sont désormais rendus dans l'ordre d'écriture (mémoire, SQLite, Postgres) ; un `after` inconnu du journal (session effacée) retombe sur la comparaison des identifiants. Le journal JSONL, qui a un fichier par session, reste trié par identifiant.
- SQLite : un index `events_ingestion` est créé à l'ouverture d'un journal existant (une fois).
- ⚠ Postgres : le schéma du journal passe en **version 3** (colonne `ingested`, identité, et son index). L'ouverture migre une base existante, comme pour la version 2 ; qui applique le DDL à la main reprend la sortie de `loom storage sql`. Une écriture prend un verrou consultatif par client juste avant l'insertion (les écritures d'un même client se suivent pendant l'insertion et la validation).

### Intégration continue
- Le job `build` de `ci.yml` et celui de `publish.yml` essaient le wheel construit : environnement vierge (`uv venv`), installation du seul wheel, interpréteur isolé hors du dépôt, puis `loom --version` (égale à la version du `pyproject.toml`), `python -m loom_ia --version`, `loom --help`, `loom schema`, `loom validate` d'une config minimale (la source d'outils `forge` du paquet doit apparaître) et `loom run` avec un modèle simulé. Un wheel qui oublierait une dépendance ou un fichier ne passe plus à `twine check` seul.
- Le job `tests-complets` lance en root (`sudo`) l'essai qui fait vraiment abandonner ses droits à `execd` : le job tourne sous l'uid 1500 et sans groupe supplémentaire, et ne lit pas un fichier de root. Hors CI, l'essai est sauté hors de root.

### Tests
- `tests/firecracker/test_firecracker_session.py` : deux essais vérifient, sans root, les options que `Session._run` passe au lancement du job (`user`, `group`, `extra_groups=[]` en root ; aucune sans root) ; ils n'étaient jamais exercés.
- `tests/conftest.py` : une fixture automatique rend le logger `loom_ia` tel qu'elle l'a trouvé. Un essai qui lançait une commande de la CLI coupait la propagation des logs pour les suivants, et `test_jsonl_partial_last_line_is_ignored_then_quarantined` échouait selon l'ordre d'exécution (TST-8). Les deux copies locales (`test_logging.py`, `test_evals.py`) sont retirées.
- `tests/unit/test_idempotence.py` : `test_the_executor_deadline_keeps_the_key_too` attend l'échéance de la réservation avant de relancer. Il relançait aussitôt après « Délai dépassé », alors que la réservation suit le délai de l'outil à quelques millisecondes près (marge mesurée : 0,1 à 5 ms) : l'essai dépendait de la vitesse de la machine et a échoué en CI (`tests-complets`, avec la couverture). Aucun code de production touché.

## 2.0.4 - publiée

Correctifs de packaging, de lecture du journal et de l'unité du service `execd`. Aucune fonctionnalité nouvelle.

### Ligne de commande et paquet
- ⚠ `loom --version` affiche la version (`loom 2.0.4`) et sort en code 0 (avant : erreur, code 2). `python -m loom_ia` lance le même programme que `loom` (nouveau `src/loom_ia/__main__.py`).
- ⚠ Les messages d'extra manquant de `loom serve`, `loom serve --reload`, `loom mcp` et de `server.mcp.http` nomment le paquet et non `uv` : « demande l'extra 'http' (installer l'extra : loom-ia[http]) » (avant : `… : uv sync --extra http`). Un script qui cherchait l'ancien texte doit s'adapter.
- L'aide de `loom report --periode` ne cite plus un nom de jalon interne.

### Intégration continue et dépôt
- `twine` est épinglé à 7.0.0 dans `ci.yml` et `publish.yml` ; le commentaire sur la version d'uv de `publish.yml` dit vrai.
- Nouveaux fichiers : `.github/dependabot.yml` (mise à jour hebdomadaire des actions GitHub épinglées par SHA ; pas des dépendances Python, dont les bornes sont choisies à la main) et `SECURITY.md` (comment signaler une vulnérabilité).
- Le dossier vide `tests/e2e/` est supprimé.

### Documentation
- `README.md` : Python 3.14 en tête ; l'exemple `Bench` dit comment il s'exécute (`asyncio_mode = "auto"` ou `@pytest.mark.asyncio`) ; `server.mcp.http` demande aussi l'extra `mcp` ; chemin d'import de `ToolError` ; les lignes de log portent `run_id`, `span_id` et `tenant_id` « quand elles les connaissent » ; `examples/` n'est pas dans le paquet pip.
- `docs/conception.md` et `docs/fonctions.md` : exemples de configuration qui passent le chargeur (`approval: {expires_in, on_expiry, scope}`, `lease: 60`, `shutdown_timeout` une seule fois) ; modèle de référence `claude-haiku-5-5` partout (conception, howto, exemples `j2` à `j5`) ; Firestore et GCS marqués « non réalisé » ; `run.paused` et `run.resumed` remplacés par `run.transitioned` (`to_state=paused`, puis `running`) ; listes d'extras alignées sur `pyproject.toml` ; plafond MCP de 4 Mio du SDK (`max_body_bytes` ne peut que l'abaisser) ; champ `PendingApproval.launched` documenté.
- `docs/conception.md` relu contre le code des contrats modifiés en 2.0.2 à 2.0.4 : contrat de `cancel` (run `CANCELLED`, non `CancelledError`), clé d'idempotence `client:outil:clé`, 404 (et non 403) pour un agent fermé au client, 403 sur le flux d'un arbre contenant un agent interdit, 422/503/500 du journal, codes de sortie de la CLI et règle de `serve --host`, rotation de la clé de tête du chiffrement, secrets copiés au premier accès, garde « journal durable », noms MCP réservés, dépendances du noyau (`regex`, `jsonschema<4.27`), délais Postgres, sandbox (liens, tubes, `hidepid`), compaction, évals, contrats (`$ref`, `$schema`, `REGEX_TIMEOUT`).

### Dépendances
- `referencing` et `jsonschema-specifications` sont déclarées dans les dépendances du noyau : `core/bounded_regex.py` les importe directement. Elles étaient déjà installées, comme dépendances de `jsonschema`. Planchers : ceux que `jsonschema` 4.26 exige (`referencing>=0.28.4,<1`, `jsonschema-specifications>=2023.3.6`).
- Les paquets que le code d'un extra importe directement sont déclarés dans cet extra, avec les planchers du SDK qui les tire : `httpx2` (extras `anthropic` et `openai`), `anyio`, `httpx` et `starlette` (extra `mcp`), `starlette` (extra `http`). Aucun paquet nouveau n'est installé : ils venaient déjà des SDK.
- Nouveau test `tests/unit/test_dependances.py` : tout paquet tiers que `src` importe doit être déclaré dans `pyproject.toml`.

### Journal
- ⚠ **Les champs inconnus d'un événement sont ignorés à la lecture** (journaux JSONL, SQLite et Postgres, chiffrés ou non, et rejeu). Avant, un code plus ancien (retour arrière, workers de versions mêlées) déclarait illisible une session dont un événement portait un champ ajouté par un code plus récent (`Extra inputs are not permitted`). L'écriture reste stricte, et une `schema_version` autre que 1 reste refusée. La documentation promettait un convertisseur à la lecture, qui n'existe pas : elle dit maintenant qu'il sera écrit avec la première version 2.

### OpenAI Responses
- `include: reasoning.encrypted_content` reste envoyé même pour un modèle `thinking: false` : décision prise, documentée, non vérifiée sur l'API réelle pour un modèle sans raisonnement (MOD-1).

### Firecracker
- ⚠ **`/proc` cloisonné dans l'invité** (`hidepid=2`, unité `procfs-hidepid.service` remontée avant `execd`, qui la requiert) : tous les jobs partagent l'uid 1500 et la ligne de commande de leur runner porte le dossier de la session (`/data/jobs/<uuid>/job.json`), seul secret entre deux sessions. Mesuré sur l'image réelle : un job lisait les processus de tous les comptes. Un job ne voit plus que les siens ; root, donc `execd`, voit tout. Tant que `EXECD_MAX_CONCURRENT_EXEC` reste à 1 (un job à la fois), l'exposition était limitée ; elle ne l'est plus dès qu'on relève cette valeur. **Prend effet après reconstruction de l'image.** `make_vm.sh` signale l'absence de l'unité à la vérification finale.
- ⚠ **Unité `execd.service`** (générée par `make_vm.sh`) : `StartLimitIntervalSec=0` et `LimitNOFILE=8192`. Avec le plafond par défaut de systemd (5 départs en 10 s) et `RestartSec=200ms`, un défaut qui faisait tomber `execd` à répétition laissait l'unité arrêtée pour de bon, VM injoignable jusqu'au prochain démarrage ; elle redémarre maintenant sans plafond. Les connexions refusées en `busy` gardent un descripteur jusqu'à 10 s : la limite de descripteurs d'`execd` est explicite au lieu des 1 024 du défaut (celle des jobs reste fixée par `runner.py`). **Prend effet après reconstruction de l'image** (`./make_vm.sh <dossier>`) ; une VM déjà construite garde son ancienne unité.
