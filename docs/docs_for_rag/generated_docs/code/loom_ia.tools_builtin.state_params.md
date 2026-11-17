# loom_ia.tools_builtin.state_params

Module `loom_ia.tools_builtin.state_params` de loom_v2 (fichier `src/loom_ia/tools_builtin/state_params.py`).

Paramètres de `loom.state` : aucun.

Ce que l'outil écrit se déclare sur les clés du state de l'agent
(`writers: [model]` et `extract:`), pas sur sa référence :

    tools:
    - python: loom.state

Module léger — Pydantic seulement — : la config l'importe pour valider la
référence au chargement, sans importer l'outil.

## Classe `loom_ia.tools_builtin.state_params.StateParams`

```python
class StateParams(BaseModel)
```

Aucun paramètre : une clé inconnue est refusée.
