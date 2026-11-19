# Commande loom reject

Refuse un appel que le run attend.

```text
usage: loom reject [-h] [--tenant TENANT] [--user UTILISATEUR] [--call CALL_ID] [--reason REASON]
                   [--session SESSION] [--no-wait] [--json] [--config CONFIG]
                   [--profile {dev,prod}] [--env-file ENV_FILE | --no-env-file]
                   run_id
```

Tranche ce qu'un run attend, puis pilote sa reprise (point 17).

La décision met un travail `resume` en file **dans cette instance** :
sans `--no-wait`, c'est donc ce terminal qui mène le run à son terme et
en affiche la réponse. Avec, la décision est écrite au journal et la
commande sort sans rien reprendre ni attendre : la reprise revient à qui
écoute ailleurs — un worker (la file d'un courtier a reçu le travail),
`loom resume`, ou la reprise au démarrage d'un worker.

Avec une file servie par un courtier (`storage.queue.backend: rabbitmq`),
le travail de reprise part au worker : ce terminal n'a rien à attendre, et
relire le run aussitôt montrerait encore la pause (essai de la fonction H7, 21/10 :
« Statut : paused », code 1, pendant que le worker concluait). La commande
dit donc à qui la reprise est confiée et sort en 0, comme avec `--no-wait`.

Arguments et options de `loom reject` :

- `--tenant` : client au nom duquel agir (défaut : celui de la config, sinon 'default')
- `--user` : utilisateur déclaré dans security.users (défaut : LOOM_USER) ; le client est le sien
- `run_id` : (sans aide)
- `--call` : appel visé ; sans lui, tout ce que le run attend est tranché
- `--reason` (défaut : ) : motif de la décision
- `--session` : journal du run
- `--no-wait` : écrit la décision et sort, sans piloter la reprise
- `--json` : affiche le résultat en JSON
- `--config` : amorce, qui dit où lire la config (défaut : loom-boot.yaml)
- `--profile` (choix : dev, prod) : profil actif ; l'emporte sur LOOM_PROFILE et sur l'amorce
- `--env-file` : fichier de variables à lire, au lieu de 'env_file:' de l'amorce ou du .env voisin de l'amorce (une variable déjà définie l'emporte)
- `--no-env-file` : ne lit aucun fichier de variables
