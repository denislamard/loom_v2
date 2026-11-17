# loom_ia.core.ports.queue

Module `loom_ia.core.ports.queue` de loom_v2 (fichier `src/loom_ia/core/ports/queue.py`).

Port de la file de tâches de fond (point 27, fonctions H5 et H7).

La durabilité ne vient pas de la file, mais du journal : au démarrage, les
runs restés dans un état actionnable sont remis en file, et une livraison en
double est sans risque puisque l'état se reconstruit depuis le journal. Une
file non durable suffit donc en mode librairie.

C'est ce qui permet d'assumer une livraison **au moins une fois** : un
courtier qui redélivre après la mort d'un worker est dans son droit, la
concession (point 27) refuse le second pilote d'un run déjà tenu, et une tâche
rejouée retrouve un état qu'elle reconnaît. Un courtier ne sait pas non plus
toujours répondre sur une tâche : `state` peut rendre `unknown` et
`cancel` faux sans que rien n'aille mal — le journal fait foi, pas la file.

Quatre types de tâche (`JobKind`) : le résumé d'une session (`compaction`),
un run lancé en arrière-plan (`run`), sa reprise après une approbation
(`resume`) et l'expiration différée d'une demande d'approbation
(`expire_approval`).

Implémentations fournies (`adapters/queue`) : asyncio, dans le process
(défaut), et RabbitMQ, consommée par `loom worker`. La file se déclare sous
`storage.queue` (`max_running` : 16 tâches de front par défaut).

## Constantes de `loom_ia.core.ports.queue`

- `JobKind = Literal['compaction', 'run', 'resume', 'expire_approval']` : Type d'une tâche de fond.  `compaction` : résumé d'une session (phase 4.1b). `run` et `resume` : run en arrière-plan et reprise après approbation (phases 4.2 et 4.3). `expire_approval` : tâche différée qui fait expirer une demande d'approbation (phase 4.3).
- `JobState = Literal['pending', 'running', 'done', 'failed', 'cancelled', 'unknown']` : État d'une tâche ; `unknown` quand la file ne la connaît plus (ou ne sait pas répondre).

## Classe `loom_ia.core.ports.queue.Job`

```python
@dataclass(frozen=True, slots=True, kw_only=True)
class Job
```

Tâche à faire hors du run qui l'a demandée.

**Attributs :**

- `kind` : Type de la tâche.
- `tenant_id` : Client concerné.
- `session_id` : Session concernée.
- `run_id` : Run concerné, quand la tâche en vise un.
- `params` : Paramètres propres au type de tâche.

## Classe `loom_ia.core.ports.queue.TaskQueue`

```python
class TaskQueue(Protocol)
```

File des tâches de fond : runs en arrière-plan, reprises, résumés, expirations.

### Méthode `loom_ia.core.ports.queue.TaskQueue.submit`

```python
async def submit(self, job: Job, *, key: str | None=None, delay: float | None=None) -> str
```

Met une tâche en file.

**Arguments :**

- `job` : La tâche.
- `key` : Clé de dédoublonnage : une tâche de même clé déjà en attente ou en cours rend son identifiant, sans en créer une seconde.
- `delay` : Départ différé, en secondes.

**Renvoie :**

L'identifiant de la tâche.

### Méthode `loom_ia.core.ports.queue.TaskQueue.state`

```python
async def state(self, job_id: str) -> JobState
```

État d'une tâche ; `unknown` si la file ne la connaît plus.

**Arguments :**

- `job_id` : Identifiant rendu par `submit`.

### Méthode `loom_ia.core.ports.queue.TaskQueue.cancel`

```python
async def cancel(self, job_id: str) -> bool
```

Annule une tâche.

**Arguments :**

- `job_id` : Identifiant rendu par `submit`.

**Renvoie :**

Vrai si la tâche est annulée ; faux si elle est déjà terminée ou
inconnue.

### Méthode `loom_ia.core.ports.queue.TaskQueue.drain`

```python
async def drain(self) -> None
```

Attend les tâches en cours **dans ce process**, sans en accepter d'autres.

Une file qui exécute chez elle attend ses tâches ; une file qui ne
fait que publier n'a rien à attendre — ce qu'elle a publié tourne
ailleurs, et c'est le journal qui dit où il en est.

### Méthode `loom_ia.core.ports.queue.TaskQueue.aclose`

```python
async def aclose(self) -> None
```

Ferme la file et libère ses ressources.

## Classe `loom_ia.core.ports.queue.ServedQueue`

```python
@runtime_checkable
class ServedQueue(Protocol)
```

Une file dont les tâches se consomment depuis un autre process (fonction H7).

Ce que le port principal ne dit pas : une file en mémoire exécute ce
qu'on lui donne, tandis qu'une file chez un courtier attend un
consommateur — `loom worker`. C'est à cette forme-là que la commande
s'adresse, et c'est ainsi qu'elle refuse une file qui n'en est pas une.

### Méthode `loom_ia.core.ports.queue.ServedQueue.serve`

```python
async def serve(self, *, jobs: int=1) -> None
```

Consomme les tâches jusqu'à l'arrêt ; c'est ce que fait `loom worker`.

**Arguments :**

- `jobs` : Tâches menées de front au plus (`loom worker --jobs`). Défaut : 1.

### Méthode `loom_ia.core.ports.queue.ServedQueue.stop`

```python
async def stop(self) -> None
```

Demande l'arrêt : plus de tâche prise, celles en cours vont au bout.
