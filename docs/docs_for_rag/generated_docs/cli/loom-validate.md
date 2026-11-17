# Commande loom validate

Vérifie la config et monte les agents.

```text
[1;34musage: [0m[1;35mloom validate[0m [[32m-h[0m] [[36m--no-keys[0m] [[36m--config [33mCONFIG[0m] [[36m--profile [33m{dev,prod}[0m]
                     [[36m--env-file [33mENV_FILE[0m | [36m--no-env-file[0m]
```

`loom validate` : vérifie la config, monte chaque agent pour chaque client, puis imprime le rapport.

Le verdict passe en tête, puis les sections CONFIGURATION, EXTENSIONS,
EXÉCUTION, ACCÈS et CLIENTS (ou AGENTS), et les avertissements.
`--no-keys` monte les agents sans créer leurs clients de modèle : une clé
d'API ou un SDK absent ne fait pas échouer. Si un montage échoue, le rapport
partiel s'imprime avec un verdict d'échec, avant l'erreur.

**Arguments :**

- `args` : Arguments de la commande.

**Renvoie :**

0 si tout se monte ; une erreur remonte à `main`, qui la traduit.

Arguments et options de `loom validate` :

- `--no-keys, --sans-cles` : monte les agents sans créer leurs clients de modèle : une clé d'API (ou un SDK) absente ne fait pas échouer
- `--config` : fichier de configuration (défaut : loom.yaml)
- `--profile` (choix : dev, prod) : profil actif ; l'emporte sur LOOM_PROFILE et sur la config
- `--env-file` : fichier de variables à lire, au lieu de 'env_file:' de la config ou du .env voisin de la config (une variable déjà définie l'emporte)
- `--no-env-file` : ne lit aucun fichier de variables
