# Commande loom eval

Joue une suite d'évals : des cas et leurs attendus, pour chaque variante, en mémoire (outils à effets de bord doublés ou refusés).

```text
usage: loom eval [-h] [--case NOM] [--variant NOM] [--export DOSSIER] [--json] [--config CONFIG]
                 [--profile {dev,prod}] [--env-file ENV_FILE | --no-env-file]
                 suite
```

Joue une suite d'évals ; 0 si tout passe, 1 si un attendu tombe, 2 si elle est injouable.

Un run non joué (plafond de dépense atteint) rend aussi 1 : ce qu'il
devait éprouver ne l'a pas été. Sans `config` dans la suite, c'est
`--config` qui sert.

Arguments et options de `loom eval` :

- `suite` : fichier YAML de la suite
- `--case` (défaut : []) : ne joue que ce cas (répétable)
- `--variant` (défaut : []) : ne joue que cette variante (répétable)
- `--export` : garde le journal de chaque run, un JSONL par run (<variante>--<cas>--<n>.jsonl)
- `--json` : affiche le rapport en JSON
- `--config` : amorce, qui dit où lire la config (défaut : loom-boot.yaml)
- `--profile` (choix : dev, prod) : profil actif ; l'emporte sur LOOM_PROFILE et sur l'amorce
- `--env-file` : fichier de variables à lire, au lieu de 'env_file:' de l'amorce ou du .env voisin de l'amorce (une variable déjà définie l'emporte)
- `--no-env-file` : ne lit aucun fichier de variables
