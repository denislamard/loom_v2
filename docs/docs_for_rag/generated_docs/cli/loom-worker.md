# Commande loom worker

Consomme la file des tâches de fond (runs, reprises, résumés).

```text
[1;34musage: [0m[1;35mloom worker[0m [[32m-h[0m] [[36m--jobs [33mN[0m] [[36m--config [33mCONFIG[0m] [[36m--profile [33m{dev,prod}[0m] [[36m--env-file [33mENV_FILE[0m |
                   [36m--no-env-file[0m]
```

`loom worker` : consomme la file des tâches de fond jusqu'à Ctrl-C ou SIGTERM, `--jobs` tâches de front.

Reprend d'abord, pour chaque client, les runs restés en plan. Demande une
file servie par un courtier (`storage.queue.backend: rabbitmq`).

**Arguments :**

- `args` : Arguments de la commande.

**Renvoie :**

0 à l'arrêt.

Arguments et options de `loom worker` :

- `--jobs` (défaut : 1) : tâches menées de front (défaut : 1)
- `--config` : fichier de configuration (défaut : loom.yaml)
- `--profile` (choix : dev, prod) : profil actif ; l'emporte sur LOOM_PROFILE et sur la config
- `--env-file` : fichier de variables à lire, au lieu de 'env_file:' de la config ou du .env voisin de la config (une variable déjà définie l'emporte)
- `--no-env-file` : ne lit aucun fichier de variables
