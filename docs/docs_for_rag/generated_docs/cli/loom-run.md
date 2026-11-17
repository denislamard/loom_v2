# Commande loom run

Lance un run et attend sa fin.

```text
[1;34musage: [0m[1;35mloom run[0m [[32m-h[0m] [[36m--tenant [33mTENANT[0m] [[36m--user [33mUTILISATEUR[0m] [[36m--stream[0m] [[36m--json[0m] [[32m-q[0m] [[36m--onlydata[0m]
                [[36m--session [33mSESSION[0m] [[36m--run-id [33mRUN_ID[0m] [[36m--attach [33mFICHIER[0m]
                [[36m--judges [33m{auto,force,skip}[0m] [[36m--state [33mCLE=VALEUR[0m] [[36m--config [33mCONFIG[0m]
                [[36m--profile [33m{dev,prod}[0m] [[36m--env-file [33mENV_FILE[0m | [36m--no-env-file[0m]
                [32magent[0m [32mmessage[0m
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
- `--config` : fichier de configuration (défaut : loom.yaml)
- `--profile` (choix : dev, prod) : profil actif ; l'emporte sur LOOM_PROFILE et sur la config
- `--env-file` : fichier de variables à lire, au lieu de 'env_file:' de la config ou du .env voisin de la config (une variable déjà définie l'emporte)
- `--no-env-file` : ne lit aucun fichier de variables
