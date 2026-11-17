# loom_ia.adapters.models.recording

Module `loom_ia.adapters.models.recording` de loom_v2 (fichier `src/loom_ia/adapters/models/recording.py`).

Client HTTP qui garde ce qu'il envoie et reçoit (point 31, jalon J6.1b).

Passé au SDK à la place de son client par défaut, quand un client de loom a
demandé les échanges bruts (`telemetry.capture.raw_exchanges`). Il ne garde
rien de lui-même : il dépose chaque échange dans le registre que le moteur a
ouvert pour la tentative (`exchange_log`), et ne fait rien s'il n'y en a pas.

On enrobe `send`, et non le transport : c'est par `send` que passent tous
les appels des SDK, et c'est au-dessus des proxys que l'environnement peut
poser — un transport enrobé serait contourné par eux.

Un corps de réponse diffusé (SSE) est recopié au fil de sa lecture, et
l'échange est déposé quand le flux se ferme : son corps est alors celui que
le SDK a lu, décompressé. Les réglages du client reprennent ceux des SDK
(connexions, redirections) ; seules les options de keepalive TCP du client
par défaut d'Anthropic ne sont pas reprises.

## Constantes de `loom_ia.adapters.models.recording`

- `LIMITS = httpx2.Limits(max_connections=1000, max_keepalive_connections=100)` : Ceux des clients par défaut d'Anthropic et d'OpenAI.

## Classe `loom_ia.adapters.models.recording.RecordingClient`

```python
class RecordingClient(httpx2.AsyncClient)
```

`httpx2.AsyncClient` qui dépose ses échanges dans le registre ouvert.

### Méthode `loom_ia.adapters.models.recording.RecordingClient.send`

```python
async def send(self, request: httpx2.Request, *, stream: bool=False, **options: Any) -> httpx2.Response
```

Envoie la requête et dépose l'échange dans le registre ouvert, s'il y en a un.

Une erreur d'envoi est déposée, puis relevée. Une réponse lue d'un bloc
est déposée tout de suite ; un flux est recopié au fil de sa lecture, et
l'échange est déposé à sa fermeture, décompressé.

## Classe `loom_ia.adapters.models.recording._Copied`

```python
class _Copied(httpx2.AsyncByteStream)
```

Flux de réponse recopié au fil de la lecture ; la copie part à la fermeture.

### Méthode `loom_ia.adapters.models.recording._Copied.aclose`

```python
async def aclose(self) -> None
```

Ferme le flux d'origine, puis dépose la copie.

## Fonction `loom_ia.adapters.models.recording._deposit`

```python
def _deposit(log: ExchangeLog, request: httpx2.Request, sent: bytes, response: httpx2.Response, received: bytes, started: float) -> None
```

Dépose un échange : requête, réponse, en-têtes, statut et durée.

## Fonction `loom_ia.adapters.models.recording._decoded`

```python
def _decoded(response: httpx2.Response, received: bytes) -> bytes
```

Un flux recopié l'est avant décodage : le corps tel que le SDK l'a lu.

Le décodage est celui de httpx, par une réponse jetable : mêmes
algorithmes que pour le SDK, sans toucher à leurs rouages privés.
