# loom_ia.core.events

Paquet `loom_ia.core.events` de loom_v2 (fichier `src/loom_ia/core/events/__init__.py`).

Événements typés du journal : enveloppe, charges, recherche, masquage et schéma (point 22).

Le journal d'une session est une suite d'événements immuables ; il fait foi,
et tout le reste (historique, état d'un run, traces, consommation, rejeu) en
est déduit par des projections (`core.projections`).

- `envelope` : `Event` (un événement écrit), `EventDraft` (avant
  écriture), `RunScope` (les coordonnées d'un run, qui fabrique ses
  brouillons), `SCHEMA_VERSION` ;
- `payloads` : une charge typée par type d'événement (`run.started`,
  `tool.completed`…) et leur union `DurablePayload` ;
- `query` : `EventQuery`, les critères d'une recherche ;
- `redaction` : le masquage du contenu sans la permission
  `read_content` ;
- `schema` : le JSON Schema d'un événement, contrat de l'interface de
  suivi.
