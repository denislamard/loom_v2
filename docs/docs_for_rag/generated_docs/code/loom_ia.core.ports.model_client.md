# loom_ia.core.ports.model_client

Module `loom_ia.core.ports.model_client` de loom_v2 (fichier `src/loom_ia/core/ports/model_client.py`).

Port des modèles : le client d'un fournisseur et ses erreurs (points 10 et 11, fonctions B1 à B3).

Un adaptateur n'implémente qu'une chose : le flux de morceaux neutres
(streaming comme primitive de base, point 11). `complete` en déduit la
réponse complète. Toute erreur du fournisseur est traduite en `ModelError`,
dont le genre décide des nouvelles tentatives et de la bascule vers un modèle
de secours.

Implémentations fournies (`adapters/models`) : Anthropic (``sdk:
anthropic`), OpenAI en API `chat` et `responses` (`sdk: openai``, aussi
pour Together, vLLM, Ollama…) et un modèle simulé (`sdk: fake`).

## Constantes de `loom_ia.core.ports.model_client`

- `ChunkCallback = Callable[[ModelChunk], Awaitable[None]]` : Fonction appelée avec chaque morceau du flux, au passage (diffusion en direct).
- `RETRYABLE_ERRORS = frozenset({'transient', 'overloaded'})` : Genres d'erreur qui donnent lieu à de nouvelles tentatives sur le même modèle.

## Classe `loom_ia.core.ports.model_client.ModelError`

```python
class ModelError(Exception)
```

Échec d'un appel de modèle, classé de façon neutre (fonction B3).

Genres (`kind`) et suite donnée :

- `transient`, `overloaded` : nouvelles tentatives, puis bascule vers
  le secours ;
- `quota_exhausted` : bascule aussitôt ;
- `context_overflow` : bascule vers un secours à plus grande fenêtre ;
- `auth`, `invalid_request`, `content_filtered` : l'appel échoue ;
- `truncated` : sortie coupée par la limite de tokens (`max_tokens`) :
  jamais une réponse, l'appel échoue ;
- `unavailable` : toute la chaîne est écartée par ses disjoncteurs.

**Arguments :**

- `kind` : Genre de l'erreur.
- `message` : Message, repris au journal (`model.retried`, `run.failed`).
- `http_status` : Statut HTTP de la réponse du fournisseur, s'il y en a eu une.
- `retry_after` : Délai demandé par le fournisseur (`Retry-After`), en secondes.
- `by_client` : Vrai si l'appel a été arrêté à dessein par le client de modèle lui-même — le rejeu qui s'arrête à sa première divergence (phase 6.2b) — et non par une panne : le moteur le note sans le crier.

**Attributs :**

- `kind` : Genre de l'erreur.
- `message` : Le message.
- `http_status` : Statut HTTP, ou `None`.
- `retry_after` : Délai demandé, ou `None`.
- `by_client` : Arrêt voulu par le client.
- `usage` : Ce que le fournisseur a annoncé avoir consommé avant l'échec, quand il l'a dit (début d'un flux coupé, sortie tronquée) : facturé quand même. `None` par défaut ; l'adaptateur le pose.

### Méthode `loom_ia.core.ports.model_client.ModelError.__init__`

```python
def __init__(self, kind: ModelErrorKind, message: str, *, http_status: int | None=None, retry_after: float | None=None, by_client: bool=False) -> None
```

Crée l'erreur ; les arguments sont décrits dans la docstring de la classe.

### Méthode `loom_ia.core.ports.model_client.ModelError.retryable`

```python
@property
def retryable(self) -> bool
```

Vrai si l'erreur donne lieu à de nouvelles tentatives (`transient`, `overloaded`).

## Fonction `loom_ia.core.ports.model_client.stopped_by_client`

```python
def stopped_by_client(error: BaseException) -> bool
```

Vrai si une erreur, ou ce qui l'a causée, est un arrêt voulu par le client de modèle.

Un juge enrobe l'erreur de son modèle dans une erreur de politique : on
remonte donc la chaîne des causes (`__cause__`).

**Arguments :**

- `error` : L'erreur reçue.

**Renvoie :**

Vrai si un `ModelError` de la chaîne porte `by_client`.

## Classe `loom_ia.core.ports.model_client.ModelClient`

```python
class ModelClient(Protocol)
```

Client d'un fournisseur de modèles : il rend la réponse en flux de morceaux neutres.

### Méthode `loom_ia.core.ports.model_client.ModelClient.provider`

```python
@property
def provider(self) -> str
```

Nom du fournisseur, recopié dans `model.responded`.

### Méthode `loom_ia.core.ports.model_client.ModelClient.stream`

```python
def stream(self, request: ModelRequest) -> AsyncGenerator[ModelChunk]
```

Appelle le modèle et rend sa réponse en morceaux, dans l'ordre d'arrivée.

**Arguments :**

- `request` : La requête neutre : modèle, système, messages, outils, réglages, fichiers résolus.

**Produit :**

Les morceaux neutres : texte, appels d'outils, raisonnement,
consommation, puis un `Stopped` final.

**Lève :**

- `ModelError` : Toute erreur du fournisseur, classée par genre.

### Méthode `loom_ia.core.ports.model_client.ModelClient.aclose`

```python
async def aclose(self) -> None
```

Libère les connexions du client.

## Classe `loom_ia.core.ports.model_client.AnsweringClient`

```python
class AnsweringClient(ABC)
```

Client qui peut connaître d'avance sa réponse entière, et la rend telle quelle (phase 6.2a).

C'est le client du rejeu : ses réponses viennent du journal. Passer par le
flux de morceaux les reconstruirait, et la reconstruction perd ce que les
morceaux ne portent pas (les métadonnées d'un bloc de texte ou d'un appel
d'outil) — la requête suivante, qui contient cette réponse, aurait alors une
autre empreinte, et le rejeu verrait une divergence qu'il a lui-même créée.
`ModelCall` le reconnaît et prend sa réponse sans flux.

En variante (phase 6.2b), une requête que le journal ne connaît pas part pour
de vrai : `answer` rend alors `None`, et `ModelCall` lit le flux du
client, comme pour tout autre.

### Méthode `loom_ia.core.ports.model_client.AnsweringClient.answer`

```python
@abstractmethod
async def answer(self, request: ModelRequest) -> ModelResponse | None
```

La réponse connue à une requête, sans appel au modèle.

**Arguments :**

- `request` : La requête d'origine, références de fichiers non résolues : celle dont l'empreinte est au journal. `stream` reçoit la même, fichiers résolus.

**Renvoie :**

La réponse enregistrée ; `None` : il faut la demander au modèle
(`stream`), ce que fait la variante.

**Lève :**

- `ModelError` : Le client refuse la requête (rejeu identique : elle n'est pas au journal).

### Méthode `loom_ia.core.ports.model_client.AnsweringClient.stream`

```python
@abstractmethod
def stream(self, request: ModelRequest) -> AsyncGenerator[ModelChunk]
```

Morceaux de la réponse d'un vrai appel, quand `answer` n'en a pas ; comme `ModelClient.stream`.

## Fonction `loom_ia.core.ports.model_client.require_end`

```python
def require_end(accumulator: ResponseAccumulator) -> None
```

Vérifie qu'un flux s'est terminé proprement, par un `Stopped`.

Connexion coupée, flux vide : ce que le flux a livré est un début de
réponse, pas une réponse, et ne doit jamais finir en run réussi. L'erreur
est rejouable, donc soumise aux nouvelles tentatives, au secours et au
disjoncteur. Une réponse proprement terminée, même sans contenu, n'est pas
concernée.

**Arguments :**

- `accumulator` : Ce que le flux a livré.

**Lève :**

- `ModelError` : Genre `transient` (« Flux interrompu : le fournisseur a fermé le flux sans signal de fin »).

## Fonction `loom_ia.core.ports.model_client.complete`

```python
async def complete(client: ModelClient, request: ModelRequest, *, on_chunk: ChunkCallback | None=None) -> ModelResponse
```

Consomme le flux d'un client et renvoie la réponse complète.

**Arguments :**

- `client` : Le client de modèle.
- `request` : La requête.
- `on_chunk` : Reçoit chaque morceau au passage (diffusion en direct).

**Renvoie :**

La réponse assemblée, avec le nom du modèle et du fournisseur.

**Lève :**

- `ModelError` : Erreur du fournisseur, ou flux sans fin propre (`require_end`).
