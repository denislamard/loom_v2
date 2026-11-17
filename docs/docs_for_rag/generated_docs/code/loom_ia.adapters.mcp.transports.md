# loom_ia.adapters.mcp.transports

Module `loom_ia.adapters.mcp.transports` de loom_v2 (fichier `src/loom_ia/adapters/mcp/transports.py`).

Ouverture d'une session MCP selon le transport déclaré (fonction D3).

Une fabrique de session prend le gestionnaire des messages du serveur
(notifications `tools/list_changed`…) et ouvre une `ClientSession`
initialisée. Les secrets sont lus dans l'environnement à la création de la
fabrique : une variable absente est une erreur de config, signalée au montage.

## Constantes de `loom_ia.adapters.mcp.transports`

- `SessionFactory = Callable[[MessageHandlerFnT], AbstractAsyncContextManager[ClientSession]]` : Ouvre une session MCP, avec le gestionnaire des messages du serveur.
- `CLIENT_INFO = types.Implementation(name='loom-ia', version=__version__)` : Nom et version que loom-ia annonce aux serveurs MCP.
- `STDERR_LINES = 20` : Lignes de stderr d'un serveur stdio gardées pour expliquer un échec d'ouverture.
- `STDERR_LINE_CHARS = 300` : Longueur gardée d'une ligne : un serveur peut écrire une trace d'un seul tenant.
- `STDERR_SHOWN = 3` : Lignes citées dans l'erreur : la dernière est presque toujours l'exception.
- `_MARKERS = re.compile('[~^\\s]+')` : Ligne faite seulement des repères d'une trace Python (`~~~~^^^^`).

## Classe `loom_ia.adapters.mcp.transports.McpConfigError`

```python
class McpConfigError(ValueError)
```

Serveur MCP inutilisable en l'état (variable d'environnement absente…).

## Classe `loom_ia.adapters.mcp.transports.McpStartError`

```python
class McpStartError(ConnectionError)
```

Serveur stdio qui s'arrête ou refuse l'ouverture, avec ce qu'il a écrit sur stderr.

Le SDK MCP ne dit que « Connection closed » ou `BrokenResourceError` : ni
le code de sortie (il ne donne pas accès au processus), ni ce que le
serveur a écrit. Un chemin faux ou une config illisible ne se voyaient
qu'en relançant le serveur à la main (essai de la fonction D3, 18/10).

## Fonction `loom_ia.adapters.mcp.transports.session_factory`

```python
def session_factory(spec: McpServerSpec, *, environ: Mapping[str, str]) -> SessionFactory
```

Fabrique des sessions du serveur `spec`.

## Fonction `loom_ia.adapters.mcp.transports._stdio`

```python
def _stdio(spec: McpServerSpec, environ: Mapping[str, str]) -> SessionFactory
```

Ouvre les sessions d'un serveur stdio : la commande, ses arguments, son environnement (`env`, puis `env_from` lu dans le nôtre) et son dossier.

Une ouverture ratée lève `McpStartError`, avec la fin du stderr du serveur.

## Classe `loom_ia.adapters.mcp.transports._StderrTail`

```python
class _StderrTail
```

Le stderr d'un serveur stdio, recopié sur le nôtre et dont on garde la fin.

Le SDK branche le stderr du processus sur un fichier : on lui donne
l'extrémité d'écriture d'un tuyau, et un fil lit l'autre. Chaque ligne
part sur notre stderr préfixée du nom du serveur (`[notes] …`), et les
`STDERR_LINES` dernières restent en mémoire, sans le préfixe. À l'essai de la
fonction F6, le 19/10, les avertissements de torch et les traces de fastmcp d'un
serveur se mêlaient à la sortie de loom sans dire qui parlait. Le fil
s'arrête quand plus personne n'écrit : le processus est mort et notre
extrémité est fermée.

### Méthode `loom_ia.adapters.mcp.transports._StderrTail.lines`

```python
def lines(self) -> list[str]
```

Les dernières lignes utiles du stderr du serveur.

### Méthode `loom_ia.adapters.mcp.transports._StderrTail.close`

```python
def close(self) -> None
```

Ferme notre extrémité, puis laisse au fil le temps de lire ce qui reste.

## Fonction `loom_ia.adapters.mcp.transports._started`

```python
def _started(error: BaseException, lines: list[str]) -> str
```

Ce que l'on sait d'une ouverture ratée : la cause, et la fin du stderr du serveur.

## Fonction `loom_ia.adapters.mcp.transports._innermost`

```python
def _innermost(error: BaseException) -> BaseException
```

Première exception d'un groupe (anyio regroupe celles de ses tâches).

## Fonction `loom_ia.adapters.mcp.transports._http`

```python
def _http(spec: McpServerSpec, environ: Mapping[str, str]) -> SessionFactory
```

Ouvre les sessions d'un serveur HTTP (streamable HTTP), avec les en-têtes lus dans l'environnement (`headers_env`).
