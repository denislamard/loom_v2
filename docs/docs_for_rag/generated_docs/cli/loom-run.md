# Commande loom run

Lance un run et attend sa fin.

```text
usage: loom run [-h] [--tenant TENANT] [--user UTILISATEUR] [--stream] [--json] [-q] [--onlydata]
                [--session SESSION] [--run-id RUN_ID] [--attach FICHIER]
                [--judges {auto,force,skip}] [--state CLE=VALEUR] [--config CONFIG]
                [--profile {dev,prod}] [--env-file ENV_FILE | --no-env-file]
                agent message
```

`loom run <agent> <message>` : lance un run et attend sa fin ; la réponse sur stdout, le bilan sur stderr.

Options : `--session` (journal où rattacher le run), `--run-id`,
`--attach` (image jointe, répétable), `--state CLE=VALEUR` (state posé à
l'ouverture, répétable), `--judges auto|force|skip`, `--stream`
(réponse et déroulé au fil de l'eau), `--json`, `--json --onlydata`
(seulement `data`), `-q` (bilan tu). Sans `--run-id`, dans une
session, le message peut répondre à une question qu'un run attend.

**Arguments :**

- `args` : Arguments de la commande.

**Renvoie :**

0 si le run a abouti, ou attend une réponse à une question ; 1 sinon ;
2 pour une demande refusée (`--onlydata` sans `--json`, pièce jointe
illisible, clé de `--state` donnée deux fois).

Arguments et options de `loom run` :

- `--tenant` : client au nom duquel agir (défaut : celui de la config, sinon 'default')
- `--user` : utilisateur déclaré dans security.users (défaut : LOOM_USER) ; le client est le sien
- `agent` : (sans aide)
- `message` : (sans aide)
- `--stream` : affiche la réponse au fil de l'eau
- `--json` : affiche le résultat en JSON
- `-q, --quiet` : n'affiche que la réponse : le bilan se tait, sauf une erreur ou une approbation en attente
- `--onlydata` : avec --json : n'affiche que data, la réponse validée par le schéma du contrat de sortie ; sans data, rien sur stdout et le code 1
- `--session` : journal auquel rattacher le run
- `--run-id` : identifiant choisi pour le run
- `--attach` : image jointe à la demande (répétable)
- `--judges` (choix : auto, force, skip ; défaut : auto) : juges : selon leur 'when' (auto), tous (force) ou aucun (skip)
- `--state` : state de la session posé à l'ouverture (répétable) : la valeur en JSON (3, true, null qui efface, "texte"), sinon telle quelle
- `--config` : amorce, qui dit où lire la config (défaut : loom-boot.yaml)
- `--profile` (choix : dev, prod) : profil actif ; l'emporte sur LOOM_PROFILE et sur l'amorce
- `--env-file` : fichier de variables à lire, au lieu de 'env_file:' de l'amorce ou du .env voisin de l'amorce (une variable déjà définie l'emporte)
- `--no-env-file` : ne lit aucun fichier de variables
