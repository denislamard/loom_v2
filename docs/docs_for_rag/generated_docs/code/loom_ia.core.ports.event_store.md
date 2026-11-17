# loom_ia.core.ports.event_store

Module `loom_ia.core.ports.event_store` de loom_v2 (fichier `src/loom_ia/core/ports/event_store.py`).

Port du journal d'événements (point 22).

Le journal d'une session est une suite ordonnée d'événements numérotés à
partir de 1 (`seq`). Un run sans session a pour journal ``session_id =
run_id``. Le journal fait foi : tout le reste en est déduit.

Implémentations fournies (`adapters/stores`) : en mémoire (défaut, perdu à
l'arrêt), JSONL (un fichier par session), SQLite et Postgres. Le journal se
déclare sous `storage.events`. Un agent qui peut se mettre en pause exige un
journal durable.

## Classe `loom_ia.core.ports.event_store.SessionRecord`

```python
class SessionRecord(DomainModel)
```

Repère d'une session, pour la lister (fonction F7).

**Attributs :**

- `session_id` : La session.
- `last_seq` : Dernier `seq` écrit : la taille du journal.
- `updated_at` : Instant de la dernière écriture.

## Classe `loom_ia.core.ports.event_store.SequenceConflict`

```python
class SequenceConflict(Exception)
```

Le journal a avancé depuis la dernière lecture (verrou optimiste) ; rien n'a été écrit.

**Arguments :**

- `session_id` : La session.
- `expected` : Dernier `seq` que l'appelant croyait lire.
- `actual` : Dernier `seq` trouvé.

**Attributs :**

- `session_id` : La session.
- `expected` : Dernier `seq` attendu.
- `actual` : Dernier `seq` trouvé.

## Classe `loom_ia.core.ports.event_store.JournalCorrupted`

```python
class JournalCorrupted(Exception)
```

Une ligne complète du journal est illisible (JSON invalide ou événement qui ne se valide pas).

## Classe `loom_ia.core.ports.event_store.StorageNotReady`

```python
class StorageNotReady(RuntimeError)
```

Le stockage n'est pas prêt à servir : c'est à son administrateur de le préparer.

Base Postgres sans schéma ou à migrer par un rôle qui n'en a pas le droit,
migration refusée, rôle applicatif impossible à prendre. Rien n'a été écrit.
Défini dans le noyau, sans dépendre d'asyncpg, pour que la ligne de commande
et les accès le reconnaissent sans l'extra `postgres`.

## Classe `loom_ia.core.ports.event_store.JournalBusy`

```python
class JournalBusy(Exception)
```

Le journal est tenu par un autre écrivain au-delà du délai de verrou.

Rien n'a été écrit : la même demande peut être refaite plus tard.

## Classe `loom_ia.core.ports.event_store.UnusableId`

```python
class UnusableId(ValueError)
```

Un identifiant (client, session) que ce journal ne peut pas nommer.

Reste une `ValueError`, comme avant : celui qui l'interceptait ainsi la
reçoit toujours. Les accès, eux, la distinguent d'une panne.

## Fonction `loom_ia.core.ports.event_store.journal_key`

```python
def journal_key(drafts: Sequence[EventDraft]) -> tuple[TenantId, SessionId]
```

Client et session communs à un lot d'écriture.

**Arguments :**

- `drafts` : Le lot, non vide.

**Renvoie :**

Le couple (client, session) de tous les brouillons.

**Lève :**

- `ValueError` : Les brouillons visent plusieurs journaux ; un lot s'écrit dans un seul.

## Classe `loom_ia.core.ports.event_store.EventStore`

```python
class EventStore(Protocol)
```

Journal des événements, une suite par session.

### Méthode `loom_ia.core.ports.event_store.EventStore.append`

```python
async def append(self, drafts: Sequence[EventDraft], *, expected_seq: int | None) -> list[Event]
```

Écrit un lot à la suite du journal d'une session, d'un seul tenant.

**Arguments :**

- `drafts` : Le lot, dans un seul journal (même client, même session).
- `expected_seq` : Dernier `seq` connu de l'appelant ; `None` désactive ce contrôle (compaction, point 23).

**Renvoie :**

Les événements écrits, numérotés à la suite.

**Lève :**

- `SequenceConflict` : Le journal a avancé depuis `expected_seq` ; rien n'est écrit.
- `JournalBusy` : Un autre écrivain tient le journal au-delà du délai de verrou ; rien n'est écrit.
- `UnusableId` : Le client ou la session ne peut pas nommer un journal dans ce stockage.

### Méthode `loom_ia.core.ports.event_store.EventStore.read`

```python
async def read(self, tenant_id: TenantId, session_id: SessionId, *, after_seq: int=0, run_id: RunId | None=None) -> list[Event]
```

Lit le journal d'une session, dans l'ordre des `seq`.

**Arguments :**

- `tenant_id` : Le client.
- `session_id` : La session.
- `after_seq` : Ne rend que les événements après ce `seq` ; 0 : tout.
- `run_id` : Ne rend que les événements de ce run.

**Renvoie :**

Les événements ; une liste vide pour un journal inconnu.

**Lève :**

- `JournalCorrupted` : Une ligne du journal est illisible.

### Méthode `loom_ia.core.ports.event_store.EventStore.query`

```python
async def query(self, query: EventQuery) -> list[Event]
```

Cherche des événements par enveloppe et facettes, dans les journaux d'un client.

**Arguments :**

- `query` : Les critères ; voir `EventQuery` pour l'ordre et la pagination.

**Renvoie :**

Une page d'événements, `query.limit` au plus.

### Méthode `loom_ia.core.ports.event_store.EventStore.last_seq`

```python
async def last_seq(self, tenant_id: TenantId, session_id: SessionId) -> int
```

Dernier `seq` du journal d'une session ; 0 s'il est vide ou inconnu.

### Méthode `loom_ia.core.ports.event_store.EventStore.sessions`

```python
async def sessions(self, tenant_id: TenantId) -> list[SessionRecord]
```

Sessions d'un client, de la plus récemment écrite à la plus ancienne (fonction F7).

**Arguments :**

- `tenant_id` : Le client.

**Renvoie :**

Un repère par session.

### Méthode `loom_ia.core.ports.event_store.EventStore.delete`

```python
async def delete(self, tenant_id: TenantId, session_id: SessionId) -> int
```

Supprime le journal d'une session (RGPD).

Exception assumée à l'immuabilité du journal : le RGPD demande une
suppression physique (conception §11.5). `loom sessions delete` et
`DELETE /v1/sessions/{id}` y passent.

**Arguments :**

- `tenant_id` : Le client.
- `session_id` : La session.

**Renvoie :**

Le nombre d'événements retirés.

### Méthode `loom_ia.core.ports.event_store.EventStore.aclose`

```python
async def aclose(self) -> None
```

Ferme le journal et libère ses ressources (connexions, fichiers).
