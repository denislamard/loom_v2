# loom_ia.engine.model_call

Module `loom_ia.engine.model_call` de loom_v2 (fichier `src/loom_ia/engine/model_call.py`).

Appel d'un modèle : fichiers, fenêtre de contexte, délais, nouvelles tentatives (fonctions B3 et B7, point 14).

La politique de retry est celle de loom-ia, pas celle des SDK (désactivée) :
chaque tentative ratée est annoncée par un `model.retried`, que le moteur
écrit dans le journal avant d'attendre. Un appel relancé l'est en entier ; si
des morceaux avaient déjà été diffusés, `on_chunk` reçoit d'abord un
`StreamReset`.

Avant la première tentative, les références de fichiers de la requête sont
résolues selon les capacités du modèle (`MediaResolver`) ; la fenêtre de
contexte est estimée sur la requête non résolue, plus une part fixe par image.

Fin de l'appel : une tentative ne rend une réponse que si son flux a une fin
propre (`Stopped`) et n'a pas été arrêté par la limite de tokens. Un flux
coupé ou vide est une erreur `transient` (nouvelles tentatives, secours,
disjoncteur) ; une sortie arrêtée par `max_tokens` est une erreur
`truncated`, définitive : refaire le même appel couperait au même endroit.
La réponse d'un journal (`AnsweringClient`) n'est pas contrôlée : elle a été
acceptée quand le journal a été écrit, et le rejeu la ressert telle quelle. Le
journal est interrogé avec la requête **d'origine**, références de fichiers
comprises (c'est elle qu'a hachée `request_hash`) ; seul le flux d'un vrai
appel reçoit la requête résolue.

Échanges bruts (jalon J6.1b) : avec `raw_max_bytes`, chaque tentative ouvre un
registre (`recording`) où le client HTTP de l'adaptateur dépose ce qu'il a
envoyé et reçu ; à la fin de la tentative, réussie ou non, chaque échange sort
en `model.exchanged`, **avant** le `model.retried` ou la réponse.

Réglages d'un modèle : `retry` (tentatives, délais), `timeouts`
(`first_token`, `idle`, `total`), `capabilities.context_window`.

## Constantes de `loom_ia.engine.model_call`

- `Sleep = Callable[[float], Awaitable[None]]` : Attente de n secondes ; `asyncio.sleep` sauf dans les tests.

## Fonction `loom_ia.engine.model_call.responded`

```python
def responded(request: ModelRequest, response: ModelResponse, spec: ModelSpec, *, attempts: int, latency_ms: float, message: Message | None=None, call_id: str | None=None) -> ModelResponded
```

Événement d'une réponse obtenue ; `message` remplace celui de la réponse.

**Arguments :**

- `request` : Requête d'origine, dont l'empreinte (`request_hash`) et les parties sont recopiées.
- `response` : La réponse.
- `spec` : Le modèle de la config qui a répondu ; son tarif donne le coût.
- `attempts` : Tentatives sur ce modèle, la dernière comprise.
- `latency_ms` : Durée de l'appel, en millisecondes.
- `message` : Message à écrire à la place de celui de la réponse (une réponse retouchée par une politique, par exemple).
- `call_id` : Appel d'outil délégué auquel la réponse appartient, s'il y en a un.

**Renvoie :**

L'événement `model.responded`.

## Fonction `loom_ia.engine.model_call.estimate_tokens`

```python
def estimate_tokens(request: ModelRequest) -> int
```

Estimation grossière des tokens d'entrée d'une requête.

Longueur du JSON du système, des messages et des outils, divisée par
`CHARS_PER_TOKEN`. Les images comptent à part (`IMAGE_TOKENS`).

**Arguments :**

- `request` : La requête.

**Renvoie :**

Le nombre estimé de tokens.

## Classe `loom_ia.engine.model_call._Progress`

```python
@dataclass
class _Progress
```

Morceaux déjà diffusés pendant une tentative.

## Classe `loom_ia.engine.model_call.ModelCall`

```python
class ModelCall
```

Exécute les appels d'un modèle selon sa définition.

**Attributs :**

- `client` : Client du fournisseur.
- `spec` : Définition du modèle.
- `on_chunk` : Diffusion en direct de la réponse.
- `media` : Résolution des références de fichiers pour ce modèle.
- `raw_max_bytes` : Borne d'un corps brut gardé ; `None` : les échanges ne sont pas gardés.

### Méthode `loom_ia.engine.model_call.ModelCall.__init__`

```python
def __init__(self, client: ModelClient, spec: ModelSpec, *, on_chunk: ChunkCallback | None=None, artifacts: ArtifactStore | None=None, sleep: Sleep=asyncio.sleep, jitter: Callable[[], float]=random.random) -> None
```

Prépare l'appel d'un modèle.

**Arguments :**

- `client` : Client du fournisseur ; un `Recorded` fait garder les échanges bruts, où qu'il serve (orchestrateur, rôles, juges, secours).
- `spec` : Définition du modèle.
- `on_chunk` : Diffusion en direct de la réponse.
- `artifacts` : Magasin de fichiers, pour résoudre les références.
- `sleep` : Attente entre deux tentatives.
- `jitter` : Tirage de la part aléatoire du délai, entre 0 et 1.

### Méthode `loom_ia.engine.model_call.ModelCall.run`

```python
async def run(self, request: ModelRequest) -> AsyncGenerator[ModelExchanged | ModelRetried | ModelResponse]
```

Émet un `ModelRetried` par tentative ratée, puis la réponse.

Avec la capture des échanges bruts, chaque tentative émet d'abord ses
`ModelExchanged`. Lève `ModelError` si l'erreur n'est pas
rejouable, si les tentatives sont épuisées, ou si un fichier de la
requête ne peut pas être envoyé — après les échanges de la dernière
tentative, qui sont justement ceux qu'on voudra lire.

**Arguments :**

- `request` : La requête, références de fichiers non résolues.

**Produit :**

Les `model.exchanged` de chaque tentative, un `model.retried`
par tentative ratée suivie d'une autre, puis la réponse.

**Lève :**

- `ModelError` : `context_overflow` si la requête estimée dépasse la fenêtre ; erreur définitive (`auth`, `invalid_request`, `truncated`…) ; tentatives épuisées ; fichier impossible à envoyer.

### Méthode `loom_ia.engine.model_call.ModelCall._retried`

```python
def _retried(self, request: ModelRequest, failure: ModelError, attempt: int, delay: float, billed: Usage | None, *, final: bool=False) -> ModelRetried
```

`model.retried` d'une tentative ratée.

**Arguments :**

- `request` : La requête.
- `failure` : L'erreur de la tentative.
- `attempt` : Rang de la tentative, à partir de 1.
- `delay` : Attente avant la suivante, en secondes.
- `billed` : Usage annoncé par le fournisseur avant l'échec ; il est facturé.
- `final` : Vrai si aucune tentative ne suit.

### Méthode `loom_ia.engine.model_call.ModelCall._check_context`

```python
def _check_context(self, request: ModelRequest) -> None
```

Vérifie que la requête estimée, sortie comprise, tient dans la fenêtre du modèle.

**Lève :**

- `ModelError` : `context_overflow`, avec l'estimation et la fenêtre.

### Méthode `loom_ia.engine.model_call.ModelCall._retry_delay`

```python
def _retry_delay(self, error: ModelError, attempt: int) -> float | None
```

Attente avant la tentative suivante, ou `None` pour abandonner.

`None` pour une erreur non rejouable ou au-delà de
`retry.max_attempts` ; le `retry_after` du fournisseur s'il ne
dépasse pas `retry.max_delay` (au-delà, abandon) ; sinon le délai
exponentiel de `retry`.

### Méthode `loom_ia.engine.model_call.ModelCall._attempt`

```python
async def _attempt(self, request: ModelRequest, sent: ModelRequest, progress: _Progress) -> ModelResponse
```

Une tentative : `request` est la requête d'origine, `sent` celle qui part.

**Lève :**

- `ModelError` : `truncated` si la sortie s'est arrêtée sur `max_tokens` ; `transient` pour un flux coupé, vide ou trop lent. L'usage annoncé avant l'échec est porté par l'erreur (fonction J1).

### Méthode `loom_ia.engine.model_call.ModelCall._stream`

```python
async def _stream(self, sent: ModelRequest, accumulator: ResponseAccumulator, progress: _Progress) -> None
```

Lit le flux d'une tentative dans `accumulator`, sous ses délais.

Le premier morceau attendu est le premier **contenu** : un `UsageDelta`
(l'entrée annoncée par le fournisseur à l'ouverture du flux) ne compte ni
pour lui, ni pour ce qui a été diffusé. Son délai court donc depuis le
début de la tentative, quoi qu'il arrive avant.

Délais : `timeouts.first_token` jusqu'au premier contenu,
`timeouts.idle` entre deux morceaux ensuite, `timeouts.total` pour
tout le flux.

**Lève :**

- `ModelError` : `transient` quand un délai est dépassé.

## Fonction `loom_ia.engine.model_call._billed`

```python
def _billed(failure: ModelError) -> Usage | None
```

Usage annoncé par le fournisseur avant l'échec, ou None s'il n'a rien dit.
