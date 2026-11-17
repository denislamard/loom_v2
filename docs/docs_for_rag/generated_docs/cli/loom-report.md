# Commande loom report

Consommation d'un run, d'une session, ou d'un client sur une période.

```text
[1;34musage: [0m[1;35mloom report[0m [[32m-h[0m] [[36m--tenant [33mTENANT[0m] [[36m--session [33mSESSION[0m] [[36m--period [33m{day,month}[0m] [[36m--json[0m]
                   [[36m--config [33mCONFIG[0m] [[36m--profile [33m{dev,prod}[0m] [[36m--env-file [33mENV_FILE[0m | [36m--no-env-file[0m]
                   [32m[run_id][0m
```

`loom report` : consommation d'un run, d'une session, ou du client sur une période.

Un run (`run_id`), une session (`--session`), ou le client sur la
journée ou le mois en cours (`--period day|month`).

`--json` pour le rapport en JSON.

**Arguments :**

- `args` : Arguments de la commande.

**Renvoie :**

0 ; 1 pour un run inconnu ; 2 sans run, session ni période.

Arguments et options de `loom report` :

- `--tenant` : client au nom duquel agir (défaut : celui de la config, sinon 'default')
- `run_id` : (sans aide)
- `--session` : journal du run, ou session
- `--period, --periode` (choix : day, month) : consommation du client sur la journée (day) ou le mois (month) en cours ; 'jour' et 'mois', l'ancienne écriture, restent compris
- `--json` : affiche le rapport en JSON
- `--config` : fichier de configuration (défaut : loom.yaml)
- `--profile` (choix : dev, prod) : profil actif ; l'emporte sur LOOM_PROFILE et sur la config
- `--env-file` : fichier de variables à lire, au lieu de 'env_file:' de la config ou du .env voisin de la config (une variable déjà définie l'emporte)
- `--no-env-file` : ne lit aucun fichier de variables
