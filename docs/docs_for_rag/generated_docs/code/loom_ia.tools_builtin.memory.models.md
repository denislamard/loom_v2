# loom_ia.tools_builtin.memory.models

Module `loom_ia.tools_builtin.memory.models` de loom_v2 (fichier `src/loom_ia/tools_builtin/memory/models.py`).

Modèles de la mémoire : interfaces, chargement paresseux sous verrou, registre du process.

Les modèles sont synchrones : la mémoire les appelle dans un thread. Ils sont
lourds (BGE-M3 et son reranker font chacun ~568 M de paramètres) : un seul
exemplaire par process et par réglage, partagé par tous les agents et tous
les clients (`shared_models`). Le device se choisit pour le process, pas
pour un appel.

Un calcul s'abandonne entre deux lots (`cancel`) : quand l'appel qui
l'attendait est annulé — délai de l'outil dépassé, run arrêté —, le thread ne
finit pas un calcul dont personne ne lira le résultat, et rend le modèle aux
appels suivants. Un appel annulé pendant qu'il attend le modèle quitte la file
sans calculer.

## Constantes de `loom_ia.tools_builtin.memory.models`

- `log = logging.getLogger('loom_ia.memory')` : Le journal de la mémoire.
- `_CANCEL_POLL = 0.1` : Toutes les combien de secondes un appel qui attend le modèle regarde s'il est annulé.

## Classe `loom_ia.tools_builtin.memory.models.SparseVec`

```python
@dataclass(frozen=True, slots=True)
class SparseVec
```

Poids lexicaux : identifiants de tokens triés, et leurs poids (tous positifs).

## Classe `loom_ia.tools_builtin.memory.models.Embedding`

```python
@dataclass(frozen=True, slots=True)
class Embedding
```

Les vecteurs d'un texte.

**Attributs :**

- `dense` : Le vecteur dense, normalisé.
- `sparse` : Les poids lexicaux.

## Classe `loom_ia.tools_builtin.memory.models.Embedder`

```python
class Embedder(Protocol)
```

Les vecteurs des textes. `cancel` levé, le calcul s'arrête au lot suivant.

### Méthode `loom_ia.tools_builtin.memory.models.Embedder.model_name`

```python
@property
def model_name(self) -> str
```

Le nom du modèle : la base l'inscrit, et refuse un autre modèle.

### Méthode `loom_ia.tools_builtin.memory.models.Embedder.dim`

```python
@property
def dim(self) -> int
```

La dimension du dense.

### Méthode `loom_ia.tools_builtin.memory.models.Embedder.device`

```python
@property
def device(self) -> str | None
```

Le device des poids chargés ; `None` tant qu'ils ne le sont pas.

### Méthode `loom_ia.tools_builtin.memory.models.Embedder.load`

```python
def load(self) -> None
```

Charge les poids s'ils ne le sont pas : ce que fait le préchargement.

### Méthode `loom_ia.tools_builtin.memory.models.Embedder.embed_documents`

```python
def embed_documents(self, texts: Sequence[str], cancel: threading.Event | None=None) -> list[Embedding]
```

Les vecteurs des passages, dans l'ordre.

### Méthode `loom_ia.tools_builtin.memory.models.Embedder.embed_query`

```python
def embed_query(self, text: str, cancel: threading.Event | None=None) -> Embedding
```

Les vecteurs d'une question.

## Classe `loom_ia.tools_builtin.memory.models.Reranker`

```python
class Reranker(Protocol)
```

Les scores de pertinence des passages pour une question. `cancel` levé, le calcul s'arrête au lot suivant.

### Méthode `loom_ia.tools_builtin.memory.models.Reranker.model_name`

```python
@property
def model_name(self) -> str
```

Le nom du reranker.

### Méthode `loom_ia.tools_builtin.memory.models.Reranker.device`

```python
@property
def device(self) -> str | None
```

Le device des poids chargés ; `None` tant qu'ils ne le sont pas.

### Méthode `loom_ia.tools_builtin.memory.models.Reranker.load`

```python
def load(self) -> None
```

Charge les poids s'ils ne le sont pas : ce que fait le préchargement.

### Méthode `loom_ia.tools_builtin.memory.models.Reranker.score`

```python
def score(self, query: str, passages: Sequence[str], cancel: threading.Event | None=None) -> list[float]
```

Un score par passage, entre 0 et 1.

## Classe `loom_ia.tools_builtin.memory.models.ModelError`

```python
class ModelError(RuntimeError)
```

Un modèle n'a pas pu servir : message lisible, cause en une ligne.

## Classe `loom_ia.tools_builtin.memory.models.ModelLoadError`

```python
class ModelLoadError(ModelError)
```

Un modèle n'a pas pu être chargé, ou n'a pas tourné une première fois.

Rien n'est gardé : l'appel suivant recharge le modèle.

## Classe `loom_ia.tools_builtin.memory.models.ModelNotCached`

```python
class ModelNotCached(Exception)
```

Un modèle lu hors ligne (`models.offline`) manque au cache local de Hugging Face.

## Classe `loom_ia.tools_builtin.memory.models.ModelRunError`

```python
class ModelRunError(ModelError)
```

Un modèle chargé a échoué sur un calcul (mémoire du GPU saturée, par exemple).

Le modèle est gardé : l'échec tient au calcul, pas au chargement.

## Classe `loom_ia.tools_builtin.memory.models.ModelCancelled`

```python
class ModelCancelled(Exception)
```

Calcul abandonné : l'appel qui l'attendait a été annulé (`cancel`).

Pas une `ModelError` : personne n'attend plus le résultat, il n'y a rien à
dire au modèle. Le modèle chargé est gardé.

## Fonction `loom_ia.tools_builtin.memory.models.checkpoint`

```python
def checkpoint(cancel: threading.Event | None) -> None
```

Lève `ModelCancelled` si le calcul a été abandonné : à appeler entre deux lots.

## Classe `loom_ia.tools_builtin.memory.models.UnsupportedDevice`

```python
class UnsupportedDevice(Exception)
```

Le device forcé (`models.device: cuda`) n'est pas utilisable par ce torch.

## Fonction `loom_ia.tools_builtin.memory.models.resolve_device`

```python
@functools.cache
def resolve_device(device: str) -> str
```

Résout `auto` en cuda, sinon mps, sinon cpu ; vérifie un `cuda` forcé.

`cuda` n'est choisi que si torch a des noyaux pour la carte : un GPU vu par
torch mais trop ancien pour la roue installée fait échouer les modèles plus
loin, sans message utile. Avec `auto`, il est écarté avec un avertissement ;
forcé (`cuda`, `cuda:N`), il lève `UnsupportedDevice` qui dit pourquoi,
comme l'absence de GPU. torch n'est importé que pour `auto` et `cuda`, au
chargement des vrais modèles ; le résultat est gardé, l'avertissement n'est
donc écrit qu'une fois.

## Fonction `loom_ia.tools_builtin.memory.models.unsupported_gpu`

```python
def unsupported_gpu(torch: Any, index: int=0) -> str | None
```

Dit pourquoi la carte `index` n'est pas utilisable par ce torch, ou `None` si elle l'est.

Même règle que torch : un binaire `sm_XY` tourne sur une carte de même
version majeure X et de version mineure au moins Y ; un code `compute_XY`
(PTX) est compilé à la volée pour toute carte de version au moins X.Y. Une
liste vide ne permet pas de juger : la carte est gardée. Les avertissements
de torch sur la carte sont tus, celui-ci les remplace.

## Fonction `loom_ia.tools_builtin.memory.models.first_line`

```python
def first_line(exc: BaseException) -> str
```

La première ligne du message d'une exception ; à défaut, le nom de son type.

## Classe `loom_ia.tools_builtin.memory.models.LazyModel`

```python
class LazyModel
```

Modèle chargé à la première utilisation, une seule fois, et utilisé sous verrou.

Le chargement de transformers n'est pas sûr entre threads : deux chargements
simultanés — le préchargement et une première requête — peuvent casser le
modèle. Le verrou les met en file, et sert aussi pour l'usage : un seul device
ne calcule de toute façon pas deux lots à la fois.

Le modèle construit tourne une première fois (`check`) avant d'être gardé :
un modèle qui ne tourne pas est jeté, l'erreur devient `ModelLoadError`, et
l'appel suivant recharge.

`run` s'abandonne (`cancel`) en attendant le verrou, avant le chargement,
et avant le calcul ; pendant le calcul, c'est à `fn` de regarder `cancel`
entre ses lots. Un chargement commencé va à son terme : il servira à l'appel
suivant.

### Méthode `loom_ia.tools_builtin.memory.models.LazyModel.device`

```python
@property
def device(self) -> str | None
```

Le device où le modèle est chargé ; `None` tant qu'il ne l'est pas.

### Méthode `loom_ia.tools_builtin.memory.models.LazyModel.load`

```python
def load(self) -> None
```

Charge le modèle s'il ne l'est pas déjà.

### Méthode `loom_ia.tools_builtin.memory.models.LazyModel.run`

```python
def run[R](self, fn: Callable[[T], R], cancel: threading.Event | None=None) -> R
```

Applique `fn` au modèle, chargé au premier appel, un appel à la fois.

**Lève :**

- `ModelCancelled` : `cancel` levé.
- `ModelLoadError` : Le chargement a échoué.
- `ModelRunError` : Le calcul a échoué.

### Méthode `loom_ia.tools_builtin.memory.models.LazyModel._acquire`

```python
def _acquire(self, cancel: threading.Event | None) -> None
```

Le verrou ; un appel annulé pendant l'attente quitte la file (`ModelCancelled`).

### Méthode `loom_ia.tools_builtin.memory.models.LazyModel._load`

```python
def _load(self) -> T
```

Charge le modèle sur son device et le vérifie.

**Lève :**

- `ModelLoadError` : Dépendance absente, device refusé, modèle absent du cache hors ligne, ou autre échec ; le message dit quoi faire.

## Classe `loom_ia.tools_builtin.memory.models.Models`

```python
@dataclass(slots=True)
class Models
```

L'embedder et le reranker d'un réglage, partagés par le process.

### Méthode `loom_ia.tools_builtin.memory.models.Models.preload`

```python
def preload(self) -> None
```

Lance le chargement en tâche de fond, une seule fois ; une erreur est journalisée.

## Fonction `loom_ia.tools_builtin.memory.models.shared_models`

```python
def shared_models(spec: ModelsSpec, target: Target | None=None) -> Models
```

Les modèles du réglage, construits au premier appel puis partagés par le process.

Rien n'est chargé ici : les poids le sont au premier calcul, ou par `preload`.
Les modèles factices ne sont pas partagés : ils ne coûtent rien. Avec
`spec.server`, `target` dit où joindre `loom-mem-gpu` (`url_env` et
`token_env` lus) : les modèles sont alors ses clients, une connexion par
application, et rien ne se charge dans ce process.
