# loom_ia.tools_builtin.memory.server.app

Module `loom_ia.tools_builtin.memory.server.app` de loom_v2 (fichier `src/loom_ia/tools_builtin/memory/server/app.py`).

`loom-mem-gpu` : BGE-M3 et son reranker chargés une fois, servis en HTTP sur la boucle locale.

Bibliothèque standard seulement (`http.server`) : un thread par requête,
les modèles étant synchrones. Chaque modèle calcule un lot à la fois (verrou
de `LazyModel`) ; l'embedder et le reranker, en parallèle.

L'ordre du démarrage (`run`) :

1. réglages validés — sinon sortie 2 ;
2. port ouvert **avant** tout chargement — pris, sortie 3 sans rien charger :
   la VRAM n'est jamais occupée deux fois ;
3. le serveur répond aussitôt ; `/v1/health` dit `loading` (503) ;
4. les modèles se chargent dans un thread, avec leur passage d'essai — en
   échec, `/v1/health` dit `failed` (503) quelques secondes, puis sortie 4 ;
5. `ready` : les modèles ne sont plus jamais déchargés.

SIGTERM ou SIGINT : plus de nouvelle requête, celles en cours finissent (un
lot au plus), sortie 0. L'application ne garde rien : ni base, ni texte. Son
journal dit la requête, son code, le nombre de textes et la durée, jamais leur
contenu.

## Constantes de `loom_ia.tools_builtin.memory.server.app`

- `log = logging.getLogger('loom_ia.memory.server')` : Le journal de l'application.
- `EXIT_OK = 0` : Code de sortie d'un arrêt normal.
- `EXIT_SETTINGS = 2` : Code de sortie : réglages invalides ou dépendances absentes.
- `EXIT_PORT = 3` : Code de sortie : port déjà pris.
- `EXIT_LOAD = 4` : Code de sortie : chargement des modèles raté.
- `QUEUE = 32` : Requêtes servies à la fois ; au-delà, 503 et Retry-After.
- `SOCKET_TIMEOUT = 30.0` : Délai de socket d'une requête, en secondes : un client muet ne garde pas de thread.
- `FAILED_GRACE = 5.0` : Après un chargement raté, le temps laissé à `status` pour lire l'erreur, en secondes.
- `RETRY_AFTER = '1'` : L'en-tête Retry-After d'une réponse 503, en secondes.
- `MODULES = ('torch', 'transformers')` : Ce que l'extra `memory` apporte, et que le chargement des vrais modèles importe.

## Classe `loom_ia.tools_builtin.memory.server.app._Reply`

```python
class _Reply(Exception)
```

Une réponse d'erreur : son code, son message, ses en-têtes.

## Classe `loom_ia.tools_builtin.memory.server.app.Host`

```python
@dataclass(slots=True)
class Host
```

Les deux modèles servis, leur chargement et leur état.

`prepare` tourne avant le chargement (plafond de VRAM) ; `vram` lit la
mémoire de la carte réservée par le process, ou `None` hors carte.

### Méthode `loom_ia.tools_builtin.memory.server.app.Host.load`

```python
def load(self) -> None
```

Charge les deux modèles et les essaie ; l'échec est gardé dans `state`.

### Méthode `loom_ia.tools_builtin.memory.server.app.Host.vram_used_mb`

```python
def vram_used_mb(self) -> int | None
```

La VRAM occupée, en Mo ; `None` si elle ne se mesure pas.

### Méthode `loom_ia.tools_builtin.memory.server.app.Host.health`

```python
def health(self) -> Health
```

L'état de l'application ; une fois prête, ses modèles et ses plafonds.

### Méthode `loom_ia.tools_builtin.memory.server.app.Host.embed`

```python
def embed(self, texts: Sequence[str]) -> list[Embedding]
```

Les vecteurs des textes.

### Méthode `loom_ia.tools_builtin.memory.server.app.Host.score`

```python
def score(self, query: str, passages: Sequence[str]) -> list[float]
```

Les scores des passages pour la question.

## Fonction `loom_ia.tools_builtin.memory.server.app.bge_host`

```python
def bge_host(settings: ServiceSettings) -> Host
```

BGE-M3 et son reranker, avec les réglages de l'application ; rien n'est chargé ici.

## Classe `loom_ia.tools_builtin.memory.server.app.ModelServer`

```python
class ModelServer(ThreadingHTTPServer)
```

Le serveur : un thread par requête ; à l'arrêt, les requêtes en cours finissent.

## Classe `loom_ia.tools_builtin.memory.server.app._Handler`

```python
class _Handler(BaseHTTPRequestHandler)
```

Une requête HTTP : `GET /v1/health`, `POST /v1/embed` et `POST /v1/score`, jeton vérifié s'il en faut un.

### Méthode `loom_ia.tools_builtin.memory.server.app._Handler.models`

```python
@property
def models(self) -> ModelServer
```

Le serveur, et ce qu'il porte : l'hôte des modèles et la file.

### Méthode `loom_ia.tools_builtin.memory.server.app._Handler.log_message`

```python
def log_message(self, format: str, *args: Any) -> None
```

Rien : le journal d'accès est écrit par `_respond`, sans adresse ni contenu.

### Méthode `loom_ia.tools_builtin.memory.server.app._Handler.do_GET`

```python
def do_GET(self) -> None
```

Sert `/v1/health` : 200 une fois prête, 503 avant (Retry-After pendant le chargement).

### Méthode `loom_ia.tools_builtin.memory.server.app._Handler.do_POST`

```python
def do_POST(self) -> None
```

Sert `/v1/embed` et `/v1/score` : modèles prêts, corps lu, une place dans la file (sinon 503 et Retry-After), puis le calcul.

### Méthode `loom_ia.tools_builtin.memory.server.app._Handler._ready`

```python
def _ready(self) -> None
```

Refuse la requête (503) tant que les modèles ne sont pas chargés, ou si leur chargement a raté.

### Méthode `loom_ia.tools_builtin.memory.server.app._Handler._body`

```python
def _body(self) -> bytes
```

Le corps, lu seulement si `Content-Length` est donné et sous le plafond.

### Méthode `loom_ia.tools_builtin.memory.server.app._Handler._respond`

```python
def _respond(self, status: HTTPStatus, payload: BaseModel, started: float, *, count: int=0, retry: bool=False, exclude_none: bool=False) -> None
```

Envoie la réponse en JSON, Retry-After au besoin, et l'inscrit au journal d'accès : méthode, chemin, code, nombre de textes, durée.

## Classe `loom_ia.tools_builtin.memory.server.app.PortTaken`

```python
class PortTaken(Exception)
```

Le port est déjà pris : une autre instance tourne, ou un autre programme.

## Classe `loom_ia.tools_builtin.memory.server.app.Service`

```python
class Service
```

Le serveur et ses modèles : ouvert, chargé, servi, arrêté.

### Méthode `loom_ia.tools_builtin.memory.server.app.Service.server`

```python
@property
def server(self) -> ModelServer
```

Le serveur HTTP lié.

### Méthode `loom_ia.tools_builtin.memory.server.app.Service.address`

```python
@property
def address(self) -> tuple[str, int]
```

L'adresse et le port d'écoute.

### Méthode `loom_ia.tools_builtin.memory.server.app.Service.bind`

```python
def bind(self, host: str, port: int) -> None
```

Ouvre le port ; `PortTaken` s'il est déjà pris. Rien n'est chargé.

### Méthode `loom_ia.tools_builtin.memory.server.app.Service.start_loading`

```python
def start_loading(self) -> threading.Thread
```

Charge les modèles dans un thread ; en échec, arrête le service après un délai.

### Méthode `loom_ia.tools_builtin.memory.server.app.Service.serve_forever`

```python
def serve_forever(self) -> None
```

Sert les requêtes jusqu'à l'arrêt.

### Méthode `loom_ia.tools_builtin.memory.server.app.Service.stop`

```python
def stop(self, code: int=EXIT_OK) -> None
```

Demande l'arrêt ; sans attendre (appelable depuis un gestionnaire de signal).

### Méthode `loom_ia.tools_builtin.memory.server.app.Service.close`

```python
def close(self) -> None
```

Ferme le port, une fois les requêtes en cours finies.

## Fonction `loom_ia.tools_builtin.memory.server.app.missing_modules`

```python
def missing_modules() -> list[str]
```

Les paquets de l'extra `memory` absents de ce Python.

## Fonction `loom_ia.tools_builtin.memory.server.app.preflight`

```python
def preflight(settings: ServiceSettings) -> str | None
```

Ce qui empêcherait de démarrer, vu sans rien charger ni ouvrir ; `None` si rien.

Le jeton (`token_env`) doit être lu, et l'extra `memory` installé. `run` le
vérifie avant d'ouvrir le port, `serve --check` (l'aide à l'installation) aussi.

## Fonction `loom_ia.tools_builtin.memory.server.app.run`

```python
def run(settings: ServiceSettings) -> int
```

Lance l'application jusqu'à son arrêt ; rend le code de sortie.
