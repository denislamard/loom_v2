# Commande loom sessions

Journaux de session : lister, exporter, supprimer.

```text
usage: loom sessions [-h] [--config CONFIG] [--profile {dev,prod}] [--env-file ENV_FILE |
                     --no-env-file]
                     {list,show,files,export,delete} ...
```

Arguments et options de `loom sessions` :

- `--config` : amorce, qui dit où lire la config (défaut : loom-boot.yaml)
- `--profile` (choix : dev, prod) : profil actif ; l'emporte sur LOOM_PROFILE et sur l'amorce
- `--env-file` : fichier de variables à lire, au lieu de 'env_file:' de l'amorce ou du .env voisin de l'amorce (une variable déjà définie l'emporte)
- `--no-env-file` : ne lit aucun fichier de variables

## Commande `loom sessions list`

Sessions du journal, la plus récente d'abord.

```text
usage: loom sessions list [-h] [--tenant TENANT] [--json] [--config CONFIG] [--profile {dev,prod}]
                          [--env-file ENV_FILE | --no-env-file]
```

`loom sessions list` : sessions du client, la plus récemment écrite d'abord (identifiant, nombre d'événements, date).

`--json` pour la liste en JSON. Un journal en mémoire est signalé : il
est vide à chaque commande.

**Arguments :**

- `args` : Arguments de la commande.

**Renvoie :**

0.

Arguments et options de `loom sessions list` :

- `--tenant` : client au nom duquel agir (défaut : celui de la config, sinon 'default')
- `--json` : affiche la liste en JSON
- `--config` : amorce, qui dit où lire la config (défaut : loom-boot.yaml)
- `--profile` (choix : dev, prod) : profil actif ; l'emporte sur LOOM_PROFILE et sur l'amorce
- `--env-file` : fichier de variables à lire, au lieu de 'env_file:' de l'amorce ou du .env voisin de l'amorce (une variable déjà définie l'emporte)
- `--no-env-file` : ne lit aucun fichier de variables

## Commande `loom sessions show`

Fiche d'une session : ses runs, et les approbations en attente.

```text
usage: loom sessions show [-h] [--tenant TENANT] [--json] [--config CONFIG] [--profile {dev,prod}]
                          [--env-file ENV_FILE | --no-env-file]
                          session_id
```

Fiche d'une session : ce qu'un approbateur doit voir en revenant.

Les runs, avec leur statut, et chaque approbation en attente avec la
commande qui la tranche. Les arguments des appels ne sont pas affichés —
ils peuvent être longs ou sensibles — mais `--json` donne la fiche
entière, `SessionInfo` tel que l'API le rend.

Arguments et options de `loom sessions show` :

- `--tenant` : client au nom duquel agir (défaut : celui de la config, sinon 'default')
- `session_id` : (sans aide)
- `--json` : affiche la fiche en JSON
- `--config` : amorce, qui dit où lire la config (défaut : loom-boot.yaml)
- `--profile` (choix : dev, prod) : profil actif ; l'emporte sur LOOM_PROFILE et sur l'amorce
- `--env-file` : fichier de variables à lire, au lieu de 'env_file:' de l'amorce ou du .env voisin de l'amorce (une variable déjà définie l'emporte)
- `--no-env-file` : ne lit aucun fichier de variables

## Commande `loom sessions files`

Fichiers d'une session ; --out les écrit sous leur vrai nom.

```text
usage: loom sessions files [-h] [--tenant TENANT] [--json]
                           [--origin {attachment,tool_output,offload}] [--out DOSSIER] [--force]
                           [--config CONFIG] [--profile {dev,prod}] [--env-file ENV_FILE |
                           --no-env-file]
                           session_id
```

Fichiers d'une session, lus au journal ; `--out` les écrit sous leur vrai nom.

Le stockage les range sous leur condensé (`cea7c3….csv`) : seul le
journal dit ce qu'ils sont. Deux fichiers de même nom sont départagés
(`graphique-2.png`), un fichier sans nom garde son condensé, et un
fichier déjà présent dans le dossier n'est pas écrasé sans `--force`.

Arguments et options de `loom sessions files` :

- `--tenant` : client au nom duquel agir (défaut : celui de la config, sinon 'default')
- `session_id` : (sans aide)
- `--json` : affiche la liste en JSON
- `--origin` (choix : attachment, tool_output, offload) : ne garde que cette origine (répétable)
- `--out` : écrit les fichiers dans ce dossier
- `--force` : écrase un fichier déjà présent
- `--config` : amorce, qui dit où lire la config (défaut : loom-boot.yaml)
- `--profile` (choix : dev, prod) : profil actif ; l'emporte sur LOOM_PROFILE et sur l'amorce
- `--env-file` : fichier de variables à lire, au lieu de 'env_file:' de l'amorce ou du .env voisin de l'amorce (une variable déjà définie l'emporte)
- `--no-env-file` : ne lit aucun fichier de variables

## Commande `loom sessions export`

Écrit les événements d'une session en JSONL.

```text
usage: loom sessions export [-h] [--tenant TENANT] [--out FICHIER] [--config CONFIG]
                            [--profile {dev,prod}] [--env-file ENV_FILE | --no-env-file]
                            session_id
```

`loom sessions export <session_id>` : écrit les événements de la session en JSONL, sur stdout ou dans `--out`.

C'est le format que relisent `loom replay --journal` et les cas de rejeu
des évals.

**Arguments :**

- `args` : Arguments de la commande.

**Renvoie :**

0 ; 1 pour une session inconnue.

Arguments et options de `loom sessions export` :

- `--tenant` : client au nom duquel agir (défaut : celui de la config, sinon 'default')
- `session_id` : (sans aide)
- `--out` : fichier de sortie (défaut : la sortie standard)
- `--config` : amorce, qui dit où lire la config (défaut : loom-boot.yaml)
- `--profile` (choix : dev, prod) : profil actif ; l'emporte sur LOOM_PROFILE et sur l'amorce
- `--env-file` : fichier de variables à lire, au lieu de 'env_file:' de l'amorce ou du .env voisin de l'amorce (une variable déjà définie l'emporte)
- `--no-env-file` : ne lit aucun fichier de variables

## Commande `loom sessions delete`

Supprime une session : journal, fichiers et clés (RGPD).

```text
usage: loom sessions delete [-h] [--tenant TENANT] [--yes] [--config CONFIG]
                            [--profile {dev,prod}] [--env-file ENV_FILE | --no-env-file]
                            session_id
```

`loom sessions delete <session_id>` : supprime la session — journal, fichiers et clés d'idempotence (RGPD).

Demande confirmation, sauf avec `--yes`.

**Arguments :**

- `args` : Arguments de la commande.

**Renvoie :**

0 ; 1 pour une session inconnue ; 2 si la suppression n'est pas
confirmée.

Arguments et options de `loom sessions delete` :

- `--tenant` : client au nom duquel agir (défaut : celui de la config, sinon 'default')
- `session_id` : (sans aide)
- `--yes` : ne demande pas confirmation
- `--config` : amorce, qui dit où lire la config (défaut : loom-boot.yaml)
- `--profile` (choix : dev, prod) : profil actif ; l'emporte sur LOOM_PROFILE et sur l'amorce
- `--env-file` : fichier de variables à lire, au lieu de 'env_file:' de l'amorce ou du .env voisin de l'amorce (une variable déjà définie l'emporte)
- `--no-env-file` : ne lit aucun fichier de variables
