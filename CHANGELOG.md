# Journal des modifications

Les versions sont listées de la plus récente à la plus ancienne. Une ligne marquée ⚠ change un comportement visible.

## 2.0.4 — non publiée

Correctifs de packaging, de lecture du journal et de l'unité du service `execd`. Aucune fonctionnalité nouvelle.

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
