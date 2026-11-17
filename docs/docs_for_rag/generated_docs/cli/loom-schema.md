# Commande loom schema

JSON Schema du fichier de configuration.

```text
[1;34musage: [0m[1;35mloom schema[0m [[32m-h[0m] [[36m--config [33mCONFIG[0m] [[36m--profile [33m{dev,prod}[0m] [[36m--env-file [33mENV_FILE[0m |
                   [36m--no-env-file[0m]
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
