# Commande loom mcp

Sert les agents en MCP, sur stdio.

```text
usage: loom mcp [-h] [--tenant TENANT] [--user UTILISATEUR] [--config CONFIG]
                [--profile {dev,prod}] [--env-file ENV_FILE | --no-env-file]
```

`loom mcp` : sert les agents en MCP sur stdio (extra `mcp`), au nom de `--user` ; rien d'autre ne passe sur stdout.

**Arguments :**

- `args` : Arguments de la commande.

**Renvoie :**

0 à l'arrêt ; 2 sans l'extra `mcp`.

Arguments et options de `loom mcp` :

- `--tenant` : client au nom duquel agir (défaut : celui de la config, sinon 'default')
- `--user` : utilisateur déclaré dans security.users (défaut : LOOM_USER) ; le client est le sien
- `--config` : amorce, qui dit où lire la config (défaut : loom-boot.yaml)
- `--profile` (choix : dev, prod) : profil actif ; l'emporte sur LOOM_PROFILE et sur l'amorce
- `--env-file` : fichier de variables à lire, au lieu de 'env_file:' de l'amorce ou du .env voisin de l'amorce (une variable déjà définie l'emporte)
- `--no-env-file` : ne lit aucun fichier de variables
