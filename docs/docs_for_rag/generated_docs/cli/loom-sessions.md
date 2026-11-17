# Commande loom sessions

Journaux de session : lister, exporter, supprimer.

```text
[1;34musage: [0m[1;35mloom sessions[0m [[32m-h[0m] [[36m--config [33mCONFIG[0m] [[36m--profile [33m{dev,prod}[0m] [[36m--env-file [33mENV_FILE[0m |
                     [36m--no-env-file[0m]
                     [32m{list,show,files,export,delete} ...[0m
```

Arguments et options de `loom sessions` :

- `--config` : fichier de configuration (défaut : loom.yaml)
- `--profile` (choix : dev, prod) : profil actif ; l'emporte sur LOOM_PROFILE et sur la config
- `--env-file` : fichier de variables à lire, au lieu de 'env_file:' de la config ou du .env voisin de la config (une variable déjà définie l'emporte)
- `--no-env-file` : ne lit aucun fichier de variables

## Commande `loom sessions list`

Sessions du journal, la plus récente d'abord.

```text
[1;34musage: [0m[1;35mloom sessions list[0m [[32m-h[0m] [[36m--tenant [33mTENANT[0m] [[36m--json[0m] [[36m--config [33mCONFIG[0m] [[36m--profile [33m{dev,prod}[0m]
                          [[36m--env-file [33mENV_FILE[0m | [36m--no-env-file[0m]
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
- `--config` : fichier de configuration (défaut : loom.yaml)
- `--profile` (choix : dev, prod) : profil actif ; l'emporte sur LOOM_PROFILE et sur la config
- `--env-file` : fichier de variables à lire, au lieu de 'env_file:' de la config ou du .env voisin de la config (une variable déjà définie l'emporte)
- `--no-env-file` : ne lit aucun fichier de variables

## Commande `loom sessions show`

Fiche d'une session : ses runs, et les approbations en attente.

```text
[1;34musage: [0m[1;35mloom sessions show[0m [[32m-h[0m] [[36m--tenant [33mTENANT[0m] [[36m--json[0m] [[36m--config [33mCONFIG[0m] [[36m--profile [33m{dev,prod}[0m]
                          [[36m--env-file [33mENV_FILE[0m | [36m--no-env-file[0m]
                          [32msession_id[0m
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
- `--config` : fichier de configuration (défaut : loom.yaml)
- `--profile` (choix : dev, prod) : profil actif ; l'emporte sur LOOM_PROFILE et sur la config
- `--env-file` : fichier de variables à lire, au lieu de 'env_file:' de la config ou du .env voisin de la config (une variable déjà définie l'emporte)
- `--no-env-file` : ne lit aucun fichier de variables

## Commande `loom sessions files`

Fichiers d'une session ; --out les écrit sous leur vrai nom.

```text
[1;34musage: [0m[1;35mloom sessions files[0m [[32m-h[0m] [[36m--tenant [33mTENANT[0m] [[36m--json[0m]
                           [[36m--origin [33m{attachment,tool_output,offload}[0m] [[36m--out [33mDOSSIER[0m] [[36m--force[0m]
                           [[36m--config [33mCONFIG[0m] [[36m--profile [33m{dev,prod}[0m] [[36m--env-file [33mENV_FILE[0m |
                           [36m--no-env-file[0m]
                           [32msession_id[0m
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
- `--config` : fichier de configuration (défaut : loom.yaml)
- `--profile` (choix : dev, prod) : profil actif ; l'emporte sur LOOM_PROFILE et sur la config
- `--env-file` : fichier de variables à lire, au lieu de 'env_file:' de la config ou du .env voisin de la config (une variable déjà définie l'emporte)
- `--no-env-file` : ne lit aucun fichier de variables

## Commande `loom sessions export`

Écrit les événements d'une session en JSONL.

```text
[1;34musage: [0m[1;35mloom sessions export[0m [[32m-h[0m] [[36m--tenant [33mTENANT[0m] [[36m--out [33mFICHIER[0m] [[36m--config [33mCONFIG[0m]
                            [[36m--profile [33m{dev,prod}[0m] [[36m--env-file [33mENV_FILE[0m | [36m--no-env-file[0m]
                            [32msession_id[0m
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
- `--config` : fichier de configuration (défaut : loom.yaml)
- `--profile` (choix : dev, prod) : profil actif ; l'emporte sur LOOM_PROFILE et sur la config
- `--env-file` : fichier de variables à lire, au lieu de 'env_file:' de la config ou du .env voisin de la config (une variable déjà définie l'emporte)
- `--no-env-file` : ne lit aucun fichier de variables

## Commande `loom sessions delete`

Supprime une session : journal, fichiers et clés (RGPD).

```text
[1;34musage: [0m[1;35mloom sessions delete[0m [[32m-h[0m] [[36m--tenant [33mTENANT[0m] [[36m--yes[0m] [[36m--config [33mCONFIG[0m]
                            [[36m--profile [33m{dev,prod}[0m] [[36m--env-file [33mENV_FILE[0m | [36m--no-env-file[0m]
                            [32msession_id[0m
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
- `--config` : fichier de configuration (défaut : loom.yaml)
- `--profile` (choix : dev, prod) : profil actif ; l'emporte sur LOOM_PROFILE et sur la config
- `--env-file` : fichier de variables à lire, au lieu de 'env_file:' de la config ou du .env voisin de la config (une variable déjà définie l'emporte)
- `--no-env-file` : ne lit aucun fichier de variables
