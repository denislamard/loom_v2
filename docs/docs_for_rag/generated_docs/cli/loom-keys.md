# Commande loom keys

Clés d'API de l'accès REST.

```text
usage: loom keys [-h] [--config CONFIG] [--profile {dev,prod}] [--env-file ENV_FILE |
                 --no-env-file]
                 {create} ...
```

Arguments et options de `loom keys` :

- `--config` : amorce, qui dit où lire la config (défaut : loom-boot.yaml)
- `--profile` (choix : dev, prod) : profil actif ; l'emporte sur LOOM_PROFILE et sur l'amorce
- `--env-file` : fichier de variables à lire, au lieu de 'env_file:' de l'amorce ou du .env voisin de l'amorce (une variable déjà définie l'emporte)
- `--no-env-file` : ne lit aucun fichier de variables

## Commande `loom keys create`

Fabrique une clé et son empreinte.

```text
usage: loom keys create [-h] --user UTILISATEUR [--scope PERMISSION] [--agent AGENT]
                        [--expires QUAND] [--rate-limit N] [--config CONFIG]
                        [--profile {dev,prod}] [--env-file ENV_FILE | --no-env-file]
                        id
```

`loom keys create <id> --user <utilisateur>` : fabrique une clé d'API et imprime le bloc `security.api_keys` à ajouter à la config.

La clé n'est affichée qu'une fois : la config n'en garde que l'empreinte.
Options : `--scope` (permission gardée, répétable ; défaut : toutes),
`--agent` (agent autorisé, répétable ; défaut : tous), `--expires`
(date ou durée : `2027-01-01`, `90j`, `12h`), `--rate-limit`
(requêtes par minute). Une clé qui peut approuver sans `read_content` est
signalée : les arguments d'un appel en attente lui sont masqués.

**Arguments :**

- `args` : Arguments de la commande.

**Renvoie :**

0 ; 2 pour un `--expires` illisible.

Arguments et options de `loom keys create` :

- `id` : nom de la clé dans la configuration
- `--user` (obligatoire) : utilisateur que la clé identifie, déclaré dans security.users (D-4)
- `--scope` (choix : run, answer, cancel, approve, self_approve, amend, read, read_content, admin, act_as) : permission que la clé garde de celles de son utilisateur (répétable ; défaut : toutes)
- `--agent` : agent autorisé (répétable ; défaut : tous)
- `--expires` : fin de validité : une date (2027-01-01) ou une durée (90j, 12h)
- `--rate-limit` : requêtes par minute accordées à cette clé (défaut : aucune limite)
- `--config` : amorce, qui dit où lire la config (défaut : loom-boot.yaml)
- `--profile` (choix : dev, prod) : profil actif ; l'emporte sur LOOM_PROFILE et sur l'amorce
- `--env-file` : fichier de variables à lire, au lieu de 'env_file:' de l'amorce ou du .env voisin de l'amorce (une variable déjà définie l'emporte)
- `--no-env-file` : ne lit aucun fichier de variables
