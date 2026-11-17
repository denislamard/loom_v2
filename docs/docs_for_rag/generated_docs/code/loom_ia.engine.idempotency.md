# loom_ia.engine.idempotency

Module `loom_ia.engine.idempotency` de loom_v2 (fichier `src/loom_ia/engine/idempotency.py`).

Magasin d'idempotence adossé au journal du run (points 18 et 49).

Il n'a pas d'autre stockage que le journal : la trace est durable dès que le
journal l'est, et il n'y a rien à installer. En contrepartie il ne porte que
des **clés techniques** — celles d'un appel précis, `hash(run_id, call_id)`.
Une clé métier doit être vue depuis un autre run, et le journal d'un run ne
l'est pas (magasin partagé, jalon J4.4b).

La réservation, elle, est déjà au journal : c'est le `tool.called` de
l'appel, écrit avant son exécution. `reserve` n'a donc rien à écrire, et
c'est la règle de reprise (point 18) qui décide du sort d'un appel commencé sans
résultat. Ce que ce magasin ajoute, c'est l'autre moitié : un appel rejoué
dont l'effet **a** eu lieu rend le résultat mémorisé au lieu de le refaire.

La concession (point 27) garantit un seul pilote par run : deux exécutions
simultanées du même appel ne peuvent pas se produire, et `reserve` n'a donc
pas à les départager.

Une clé **métier** n'a donc rien à faire ici, et le chargement le refuse : ce
magasin ne la porterait que dans un run, là où elle doit valoir pour tous.

`complete` écrit tout de suite, par l'écrivain de la session, sans passer
par la file du lot : c'est justement entre l'effet et le `tool.completed`
que se situe l'accident qu'on veut couvrir. Un enregistrement mis en file
serait écrit en même temps que le résultat, et ne couvrirait rien.

Le jeton de détenteur du port (`holder`) n'a ici qu'un rôle de contrat : une
instance sert **un** appel, et la concession du run n'a qu'un pilote. Elle
retient le jeton de son `reserve`, et un `complete` qui en présente un
autre n'écrit rien. Rien n'est ajouté au journal pour cela : le jeton ne
s'écrit pas, il n'a de sens que le temps de l'appel.

## Constantes de `loom_ia.engine.idempotency`

- `_FOREVER = timedelta(days=365 * 100)` : Validité donnée à un enregistrement : il ne périme pas, il vaut tant que le run existe.

## Classe `loom_ia.engine.idempotency.JournalIdempotency`

```python
class JournalIdempotency
```

`IdempotencyStore` qui lit et écrit dans le journal du run.

L'exécuteur en crée une instance par appel d'un outil décoré
`@idempotent` dont la clé est technique. L'enregistrement est un
événement `idempotency.recorded` (clé, appel, outil, résultat).

### Méthode `loom_ia.engine.idempotency.JournalIdempotency.__init__`

```python
def __init__(self, writer: SessionWriter, scope: RunScope, *, call_id: str, tool_name: str, span_id: SpanId | None=None, parent_span_id: SpanId | None=None) -> None
```

Prépare le magasin d'un appel.

**Arguments :**

- `writer` : Écrivain de la session : lecture du journal et écriture immédiate de l'enregistrement.
- `scope` : Run de l'appel ; donne le `run_id` et les champs communs des événements.
- `call_id` : Identifiant de l'appel d'outil.
- `tool_name` : Nom de l'outil.
- `span_id` : Span de l'appel : l'enregistrement se range sous lui, comme son `tool.called` ; sans lui, il tombe sur le span racine du run.
- `parent_span_id` : Span de l'étape qui contient l'appel.

### Méthode `loom_ia.engine.idempotency.JournalIdempotency.get`

```python
async def get(self, key: str) -> IdempotencyRecord | None
```

Résultat mémorisé pour cette clé, s'il a été enregistré dans ce run.

Relit les événements du run à chaque appel : c'est le prix d'un
magasin sans stockage propre, et il se paie une fois par appel d'outil
décoré. Une clé seulement réservée n'est pas ici — elle est au
`tool.called`, que la reprise (point 18) consulte de son côté.

**Arguments :**

- `key` : Clé technique de l'appel.

**Renvoie :**

Le dernier enregistrement du run sous cette clé, au statut
`completed` et sans péremption ; `None` s'il n'y en a pas.

### Méthode `loom_ia.engine.idempotency.JournalIdempotency.reserve`

```python
async def reserve(self, key: str, ttl: float, scope: KeyScope, *, holder: str | None=None) -> bool
```

Toujours vrai : le `tool.called` de l'appel tient lieu de réservation.

**Arguments :**

- `key` : Clé technique de l'appel (non utilisée).
- `ttl` : Durée de la réservation (non utilisée).
- `scope` : Portée de la clé (non utilisée : le chargement refuse une clé métier).
- `holder` : Jeton du détenteur, retenu pour `complete`.

**Renvoie :**

`True`.

### Méthode `loom_ia.engine.idempotency.JournalIdempotency.complete`

```python
async def complete(self, key: str, result: object, ttl: float | None=None, *, holder: str | None=None) -> None
```

Écrit l'effet au journal du run, sous sa clé, tout de suite.

**Arguments :**

- `key` : Clé technique de l'appel.
- `result` : Résultat de l'effet, rendu enregistrable (`recordable`).
- `ttl` : Durée de vie (non utilisée : l'enregistrement vaut tant que le run existe).
- `holder` : Jeton du détenteur : s'il diffère de celui de `reserve`, rien n'est écrit.

### Méthode `loom_ia.engine.idempotency.JournalIdempotency.release`

```python
async def release(self, key: str, *, holder: str | None=None) -> None
```

Sans objet : rien n'a été écrit à la réservation.

### Méthode `loom_ia.engine.idempotency.JournalIdempotency.forget`

```python
async def forget(self, tenant_id: TenantId, session_id: SessionId | None=None) -> int
```

Sans objet : les enregistrements partent avec le journal de leur session.

Supprimer une session (RGPD) efface ses événements, et ceux-ci en
font partie. Il n'y a rien de plus à oublier ici.
