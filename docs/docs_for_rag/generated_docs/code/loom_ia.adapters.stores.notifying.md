# loom_ia.adapters.stores.notifying

Module `loom_ia.adapters.stores.notifying` de loom_v2 (fichier `src/loom_ia/adapters/stores/notifying.py`).

Journal qui prévient ses abonnés à chaque écriture (point 22).

Les trois accès de la phase 1.6 doivent suivre un run pendant qu'il se
déroule. Plutôt que de relire le journal en boucle, cet enrobage remet
chaque événement écrit aux abonnés du process, dans l'ordre d'écriture.

Deux formes d'abonnement, la seconde bâtie sur la première :

- `listen(sink)` : une fonction appelée au moment de l'écriture ; c'est
  elle qui garantit l'ordre quand l'appelant mêle les événements à une autre
  source, comme les morceaux du modèle ;
- `subscribe()` : un objet qui s'itère en `async for`.

Les deux se restreignent à un run (`run_id`) ou à un filtre (`accept`),
appelé à chaque écriture dans l'ordre du journal : l'arbre d'un run
(`RunTree.admit`) s'y reconnaît au fil de l'eau.

C'est la place que prendra le bus du jalon J4 : le jour où les événements
passeront par un courtier, seuls ces deux abonnements changeront de source.

## Constantes de `loom_ia.adapters.stores.notifying`

- `EventSink = Callable[[Event], None]` : Abonné : appelé avec chaque événement remis ; il ne doit rien attendre.
- `EventFilter = Callable[[Event], bool]` : Filtre d'un abonné : vrai pour un événement à lui remettre.
- `FOLLOW_BACKOFF = (1.0, 2.0, 5.0, 10.0, 30.0)` : Attentes successives, en secondes, avant de se réabonner à un bus tombé : la dernière vaut ensuite pour toutes les tentatives suivantes.
- `PUBLISH_TIMEOUT = 5.0` : Attente maximale d'une publication sur le bus : au-delà, la nouvelle est abandonnée (l'écriture, elle, a eu lieu), et un bus muet ne suspend pas chaque écriture.

## Classe `loom_ia.adapters.stores.notifying.Subscription`

```python
class Subscription
```

File d'événements alimentée par les écritures du journal.

S'itère en `async for` jusqu'à la fermeture de l'abonnement. La file
n'a pas de borne : un abonné lent retarde sa propre lecture, jamais
l'écriture du journal.

### Méthode `loom_ia.adapters.stores.notifying.Subscription.offer`

```python
def offer(self, event: Event) -> None
```

Met un événement en file, sauf si l'abonnement est fermé.

### Méthode `loom_ia.adapters.stores.notifying.Subscription.close`

```python
def close(self) -> None
```

Termine l'itération, après les événements déjà en file.

### Méthode `loom_ia.adapters.stores.notifying.Subscription.pending`

```python
@property
def pending(self) -> int
```

Événements en file, pas encore lus.

## Classe `loom_ia.adapters.stores.notifying.NotifyingEventStore`

```python
class NotifyingEventStore
```

Enrobe un journal et remet ce qu'il écrit aux abonnés du process.

Avec un `bus` (jalon J5.3c), il remet aussi ce que **les autres process**
écrivent : leurs nouvelles disent où regarder, le journal est relu, et les
événements retrouvés vont aux mêmes abonnés. Un abonné ne sait pas d'où
vient ce qu'il reçoit, et c'est tout l'intérêt — SSE, `Loom.stream()`,
la progression MCP et la CLI n'ont pas changé d'une ligne.

### Méthode `loom_ia.adapters.stores.notifying.NotifyingEventStore.inner`

```python
@property
def inner(self) -> EventStore
```

Le journal enrobé.

### Méthode `loom_ia.adapters.stores.notifying.NotifyingEventStore.listeners`

```python
@property
def listeners(self) -> int
```

Nombre d'abonnés en cours.

### Méthode `loom_ia.adapters.stores.notifying.NotifyingEventStore.listen`

```python
@contextmanager
def listen(self, sink: EventSink, run_id: RunId | None=None, *, accept: EventFilter | None=None, own: bool=False) -> Generator[EventSink]
```

Appelle `sink` à chaque écriture, le temps du bloc.

Sans `run_id` ni `accept`, tous les événements écrits sont remis.
`sink` et `accept` sont appelés depuis `append` : ils ne doivent
rien attendre. `own` écarte ce que le bus rapporte des autres
process : l'abonné ne reçoit que ce que cette instance écrit — c'est
ce qu'il faut à un export, que chaque process fait pour sa part (jalon J6.1a).

### Méthode `loom_ia.adapters.stores.notifying.NotifyingEventStore.subscribe`

```python
@asynccontextmanager
async def subscribe(self, run_id: RunId | None=None, *, accept: EventFilter | None=None) -> AsyncGenerator[Subscription]
```

Abonnement itérable, fermé à la sortie du bloc.

### Méthode `loom_ia.adapters.stores.notifying.NotifyingEventStore.append`

```python
async def append(self, drafts: Sequence[EventDraft], *, expected_seq: int | None) -> list[Event]
```

Écrit un lot à la suite du journal d'une session (`EventStore.append`).

Le lot est écrit dans le journal enrobé, remis aux abonnés de ce process,
puis annoncé sur le bus s'il y en a un. Un abonné qui échoue est
journalisé, sans faire échouer l'écriture.

**Arguments :**

- `drafts` : Le lot, dans un seul journal (même client, même session).
- `expected_seq` : Dernier `seq` connu de l'appelant ; `None` désactive ce contrôle.

**Renvoie :**

Les événements écrits, numérotés à la suite.

**Lève :**

- `SequenceConflict` : Le journal a avancé depuis `expected_seq` ; rien n'est écrit.

### Méthode `loom_ia.adapters.stores.notifying.NotifyingEventStore._offer`

```python
def _offer(self, event: Event, *, own: bool) -> None
```

Remet un événement aux abonnés que son run et leur filtre acceptent.

Un abonné ou un filtre qui lève est journalisé, et les suivants sont
servis quand même : l'écriture a eu lieu, elle ne peut plus échouer
pour ça, et la nouvelle au bus (`_announce`) doit partir.

### Méthode `loom_ia.adapters.stores.notifying.NotifyingEventStore.source`

```python
@property
def source(self) -> str
```

Identité de cette instance sur le bus.

### Méthode `loom_ia.adapters.stores.notifying.NotifyingEventStore._announce`

```python
async def _announce(self, events: Sequence[Event]) -> None
```

Dit aux autres process qu'il y a du neuf. N'échoue jamais.

Une écriture au journal a eu lieu : la manquer sur le bus coûte une
notification, pas un événement, et celui qui la rate rattrape à la
suivante. Faire échouer l'écriture pour un bus en panne serait
disproportionné.

### Méthode `loom_ia.adapters.stores.notifying.NotifyingEventStore.follow`

```python
async def follow(self) -> None
```

Suit le bus jusqu'à sa fermeture, et remet ce que les autres écrivent.

Une panne du bus n'arrête pas le suivi : elle est journalisée, puis
l'abonnement est repris après une attente croissante
(`FOLLOW_BACKOFF`). Ce que les autres ont écrit pendant la coupure
se rattrape à la nouvelle suivante, depuis la position gardée. Le suivi
s'arrête quand le bus rend la main (il est fermé), ou à l'annulation,
qui coupe aussi l'attente avant de se réabonner.

### Méthode `loom_ia.adapters.stores.notifying.NotifyingEventStore._catch_up`

```python
async def _catch_up(self, notice: Notice) -> None
```

Relit ce qui manque pour ce journal et le remet aux abonnés.

Depuis notre position s'il y en a une — ce qui rattrape ce que le bus
aurait laissé passer —, depuis le début du lot annoncé sinon.

### Méthode `loom_ia.adapters.stores.notifying.NotifyingEventStore.read`

```python
async def read(self, tenant_id: TenantId, session_id: SessionId, *, after_seq: int=0, run_id: RunId | None=None) -> list[Event]
```

Lit le journal enrobé (`EventStore.read`).

### Méthode `loom_ia.adapters.stores.notifying.NotifyingEventStore.query`

```python
async def query(self, query: EventQuery) -> list[Event]
```

Cherche dans le journal enrobé (`EventStore.query`).

### Méthode `loom_ia.adapters.stores.notifying.NotifyingEventStore.last_seq`

```python
async def last_seq(self, tenant_id: TenantId, session_id: SessionId) -> int
```

Dernier `seq` du journal enrobé (`EventStore.last_seq`).

### Méthode `loom_ia.adapters.stores.notifying.NotifyingEventStore.sessions`

```python
async def sessions(self, tenant_id: TenantId) -> list[SessionRecord]
```

Sessions du journal enrobé (`EventStore.sessions`).

### Méthode `loom_ia.adapters.stores.notifying.NotifyingEventStore.delete`

```python
async def delete(self, tenant_id: TenantId, session_id: SessionId) -> int
```

Supprime le journal d'une session (RGPD) ; rend le nombre d'événements supprimés.

Oublie aussi la position atteinte : recréé sous le même identifiant, le
journal repart de 1. Les autres process gardent la leur, le bus
n'annonçant pas les suppressions.

### Méthode `loom_ia.adapters.stores.notifying.NotifyingEventStore.aclose`

```python
async def aclose(self) -> None
```

Retire tous les abonnés, puis ferme le journal enrobé.
