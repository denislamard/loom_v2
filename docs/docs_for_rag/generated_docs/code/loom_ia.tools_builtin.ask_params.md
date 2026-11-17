# loom_ia.tools_builtin.ask_params

Module `loom_ia.tools_builtin.ask_params` de loom_v2 (fichier `src/loom_ia/tools_builtin/ask_params.py`).

Paramètres de `loom.ask` : le délai laissé à l'utilisateur.

    tools:
    - python: loom.ask
      expires_in: 86400        # facultatif : sans réponse après ce délai (s), le modèle l'apprend

Module léger — Pydantic seulement — : la config l'importe pour valider la
référence au chargement, sans importer l'outil.

## Classe `loom_ia.tools_builtin.ask_params.AskParams`

```python
class AskParams(BaseModel)
```

Paramètres de `loom.ask`.

**Attributs :**

- `expires_in` : Délai, en secondes, après lequel une question sans réponse expire et le modèle reprend avec « pas de réponse ». Par défaut, elle attend indéfiniment : la conversation reprend quand l'utilisateur revient.
