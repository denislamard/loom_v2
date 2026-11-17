# Commande loom resume

Reprend un run interrompu.

```text
[1;34musage: [0m[1;35mloom resume[0m [[32m-h[0m] [[36m--tenant [33mTENANT[0m] [[36m--user [33mUTILISATEUR[0m] [[36m--json[0m] [[36m--session [33mSESSION[0m]
                   [[36m--config [33mCONFIG[0m] [[36m--profile [33m{dev,prod}[0m] [[36m--env-file [33mENV_FILE[0m | [36m--no-env-file[0m]
                   [32mrun_id[0m
```

`loom resume <run_id>` : reprend un run interrompu et attend sa fin.

Un run déjà terminé n'est pas repris : la commande le dit, avec la
commande `loom inspect` qui le relit.

**Arguments :**

- `args` : Arguments de la commande.

**Renvoie :**

0 si le run aboutit, attend une réponse, ou était déjà terminé ; 1 sinon.

Arguments et options de `loom resume` :

- `--tenant` : client au nom duquel agir (défaut : celui de la config, sinon 'default')
- `--user` : utilisateur déclaré dans security.users (défaut : LOOM_USER) ; le client est le sien
- `run_id` : (sans aide)
- `--json` : affiche le résultat en JSON
- `--session` : journal du run
- `--config` : fichier de configuration (défaut : loom.yaml)
- `--profile` (choix : dev, prod) : profil actif ; l'emporte sur LOOM_PROFILE et sur la config
- `--env-file` : fichier de variables à lire, au lieu de 'env_file:' de la config ou du .env voisin de la config (une variable déjà définie l'emporte)
- `--no-env-file` : ne lit aucun fichier de variables
