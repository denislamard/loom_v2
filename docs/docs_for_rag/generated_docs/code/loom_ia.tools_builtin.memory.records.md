# loom_ia.tools_builtin.memory.records

Module `loom_ia.tools_builtin.memory.records` de loom_v2 (fichier `src/loom_ia/tools_builtin/memory/records.py`).

Ce que la mémoire range et rend : documents, passages, résultats.

`Document` a les champs d'un export de loom-notes (une ligne JSON par
document) : un tel export se réimporte tel quel avec `loom memory import`.

## Constantes de `loom_ia.tools_builtin.memory.records`

- `SourceKind = Literal['text', 'url', 'file']` : L'origine d'un document : un texte donné, une page web ou un fichier.

## Fonction `loom_ia.tools_builtin.memory.records.utc_now_iso`

```python
def utc_now_iso() -> str
```

L'instant présent en ISO 8601, UTC, microsecondes comprises.

## Fonction `loom_ia.tools_builtin.memory.records.new_doc_id`

```python
def new_doc_id() -> str
```

Un nouvel identifiant de document (UUID 4).

## Classe `loom_ia.tools_builtin.memory.records.Document`

```python
class Document(BaseModel)
```

Un document de la mémoire : son texte intégral et ses métadonnées.

## Classe `loom_ia.tools_builtin.memory.records.Chunk`

```python
class Chunk(BaseModel)
```

Un passage d'un document, tel qu'il est vectorisé.

### Méthode `loom_ia.tools_builtin.memory.records.Chunk.embed_text`

```python
@property
def embed_text(self) -> str
```

Texte réellement vectorisé : le chemin de titres préfixe le contenu.

## Classe `loom_ia.tools_builtin.memory.records.DocSummary`

```python
class DocSummary(BaseModel)
```

Un document sans son texte.

### Méthode `loom_ia.tools_builtin.memory.records.DocSummary.of`

```python
@classmethod
def of(cls, doc: Document) -> DocSummary
```

Le résumé d'un document : ses métadonnées et la longueur de son texte.

## Classe `loom_ia.tools_builtin.memory.records.Hit`

```python
class Hit(BaseModel)
```

Un passage trouvé par `search`.

## Classe `loom_ia.tools_builtin.memory.records.IngestResult`

```python
class IngestResult(BaseModel)
```

Ce qu'un ajout ou une mise à jour a réellement fait.
