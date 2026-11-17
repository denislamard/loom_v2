# loom_ia.sessions

Paquet `loom_ia.sessions` de loom_v2 (fichier `src/loom_ia/sessions/__init__.py`).

Vie d'une session : historique matérialisé, compaction, nettoyage (fonctions F1 à F5 et F7).

- `snapshot` : vue matérialisée de l'historique (`session.snapshot`), et
  positions où couper le journal sans couper un run (`Cuts`) ;
- `compaction` : résumé des échanges anciens par l'agent interne
  `_compaction` (`session.compacted`), et coupe de sécurité
  (`session.trimmed`).

Config : `sessions` (`snapshot_every`, `compaction`, `media`,
`state`).
