# Commande loom : options globales et liste des commandes

```text
usage: loom [-h] [--version] [--config CONFIG] [--profile {dev,prod}] [--env-file ENV_FILE |
            --no-env-file]
            {validate,run,state,resume,replay,inspect,eval,approve,reject,answer,serve,mcp,keys,report,sessions,worker,retention,storage,memory,schema} ...
```

L'analyseur de la ligne de commande : options globales, puis une sous-commande par action.

Sous-commandes : `validate`, `run`, `state`, `resume`, `replay`,
`inspect`, `eval`, `approve`, `reject`, `answer`, `serve`,
`mcp`, `keys create`, `report`, `sessions` (`list`, `show`,
`files`, `export`, `delete`), `worker`, `retention`,
`storage sql`, `memory` (`export`, `import`, `reindex`, `eval`)
et `schema`. Les commandes qui agissent prennent `--user` (à défaut
`LOOM_USER`), celles qui visent un client `--tenant`. Les options
globales (`--config`, `--profile`, `--env-file`, `--no-env-file`)
se lisent avant comme après la sous-commande, où elles l'emportent.

Options globales :

- `--version` : show program's version number and exit
- `--config` (défaut : loom-boot.yaml) : amorce, qui dit où lire la config (défaut : loom-boot.yaml)
- `--profile` (choix : dev, prod) : profil actif ; l'emporte sur LOOM_PROFILE et sur l'amorce
- `--env-file` : fichier de variables à lire, au lieu de 'env_file:' de l'amorce ou du .env voisin de l'amorce (une variable déjà définie l'emporte)
- `--no-env-file` : ne lit aucun fichier de variables

Commandes :

- `loom validate` : vérifie la config et monte les agents
- `loom run` : lance un run et attend sa fin
- `loom state` : state d'une session : ce que l'application et les outils y ont écrit
- `loom resume` : reprend un run interrompu
- `loom replay` : rejoue un run fini, à l'identique (sans appel) ou en variante, et dit où il s'écarte
- `loom inspect` : relit un run : son arbre (étapes, modèles, outils, sous-runs), sa réponse et son bilan
- `loom eval` : joue une suite d'évals : des cas et leurs attendus, pour chaque variante, en mémoire (outils à effets de bord doublés ou refusés)
- `loom approve` : autorise un appel que le run attend
- `loom reject` : refuse un appel que le run attend
- `loom answer` : répond à une question que le run attend
- `loom serve` : sert l'API REST
- `loom mcp` : sert les agents en MCP, sur stdio
- `loom keys` : clés d'API de l'accès REST
- `loom report` : consommation d'un run, d'une session, ou d'un client sur une période
- `loom sessions` : journaux de session : lister, exporter, supprimer
- `loom worker` : consomme la file des tâches de fond (runs, reprises, résumés)
- `loom retention` : efface les sessions dormantes au-delà de la borne déclarée (RGPD)
- `loom storage` : stockages de service : le SQL à appliquer
- `loom memory` : entretient la mémoire d'un agent (loom.memory) : export, import…
- `loom schema` : JSON Schema du fichier de configuration
