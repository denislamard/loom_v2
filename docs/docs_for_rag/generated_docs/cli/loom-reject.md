# Commande loom reject

Refuse un appel que le run attend.

```text
[1;34musage: [0m[1;35mloom reject[0m [[32m-h[0m] [[36m--tenant [33mTENANT[0m] [[36m--user [33mUTILISATEUR[0m] [[36m--call [33mCALL_ID[0m] [[36m--reason [33mREASON[0m]
                   [[36m--session [33mSESSION[0m] [[36m--no-wait[0m] [[36m--json[0m] [[36m--config [33mCONFIG[0m]
                   [[36m--profile [33m{dev,prod}[0m] [[36m--env-file [33mENV_FILE[0m | [36m--no-env-file[0m]
                   [32mrun_id[0m
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
- `--config` : fichier de configuration (défaut : loom.yaml)
- `--profile` (choix : dev, prod) : profil actif ; l'emporte sur LOOM_PROFILE et sur la config
- `--env-file` : fichier de variables à lire, au lieu de 'env_file:' de la config ou du .env voisin de la config (une variable déjà définie l'emporte)
- `--no-env-file` : ne lit aucun fichier de variables
