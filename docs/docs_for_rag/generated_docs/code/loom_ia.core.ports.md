# loom_ia.core.ports

Paquet `loom_ia.core.ports` de loom_v2 (fichier `src/loom_ia/core/ports/__init__.py`).

Ports : les contrats entre le moteur et l'extérieur, que remplissent les adaptateurs.

Le moteur ne connaît que ces contrats ; `loom_ia.adapters` les remplit, et
`runtime/wiring.py` choisit l'adaptateur selon la config.

- `event_store` : le journal (`EventStore`) — mémoire, JSONL, SQLite,
  Postgres (`storage.events`) ;
- `bus` : les nouvelles d'écriture entre process (`EventBus`) — mémoire,
  Postgres, Redis (`storage.bus`) ;
- `queue` : la file des tâches de fond (`TaskQueue`) — asyncio, RabbitMQ
  (`storage.queue`) ;
- `artifact_store` : les fichiers hors journal (`ArtifactStore`) — mémoire,
  dossier local (`storage.artifacts`) ;
- `idempotency` : la mémoire des effets déjà produits
  (`IdempotencyStore`) — journal, mémoire, SQLite, Postgres, Redis
  (`storage.idempotency`) ;
- `cipher` : le sceau des contenus au repos (`Cipher`, `Keyring`) —
  AES-256-GCM (`storage.encryption`) ;
- `model_client` : l'appel d'un modèle (`ModelClient`) — Anthropic,
  OpenAI (chat et responses), modèle simulé ;
- `tool` : l'outil (`Tool`) et la source d'outils (`ToolSource`) ;
- `policy` : la politique branchée aux points d'accroche (`Policy`) ;
- `secrets` : la table des secrets d'un client (`SecretProvider`) ;
- `usage_counter` : la consommation d'un client par période
  (`UsageCounter`) ;
- `exchanges` : l'enregistrement des échanges HTTP bruts avec un
  fournisseur.
