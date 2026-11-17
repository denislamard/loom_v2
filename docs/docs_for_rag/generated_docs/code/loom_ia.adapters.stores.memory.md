# loom_ia.adapters.stores.memory

Module `loom_ia.adapters.stores.memory` de loom_v2 (fichier `src/loom_ia/adapters/stores/memory.py`).

Journal d'événements en mémoire : tests et runs sans pause (point 22).

## Classe `loom_ia.adapters.stores.memory.InMemoryEventStore`

```python
class InMemoryEventStore
```

Journal non durable, limité au process.

`append` ne contient aucun `await` : le contrôle de séquence et
l'écriture sont donc atomiques pour la boucle asyncio.

### Méthode `loom_ia.adapters.stores.memory.InMemoryEventStore.append`

```python
async def append(self, drafts: Sequence[EventDraft], *, expected_seq: int | None) -> list[Event]
```

Écrit un lot à la suite du journal d'une session (`EventStore.append`).

Sans aucun `await` : contrôle de séquence et écriture sont atomiques pour
la boucle asyncio.

**Arguments :**

- `drafts` : Le lot, dans un seul journal (même client, même session).
- `expected_seq` : Dernier `seq` connu de l'appelant ; `None` désactive ce contrôle.

**Renvoie :**

Les événements écrits, numérotés à la suite.

**Lève :**

- `SequenceConflict` : Le journal a avancé depuis `expected_seq` ; rien n'est écrit.

### Méthode `loom_ia.adapters.stores.memory.InMemoryEventStore.read`

```python
async def read(self, tenant_id: TenantId, session_id: SessionId, *, after_seq: int=0, run_id: RunId | None=None) -> list[Event]
```

Lit le journal d'une session, dans l'ordre des `seq` (`EventStore.read`).

**Arguments :**

- `tenant_id` : Le client.
- `session_id` : La session.
- `after_seq` : Ne rend que les événements après ce `seq` ; 0 : tout.
- `run_id` : Ne rend que les événements de ce run.

### Méthode `loom_ia.adapters.stores.memory.InMemoryEventStore.query`

```python
async def query(self, query: EventQuery) -> list[Event]
```

Cherche des événements dans les journaux d'un client (`EventStore.query`).

L'ordre d'écriture est le rang de chaque événement, toutes sessions
confondues ; `after` reprend après l'événement indiqué (un événement
inconnu retombe sur la comparaison des identifiants).

**Arguments :**

- `query` : Les critères, l'ordre et la pagination (`EventQuery`).

### Méthode `loom_ia.adapters.stores.memory.InMemoryEventStore.last_seq`

```python
async def last_seq(self, tenant_id: TenantId, session_id: SessionId) -> int
```

Dernier `seq` du journal d'une session ; 0 s'il est vide ou inconnu.

### Méthode `loom_ia.adapters.stores.memory.InMemoryEventStore.sessions`

```python
async def sessions(self, tenant_id: TenantId) -> list[SessionRecord]
```

Sessions d'un client, de la plus récemment écrite à la plus ancienne (fonction F7).

**Arguments :**

- `tenant_id` : Le client.

### Méthode `loom_ia.adapters.stores.memory.InMemoryEventStore.delete`

```python
async def delete(self, tenant_id: TenantId, session_id: SessionId) -> int
```

Supprime le journal d'une session (RGPD) ; rend le nombre d'événements supprimés.

### Méthode `loom_ia.adapters.stores.memory.InMemoryEventStore.aclose`

```python
async def aclose(self) -> None
```

Rien à fermer.
