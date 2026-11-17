# Commande loom mcp

Sert les agents en MCP, sur stdio.

```text
[1;34musage: [0m[1;35mloom mcp[0m [[32m-h[0m] [[36m--tenant [33mTENANT[0m] [[36m--user [33mUTILISATEUR[0m] [[36m--config [33mCONFIG[0m]
                [[36m--profile [33m{dev,prod}[0m] [[36m--env-file [33mENV_FILE[0m | [36m--no-env-file[0m]
```

`loom mcp` : sert les agents en MCP sur stdio (extra `mcp`), au nom de `--user` ; rien d'autre ne passe sur stdout.

**Arguments :**

- `args` : Arguments de la commande.

**Renvoie :**

0 à l'arrêt ; 2 sans l'extra `mcp`.

Arguments et options de `loom mcp` :

- `--tenant` : client au nom duquel agir (défaut : celui de la config, sinon 'default')
- `--user` : utilisateur déclaré dans security.users (défaut : LOOM_USER) ; le client est le sien
- `--config` : fichier de configuration (défaut : loom.yaml)
- `--profile` (choix : dev, prod) : profil actif ; l'emporte sur LOOM_PROFILE et sur la config
- `--env-file` : fichier de variables à lire, au lieu de 'env_file:' de la config ou du .env voisin de la config (une variable déjà définie l'emporte)
- `--no-env-file` : ne lit aucun fichier de variables
