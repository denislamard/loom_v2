# Commande loom retention

Efface les sessions dormantes au-delà de la borne déclarée (RGPD).

```text
[1;34musage: [0m[1;35mloom retention[0m [[32m-h[0m] [[36m--tenant [33mTENANT[0m] [[36m--yes[0m] [[36m--json[0m] [[36m--config [33mCONFIG[0m]
                      [[36m--profile [33m{dev,prod}[0m] [[36m--env-file [33mENV_FILE[0m | [36m--no-env-file[0m]
```

Balaie les sessions dormantes ; sans `--yes`, dit seulement ce qui partirait.

L'essai à blanc est le défaut, et il n'y a pas de question posée : cette
commande est faite pour être mise à l'heure par une plateforme, et une
question sans personne pour y répondre bloquerait le balayage.

Arguments et options de `loom retention` :

- `--tenant` : un seul client (défaut : tous les clients déclarés)
- `--yes` : supprime pour de bon ; sans cette option, rien n'est touché
- `--json` : affiche le rapport en JSON
- `--config` : fichier de configuration (défaut : loom.yaml)
- `--profile` (choix : dev, prod) : profil actif ; l'emporte sur LOOM_PROFILE et sur la config
- `--env-file` : fichier de variables à lire, au lieu de 'env_file:' de la config ou du .env voisin de la config (une variable déjà définie l'emporte)
- `--no-env-file` : ne lit aucun fichier de variables
