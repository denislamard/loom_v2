# loom.yaml : la clé storage

La clé `storage` de `loom.yaml` et tout ce qu'elle contient.

## `storage`

Où loom range ce qu'il garde (`storage`), à la racine ou pour un client.

Modèle Pydantic : `loom_ia.config.models.StorageConfig`.

- `storage.events` (type `EventsStorage` ; défaut : les défauts de la section) : Le journal des événements.
- `storage.artifacts` (type `ArtifactsStorage` ; défaut : les défauts de la section) : Le stockage des fichiers.
- `storage.idempotency` (type `IdempotencyStorage` ; défaut : les défauts de la section) : Le magasin d'idempotence.
- `storage.queue` (type `QueueStorage` ; défaut : les défauts de la section) : La file des tâches de fond.
- `storage.bus` (type `BusStorage` ; défaut : les défauts de la section) : Le bus des nouvelles d'écriture entre process.
- `storage.encryption` (type `EncryptionStorage | null` ; défaut : `null`) : Sceau des contenus au repos. Sans ce bloc, les contenus sont rangés en clair : c'est le journal de toujours, et l'isolation par client, les portées de clés et la suppression RGPD restent ce qui les protège.
- `storage.retention` (type `RetentionStorage` ; défaut : les défauts de la section) : Rétention des sessions dormantes ; sans borne, rien ne s'efface.

## `storage.events`

Journal des événements (`storage.events`).

Modèle Pydantic : `loom_ia.config.models.EventsStorage`.

- `storage.events.backend` (type `str` ; défaut : `"memory"`) : `memory`, `jsonl`, `sqlite` ou `postgres`.
- `storage.events.path` (type `Path | null` ; défaut : `null`) : `jsonl` : dossier des journaux. `sqlite` : fichier de la base. Relatif au fichier de config ; obligatoire pour ces deux backends.
- `storage.events.dsn_env` (type `str | null` ; défaut : `null`) : `postgres` : **nom** de la variable d'environnement qui porte le DSN (obligatoire). La config ne porte jamais un secret, seulement où le lire (conception §16.3).
- `storage.events.role` (type `str | null` ; défaut : `"loom_app"`) : `postgres` : rôle applicatif pris par chaque connexion (`loom_app` par défaut) : c'est à lui que s'applique la politique de lignes, et il n'a pas le droit de modifier un événement écrit. `null` s'en passe — la politique tient encore, par `FORCE`, mais le journal redevient modifiable.
- `storage.events.pool` (type `PostgresPoolSettings` ; défaut : les défauts de la section) : `postgres` : taille et délais du pool.

## `storage.events.pool`

Taille et délais du pool de connexions d'un stockage Postgres.

Modèle Pydantic : `loom_ia.config.models.PostgresPoolSettings`.

- `storage.events.pool.min_size` (type `int` ; défaut : `1` ; bornes : ≥ 1) : Connexions ouvertes au moins (1 par défaut).
- `storage.events.pool.max_size` (type `int` ; défaut : `10` ; bornes : ≥ 1) : Connexions ouvertes au plus (10 par défaut) ; au moins `min_size`.
- `storage.events.pool.command_timeout` (type `float` ; défaut : `60.0` ; bornes : > 0) : Durée maximale d'une instruction, en secondes (60 par défaut) ; au-delà, elle est interrompue.
- `storage.events.pool.lock_timeout` (type `float` ; défaut : `10.0` ; bornes : > 0) : Attente d'un verrou (journal d'une session, schéma) avant d'y renoncer, en secondes (10 par défaut).
- `storage.events.pool.acquire_timeout` (type `float` ; défaut : `30.0` ; bornes : > 0) : Attente d'une connexion libre quand les `max_size` sont prises, en secondes (30 par défaut).

## `storage.artifacts`

Stockage des fichiers : pièces jointes, sorties d'outils, résultats déportés (fonction G2).

Modèle Pydantic : `loom_ia.config.models.ArtifactsStorage`.

- `storage.artifacts.backend` (type `"local" | "memory" | null` ; défaut : `null`) : `local` (dossier) ou `memory` ; `None` : suit le journal.
- `storage.artifacts.path` (type `Path | null` ; défaut : `null`) : Dossier du stockage `local`, relatif au fichier de config ; obligatoire quand le journal n'est pas en fichiers.

## `storage.idempotency`

Magasin des clés d'idempotence (`storage.idempotency` ; points 18 et 49).

Modèle Pydantic : `loom_ia.config.models.IdempotencyStorage`.

- `storage.idempotency.backend` (type `str` ; défaut : `"journal"`) : `journal`, `memory`, `sqlite`, `postgres` ou `redis`.
- `storage.idempotency.path` (type `Path | null` ; défaut : `null`) : `sqlite` : fichier de la base (obligatoire), relatif au fichier de config. Sa propre base : ses écritures ne se disputent pas le verrou du journal.
- `storage.idempotency.dsn_env` (type `str | null` ; défaut : `null`) : `postgres` : nom de la variable qui porte le DSN (obligatoire). Peut être celle du journal — les deux tables cohabitent sans se gêner, Postgres ne se verrouille pas par fichier.
- `storage.idempotency.url_env` (type `str | null` ; défaut : `null`) : `redis` : nom de la variable qui porte l'URL du serveur.
- `storage.idempotency.role` (type `str | null` ; défaut : `"loom_app"`) : `postgres` : rôle applicatif des connexions (`loom_app` par défaut).
- `storage.idempotency.pool` (type `PostgresPoolSettings` ; défaut : les défauts de la section) : `postgres` : taille et délais du pool.

## `storage.idempotency.pool`

Même forme que `storage.events.pool` (page « loom.yaml : la clé storage »).

## `storage.queue`

File des tâches de fond : runs soumis, reprises, résumés (point 27).

Modèle Pydantic : `loom_ia.config.models.QueueStorage`.

- `storage.queue.backend` (type `str` ; défaut : `"asyncio"`) : `asyncio` (défaut) ou `rabbitmq`.
- `storage.queue.url_env` (type `str | null` ; défaut : `null`) : `rabbitmq` : nom de la variable d'environnement qui porte l'URL du courtier (`amqp://…`), obligatoire. La config ne porte jamais un secret (conception §16.3).
- `storage.queue.max_running` (type `int` ; défaut : `16` ; bornes : > 0) : `asyncio` : tâches menées de front (16 par défaut) ; les suivantes attendent leur tour. Une reprise remet tous les runs laissés en plan d'un coup (point 27, fonction H3).

## `storage.bus`

Bus des nouvelles d'écriture, entre les process d'un même service (point 5).

Modèle Pydantic : `loom_ia.config.models.BusStorage`.

- `storage.bus.backend` (type `str` ; défaut : `"memory"`) : `memory` (défaut), `postgres` ou `redis`.
- `storage.bus.dsn_env` (type `str | null` ; défaut : `null`) : `postgres` : nom de la variable qui porte le DSN — celle du journal si c'est la même base.
- `storage.bus.url_env` (type `str | null` ; défaut : `null`) : `redis` : nom de la variable qui porte l'URL.

## `storage.encryption`

Sceau des contenus au repos, avec la clé de chaque client (`storage.encryption` ; point 30, conception §11.5).

Modèle Pydantic : `loom_ia.config.models.EncryptionStorage`.

- `storage.encryption.keys` (type `tuple[str, ...]` ; obligatoire ; bornes : longueur ≥ 1) : Noms des secrets qui portent les clés, un au moins ; la première scelle, les suivantes ne font que lire (rotation).

## `storage.retention`

Combien de temps une session dormante est gardée (`storage.retention` ; point 30, conception §11.5).

Modèle Pydantic : `loom_ia.config.models.RetentionStorage`.

- `storage.retention.events_days` (type `int | null` ; défaut : `null`) : Jours depuis la dernière écriture d'une session au-delà desquels elle est effacée ; `null` : jamais.
