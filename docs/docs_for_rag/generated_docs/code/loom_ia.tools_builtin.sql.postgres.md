# loom_ia.tools_builtin.sql.postgres

Module `loom_ia.tools_builtin.sql.postgres` de loom_v2 (fichier `src/loom_ia/tools_builtin/sql/postgres.py`).

Une base Postgres : un pool ouvert au premier appel, et le rôle de la base qui fait foi.

La barrière est le **rôle** du DSN : ses droits, table par table. L'outil
s'en assure au premier usage du pool (`_check`) :

- un superutilisateur, ou un rôle `BYPASSRLS`, est refusé : il passe outre
  tout droit, et la config ne promettrait plus rien ;
- un droit que la config accorde et que la base refuse fait échouer chaque
  appel, jusqu'à ce qu'il soit donné : le modèle croirait pouvoir le faire ;
- un droit que la base accorde en plus de la config (une autre table, TRUNCATE,
  CREATE sur le schéma, tables temporaires…) est dit une fois, en
  avertissement : la défense ne repose plus alors que sur l'analyse.

Chaque appel est une transaction — en lecture seule pour `query` et
`schema` — qui pose en local `search_path` (le schéma déclaré),
`statement_timeout` et `lock_timeout`. Chaque instruction est préparée
(protocole étendu) : le serveur refuse alors d'en recevoir plusieurs, et
donne les types de ses paramètres, qui servent à convertir les valeurs JSON
du modèle (asyncpg refuse une chaîne pour une `date`).

Une annulation (délai de l'outil, arrêt du run) annule la requête côté serveur
(asyncpg envoie l'annulation) et la transaction ; la connexion est rendue au
pool, qui la remet à zéro ou la ferme.

## Constantes de `loom_ia.tools_builtin.sql.postgres`

- `Held = PoolConnectionProxy[asyncpg.Record]` : Une connexion prise au pool asyncpg.
- `POOL_MAX = 4` : Connexions au plus dans le pool d'une base.
- `CONNECT_TIMEOUT = 10.0` : Délai de connexion à la base, en secondes.

## Classe `loom_ia.tools_builtin.sql.postgres.PostgresDatabase`

```python
class PostgresDatabase
```

Une base Postgres de la config, montée : son DSN (jamais montré), ses droits.

### Méthode `loom_ia.tools_builtin.sql.postgres.PostgresDatabase.where`

```python
def where(self) -> Place
```

Où est la base, pour `loom validate` : la variable du DSN et le schéma.

### Méthode `loom_ia.tools_builtin.sql.postgres.PostgresDatabase.query`

```python
async def query(self, unit: Statement, analysis: Analysis) -> dict[str, JsonValue]
```

Lance un SELECT dans une transaction en lecture seule ; rend au plus `max_rows` lignes, et dit si le résultat est coupé.

### Méthode `loom_ia.tools_builtin.sql.postgres.PostgresDatabase.execute`

```python
async def execute(self, units: list[tuple[Statement, Analysis]]) -> dict[str, JsonValue]
```

Exécute les écritures dans une même transaction.

Au-delà de `max_affected_rows` lignes modifiées, ou à la première erreur, rien
n'est enregistré.

### Méthode `loom_ia.tools_builtin.sql.postgres.PostgresDatabase.introspect`

```python
async def introspect(self) -> list[TableInfo]
```

Les tables et vues déclarées, lues dans le catalogue : colonnes, commentaires, contraintes (sauf les clés étrangères vers une table non permise).

### Méthode `loom_ia.tools_builtin.sql.postgres.PostgresDatabase.aclose`

```python
async def aclose(self) -> None
```

Ferme le pool, s'il est ouvert.

### Méthode `loom_ia.tools_builtin.sql.postgres.PostgresDatabase._run`

```python
async def _run[T](self, work: Callable[[Held], Awaitable[T]], *, readonly: bool) -> T
```

Fait `work` sur une connexion du pool, dans une transaction, réglages de session posés ; les erreurs de la base deviennent des `ToolError`.

### Méthode `loom_ia.tools_builtin.sql.postgres.PostgresDatabase._ready`

```python
async def _ready(self) -> asyncpg.Pool[asyncpg.Record]
```

Le pool, créé au premier appel ; la connexion vérifiée une fois.

**Lève :**

- `ToolError` : Base injoignable, ou connexion refusée.

### Méthode `loom_ia.tools_builtin.sql.postgres.PostgresDatabase._check`

```python
async def _check(self, conn: Held) -> None
```

Le rôle, et ses droits face à la config ; voir la docstring du module.

## Classe `loom_ia.tools_builtin.sql.postgres._Failed`

```python
class _Failed(Exception)
```

Une instruction d'`execute` refusée par la base.

**Attributs :**

- `position` : Son rang, à partir de 1.
- `error` : L'erreur de la base.

## Fonction `loom_ia.tools_builtin.sql.postgres._affected`

```python
def _affected(status: str | None, returned: int) -> int
```

« UPDATE 3 », « INSERT 0 1 », « DELETE 2 » : le dernier nombre.

## Fonction `loom_ia.tools_builtin.sql.postgres.convert`

```python
def convert(params: Sequence[ParamValue], types: Sequence[Type], zone: ZoneInfo) -> list[object]
```

Les paramètres convertis aux types que la base attend, un par `$n`.

**Lève :**

- `ToolError` : Mauvais nombre de paramètres, ou valeur qui ne convient pas.

## Fonction `loom_ia.tools_builtin.sql.postgres._value`

```python
def _value(value: ParamValue, expected: Type, zone: ZoneInfo, where: str) -> object
```

Un paramètre converti au type attendu ; un tableau, élément par élément.

**Lève :**

- `ToolError` : La valeur ne convient pas, `$n` nommé.

## Fonction `loom_ia.tools_builtin.sql.postgres._scalar`

```python
def _scalar(value: ParamValue, name: str, expected: Type, zone: ZoneInfo) -> object
```

Une valeur simple convertie au type Postgres.

Entiers, flottants, numeric, booléens, textes et enums, dates et heures
ISO 8601 (une heure sans fuseau prise dans `zone`), uuid, intervalles ;
json et jsonb tels quels.

**Lève :**

- `ToolError` : La valeur ne convient pas, ou le type n'est pas converti.
