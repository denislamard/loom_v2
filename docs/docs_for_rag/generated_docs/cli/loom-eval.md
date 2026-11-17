# Commande loom eval

Joue une suite d'évals : des cas et leurs attendus, pour chaque variante, en mémoire (outils à effets de bord doublés ou refusés).

```text
[1;34musage: [0m[1;35mloom eval[0m [[32m-h[0m] [[36m--case [33mNOM[0m] [[36m--variant [33mNOM[0m] [[36m--export [33mDOSSIER[0m] [[36m--json[0m] [[36m--config [33mCONFIG[0m]
                 [[36m--profile [33m{dev,prod}[0m] [[36m--env-file [33mENV_FILE[0m | [36m--no-env-file[0m]
                 [32msuite[0m
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
- `--config` : fichier de configuration (défaut : loom.yaml)
- `--profile` (choix : dev, prod) : profil actif ; l'emporte sur LOOM_PROFILE et sur la config
- `--env-file` : fichier de variables à lire, au lieu de 'env_file:' de la config ou du .env voisin de la config (une variable déjà définie l'emporte)
- `--no-env-file` : ne lit aucun fichier de variables
