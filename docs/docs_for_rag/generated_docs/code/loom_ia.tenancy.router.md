# loom_ia.tenancy.router

Module `loom_ia.tenancy.router` de loom_v2 (fichier `src/loom_ia/tenancy/router.py`).

Isolation physique par client : `TenantRouter` (point 34, conception §7.3).

L'isolation logique tient au `tenant_id` que porte chaque événement et au
préfixe de chaque URI d'artefact : elle suffit tant que tout le monde
partage la même base. Un client peut vouloir plus — son dossier, son
fichier, et plus tard son schéma Postgres, sa collection Firestore ou son
bucket (jalon J5.3). Il lui suffit de déclarer son propre `storage`, `events` compris :
la config refuse un bloc qui l'omet, son journal serait en mémoire.

Le routeur ne s'insère nulle part dans le moteur : il **est** un journal et
un stockage d'artefacts, qui choisissent le leur au vu du client. Toutes les
opérations des deux ports nomment déjà le client — directement pour le
journal, par l'URI pour les artefacts —, donc le reste de loom-ia ne voit
qu'un stockage de plus et ne sait pas qu'il y en a plusieurs.

## Constantes de `loom_ia.tenancy.router`

- `EventStoreFactory = Callable[[StorageConfig], EventStore]` : Fabrique d'un journal à partir d'un bloc `storage`.
- `ArtifactStoreFactory = Callable[[StorageConfig], ArtifactStore]` : Fabrique d'un stockage d'artefacts à partir d'un bloc `storage`.

## Classe `loom_ia.tenancy.router.TenantRouter`

```python
class TenantRouter
```

Choisit le journal et le stockage d'artefacts d'un client.

Un client sans bloc `storage` partage ceux de la racine : c'est le cas
courant, et l'isolation y reste logique. Les stockages créés pour un
client appartiennent au routeur, qui les ferme ; ceux de la racine
restent à leur propriétaire.

**Arguments :**

- `tenants` : Les clients ; ceux qui déclarent `storage` ont le leur.
- `events` : Fabrique des journaux des clients.
- `artifacts` : Fabrique des stockages d'artefacts des clients.
- `shared_events` : Journal de la racine.
- `shared_artifacts` : Stockage d'artefacts de la racine.

### Méthode `loom_ia.tenancy.router.TenantRouter.__init__`

```python
def __init__(self, tenants: Tenants, *, events: EventStoreFactory, artifacts: ArtifactStoreFactory, shared_events: EventStore, shared_artifacts: ArtifactStore) -> None
```

Crée le journal et le stockage d'artefacts de chaque client qui déclare `storage`.

### Méthode `loom_ia.tenancy.router.TenantRouter.routed`

```python
@property
def routed(self) -> bool
```

Vrai si au moins un client a son propre stockage.

### Méthode `loom_ia.tenancy.router.TenantRouter.tenants`

```python
@property
def tenants(self) -> tuple[TenantId, ...]
```

Clients qui ont leur propre stockage.

### Méthode `loom_ia.tenancy.router.TenantRouter.events`

```python
def events(self, tenant_id: TenantId) -> EventStore
```

Journal du client : le sien s'il en déclare un, sinon celui de la racine.

### Méthode `loom_ia.tenancy.router.TenantRouter.artifacts`

```python
def artifacts(self, tenant_id: TenantId) -> ArtifactStore
```

Stockage d'artefacts du client : le sien s'il en déclare un, sinon celui de la racine.

### Méthode `loom_ia.tenancy.router.TenantRouter.aclose`

```python
async def aclose(self) -> None
```

Ferme les stockages créés pour des clients ; pas ceux de la racine.

Un stockage qui échoue à se fermer n'épargne pas les autres : l'échec
est levé une fois tout fermé.

## Classe `loom_ia.tenancy.router.RoutedEventStore`

```python
class RoutedEventStore
```

Journal qui délègue au journal du client de chaque opération.

Chaque méthode est celle du port `EventStore`, appliquée au journal du
client qu'elle nomme.

**Arguments :**

- `router` : Le routeur.

### Méthode `loom_ia.tenancy.router.RoutedEventStore.append`

```python
async def append(self, drafts: Sequence[EventDraft], *, expected_seq: int | None) -> list[Event]
```

Écrit dans le journal du client des brouillons (un seul client et une seule session par lot).

### Méthode `loom_ia.tenancy.router.RoutedEventStore.read`

```python
async def read(self, tenant_id: TenantId, session_id: SessionId, *, after_seq: int=0, run_id: RunId | None=None) -> list[Event]
```

Événements d'une session, lus dans le journal du client.

### Méthode `loom_ia.tenancy.router.RoutedEventStore.query`

```python
async def query(self, query: EventQuery) -> list[Event]
```

Recherche dans le journal du client de la requête.

### Méthode `loom_ia.tenancy.router.RoutedEventStore.last_seq`

```python
async def last_seq(self, tenant_id: TenantId, session_id: SessionId) -> int
```

Dernier `seq` d'une session, dans le journal du client.

### Méthode `loom_ia.tenancy.router.RoutedEventStore.sessions`

```python
async def sessions(self, tenant_id: TenantId) -> list[SessionRecord]
```

Sessions du client.

### Méthode `loom_ia.tenancy.router.RoutedEventStore.delete`

```python
async def delete(self, tenant_id: TenantId, session_id: SessionId) -> int
```

Supprime une session du journal du client ; rend le nombre d'événements supprimés.

### Méthode `loom_ia.tenancy.router.RoutedEventStore.aclose`

```python
async def aclose(self) -> None
```

Ne ferme rien : les stockages sont fermés par le routeur, une fois, à la fermeture de l'instance.

## Classe `loom_ia.tenancy.router.RoutedArtifactStore`

```python
class RoutedArtifactStore
```

Stockage d'artefacts qui suit le client nommé par l'URI.

**Arguments :**

- `router` : Le routeur.

### Méthode `loom_ia.tenancy.router.RoutedArtifactStore.put`

```python
async def put(self, uri: str, data: bytes) -> None
```

Range un fichier dans le stockage du client de l'URI (`artifact://<client>/…`).

### Méthode `loom_ia.tenancy.router.RoutedArtifactStore.get`

```python
async def get(self, uri: str) -> bytes
```

Lit un fichier dans le stockage du client de l'URI.

### Méthode `loom_ia.tenancy.router.RoutedArtifactStore.delete`

```python
async def delete(self, tenant_id: TenantId, session_id: SessionId) -> int
```

Supprime les fichiers d'une session du client ; rend leur nombre.

### Méthode `loom_ia.tenancy.router.RoutedArtifactStore.aclose`

```python
async def aclose(self) -> None
```

Ne ferme rien : les stockages sont fermés une fois, par le routeur.
