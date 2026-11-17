# loom_ia.tools_builtin.memory.store

Module `loom_ia.tools_builtin.memory.store` de loom_v2 (fichier `src/loom_ia/tools_builtin/memory/store.py`).

La base de la mémoire : un fichier SQLite, la bibliothèque standard, rien d'autre.

Quatre tables :

- `documents` : texte intégral et métadonnées, la source de vérité ; un même
  contenu (`content_hash`) et une même source n'y figurent qu'une fois par projet ;
- `chunks` : les passages et leurs vecteurs (dense en float32, lexical en
  paires indice/poids), reconstruits depuis `documents` par `reindex` ;
- `doc_tags` : les tags, pour filtrer ;
- `meta` : le modèle qui a indexé la base, sa dimension, la version du
  schéma et une génération que chaque écriture incrémente.

Un document et ses passages s'écrivent dans **une** transaction : un arrêt en
cours de route ne laisse rien à moitié. WAL et `busy_timeout` : plusieurs
process (workers, `serve --reload`, la CLI) partagent la base. Une
connexion par opération, ouverte et fermée dans le thread qui l'utilise.

## Constantes de `loom_ia.tools_builtin.memory.store`

- `SCHEMA_VERSION = '1'` : Version du schéma de la base de mémoire ; une autre version est refusée.
- `BUSY_TIMEOUT_S = 10.0` : Attente au plus d'un verrou SQLite, en secondes.
- `PEEK_TIMEOUT_S = 0.5` : Un coup d'œil avant un appel tourne dans la boucle du moteur : il n'attend pas un verrou.
- `_WAL_FIRST_WAIT = 0.01` : Attentes entre deux essais du passage en WAL, en secondes : de 10 ms à 200 ms.

## Fonction `loom_ia.tools_builtin.memory.store.use_wal`

```python
def use_wal(connection: sqlite3.Connection, timeout: float=BUSY_TIMEOUT_S) -> None
```

Passe la base en WAL, en réessayant tant qu'elle est occupée, au plus `timeout`.

Le passage demande un verrou exclusif. Quand plusieurs connexions ouvrent en
même temps une base neuve, SQLite peut répondre « occupée » sur-le-champ, sans
attendre `busy_timeout` (il y voit un interblocage) : l'essai est refait.
Une base déjà en WAL le reste, sans verrou.

## Classe `loom_ia.tools_builtin.memory.store.ModelMismatchError`

```python
class ModelMismatchError(Exception)
```

La base a été indexée avec un autre modèle que celui de la config.

## Classe `loom_ia.tools_builtin.memory.store.Peeked`

```python
@dataclass(frozen=True, slots=True)
class Peeked
```

Ce qu'un coup d'œil à la base dit d'un document (`MemoryStore.peek`).

**Attributs :**

- `doc` : Le document cherché, `None` s'il n'existe pas.
- `same` : Dans le projet de `doc`, le document qui a déjà le contenu cherché.

## Classe `loom_ia.tools_builtin.memory.store.IndexedChunk`

```python
@dataclass(frozen=True, slots=True)
class IndexedChunk
```

Un passage tel que la recherche le compare : ses vecteurs et ce qu'il faut pour le rendre.

## Fonction `loom_ia.tools_builtin.memory.store.pack_dense`

```python
def pack_dense(values: Sequence[float]) -> bytes
```

Le dense en octets : des float32, dans l'ordre de la machine.

## Fonction `loom_ia.tools_builtin.memory.store.unpack_dense`

```python
def unpack_dense(blob: bytes) -> array[float]
```

Le dense relu de ses octets.

## Fonction `loom_ia.tools_builtin.memory.store.pack_sparse`

```python
def pack_sparse(indices: Sequence[int], values: Sequence[float]) -> bytes
```

Le lexical en octets : les indices (uint32), puis les poids (float32).

**Lève :**

- `ValueError` : Indices et poids de longueurs différentes.

## Fonction `loom_ia.tools_builtin.memory.store.unpack_sparse`

```python
def unpack_sparse(blob: bytes) -> dict[int, float]
```

Le lexical relu de ses octets : indice → poids.

## Classe `loom_ia.tools_builtin.memory.store.MemoryStore`

```python
class MemoryStore
```

Un fichier SQLite de mémoire. Toutes les méthodes sont synchrones : à appeler hors boucle.

### Méthode `loom_ia.tools_builtin.memory.store.MemoryStore._writing`

```python
@contextmanager
def _writing(self) -> Generator[sqlite3.Connection]
```

Une transaction d'écriture : tout ou rien, la génération incrémentée.

### Méthode `loom_ia.tools_builtin.memory.store.MemoryStore.ensure`

```python
def ensure(self, model: str, dim: int, *, check: bool=True) -> None
```

Crée le schéma s'il manque ; vérifie que la base a été indexée avec ce modèle.

`check=False` : seulement pour `reindex`, qui remplace l'index.

### Méthode `loom_ia.tools_builtin.memory.store.MemoryStore.generation`

```python
def generation(self) -> int
```

Le compteur des écritures : il change à chaque modification, et l'index en mémoire se recharge alors.

### Méthode `loom_ia.tools_builtin.memory.store.MemoryStore.get`

```python
def get(self, doc_id: str) -> Document | None
```

Le document de cet identifiant, ou `None`.

### Méthode `loom_ia.tools_builtin.memory.store.MemoryStore.find_by_hash`

```python
def find_by_hash(self, content_hash: str, project: str) -> Document | None
```

Le document du projet qui a ce contenu, ou `None`.

### Méthode `loom_ia.tools_builtin.memory.store.MemoryStore.find_by_source`

```python
def find_by_source(self, source: str, project: str) -> Document | None
```

Le document du projet venu de cette source (adresse ou fichier), ou `None`.

### Méthode `loom_ia.tools_builtin.memory.store.MemoryStore.list_documents`

```python
def list_documents(self, project: str | None, limit: int) -> list[Document]
```

Les `limit` derniers documents ajoutés, du projet donné ou de tous.

### Méthode `loom_ia.tools_builtin.memory.store.MemoryStore.documents`

```python
def documents(self) -> list[Document]
```

Tous les documents, du plus ancien au plus récent : l'export, la réindexation.

### Méthode `loom_ia.tools_builtin.memory.store.MemoryStore.project_counts`

```python
def project_counts(self) -> dict[str, int]
```

Chaque projet et son nombre de documents, par nom.

### Méthode `loom_ia.tools_builtin.memory.store.MemoryStore.count`

```python
def count(self) -> tuple[int, int]
```

Nombre de documents et de passages.

### Méthode `loom_ia.tools_builtin.memory.store.MemoryStore.index`

```python
def index(self) -> tuple[int, list[IndexedChunk]]
```

La génération et tous les passages, lus dans une même transaction.

### Méthode `loom_ia.tools_builtin.memory.store.MemoryStore.peek`

```python
def peek(self, doc_id: str, content_hash: str | None=None) -> Peeked | None
```

Le document `doc_id`, et celui qui a déjà `content_hash` dans son projet.

Pour juger un appel avant de le soumettre à un humain. En lecture seule : la
base n'est jamais créée ni modifiée, et une base absente n'a aucun document.
`None` quand la base ne peut pas être lue (verrou au-delà de
`PEEK_TIMEOUT_S`, schéma absent, fichier abîmé) : on ne sait pas.

### Méthode `loom_ia.tools_builtin.memory.store.MemoryStore.peek_content`

```python
def peek_content(self, content_hash: str, project: str) -> Peeked | None
```

Le document de `project` qui a déjà ce contenu (`same`) ; comme `peek`.

### Méthode `loom_ia.tools_builtin.memory.store.MemoryStore.write`

```python
def write(self, doc: Document, chunks: Sequence[Chunk], embeddings: Sequence[Embedding]) -> None
```

Écrit le document et ses passages (ceux d'avant remplacés), en une transaction.

### Méthode `loom_ia.tools_builtin.memory.store.MemoryStore.delete`

```python
def delete(self, doc_id: str) -> bool
```

Supprime le document et ses passages ; faux s'il n'existait pas.

### Méthode `loom_ia.tools_builtin.memory.store.MemoryStore.reset_index`

```python
def reset_index(self, model: str, dim: int) -> None
```

Vide les passages et inscrit le nouveau modèle : la réindexation suit.

## Fonction `loom_ia.tools_builtin.memory.store._document`

```python
def _document(row: Sequence[object], tags: list[str]) -> Document
```

Un document reconstruit d'une ligne de la table `documents` et de ses tags.
