# loom_ia.tools_builtin.memory.arguments

Module `loom_ia.tools_builtin.memory.arguments` de loom_v2 (fichier `src/loom_ia/tools_builtin/memory/arguments.py`).

Ce que le modèle envoie à `memory` : l'action et ses arguments.

Le schéma montré au modèle ne garde que les actions permises et les
arguments qu'elles utilisent ; `project` en disparaît quand la config
l'impose. L'analyse refuse, avec un message qui dit quoi corriger, une
action non permise, un argument manquant ou un argument qui ne sert pas à
l'action.

## Constantes de `loom_ia.tools_builtin.memory.arguments`

- `NAME = 'memory'` : Le nom de l'outil.
- `WRITING = frozenset({'add_text', 'add_url', 'add_file'})` : Les actions qui écrivent dans la mémoire.
- `_REQUIRED = {'search': frozenset({'query'}), 'get': frozenset({'doc_id'}), 'list_docs': frozenset(), 'projects': frozenset(), 'add_…` : Arguments de chaque action : obligatoires, puis facultatifs. `project` est ajouté (obligatoire pour une écriture, facultatif pour une lecture) quand la config ne l'impose pas.

## Classe `loom_ia.tools_builtin.memory.arguments.MemoryArguments`

```python
class MemoryArguments(BaseModel)
```

Arguments d'un appel : ceux que voit le modèle.

## Fonction `loom_ia.tools_builtin.memory.arguments.expected`

```python
def expected(action: MemoryAction, params: MemoryParams) -> tuple[frozenset[str], frozenset[str]]
```

Arguments obligatoires et facultatifs de l'action, `action` exclu.

## Fonction `loom_ia.tools_builtin.memory.arguments.schema`

```python
def schema(params: MemoryParams) -> dict[str, JsonValue]
```

Le schéma que voit le modèle : les actions permises et leurs seuls arguments.

## Fonction `loom_ia.tools_builtin.memory.arguments.parse`

```python
def parse(arguments: Mapping[str, JsonValue], params: MemoryParams) -> MemoryArguments
```

Les arguments validés pour l'action demandée ; `ToolError` qui dit quoi corriger.
