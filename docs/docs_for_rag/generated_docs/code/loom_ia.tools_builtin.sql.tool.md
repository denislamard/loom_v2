# loom_ia.tools_builtin.sql.tool

Module `loom_ia.tools_builtin.sql.tool` de loom_v2 (fichier `src/loom_ia/tools_builtin/sql/tool.py`).

L'outil `sql` monté : ses bases, leurs droits ; et sa fabrique.

## Classe `loom_ia.tools_builtin.sql.tool.Backend`

```python
class Backend(Protocol)
```

Ce que l'outil demande à une base, Postgres ou SQLite.

### Méthode `loom_ia.tools_builtin.sql.tool.Backend.where`

```python
def where(self) -> Place
```

Où est la base, pour `loom validate` : son type, son fichier ou sa variable.

### Méthode `loom_ia.tools_builtin.sql.tool.Backend.query`

```python
async def query(self, unit: Statement, analysis: Analysis) -> dict[str, JsonValue]
```

Lance un SELECT ; rend ses colonnes et ses lignes.

### Méthode `loom_ia.tools_builtin.sql.tool.Backend.execute`

```python
async def execute(self, units: list[tuple[Statement, Analysis]]) -> dict[str, JsonValue]
```

Exécute des écritures, ensemble ou pas du tout.

### Méthode `loom_ia.tools_builtin.sql.tool.Backend.introspect`

```python
async def introspect(self) -> list[TableInfo]
```

Les tables et vues déclarées, lues dans la base.

### Méthode `loom_ia.tools_builtin.sql.tool.Backend.aclose`

```python
async def aclose(self) -> None
```

Libère les connexions.

## Classe `loom_ia.tools_builtin.sql.tool.Database`

```python
@dataclass(frozen=True, slots=True)
class Database
```

Une base montée : sa déclaration, ses droits pour l'analyse, sa connexion, son schéma.

### Méthode `loom_ia.tools_builtin.sql.tool.Database.name`

```python
@property
def name(self) -> str
```

Le nom de la base.

### Méthode `loom_ia.tools_builtin.sql.tool.Database.asking`

```python
def asking(self, table: str) -> list[SqlRight]
```

Les droits de la table qui demandent une approbation à chaque appel.

### Méthode `loom_ia.tools_builtin.sql.tool.Database.granted`

```python
def granted(self, table: str) -> str
```

« select, update (approbation) ».

## Classe `loom_ia.tools_builtin.sql.tool.SqlTool`

```python
class SqlTool
```

L'outil `sql` monté avec ses bases.

### Méthode `loom_ia.tools_builtin.sql.tool.SqlTool.__init__`

```python
def __init__(self, databases: Sequence[Database], zone: ZoneInfo) -> None
```

Prépare l'outil : les bases, leurs droits, et la définition qui en découle (effets, approbation, conduite après un résultat inconnu).

**Arguments :**

- `databases` : Les bases montées.
- `zone` : Le fuseau des dates-heures sans fuseau.

**Lève :**

- `ValueError` : Aucune base.

### Méthode `loom_ia.tools_builtin.sql.tool.SqlTool.spec`

```python
@property
def spec(self) -> ToolSpec
```

La définition de l'outil.

### Méthode `loom_ia.tools_builtin.sql.tool.SqlTool.settings`

```python
def settings(self) -> Settings
```

Pour `loom validate` : chaque base, ses droits table par table, ses bornes ; le fuseau.

Un droit refusé n'est pas listé. `granted()`, que lit le modèle, reste à part.

### Méthode `loom_ia.tools_builtin.sql.tool.SqlTool.assess_call`

```python
def assess_call(self, arguments: Mapping[str, JsonValue]) -> CallAssessment
```

Approbation et effets de cet appel : les droits qu'il exerce, table par table.

### Méthode `loom_ia.tools_builtin.sql.tool.SqlTool.invoke`

```python
async def invoke(self, arguments: dict[str, JsonValue], context: ToolContext) -> ToolOutput
```

Lit le schéma, lance une requête ou exécute des écritures ; une erreur de la base reçoit, s'il y a lieu, une indication sur le schéma.

**Lève :**

- `ToolError` : Arguments, instruction ou base refusés.

### Méthode `loom_ia.tools_builtin.sql.tool.SqlTool.aclose`

```python
async def aclose(self) -> None
```

Ferme les connexions des bases.

### Méthode `loom_ia.tools_builtin.sql.tool.SqlTool._analyses`

```python
def _analyses(self, request: SqlArguments, database: Database) -> list[Analysis]
```

L'analyse de chaque instruction de l'appel ; aucune pour `schema`.

### Méthode `loom_ia.tools_builtin.sql.tool.SqlTool._schema`

```python
async def _schema(self, database: Database, wanted: Sequence[str] | None) -> str
```

Le schéma des tables voulues (toutes si aucune), rendu pour le modèle.

### Méthode `loom_ia.tools_builtin.sql.tool.SqlTool._hint`

```python
async def _hint(self, database: Database, message: str, analyses: list[Analysis]) -> str | None
```

Les colonnes proches d'une colonne inconnue, d'après le schéma (lu au besoin).

## Fonction `loom_ia.tools_builtin.sql.tool._description`

```python
def _description(databases: Sequence[Database], zone: ZoneInfo) -> str
```

La description de l'outil : les bases, leurs tables, droits, limites et descriptions, les actions et les règles du SQL.

## Fonction `loom_ia.tools_builtin.sql.tool.build`

```python
def build(params: BaseModel, context: BuiltinContext) -> SqlTool
```

La fabrique de `loom.sql` : vérifie les bases sans s'y connecter.

## Fonction `loom_ia.tools_builtin.sql.tool._dsn`

```python
def _dsn(spec: DatabaseSpec, secrets: Mapping[str, str], context: BuiltinContext) -> str
```

Le DSN d'une base Postgres, lu dans les secrets.

**Lève :**

- `ValueError` : Variable absente ou vide, ou DSN du stockage de loom (le modèle pourrait y réécrire son journal).
