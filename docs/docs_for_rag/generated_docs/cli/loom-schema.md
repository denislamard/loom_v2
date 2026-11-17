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

- `--config` : fichier de configuration (défaut : loom.yaml)
- `--profile` (choix : dev, prod) : profil actif ; l'emporte sur LOOM_PROFILE et sur la config
- `--env-file` : fichier de variables à lire, au lieu de 'env_file:' de la config ou du .env voisin de la config (une variable déjà définie l'emporte)
- `--no-env-file` : ne lit aucun fichier de variables
