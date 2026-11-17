# Commande loom state

State d'une session : ce que l'application et les outils y ont écrit.

```text
[1;34musage: [0m[1;35mloom state[0m [[32m-h[0m] [[36m--tenant [33mTENANT[0m] [[36m--config [33mCONFIG[0m] [[36m--profile [33m{dev,prod}[0m]
                  [[36m--env-file [33mENV_FILE[0m | [36m--no-env-file[0m]
                  [32msession_id[0m
```

State d'une session : ce qui est écrit, sans les valeurs par défaut, en JSON.

Arguments et options de `loom state` :

- `--tenant` : client au nom duquel agir (défaut : celui de la config, sinon 'default')
- `session_id` : (sans aide)
- `--config` : fichier de configuration (défaut : loom.yaml)
- `--profile` (choix : dev, prod) : profil actif ; l'emporte sur LOOM_PROFILE et sur la config
- `--env-file` : fichier de variables à lire, au lieu de 'env_file:' de la config ou du .env voisin de la config (une variable déjà définie l'emporte)
- `--no-env-file` : ne lit aucun fichier de variables
