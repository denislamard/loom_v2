# Commande loom worker

Consomme la file des tâches de fond (runs, reprises, résumés).

```text
usage: loom worker [-h] [--jobs N] [--config CONFIG] [--profile {dev,prod}] [--env-file ENV_FILE |
                   --no-env-file]
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
- `--config` : amorce, qui dit où lire la config (défaut : loom-boot.yaml)
- `--profile` (choix : dev, prod) : profil actif ; l'emporte sur LOOM_PROFILE et sur l'amorce
- `--env-file` : fichier de variables à lire, au lieu de 'env_file:' de l'amorce ou du .env voisin de l'amorce (une variable déjà définie l'emporte)
- `--no-env-file` : ne lit aucun fichier de variables
