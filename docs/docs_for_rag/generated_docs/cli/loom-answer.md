# Commande loom answer

Répond à une question que le run attend.

```text
usage: loom answer [-h] [--tenant TENANT] [--user UTILISATEUR] [--answer RÉPONSE] [--call CALL_ID]
                   [--session SESSION] [--no-wait] [--json] [--config CONFIG]
                   [--profile {dev,prod}] [--env-file ENV_FILE | --no-env-file]
                   run_id [texte]
```

Répond à une question que le run attend (wait_answer), puis pilote sa reprise.

Comme `approve` : la reprise se fait dans ce terminal, sauf `--no-wait`
ou une file servie par un courtier. Plus simple encore : ``loom run <agent>
"<réponse>" --session <session>`` répond aussi, dans la conversation.

Un refus montre la commande à corriger ; une réponse acceptée est répétée
question par question (« Relances → Oui »), pour vérifier ce qui a été
compris avant la reprise (essai réel t-2000 du 02/11, docs/multi-questions.md).

Arguments et options de `loom answer` :

- `--tenant` : client au nom duquel agir (défaut : celui de la config, sinon 'default')
- `--user` : utilisateur déclaré dans security.users (défaut : LOOM_USER) ; le client est le sien
- `run_id` : (sans aide)
- `texte` : la réponse, en message libre
- `--answer` : réponse à une question, dans leur ordre : le libellé ou le numéro d'un choix, ou un texte (répéter ; « ; » sépare plusieurs choix)
- `--call` : question visée
- `--session` : journal du run
- `--no-wait` : écrit la réponse et sort, sans piloter la reprise
- `--json` : affiche le résultat en JSON
- `--config` : fichier de configuration (défaut : loom.yaml)
- `--profile` (choix : dev, prod) : profil actif ; l'emporte sur LOOM_PROFILE et sur la config
- `--env-file` : fichier de variables à lire, au lieu de 'env_file:' de la config ou du .env voisin de la config (une variable déjà définie l'emporte)
- `--no-env-file` : ne lit aucun fichier de variables
