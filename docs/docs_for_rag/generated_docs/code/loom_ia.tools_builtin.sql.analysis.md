# loom_ia.tools_builtin.sql.analysis

Module `loom_ia.tools_builtin.sql.analysis` de loom_v2 (fichier `src/loom_ia/tools_builtin/sql/analysis.py`).

Analyse d'une instruction, sans base : ce qu'elle est, les tables qu'elle touche, et comment.

C'est elle qui décide de l'approbation d'un appel (`assess_call` ne parle à
aucune base) et qui donne au modèle des erreurs qu'il sait corriger. Elle ne
fait pas foi : les droits de la base (rôle Postgres, authorizer SQLite) sont
la barrière ; l'analyse passe devant, et refuse plus tôt et plus clairement.

Règles :

- une instruction par entrée ; `query` n'admet qu'une lecture (`exp.Query`,
  sans `INTO` ni `FOR UPDATE`, sans écriture imbriquée), `execute` un
  `INSERT`, `UPDATE` ou `DELETE` à la racine, sans autre écriture
  imbriquée (pas de `WITH … DELETE`) ; tout le reste est refusé ;
- toute table citée est déclarée, sans schéma ni base devant ; la cible d'une
  écriture demande le droit de son opération (`ON CONFLICT DO UPDATE` :
  `update` en plus), `RETURNING`, `WHERE` et toute autre table citée
  demandent `select` ; une CTE n'est pas une table ;
- `UPDATE` et `DELETE` sans `WHERE` sont refusés, sauf `allow_full_table` ;
- une fonction que sqlglot ne connaît pas (`exp.Anonymous`) est refusée, sauf
  si la base la permet (`functions`) ; celles de `FORBIDDEN` le sont
  toujours — toutes celles qui agissent hors de la requête y sont inconnues
  de sqlglot (essai du 26/10 sur 30.24), et un test le revérifie ;
- les paramètres s'écrivent `$1`, `$2`… et chacun de `params` sert.

## Constantes de `loom_ia.tools_builtin.sql.analysis`

- `Dialect = Literal['postgres', 'sqlite']` : Le dialecte SQL de la base.
- `Operation = Literal['select', 'insert', 'update', 'delete']` : Ce qu'une instruction permise fait : lire, insérer, modifier ou supprimer.
- `FORBIDDEN = frozenset({'pg_sleep', 'pg_sleep_for', 'pg_sleep_until', 'pg_terminate_backend', 'pg_cancel_backend', 'pg_reload_conf',…` : Fonctions refusées même si une base les permet : elles agissent hors de la requête (process, verrous, séquences, fichiers, réseau, SQL dynamique).
- `FORBIDDEN_PREFIXES = ('pg_advisory', 'pg_try_advisory', 'lo_', 'dblink')` : Préfixes de familles de fonctions interdites : verrous consultatifs, gros objets, dblink.
- `_SAID = {'truncatetable': 'TRUNCATE', 'transaction': 'BEGIN', 'altertable': 'ALTER TABLE'}` : Ce que l'on dit d'une instruction refusée, quand le nom du nœud n'est pas parlant.

## Classe `loom_ia.tools_builtin.sql.analysis._QuietFallback`

```python
class _QuietFallback(logging.Filter)
```

sqlglot dit en avertissement chaque instruction qu'il ne sait pas lire, SQL compris.

Ce SQL vient du modèle, et l'outil le refuse de toute façon : il n'a rien à
faire dans les journaux de l'application.

### Méthode `loom_ia.tools_builtin.sql.analysis._QuietFallback.filter`

```python
def filter(self, record: logging.LogRecord) -> bool
```

Écarte les avertissements de repli de sqlglot ; garde le reste.

## Classe `loom_ia.tools_builtin.sql.analysis.Catalog`

```python
@dataclass(frozen=True, slots=True)
class Catalog
```

Les tables d'une base, sous le nom qui les compare, avec leurs droits.

**Attributs :**

- `dialect` : Le dialecte de la base.
- `rights` : Nom déclaré → droits.
- `functions` : Les fonctions permises en plus, en minuscules.
- `allow_full_table` : Permet un UPDATE ou un DELETE sans WHERE.

### Méthode `loom_ia.tools_builtin.sql.analysis.Catalog.of`

```python
@classmethod
def of(cls, spec: DatabaseSpec) -> Catalog
```

Le catalogue d'une base déclarée : ses tables et leurs droits, ses fonctions permises.

### Méthode `loom_ia.tools_builtin.sql.analysis.Catalog.declared`

```python
def declared(self, name: str, *, quoted: bool) -> str | None
```

Le nom déclaré qui désigne cet identifiant, ou `None`.

Postgres replie en minuscules un nom sans guillemets, et compare tel quel
un nom entre guillemets ; SQLite ne distingue pas la casse.

### Méthode `loom_ia.tools_builtin.sql.analysis.Catalog.granted`

```python
def granted(self, table: str) -> str
```

Les droits d'une table déclarée, pour un message : « select, insert ».

## Classe `loom_ia.tools_builtin.sql.analysis.Analysis`

```python
@dataclass(frozen=True, slots=True)
class Analysis
```

Une instruction jugée : son opération, et les droits qu'elle exerce, table par table.

**Attributs :**

- `operation` : Ce que fait l'instruction.
- `needs` : Table → droits exercés.
- `ctes` : Les noms des CTE : SQLite les lit comme des tables sans base (authorizer).
- `target` : La table modifiée, pour une écriture.

### Méthode `loom_ia.tools_builtin.sql.analysis.Analysis.exercised`

```python
def exercised(self) -> Iterable[tuple[str, SqlRight]]
```

Les droits exercés, table par table, dans l'ordre select, insert, update, delete.

## Fonction `loom_ia.tools_builtin.sql.analysis.analyze`

```python
def analyze(sql: str, params: int, catalog: Catalog, *, write: bool, position: str='') -> Analysis
```

L'instruction jugée, ou `ToolError` qui dit au modèle quoi corriger.

`write` : l'instruction vient d'`execute`. `position` (« instruction 2 : »)
préfixe les messages d'un `execute` à plusieurs instructions.

## Fonction `loom_ia.tools_builtin.sql.analysis._analyze`

```python
def _analyze(sql: str, params: int, catalog: Catalog, *, write: bool) -> Analysis
```

Juge une instruction, ou lève ce qui la refuse.

Une seule instruction, lisible, aux paramètres `$n` ; une opération que
l'action permet ; des tables déclarées et non qualifiées ; des fonctions
permises ; des droits suffisants.

**Lève :**

- `ToolError` : Ce qui est refusé, dit au modèle.

## Fonction `loom_ia.tools_builtin.sql.analysis._operation`

```python
def _operation(root: exp.Expr, *, write: bool) -> Operation
```

L'opération de l'instruction, si l'action la permet.

SELECT pour `query` (sans écriture imbriquée, INTO ni verrou) ; INSERT, UPDATE ou
DELETE pour `execute` (sans écriture imbriquée ni verrou). Toute autre instruction
est refusée.

## Fonction `loom_ia.tools_builtin.sql.analysis._target`

```python
def _target(root: exp.Expr, catalog: Catalog, operation: Operation) -> tuple[exp.Table | None, str | None]
```

La table modifiée par une écriture : son nœud et son nom déclaré, `(None, None)` pour une lecture.

Un UPDATE ou un DELETE sans WHERE est refusé, sauf `allow_full_table`.

## Fonction `loom_ia.tools_builtin.sql.analysis.is_forbidden`

```python
def is_forbidden(name: str) -> bool
```

Vrai si la fonction est interdite : nommée dans `FORBIDDEN`, ou d'une famille de `FORBIDDEN_PREFIXES`.

## Fonction `loom_ia.tools_builtin.sql.analysis._check_parameters`

```python
def _check_parameters(sql: str, given: int) -> None
```

`$1…$n` : chaque numéro cité a sa valeur, et chaque valeur sert.
