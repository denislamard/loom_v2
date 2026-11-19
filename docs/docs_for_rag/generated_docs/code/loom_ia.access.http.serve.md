# loom_ia.access.http.serve

Module `loom_ia.access.http.serve` de loom_v2 (fichier `src/loom_ia/access/http/serve.py`).

Serveur HTTP de développement (fonction N2), et son rechargement (point 48, jalon J6.4a).

`uvicorn` sert l'application ; la configuration donne l'adresse et le port,
que la ligne de commande peut remplacer. La journalisation reste celle de
loom (`telemetry.logging`) : uvicorn ne réinstalle pas la sienne.

`loom serve --reload` (`serve_reloading`) relance le **process** qui sert
quand un fichier change — config relue, agents remontés, modules voisins
réimportés, serveurs MCP relancés ; rien ne se recharge à chaud dans un
process vivant. Un superviseur garde la socket et surveille les fichiers ; à
chaque changement :

1. un **nouveau** process charge la config et monte chaque agent pour chaque
   client, pendant que l'ancien sert encore ;
2. s'il échoue — config cassée, module voisin qui ne s'importe pas, outil
   introuvable —, il le dit et sort : l'ancien continue de servir ;
3. sinon il prend la socket, et l'ancien s'arrête comme sur un Ctrl+C : il
   ne prend plus de requêtes, finit celles en cours et ses runs (jusqu'à
   `execution.shutdown_timeout`), puis sort.

Les deux process partagent la socket : une requête qui arrive pendant la
relève attend dans sa file, elle n'est pas refusée.

Ce qui est surveillé (décision A-9) : tout le dossier de la config (et
`agents_dir`, `resources_dir` s'ils sont ailleurs), les modules Python
(`.py`) de `workpath` s'il est ailleurs, et l'amorce, **sauf** ce que la
config écrit — journal, fichiers, bases SQLite, ceux de chaque client — et le
bruit d'usage (`__pycache__`, `.git`, `.venv`, fichiers temporaires
d'éditeur). Dans `workpath`, seuls les `.py` comptent : les outils y
écrivent leurs données, qui ne doivent pas relancer le service. L'amorce et
le fichier d'environnement, hors des dossiers surveillés, le sont seuls.
Chaque process neuf le relit : une clé changée y est prise, une variable du
shell reste celle du shell.

Rien ne passe entre deux guetteurs. « Rechargement : surveille … » n'est dit
qu'une fois le guetteur armé ; et ce qui a changé avant — pendant que le
premier process démarrait, ou que les dossiers surveillés changeaient — se
voit en comparant l'état des fichiers (date, taille, inode) que le process a
relevé avant de lire sa config à celui du disque, guetteur armé. Un écart
recharge, comme un changement vu par le guetteur.

Le montage d'essai remplace les clients de modèle par des clients jamais
appelés : une clé d'API absente ne se voit qu'au premier run, comme sans
`--reload`. Refusé en profil `prod`.

## Constantes de `loom_ia.access.http.serve`

- `Banner = Callable[[LoomConfig, str | None, str, int, EnvFileReport], None]` : Bandeau imprimé par chaque process qui prend la main : la config, le profil demandé par l'option, l'adresse servie, le fichier d'environnement qu'il a lu.
- `REFUSED = 2` : Code de sortie d'un process qui refuse sa config : celui de la CLI.
- `NOT_STARTED = 3` : Code d'un process dont l'application n'a pas démarré (uvicorn le dit).
- `NOISE_DIRS = frozenset({'__pycache__', '.git', '.hg', '.svn', '.tox', '.venv', '.idea', 'node_modules', '.mypy_cache', '.pytest_cach…` : Dossiers de bruit, où qu'ils soient sous un dossier surveillé : ceux que `watchfiles` écarte, plus le cache de ruff.
- `NOISE_FILES = tuple((re.compile(pattern) for pattern in ('\\.py[cod]$', '\\.___jb_...___$', '\\.sw.$', '~$', '^\\.\\#', '^\\.DS_Store…` : Fichiers de bruit, par leur nom : compilés, temporaires d'éditeur.
- `SHOWN_CHANGES = 5` : Changements montrés en entier ; au-delà, le compte.
- `DEAF = 'sourd'` : Ce qu'un process qui s'arrête dit quand il ne prend plus de requêtes.
- `DEAFNESS = 5.0` : Combien de temps on attend ce mot, en secondes : au-delà, la boucle du process est bloquée, et elle ne prend rien non plus.
- `ORPHANED_EVERY = 0.5` : Tous les combien un process qui sert vérifie que son superviseur est là (secondes).
- `WAKE_MS = 200` : Tous les combien le guetteur rend la main sans changement (millisecondes) : sa première main dit qu'il est armé.
- `READS = 3` : Lectures de la config au plus, au démarrage d'un process, pour qu'elle et son relevé portent sur les mêmes dossiers.
- `FileState = tuple[int, int, int]` : L'état d'un fichier surveillé : date de modification (ns), taille, inode.

## Fonction `loom_ia.access.http.serve.serve`

```python
def serve(loom: Loom, *, host: str | None=None, port: int | None=None) -> None
```

Sert l'instance jusqu'à l'arrêt du process, puis la ferme.

`host` remplace celui de la config, et c'est lui que juge l'avertissement d'une API
sans clé déclarée (une erreur en profil prod).

## Classe `loom_ia.access.http.serve.Watched`

```python
@dataclass(frozen=True, slots=True)
class Watched
```

Ce que le rechargement surveille, et ce qu'il écarte parce que loom l'écrit.

**Attributs :**

- `roots` : Dossiers surveillés, sous-dossiers compris : celui de la config, et ceux des agents et des ressources s'ils sont ailleurs.
- `dirs` : Dossiers écrits par loom : journaux JSONL, fichiers des runs.
- `files` : Fichiers écrits par loom : bases SQLite (leurs `-wal`, `-shm` et `-journal` avec elles).
- `extra` : Fichiers surveillés seuls, hors des dossiers : l'amorce et le fichier d'environnement rangés ailleurs. Le guetteur ne les voit pas ; leur état se relève à chaque réveil du superviseur (`moved`).
- `code` : Dossiers où seuls les modules Python (`.py`) comptent : `workpath` quand il n'est pas sous un dossier de `roots` (décision A-9).

### Méthode `loom_ia.access.http.serve.Watched.counts`

```python
def counts(self, path: Path) -> bool
```

Vrai si un changement de ce fichier doit relancer le serveur.

### Méthode `loom_ia.access.http.serve.Watched.filter`

```python
def filter(self, change: Change, raw: str) -> bool
```

Le filtre donné à `watchfiles` : `counts` sur le chemin reçu.

### Méthode `loom_ia.access.http.serve.Watched.present`

```python
def present(self) -> frozenset[Path]
```

Les fichiers qui comptent, tels qu'ils sont sur le disque maintenant.

### Méthode `loom_ia.access.http.serve.Watched.state`

```python
def state(self) -> dict[Path, FileState]
```

Les fichiers qui comptent, et l'état de chacun (date, taille, inode).

### Méthode `loom_ia.access.http.serve.Watched.moved`

```python
def moved(self, known: Mapping[Path, FileState]) -> bool
```

Vrai si un fichier surveillé seul a changé depuis `known`.

Quelques `stat` par seconde : surveiller son dossier, ce serait
surveiller tout ce qu'il contient, `$HOME` peut-être.

### Méthode `loom_ia.access.http.serve.Watched.describe`

```python
def describe(self) -> str
```

Une ligne : les dossiers surveillés, puis ce qui en est écarté.

## Fonction `loom_ia.access.http.serve.watched`

```python
def watched(config: LoomConfig, env_file: Path | None=None) -> Watched
```

Ce que le rechargement surveille pour cette config.

Le dossier de la config (et ceux des agents et des ressources s'ils sont
ailleurs) en entier, les `.py` de `workpath` s'il n'est pas dessous,
l'amorce seule si elle est ailleurs. `env_file` est le fichier
d'environnement lu : hors des dossiers surveillés, il l'est seul. Lève
`ConfigError` si un dossier que loom écrit contient un dossier
surveillé : le rechargement ne saurait pas distinguer ses écritures de
celles du développeur.

## Fonction `loom_ia.access.http.serve._file_state`

```python
def _file_state(path: Path) -> FileState | None
```

L'état d'un fichier (date, taille, inode), ou None s'il n'est pas là.

## Fonction `loom_ia.access.http.serve._written`

```python
def _written(storage: StorageConfig) -> tuple[list[Path], list[Path]]
```

Les dossiers et les fichiers qu'un stockage écrit sur le disque.

## Fonction `loom_ia.access.http.serve.changed`

```python
def changed(changes: Iterable[tuple[Change, str]], before: Mapping[Path, FileState], after: Mapping[Path, FileState], base: Path) -> str
```

Les fichiers changés, en clair (`prompts/relance.md modifié, …`) ; vide si aucun.

Le verbe se lit sur le disque, avant et après, pas sur l'événement : un
éditeur qui enregistre par renommage remplace le fichier (« modifié », pas
« ajouté »), et un fichier temporaire apparu puis disparu dans le même lot
n'a rien changé — un lot qui n'a que ceux-là ne recharge pas.

L'état ajoute ce qu'aucun événement n'a dit : un changement fait avant
que le guetteur soit armé. Il n'en retire rien — une modification de
même taille dans le même tic d'horloge du disque ne change pas l'état,
l'événement la dit.

## Fonction `loom_ia.access.http.serve.serve_reloading`

```python
def serve_reloading(path: Path, *, profile: str | None, host: str, port: int, banner: Banner, env: EnvFileReport) -> int
```

Sert la config de `path` et la recharge à chaque changement ; rend le code de sortie.

Rend 0 à l'arrêt (Ctrl+C, `SIGTERM`), 2 si le premier process refuse sa
config ou si la socket ne s'ouvre pas : il n'y a alors rien à garder.
`banner` est appelée par chaque process qui prend la main ; elle doit
être une fonction de module (le process neuf la retrouve par son nom).
`env` est la lecture du fichier d'environnement faite par le
superviseur : chaque process neuf retire ce qu'elle a posé et relit.

## Classe `loom_ia.access.http.serve._Serving`

```python
@dataclass(slots=True)
class _Serving
```

Un process qui sert, et le tube par lequel il dit où il en est.

### Méthode `loom_ia.access.http.serve._Serving.leave`

```python
def leave(self) -> None
```

Lui demande de s'arrêter, et attend qu'il ne prenne plus de requêtes.

Il le dit dès qu'il a cessé d'écouter, avant de finir ses requêtes et
ses runs ; un process qui ne le dit pas dans `DEAFNESS` est laissé à
sa fin.

### Méthode `loom_ia.access.http.serve._Serving.close`

```python
def close(self) -> None
```

Attend la fin du process, puis ferme le tube.

## Classe `loom_ia.access.http.serve._Launcher`

```python
class _Launcher
```

Démarre un process qui sert, et attend qu'il ait pris la main ou renoncé.

### Méthode `loom_ia.access.http.serve._Launcher.__init__`

```python
def __init__(self, context: SpawnContext, path: Path, profile: str | None, host: str, port: int, sock: socket.socket, banner: Banner, env: EnvFileReport, stopping: threading.Event) -> None
```

Retient de quoi lancer un process qui sert.

**Arguments :**

- `context` : Contexte de multiprocessing.
- `path` : Fichier de configuration.
- `profile` : Profil demandé.
- `host` : Hôte servi.
- `port` : Port servi.
- `sock` : La socket, ouverte par le superviseur et partagée.
- `banner` : Imprime le bandeau du process qui prend la main.
- `env` : Le fichier d'environnement lu.
- `stopping` : Levé quand le superviseur s'arrête.

### Méthode `loom_ia.access.http.serve._Launcher.__call__`

```python
def __call__(self) -> tuple[_Serving, Watched, dict[Path, FileState]] | None
```

Démarre un process qui sert, et attend qu'il ait pris la main ou renoncé.

**Renvoie :**

Le process, ce qu'il fait surveiller et l'état des fichiers relevé ;
`None` s'il a renoncé (config refusée) ou si le superviseur s'arrête.

## Fonction `loom_ia.access.http.serve._bound`

```python
def _bound(host: str, port: int) -> socket.socket
```

La socket que tous les process serviront, à l'écoute dès maintenant.

## Fonction `loom_ia.access.http.serve._reaped`

```python
def _reaped(leaving: list[_Serving]) -> list[_Serving]
```

Les process encore en train de finir ; ceux qui sont sortis sont relevés.

## Fonction `loom_ia.access.http.serve._serving`

```python
def _serving(path: Path, profile: str | None, host: str, port: int, sock: socket.socket, banner: Banner, env: EnvFileReport, ready: Connection) -> None
```

Corps d'un process qui sert : charger, monter, puis prendre la socket.

## Fonction `loom_ia.access.http.serve._trace`

```python
def _trace(exc: BaseException, base: Path) -> str
```

La trace d'une erreur, réduite aux lignes du dossier de la config s'il y en a.

Une erreur de syntaxe porte déjà son fichier et sa ligne : elle se dit sans
pile. Une erreur sans aucune ligne de l'utilisateur est une erreur de
loom : elle garde toute sa pile.

## Fonction `loom_ia.access.http.serve._served`

```python
async def _served(path: Path, profile: str | None, host: str, port: int, sock: socket.socket, banner: Banner, ready: Connection, supervisor: int, env: EnvFileReport) -> bool
```

Le corps d'un process qui sert : relit le fichier d'environnement et la config, monte chaque agent pour l'essai, puis sert sur la socket partagée.

Il dit au superviseur quand il a pris la main, et quand il cesse
d'écouter. Refusé en profil prod.

**Renvoie :**

Vrai si le serveur a démarré.

## Fonction `loom_ia.access.http.serve._read`

```python
def _read(path: Path, profile: str | None, env_file: Path | None=None) -> tuple[LoomConfig, Watched, dict[Path, FileState]]
```

La config qui sert, ce qu'elle fait surveiller, et l'état des fichiers relevé avant elle.

Une première lecture dit quoi relever ; la config qui sert est lue après
le relevé. Un fichier changé entre les deux est lu dans sa nouvelle
version ; changé après, le superviseur le voit en comparant l'état — il
n'échappe jamais aux deux. Si la relecture fait surveiller d'autres
dossiers, le relevé est refait sur eux.

## Classe `loom_ia.access.http.serve._Taking`

```python
class _Taking(uvicorn.Server)
```

Un serveur uvicorn qui prévient quand il écoute — il prend la main —, et quand il cesse.

### Méthode `loom_ia.access.http.serve._Taking.startup`

```python
async def startup(self, sockets: list[socket.socket] | None=None) -> None
```

Démarre le serveur ; une fois qu'il écoute, le dit, puis surveille la présence du superviseur.

### Méthode `loom_ia.access.http.serve._Taking._abandoned`

```python
async def _abandoned(self) -> None
```

S'arrête si le superviseur disparaît sans avoir pu arrêter ses process (`kill -9`).

Sans lui, personne ne relancera ni n'arrêtera ce process, qui garderait
la socket — et le port, qu'un nouveau `loom serve` ne pourrait prendre.

### Méthode `loom_ia.access.http.serve._Taking.shutdown`

```python
async def shutdown(self, sockets: list[socket.socket] | None=None) -> None
```

Cesse d'écouter, le dit au superviseur, puis s'arrête comme uvicorn.

## Fonction `loom_ia.access.http.serve._mounted`

```python
async def _mounted(loom: Loom) -> None
```

Monte chaque agent pour chaque client, sans appeler aucun modèle.

Ce que le montage refuse — un outil, une politique ou un juge introuvable,
un serveur MCP mal déclaré — refuse la config avant qu'elle prenne la main.
Les clients de modèle sont remplacés : une clé absente ne bloque pas le
rechargement d'un agent qu'on ne lance pas.

## Classe `loom_ia.access.http.serve._Unused`

```python
class _Unused
```

Client de modèle du montage d'essai : il n'est jamais appelé.

### Méthode `loom_ia.access.http.serve._Unused.provider`

```python
@property
def provider(self) -> str
```

Fournisseur déclaré du modèle (son `sdk`).

### Méthode `loom_ia.access.http.serve._Unused.stream`

```python
async def stream(self, request: ModelRequest) -> AsyncGenerator[ModelChunk]
```

Jamais appelé : lève `RuntimeError`.

### Méthode `loom_ia.access.http.serve._Unused.aclose`

```python
async def aclose(self) -> None
```

Rien à fermer.
