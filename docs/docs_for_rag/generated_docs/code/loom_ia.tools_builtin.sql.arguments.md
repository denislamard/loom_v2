# loom_ia.tools_builtin.sql.arguments

Module `loom_ia.tools_builtin.sql.arguments` de loom_v2 (fichier `src/loom_ia/tools_builtin/sql/arguments.py`).

Ce que le modèle envoie à `sql` : l'action, la base, et le SQL avec ses paramètres.

## Constantes de `loom_ia.tools_builtin.sql.arguments`

- `NAME = 'sql'` : Le nom de l'outil.
- `MAX_STATEMENTS = 20` : Instructions au plus par appel `execute`.
- `MAX_SQL_CHARS = 20000` : Caractères au plus par instruction SQL.
- `MAX_PARAMS = 100` : Paramètres au plus par instruction.
- `Action = Literal['schema', 'query', 'execute']` : Ce que fait un appel : lire le schéma, lancer un SELECT, ou exécuter des instructions qui modifient.
- `Scalar = str | int | float | bool | None` : Une valeur de paramètre : un scalaire JSON, ou une liste de scalaires (tableau Postgres).
- `ParamValue = Scalar | list[Scalar] | dict[str, JsonValue]` : La valeur d'un paramètre `$n` : un scalaire, une liste de scalaires ou un objet JSON.

## Classe `loom_ia.tools_builtin.sql.arguments.Statement`

```python
class Statement(BaseModel)
```

Une instruction et ses paramètres ($1, $2… dans l'ordre de la liste).

## Classe `loom_ia.tools_builtin.sql.arguments.SqlArguments`

```python
class SqlArguments(BaseModel)
```

Arguments d'un appel : ceux que voit le modèle.

### Méthode `loom_ia.tools_builtin.sql.arguments.SqlArguments._fields_of_the_action`

```python
@model_validator(mode='after')
def _fields_of_the_action(self) -> SqlArguments
```

Refuse les champs qui ne servent pas à l'action, et exige ceux dont elle a besoin.

### Méthode `loom_ia.tools_builtin.sql.arguments.SqlArguments.units`

```python
def units(self) -> tuple[Statement, ...]
```

Les instructions de l'appel : celle de `query`, ou celles d'`execute`.

## Fonction `loom_ia.tools_builtin.sql.arguments.parse`

```python
def parse(arguments: Mapping[str, JsonValue]) -> SqlArguments
```

Les arguments validés.

**Lève :**

- `ToolError` : « Arguments invalides », une ligne par erreur.

## Fonction `loom_ia.tools_builtin.sql.arguments.input_schema`

```python
def input_schema() -> dict[str, JsonValue]
```

Le schéma JSON des arguments, sans les titres.

## Fonction `loom_ia.tools_builtin.sql.arguments._untitled`

```python
def _untitled(node: object) -> None
```

Retire les titres que Pydantic ajoute : ils répètent les noms, en tokens.
