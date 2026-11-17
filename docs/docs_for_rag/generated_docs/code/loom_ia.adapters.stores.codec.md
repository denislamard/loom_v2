# loom_ia.adapters.stores.codec

Module `loom_ia.adapters.stores.codec` de loom_v2 (fichier `src/loom_ia/adapters/stores/codec.py`).

Forme d'une ligne de journal, en clair ou scellée (point 30, conception §11.5).

Un store range un événement en texte JSON et le relit. Entre les deux, il
passe par un **codec** : en clair, c'est le JSON de l'événement, tel qu'il a
toujours été écrit ; scellé, c'est la même enveloppe — identifiants,
horodatage, type, catégorie, statut, agent, rôle, facettes, `seq` — avec la
**charge fermée** à la place de la charge.

Pourquoi ici, et pas dans un enrobage d'`EventStore` comme le journal
notifiant : le port parle `Event`, et un `Event` ne peut pas porter une
charge fermée. L'union des charges est discriminée par `type`, et
l'enveloppe vérifie à chaque relecture que son type, sa catégorie, son
statut et ses facettes sont bien ceux de sa charge. Faire entrer une charge
fermée dans cette union obligerait tout lecteur de charge — moteur,
projections, rejeu, masquage — à la reconnaître et à la narrer. Le sceau vit
donc là où la sérialisation vit déjà : dans les stores, les seuls à voir du
texte.

**Ce que l'enveloppe garde en clair, elle le garde pour de bon.** Les
colonnes et les facettes `jsonb` des stores SQL sont remplies avec ces
champs : un journal scellé se filtre exactement comme un journal en clair —
par client, session, run, type, agent, outil, modèle —, et une page de
`GET /events` ne coûte pas plus cher. Le contenu, lui, est dans la charge,
et une facette est par règle un champ qui n'en porte pas (jalon J5.4c).

**Ce que l'AAD couvre :** le journal (client, session), la position (`seq`),
l'identité (`event_id`) et l'**horodatage** (`ts`). Pas le type ni les
facettes — l'enveloppe les recopie de la charge et les vérifie à la relecture,
si bien qu'une facette changée sous un sceau ouvert est déjà une erreur de
validation. Une charge déplacée d'un journal à un autre, d'une position à une
autre, ou re-datée, ne se verrait pas : c'est ce que l'AAD refuse.

L'horodatage y est entré en 5.5c, et pour une raison précise : la rétention
décide d'**effacer** une session sur la date de sa dernière écriture. Re-dater
une ligne, ce serait donc garder un journal pour toujours ou le faire
supprimer avant l'heure — un champ dont dépend une suppression doit être tenu.

**Une ligne en clair reste lisible par un codec scellant.** Sceller un
journal qui existe déjà ne le réécrit pas : l'ancien se relit tel quel, et
ce qui s'y ajoute est fermé. L'inverse n'est pas vrai, et c'est le principe :
sans la clé, ce qui a été fermé ne s'ouvre plus.

## Constantes de `loom_ia.adapters.stores.codec`

- `SEALED = 'sealed'` : Champ qui distingue une charge fermée d'une charge en clair. Aucune charge durable ne porte un champ de ce nom, et un essai le vérifie : le jour où l'une en porterait un, une ligne en clair passerait pour un sceau.
- `AAD_PREFIX = b'loom.seal.v1'` : Préfixe de l'AAD : il dit de quelle version du sceau il s'agit, et sépare ces octets de tout autre usage de la même clé.
- `PLAIN = PlainCodec()` : Le codec en clair, celui d'un journal sans sceau.

## Classe `loom_ia.adapters.stores.codec.Seal`

```python
class Seal(DomainModel)
```

Charge fermée, telle qu'elle est rangée à la place de la charge.

**Attributs :**

- `sealed` : La charge fermée, en base64.
- `key_id` : Identifiant de la clé qui l'a fermée.

## Classe `loom_ia.adapters.stores.codec.EventMark`

```python
class EventMark(DomainModel)
```

Ce qu'une ligne dit sans être ouverte : où elle est, quand, laquelle.

Assez pour lister des sessions et connaître la longueur d'un journal —
ce que font `sessions()` et `last_seq()` — sans la clé du client :
un journal dont la clé est effacée se laisse encore **voir et
supprimer**, ce qu'exige la suppression RGPD, sans rien livrer de son
contenu.

**Attributs :**

- `tenant_id` : Client du journal.
- `session_id` : Session du journal.
- `seq` : Place de l'événement dans le journal.
- `event_id` : Identifiant de l'événement.
- `ts` : Horodatage de l'événement.

## Classe `loom_ia.adapters.stores.codec.JournalCodec`

```python
class JournalCodec(Protocol)
```

Passage entre un événement et la ligne qui le range.

### Méthode `loom_ia.adapters.stores.codec.JournalCodec.dumps`

```python
def dumps(self, event: Event) -> str
```

Ligne qui range cet événement, sans saut de ligne.

### Méthode `loom_ia.adapters.stores.codec.JournalCodec.loads`

```python
def loads(self, line: str | bytes) -> Event
```

Événement de cette ligne ; `MissingKey` si son sceau reste fermé.

### Méthode `loom_ia.adapters.stores.codec.JournalCodec.mark`

```python
def mark(self, line: str | bytes) -> EventMark
```

Repères de cette ligne, sans l'ouvrir.

## Classe `loom_ia.adapters.stores.codec.PlainCodec`

```python
class PlainCodec
```

Le JSON de l'événement, tel quel : le journal de toujours.

### Méthode `loom_ia.adapters.stores.codec.PlainCodec.dumps`

```python
def dumps(self, event: Event) -> str
```

La ligne d'un événement : son JSON, en clair.

### Méthode `loom_ia.adapters.stores.codec.PlainCodec.loads`

```python
def loads(self, line: str | bytes) -> Event
```

L'événement d'une ligne ; un champ ajouté par un code plus récent est ignoré.

### Méthode `loom_ia.adapters.stores.codec.PlainCodec.mark`

```python
def mark(self, line: str | bytes) -> EventMark
```

Les repères d'une ligne (`EventMark`).

## Classe `loom_ia.adapters.stores.codec.SealingCodec`

```python
class SealingCodec
```

Codec qui ferme la charge avec la clé du client, et l'ouvre avec.

### Méthode `loom_ia.adapters.stores.codec.SealingCodec.dumps`

```python
def dumps(self, event: Event) -> str
```

La ligne d'un événement, sa charge fermée avec la clé courante du client.

L'enveloppe reste en clair, exactement comme Pydantic l'écrit ; la charge
devient `{"sealed": …, "key_id": …}`, authentifiée avec le journal, la
place et l'heure de l'événement (`aad_of`).

### Méthode `loom_ia.adapters.stores.codec.SealingCodec.loads`

```python
def loads(self, line: str | bytes) -> Event
```

L'événement d'une ligne, sa charge ouverte avec la clé qui l'a fermée.

Une ligne écrite avant que le sceau soit déclaré se lit telle quelle.

**Lève :**

- `MissingKey` : La clé qui a fermé la charge n'est plus, ou pas encore, dans le trousseau du client.
- `JournalCorrupted` : La ligne, le sceau ou la charge ouverte est illisible.

### Méthode `loom_ia.adapters.stores.codec.SealingCodec.mark`

```python
def mark(self, line: str | bytes) -> EventMark
```

Les repères d'une ligne, sans ouvrir sa charge.

### Méthode `loom_ia.adapters.stores.codec.SealingCodec._opener`

```python
def _opener(self, tenant_id: TenantId, key_id: str) -> Cipher
```

La clé du client qui a fermé cette charge ; `MissingKey` si son trousseau ne l'a pas, en nommant la clé à remettre.

## Fonction `loom_ia.adapters.stores.codec.aad_of`

```python
def aad_of(tenant_id: str, session_id: str, seq: int, event_id: str, ts: datetime) -> bytes
```

Ce que le sceau authentifie sans le chiffrer : le journal, la place, l'heure.

L'horodatage est ramené à UTC avant d'être écrit : l'écriture part d'un
`datetime`, la relecture d'un texte analysé, et deux représentations du
même instant donneraient sinon deux AAD différentes.
