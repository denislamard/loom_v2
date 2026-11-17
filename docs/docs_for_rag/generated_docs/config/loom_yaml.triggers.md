# loom.yaml : la clé triggers

La clé `triggers` de `loom.yaml` et tout ce qu'elle contient.

## `triggers[]`

Une porte d'entrée déclarée (`triggers`) : ce qu'un appel extérieur ouvre (fonction H6, jalon J5.4c).

Modèle Pydantic : `loom_ia.config.models.TriggerSpec`.

- `triggers[].name` (type `str` ; obligatoire ; bornes : motif `^[a-z0-9][a-z0-9_-]{0,63}$`) : Dernier segment de la route (`POST /v1/hooks/<name>`) : un identifiant sûr, qui sert aussi de facette au journal.
- `triggers[].agent` (type `str` ; obligatoire) : Agent lancé.
- `triggers[].message` (type `str` ; obligatoire) : Gabarit du message, rendu sur `{payload, trigger, delivery}`. Une variable absente donne une chaîne vide (point 50) : une charge incomplète ne fait pas échouer la livraison, elle fait un message plus pauvre.
- `triggers[].session` (type `str | null` ; défaut : `null`) : Gabarit de la session, pour rattacher les livraisons d'un même sujet au même journal. Sans lui, chaque run a sa session, comme ailleurs.
- `triggers[].delivery_header` (type `str | null` ; défaut : `null`) : En-tête qui porte l'identifiant de livraison (`X-Delivery-Id`, `Stripe-Id`…). Il devient le `run_id`, si bien qu'une seconde livraison **retrouve** le run au lieu d'en ouvrir un autre.
