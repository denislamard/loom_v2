# loom_ia.tools_builtin.memory.tool

Module `loom_ia.tools_builtin.memory.tool` de loom_v2 (fichier `src/loom_ia/tools_builtin/memory/tool.py`).

L'outil `memory` monté : ses actions, leur approbation, leur exécution.

L'approbation se décide appel par appel (`assess_call`) d'après l'action et
la config ; la déclaration fixe (`spec`) dit le pire des actions permises,
pour ce qui ne regarde pas l'appel lui-même.

| Action | Effet | Idempotente |
| --- | --- | --- |
| search, get, list_docs, projects | aucun | — |
| add_text, add_url, add_file | réversible | oui : même contenu ou même source, rien de doublé |
| update | réversible | oui : le même texte ne réécrit rien |
| delete | irréversible | non |

`add_file` lit du texte (.md, .markdown, .txt), du Word et du PDF (`files`).
Avant l'approbation, seul ce qui ne peut pas changer est vérifié : dossier,
droit, extension. Le motif dit ce que l'humain approuve — la taille du fichier
et, pour un texte, le nombre de passages et le temps de calcul quand le modèle
est chargé —, mais n'en décide rien : le fichier peut changer pendant
l'attente, et l'exécution refait tout.

## Constantes de `loom_ia.tools_builtin.memory.tool`

- `TIMEOUT = 300.0` : Délai d'un appel, en secondes, quand l'agent n'en donne pas (`timeout`). Indexer une page sur CPU prend de l'ordre de 1,5 s par passage, et le premier appel peut charger les modèles : le délai commun des outils (30 s) n'y suffit pas.
- `SECONDS_PER_PASSAGE = {'cpu': 2.0, 'cuda': 0.015}` : Calcul d'un passage, en secondes, mesuré le 03/11/2026 (BGE-M3, lots de 16) : de 1,5 à 2 s sur CPU, environ 14 ms sur GPU. Sert seulement à prévenir l'humain qui approuve.

## Classe `loom_ia.tools_builtin.memory.tool.MemoryTool`

```python
class MemoryTool
```

L'outil `memory` monté sur sa base, ses modèles et ses dossiers.

### Méthode `loom_ia.tools_builtin.memory.tool.MemoryTool.__init__`

```python
def __init__(self, params: MemoryParams, store: MemoryStore, folders: Folders | None, unreadable: Mapping[str, str] | None=None, target: Target | None=None) -> None
```

Prépare l'outil : service de mémoire, modèles partagés, formats lus, et la définition qui découle des actions permises.

**Arguments :**

- `params` : Les réglages de l'outil.
- `store` : La base.
- `folders` : Les dossiers que `add_file` peut lire ; `None` : aucun.
- `unreadable` : Les formats que ce process ne sait pas lire, et pourquoi (un extra manque).
- `target` : Où joindre loom-mem-gpu (`models.server`), une fois ses variables lues.

### Méthode `loom_ia.tools_builtin.memory.tool.MemoryTool.spec`

```python
@property
def spec(self) -> ToolSpec
```

La définition de l'outil.

### Méthode `loom_ia.tools_builtin.memory.tool.MemoryTool.store`

```python
@property
def store(self) -> MemoryStore
```

La base de la mémoire.

### Méthode `loom_ia.tools_builtin.memory.tool.MemoryTool.assess_call`

```python
def assess_call(self, arguments: Mapping[str, JsonValue]) -> CallAssessment
```

Approbation et effets de cet appel, d'après son action et la config.

### Méthode `loom_ia.tools_builtin.memory.tool.MemoryTool._writes_nothing`

```python
def _writes_nothing(self, request: MemoryArguments) -> bool
```

Vrai si `update` ou `delete` ne peut rien écrire ; au moindre doute, faux.

Document inconnu, ou hors du projet imposé : l'appel sera refusé. `update`
d'un texte vide sera refusé ; d'un texte inchangé (casse et espaces mis à part),
il n'écrit rien ; du texte d'un autre document, il est refusé. La base est lue
sans être créée ; illisible, l'approbation est demandée comme d'habitude.

### Méthode `loom_ia.tools_builtin.memory.tool.MemoryTool._adds_nothing`

```python
def _adds_nothing(self, request: MemoryArguments) -> bool
```

Vrai si `add_text` ne peut rien écrire ; au moindre doute, faux.

Titre trop court, texte vide ou projet absent : l'appel sera refusé. Un texte
que le projet a déjà (casse et espaces mis à part) : l'exécution rend
`duplicate_of` sans rien écrire. La base est lue sans être créée.

### Méthode `loom_ia.tools_builtin.memory.tool.MemoryTool._what`

```python
def _what(self, request: MemoryArguments) -> str
```

Ce que vise l'appel, pour la demande d'approbation : titre, adresse, fichier (et son coût de calcul), document ou projet.

### Méthode `loom_ia.tools_builtin.memory.tool.MemoryTool._file_cost`

```python
def _file_cost(self, path: str) -> str
```

Ce que coûtera le fichier, pour l'humain qui approuve ; ne lève jamais.

La taille ; pour un texte, le nombre de passages (le texte découpé comme il
le sera) et, si le modèle est chargé, le temps de calcul. Un .docx ou un .pdf
n'est pas converti ici : sa taille seule. Ce qui sera refusé à l'exécution est
dit, sans rien décider : le fichier peut changer d'ici là.

### Méthode `loom_ia.tools_builtin.memory.tool.MemoryTool._duration`

```python
def _duration(self, passages: int) -> str | None
```

Le temps de calcul, si le modèle est chargé sur un device dont on connaît la vitesse.

### Méthode `loom_ia.tools_builtin.memory.tool.MemoryTool.invoke`

```python
async def invoke(self, arguments: dict[str, JsonValue], context: ToolContext) -> ToolOutput
```

Fait l'action demandée : chercher, lire, lister, mémoriser, mettre à jour ou supprimer.

**Lève :**

- `ToolError` : Arguments, document, fichier ou page refusés.

### Méthode `loom_ia.tools_builtin.memory.tool.MemoryTool._file_target`

```python
def _file_target(self, path: str) -> tuple[Located, Path]
```

Le fichier « <dossier>/<chemin> », s'il est permis : dossier, droit, extension.

### Méthode `loom_ia.tools_builtin.memory.tool.MemoryTool._read_file`

```python
async def _read_file(self, path: str) -> tuple[str, Extracted]
```

Le fichier désigné « <dossier>/<chemin> » : son adresse, son titre, son texte.

### Méthode `loom_ia.tools_builtin.memory.tool.MemoryTool._description`

```python
def _description(self) -> str
```

La description de l'outil.

Le projet, les actions permises (et celles qui attendent une approbation), les
fichiers lisibles, la règle des écritures.

### Méthode `loom_ia.tools_builtin.memory.tool.MemoryTool.settings`

```python
def settings(self) -> Settings
```

La base, les actions et leur approbation, les modèles : pour `loom validate`.

## Fonction `loom_ia.tools_builtin.memory.tool.build`

```python
def build(params: BaseModel, context: BuiltinContext) -> MemoryTool
```

La fabrique de `loom.memory` : vérifie la base et les dossiers, n'ouvre rien.

## Fonction `loom_ia.tools_builtin.memory.tool._size`

```python
def _size(size: int) -> str
```

Une taille de fichier lisible : « 820 o », « 64 Ko », « 1,2 Mo ».

## Fonction `loom_ia.tools_builtin.memory.tool._server_target`

```python
def _server_target(server: ServerSpec | None, context: BuiltinContext) -> Target | None
```

Où joindre loom-mem-gpu : `url_env` et `token_env` lus dans les secrets du client.

## Fonction `loom_ia.tools_builtin.memory.tool._store_path`

```python
def _store_path(params: MemoryParams, context: BuiltinContext) -> Path
```

Le chemin absolu de la base, lu dans `store.path` ou sa variable.

**Lève :**

- `ValueError` : Variable absente, chemin relatif, dossier absent, ou fichier de loom.
