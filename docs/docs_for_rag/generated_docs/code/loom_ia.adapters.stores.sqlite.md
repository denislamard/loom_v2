# loom_ia.adapters.stores.sqlite

Module `loom_ia.adapters.stores.sqlite` de loom_v2 (fichier `src/loom_ia/adapters/stores/sqlite.py`).

Journal d'événements en SQLite : mode service sur une machine (point 22).

Une table `events` par base : colonnes pour l'enveloppe et index composés,
dont `(tenant_id, session_id, seq)` qui est la clé primaire. L'événement
entier est rangé en JSON dans `event` : le relire le reconstruit tel quel,
sans avoir à recomposer l'enveloppe. Les facettes vivent à part, en JSON, et
se filtrent par `json_extract`.

Le contrôle de séquence et l'écriture tiennent dans une transaction
`BEGIN IMMEDIATE` : deux process qui écrivent dans la même base ne peuvent
pas se croiser. Le journal SQLite est en WAL et les commits sont synchrones,
car chaque événement sert de point de reprise. Un `append` qui échoue ou qui
est annulé défait sa transaction, et ne laisse jamais la connexion « en
transaction » : le suivant repart d'une connexion libre.

Les `ts` sont rangés en UTC, en texte : les bornes `since` / `until` d'une
requête sont ramenées en UTC avant d'être comparées (un client en `+02:00`
compare comme un client en `Z`). La base supprime en les écrasant
(`secure_delete`) et `delete` vide ensuite le WAL (RGPD).

Une requête est d'abord réduite en SQL sur les colonnes indexées et les
facettes, puis repassée par `EventQuery.select` : c'est lui qui fait foi.

## Constantes de `loom_ia.adapters.stores.sqlite`

- `SCHEMA = "\nCREATE TABLE IF NOT EXISTS events (\n tenant_id TEXT NOT NULL,\n session_id TEXT NOT NULL,\n seq INTEGER NOT NULL,\n…` : Table `events` et ses index, créés à la première connexion s'ils manquent.

## Fonction `loom_ia.adapters.stores.sqlite._row`

```python
def _row(event: Event, codec: JournalCodec) -> tuple[Any, ...]
```

Les colonnes d'une ligne : l'enveloppe (`ts` en UTC, en texte), les facettes en JSON, et l'événement rangé par le codec.

## Fonction `loom_ia.adapters.stores.sqlite._conditions`

```python
def _conditions(query: EventQuery) -> tuple[str, list[Any]]
```

Clauses SQL qui réduisent la recherche ; `select` tranche ensuite.

## Classe `loom_ia.adapters.stores.sqlite.SqliteEventStore`

```python
class SqliteEventStore
```

Journal durable dans un fichier SQLite.

### Méthode `loom_ia.adapters.stores.sqlite.SqliteEventStore._connect`

```python
async def _connect(self) -> aiosqlite.Connection
```

La connexion, ouverte au premier usage : WAL, commits synchrones, attente de verrou de 5 s, suppression par écrasement, schéma créé s'il manque.

### Méthode `loom_ia.adapters.stores.sqlite.SqliteEventStore.append`

```python
async def append(self, drafts: Sequence[EventDraft], *, expected_seq: int | None) -> list[Event]
```

Écrit un lot à la suite du journal d'une session (`EventStore.append`).

Dans une transaction `BEGIN IMMEDIATE` : deux process ne peuvent pas se
croiser. Un échec ou une annulation défait la transaction, et ne laisse
jamais la connexion en transaction.

**Arguments :**

- `drafts` : Le lot, dans un seul journal (même client, même session).
- `expected_seq` : Dernier `seq` connu de l'appelant ; `None` désactive ce contrôle.

**Renvoie :**

Les événements écrits, numérotés à la suite.

**Lève :**

- `SequenceConflict` : Le journal a avancé depuis `expected_seq` ; rien n'est écrit.

### Méthode `loom_ia.adapters.stores.sqlite.SqliteEventStore._abort`

```python
async def _abort(self, connection: aiosqlite.Connection) -> None
```

Défait la transaction en cours ; si le `ROLLBACK` échoue, jette la connexion.

L'erreur qui remonte à l'appelant reste celle qui a fait échouer l'`append`
(ou l'annulation), jamais celle du nettoyage. Une connexion dont on ignore si
elle est encore en transaction n'est pas gardée : fermée, elle défait ce qui
reste, et l'appel suivant en ouvre une neuve.

### Méthode `loom_ia.adapters.stores.sqlite.SqliteEventStore.read`

```python
async def read(self, tenant_id: TenantId, session_id: SessionId, *, after_seq: int=0, run_id: RunId | None=None) -> list[Event]
```

Lit le journal d'une session, dans l'ordre des `seq` (`EventStore.read`).

**Arguments :**

- `tenant_id` : Le client.
- `session_id` : La session.
- `after_seq` : Ne rend que les événements après ce `seq` ; 0 : tout.
- `run_id` : Ne rend que les événements de ce run.

### Méthode `loom_ia.adapters.stores.sqlite.SqliteEventStore.query`

```python
async def query(self, query: EventQuery) -> list[Event]
```

Cherche des événements dans les journaux d'un client (`EventStore.query`).

Clauses SQL sur les colonnes indexées et les facettes (`json_extract`),
dans l'ordre d'écriture (`rowid`), puis `EventQuery.refine`.

**Arguments :**

- `query` : Les critères, l'ordre et la pagination (`EventQuery`).

### Méthode `loom_ia.adapters.stores.sqlite.SqliteEventStore.last_seq`

```python
async def last_seq(self, tenant_id: TenantId, session_id: SessionId) -> int
```

Dernier `seq` du journal d'une session ; 0 s'il est vide ou inconnu.

### Méthode `loom_ia.adapters.stores.sqlite.SqliteEventStore.sessions`

```python
async def sessions(self, tenant_id: TenantId) -> list[SessionRecord]
```

Sessions d'un client, de la plus récemment écrite à la plus ancienne (fonction F7).

**Arguments :**

- `tenant_id` : Le client.

### Méthode `loom_ia.adapters.stores.sqlite.SqliteEventStore.delete`

```python
async def delete(self, tenant_id: TenantId, session_id: SessionId) -> int
```

Supprime le journal d'une session (RGPD) ; rend le nombre d'événements supprimés.

Les lignes sont écrasées (`secure_delete`), puis le WAL est vidé.

### Méthode `loom_ia.adapters.stores.sqlite.SqliteEventStore.aclose`

```python
async def aclose(self) -> None
```

Ferme la connexion, si elle est ouverte.
