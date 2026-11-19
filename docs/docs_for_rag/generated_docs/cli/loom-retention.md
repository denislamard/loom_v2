# Commande loom retention

Efface les sessions dormantes au-delà de la borne déclarée (RGPD).

```text
usage: loom retention [-h] [--tenant TENANT] [--yes] [--json] [--config CONFIG]
                      [--profile {dev,prod}] [--env-file ENV_FILE | --no-env-file]
```

Balaie les sessions dormantes ; sans `--yes`, dit seulement ce qui partirait.

L'essai à blanc est le défaut, et il n'y a pas de question posée : cette
commande est faite pour être mise à l'heure par une plateforme, et une
question sans personne pour y répondre bloquerait le balayage.

Arguments et options de `loom retention` :

- `--tenant` : un seul client (défaut : tous les clients déclarés)
- `--yes` : supprime pour de bon ; sans cette option, rien n'est touché
- `--json` : affiche le rapport en JSON
- `--config` : amorce, qui dit où lire la config (défaut : loom-boot.yaml)
- `--profile` (choix : dev, prod) : profil actif ; l'emporte sur LOOM_PROFILE et sur l'amorce
- `--env-file` : fichier de variables à lire, au lieu de 'env_file:' de l'amorce ou du .env voisin de l'amorce (une variable déjà définie l'emporte)
- `--no-env-file` : ne lit aucun fichier de variables
