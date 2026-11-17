# Commande loom state

State d'une session : ce que l'application et les outils y ont écrit.

```text
usage: loom state [-h] [--tenant TENANT] [--config CONFIG] [--profile {dev,prod}]
                  [--env-file ENV_FILE | --no-env-file]
                  session_id
```

State d'une session : ce qui est écrit, sans les valeurs par défaut, en JSON.

Arguments et options de `loom state` :

- `--tenant` : client au nom duquel agir (défaut : celui de la config, sinon 'default')
- `session_id` : (sans aide)
- `--config` : fichier de configuration (défaut : loom.yaml)
- `--profile` (choix : dev, prod) : profil actif ; l'emporte sur LOOM_PROFILE et sur la config
- `--env-file` : fichier de variables à lire, au lieu de 'env_file:' de la config ou du .env voisin de la config (une variable déjà définie l'emporte)
- `--no-env-file` : ne lit aucun fichier de variables
