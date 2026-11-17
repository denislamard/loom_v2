# Commande loom inspect

Relit un run : son arbre (étapes, modèles, outils, sous-runs), sa réponse et son bilan.

```text
usage: loom inspect [-h] [--tenant TENANT] [--session SESSION] [--full] [--json] [--config CONFIG]
                    [--profile {dev,prod}] [--env-file ENV_FILE | --no-env-file]
                    run_id
```

Relit un run et ses sous-runs, lisiblement ; 2 s'il est introuvable.

En local, la commande lit le journal elle-même : elle montre le contenu.
`--json` rend la trace telle que l'API la rend à une clé `read_content`.

Arguments et options de `loom inspect` :

- `--tenant` : client au nom duquel agir (défaut : celui de la config, sinon 'default')
- `run_id` : (sans aide)
- `--session` : journal du run
- `--full` : arguments et résultats en entier, sans coupe
- `--json` : la trace en JSON, celle de GET /v1/traces/{run_id}
- `--config` : fichier de configuration (défaut : loom.yaml)
- `--profile` (choix : dev, prod) : profil actif ; l'emporte sur LOOM_PROFILE et sur la config
- `--env-file` : fichier de variables à lire, au lieu de 'env_file:' de la config ou du .env voisin de la config (une variable déjà définie l'emporte)
- `--no-env-file` : ne lit aucun fichier de variables
