# Commande loom storage

Stockages de service : le SQL à appliquer.

```text
usage: loom storage [-h] [--config CONFIG] [--profile {dev,prod}] [--env-file ENV_FILE |
                    --no-env-file]
                    {sql} ...
```

Arguments et options de `loom storage` :

- `--config` : amorce, qui dit où lire la config (défaut : loom-boot.yaml)
- `--profile` (choix : dev, prod) : profil actif ; l'emporte sur LOOM_PROFILE et sur l'amorce
- `--env-file` : fichier de variables à lire, au lieu de 'env_file:' de l'amorce ou du .env voisin de l'amorce (une variable déjà définie l'emporte)
- `--no-env-file` : ne lit aucun fichier de variables

## Commande `loom storage sql`

Imprime le SQL du stockage Postgres déclaré (tables, rôle, politiques).

```text
usage: loom storage sql [-h] [--config CONFIG] [--profile {dev,prod}] [--env-file ENV_FILE |
                        --no-env-file]
```

Le SQL à appliquer pour le stockage Postgres que la config déclare.

Rien n'est exécuté : la sortie se relit, se met en revue et s'applique
avec le rôle qui en a le droit (`psql -f`). loom l'applique aussi de
lui-même à la première ouverture, si le rôle connecté le peut.

Arguments et options de `loom storage sql` :

- `--config` : amorce, qui dit où lire la config (défaut : loom-boot.yaml)
- `--profile` (choix : dev, prod) : profil actif ; l'emporte sur LOOM_PROFILE et sur l'amorce
- `--env-file` : fichier de variables à lire, au lieu de 'env_file:' de l'amorce ou du .env voisin de l'amorce (une variable déjà définie l'emporte)
- `--no-env-file` : ne lit aucun fichier de variables
