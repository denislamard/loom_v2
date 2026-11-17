# loom_ia.tools_builtin.memory.bge_m3

Module `loom_ia.tools_builtin.memory.bge_m3` de loom_v2 (fichier `src/loom_ia/tools_builtin/memory/bge_m3.py`).

BGE-M3 et son reranker sur `transformers` (extra `memory`), sur CPU ou GPU.

Les mêmes calculs que FlagEmbedding, sans lui :

- **dense** : le vecteur du premier token (`[CLS]`) de la dernière couche,
  normé (1024 dimensions) ;
- **lexical** : la tête `sparse_linear.pt` du dépôt du modèle, appliquée à
  chaque token de la dernière couche, puis ReLU ; un poids par identifiant de
  token (le plus fort s'il revient), sans les tokens spéciaux ni les poids nuls ;
- **reranker** : un classifieur de paires (question, passage), son logit passé
  dans une sigmoïde, d'où un score entre 0 et 1.

torch et transformers ne sont importés qu'au chargement des poids, au premier
calcul ou au préchargement : ce module s'importe sans eux. Demi-précision sur
GPU (`fp16`, faite avant le transfert vers la carte), float32 sur CPU. Lots
de 16 textes ; au plus 1024 tokens par texte, 512 par paire pour le reranker.
Un calcul abandonné (`cancel`) s'arrête avant le lot suivant : sur CPU, un lot
prend de l'ordre de 30 s.

Les barres de progression de transformers et de huggingface_hub (« Loading
weights », téléchargements) sont coupées au chargement : un outil n'écrit rien
dans la console. Le réglage vaut pour le process. Avec `models.offline`, les
fichiers sont lus dans le cache local seulement, sans requête vers le Hub.

## Constantes de `loom_ia.tools_builtin.memory.bge_m3`

- `ALGO = 'bge-m3/2'` : Version du calcul : dense, lexical, troncature, normalisation, sigmoïde du reranker. À incrémenter dès que l'un d'eux change : loom-mem-gpu l'annonce, et un client d'une autre version refuse ses vecteurs plutôt que d'en mêler deux sortes dans une base.
- `DENSE_DIM = 1024` : Dimension du dense des modèles de la famille BGE-M3 : la base l'inscrit avant tout calcul.
- `BATCH = 16` : Textes par lot de calcul, en local comme dans les requêtes à l'application.
- `MAX_CHARS = 20000` : Caractères lus au plus par texte (question, passage), ici comme par loom-mem-gpu : bien au-delà de ce que lit le tokenizer (1024 tokens, environ 4 caractères chacun en français), la coupe ne change le calcul que pour un texte dont les tokens feraient en moyenne plus de 19 caractères. Elle donne aux deux chemins la même borne : sans elle, un texte plus long passait ici et était refusé par l'application (413).
- `MAX_TOKENS = 1024` : Tokens lus au plus par texte, pour le dense et le lexical.
- `MAX_PAIR_TOKENS = 512` : Tokens lus au plus par paire question-passage, pour le reranker.
- `SPARSE_HEAD = 'sparse_linear.pt'` : Le fichier des poids de la tête lexicale de BGE-M3.

## Fonction `loom_ia.tools_builtin.memory.bge_m3._modules`

```python
def _modules() -> tuple[Any, Any]
```

Importe torch et transformers ; `ImportError` (nommant le paquet) sans l'extra `memory`.

## Fonction `loom_ia.tools_builtin.memory.bge_m3._from_hub`

```python
@contextlib.contextmanager
def _from_hub(offline: bool) -> Generator[None]
```

Hors ligne, un fichier absent du cache (`OSError`) devient `ModelNotCached`.

## Fonction `loom_ia.tools_builtin.memory.bge_m3.half_precision`

```python
def half_precision(fp16: bool, device: str) -> bool
```

Demi-précision : demandée (`fp16`) et sur une carte ; jamais sur CPU ni mps.

## Fonction `loom_ia.tools_builtin.memory.bge_m3.clipped`

```python
def clipped(text: str) -> str
```

Le texte borné à `MAX_CHARS` caractères, comme le lisent les deux chemins.

## Fonction `loom_ia.tools_builtin.memory.bge_m3.batches`

```python
def batches[T](items: Sequence[T]) -> list[Sequence[T]]
```

Les lots de `BATCH` éléments : ceux du calcul local et des requêtes à l'application.

## Classe `loom_ia.tools_builtin.memory.bge_m3._Encoder`

```python
@dataclass(frozen=True, slots=True)
class _Encoder
```

BGE-M3 chargé : calcule le dense et le lexical des textes.

**Attributs :**

- `torch` : Le module torch, importé au chargement.
- `tokenizer` : Le tokenizer du modèle.
- `model` : Le modèle.
- `head` : La tête lexicale (`sparse_linear`).
- `device` : Le périphérique de calcul.
- `special` : Les tokens spéciaux, exclus du lexical.

### Méthode `loom_ia.tools_builtin.memory.bge_m3._Encoder.encode`

```python
def encode(self, texts: Sequence[str], cancel: threading.Event | None=None) -> list[Embedding]
```

Le dense et le lexical de chaque texte, par lots de `BATCH`, chaque texte coupé à `MAX_TOKENS` tokens.

Le dense est le token CLS normalisé ; le lexical, le poids de chaque token (le
plus fort s'il revient), hors tokens spéciaux.

## Classe `loom_ia.tools_builtin.memory.bge_m3._PairScorer`

```python
@dataclass(frozen=True, slots=True)
class _PairScorer
```

Le reranker chargé : note des paires question-passage.

**Attributs :**

- `torch` : Le module torch, importé au chargement.
- `tokenizer` : Le tokenizer du reranker.
- `model` : Le reranker.
- `device` : Le périphérique de calcul.

### Méthode `loom_ia.tools_builtin.memory.bge_m3._PairScorer.score`

```python
def score(self, query: str, passages: Sequence[str], cancel: threading.Event | None=None) -> list[float]
```

Le score de chaque passage pour la question, entre 0 et 1 (sigmoïde du reranker), par lots de `BATCH`.

Seul le passage est coupé pour tenir dans `MAX_PAIR_TOKENS`.

## Classe `loom_ia.tools_builtin.memory.bge_m3.BgeM3Embedder`

```python
class BgeM3Embedder
```

BGE-M3 : dense et lexical en un seul passage du modèle.

### Méthode `loom_ia.tools_builtin.memory.bge_m3.BgeM3Embedder.model_name`

```python
@property
def model_name(self) -> str
```

Le nom du modèle, tel que réglé.

### Méthode `loom_ia.tools_builtin.memory.bge_m3.BgeM3Embedder.dim`

```python
@property
def dim(self) -> int
```

La dimension du dense.

### Méthode `loom_ia.tools_builtin.memory.bge_m3.BgeM3Embedder.device`

```python
@property
def device(self) -> str | None
```

Le device des poids chargés ; `None` tant qu'ils ne le sont pas.

### Méthode `loom_ia.tools_builtin.memory.bge_m3.BgeM3Embedder.load`

```python
def load(self) -> None
```

Charge les poids s'ils ne le sont pas.

### Méthode `loom_ia.tools_builtin.memory.bge_m3.BgeM3Embedder._build`

```python
def _build(self, device: str) -> _Encoder
```

Charge BGE-M3 sur `device` : tokenizer, modèle, tête lexicale, demi-précision au besoin.

### Méthode `loom_ia.tools_builtin.memory.bge_m3.BgeM3Embedder.embed_documents`

```python
def embed_documents(self, texts: Sequence[str], cancel: threading.Event | None=None) -> list[Embedding]
```

Les vecteurs des textes, dans l'ordre.

### Méthode `loom_ia.tools_builtin.memory.bge_m3.BgeM3Embedder.embed_query`

```python
def embed_query(self, text: str, cancel: threading.Event | None=None) -> Embedding
```

Les vecteurs d'une question.

## Classe `loom_ia.tools_builtin.memory.bge_m3.BgeReranker`

```python
class BgeReranker
```

bge-reranker-v2-m3 : un score entre 0 et 1 par paire (question, passage).

### Méthode `loom_ia.tools_builtin.memory.bge_m3.BgeReranker.model_name`

```python
@property
def model_name(self) -> str
```

Le nom du reranker, tel que réglé.

### Méthode `loom_ia.tools_builtin.memory.bge_m3.BgeReranker.device`

```python
@property
def device(self) -> str | None
```

Le device des poids chargés ; `None` tant qu'ils ne le sont pas.

### Méthode `loom_ia.tools_builtin.memory.bge_m3.BgeReranker.load`

```python
def load(self) -> None
```

Charge les poids s'ils ne le sont pas.

### Méthode `loom_ia.tools_builtin.memory.bge_m3.BgeReranker.score`

```python
def score(self, query: str, passages: Sequence[str], cancel: threading.Event | None=None) -> list[float]
```

Un score par passage, entre 0 et 1.
