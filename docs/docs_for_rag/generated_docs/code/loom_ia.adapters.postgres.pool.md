# loom_ia.adapters.postgres.pool

Module `loom_ia.adapters.postgres.pool` de loom_v2 (fichier `src/loom_ia/adapters/postgres/pool.py`).

Raccordement Postgres : DSN, pool, rôle applicatif et client par transaction.

Partagé par le journal (`adapters.stores.postgres`) et l'idempotence
(`adapters.idempotency.postgres`), qui ne se connaissent pas — les familles
d'adaptateurs sont indépendantes — mais ont le même raccordement à faire.

Quatre choses s'y jouent, et aucune ne va de soi.

**Le DSN ne vient jamais de la config.** Elle nomme la variable
d'environnement qui le porte (`dsn_env`), comme elle nomme déjà celle d'une
clé d'API : un mot de passe n'a pas à se trouver dans un fichier versionné
(conception §16.3).

**Le schéma est créé à la première ouverture**, si le rôle connecté le peut.
Sinon l'erreur renvoie vers `loom storage sql`, dont la sortie s'applique
avec le rôle qui en a le droit. Le DDL est rejouable, donc les deux chemins
cohabitent. Un seul process le pose à la fois, sous verrou consultatif, et
un pool ne s'ouvre qu'une fois même si ses premières requêtes arrivent
ensemble.

**Chaque connexion prend le rôle applicatif.** La sécurité au niveau des
lignes ne s'applique pas au propriétaire d'une table, sauf `FORCE` — que le
DDL pose —, et le rôle applicatif n'a de toute façon pas le droit de modifier
un événement écrit. Le rôle est pris une fois par connexion, pas par
transaction : c'est le pool qui le fait à l'ouverture.

**Le client est posé par transaction**, par `set_config(..., true)` dont le
`true` veut dire « local à la transaction ». Toute lecture et toute
écriture passent donc par une transaction, même une simple lecture : hors
transaction, le réglage ne vaudrait que pour l'instruction suivante et la
politique ne verrait rien.

## Constantes de `loom_ia.adapters.postgres.pool`

- `Held = PoolConnectionProxy[asyncpg.Record]` : Connexion tenue le temps d'une transaction : ce que rend le pool, et non un `asyncpg.Connection`, qu'il enrobe.
- `MIN_SIZE = 1` : Connexions gardées ouvertes par pool. Basse par défaut : un service de quelques agents n'a pas besoin de dix connexions, et un client qui déclare son propre stockage a son propre pool (conception §7.3).
- `MAX_SIZE = 10` : Connexions ouvertes au plus par pool.
- `COMMAND_TIMEOUT = 60.0` : Délais par défaut, en secondes. Avant eux, rien ne bornait l'attente : un verrou tenu par une transaction coincée gelait tous les `append` de la session, et un pool épuisé faisait attendre `acquire()` sans fin.
- `LOCK_TIMEOUT = 10.0` : Attente d'un verrou par défaut, en secondes (`lock_timeout`) ; au-delà, `JournalBusy`.
- `ACQUIRE_TIMEOUT = 30.0` : Attente d'une connexion libre par défaut, en secondes ; au-delà, `TimeoutError`.
- `_SCHEMA_LOCK = 'SELECT pg_advisory_lock(hashtext($1)::bigint)'` : Verrou consultatif de session, pris le temps de poser le schéma : tous les process qui ouvrent un stockage Postgres sur la même base le partagent.
- `_SET_TENANT = f"SELECT set_config('{TENANT_SETTING}', $1, true), set_config('lock_timeout', $2, true)"` : Réglages **de la transaction** (`true`) : le client pour la politique de lignes, et le délai des verrous. Le pool d'asyncpg rend ses connexions avec `RESET ALL` — un `SET` fait à l'ouverture ne survivrait pas à la première transaction.
- `DEFAULT_LIMITS = PoolLimits()` : Taille et délais d'un pool sans réglage : de 1 à 10 connexions, 60 s par instruction, 10 s par verrou, 30 s pour une connexion.

## Classe `loom_ia.adapters.postgres.pool.PoolLimits`

```python
@dataclass(frozen=True, slots=True)
class PoolLimits
```

Taille d'un pool et délais de ses attentes.

`command_timeout` borne une instruction, `lock_timeout` l'attente d'un
verrou (le verrou consultatif du journal, celui du schéma, un verrou de
ligne), `acquire_timeout` l'attente d'une connexion libre du pool. Un
dépassement lève : `TimeoutError` pour la première et la dernière,
`JournalBusy` pour la seconde (`asyncpg.LockNotAvailableError` en cause).

**Attributs :**

- `min_size` : Connexions gardées ouvertes.
- `max_size` : Connexions ouvertes au plus.
- `command_timeout` : Délai d'une instruction, en secondes.
- `lock_timeout` : Attente d'un verrou, en secondes.
- `acquire_timeout` : Attente d'une connexion libre, en secondes.

### Méthode `loom_ia.adapters.postgres.pool.PoolLimits.lock_milliseconds`

```python
@property
def lock_milliseconds(self) -> str
```

Délai de verrou tel que Postgres le lit : des millisecondes entières, une au moins.

## Classe `loom_ia.adapters.postgres.pool.PostgresNotPrepared`

```python
class PostgresNotPrepared(StorageNotReady)
```

La base n'a pas le schéma de loom, et le rôle connecté ne peut pas le poser.

Le mot de Postgres est repris tel quel : le schéma tient des tables, un
rôle et des politiques, et ce n'est pas toujours la table qui manque —
un rôle applicatif créé par quelqu'un d'autre ne s'accorde pas non plus.

## Classe `loom_ia.adapters.postgres.pool.PostgresMigrationFailed`

```python
class PostgresMigrationFailed(StorageNotReady)
```

Une migration du schéma a échoué ; elle est annulée en entier, la base n'a pas bougé.

## Classe `loom_ia.adapters.postgres.pool.RoleUnavailable`

```python
class RoleUnavailable(StorageNotReady)
```

Le rôle connecté ne peut pas endosser le rôle applicatif.

## Classe `loom_ia.adapters.postgres.pool.PostgresPool`

```python
class PostgresPool
```

Pool de connexions vers la base d'un stockage, ouvert à la première requête.

`ddl` est appliqué si `table` n'existe pas encore ; `role`, s'il est
donné, est pris par chaque connexion du pool ; `limits` fixe sa taille et
ses délais. `upgrade`, s'il est donné,
est appliqué quand la table existe déjà : la mise à niveau d'un schéma plus
ancien, qui doit pouvoir se rejouer sans effet. `schema` — (composant,
version attendue) — évite de le rejouer quand la table de versions dit que
le composant est à jour ; un rôle qui ne peut pas la lire ne migre pas.

### Méthode `loom_ia.adapters.postgres.pool.PostgresPool.__init__`

```python
def __init__(self, dsn: str, *, table: str, ddl: str, role: str | None, label: str='Postgres', upgrade: str | None=None, schema: tuple[str, int] | None=None, limits: PoolLimits=DEFAULT_LIMITS) -> None
```

Prépare le pool, sans l'ouvrir : il s'ouvre à la première requête.

**Arguments :**

- `dsn` : Adresse de la base.
- `table` : Table du stockage ; son absence fait appliquer `ddl`.
- `ddl` : SQL qui crée le stockage.
- `role` : Rôle applicatif pris par chaque connexion ; `None` : aucun.
- `label` : Nom du stockage dans les messages.
- `upgrade` : SQL de mise à niveau, rejouable sans effet.
- `schema` : (composant, version attendue), pour ne pas rejouer une mise à niveau déjà faite.
- `limits` : Taille et délais du pool.

### Méthode `loom_ia.adapters.postgres.pool.PostgresPool.pool`

```python
async def pool(self) -> asyncpg.Pool[asyncpg.Record]
```

Le pool, créé et vérifié à la première demande.

Les premières demandes simultanées attendent la même ouverture : un
seul `_prepare` et un seul pool, jamais un pool ouvert puis perdu.

### Méthode `loom_ia.adapters.postgres.pool.PostgresPool._prepare`

```python
async def _prepare(self) -> None
```

Crée le schéma s'il manque, avec une connexion **sans** rôle applicatif.

Sans rôle, parce que c'est le propriétaire qui crée : la connexion du
pool, elle, aura déjà pris le rôle applicatif et n'aurait pas le
droit.

### Méthode `loom_ia.adapters.postgres.pool.PostgresPool._up_to_date`

```python
async def _up_to_date(self, connection: asyncpg.Connection[asyncpg.Record]) -> bool
```

La table de versions dit-elle le composant à jour ? Faux si elle manque.

Un rôle qui n'a pas le droit de la lire n'est pas celui qui migre : on
le laisse ouvrir, avec un avertissement, plutôt que de lui refuser une
base que le propriétaire a préparée.

### Méthode `loom_ia.adapters.postgres.pool.PostgresPool._take_role`

```python
async def _take_role(self, connection: asyncpg.Connection[asyncpg.Record]) -> None
```

Prend le rôle applicatif sur une connexion neuve du pool.

### Méthode `loom_ia.adapters.postgres.pool.PostgresPool.transaction`

```python
@asynccontextmanager
async def transaction(self, tenant_id: str | None=None) -> AsyncGenerator[Held]
```

Une transaction, avec le client posé pour la politique s'il est donné.

Sans `tenant_id`, la politique du journal ne laisse rien passer :
c'est pour les tables qui n'en ont pas, comme celle de l'idempotence.

### Méthode `loom_ia.adapters.postgres.pool.PostgresPool.aclose`

```python
async def aclose(self) -> None
```

Ferme le pool, s'il est ouvert.

## Fonction `loom_ia.adapters.postgres.pool.rows_touched`

```python
def rows_touched(status: str) -> int
```

Nombre de lignes d'un état de commande asyncpg (« UPDATE 1 », « INSERT 0 1 »).
