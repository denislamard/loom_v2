# Journal des modifications

Les versions sont listées de la plus récente à la plus ancienne. Une ligne marquée ⚠ change un comportement visible.

## 2.0.5 - non publiée

### Journal
- ⚠ **`after` (REST `GET /v1/events`, `EventQuery.after`) suit l'ordre d'écriture, non l'ordre des identifiants.** L'identifiant d'un événement est posé à la création du brouillon, avant l'écriture : un écrivain lent (approbateur REST, sous-agents, autre worker) pouvait écrire après un autre un événement d'identifiant plus petit, que reprendre « après » le dernier id vu ne rendait jamais. Les résultats sont désormais rendus dans l'ordre d'écriture (mémoire, SQLite, Postgres) ; un `after` inconnu du journal (session effacée) retombe sur la comparaison des identifiants. Le journal JSONL, qui a un fichier par session, reste trié par identifiant.
- SQLite : un index `events_ingestion` est créé à l'ouverture d'un journal existant (une fois).
- ⚠ Postgres : le schéma du journal passe en **version 3** (colonne `ingested`, identité, et son index). L'ouverture migre une base existante, comme pour la version 2 ; qui applique le DDL à la main reprend la sortie de `loom storage sql`. Une écriture prend un verrou consultatif par client juste avant l'insertion (les écritures d'un même client se suivent pendant l'insertion et la validation).

### Intégration continue
- Le job `build` de `ci.yml` et celui de `publish.yml` essaient le wheel construit : environnement vierge (`uv venv`), installation du seul wheel, interpréteur isolé hors du dépôt, puis `loom --version` (égale à la version du `pyproject.toml`), `python -m loom_ia --version`, `loom --help`, `loom schema`, `loom validate` d'une config minimale (la source d'outils `forge` du paquet doit apparaître) et `loom run` avec un modèle simulé. Un wheel qui oublierait une dépendance ou un fichier ne passe plus à `twine check` seul.
- Le job `tests-complets` lance en root (`sudo`) l'essai qui fait vraiment abandonner ses droits à `execd` : le job tourne sous l'uid 1500 et sans groupe supplémentaire, et ne lit pas un fichier de root. Hors CI, l'essai est sauté hors de root.

### Tests
- `tests/firecracker/test_firecracker_session.py` : deux essais vérifient, sans root, les options que `Session._run` passe au lancement du job (`user`, `group`, `extra_groups=[]` en root ; aucune sans root) ; ils n'étaient jamais exercés.

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
