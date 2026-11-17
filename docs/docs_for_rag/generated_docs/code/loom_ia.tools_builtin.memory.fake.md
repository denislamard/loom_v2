# loom_ia.tools_builtin.memory.fake

Module `loom_ia.tools_builtin.memory.fake` de loom_v2 (fichier `src/loom_ia/tools_builtin/memory/fake.py`).

Modèles factices et déterministes (`models.kind: fake`) : pas de torch, pas de téléchargement.

Le dense est un sac de mots haché, le lexical compte les mots, le reranker
mesure la part des mots de la question présents dans le passage : assez pour
que la recherche se comporte de façon plausible dans les essais et les
démonstrations. Ce n'est pas une recherche par le sens.

## Classe `loom_ia.tools_builtin.memory.fake.FakeEmbedder`

```python
class FakeEmbedder
```

Un embedder factice : sac de mots haché, sans modèle.

### Méthode `loom_ia.tools_builtin.memory.fake.FakeEmbedder.model_name`

```python
@property
def model_name(self) -> str
```

`fake-<dim>`.

### Méthode `loom_ia.tools_builtin.memory.fake.FakeEmbedder.dim`

```python
@property
def dim(self) -> int
```

La dimension du dense.

### Méthode `loom_ia.tools_builtin.memory.fake.FakeEmbedder.device`

```python
@property
def device(self) -> str | None
```

Toujours `None` : rien n'est chargé.

### Méthode `loom_ia.tools_builtin.memory.fake.FakeEmbedder.load`

```python
def load(self) -> None
```

Rien à charger.

### Méthode `loom_ia.tools_builtin.memory.fake.FakeEmbedder._one`

```python
def _one(self, text: str) -> Embedding
```

Les vecteurs d'un texte : le dense, un sac de mots haché sur `dim` cases et normalisé ; le lexical, le compte de chaque mot haché.

### Méthode `loom_ia.tools_builtin.memory.fake.FakeEmbedder.embed_documents`

```python
def embed_documents(self, texts: Sequence[str], cancel: threading.Event | None=None) -> list[Embedding]
```

Les vecteurs des textes, dans l'ordre.

### Méthode `loom_ia.tools_builtin.memory.fake.FakeEmbedder.embed_query`

```python
def embed_query(self, text: str, cancel: threading.Event | None=None) -> Embedding
```

Les vecteurs d'une question.

## Classe `loom_ia.tools_builtin.memory.fake.FakeReranker`

```python
class FakeReranker
```

Un reranker factice : la part des mots de la question trouvés dans le passage.

### Méthode `loom_ia.tools_builtin.memory.fake.FakeReranker.model_name`

```python
@property
def model_name(self) -> str
```

`fake-reranker`.

### Méthode `loom_ia.tools_builtin.memory.fake.FakeReranker.device`

```python
@property
def device(self) -> str | None
```

Toujours `None` : rien n'est chargé.

### Méthode `loom_ia.tools_builtin.memory.fake.FakeReranker.load`

```python
def load(self) -> None
```

Rien à charger.

### Méthode `loom_ia.tools_builtin.memory.fake.FakeReranker.score`

```python
def score(self, query: str, passages: Sequence[str], cancel: threading.Event | None=None) -> list[float]
```

La part des mots de la question présents dans chaque passage.
