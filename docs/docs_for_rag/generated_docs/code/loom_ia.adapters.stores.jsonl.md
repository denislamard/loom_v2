# loom_ia.adapters.stores.jsonl

Module `loom_ia.adapters.stores.jsonl` de loom_v2 (fichier `src/loom_ia/adapters/stores/jsonl.py`).

Journal d'événements en fichiers JSONL : mode librairie et dev (point 22).

Un fichier par session : `<racine>/<client>/<session>.jsonl`, un événement
par ligne. Plusieurs process peuvent écrire dans le même journal : chaque
écriture prend un verrou exclusif `flock` (POSIX : Linux, macOS) et se
termine par un `fsync`, car chaque événement sert de point de reprise.

Ce qu'une ligne contient dépend du codec (`codec.py`) : le JSON de
l'événement en clair, ou la même enveloppe avec sa charge scellée. Lister les
sessions et connaître la longueur d'un journal ne lisent que la **marque** de
la dernière ligne, jamais sa charge : un journal dont la clé est effacée se
laisse encore voir et supprimer.

Plantage en cours d'écriture : la dernière ligne peut rester incomplète.
Elle est ignorée à la lecture ; à l'écriture suivante, elle est déplacée
dans `<session>.jsonl.corrupt` puis retirée du journal. Une ligne
complète illisible lève `JournalCorrupted` — pour `read`, `query`,
`last_seq` (si c'est la dernière) et `append` ; `sessions` la saute,
pour qu'un journal abîmé reste listable et supprimable. Une ligne est
complète quand elle se termine par un saut de ligne, même si ce qui
précède est un JSON entier : la règle est la même pour `read`, `query`,
`last_seq`, `sessions` et `append`. Écarter ainsi un JSON entier sans
son saut de ligne ne perd rien de promis : `append` ne rend la main
qu'une fois tout le lot écrit et `fsync`, donc une ligne sans saut de
ligne appartient à un lot que l'appelant n'a pas vu confirmé.

Un lot s'écrit d'un seul bloc, sous verrou. Si l'écriture échoue (disque plein,
erreur d'entrée-sortie), le fichier retrouve sa taille d'avant le lot : tout ou
rien. Si c'est la machine qui tombe en plein milieu, le disque peut garder un
préfixe du lot : ses premières lignes, complètes et valides, puis une ligne
incomplète traitée comme ci-dessus. Ce choix est voulu : le mode JSONL est
le mode local, et un lot tout ou rien après une panne de machine demande
SQLite ou Postgres (ils le sont). Un fichier d'intention rejoué au démarrage
l'obtiendrait ici, au prix de trois `fsync` par ajout au lieu d'un et d'un
nouveau type de fichier à lister, supprimer et exporter : non retenu.

## Constantes de `loom_ia.adapters.stores.jsonl`

- `SUFFIX = '.jsonl'` : Extension du fichier d'une session.
- `CORRUPT_SUFFIX = '.corrupt'` : Ajouté au nom du fichier qui reçoit les lignes finales incomplètes mises de côté.
- `TAIL_BYTES = 64 * 1024` : Fin de fichier lue pour connaître le dernier événement sans relire tout le journal ; au-delà (un événement plus gros que ça), on relit le fichier.

## Fonction `loom_ia.adapters.stores.jsonl._parse`

```python
def _parse(path: Path, content: bytes, codec: JournalCodec) -> list[Event]
```

Événements des lignes complètes ; la ligne finale incomplète est ignorée.

## Fonction `loom_ia.adapters.stores.jsonl._mark`

```python
def _mark(path: Path, line: bytes, codec: JournalCodec) -> EventMark
```

Repères d'une ligne complète, sans l'ouvrir ; `JournalCorrupted` sinon.

## Fonction `loom_ia.adapters.stores.jsonl._tail`

```python
def _tail(path: Path, size: int) -> bytes
```

Fin du fichier, sous verrou partagé.

## Fonction `loom_ia.adapters.stores.jsonl._last_mark`

```python
def _last_mark(content: bytes, codec: JournalCodec) -> EventMark | None
```

Repères de la dernière ligne lisible, sans l'ouvrir.

La ligne finale sans saut de ligne est incomplète, même si c'est un JSON
entier (comme pour `_parse`) ; la première peut être tronquée par une
lecture partielle : celles-là sont passées. Lire une **marque** et non un
événement, c'est ce qui laisse un journal scellé se lister et se
supprimer sans sa clé.

## Classe `loom_ia.adapters.stores.jsonl._FullReadNeeded`

```python
class _FullReadNeeded(Exception)
```

La fin du fichier ne suffit pas à lire la dernière ligne : relire tout le fichier.

## Fonction `loom_ia.adapters.stores.jsonl._strict_mark`

```python
def _strict_mark(path: Path, content: bytes, codec: JournalCodec, *, partial_start: bool) -> EventMark | None
```

Repères de la dernière ligne complète ; lève si elle est illisible.

`partial_start` : `content` est la fin d'un fichier, sa première ligne peut être
coupée ; si c'est la seule et qu'elle ne se lit pas, `_FullReadNeeded`.

## Fonction `loom_ia.adapters.stores.jsonl._write_batch`

```python
def _write_batch(fh: BinaryIO, data: bytes) -> None
```

Écrit le lot d'un bloc puis `fsync` ; en cas d'échec, remet le fichier comme il était.

Les octets passent par `os.write`, sans le tampon de `fh` : un tampon
resté plein après l'échec serait écrit à la fermeture, après le retour en
arrière. Une panne de la machine n'est pas couverte (voir l'en-tête).

## Classe `loom_ia.adapters.stores.jsonl.JsonlEventStore`

```python
class JsonlEventStore
```

Journal durable en fichiers JSONL, un par session : `<racine>/<client>/<session>.jsonl`.

**Arguments :**

- `root` : Dossier racine des journaux.
- `codec` : Forme des lignes : en clair (`PLAIN`) ou scellées (`SealingCodec`).

### Méthode `loom_ia.adapters.stores.jsonl.JsonlEventStore.path`

```python
def path(self, tenant_id: TenantId, session_id: SessionId) -> Path
```

Chemin du fichier d'une session ; `UnusableId` si le client ou la session ne peut pas nommer un fichier.

### Méthode `loom_ia.adapters.stores.jsonl.JsonlEventStore.append`

```python
async def append(self, drafts: Sequence[EventDraft], *, expected_seq: int | None) -> list[Event]
```

Écrit un lot à la suite du journal d'une session (`EventStore.append`).

Sous verrou exclusif `flock`, d'un seul bloc suivi d'un `fsync` : un
échec d'écriture rend au fichier sa taille d'avant le lot. Une ligne finale
incomplète, laissée par un plantage, est d'abord déplacée dans
`<session>.jsonl.corrupt`.

**Arguments :**

- `drafts` : Le lot, dans un seul journal (même client, même session).
- `expected_seq` : Dernier `seq` connu de l'appelant ; `None` désactive ce contrôle.

**Renvoie :**

Les événements écrits, numérotés à la suite.

**Lève :**

- `SequenceConflict` : Le journal a avancé depuis `expected_seq` ; rien n'est écrit.
- `UnusableId` : Le client ou la session ne peut pas nommer un fichier.
- `JournalCorrupted` : La dernière ligne complète est illisible.

### Méthode `loom_ia.adapters.stores.jsonl.JsonlEventStore._append_sync`

```python
def _append_sync(self, path: Path, session_id: SessionId, drafts: list[EventDraft], expected_seq: int | None) -> list[Event]
```

L'écriture d'un lot, dans un fil : verrou exclusif, contrôle de séquence, lot écrit d'un bloc, dernier `seq` retenu.

### Méthode `loom_ia.adapters.stores.jsonl.JsonlEventStore._last_seq_locked`

```python
def _last_seq_locked(self, path: Path, fh: BinaryIO) -> int
```

Dernier seq, sous verrou exclusif ; répare une ligne finale incomplète.

### Méthode `loom_ia.adapters.stores.jsonl.JsonlEventStore.read`

```python
async def read(self, tenant_id: TenantId, session_id: SessionId, *, after_seq: int=0, run_id: RunId | None=None) -> list[Event]
```

Lit le journal d'une session, dans l'ordre des `seq` (`EventStore.read`).

Sous verrou partagé. La ligne finale incomplète est ignorée ; une ligne
complète illisible lève `JournalCorrupted`.

**Arguments :**

- `tenant_id` : Le client.
- `session_id` : La session.
- `after_seq` : Ne rend que les événements après ce `seq` ; 0 : tout.
- `run_id` : Ne rend que les événements de ce run.

### Méthode `loom_ia.adapters.stores.jsonl.JsonlEventStore.query`

```python
async def query(self, query: EventQuery) -> list[Event]
```

Cherche des événements dans les journaux d'un client (`EventStore.query`).

Lit le journal de la session demandée, ou tous ceux du client, puis
laisse `EventQuery.select` filtrer, ordonner et paginer.

**Arguments :**

- `query` : Les critères, l'ordre et la pagination (`EventQuery`).

### Méthode `loom_ia.adapters.stores.jsonl.JsonlEventStore.last_seq`

```python
async def last_seq(self, tenant_id: TenantId, session_id: SessionId) -> int
```

Dernier `seq` du journal d'une session ; 0 s'il est vide ou inconnu.

Lit la marque de la dernière ligne, par la fin du fichier, sans ouvrir sa
charge ; `JournalCorrupted` si cette ligne est illisible.

### Méthode `loom_ia.adapters.stores.jsonl.JsonlEventStore.sessions`

```python
async def sessions(self, tenant_id: TenantId) -> list[SessionRecord]
```

Sessions d'un client, de la plus récemment écrite à la plus ancienne (fonction F7).

Lit la marque de la dernière ligne de chaque fichier, sans ouvrir de
charge, et saute une ligne illisible : un journal abîmé, ou dont la clé
est effacée, reste listable et supprimable.

**Arguments :**

- `tenant_id` : Le client.

### Méthode `loom_ia.adapters.stores.jsonl.JsonlEventStore._last_mark_of`

```python
def _last_mark_of(self, path: Path) -> EventMark | None
```

Repères du dernier événement du journal, lus par la fin du fichier.

### Méthode `loom_ia.adapters.stores.jsonl.JsonlEventStore._last_mark_strict`

```python
def _last_mark_strict(self, path: Path) -> EventMark | None
```

Repères du dernier événement ; `JournalCorrupted` si sa ligne est illisible.

Comme `append`, qui lève sur cette ligne : annoncer un numéro plus ancien
ferait ouvrir une écriture qui échouera.

### Méthode `loom_ia.adapters.stores.jsonl.JsonlEventStore.delete`

```python
async def delete(self, tenant_id: TenantId, session_id: SessionId) -> int
```

Supprime le journal d'une session (RGPD) ; rend le nombre d'événements supprimés.

Supprime le fichier, et la ligne incomplète mise de côté (`.corrupt`).

### Méthode `loom_ia.adapters.stores.jsonl.JsonlEventStore._delete_sync`

```python
def _delete_sync(self, path: Path) -> int
```

Supprime le fichier de la session, ligne incomplète mise de côté comprise.

### Méthode `loom_ia.adapters.stores.jsonl.JsonlEventStore.aclose`

```python
async def aclose(self) -> None
```

Rien à fermer : aucun fichier ne reste ouvert.
