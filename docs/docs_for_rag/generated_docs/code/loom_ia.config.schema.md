# loom_ia.config.schema

Module `loom_ia.config.schema` de loom_v2 (fichier `src/loom_ia/config/schema.py`).

JSON Schema de la config, pour l'éditeur (point 35).

loom-ia ne lit jamais ce schéma : il valide avec Pydantic. Le fichier sert à
la complétion et aux erreurs dans l'éditeur :

\    # yaml-language-server: $schema=./loom.schema.json

## Fonction `loom_ia.config.schema.config_json_schema`

```python
def config_json_schema() -> dict[str, Any]
```

Schéma du fichier racine `loom.yaml`.

**Renvoie :**

Le JSON Schema de `LoomConfig`, en mode validation.

## Fonction `loom_ia.config.schema.agent_json_schema`

```python
def agent_json_schema() -> dict[str, Any]
```

Schéma d'un fichier d'agent (`agents/*.yaml`).

**Renvoie :**

Le JSON Schema de `AgentSpec`, en mode validation.
