# Commande loom replay

Rejoue un run fini, à l'identique (sans appel) ou en variante, et dit où il s'écarte.

```text
[1;34musage: [0m[1;35mloom replay[0m [[32m-h[0m] [[36m--tenant [33mTENANT[0m] [[36m--session [33mSESSION[0m] [[36m--journal [33mFICHIER[0m]
                   [[36m--mode [33m{exact,variant}[0m] [[36m--model [33mETAPE=MODELE[0m] [[36m--double [33mOUTIL=REF[0m]
                   [[36m--export [33mFICHIER[0m] [[36m--json[0m] [[36m--config [33mCONFIG[0m] [[36m--profile [33m{dev,prod}[0m]
                   [[36m--env-file [33mENV_FILE[0m | [36m--no-env-file[0m]
                   [32m[run_id][0m
```

Rejoue un run ; rend 1 s'il diverge (ou si la variante échoue), 2 s'il ne peut pas l'être.

Le code de sortie fait de la commande un garde-fou : une config qui ne
reproduit plus un run de référence se voit en CI, sans lire la sortie. En
variante, s'écarter est le but : le code dit seulement si la variante est
allée au bout (0) ou a échoué (1). Avec `--journal` (jalon J6.3b), chaque run
fini du fichier est rejoué ; un seul qui diverge, ou un run inachevé,
rend 1.

Arguments et options de `loom replay` :

- `--tenant` : client au nom duquel agir (défaut : celui de la config, sinon 'default')
- `run_id` : (sans aide)
- `--session` : journal du run
- `--journal` : rejoue depuis un journal JSONL exporté (sessions export, eval --export), hors du journal de la config ; sans run_id, chacun de ses runs finis
- `--mode` (choix : exact, variant ; défaut : exact) : exact : tout vient du journal ; variant : ce qu'il ne connaît pas part pour de vrai, et les deux runs sont comparés
- `--model` (défaut : []) : variante : autre modèle déclaré pour une étape (main, un rôle, judge:<nom>)
- `--double` (défaut : []) : variante : doublure d'un outil (nom enregistré ou module:fonction), appelée à la place d'un appel absent du journal
- `--export` : garde le journal du rejeu en JSONL (rien n'est écrit dans le journal)
- `--json` : affiche le rapport en JSON
- `--config` : fichier de configuration (défaut : loom.yaml)
- `--profile` (choix : dev, prod) : profil actif ; l'emporte sur LOOM_PROFILE et sur la config
- `--env-file` : fichier de variables à lire, au lieu de 'env_file:' de la config ou du .env voisin de la config (une variable déjà définie l'emporte)
- `--no-env-file` : ne lit aucun fichier de variables
