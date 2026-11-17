# loom_ia.adapters.mcp.server

Module `loom_ia.adapters.mcp.server` de loom_v2 (fichier `src/loom_ia/adapters/mcp/server.py`).

Un serveur MCP vu du client : connexion, reconnexion, cache des outils (point 19, fonction D3).

La session du SDK est tenue par une tâche dédiée : ses contextes (anyio) sont
ouverts et refermés dans la même tâche, quelle que soit celle qui l'utilise.

- **Connexion à la demande**, avec un délai (`connect_timeout`).
- **Contrôle de santé** : un `ping` à chaque réutilisation d'une connexion
  pour un nouveau run ; une connexion morte est rouverte.
- **Reconnexion avec backoff** : après un échec, la tentative suivante attend
  1, 2, 5, 10 puis 30 s ; entre-temps, le serveur est déclaré indisponible.
- **Appel interrompu par une perte de connexion** : rejoué une fois, après
  reconnexion, seulement si l'outil peut l'être sans risque (point 18). Une session
  HTTP que le serveur ne reconnaît plus (réponse 404) est une connexion perdue.
- **Une seule connexion fermée par échec** : celle qui a échoué, et seulement
  si elle est encore la courante — une autre, ouverte entre-temps, reste.
- **Cache des outils**, vidé sur `notifications/tools/list_changed` et à
  chaque nouvelle connexion.
- **Fermeture après inactivité** (`idle_timeout`), pour la portée `shared`.
- **Appel abandonné** (délai dépassé, run annulé) : le serveur en est prévenu
  par `notifications/cancelled` (décisions B-2/D-6 du délai et des threads). Le SDK ne le fait pas de lui-même ;
  un serveur qui respecte la notification arrête l'appel, les autres le
  finissent, et l'effet reste d'état inconnu pour le modèle.

## Constantes de `loom_ia.adapters.mcp.server`

- `BACKOFF = (1.0, 2.0, 5.0, 10.0, 30.0)` : Attentes successives avant une nouvelle tentative de connexion, en secondes.
- `CLOSE_TIMEOUT = 5.0` : Délai laissé à une session pour se fermer proprement.
- `CANCEL_TIMEOUT = 2.0` : Délai laissé à l'envoi de `notifications/cancelled` au serveur (décisions B-2/D-6 du délai et des threads).
- `SESSION_TERMINATED = 'Session terminated'` : Message que le SDK met dans l'erreur d'une requête à laquelle un serveur HTTP répond 404 : la session est inconnue ou expirée (spécification MCP). Le code qu'il y joint est 32600, et non -32600 (`INVALID_REQUEST`) : seul le message est fiable.
- `Clock = Callable[[], float]` : Horloge en secondes (par défaut `time.monotonic`) ; un essai peut en donner une autre.

## Classe `loom_ia.adapters.mcp.server.ConnectionLost`

```python
class ConnectionLost(Exception)
```

Connexion perdue pendant un appel : l'appel a peut-être produit son effet.

## Fonction `loom_ia.adapters.mcp.server.is_connection_lost`

```python
def is_connection_lost(error: BaseException) -> bool
```

Vrai si l'erreur vient du transport, et non de l'outil ou du protocole.

Une session HTTP expirée en fait partie : le serveur répond 404 et il faut
rouvrir une session.

## Classe `loom_ia.adapters.mcp.server._Held`

```python
class _Held
```

Une session ouverte par une tâche dédiée, jusqu'à `close`.

### Méthode `loom_ia.adapters.mcp.server._Held.start`

```python
async def start(self, delay: float) -> ClientSession
```

Ouvre la session ; `TimeoutError` si elle n'est pas prête en `delay` secondes.

### Méthode `loom_ia.adapters.mcp.server._Held.close`

```python
async def close(self, *, force: bool=False) -> None
```

Ferme la session : laisse `CLOSE_TIMEOUT` à la tâche (sauf `force`), puis l'annule.

L'attente ne lève jamais l'issue de la tâche, mais laisse passer l'annulation de
l'appelant : un run annulé, ou dont le délai tombe, pendant une fermeture ne
poursuit pas son appel. La tâche, déjà annulée, finit alors seule.

## Classe `loom_ia.adapters.mcp.server.McpServer`

```python
class McpServer
```

Connexion gérée à un serveur déclaré.

### Méthode `loom_ia.adapters.mcp.server.McpServer.__init__`

```python
def __init__(self, spec: McpServerSpec, factory: SessionFactory, *, idle_timeout: float | None=None, clock: Clock=time.monotonic, backoff: Sequence[float]=BACKOFF) -> None
```

Prépare la connexion, sans l'ouvrir : elle s'ouvre au premier usage.

**Arguments :**

- `spec` : Le serveur déclaré.
- `factory` : Ouvre une session selon le transport (`session_factory`).
- `idle_timeout` : Inactivité, en secondes, après laquelle la connexion est fermée ; `None` : jamais.
- `clock` : Horloge, en secondes.
- `backoff` : Attentes successives avant une nouvelle tentative de connexion, en secondes.

### Méthode `loom_ia.adapters.mcp.server.McpServer.name`

```python
@property
def name(self) -> str
```

Nom du serveur déclaré.

### Méthode `loom_ia.adapters.mcp.server.McpServer.connected`

```python
@property
def connected(self) -> bool
```

Vrai si une session est ouverte.

### Méthode `loom_ia.adapters.mcp.server.McpServer.tools`

```python
async def tools(self) -> tuple[types.Tool, ...]
```

Outils du serveur, après un contrôle de santé de la connexion.

### Méthode `loom_ia.adapters.mcp.server.McpServer.call`

```python
async def call(self, name: str, arguments: dict[str, Any], *, meta: dict[str, Any], retry: bool) -> types.CallToolResult
```

Appelle un outil ; rejoue une fois après une perte de connexion si `retry`.

### Méthode `loom_ia.adapters.mcp.server.McpServer._cancel_remote`

```python
def _cancel_remote(self, session: ClientSession, request_id: int, name: str) -> None
```

Prévient le serveur que loom abandonne la requête ; sans attendre, sans lever.

L'envoi part dans une tâche à part : la tâche de l'appel est annulée, et
une seconde annulation (fin du lot) couperait un envoi fait en ligne.

### Méthode `loom_ia.adapters.mcp.server.McpServer.on_message`

```python
async def on_message(self, message: RequestResponder[types.ServerRequest, types.ClientResult] | types.ServerNotification | Exception) -> None
```

Messages spontanés du serveur : seul le changement de liste d'outils compte.

### Méthode `loom_ia.adapters.mcp.server.McpServer.aclose`

```python
async def aclose(self) -> None
```

Ferme la connexion ; plus aucune ne s'ouvre ensuite.

### Méthode `loom_ia.adapters.mcp.server.McpServer._connect_locked`

```python
async def _connect_locked(self) -> ClientSession
```

Ouvre une session, sous verrou, dans le délai `connect_timeout`.

Pendant l'attente qui suit un échec (1, 2, 5, 10 puis 30 s), le serveur est
indisponible sans nouvelle tentative ; le cache des outils est vidé à chaque
connexion.

**Lève :**

- `SourceUnavailable` : Connexion fermée, attente en cours, ou ouverture ratée.

### Méthode `loom_ia.adapters.mcp.server.McpServer._drop`

```python
async def _drop(self, failed: ClientSession | None=None) -> None
```

Ferme la connexion courante ; avec `failed`, seulement si c'est celle de cette session.

Un appel qui échoue sur une connexion déjà remplacée (elle a été fermée avec le
remplacement) ne doit pas fermer la neuve, saine, sur laquelle d'autres appels sont en vol.

## Fonction `loom_ia.adapters.mcp.server._describe`

```python
def _describe(error: BaseException) -> str
```

Message court d'une erreur de connexion, y compris dans un groupe d'exceptions.

## Fonction `loom_ia.adapters.mcp.server._innermost`

```python
def _innermost(error: BaseException) -> BaseException
```

Première exception d'un groupe (anyio regroupe celles de ses tâches).

## Fonction `loom_ia.adapters.mcp.server._next_request_id`

```python
def _next_request_id(session: ClientSession) -> int | None
```

L'identifiant de la prochaine requête de la session, ou `None` si le SDK a changé.

`BaseSession.send_request` le lit puis l'incrémente, avant tout `await` :
lu juste avant `call_tool`, sans `await` entre les deux, c'est celui de
l'appel. Un SDK qui ne l'exposerait plus prive seulement de l'annulation.
