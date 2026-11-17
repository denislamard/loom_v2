# loom_ia.tools_builtin.excel.arguments

Module `loom_ia.tools_builtin.excel.arguments` de loom_v2 (fichier `src/loom_ia/tools_builtin/excel/arguments.py`).

Ce que voit le modèle : les arguments de `excel`, leur vocabulaire, les bornes d'un appel.

Le modèle décrit ce que **sont** les données — types de colonne, rôles des
lignes et des cellules, formules —, jamais leur apparence. Pydantic
seulement : ni openpyxl, ni disque.

Les modèles d'un onglet n'ont pas de docstring : elle partirait dans le
schéma, envoyé au modèle à chaque appel. Ils sont décrits ici :

- `ColumnSpec` : une colonne, son en-tête (`header`) et son type de
  valeur (`type`, `text` par défaut).
- `RowSpec` : une ligne donnée dans l'appel. `cells` : ses valeurs dans
  l'ordre des colonnes, « =… » pour une formule, ou une `CellSpec` qui porte
  son propre rôle ou un commentaire ; `role` : le rôle de la ligne
  (`Role`), qui donne sa couleur ; `note` : son commentaire.
- `DataSource` : un fichier de données qui remplit l'onglet à la place de
  `rows` (.csv, .json ou .xlsx) ; `sheet` : l'onglet lu dans un .xlsx.
- `SheetSpec` : un onglet. `name`, `columns`, puis ses lignes, données
  (`rows`) ou lues (`data`), l'un ou l'autre ; `totals` : en-tête →
  agrégat, calculé par une formule sous la colonne.
- `ChartedSheetSpec` : le même onglet avec `charts`, ses graphiques
  (`ChartSpec`) ; c'est celui que voit un outil monté avec `charts`.

## Constantes de `loom_ia.tools_builtin.excel.arguments`

- `NAME = 'excel'` : Nom de l'outil.
- `MAX_SHEETS = 50` : Onglets au plus dans un classeur écrit.
- `MAX_COLUMNS = 200` : Colonnes au plus dans un onglet écrit.
- `MAX_INLINE_ROWS = 200` : Lignes au plus données dans l'appel (`rows`).
- `MAX_DATA_ROWS = 100000` : Lignes au plus chargées d'un fichier de données.
- `MAX_SOURCE_BYTES = 20000000` : Taille au plus d'un fichier lu, en octets.
- `MAX_OUTPUT_BYTES = 50000000` : Taille au plus d'un classeur écrit, en octets.
- `MAX_WARNINGS = 20` : Avertissements rendus au plus.
- `MAX_ERRORS_SAID = 10` : Erreurs citées au plus dans un refus.
- `READ_ROWS = 200` : Lecture : lignes rendues par appel, par défaut et au plus.
- `MAX_READ_ROWS = 1000` : Lignes rendues au plus par une lecture.
- `MAX_SHEET_ROWS = 1048576` : Excel : 1 048 576 lignes, 16 384 colonnes (XFD).
- `MAX_SHEET_COLUMNS = 16384` : Colonnes d'une feuille Excel (XFD).
- `Role = Literal['input', 'formula', 'assumption', 'total', 'note']` : Rôle d'une colonne : saisie, formule, hypothèse, total ou note ; il donne sa couleur.
- `Action = Literal['write', 'read']` : Action de l'outil : écrire ou lire.

## Classe `loom_ia.tools_builtin.excel.arguments._Model`

```python
class _Model(BaseModel)
```

La base des modèles d'arguments : figés, sans champ inconnu.

Sa docstring ne part pas dans le schéma : Pydantic ne la transmet pas aux sous-classes.

## Classe `loom_ia.tools_builtin.excel.arguments.ColumnSpec`

```python
class ColumnSpec(_Model)
```

(sans docstring)

## Classe `loom_ia.tools_builtin.excel.arguments.CellSpec`

```python
class CellSpec(_Model)
```

Une cellule qui porte son propre rôle ou un commentaire.

## Classe `loom_ia.tools_builtin.excel.arguments.RowSpec`

```python
class RowSpec(_Model)
```

(sans docstring)

## Classe `loom_ia.tools_builtin.excel.arguments.DataSource`

```python
class DataSource(_Model)
```

(sans docstring)

## Classe `loom_ia.tools_builtin.excel.arguments.SheetSpec`

```python
class SheetSpec(_Model)
```

(sans docstring)

### Méthode `loom_ia.tools_builtin.excel.arguments.SheetSpec.chart_specs`

```python
def chart_specs(self) -> Sequence[ChartSpec]
```

Les graphiques de l'onglet : aucun, sans `charts` dans la config de l'outil.

## Classe `loom_ia.tools_builtin.excel.arguments.ChartSpec`

```python
class ChartSpec(_Model)
```

Un graphique tiré des colonnes de son onglet : libellés et séries, par en-tête.

## Classe `loom_ia.tools_builtin.excel.arguments.ChartedSheetSpec`

```python
class ChartedSheetSpec(SheetSpec)
```

(sans docstring)

### Méthode `loom_ia.tools_builtin.excel.arguments.ChartedSheetSpec.chart_specs`

```python
def chart_specs(self) -> Sequence[ChartSpec]
```

Les graphiques de l'onglet ; aucun s'il n'en déclare pas.

## Classe `loom_ia.tools_builtin.excel.arguments.ExcelArguments`

```python
class ExcelArguments(_Model)
```

Une écriture : le classeur et ses onglets.

## Classe `loom_ia.tools_builtin.excel.arguments.ReadArguments`

```python
class ReadArguments(_Model)
```

Une lecture : sans onglet, la liste des onglets ; avec, ses cellules.

## Classe `loom_ia.tools_builtin.excel.arguments.ExcelCall`

```python
class ExcelCall(_Model)
```

Ce que voit le modèle : une écriture (par défaut) ou une lecture, à plat.

### Méthode `loom_ia.tools_builtin.excel.arguments.ExcelCall.request`

```python
def request(self) -> ExcelArguments | ReadArguments
```

La demande que l'appel porte : écrire un classeur (`ExcelArguments`) ou en lire un (`ReadArguments`).

## Classe `loom_ia.tools_builtin.excel.arguments.ExcelChartCall`

```python
class ExcelChartCall(ExcelCall)
```

Ce que voit le modèle : une écriture (par défaut) ou une lecture, à plat.

## Fonction `loom_ia.tools_builtin.excel.arguments.parse`

```python
def parse(arguments: Mapping[str, JsonValue], model: type[ExcelCall]=ExcelCall) -> ExcelArguments | ReadArguments
```

Les arguments validés ; `ToolError` qui dit chaque champ fautif (10 au plus).

## Fonction `loom_ia.tools_builtin.excel.arguments.input_schema`

```python
def input_schema(model: type[ExcelCall]=ExcelCall) -> dict[str, JsonValue]
```

Le schéma des arguments, sans les titres que Pydantic ajoute à chaque champ.

## Fonction `loom_ia.tools_builtin.excel.arguments._untitled`

```python
def _untitled(node: object, *, names: bool=False) -> object
```

`names` : `node` est une table de propriétés, dont les clés sont des noms.

## Classe `loom_ia.tools_builtin.excel.arguments.Problems`

```python
@dataclass(slots=True)
class Problems
```

Les fautes d'un passage de validation, dites ensemble ; et les avertissements.

### Méthode `loom_ia.tools_builtin.excel.arguments.Problems.error`

```python
def error(self, said: str) -> None
```

Note une erreur : le classeur sera refusé.

### Méthode `loom_ia.tools_builtin.excel.arguments.Problems.warn`

```python
def warn(self, said: str) -> None
```

Note un avertissement, une fois, `MAX_WARNINGS` au plus.

### Méthode `loom_ia.tools_builtin.excel.arguments.Problems.raise_if_any`

```python
def raise_if_any(self) -> None
```

Lève `ToolError` s'il y a des erreurs : « Classeur refusé », puis les `MAX_ERRORS_SAID` premières.
