# Commande loom serve

Sert l'API REST.

```text
usage: loom serve [-h] [--host HOST] [--port PORT] [--reload] [--config CONFIG]
                  [--profile {dev,prod}] [--env-file ENV_FILE | --no-env-file]
```

`loom serve` : sert l'API REST (extra `http`) ; `--host` et `--port` remplacent ceux de `server.http`.

Imprime d'abord le bandeau (`serving_banner`). `--reload` relance le
serveur quand un fichier de la config change ; refusé en profil prod.

**Arguments :**

- `args` : Arguments de la commande.

**Renvoie :**

0 à l'arrêt ; 2 sans l'extra `http`, ou `--reload` refusé.

Arguments et options de `loom serve` :

- `--host` : (sans aide)
- `--port` : (sans aide)
- `--reload` : relance le serveur quand un fichier de la config change (dev ; refusé en prod) ; une config cassée est refusée et l'ancien process continue de servir
- `--config` : amorce, qui dit où lire la config (défaut : loom-boot.yaml)
- `--profile` (choix : dev, prod) : profil actif ; l'emporte sur LOOM_PROFILE et sur l'amorce
- `--env-file` : fichier de variables à lire, au lieu de 'env_file:' de l'amorce ou du .env voisin de l'amorce (une variable déjà définie l'emporte)
- `--no-env-file` : ne lit aucun fichier de variables
