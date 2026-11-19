# Commande loom schema

JSON Schema du fichier de configuration.

```text
usage: loom schema [-h] [--config CONFIG] [--profile {dev,prod}] [--env-file ENV_FILE |
                   --no-env-file]
```

`loom schema` : imprime le JSON Schema du fichier de configuration.

**Arguments :**

- `args` : Arguments de la commande.

**Renvoie :**

0.

Arguments et options de `loom schema` :

- `--config` : amorce, qui dit où lire la config (défaut : loom-boot.yaml)
- `--profile` (choix : dev, prod) : profil actif ; l'emporte sur LOOM_PROFILE et sur l'amorce
- `--env-file` : fichier de variables à lire, au lieu de 'env_file:' de l'amorce ou du .env voisin de l'amorce (une variable déjà définie l'emporte)
- `--no-env-file` : ne lit aucun fichier de variables
