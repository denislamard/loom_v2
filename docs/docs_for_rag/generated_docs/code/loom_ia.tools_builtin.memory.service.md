# loom_ia.tools_builtin.memory.service

Module `loom_ia.tools_builtin.memory.service` de loom_v2 (fichier `src/loom_ia/tools_builtin/memory/service.py`).

Les opérations de la mémoire, communes à l'outil et à `loom memory`.

Asynchrones : la base et les modèles travaillent dans des threads. Les
erreurs destinées au modèle sont des `ToolError`, au message lisible. Un appel
annulé (délai de l'outil dépassé, run arrêté) abandonne le calcul de modèle en
cours au lot suivant : il ne garde pas le modèle pour un résultat que personne
ne lira.

Ce que la mémoire garantit, et que vérifient ses essais :

- un même contenu n'est rangé qu'une fois par projet ; le redonner rend le
  document existant (`duplicate_of`) sans rien écrire : un ajout se refait
  sans risque après une interruption ;
- une même source (page, fichier) n'est qu'un document par projet : la
  redonner remplace son texte ; elle ne déplace jamais un document d'un autre
  projet ;
- `update` refuse un texte qui est déjà celui d'un autre document du projet,
  au lieu de ne rien faire en silence ;
- avec un projet imposé, `get`, `update` et `delete` ne voient que ses
  documents.

## Constantes de `loom_ia.tools_builtin.memory.service`

- `MIN_TITLE_CHARS = 3` : Caractères au moins dans le titre d'un document.

## Classe `loom_ia.tools_builtin.memory.service.Extracted`

```python
@dataclass(frozen=True, slots=True)
class Extracted
```

Un texte à ranger et son titre, tels que l'extraction les a lus.

## Classe `loom_ia.tools_builtin.memory.service.MemoryService`

```python
class MemoryService
```

Une base de mémoire, ses modèles et ses réglages.

### Méthode `loom_ia.tools_builtin.memory.service.MemoryService.__init__`

```python
def __init__(self, store: MemoryStore, models: Models, retrieval: RetrievalSpec, chunking: ChunkingSpec) -> None
```

Prépare le service ; rien n'est ouvert avant `start`.

**Arguments :**

- `store` : La base.
- `models` : L'embedder et le reranker.
- `retrieval` : Les réglages de la recherche.
- `chunking` : Les réglages du découpage.

### Méthode `loom_ia.tools_builtin.memory.service.MemoryService.start`

```python
async def start(self, *, check_model: bool=True) -> None
```

Crée le schéma s'il manque et vérifie le modèle d'indexation, une fois.

### Méthode `loom_ia.tools_builtin.memory.service.MemoryService.search`

```python
async def search(self, query: str, *, project: str | None, tags: Sequence[str]=(), k: int=5, min_score: float | None=None) -> list[Hit]
```

Les passages les plus pertinents pour la question : candidats du dense et du lexical fusionnés, puis reclassés par le reranker.

**Lève :**

- `ToolError` : Question vide.

### Méthode `loom_ia.tools_builtin.memory.service.MemoryService.get`

```python
async def get(self, doc_id: str, *, project: str | None) -> Document
```

Un document entier.

**Lève :**

- `ToolError` : Document inconnu, ou hors du projet donné.

### Méthode `loom_ia.tools_builtin.memory.service.MemoryService.list_docs`

```python
async def list_docs(self, *, project: str | None, n: int=20) -> list[DocSummary]
```

Les `n` derniers documents, du projet donné ou de tous.

### Méthode `loom_ia.tools_builtin.memory.service.MemoryService.projects`

```python
async def projects(self) -> dict[str, int]
```

Chaque projet et son nombre de documents.

### Méthode `loom_ia.tools_builtin.memory.service.MemoryService.add`

```python
async def add(self, extracted: Extracted, *, kind: SourceKind, source: str | None, project: str | None, tags: Sequence[str]=()) -> IngestResult
```

Range un texte. Une source déjà connue du projet est remplacée, pas doublée.

### Méthode `loom_ia.tools_builtin.memory.service.MemoryService.update`

```python
async def update(self, doc_id: str, text: str, *, project: str | None) -> IngestResult
```

Remplace le texte d'un document : même identifiant, titre, projet et tags.

### Méthode `loom_ia.tools_builtin.memory.service.MemoryService.delete`

```python
async def delete(self, doc_id: str, *, project: str | None) -> DocSummary
```

Supprime un document ; rend ce qu'il était.

**Lève :**

- `ToolError` : Document inconnu, ou hors du projet donné.

### Méthode `loom_ia.tools_builtin.memory.service.MemoryService.index_document`

```python
async def index_document(self, doc: Document) -> int
```

Découpe, vectorise et écrit le document ; rend le nombre de passages.

### Méthode `loom_ia.tools_builtin.memory.service.MemoryService._current_index`

```python
def _current_index(self) -> list[IndexedChunk]
```

Les passages de la base, relus seulement si une écriture a eu lieu depuis.

### Méthode `loom_ia.tools_builtin.memory.service.MemoryService._model`

```python
async def _model[R](self, fn: Callable[[threading.Event], R]) -> R
```

Un calcul de modèle, hors de la boucle ; son échec dit au modèle, sans trace.

Le thread ne s'interrompt pas : annulé, l'appel lève l'événement que le
calcul regarde entre ses lots, puis laisse passer l'annulation.

## Fonction `loom_ia.tools_builtin.memory.service.clean_project`

```python
def clean_project(project: str | None) -> str | None
```

Le nom de projet normalisé (sans espaces autour, en minuscules) ; `None` s'il est vide.

## Fonction `loom_ia.tools_builtin.memory.service.clean_tags`

```python
def clean_tags(tags: Sequence[str] | None) -> tuple[str, ...]
```

Les tags normalisés (sans espaces autour, en minuscules), sans vides ni doublons, dans l'ordre.
