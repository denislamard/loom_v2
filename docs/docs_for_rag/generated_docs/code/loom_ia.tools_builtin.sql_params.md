# loom_ia.tools_builtin.sql_params

Module `loom_ia.tools_builtin.sql_params` de loom_v2 (fichier `src/loom_ia/tools_builtin/sql_params.py`).

Paramètres de `loom.sql` : les bases permises, et pour chacune ses tables et leurs droits.

Module léger — Pydantic seulement — : la config l'importe pour valider la
référence au chargement, sans importer l'outil, ni sqlglot, ni asyncpg.

    - python: loom.sql
      timezone: Europe/Paris
      databases:
      - name: crm
        postgres: { dsn_env: CRM_DATABASE_URL, schema: public }
        tables:
          clients:  [ select ]
          relances: [ select, insert ]     # approbations par défaut
          devis:                           # modifier sans validation : dit explicitement
            rights: [ select, update ]
            approval: { update: never }
        describe:
          devis.statut: "brouillon | envoye | relance | accepte | refuse"
      - name: stock
        sqlite: { path: data/stock.db }
        tables:
          articles: [ select ]

`tables` donne, pour chaque table (ou vue), la liste de ses droits
**accordés** ; un droit absent est refusé. Chaque droit prend son approbation
par défaut : `select` et `insert` à `never`, `update` et `delete` à
`always`. Pour en changer, la table s'écrit en objet, ``{rights: […],
approval: {droit: never | always}}``, comme les dossiers de
`loom.local_files`. `update` et `delete` exigent `select` : la base lit
les colonnes de leur `WHERE`. `DatabaseSpec` garde la forme que lit
l'outil : pour chaque table, un `TableSpec` et sa table droit → approbation.

Le DSN d'une base Postgres ne vient jamais de la config : elle nomme la
variable qui le porte (`dsn_env`), comme `api_key_env` pour un modèle.

## Constantes de `loom_ia.tools_builtin.sql_params`

- `SqlRight = Literal['select', 'insert', 'update', 'delete']` : Droit sur une table.
- `RightApproval = Literal['never', 'always']` : Approbation d'un droit : `never` ou `always`.
- `SQL_RIGHTS = ('select', 'insert', 'update', 'delete')` : Dans l'ordre où on les dit.
- `DEFAULT_APPROVAL = {'select': 'never', 'insert': 'never', 'update': 'always', 'delete': 'always'}` : Approbation par défaut : `select` et `insert` à `never`, `update` et `delete` à `always`.
- `DESTRUCTIVE = frozenset({'update', 'delete'})` : Ceux qui détruisent ce qui existe : `loom validate` signale ceux qui passent sans approbation.
- `DATABASE_NAME_PATTERN = '^[a-z0-9][a-z0-9_-]{0,31}$'` : Nom d'une base pour le modèle : minuscules, chiffres, « _ » et « - ».
- `IDENTIFIER_PATTERN = '^[A-Za-z_][A-Za-z0-9_$]{0,62}$'` : Un identifiant SQL simple : lettres, chiffres, « _ », « $ ». Une majuscule veut dire un nom créé entre guillemets (« "Devis" ») : il se compare tel quel.
- `ENV_NAME_PATTERN = '^[A-Za-z_][A-Za-z0-9_]{0,127}$'` : Nom d'une variable d'environnement.
- `DEFAULT_TIMEZONE = 'Europe/Paris'` : Fuseau des dates-heures sans `timezone`.
- `DEFAULT_STATEMENT_TIMEOUT = 10.0` : Délai d'une instruction sans réglage, en secondes.
- `DEFAULT_MAX_ROWS = 200` : Lignes rendues par une lecture sans réglage.
- `DEFAULT_MAX_AFFECTED_ROWS = 100` : Lignes qu'un `execute` peut modifier sans réglage.
- `MAX_ROWS_CEILING = 1000` : Plafond de `max_rows`.

## Classe `loom_ia.tools_builtin.sql_params.TableSpec`

```python
class TableSpec(BaseModel)
```

Une table permise, telle que l'outil la lit : sa table droit → approbation.

La config l'écrit en liste ou en `{rights, approval}` ; `DatabaseSpec`
la ramène à cette forme (`_table`), et nomme la base et la table dans ses
messages.

**Attributs :**

- `rights` : Droits accordés (`select`, `insert`, `update`, `delete`) et l'approbation de chacun.

## Fonction `loom_ia.tools_builtin.sql_params._table`

```python
def _table(value: object, *, where: str) -> object
```

Une table de la config, en entrée de `TableSpec` : `{"rights": <table interne>}`.

## Classe `loom_ia.tools_builtin.sql_params.PostgresSpec`

```python
class PostgresSpec(BaseModel)
```

Une base Postgres : la variable de son DSN, et le schéma où sont ses tables.

**Attributs :**

- `dsn_env` : Variable d'environnement qui porte le DSN ; jamais le DSN lui-même.
- `schema_name` : Schéma des tables (`schema` en config), posé en `search_path` à chaque transaction : les tables se nomment sans schéma.

## Classe `loom_ia.tools_builtin.sql_params.SqliteSpec`

```python
class SqliteSpec(BaseModel)
```

Une base SQLite : son fichier, qui doit exister (l'outil n'en crée jamais).

**Attributs :**

- `path` : Fichier de la base, qui doit exister : absolu, ou relatif au dossier du fichier de config. Ni « ~ » ni variable.

## Classe `loom_ia.tools_builtin.sql_params.DatabaseSpec`

```python
class DatabaseSpec(BaseModel)
```

Une base permise : son nom pour le modèle, sa connexion, ses tables et leurs droits.

**Attributs :**

- `name` : Nom de la base pour le modèle.
- `postgres` : Connexion Postgres ; ou bien `sqlite`.
- `sqlite` : Fichier SQLite ; ou bien `postgres`.
- `tables` : Tables (ou vues) permises et leurs droits ; un droit absent est refusé. En config, une liste de droits, ou `{rights, approval}`.
- `describe` : Ce que la description de l'outil dit d'une table (« devis ») ou d'une colonne (« devis.statut ») : le sens métier, les valeurs permises…
- `functions` : Fonctions que sqlglot ne connaît pas, permises quand même (age, clock_timestamp, jsonb_build_object, une fonction à soi…). Les autres inconnues sont refusées.
- `statement_timeout` : Délai d'une instruction, côté base, en secondes (statement_timeout, ou l'interruption SQLite) ; 300 au plus.
- `max_rows` : Lignes rendues au modèle par une lecture ; au-delà, `truncated`.
- `max_affected_rows` : Lignes qu'un `execute` peut modifier en tout ; au-delà, rien n'est enregistré.
- `allow_full_table` : UPDATE et DELETE sans WHERE : refusés, sauf si l'on veut vraiment les permettre.

### Méthode `loom_ia.tools_builtin.sql_params.DatabaseSpec._resolve_tables`

```python
@model_validator(mode='before')
@classmethod
def _resolve_tables(cls, data: object) -> object
```

Chaque table ramenée à sa forme interne, ses refus nommant la base et la table.

### Méthode `loom_ia.tools_builtin.sql_params.DatabaseSpec._check`

```python
@model_validator(mode='after')
def _check(self) -> Self
```

Vérifie la base.

`postgres` ou `sqlite`, un seul ; des tables, chacune avec un droit ;
`select` pour `update` et `delete` ; pas de tables qui ne diffèrent que
par la casse en SQLite ; `describe` sur des tables déclarées ; fonctions sans
doublon.

### Méthode `loom_ia.tools_builtin.sql_params.DatabaseSpec.dialect`

```python
@property
def dialect(self) -> Literal['postgres', 'sqlite']
```

`postgres` ou `sqlite`.

### Méthode `loom_ia.tools_builtin.sql_params.DatabaseSpec.writes`

```python
@property
def writes(self) -> bool
```

Vrai si une table au moins accorde une écriture.

## Classe `loom_ia.tools_builtin.sql_params.SqlParams`

```python
class SqlParams(BaseModel)
```

Paramètres de `loom.sql`.

**Attributs :**

- `databases` : Les bases permises, au moins une, de noms distincts.
- `timezone` : Fuseau IANA où passent les dates-heures rendues au modèle, et où se lit une date-heure sans fuseau donnée en paramètre d'une colonne `timestamptz`.

### Méthode `loom_ia.tools_builtin.sql_params.SqlParams.secret_names`

```python
def secret_names(self) -> tuple[str, ...]
```

Les variables que l'outil lit : un client peut les rediriger (`secrets`).
