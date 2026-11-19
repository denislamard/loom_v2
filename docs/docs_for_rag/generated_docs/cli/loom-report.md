# Commande loom report

Consommation d'un run, d'une session, ou d'un client sur une période.

```text
usage: loom report [-h] [--tenant TENANT] [--session SESSION] [--period {day,month}] [--json]
                   [--config CONFIG] [--profile {dev,prod}] [--env-file ENV_FILE | --no-env-file]
                   [run_id]
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
- `--config` : amorce, qui dit où lire la config (défaut : loom-boot.yaml)
- `--profile` (choix : dev, prod) : profil actif ; l'emporte sur LOOM_PROFILE et sur l'amorce
- `--env-file` : fichier de variables à lire, au lieu de 'env_file:' de l'amorce ou du .env voisin de l'amorce (une variable déjà définie l'emporte)
- `--no-env-file` : ne lit aucun fichier de variables
