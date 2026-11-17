# Commande loom storage

Stockages de service : le SQL à appliquer.

```text
usage: loom storage [-h] [--config CONFIG] [--profile {dev,prod}] [--env-file ENV_FILE |
                    --no-env-file]
                    {sql} ...
```

Arguments et options de `loom storage` :

- `--config` : fichier de configuration (défaut : loom.yaml)
- `--profile` (choix : dev, prod) : profil actif ; l'emporte sur LOOM_PROFILE et sur la config
- `--env-file` : fichier de variables à lire, au lieu de 'env_file:' de la config ou du .env voisin de la config (une variable déjà définie l'emporte)
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

- `--config` : fichier de configuration (défaut : loom.yaml)
- `--profile` (choix : dev, prod) : profil actif ; l'emporte sur LOOM_PROFILE et sur la config
- `--env-file` : fichier de variables à lire, au lieu de 'env_file:' de la config ou du .env voisin de la config (une variable déjà définie l'emporte)
- `--no-env-file` : ne lit aucun fichier de variables
