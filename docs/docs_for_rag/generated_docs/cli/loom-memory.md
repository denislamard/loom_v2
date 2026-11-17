# Commande loom memory

Entretient la mémoire d'un agent (loom.memory) : export, import….

```text
usage: loom memory [-h] [--config CONFIG] [--profile {dev,prod}] [--env-file ENV_FILE |
                   --no-env-file]
                   {export,import,reindex,eval} ...
```

Arguments et options de `loom memory` :

- `--config` : fichier de configuration (défaut : loom.yaml)
- `--profile` (choix : dev, prod) : profil actif ; l'emporte sur LOOM_PROFILE et sur la config
- `--env-file` : fichier de variables à lire, au lieu de 'env_file:' de la config ou du .env voisin de la config (une variable déjà définie l'emporte)
- `--no-env-file` : ne lit aucun fichier de variables

## Commande `loom memory export`

Écrit les documents en JSONL.

```text
usage: loom memory export [-h] [--tenant TENANT] [--config CONFIG] [--profile {dev,prod}]
                          [--env-file ENV_FILE | --no-env-file]
                          agent file
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
usage: loom memory import [-h] [--tenant TENANT] [--config CONFIG] [--profile {dev,prod}]
                          [--env-file ENV_FILE | --no-env-file]
                          agent file
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
usage: loom memory reindex [-h] [--tenant TENANT] [--config CONFIG] [--profile {dev,prod}]
                           [--env-file ENV_FILE | --no-env-file]
                           agent
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
usage: loom memory eval [-h] [--tenant TENANT] [--k K] [--json] [--config CONFIG]
                        [--profile {dev,prod}] [--env-file ENV_FILE | --no-env-file]
                        agent file
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
