# Commande loom replay

Rejoue un run fini, à l'identique (sans appel) ou en variante, et dit où il s'écarte.

```text
usage: loom replay [-h] [--tenant TENANT] [--session SESSION] [--journal FICHIER]
                   [--mode {exact,variant}] [--model ETAPE=MODELE] [--double OUTIL=REF]
                   [--export FICHIER] [--json] [--config CONFIG] [--profile {dev,prod}]
                   [--env-file ENV_FILE | --no-env-file]
                   [run_id]
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
