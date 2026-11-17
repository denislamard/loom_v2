# loom_ia.policies.builtin

Module `loom_ia.policies.builtin` de loom_v2 (fichier `src/loom_ia/policies/builtin.py`).

Politiques fournies par loom-ia, référencées par leur nom `loom.…`.

Un agent les branche par `hook: loom.<nom>` dans `policies`.

## Constantes de `loom_ia.policies.builtin`

- `require_tool = FunctionPolicy(_require_tool, name='loom.require_tool', points=['before_model'], decisions=['replace'], builtin=True)` : Politique `loom.require_tool` : point `before_model`, décision `replace`.
- `BUILTIN_POLICIES = {require_tool.name: require_tool}` : Politiques fournies, par nom.

## Fonction `loom_ia.policies.builtin._require_tool`

```python
def _require_tool(subject: BeforeModel) -> Decision
```

Impose un appel d'outil tant que le run n'en a fait aucun (backlog #012).

Sans effet pendant la réponse forcée, quand la requête ne propose aucun
outil, ou quand l'orchestrateur répare une réponse sans outils.

**Arguments :**

- `subject` : La requête à envoyer au modèle et l'état du run.

**Renvoie :**

`Replace` d'une requête en `tool_choice: required` si le run n'a
encore appelé aucun outil ; `CONTINUE` sinon.
