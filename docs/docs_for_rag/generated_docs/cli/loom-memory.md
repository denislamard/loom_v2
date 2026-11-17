# Commande loom memory

Entretient la mémoire d'un agent (loom.memory) : export, import….

```text
[1;34musage: [0m[1;35mloom memory[0m [[32m-h[0m] [[36m--config [33mCONFIG[0m] [[36m--profile [33m{dev,prod}[0m] [[36m--env-file [33mENV_FILE[0m |
                   [36m--no-env-file[0m]
                   [32m{export,import,reindex,eval} ...[0m
```

Arguments et options de `loom memory` :

- `--config` : fichier de configuration (défaut : loom.yaml)
- `--profile` (choix : dev, prod) : profil actif ; l'emporte sur LOOM_PROFILE et sur la config
- `--env-file` : fichier de variables à lire, au lieu de 'env_file:' de la config ou du .env voisin de la config (une variable déjà définie l'emporte)
- `--no-env-file` : ne lit aucun fichier de variables

## Commande `loom memory export`

Écrit les documents en JSONL.

```text
[1;34musage: [0m[1;35mloom memory export[0m [[32m-h[0m] [[36m--tenant [33mTENANT[0m] [[36m--config [33mCONFIG[0m] [[36m--profile [33m{dev,prod}[0m]
                          [[36m--env-file [33mENV_FILE[0m | [36m--no-env-file[0m]
                          [32magent[0m [32mfile[0m
```

`loom memory` : la base de l'agent, hors de tout run (`access/cli/memory.py`).

Arguments et options de `loom memory export` :

- `--tenant` : client au nom duquel agir (défaut : celui de la config, sinon 'default')
- `agent` : (sans aide)
- `file` : fichier à créer (jamais écrasé)
- `--config` : fichier de configuration (défaut : loom.yaml)
- `--profile` (choix : dev, prod) : profil actif ; l'emporte sur LOOM_PROFILE et sur la config
- `--env-file` : fichier de variables à lire, au lieu de 'env_file:' de la config ou du .env voisin de la config (une variable déjà définie l'emporte)
- `--no-env-file` : ne lit aucun fichier de variables

## Commande `loom memory import`

Réimporte un export JSONL.

```text
[1;34musage: [0m[1;35mloom memory import[0m [[32m-h[0m] [[36m--tenant [33mTENANT[0m] [[36m--config [33mCONFIG[0m] [[36m--profile [33m{dev,prod}[0m]
                          [[36m--env-file [33mENV_FILE[0m | [36m--no-env-file[0m]
                          [32magent[0m [32mfile[0m
```

`loom memory` : la base de l'agent, hors de tout run (`access/cli/memory.py`).

Arguments et options de `loom memory import` :

- `--tenant` : client au nom duquel agir (défaut : celui de la config, sinon 'default')
- `agent` : (sans aide)
- `file` : (sans aide)
- `--config` : fichier de configuration (défaut : loom.yaml)
- `--profile` (choix : dev, prod) : profil actif ; l'emporte sur LOOM_PROFILE et sur la config
- `--env-file` : fichier de variables à lire, au lieu de 'env_file:' de la config ou du .env voisin de la config (une variable déjà définie l'emporte)
- `--no-env-file` : ne lit aucun fichier de variables

## Commande `loom memory reindex`

Recalcule les passages avec le modèle de la config.

```text
[1;34musage: [0m[1;35mloom memory reindex[0m [[32m-h[0m] [[36m--tenant [33mTENANT[0m] [[36m--config [33mCONFIG[0m] [[36m--profile [33m{dev,prod}[0m]
                           [[36m--env-file [33mENV_FILE[0m | [36m--no-env-file[0m]
                           [32magent[0m
```

`loom memory` : la base de l'agent, hors de tout run (`access/cli/memory.py`).

Arguments et options de `loom memory reindex` :

- `--tenant` : client au nom duquel agir (défaut : celui de la config, sinon 'default')
- `agent` : (sans aide)
- `--config` : fichier de configuration (défaut : loom.yaml)
- `--profile` (choix : dev, prod) : profil actif ; l'emporte sur LOOM_PROFILE et sur la config
- `--env-file` : fichier de variables à lire, au lieu de 'env_file:' de la config ou du .env voisin de la config (une variable déjà définie l'emporte)
- `--no-env-file` : ne lit aucun fichier de variables

## Commande `loom memory eval`

Mesure la recherche sur un jeu doré.

```text
[1;34musage: [0m[1;35mloom memory eval[0m [[32m-h[0m] [[36m--tenant [33mTENANT[0m] [[36m--k [33mK[0m] [[36m--json[0m] [[36m--config [33mCONFIG[0m]
                        [[36m--profile [33m{dev,prod}[0m] [[36m--env-file [33mENV_FILE[0m | [36m--no-env-file[0m]
                        [32magent[0m [32mfile[0m
```

`loom memory` : la base de l'agent, hors de tout run (`access/cli/memory.py`).

Arguments et options de `loom memory eval` :

- `--tenant` : client au nom duquel agir (défaut : celui de la config, sinon 'default')
- `agent` : (sans aide)
- `file` : jeu doré JSONL : {query, doc_id, project?} ; doc_id null : question hors sujet
- `--k` (choix : 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 45, 46, 47, 48, 49, 50 ; défaut : 10) : (sans aide)
- `--json` : rapport complet en JSON
- `--config` : fichier de configuration (défaut : loom.yaml)
- `--profile` (choix : dev, prod) : profil actif ; l'emporte sur LOOM_PROFILE et sur la config
- `--env-file` : fichier de variables à lire, au lieu de 'env_file:' de la config ou du .env voisin de la config (une variable déjà définie l'emporte)
- `--no-env-file` : ne lit aucun fichier de variables
