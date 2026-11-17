# loom.yaml : la clé storage

La clé `storage` de `loom.yaml` et tout ce qu'elle contient.

## `storage`

Où loom range ce qu'il garde (`storage`), à la racine ou pour un client.

Un chiffrement exige un journal durable ; un journal `postgres` exige
de déclarer `artifacts`.

Modèle Pydantic : `loom_ia.config.models.StorageConfig`.

- `storage.events` (type `EventsStorage` ; défaut : les défauts de la section (`backend: "memory"`, `role: "loom_app"`, `pool.min_size: 1`, `pool.max_size: 10`, `pool.command_timeout: 60.0`, `pool.lock_timeout: 10.0`, `pool.acquire_timeout: 30.0`)) : Le journal des événements.
- `storage.artifacts` (type `ArtifactsStorage` ; défaut : les défauts de la section) : Le stockage des fichiers.
- `storage.idempotency` (type `IdempotencyStorage` ; défaut : les défauts de la section (`backend: "journal"`, `role: "loom_app"`, `pool.min_size: 1`, `pool.max_size: 10`, `pool.command_timeout: 60.0`, `pool.lock_timeout: 10.0`, `pool.acquire_timeout: 30.0`)) : Le magasin d'idempotence.
- `storage.queue` (type `QueueStorage` ; défaut : les défauts de la section (`backend: "asyncio"`, `max_running: 16`)) : La file des tâches de fond.
- `storage.bus` (type `BusStorage` ; défaut : les défauts de la section (`backend: "memory"`)) : Le bus des nouvelles d'écriture entre process.
- `storage.encryption` (type `EncryptionStorage | null` ; défaut : `null`) : Sceau des contenus au repos. Sans ce bloc, les contenus sont rangés en clair : c'est le journal de toujours, et l'isolation par client, les portées de clés et la suppression RGPD restent ce qui les protège.
- `storage.retention` (type `RetentionStorage` ; défaut : les défauts de la section) : Rétention des sessions dormantes ; sans borne, rien ne s'efface.

## `storage.events`

Journal des événements (`storage.events`).

Backends : `memory` (défaut, perdu à l'arrêt), `jsonl` (un fichier par
session sous `path`), `sqlite` (une base en fichier), `postgres`
(DSN lu dans `dsn_env`). `path` et `dsn_env` ne valent que pour leur
backend ; `pool` seulement pour `postgres`.

Exemple :

```
storage:
  events: {backend: jsonl, path: .loom/journal}
```

Modèle Pydantic : `loom_ia.config.models.EventsStorage`.

- `storage.events.backend` (type `str` ; défaut : `"memory"`) : `memory`, `jsonl`, `sqlite` ou `postgres`.
- `storage.events.path` (type `Path | null` ; défaut : `null`) : `jsonl` : dossier des journaux. `sqlite` : fichier de la base. Relatif au fichier de config ; obligatoire pour ces deux backends.
- `storage.events.dsn_env` (type `str | null` ; défaut : `null`) : `postgres` : **nom** de la variable d'environnement qui porte le DSN (obligatoire). La config ne porte jamais un secret, seulement où le lire (conception §16.3).
- `storage.events.role` (type `str | null` ; défaut : `"loom_app"`) : `postgres` : rôle applicatif pris par chaque connexion (`loom_app` par défaut) : c'est à lui que s'applique la politique de lignes, et il n'a pas le droit de modifier un événement écrit. `null` s'en passe — la politique tient encore, par `FORCE`, mais le journal redevient modifiable.
- `storage.events.pool` (type `PostgresPoolSettings` ; défaut : les défauts de la section (`min_size: 1`, `max_size: 10`, `command_timeout: 60.0`, `lock_timeout: 10.0`, `acquire_timeout: 30.0`)) : `postgres` : taille et délais du pool.

## `storage.events.pool`

Taille et délais du pool de connexions d'un stockage Postgres.

Un client qui déclare son propre stockage a son propre pool : ces valeurs
s'appliquent à chacun. Les délais sont en secondes ; ils ne valaient pas
avant (une attente pouvait durer sans fin).

Modèle Pydantic : `loom_ia.config.models.PostgresPoolSettings`.

- `storage.events.pool.min_size` (type `int` ; défaut : `1` ; bornes : ≥ 1) : Connexions ouvertes au moins (1 par défaut).
- `storage.events.pool.max_size` (type `int` ; défaut : `10` ; bornes : ≥ 1) : Connexions ouvertes au plus (10 par défaut) ; au moins `min_size`.
- `storage.events.pool.command_timeout` (type `float` ; défaut : `60.0` ; bornes : > 0) : Durée maximale d'une instruction, en secondes (60 par défaut) ; au-delà, elle est interrompue.
- `storage.events.pool.lock_timeout` (type `float` ; défaut : `10.0` ; bornes : > 0) : Attente d'un verrou (journal d'une session, schéma) avant d'y renoncer, en secondes (10 par défaut).
- `storage.events.pool.acquire_timeout` (type `float` ; défaut : `30.0` ; bornes : > 0) : Attente d'une connexion libre quand les `max_size` sont prises, en secondes (30 par défaut).

## `storage.artifacts`

Stockage des fichiers : pièces jointes, sorties d'outils, résultats déportés (fonction G2).

Sans `backend`, il suit le journal : dossier `.artifacts` sous celui
d'un journal `jsonl`, mémoire pour un journal `memory`.

Modèle Pydantic : `loom_ia.config.models.ArtifactsStorage`.

- `storage.artifacts.backend` (type `"local" | "memory" | null` ; défaut : `null`) : `local` (dossier) ou `memory` ; `None` : suit le journal.
- `storage.artifacts.path` (type `Path | null` ; défaut : `null`) : Dossier du stockage `local`, relatif au fichier de config ; obligatoire quand le journal n'est pas en fichiers.

## `storage.idempotency`

Magasin des clés d'idempotence (`storage.idempotency` ; points 18 et 49).

`journal` (défaut) n'a pas de stockage propre : chaque run garde ses
enregistrements dans son journal, ce qui suffit aux clés techniques d'un
appel. `memory` voit tout un process, mais ne survit pas à sa
fermeture. Une clé **métier** exige d'être vue de partout et de durer :
seuls `sqlite`, `postgres` et `redis` s'en chargent, et le chargement
le vérifie.

Modèle Pydantic : `loom_ia.config.models.IdempotencyStorage`.

- `storage.idempotency.backend` (type `str` ; défaut : `"journal"`) : `journal`, `memory`, `sqlite`, `postgres` ou `redis`.
- `storage.idempotency.path` (type `Path | null` ; défaut : `null`) : `sqlite` : fichier de la base (obligatoire), relatif au fichier de config. Sa propre base : ses écritures ne se disputent pas le verrou du journal.
- `storage.idempotency.dsn_env` (type `str | null` ; défaut : `null`) : `postgres` : nom de la variable qui porte le DSN (obligatoire). Peut être celle du journal — les deux tables cohabitent sans se gêner, Postgres ne se verrouille pas par fichier.
- `storage.idempotency.url_env` (type `str | null` ; défaut : `null`) : `redis` : nom de la variable qui porte l'URL du serveur.
- `storage.idempotency.role` (type `str | null` ; défaut : `"loom_app"`) : `postgres` : rôle applicatif des connexions (`loom_app` par défaut).
- `storage.idempotency.pool` (type `PostgresPoolSettings` ; défaut : les défauts de la section (`min_size: 1`, `max_size: 10`, `command_timeout: 60.0`, `lock_timeout: 10.0`, `acquire_timeout: 30.0`)) : `postgres` : taille et délais du pool.

## `storage.idempotency.pool`

Même forme que `storage.events.pool` (page « loom.yaml : la clé storage »).

## `storage.queue`

File des tâches de fond : runs soumis, reprises, résumés (point 27).

`asyncio` exécute dans le process qui met en file : c'est le mode
librairie, et la durabilité vient du journal, pas de la file. `rabbitmq`
ne fait que **publier** — les tâches tournent dans les workers
(`loom worker`), et un process qui met en file sans worker derrière voit
ses tâches attendre.

Modèle Pydantic : `loom_ia.config.models.QueueStorage`.

- `storage.queue.backend` (type `str` ; défaut : `"asyncio"`) : `asyncio` (défaut) ou `rabbitmq`.
- `storage.queue.url_env` (type `str | null` ; défaut : `null`) : `rabbitmq` : nom de la variable d'environnement qui porte l'URL du courtier (`amqp://…`), obligatoire. La config ne porte jamais un secret (conception §16.3).
- `storage.queue.max_running` (type `int` ; défaut : `16` ; bornes : > 0) : `asyncio` : tâches menées de front (16 par défaut) ; les suivantes attendent leur tour. Une reprise remet tous les runs laissés en plan d'un coup (point 27, fonction H3).

## `storage.bus`

Bus des nouvelles d'écriture, entre les process d'un même service (point 5).

`memory` ne franchit aucune frontière : dans un seul process, le journal
remet déjà ses écritures à ses abonnés. Les deux autres servent dès qu'un
`loom serve` doit suivre un run piloté par un `loom worker`.

Modèle Pydantic : `loom_ia.config.models.BusStorage`.

- `storage.bus.backend` (type `str` ; défaut : `"memory"`) : `memory` (défaut), `postgres` ou `redis`.
- `storage.bus.dsn_env` (type `str | null` ; défaut : `null`) : `postgres` : nom de la variable qui porte le DSN — celle du journal si c'est la même base.
- `storage.bus.url_env` (type `str | null` ; défaut : `null`) : `redis` : nom de la variable qui porte l'URL.

## `storage.encryption`

Sceau des contenus au repos, avec la clé de chaque client (`storage.encryption` ; point 30, conception §11.5).

Déclarer ce bloc scelle la **charge** de chaque événement écrit et les
**octets** de chaque fichier rangé. L'enveloppe du journal reste en clair
— identifiants, horodatage, type, statut, agent, facettes —, si bien que
tout ce qui se filtre continue de se filtrer ; ce qui part, c'est le
contenu.

`keys` ne porte pas de clés mais des **noms de secrets**, comme partout
ailleurs dans la config. Chaque client redirige ce nom vers sa variable
(`secrets: {journal_key: MARTIN_JOURNAL_KEY}`) : c'est ainsi qu'une clé
est propre à un client, et effacer sa variable rend son journal illisible
pour de bon — *crypto-shredding* —, sans toucher à celui du voisin.

Plusieurs noms, dans l'ordre : le premier ferme, tous ouvrent. C'est le
renouvellement d'une clé, l'ancienne restant le temps que les anciens
journaux servent.

Modèle Pydantic : `loom_ia.config.models.EncryptionStorage`.

- `storage.encryption.keys` (type `tuple[str, ...]` ; obligatoire ; bornes : longueur ≥ 1) : Noms des secrets qui portent les clés, un au moins ; la première scelle, les suivantes ne font que lire (rotation).

## `storage.retention`

Combien de temps une session dormante est gardée (`storage.retention` ; point 30, conception §11.5).

`events_days` est un nombre de jours **depuis la dernière écriture** d'une
session. Au-delà, la session part en entier — journal, fichiers, clés
d'idempotence —, comme une suppression RGPD demandée à la main. `null`
(le défaut) n'efface rien : c'est le comportement de toujours.

La borne se décide sur la **marque** d'une session (sa dernière écriture),
jamais sur son contenu : c'est ce qui permet d'effacer un journal scellé
dont la clé a disparu (jalon J5.5b) — là justement où la place est perdue si on ne
peut pas la reprendre. Une session **est donc effacée même si un run y
attendait une approbation** : une borne plus courte que le délai d'une
approbation détruit des approbations en attente, et c'est au déploiement de
ne pas s'y mettre.

Rien ne s'efface tout seul : `loom retention` balaie, et c'est la
plateforme qui le met à l'heure (comme les portes de 5.4c).

Modèle Pydantic : `loom_ia.config.models.RetentionStorage`.

- `storage.retention.events_days` (type `int | null` ; défaut : `null`) : Jours depuis la dernière écriture d'une session au-delà desquels elle est effacée ; `null` : jamais.
