# loom_ia.adapters.idempotency.postgres

Module `loom_ia.adapters.idempotency.postgres` de loom_v2 (fichier `src/loom_ia/adapters/idempotency/postgres.py`).

Magasin d'idempotence en Postgres : partagé entre machines (points 18 et 49).

Le magasin `sqlite` suffit à plusieurs process d'une même machine ; celui-ci
tient quand les workers sont sur plusieurs machines, ce qui est le cas dès
que la file entre en jeu (jalon J5.3b).

`reserve` tient en une seule instruction, comme en SQLite — prendre la clé
si elle est libre, la reprendre si la réservation d'un autre a expiré, ne
rien faire sinon —, et c'est Postgres qui arbitre : ``ON CONFLICT (key) DO
UPDATE … WHERE`. Le nombre de lignes touchées fait foi. Un `get`` suivi
d'un `INSERT` serait un check-then-act, et deux workers passeraient.

Pas de politique de lignes sur cette table, contrairement au journal :
`get(key)` ne nomme pas de client, et une politique par client rendrait
invisible la ligne qu'il faut justement relire. Ce qui cadre une clé, c'est
son préfixe par client (point 49) ; ce que portent les colonnes `tenant_id` et
`session_id`, c'est de quoi l'oublier avec sa session (RGPD).

La colonne `holder` porte le jeton du détenteur de la clé : `complete` et
`release` qui en présentent un ne touchent que la ligne encore à lui. Elle
est nullable, et ajoutée par l'ouverture à une table créée avant elle, une
seule fois : la version du schéma, inscrite dans `loom_schema`, dit si c'est
fait (il y faut le droit de `ALTER TABLE` ; sinon `loom storage sql` donne
ce qu'un exploitant applique). Une ligne sans jeton ne répond à aucun jeton.

## Constantes de `loom_ia.adapters.idempotency.postgres`

- `_RESERVE = f"\nINSERT INTO {IDEMPOTENCY_TABLE} (key, tenant_id, session_id, status, result, expires_at, holder)\nVALUES ($1, $2, $…` : Ce qui protège, c'est la date : une réservation encore tenue ou un résultat encore mémorisé ne bougent pas. Passée leur échéance, la clé est libre. La date est celle de **la base** (`now()`) : les process qui se disputent une clé n'ont pas la même horloge, et c'est la seule qu'ils partagent. Les durées arrivent en secondes et sont ajoutées côté base.
- `_COMPLETE_HELD = _COMPLETE + ' AND holder = $4'` : Avec un jeton de détenteur : la même instruction, bornée à la ligne encore à lui.

## Classe `loom_ia.adapters.idempotency.postgres.PostgresIdempotency`

```python
class PostgresIdempotency
```

Magasin durable dans une base Postgres.

`retention` : durée de vie d'un résultat mémorisé quand l'outil n'en
fixe pas. Les résultats périmés sont effacés au fil des écritures ; les
réservations, jamais — une réservation périmée est la trace d'un effet
d'état inconnu, et l'effacer la ferait passer pour un appel jamais lancé.

### Méthode `loom_ia.adapters.idempotency.postgres.PostgresIdempotency.__init__`

```python
def __init__(self, dsn: str, *, role: str | None=DEFAULT_ROLE, retention: float=DEFAULT_RETENTION, limits: PoolLimits=DEFAULT_LIMITS) -> None
```

Prépare le pool de connexions de la table d'idempotence, avec son DDL et sa mise à niveau.

**Arguments :**

- `dsn` : Adresse de la base.
- `role` : Rôle applicatif pris par chaque connexion ; `None` : celui de la connexion.
- `retention` : Durée de vie d'un résultat, en secondes, quand l'outil n'en fixe pas.
- `limits` : Taille et délais du pool.

### Méthode `loom_ia.adapters.idempotency.postgres.PostgresIdempotency.get`

```python
async def get(self, key: str) -> IdempotencyRecord | None
```

Un résultat hors de sa rétention n'est pas rendu ; une réservation périmée, si.

### Méthode `loom_ia.adapters.idempotency.postgres.PostgresIdempotency.reserve`

```python
async def reserve(self, key: str, ttl: float, scope: KeyScope, *, holder: str | None=None) -> bool
```

Prend la clé pour `ttl` secondes ; faux si quelqu'un la tient déjà (`IdempotencyStore.reserve`).

Ce qui protège, c'est la date : une réservation tenue ou un résultat
encore mémorisé gardent la clé ; une clé périmée peut être reprise. La date est celle de la base : les process qui se
disputent une clé n'ont pas la même horloge.

**Arguments :**

- `key` : La clé.
- `ttl` : Durée de la réservation, en secondes.
- `scope` : De qui est la clé (client, session), pour l'oublier avec eux.
- `holder` : Jeton propre à cette prise de clé.

**Renvoie :**

Vrai si la clé est prise.

### Méthode `loom_ia.adapters.idempotency.postgres.PostgresIdempotency.complete`

```python
async def complete(self, key: str, result: object, ttl: float | None=None, *, holder: str | None=None) -> None
```

Enregistre ce que l'effet a rendu : la clé cesse d'être une réservation (`IdempotencyStore.complete`).

Les résultats périmés sont d'abord effacés ; les réservations, jamais. Le tout dans une transaction.

**Arguments :**

- `key` : La clé réservée.
- `result` : Ce que l'outil a rendu.
- `ttl` : Durée de consultation du résultat, en secondes ; `None` : la rétention du magasin.
- `holder` : Avec un jeton, l'enregistrement n'a lieu que s'il est celui de la clé ; sinon, il est sans effet.

**Lève :**

- `KeyError` : Le magasin ne connaît plus la clé.

### Méthode `loom_ia.adapters.idempotency.postgres.PostgresIdempotency.release`

```python
async def release(self, key: str, *, holder: str | None=None) -> None
```

Rend une clé réservée dont l'effet ne s'est pas produit (`IdempotencyStore.release`).

Un résultat enregistré n'est jamais rendu ; avec un jeton, seule une
réservation encore à ce détenteur l'est.

**Arguments :**

- `key` : La clé réservée.
- `holder` : Le jeton de la prise de clé.

### Méthode `loom_ia.adapters.idempotency.postgres.PostgresIdempotency.forget`

```python
async def forget(self, tenant_id: TenantId, session_id: SessionId | None=None) -> int
```

Oublie les clés d'un client, ou de l'une de ses sessions (RGPD).

### Méthode `loom_ia.adapters.idempotency.postgres.PostgresIdempotency.aclose`

```python
async def aclose(self) -> None
```

Ferme le pool de connexions.
