# loom_ia.tools_builtin.memory.remote

Module `loom_ia.tools_builtin.memory.remote` de loom_v2 (fichier `src/loom_ia/tools_builtin/memory/remote.py`).

Les modèles de la mémoire servis par `loom-mem-gpu` : le client, côté loom.

`RemoteEmbedder` et `RemoteReranker` ont l'API de `BgeM3Embedder` et
`BgeReranker` : `MemoryService` ne voit pas la différence. Ils partagent
une `Connection` : un client HTTP et ce que l'application a dit d'elle.

- **Premier contact**, une fois par process : `/v1/health`. L'application
  doit annoncer le protocole, l'algorithme, les modèles et la dimension
  attendus ; sinon `ModelLoadError`, comme pour une base indexée par un
  autre modèle. Encore en chargement (503 `loading`), elle est attendue au
  plus `READY_WAIT` secondes.
- **Calcul** : une requête par lot de `BATCH` textes ; `cancel` est
  regardé entre deux lots, comme en local.
- **Erreurs** : injoignable → `ServerUnavailable` (une `ModelLoadError`)
  qui dit comment la démarrer ; file pleine (503) → nouvel essai après
  `Retry-After` ; 500 → `ModelRunError`. Après une coupure, le premier
  contact est refait : une application relancée peut ne plus être la même
  version.
- **Repli** (`server.fallback`) : quand l'application est indisponible —
  injoignable, encore en chargement après `READY_WAIT`, ou sans modèles —,
  le lot est calculé dans ce process par les modèles locaux, chargés au
  premier repli. Une ligne d'avertissement, une seule fois ; l'application
  est ressayée au premier lot après `REPROBE` secondes, et reprend la main
  dès qu'elle répond. Un jeton refusé, des modèles qui ne sont pas les bons,
  un calcul en échec ne se replient pas : ce sont des erreurs, pas une panne.

Sans repli, torch n'est jamais importé : un poste sans l'extra `memory`
calcule par l'application. Le client ignore les proxys de l'environnement (`trust_env`) :
la boucle locale ne passe pas par eux.

## Constantes de `loom_ia.tools_builtin.memory.remote`

- `log = logging.getLogger('loom_ia.memory')` : Le journal de la mémoire.
- `REPROBE = 30.0` : Après un repli, l'application est ressayée au premier lot passé ce délai, en secondes.
- `READY_WAIT = 60.0` : Attente d'une application en cours de chargement, en secondes, et pas de la sonde.
- `READY_POLL = 1.0` : Attente entre deux interrogations de l'état, pendant le chargement des modèles, en secondes.
- `BUSY_PAUSE_MAX = 2.0` : Attente maximale d'un 503 « file pleine » avant un nouvel essai, en secondes.
- `_TICK = 0.1` : Tranche d'attente : un appel annulé n'attend pas plus.

## Classe `loom_ia.tools_builtin.memory.remote.Target`

```python
@dataclass(frozen=True, slots=True)
class Target
```

Où joindre l'application, une fois `url_env` et `token_env` lus.

## Classe `loom_ia.tools_builtin.memory.remote._Unreachable`

```python
class _Unreachable(Exception)
```

L'application ne répond pas (connexion refusée, délai, coupure).

## Classe `loom_ia.tools_builtin.memory.remote.ServerUnavailable`

```python
class ServerUnavailable(ModelLoadError)
```

L'application ne peut pas calculer : injoignable, en chargement trop long, sans modèles.

Le seul cas qui déclenche le repli.

## Classe `loom_ia.tools_builtin.memory.remote.Connection`

```python
class Connection
```

Le client HTTP de l'application et ce qu'elle a dit d'elle, partagés par les deux modèles.

### Méthode `loom_ia.tools_builtin.memory.remote.Connection.device`

```python
@property
def device(self) -> str | None
```

Où calcule le prochain lot : le device annoncé par l'application, ou celui du repli.

### Méthode `loom_ia.tools_builtin.memory.remote.Connection.served`

```python
@property
def served(self) -> bool
```

Vrai si le prochain lot ira à l'application (pas de repli en cours).

### Méthode `loom_ia.tools_builtin.memory.remote.Connection.local`

```python
@property
def local(self) -> Models
```

Les modèles du repli ; `ValueError` sans `server.fallback`.

### Méthode `loom_ia.tools_builtin.memory.remote.Connection.run`

```python
def run[R](self, remote: Callable[[], R], local: Callable[[Models], R]) -> R
```

Un lot : par l'application, ou dans ce process quand elle est indisponible.

### Méthode `loom_ia.tools_builtin.memory.remote.Connection.ensure`

```python
def ensure(self, cancel: threading.Event | None=None) -> Health
```

Le premier contact, fait une fois : l'application est-elle celle qu'on attend ?

### Méthode `loom_ia.tools_builtin.memory.remote.Connection.post`

```python
def post[R: BaseModel](self, path: str, payload: BaseModel, answer: type[R], cancel: threading.Event | None=None) -> R
```

Une requête de calcul ; réessaie une file pleine, refait le contact après une coupure.

### Méthode `loom_ia.tools_builtin.memory.remote.Connection.close`

```python
def close(self) -> None
```

Ferme le client HTTP, s'il est ouvert.

### Méthode `loom_ia.tools_builtin.memory.remote.Connection._check`

```python
def _check(self, health: Health) -> None
```

L'application doit calculer comme ce process le ferait : sinon, rien ne passe.

## Fonction `loom_ia.tools_builtin.memory.remote._error`

```python
def _error(response: httpx2.Response) -> ErrorBody
```

Le corps d'une réponse d'erreur ; un corps qui n'en est pas un dit le code HTTP.

## Fonction `loom_ia.tools_builtin.memory.remote._sleep`

```python
def _sleep(seconds: float, cancel: threading.Event | None) -> None
```

Attend `seconds` ; un appel annulé s'arrête à la tranche suivante.

## Classe `loom_ia.tools_builtin.memory.remote.RemoteEmbedder`

```python
class RemoteEmbedder
```

BGE-M3 calculé par `loom-mem-gpu` : dense et lexical.

### Méthode `loom_ia.tools_builtin.memory.remote.RemoteEmbedder.model_name`

```python
@property
def model_name(self) -> str
```

Le nom du modèle, tel que réglé.

### Méthode `loom_ia.tools_builtin.memory.remote.RemoteEmbedder.dim`

```python
@property
def dim(self) -> int
```

La dimension du dense de BGE-M3.

### Méthode `loom_ia.tools_builtin.memory.remote.RemoteEmbedder.device`

```python
@property
def device(self) -> str | None
```

Où calcule le prochain lot.

### Méthode `loom_ia.tools_builtin.memory.remote.RemoteEmbedder.load`

```python
def load(self) -> None
```

Fait le premier contact avec l'application, ou charge le modèle du repli.

### Méthode `loom_ia.tools_builtin.memory.remote.RemoteEmbedder.embed_documents`

```python
def embed_documents(self, texts: Sequence[str], cancel: threading.Event | None=None) -> list[Embedding]
```

Les vecteurs des textes, lot par lot, chaque lot par l'application ou par le repli.

### Méthode `loom_ia.tools_builtin.memory.remote.RemoteEmbedder.embed_query`

```python
def embed_query(self, text: str, cancel: threading.Event | None=None) -> Embedding
```

Les vecteurs d'une question.

## Classe `loom_ia.tools_builtin.memory.remote.RemoteReranker`

```python
class RemoteReranker
```

Le reranker calculé par `loom-mem-gpu` : un score entre 0 et 1 par passage.

### Méthode `loom_ia.tools_builtin.memory.remote.RemoteReranker.model_name`

```python
@property
def model_name(self) -> str
```

Le nom du reranker, tel que réglé.

### Méthode `loom_ia.tools_builtin.memory.remote.RemoteReranker.device`

```python
@property
def device(self) -> str | None
```

Où calcule le prochain lot.

### Méthode `loom_ia.tools_builtin.memory.remote.RemoteReranker.load`

```python
def load(self) -> None
```

Fait le premier contact avec l'application, ou charge le reranker du repli.

### Méthode `loom_ia.tools_builtin.memory.remote.RemoteReranker.score`

```python
def score(self, query: str, passages: Sequence[str], cancel: threading.Event | None=None) -> list[float]
```

Un score par passage, lot par lot, chaque lot par l'application ou par le repli.

## Fonction `loom_ia.tools_builtin.memory.remote.remote_models`

```python
def remote_models(target: Target, spec: ModelsSpec) -> tuple[RemoteEmbedder, RemoteReranker]
```

L'embedder et le reranker servis par l'application, sur une même connexion.
