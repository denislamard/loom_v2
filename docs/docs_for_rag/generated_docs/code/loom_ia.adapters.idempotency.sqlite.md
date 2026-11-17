# loom_ia.adapters.idempotency.sqlite

Module `loom_ia.adapters.idempotency.sqlite` de loom_v2 (fichier `src/loom_ia/adapters/idempotency/sqlite.py`).

Magasin d'idempotence en SQLite : partagé, durable, entre process (points 18 et 49).

C'est le magasin que réclame une clé **métier** : elle doit être vue depuis un
autre run, une autre session, un autre worker, et survivre à un redémarrage.
Le magasin `journal` ne voit que son run, `memory` que son process.

Une table, une ligne par clé. `reserve` tient en **une seule instruction** :

    INSERT … ON CONFLICT(key) DO UPDATE … WHERE idempotency.expires_at < <maintenant>

Prendre la clé si elle est libre, la reprendre si la ligne qui la tient a
expiré (une réservation dont l'effet est d'état inconnu, ou un résultat qui
n'a plus cours), ne rien faire sinon — et c'est SQLite qui arbitre, pas nous. Un
`get` suivi d'un `INSERT` serait un check-then-act : entre les deux, un
second worker passerait. Le nombre de lignes touchées (0 ou 1) fait foi.

Une réservation périmée n'est jamais effacée d'elle-même : c'est la trace
d'un effet d'état inconnu, et l'effacer la ferait passer pour un appel jamais
lancé. Seuls les résultats hors rétention s'oublient, au fil des écritures.

La colonne `holder` porte le jeton du détenteur de la clé : `complete` et
`release` qui en présentent un ne touchent que la ligne qui est encore à lui.
Elle est **nullable** et ajoutée à l'ouverture d'une base créée avant elle ;
une ligne sans jeton (écrite avant, ou sans) ne répond à aucun jeton.

La base supprime en écrasant (`secure_delete`) et `forget` vide ensuite le
WAL, pour qu'un client oublié ne laisse pas de trace dans les fichiers (RGPD).

## Constantes de `loom_ia.adapters.idempotency.sqlite`

- `SCHEMA = '\nCREATE TABLE IF NOT EXISTS idempotency (\n key TEXT NOT NULL PRIMARY KEY,\n tenant_id TEXT NOT NULL,\n session_id TE…` : Table `idempotency` et ses index, créés s'ils manquent.
- `_RESERVE = "\nINSERT INTO idempotency (key, tenant_id, session_id, status, result, expires_at, holder)\nVALUES (?, ?, ?, 'in_progr…` : Prend la clé, ou reprend une ligne périmée. Ce qui protège, c'est la date : une réservation encore tenue ou un résultat encore mémorisé ne bougent pas. Passée leur échéance, la clé est libre — une réservation périmée parce que son effet est d'état inconnu, un résultat parce qu'il n'a plus cours.
- `_COMPLETE_HELD = _COMPLETE + ' AND holder = ?'` : Avec un jeton de détenteur : la même instruction, bornée à la ligne encore à lui.

## Classe `loom_ia.adapters.idempotency.sqlite.SqliteIdempotency`

```python
class SqliteIdempotency
```

Magasin durable dans un fichier SQLite, sa propre base.

`retention` : durée de vie d'un résultat mémorisé quand l'outil n'en
fixe pas. Les résultats périmés sont effacés au fil des écritures ; les
réservations, jamais.

### Méthode `loom_ia.adapters.idempotency.sqlite.SqliteIdempotency._connect`

```python
async def _connect(self) -> aiosqlite.Connection
```

La connexion, ouverte au premier usage.

WAL, commits synchrones, attente de verrou de 5 s, suppression par
écrasement, schéma créé ou complété (colonne `holder`).

### Méthode `loom_ia.adapters.idempotency.sqlite.SqliteIdempotency.get`

```python
async def get(self, key: str) -> IdempotencyRecord | None
```

Un résultat hors de sa rétention n'est pas rendu ; une réservation périmée, si.

### Méthode `loom_ia.adapters.idempotency.sqlite.SqliteIdempotency.reserve`

```python
async def reserve(self, key: str, ttl: float, scope: KeyScope, *, holder: str | None=None) -> bool
```

Prend la clé pour `ttl` secondes ; faux si quelqu'un la tient déjà (`IdempotencyStore.reserve`).

Ce qui protège, c'est la date : une réservation tenue ou un résultat
encore mémorisé gardent la clé ; une clé périmée peut être reprise.

**Arguments :**

- `key` : La clé.
- `ttl` : Durée de la réservation, en secondes.
- `scope` : De qui est la clé (client, session), pour l'oublier avec eux.
- `holder` : Jeton propre à cette prise de clé.

**Renvoie :**

Vrai si la clé est prise.

### Méthode `loom_ia.adapters.idempotency.sqlite.SqliteIdempotency.complete`

```python
async def complete(self, key: str, result: object, ttl: float | None=None, *, holder: str | None=None) -> None
```

Enregistre ce que l'effet a rendu : la clé cesse d'être une réservation (`IdempotencyStore.complete`).

Les résultats périmés sont d'abord effacés ; les réservations, jamais.

**Arguments :**

- `key` : La clé réservée.
- `result` : Ce que l'outil a rendu.
- `ttl` : Durée de consultation du résultat, en secondes ; `None` : la rétention du magasin.
- `holder` : Avec un jeton, l'enregistrement n'a lieu que s'il est celui de la clé ; sinon, il est sans effet.

**Lève :**

- `KeyError` : Le magasin ne connaît plus la clé.

### Méthode `loom_ia.adapters.idempotency.sqlite.SqliteIdempotency.release`

```python
async def release(self, key: str, *, holder: str | None=None) -> None
```

Rend une clé réservée dont l'effet ne s'est pas produit (`IdempotencyStore.release`).

Un résultat enregistré n'est jamais rendu ; avec un jeton, seule une
réservation encore à ce détenteur l'est.

**Arguments :**

- `key` : La clé réservée.
- `holder` : Le jeton de la prise de clé.

### Méthode `loom_ia.adapters.idempotency.sqlite.SqliteIdempotency.forget`

```python
async def forget(self, tenant_id: TenantId, session_id: SessionId | None=None) -> int
```

Oublie les clés d'un client, ou de l'une de ses sessions (RGPD).

### Méthode `loom_ia.adapters.idempotency.sqlite.SqliteIdempotency.aclose`

```python
async def aclose(self) -> None
```

Ferme la connexion, si elle est ouverte.

## Fonction `loom_ia.adapters.idempotency.sqlite._add_holder`

```python
async def _add_holder(connection: aiosqlite.Connection) -> None
```

Ajoute `holder` à une table créée avant lui ; sans effet s'il est déjà là.
