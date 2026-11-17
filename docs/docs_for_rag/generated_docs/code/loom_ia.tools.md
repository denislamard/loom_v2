# loom_ia.tools

Paquet `loom_ia.tools` de loom_v2 (fichier `src/loom_ia/tools/__init__.py`).

Outils fournis aux agents : fonctions Python (fonction D1), images produites (fonction G3).

- `python` : `@tool` et `FunctionTool`, un outil tiré d'une fonction
  (schéma depuis la signature, description depuis la docstring) ; `Image` ;
- `idempotent` : `@idempotent`, un effet produit une seule fois par clé ;
- `configured` : les déclarations d'un outil remplacées par la config.

Les outils fournis par loom-ia (`loom.<outil>`) sont dans `tools_builtin`.
