# loom_ia.config

Paquet `loom_ia.config` de loom_v2 (fichier `src/loom_ia/config/__init__.py`).

Configuration : schéma Pydantic, lecture YAML, références Python (fonctions M1 à M3).

- `models` : le schéma de `loom.yaml` (`LoomConfig`) et de l'amorce
  `loom-boot.yaml` (`BootConfig`) ;
- `loader` : `load_config`, qui lit `loom.yaml`, les agents, les
  prompts et les skills, applique le profil et contrôle le tout ;
- `env_file` : le fichier d'environnement du projet (`.env`) ;
- `references` : `imports`, registre des outils, `module:attr` ;
- `telemetry` : capture, masquage et collecteurs ;
- `compaction` : l'agent interne `_compaction` ;
- `keys` : clés d'API et leur empreinte ;
- `schema` : le JSON Schema publié pour l'éditeur (`loom schema`) ;
- `errors` : `ConfigError`, des messages qui nomment fichier et chemin.
