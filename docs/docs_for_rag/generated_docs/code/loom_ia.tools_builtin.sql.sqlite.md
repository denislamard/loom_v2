# loom_ia.tools_builtin.sql.sqlite

Module `loom_ia.tools_builtin.sql.sqlite` de loom_v2 (fichier `src/loom_ia/tools_builtin/sql/sqlite.py`).

Une base SQLite : une connexion par appel, et l'authorizer qui fait foi.

SQLite n'a pas de rôles : la barrière est l'**authorizer** de la connexion,
appelé à la préparation de chaque instruction — triggers et vues compris.
Il accorde, table par table, ce que la config accorde, et rien d'autre :
DDL, `ATTACH`, `PRAGMA`, fonctions de `FORBIDDEN` sont refusés.

Ce qu'il voit, essayé le 26/10 (SQLite 3.53) :

- une lecture de colonne : `READ(table, colonne, 'main', origine)` ; l'origine
  est la vue, la CTE ou le trigger qui la fait, `None` à la racine ;
- `count(*)` : `READ(table, '', None, None)` — la même forme qu'une CTE
  lue sans colonne. Une table non déclarée y est donc refusée, à moins que
  l'analyse n'en ait fait une CTE ;
- `count(*)` sur une vue : ses colonnes lues avec la vue pour origine, puis
  `READ(table, '', 'main', None)` — permis pour une table qu'une vue permise
  vient de lire dans le même appel ;
- un trigger qui écrit : `INSERT(table, None, 'main', trigger)`. L'agent ne
  peut pas créer de trigger (DDL refusé) : ceux de la base sont ceux de son
  propriétaire, et leurs accès sont permis.

La connexion s'ouvre en `mode=ro` pour une lecture, `mode=rw` pour une
écriture, jamais `rwc` : l'outil ne crée pas de base. `trusted_schema`
est coupé et `ATTACH` borné à zéro avant que l'authorizer ne soit posé.
Le délai d'une instruction passe par `set_progress_handler` ; une
annulation (délai de l'outil, arrêt du run) interrompt la connexion depuis
la boucle (`interrupt`), et la transaction est annulée.

## Constantes de `loom_ia.tools_builtin.sql.sqlite`

- `_PROGRESS_STEPS = 1000` : Toutes les N instructions de la machine virtuelle de SQLite, le délai est regardé.

## Classe `loom_ia.tools_builtin.sql.sqlite.Objects`

```python
@dataclass(frozen=True, slots=True)
class Objects
```

Les vues et les triggers de la base : l'origine d'un accès en est un, ou une CTE.

## Fonction `loom_ia.tools_builtin.sql.sqlite.authorizer`

```python
def authorizer(catalog: Catalog, objects: Objects, ctes: frozenset[str], *, write: bool) -> Callable[[int, str | None, str | None, str | None, str | None], int]
```

L'authorizer d'un appel : les droits de la config, rien d'autre.

## Classe `loom_ia.tools_builtin.sql.sqlite.SqliteDatabase`

```python
class SqliteDatabase
```

Une base SQLite de la config, montée : son fichier et ses droits.

### Méthode `loom_ia.tools_builtin.sql.sqlite.SqliteDatabase.where`

```python
def where(self) -> Place
```

Où est la base, pour `loom validate` : son fichier.

### Méthode `loom_ia.tools_builtin.sql.sqlite.SqliteDatabase.query`

```python
async def query(self, unit: Statement, analysis: Analysis) -> dict[str, JsonValue]
```

Lance un SELECT dans une transaction annulée ensuite ; rend au plus `max_rows` lignes, et dit si le résultat est coupé.

### Méthode `loom_ia.tools_builtin.sql.sqlite.SqliteDatabase.execute`

```python
async def execute(self, units: list[tuple[Statement, Analysis]]) -> dict[str, JsonValue]
```

Exécute les écritures dans une même transaction.

Au-delà de `max_affected_rows` lignes modifiées, ou à la première erreur, rien
n'est enregistré.

### Méthode `loom_ia.tools_builtin.sql.sqlite.SqliteDatabase.introspect`

```python
async def introspect(self) -> list[TableInfo]
```

Les tables et vues déclarées, lues dans `sqlite_schema` : colonnes et DDL d'origine.

### Méthode `loom_ia.tools_builtin.sql.sqlite.SqliteDatabase.aclose`

```python
async def aclose(self) -> None
```

Rien à fermer : chaque appel ouvre sa connexion.

### Méthode `loom_ia.tools_builtin.sql.sqlite.SqliteDatabase._connect`

```python
def _connect(self, ctes: frozenset[str], *, write: bool) -> sqlite3.Connection
```

Une connexion bridée : schéma non fiable, pas d'ATTACH, délai d'instruction, authorizer qui n'ouvre que les tables et droits permis.

### Méthode `loom_ia.tools_builtin.sql.sqlite.SqliteDatabase._run`

```python
async def _run[T](self, work: Callable[[sqlite3.Connection], T], ctes: frozenset[str], *, write: bool) -> T
```

Fait `work` dans un thread, sur sa propre connexion ; une annulation interrompt l'instruction en cours.

Les erreurs deviennent des `ToolError`.

## Classe `loom_ia.tools_builtin.sql.sqlite._Failed`

```python
class _Failed(Exception)
```

Une instruction d'`execute` refusée par la base.

**Attributs :**

- `position` : Son rang, à partir de 1.
- `error` : L'erreur de la base.

## Classe `loom_ia.tools_builtin.sql.sqlite._TooMany`

```python
class _TooMany(Exception)
```

Trop de lignes modifiées : la transaction est annulée.

## Fonction `loom_ia.tools_builtin.sql.sqlite._bindings`

```python
def _bindings(params: tuple[ParamValue, ...]) -> dict[str, object]
```

`$1…$n` sont des paramètres nommés pour SQLite : « 1 », « 2 », etc.
