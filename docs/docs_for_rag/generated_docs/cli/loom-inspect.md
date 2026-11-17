# Commande loom inspect

Relit un run : son arbre (étapes, modèles, outils, sous-runs), sa réponse et son bilan.

```text
[1;34musage: [0m[1;35mloom inspect[0m [[32m-h[0m] [[36m--tenant [33mTENANT[0m] [[36m--session [33mSESSION[0m] [[36m--full[0m] [[36m--json[0m] [[36m--config [33mCONFIG[0m]
                    [[36m--profile [33m{dev,prod}[0m] [[36m--env-file [33mENV_FILE[0m | [36m--no-env-file[0m]
                    [32mrun_id[0m
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
