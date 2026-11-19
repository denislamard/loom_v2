# loom_ia.adapters.queue.asyncio_queue

Module `loom_ia.adapters.queue.asyncio_queue` de loom_v2 (fichier `src/loom_ia/adapters/queue/asyncio_queue.py`).

File de tâches en mémoire, dans le process de l'instance (point 27).

Mode librairie : une tâche asyncio par travail, sans stockage. La durabilité
vient du journal — une tâche perdue au redémarrage est retrouvée par la
reprise, pas par la file.

`key` dédoublonne : un même travail demandé deux fois pendant qu'il tourne
n'est lancé qu'une fois (un seul résumé par session et par position, point 23).
Un travail qui se repose lui-même, depuis sa propre tâche, n'est pas un doublon :
c'est ainsi qu'un run tenu par un autre worker est repris après son bail
(`Loom._after_the_lease`).
`max_running` borne les travaux menés de front : les suivants attendent
leur tour, `pending`. Une reprise au démarrage remet d'un coup tous les
runs laissés en plan ; sans borne, ils partaient tous ensemble.
Une tâche qui échoue est journalisée et ne fait jamais échouer le run qui
l'a demandée.

## Constantes de `loom_ia.adapters.queue.asyncio_queue`

- `Handler = Callable[[Job], Awaitable[None]]` : Ce qui mène un travail de la file.
- `MAX_REMEMBERED = 256` : Tâches terminées gardées pour qu'on puisse encore lire leur état.

## Classe `loom_ia.adapters.queue.asyncio_queue._Entry`

```python
@dataclass(slots=True)
class _Entry
```

Un travail de la file : la tâche, sa clé de dédoublonnage, son état et la tâche asyncio qui le mène.

**Attributs :**

- `job` : La tâche.
- `key` : Clé de dédoublonnage, si la tâche en a une.
- `state` : État courant.
- `task` : Tâche asyncio qui mène le travail.
- `delayed` : Travail qui attend son heure (`delay`) et n'a rien commencé.

### Méthode `loom_ia.adapters.queue.asyncio_queue._Entry.open`

```python
@property
def open(self) -> bool
```

Vrai pour un travail en attente ou en cours.

### Méthode `loom_ia.adapters.queue.asyncio_queue._Entry.busy`

```python
@property
def busy(self) -> bool
```

Travail en cours, par opposition à un travail qui dort encore.

## Classe `loom_ia.adapters.queue.asyncio_queue.AsyncioTaskQueue`

```python
class AsyncioTaskQueue
```

File non durable : une `asyncio.Task` par travail.

### Méthode `loom_ia.adapters.queue.asyncio_queue.AsyncioTaskQueue.submit`

```python
async def submit(self, job: Job, *, key: str | None=None, delay: float | None=None) -> str
```

Met une tâche en file et rend son identifiant (`TaskQueue.submit`).

Avec `key`, un travail de même clé encore ouvert n'est pas relancé : son
identifiant est rendu. Sauf s'il s'agit du travail qui appelle : il se
repose lui-même (après le bail d'un autre pilote), la nouvelle tâche
prend la clé, et la fin de l'ancienne ne la libère pas. Avec `delay`,
le travail attend d'abord ce nombre de secondes.

**Arguments :**

- `job` : La tâche.
- `key` : Clé de dédoublonnage.
- `delay` : Attente avant de commencer, en secondes.

**Lève :**

- `RuntimeError` : La file est fermée.

### Méthode `loom_ia.adapters.queue.asyncio_queue.AsyncioTaskQueue.state`

```python
async def state(self, job_id: str) -> JobState
```

État d'une tâche ; `unknown` si la file ne la connaît plus.

### Méthode `loom_ia.adapters.queue.asyncio_queue.AsyncioTaskQueue.cancel`

```python
async def cancel(self, job_id: str) -> bool
```

Annule une tâche ouverte ; faux si elle est inconnue ou déjà terminée.

### Méthode `loom_ia.adapters.queue.asyncio_queue.AsyncioTaskQueue.drain`

```python
async def drain(self) -> None
```

Attend les tâches en cours, sans en accepter de nouvelles.

Un travail différé qui dort encore n'est pas une tâche en cours : il
n'a rien commencé, et l'attendre bloquerait pour toute la durée de son
délai. Ce qu'il devait faire se retrouve de toute façon au journal.

### Méthode `loom_ia.adapters.queue.asyncio_queue.AsyncioTaskQueue.aclose`

```python
async def aclose(self) -> None
```

Attend les tâches en cours, puis abandonne celles qui traînent.

Les travaux différés qui dorment encore sont abandonnés sans attendre :
rien ne justifie de retenir la fermeture pour un réveil à venir.

### Méthode `loom_ia.adapters.queue.asyncio_queue.AsyncioTaskQueue.abandon`

```python
async def abandon(self) -> None
```

Annule les tâches en cours au lieu de les attendre, et attend qu'elles s'arrêtent.

Une tâche qui n'a pas commencé ne commence pas : ce qu'elle devait faire
se retrouve au journal, comme après un arrêt brutal. Une tâche en route
s'arrête là où elle en est, sans rien écrire de plus ; le temps laissé
aux tâches pour s'arrêter est borné par `shutdown_timeout`.

### Méthode `loom_ia.adapters.queue.asyncio_queue.AsyncioTaskQueue.open`

```python
@property
def open(self) -> int
```

Tâches en attente ou en cours.

### Méthode `loom_ia.adapters.queue.asyncio_queue.AsyncioTaskQueue._work`

```python
async def _work(self, job_id: str, entry: _Entry, delay: float | None) -> None
```

Mène un travail : attente du délai, place parmi les travaux de front, gestionnaire de son genre.

Un échec est journalisé et marque la tâche `failed` sans remonter ; une
annulation la marque `cancelled`. La clé est libérée à la fin.

### Méthode `loom_ia.adapters.queue.asyncio_queue.AsyncioTaskQueue._place`

```python
def _place(self) -> AbstractAsyncContextManager[object]
```

Une place parmi les travaux menés de front, ou aucune attente sans borne.

### Méthode `loom_ia.adapters.queue.asyncio_queue.AsyncioTaskQueue._forget_old`

```python
def _forget_old(self) -> None
```

Oublie les tâches terminées les plus anciennes.
