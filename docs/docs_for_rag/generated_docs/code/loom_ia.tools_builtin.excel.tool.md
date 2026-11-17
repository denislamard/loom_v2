# loom_ia.tools_builtin.excel.tool

Module `loom_ia.tools_builtin.excel.tool` de loom_v2 (fichier `src/loom_ia/tools_builtin/excel/tool.py`).

L'outil `excel` monté : ses dossiers, son fuseau, son recalcul ; et sa fabrique.

## Constantes de `loom_ia.tools_builtin.excel.tool`

- `CHART_HELP = (f"Graphiques d'un onglet ('charts', liste) : {{kind (bar, horizontal_bar, line, pie), title, categories : en-tête de l…` : Mode d'emploi des graphiques, ajouté à la description quand `charts` est vrai.

## Classe `loom_ia.tools_builtin.excel.tool.ExcelTool`

```python
class ExcelTool
```

L'outil `excel` monté avec ses dossiers, son fuseau et, s'il y a lieu, son recalcul.

### Méthode `loom_ia.tools_builtin.excel.tool.ExcelTool.__init__`

```python
def __init__(self, space: Folders, zone: ZoneInfo, recalc: Recalc | None, unsafe: str | None=None, *, charts: bool=False) -> None
```

Prépare l'outil : dossiers, fuseau, recalcul, graphiques ; ses arguments et sa description en découlent.

**Arguments :**

- `space` : Les dossiers permis.
- `zone` : Le fuseau des dates-heures.
- `recalc` : Le recalcul par LibreOffice ; `None` : aucun.
- `unsafe` : Pourquoi la lecture n'est pas sûre (lxml trop ancien), s'il y a lieu.
- `charts` : Proposer les graphiques.

### Méthode `loom_ia.tools_builtin.excel.tool.ExcelTool.spec`

```python
@property
def spec(self) -> ToolSpec
```

La définition de l'outil.

### Méthode `loom_ia.tools_builtin.excel.tool.ExcelTool.settings`

```python
def settings(self) -> Settings
```

Les dossiers, puis le fuseau, le recalcul et la lecture, pour `loom validate`.

### Méthode `loom_ia.tools_builtin.excel.tool.ExcelTool.assess_call`

```python
def assess_call(self, arguments: Mapping[str, JsonValue]) -> CallAssessment
```

Approbation et effets de cet appel : les droits qu'il exerce, dossier par dossier.

### Méthode `loom_ia.tools_builtin.excel.tool.ExcelTool._assess_read`

```python
def _assess_read(self, request: ReadArguments) -> CallAssessment
```

Une lecture : aucun effet ; l'approbation du droit read, si le dossier la demande.

### Méthode `loom_ia.tools_builtin.excel.tool.ExcelTool.invoke`

```python
async def invoke(self, arguments: dict[str, JsonValue], context: ToolContext) -> ToolOutput
```

Lit un classeur (`read`), ou en écrit un (`write`), hors de la boucle asyncio.

**Lève :**

- `ToolError` : Arguments, chemin, droit ou classeur refusés.

### Méthode `loom_ia.tools_builtin.excel.tool.ExcelTool._workbook`

```python
def _workbook(self, path: str) -> Located
```

Le classeur à lire : .xlsx ou .xlsm, dans un dossier où `read` est accordé.

### Méthode `loom_ia.tools_builtin.excel.tool.ExcelTool._perform`

```python
def _perform(self, request: ExcelArguments, located: Located, sources: Mapping[str, Located]) -> dict[str, JsonValue]
```

Écrit le classeur.

Données chargées, description validée, onglets écrits, recalcul s'il est demandé
; rend le chemin, les onglets et les avertissements.

### Méthode `loom_ia.tools_builtin.excel.tool.ExcelTool._perform_read`

```python
def _perform_read(self, request: ReadArguments, source: Located) -> dict[str, JsonValue]
```

Lit un classeur : sans onglet, la liste des onglets et leur aperçu ; avec un onglet, les cellules d'une plage.

## Fonction `loom_ia.tools_builtin.excel.tool._description`

```python
def _description(space: Folders, zone: ZoneInfo, recalc: bool, unsafe: str | None, *, charts: bool=False) -> str
```

La description de l'outil : ce qu'il écrit et lit, les dossiers et leurs droits, le fuseau, le recalcul et les graphiques.

## Fonction `loom_ia.tools_builtin.excel.tool.build`

```python
def build(params: BaseModel, context: BuiltinContext) -> ExcelTool
```

La fabrique de `loom.excel` : les dossiers comme `local_files`, puis LibreOffice.
