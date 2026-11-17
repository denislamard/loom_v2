# loom_ia.engine.writer

Module `loom_ia.engine.writer` de loom_v2 (fichier `src/loom_ia/engine/writer.py`).

Écrivain d'une session : tous ses runs écrivent par lui (points 4 et 22).

Chaque écriture annonce le dernier `seq` qu'elle connaît (écriture
optimiste) : deux écrivains indépendants sur la même session se
contrediraient. Or un sous-agent écrit son run dans le journal de son parent,
pendant que le parent écrit le sien, et plusieurs sous-agents peuvent tourner
en parallèle. Ils partagent donc un seul écrivain : un verrou ordonne les
écritures, et le dernier `seq` est tenu à jour pour tous.

Deux runs d'une même session lancés en parallèle le partagent aussi, par le
registre `SessionWriters` de l'instance. Un autre process — un worker, un
second serveur — reste hors de portée du registre : son écriture fait avancer
le journal et la nôtre est refusée. L'écrivain lit alors ce qui a été écrit
depuis sa position. Si c'est la fin d'un run dont il écrit — arrêté, fini ou en
échec ailleurs —, il ne réécrit pas : ses brouillons parleraient d'un run que
le journal a clos, et il lève `RunMoved` (`drive` s'arrête alors sur ce que
dit le journal ; un arrêt demandé trop tard rend « déjà fini »). Sinon — un
autre run de la session, ou notre run qui avance ailleurs pendant qu'on
l'arrête —, le conflit n'invalide rien : l'écrivain relit la position et
réécrit, un nombre borné de fois.

Ouvrir un run sous un identifiant choisi (`opens`) ne tolère pas la
concurrence : deux `run.started` rendraient le journal illisible. L'écrivain
vérifie sous son verrou que le run n'a aucun événement — un écrivain partagé
suit le journal, il ne verrait aucun conflit —, puis à chaque reprise : le lot
qui perd la course lève `RunExists` au lieu de se rejouer derrière le gagnant.

Une écriture peut aussi se donner une garde (`guard`) : à chaque reprise,
elle examine ce qui s'est écrit depuis notre position et peut renoncer en
levant. C'est ce que fait la concession d'un run : si un autre worker l'a prise
entre-temps, la rejouer derrière lui ferait deux pilotes pour un run.

Le pilote d'un run qui tient sa concession écrit par une vue de l'écrivain
(`held`), qui partage sa position et son verrou (point 27). Toute écriture
reprise sur la session — la sienne, celle d'un autre run, celle de la
compaction — fait passer sous les yeux des pilotes ce qui s'est écrit depuis.
S'ils y voient un autre worker prendre la concession de leur run, ils l'ont
perdue : leur bail a expiré sans être renouvelé (store injoignable, process
gelé), et l'autre a repris le run. La vue refuse dès lors toute écriture
(`ClaimConflict`), sans rien écrire et même sans conflit de séquence : ses
brouillons partent d'un état que le journal a dépassé, et les écrire ferait
deux pilotes pour un run. De même s'ils y voient leur run clos ailleurs
(`RunMoved`) : sans cela, une écriture d'un autre run de la session, qui
avance la position commune par-dessus l'arrêt, laisserait le pilote écrire
après la clôture. Ce qui ne vient pas du pilote — un arrêt, une décision
d'approbation, la compaction — passe par l'écrivain lui-même et n'est pas
concerné.

La compaction écrit sans contrôle de séquence (`checked=False`, point 23) : son
événement ne couvre que des événements anciens, et son worker ne partage aucun
écrivain avec les runs en cours.

## Constantes de `loom_ia.engine.writer`

- `MAX_ATTEMPTS = 5` : Écritures refusées d'affilée avant d'abandonner.  Au-delà, ce n'est plus un croisement mais un journal écrit sans répit par ailleurs.
- `MAX_WRITERS = 256` : Écrivains gardés par le registre.  Au-delà, le moins récemment utilisé est oublié (un run qui le tient encore continue de s'en servir).
- `_CLOSING = (RunCompleted, RunFailed, RunCancelled)` : Ce qui clôt un run : écrit ailleurs, il rend nos brouillons sur ce run caducs.

## Classe `loom_ia.engine.writer.RunMoved`

```python
class RunMoved(RuntimeError)
```

Un autre process a clos un run que cet écrivain écrivait : rien n'a été écrit.

`drive` s'arrête alors sur ce que dit le journal.

**Arguments :**

- `run_ids` : Les runs du lot refusé.
- `events` : Les clôtures écrites ailleurs (`run.completed`, `run.failed`, `run.cancelled`).

**Attributs :**

- `run_ids` : Les runs du lot refusé.
- `events` : Les clôtures écrites ailleurs.

## Classe `loom_ia.engine.writer.ClaimConflict`

```python
class ClaimConflict(RuntimeError)
```

Un autre pilote tient la concession de ce run (point 27).

Levée avant de piloter, si sa concession est vivante. Levée aussi en plein
pilotage, par la vue du pilote (`SessionWriter.held`), dès qu'un autre
worker l'a prise : il a pu la rendre depuis, `lease_until` est alors passé.

**Arguments :**

- `run_id` : Le run.
- `worker_id` : Le worker qui tient la concession.
- `until` : Fin de sa concession.

**Attributs :**

- `run_id` : Le run.
- `worker_id` : Le worker qui tient la concession.
- `lease_until` : Fin de sa concession.

## Classe `loom_ia.engine.writer._Tenure`

```python
@dataclass(eq=False, slots=True)
class _Tenure
```

Concession qu'un pilote de ce process tient sur un run, et ce qu'il en reste.

**Attributs :**

- `run_id` : Le run.
- `worker_id` : Le worker du pilote.
- `taken` : Dernière concession prise par un autre worker depuis la nôtre : la perte.
- `closed` : Clôture du run écrite ailleurs.

### Méthode `loom_ia.engine.writer._Tenure.lost`

```python
@property
def lost(self) -> bool
```

Vrai si le run a été pris par un autre worker ou clos ailleurs.

### Méthode `loom_ia.engine.writer._Tenure.witness`

```python
def witness(self, landed: Sequence[Event]) -> None
```

Note la prise du run par un autre worker (vivante ou rendue), ou sa clôture.

### Méthode `loom_ia.engine.writer._Tenure.check`

```python
def check(self) -> None
```

Lève `RunMoved` si le run a été clos ailleurs, `ClaimConflict` s'il a été pris.

## Classe `loom_ia.engine.writer._Shared`

```python
@dataclass(eq=False, slots=True)
class _Shared
```

Ce que l'écrivain d'une session partage avec les vues de ses pilotes.

**Attributs :**

- `last_seq` : Dernier `seq` connu de la session.
- `lock` : Verrou qui ordonne les écritures.
- `tenures` : Concessions tenues par les pilotes en cours : chaque reprise les leur montre.

## Classe `loom_ia.engine.writer.RunExists`

```python
class RunExists(ValueError)
```

Le run à ouvrir a déjà des événements dans le journal : rien n'a été écrit.

C'est une `ValueError`, comme l'a toujours été « le run existe déjà ».
`session_id` nomme la session qui le détient quand ce n'est pas celle
demandée : un `run_id` choisi par l'appelant est unique chez son client.

**Arguments :**

- `run_id` : Le run.
- `session_id` : Session qui détient le run, si ce n'est pas celle demandée.

**Attributs :**

- `run_id` : Le run.
- `session_id` : Session qui détient le run, ou `None`.

## Classe `loom_ia.engine.writer.SessionWriter`

```python
class SessionWriter
```

Écritures ordonnées dans le journal d'une session.

Un seul écrivain par session et par instance (`SessionWriters`) : le
run, ses sous-agents et les runs parallèles de la session écrivent par
lui.

**Arguments :**

- `store` : Le journal.
- `tenant_id` : Client de la session.
- `session_id` : La session.
- `last_seq` : Dernier `seq` connu de la session.

**Attributs :**

- `store` : Le journal.
- `tenant_id` : Client de la session.
- `session_id` : La session.

### Méthode `loom_ia.engine.writer.SessionWriter.last_seq`

```python
@property
def last_seq(self) -> int
```

Dernier `seq` de la session connu de l'écrivain et de ses vues.

### Méthode `loom_ia.engine.writer.SessionWriter.last_seq`

```python
@last_seq.setter
def last_seq(self, seq: int) -> None
```

Avance la position commune à l'écrivain et à ses vues.

### Méthode `loom_ia.engine.writer.SessionWriter.open`

```python
@classmethod
async def open(cls, store: EventStore, tenant_id: TenantId, session_id: SessionId) -> Self
```

Écrivain qui part du dernier événement de la session.

**Arguments :**

- `store` : Le journal.
- `tenant_id` : Client de la session.
- `session_id` : La session.

**Renvoie :**

Un écrivain neuf, non partagé ; `SessionWriters.open` rend celui
de l'instance.

### Méthode `loom_ia.engine.writer.SessionWriter.held`

```python
@contextmanager
def held(self, run_id: RunId, worker_id: str) -> Generator[SessionWriter]
```

Vue du pilote qui vient d'écrire la concession de `run_id` (point 27).

À ouvrir aussitôt la concession écrite, sans attente entre les deux :
tout ce qui s'écrit ensuite sur la session passe alors sous ses yeux. La
vue écrit comme l'écrivain, dont elle partage la position et le verrou.
Mais dès qu'un autre worker a pris la concession — vivante ou déjà
rendue —, elle refuse toute écriture en levant `ClaimConflict`, sans
rien écrire, et cela jusqu'au bout ; dès que le run a été clos ailleurs,
de même avec `RunMoved`. Le pilote, ses sous-runs et ses traces
d'idempotence écrivent par elle.

**Arguments :**

- `run_id` : Le run piloté.
- `worker_id` : Le worker du pilote.

**Produit :**

La vue ; la concession est oubliée à la sortie du bloc.

### Méthode `loom_ia.engine.writer.SessionWriter.append`

```python
async def append(self, drafts: Sequence[EventDraft], *, checked: bool=True, opens: RunId | None=None, guard: Callable[[Sequence[Event]], None] | None=None, since: int | None=None) -> list[Event]
```

Écrit les brouillons à la suite, dans l'ordre, et les renvoie numérotés.

Un conflit de séquence est repris : la position est relue chez le
store, puis l'écriture rejouée telle quelle — sauf si un run dont on
écrit a été clos entre-temps, ce que `RunMoved` dit.

`opens` désigne le run que le lot ouvre : s'il a déjà des événements,
avant l'écriture ou entre deux reprises, `RunExists` est levée et
rien n'est écrit.

`guard` reçoit, avant chaque reprise, les événements écrits depuis
la position de l'écrivain ; ce qu'elle lève sort tel quel, et rien
n'est écrit. Avec `since` — le dernier `seq` que l'appelant a lu
avant de décider —, elle reçoit aussi, **sous le verrou et avant la
première tentative**, ce qui s'est écrit depuis : une décision prise
sur une lecture dépassée (deux accords, un accord et un refus, une
décision et l'expiration de la demande) est refusée au lieu de rendre
le journal illisible.

Une vue de pilote (`held`) dont la concession a été prise lève
`ClaimConflict`, et dont le run a été clos ailleurs `RunMoved` :
elle n'écrit rien.

**Arguments :**

- `drafts` : Les brouillons, dans l'ordre.
- `checked` : Faux pour écrire sans contrôle de séquence (compaction).
- `opens` : Le run que le lot ouvre, s'il en ouvre un.
- `guard` : Examen de ce qui s'est écrit depuis la position ; lève pour renoncer.
- `since` : Dernier `seq` lu par l'appelant avant de décider.

**Renvoie :**

Les événements écrits, numérotés ; la position avance jusqu'au
dernier.

**Lève :**

- `RunExists` : Le run à ouvrir a déjà des événements.
- `RunMoved` : Un run du lot a été clos ailleurs.
- `ClaimConflict` : La concession de la vue a été prise.
- `SequenceConflict` : Écriture refusée `MAX_ATTEMPTS` fois d'affilée.

### Méthode `loom_ia.engine.writer.SessionWriter._still_open`

```python
async def _still_open(self, batch: Sequence[EventDraft], actual: int, opens: RunId | None, guard: Callable[[Sequence[Event]], None] | None) -> None
```

Lève `RunExists` si le run à ouvrir l'est déjà, `RunMoved` si un run est clos.

Puis, pour une vue de pilote, `RunMoved` si son run a été clos ailleurs
et `ClaimConflict` s'il a été pris ; sinon `guard`, s'il y en a une,
examine ce qui s'est écrit depuis. Ce qui s'est écrit passe d'abord sous
les yeux des pilotes de la session.

### Méthode `loom_ia.engine.writer.SessionWriter._passed_over`

```python
async def _passed_over(self, first: int) -> None
```

Ce qu'une écriture sans contrôle de séquence a sauté passe sous les yeux des pilotes.

Sans cela, la position avancerait par-dessus la prise d'un run par un
autre worker, ou sa clôture, et le pilote de ce run écrirait sans conflit.

### Méthode `loom_ia.engine.writer.SessionWriter._still_held`

```python
def _still_held(self) -> None
```

Lève `RunMoved` ou `ClaimConflict` si cette vue de pilote a perdu son run.

## Classe `loom_ia.engine.writer.SessionWriters`

```python
class SessionWriters
```

Registre des écrivains d'une instance : un par session.

Les moins récemment utilisés sont oubliés au-delà de `max_writers`.

**Arguments :**

- `max_writers` : Nombre d'écrivains gardés.

### Méthode `loom_ia.engine.writer.SessionWriters.open`

```python
async def open(self, store: EventStore, tenant_id: TenantId, session_id: SessionId) -> SessionWriter
```

Écrivain de cette session, créé au premier appel.

Un écrivain gardé pour un autre journal (`store`) est remplacé.

**Arguments :**

- `store` : Le journal.
- `tenant_id` : Client de la session.
- `session_id` : La session.

**Renvoie :**

L'écrivain partagé de la session.

### Méthode `loom_ia.engine.writer.SessionWriters.forget`

```python
def forget(self, tenant_id: TenantId, session_id: SessionId) -> None
```

Oublie l'écrivain d'une session supprimée (fonction F7).

**Arguments :**

- `tenant_id` : Client de la session.
- `session_id` : La session.

### Méthode `loom_ia.engine.writer.SessionWriters.clear`

```python
def clear(self) -> None
```

Oublie tous les écrivains.
