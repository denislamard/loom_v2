# loom_ia.adapters.stores.postgres

Module `loom_ia.adapters.stores.postgres` de loom_v2 (fichier `src/loom_ia/adapters/stores/postgres.py`).

Journal d'événements en Postgres : mode service, plusieurs process (fonction F5, point 5).

Même table, mêmes colonnes et mêmes index qu'en SQLite — l'événement entier
rangé en texte dans `event`, les facettes à part en `jsonb` — à trois
différences près, qui sont tout l'intérêt de Postgres ici.

**La politique de lignes fait barrière.** Chaque transaction pose le client
courant ; la politique du DDL ne laisse voir et n'accepte d'écrire que ses
lignes. Le `WHERE tenant_id = $1` de chaque requête reste écrit, mais il
n'est plus ce qui protège : s'il était oublié, la requête ne rendrait pas le
journal d'un autre, elle rendrait le même résultat. C'est ce que vérifie
`test_postgres.py` en l'oubliant exprès.

**Le journal ne se modifie pas.** Le rôle applicatif n'a pas `UPDATE` sur
la table : un événement écrit ne peut plus changer, même par une faute de
code. `DELETE` reste accordé, c'est l'effacement RGPD d'une session.

**L'ordre d'écriture est arbitré par la base.** Le contrôle de séquence et
l'insertion tiennent dans une transaction ouverte par un verrou consultatif
sur le journal visé : deux process qui écrivent dans la même session
attendent leur tour plutôt que de s'entrelacer. La clé primaire
`(tenant_id, session_id, seq)` reste le dernier mot — une violation
d'unicité devient un `SequenceConflict`, comme un `expected_seq` périmé.

## Constantes de `loom_ia.adapters.stores.postgres`

- `_LOCK = 'SELECT pg_advisory_xact_lock(hashtext($1)::bigint)'` : Un verrou par journal, tenu jusqu'à la fin de la transaction. `hashtext` peut faire se rencontrer deux journaux : ils s'attendent, c'est tout.
- `_INGEST_LOCK = 'SELECT pg_advisory_xact_lock(hashtext($1)::bigint)'` : Un second verrou, par client, pris juste avant l'insertion : la colonne `ingested` est tirée d'une séquence, et sans lui une transaction qui tire 10 puis valide après celle qui a tiré 11 ferait manquer la ligne 10 à un suiveur déjà passé. Tenu jusqu'à la validation, il rend l'ordre des valeurs égal à celui de la visibilité. Pris après le verrou du journal, toujours dans cet ordre : pas d'interblocage.

## Fonction `loom_ia.adapters.stores.postgres._row`

```python
def _row(event: Event, codec: JournalCodec) -> tuple[Any, ...]
```

Les colonnes d'une ligne : l'enveloppe, les facettes en JSON, et l'événement rangé par le codec.

## Classe `loom_ia.adapters.stores.postgres._Conditions`

```python
class _Conditions
```

Clauses SQL numérotées ; `EventQuery.refine` les rejoue ensuite.

### Méthode `loom_ia.adapters.stores.postgres._Conditions.add`

```python
def add(self, clause: str, value: Any) -> None
```

Ajoute une clause ; `{n}` y devient le numéro de sa valeur (`$1`, `$2`…).

### Méthode `loom_ia.adapters.stores.postgres._Conditions.where`

```python
@property
def where(self) -> str
```

Les clauses, jointes par `AND`.

## Fonction `loom_ia.adapters.stores.postgres._conditions`

```python
def _conditions(query: EventQuery) -> _Conditions
```

Clauses SQL qui réduisent la recherche ; `EventQuery.refine` tranche ensuite.

Client, colonnes indexées, facettes (contenance `@>` : présente et de
cette valeur), bornes de temps, et reprise après un événement dans l'ordre
d'écriture (`ingested`).

## Classe `loom_ia.adapters.stores.postgres.PostgresEventStore`

```python
class PostgresEventStore
```

Journal durable dans une base Postgres, sous politique de lignes.

### Méthode `loom_ia.adapters.stores.postgres.PostgresEventStore.__init__`

```python
def __init__(self, dsn: str, *, role: str | None=DEFAULT_ROLE, codec: JournalCodec=PLAIN, limits: PoolLimits=DEFAULT_LIMITS) -> None
```

Prépare le pool de connexions de la table des événements, avec son DDL et sa mise à niveau.

**Arguments :**

- `dsn` : Adresse de la base.
- `role` : Rôle applicatif pris par chaque connexion, que la politique de lignes vise ; `None` : celui de la connexion.
- `codec` : Forme de la colonne `event` : en clair ou scellée.
- `limits` : Taille et délais du pool.

### Méthode `loom_ia.adapters.stores.postgres.PostgresEventStore.append`

```python
async def append(self, drafts: Sequence[EventDraft], *, expected_seq: int | None) -> list[Event]
```

Écrit un lot à la suite du journal d'une session (`EventStore.append`).

Dans une transaction : verrou consultatif sur le journal, contrôle de
séquence, verrou d'ingestion du client, insertion. Une violation de la clé
primaire devient un `SequenceConflict` : deux écrivains ont franchi le
verrou, l'appelant relit et rejoue.

**Arguments :**

- `drafts` : Le lot, dans un seul journal (même client, même session).
- `expected_seq` : Dernier `seq` connu de l'appelant ; `None` désactive ce contrôle.

**Renvoie :**

Les événements écrits, numérotés à la suite.

**Lève :**

- `SequenceConflict` : Le journal a avancé depuis `expected_seq` ; rien n'est écrit.

### Méthode `loom_ia.adapters.stores.postgres.PostgresEventStore.read`

```python
async def read(self, tenant_id: TenantId, session_id: SessionId, *, after_seq: int=0, run_id: RunId | None=None) -> list[Event]
```

Lit le journal d'une session, dans l'ordre des `seq` (`EventStore.read`).

**Arguments :**

- `tenant_id` : Le client.
- `session_id` : La session.
- `after_seq` : Ne rend que les événements après ce `seq` ; 0 : tout.
- `run_id` : Ne rend que les événements de ce run.

### Méthode `loom_ia.adapters.stores.postgres.PostgresEventStore.query`

```python
async def query(self, query: EventQuery) -> list[Event]
```

Cherche des événements dans les journaux d'un client (`EventStore.query`).

Clauses SQL sur les colonnes indexées et les facettes, dans l'ordre
d'écriture (`ingested`), puis `EventQuery.refine`.

**Arguments :**

- `query` : Les critères, l'ordre et la pagination (`EventQuery`).

### Méthode `loom_ia.adapters.stores.postgres.PostgresEventStore.last_seq`

```python
async def last_seq(self, tenant_id: TenantId, session_id: SessionId) -> int
```

Dernier `seq` du journal d'une session ; 0 s'il est vide ou inconnu.

### Méthode `loom_ia.adapters.stores.postgres.PostgresEventStore.sessions`

```python
async def sessions(self, tenant_id: TenantId) -> list[SessionRecord]
```

Sessions d'un client, de la plus récemment écrite à la plus ancienne (fonction F7).

**Arguments :**

- `tenant_id` : Le client.

### Méthode `loom_ia.adapters.stores.postgres.PostgresEventStore.delete`

```python
async def delete(self, tenant_id: TenantId, session_id: SessionId) -> int
```

Supprime le journal d'une session (RGPD) ; rend le nombre d'événements supprimés.

Seul `DELETE` est accordé au rôle applicatif, jamais `UPDATE`.

### Méthode `loom_ia.adapters.stores.postgres.PostgresEventStore.aclose`

```python
async def aclose(self) -> None
```

Ferme le pool de connexions.
