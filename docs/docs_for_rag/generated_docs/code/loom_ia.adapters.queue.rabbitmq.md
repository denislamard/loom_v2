# loom_ia.adapters.queue.rabbitmq

Module `loom_ia.adapters.queue.rabbitmq` de loom_v2 (fichier `src/loom_ia/adapters/queue/rabbitmq.py`).

File de tâches servie par RabbitMQ : les runs tournent dans les workers (point 27, fonction H6).

Deux moitiés dans la même classe, et une seule vit à la fois dans un process :

- **publier** (`submit`) : c'est ce que fait une instance `Loom` ordinaire,
  `loom serve` comprise. Elle met en file et n'exécute rien ;
- **consommer** (`serve`) : c'est ce que fait `loom worker`, et lui seul.

Ce partage est le but de la phase : un process web qui pilote des runs n'en est
plus un. La contrepartie, dite au chargement par `loom validate`, est qu'une
file `rabbitmq` sans worker derrière laisse ses tâches attendre.

**Au moins une fois, jamais exactement une fois.** L'acquittement suit
l'exécution : un worker tué sans préavis ne l'envoie pas, et le courtier
redélivre. Deux pilotes pour un même run, c'est la concession qui les sépare
(point 27) ; une tâche rejouée retrouve un état qu'elle reconnaît, puisque le
journal fait foi. Une tâche qui **échoue** est acquittée quand même, et
journalisée : la rejouer sans fin serait pire que de la perdre, et le run a
écrit son échec au journal. Un message que ce worker ne traitera **jamais**
(illisible, champ absent, type inconnu, type sans traitement ici — un worker
plus ancien que celui qui publie) est mis de côté : recopié tel quel, son
motif dans l'en-tête `x-loom-reason`, dans la file `loom.jobs.dead`, puis
acquitté. Le rejouer tournerait en rond, le jeter le perdrait ; de là, un
opérateur peut le relire ou le republier. Si même la copie échoue, son corps
est au log.
Un acquittement que le courtier refuse (canal fermé) est journalisé et
n'abat pas le worker : le courtier redélivre, c'est le contrat.

**Le délai d'acquittement du courtier.** RabbitMQ ferme le canal d'un
consommateur qui garde un message plus de `consumer_timeout` (30 minutes par
défaut) sans l'acquitter, et redélivre ce message. Un run plus long que ce
délai est donc redélivré : la concession empêche qu'il soit piloté deux fois,
mais le réglage du courtier doit dépasser le run le plus long
(`consumer_timeout` dans `rabbitmq.conf`, ou une politique sur la file).
`loom worker` le rappelle au démarrage, et un acquittement refusé le dit.

**Le délai n'existe pas chez RabbitMQ.** Un travail différé va dans une file
sans consommateur, avec une durée de vie par message ; à l'échéance, le
courtier le laisse tomber dans la file de travail (`dead-letter`). Le piège
est connu et assumé : cette file d'attente est une file, donc un message à
longue échéance retient ceux qui le suivent. Les délais de loom sont du même
ordre de grandeur (l'expiration d'une approbation), et cette expiration est de
toute façon relue au journal (jalon J4.3a) — le travail différé n'est qu'un rappel.

`state` rend toujours `unknown` et `cancel` toujours faux : un courtier
ne sait pas dire où en est une tâche, et ne retire pas un message publié. Le
port l'a prévu, et l'état d'un run se lit au journal.

## Constantes de `loom_ia.adapters.queue.rabbitmq`

- `WORK_QUEUE = 'loom.jobs'` : File de travail des workers (voir aussi `DELAY_QUEUE` et `DEAD_QUEUE`).
- `DELAY_QUEUE = 'loom.jobs.delayed'` : File d'attente des travaux différés.
- `DEAD_QUEUE = 'loom.jobs.dead'` : File des travaux mis de côté, ceux qu'aucun worker de cette version ne traitera.
- `REASON_HEADER = 'x-loom-reason'` : En-tête d'un travail mis de côté : pourquoi il l'a été.
- `_LOGGED_BODY = 2000` : Ce que le log garde du corps d'un message qui n'a pas pu être mis de côté.
- `DELAY_ARGUMENTS = {'x-dead-letter-exchange': '', 'x-dead-letter-routing-key': WORK_QUEUE}` : À l'échéance de sa durée de vie, un message de la file d'attente retombe dans la file de travail. Exposées, parce que RabbitMQ refuse une redéclaration qui diverge : qui déclare cette file ailleurs doit le faire à l'identique.

## Fonction `loom_ia.adapters.queue.rabbitmq.encoded`

```python
def encoded(job_id: str, job: Job) -> bytes
```

Le travail tel qu'il voyage : du JSON, lisible dans l'interface du courtier.

## Fonction `loom_ia.adapters.queue.rabbitmq.decoded`

```python
def decoded(body: bytes) -> tuple[str, Job]
```

Rend l'identifiant et le travail ; `ValueError` si le message n'en est pas un.

## Fonction `loom_ia.adapters.queue.rabbitmq._settle`

```python
async def _settle(message: AbstractIncomingMessage, *, requeue: bool | None=None, ran: float=0.0) -> None
```

Acquitte le message (ou, avec `requeue`, le remet en file) sans jamais lever.

Un canal fermé fait échouer la réponse : le courtier redélivre alors le
message, ce que le contrat « au moins une fois » prévoit. L'échec n'a pas à
abattre le worker, ni les travaux qui tournent à côté. `ran` : la durée
du travail, que le log donne pour qu'on la compare au délai du courtier.

## Classe `loom_ia.adapters.queue.rabbitmq.RabbitMqTaskQueue`

```python
class RabbitMqTaskQueue
```

File durable chez un courtier ; les tâches tournent dans `loom worker`.

`handlers` ne sert qu'à consommer : une instance qui ne fait que publier
peut les avoir sans que rien ne les appelle.

### Méthode `loom_ia.adapters.queue.rabbitmq.RabbitMqTaskQueue._work_queue`

```python
async def _work_queue(self) -> AbstractQueue
```

Le canal et les deux files, déclarés une fois, à la première demande.

Les files sont déclarées **aussi par celui qui publie** : un message
adressé à une file qui n'existe pas est jeté sans un mot par le
courtier.

### Méthode `loom_ia.adapters.queue.rabbitmq.RabbitMqTaskQueue.submit`

```python
async def submit(self, job: Job, *, key: str | None=None, delay: float | None=None) -> str
```

Publie le travail et rend son identifiant.

`key` **est** l'identifiant quand elle est donnée : le courtier ne
dédoublonne pas, mais deux mises en file de la même clé rendent le même
identifiant, et ce que la clé protégeait est protégé ailleurs — la
concession pour un run, l'idempotence pour un effet.

### Méthode `loom_ia.adapters.queue.rabbitmq.RabbitMqTaskQueue.state`

```python
async def state(self, job_id: str) -> JobState
```

Toujours `unknown` : un courtier ne suit pas ses messages (voir l'en-tête).

### Méthode `loom_ia.adapters.queue.rabbitmq.RabbitMqTaskQueue.cancel`

```python
async def cancel(self, job_id: str) -> bool
```

Toujours faux : un message publié ne se retire pas. Annuler un run, si.

### Méthode `loom_ia.adapters.queue.rabbitmq.RabbitMqTaskQueue.drain`

```python
async def drain(self) -> None
```

Rien à attendre : ce qui est publié tourne ailleurs.

### Méthode `loom_ia.adapters.queue.rabbitmq.RabbitMqTaskQueue.serve`

```python
async def serve(self, *, jobs: int=1) -> None
```

Consomme jusqu'à l'arrêt ; ne rend la main qu'une fois tout fini.

`jobs` est le nombre de travaux menés en même temps, et c'est aussi
ce que le courtier accepte de confier d'avance à ce worker.

### Méthode `loom_ia.adapters.queue.rabbitmq.RabbitMqTaskQueue.stop`

```python
async def stop(self) -> None
```

Demande l'arrêt : plus de travail pris, ceux en cours vont au bout.

### Méthode `loom_ia.adapters.queue.rabbitmq.RabbitMqTaskQueue._handle`

```python
async def _handle(self, message: AbstractIncomingMessage, places: asyncio.Semaphore) -> None
```

Mène un travail, puis l'acquitte — dans cet ordre, c'est tout l'enjeu.

### Méthode `loom_ia.adapters.queue.rabbitmq.RabbitMqTaskQueue._set_aside`

```python
async def _set_aside(self, message: AbstractIncomingMessage, reason: str) -> None
```

Recopie le message dans `DEAD_QUEUE`, son motif en en-tête, puis l'acquitte.

Si la copie échoue, le message est acquitté quand même — le remettre en
file le ferait revenir ici — et son corps est au log.

### Méthode `loom_ia.adapters.queue.rabbitmq.RabbitMqTaskQueue.aclose`

```python
async def aclose(self) -> None
```

Arrête de consommer, puis ferme la connexion au courtier.
