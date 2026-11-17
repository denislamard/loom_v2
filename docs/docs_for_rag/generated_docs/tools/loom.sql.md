# Outil fourni loom.sql

L'outil `loom.sql` est fourni avec loom_v2. Un agent l'active dans son `agent.yaml` : `- python: loom.sql` dans `tools`.

## Ce que fait loom.sql

Outil sql : lire et modifier des bases SQL permises, avec le SQL qu'écrit le modèle.

Le modèle écrit ses requêtes ; l'outil les **analyse** (`analysis`, sqlglot)
avant tout envoi — une instruction, une lecture pour `query`, des
INSERT/UPDATE/DELETE pour `execute`, seulement les tables et les droits que
la config déclare —, puis les fait exécuter par une base dont les **droits
font foi** : le rôle Postgres (`postgres`), l'authorizer SQLite (`sqlite`).
L'analyse décide aussi de l'approbation de chaque appel (`assess_call`),
sans toucher à la base. Jamais de DDL.

Trois actions : `schema` (le DDL des tables permises, avec leurs droits),
`query` (un SELECT), `execute` (jusqu'à 20 écritures en une transaction).

sqlglot et asyncpg viennent de l'extra `sql` ; ce paquet n'est importé
qu'au montage d'un agent qui active l'outil, et asyncpg seulement pour une
base Postgres. Ses paramètres sont dans `sql_params`, module léger que la
config importe seul.

## Comment loom.sql est monté

- cible : `loom_ia.tools_builtin.sql:build`
- extra de loom-ia à installer : `loom-ia[sql]`
- réglages communs refusés sur sa référence : `approval`, `side_effects`, `idempotent`, car les droits de chaque table les expriment (tables), et l'approbation se règle par table (tables.<table>.approval)
- l'outil tire sa description de ses réglages : `description` est refusé, `skill` ajoute du texte

## Les réglages de loom.sql dans agent.yaml

Ce sont des clés de sa référence dans `tools`, à côté de `python: loom.sql`.

### `tools[]` (référence `python: loom.sql`)

Paramètres de `loom.sql`.

Modèle Pydantic : `loom_ia.tools_builtin.sql_params.SqlParams`.

- `tools[].databases` (type `tuple[DatabaseSpec, ...]` ; obligatoire) : Les bases permises, au moins une, de noms distincts.
- `tools[].timezone` (type `str` ; défaut : `"Europe/Paris"`) : Fuseau IANA où passent les dates-heures rendues au modèle, et où se lit une date-heure sans fuseau donnée en paramètre d'une colonne `timestamptz`.

### `tools[].databases[]`

Une base permise : son nom pour le modèle, sa connexion, ses tables et leurs droits.

Modèle Pydantic : `loom_ia.tools_builtin.sql_params.DatabaseSpec`.

- `tools[].databases[].name` (type `str` ; obligatoire ; bornes : motif `^[a-z0-9][a-z0-9_-]{0,31}$`) : Nom de la base pour le modèle.
- `tools[].databases[].postgres` (type `PostgresSpec | null` ; défaut : `null`) : Connexion Postgres ; ou bien `sqlite`.
- `tools[].databases[].sqlite` (type `SqliteSpec | null` ; défaut : `null`) : Fichier SQLite ; ou bien `postgres`.
- `tools[].databases[].tables` (type `dict[str, TableSpec]` ; obligatoire) : Tables (ou vues) permises et leurs droits ; un droit absent est refusé. En config, une liste de droits, ou `{rights, approval}`.
- `tools[].databases[].describe` (type `dict[str, str]` ; défaut : `{}`) : Ce que la description de l'outil dit d'une table (« devis ») ou d'une colonne (« devis.statut ») : le sens métier, les valeurs permises…
- `tools[].databases[].functions` (type `tuple[str, ...]` ; défaut : `[]`) : Fonctions que sqlglot ne connaît pas, permises quand même (age, clock_timestamp, jsonb_build_object, une fonction à soi…). Les autres inconnues sont refusées.
- `tools[].databases[].statement_timeout` (type `float` ; défaut : `10.0` ; bornes : > 0, ≤ 300) : Délai d'une instruction, côté base, en secondes (statement_timeout, ou l'interruption SQLite) ; 300 au plus.
- `tools[].databases[].max_rows` (type `int` ; défaut : `200` ; bornes : ≥ 1, ≤ 1000) : Lignes rendues au modèle par une lecture ; au-delà, `truncated`.
- `tools[].databases[].max_affected_rows` (type `int` ; défaut : `100` ; bornes : ≥ 1, ≤ 100000) : Lignes qu'un `execute` peut modifier en tout ; au-delà, rien n'est enregistré.
- `tools[].databases[].allow_full_table` (type `bool` ; défaut : `false`) : UPDATE et DELETE sans WHERE : refusés, sauf si l'on veut vraiment les permettre.

### `tools[].databases[].postgres`

Une base Postgres : la variable de son DSN, et le schéma où sont ses tables.

Modèle Pydantic : `loom_ia.tools_builtin.sql_params.PostgresSpec`.

- `tools[].databases[].postgres.dsn_env` (type `str` ; obligatoire ; bornes : motif `^[A-Za-z_][A-Za-z0-9_]{0,127}$`) : Variable d'environnement qui porte le DSN ; jamais le DSN lui-même.
- `tools[].databases[].postgres.schema` (type `str` ; défaut : `"public"` ; bornes : motif `^[A-Za-z_][A-Za-z0-9_$]{0,62}$`) : Schéma des tables (`schema` en config), posé en `search_path` à chaque transaction : les tables se nomment sans schéma.

### `tools[].databases[].sqlite`

Une base SQLite : son fichier, qui doit exister (l'outil n'en crée jamais).

Modèle Pydantic : `loom_ia.tools_builtin.sql_params.SqliteSpec`.

- `tools[].databases[].sqlite.path` (type `Path` ; obligatoire) : Fichier de la base, qui doit exister : absolu, ou relatif au dossier du fichier de config. Ni « ~ » ni variable.

### `tools[].databases[].tables.<nom>`

Une table permise, telle que l'outil la lit : sa table droit → approbation.

Modèle Pydantic : `loom_ia.tools_builtin.sql_params.TableSpec`.

- `tools[].databases[].tables.<nom>.rights` (type `dict[SqlRight, RightApproval]` ; obligatoire) : Droits accordés (`select`, `insert`, `update`, `delete`) et l'approbation de chacun.

## Les arguments que le modèle donne à loom.sql

- `action` (`"schema" | "query" | "execute"` ; obligatoire) : schema : structure des tables ; query : une lecture (SELECT) ; execute : des écritures (INSERT, UPDATE, DELETE) dans une même transaction.
- `database` (`string` ; obligatoire) : Nom de la base, parmi celles décrites.
- `sql` (`string | null`) : Pour query : le SELECT.
- `params` (`array[objet | array[objet] | object] | null`) : Pour query : valeurs de $1, $2…
- `statements` (`array[Statement] | null`) : Pour execute : les instructions, appliquées ensemble ou pas du tout.
- `tables` (`array[string] | null`) : Pour schema : les tables voulues ; toutes si absent.
