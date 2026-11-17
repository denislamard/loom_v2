# loom_ia.tools_builtin.excel.charts

Module `loom_ia.tools_builtin.excel.charts` de loom_v2 (fichier `src/loom_ia/tools_builtin/excel/charts.py`).

Les graphiques d'un onglet : validés avec la description du classeur, puis dessinés.

Un graphique cite les colonnes de son onglet par leur en-tête : ses libellés
(`categories`) et ses séries (`values`), des colonnes de nombres d'un même
type. Il trace les lignes de données, depuis la ligne 2 : ni la ligne de
`totals`, ni les lignes `total` ou `note` de la fin. Les fautes sont dites
avec celles du reste du classeur.

Les bornes sont celles des documents (`word`, `pdf`), sauf pour une courbe,
qui se lit encore avec beaucoup de points.

Un graphique est celui d'Excel, **lié aux cellules** de son onglet : ses
libellés et ses séries sont des plages (`'Ventes'!$A$2:$A$7`), pas des
nombres recopiés. Changer une cellule change le graphique, et « Sélectionner
les données » montre ces plages. Une colonne de formules se trace comme une
autre : Excel la calcule à l'ouverture.

Le style est celui des graphiques de `word` et `pdf` (`_chart_style`),
appliqué par l'API d'openpyxl. Le graphique se place à droite des données,
depuis la ligne 2 ; plusieurs graphiques s'empilent.

## Constantes de `loom_ia.tools_builtin.excel.charts`

- `MAX_POINTS = 1000` : Une courbe se lit encore avec beaucoup de points (une année au jour le jour) ; des barres ou des secteurs, non.
- `MAX_CHARTS = 10` : Graphiques au plus par onglet.
- `KIND_SAID = {'bar': 'des barres', 'horizontal_bar': 'des barres', 'line': 'une courbe', 'pie': 'des secteurs'}` : Ce que trace chaque type de graphique, pour les messages.
- `NUMBERED = NUMERIC | frozenset[ColumnType]({'date', 'datetime'})` : Des libellés que l'axe garde en nombres (et dont il garde le format) ; les autres sont du texte.
- `WIDTH = 16` : Taille d'un graphique, en centimètres ; une ligne d'Excel fait 0,53 cm (15 points).
- `HEIGHT = 9` : Hauteur d'un graphique, en centimètres (voir `WIDTH`).
- `ROW_CM = 0.53` : Hauteur d'une ligne d'Excel, en centimètres.
- `Drawn = BarChart | LineChart | PieChart` : Les graphiques que l'outil dessine ; ceux qui ont des axes.
- `Axed = BarChart | LineChart` : Les graphiques qui ont des axes.
- `NOT_DATA = frozenset({'total', 'note'})` : Des lignes qui ne sont pas des données : elles ne font pas partie du graphique.

## Classe `loom_ia.tools_builtin.excel.charts.Plotted`

```python
@dataclass(frozen=True, slots=True)
class Plotted
```

Un graphique validé : ses colonnes (à partir de 1) et ce que l'on sait de ses valeurs.

**Attributs :**

- `spec` : Le graphique tel que le modèle l'a décrit.
- `category` : Colonne des libellés, à partir de 1.
- `values` : Colonnes des valeurs, à partir de 1.
- `value_type` : Type des valeurs.
- `category_type` : Type des libellés.
- `last` : Les lignes de données tracées : de la ligne 2 à `last`.
- `known` : Les valeurs écrites en clair (sans formule) : bornes de l'axe, contrôle des secteurs.
- `complete` : Toutes les valeurs sont connues (aucune formule) ?

## Fonction `loom_ia.tools_builtin.excel.charts.data_rows`

```python
def data_rows(rows: Sequence[RowSpec]) -> int
```

Combien de lignes, depuis la ligne 2, sont des données : avant tout total ou note.

## Fonction `loom_ia.tools_builtin.excel.charts.check`

```python
def check(sheet: SheetSpec, rows: Sequence[RowSpec], problems: Problems) -> list[Plotted]
```

Les graphiques de l'onglet, validés ; chaque faute est dite à `problems`.

## Fonction `loom_ia.tools_builtin.excel.charts._check_one`

```python
def _check_one(spec: ChartSpec, sheet: SheetSpec, headers: list[str], rows: Sequence[RowSpec], where: str, problems: Problems) -> Plotted | None
```

Un graphique validé : colonnes connues, libellés et valeurs du bon type, lignes de données ; `None`, avec une erreur, sinon.

## Fonction `loom_ia.tools_builtin.excel.charts._literal`

```python
def _literal(given: object) -> float | None
```

La valeur d'une cellule écrite en clair ; `None` pour une formule, un texte, un vide.

## Fonction `loom_ia.tools_builtin.excel.charts.draw`

```python
def draw(ws: Worksheet, sheet: SheetSpec, plotted: Sequence[Plotted]) -> None
```

Les graphiques, à droite des données, empilés depuis la ligne 2.

## Fonction `loom_ia.tools_builtin.excel.charts._chart`

```python
def _chart(ws: Worksheet, found: Plotted) -> Drawn
```

Le graphique openpyxl d'un graphique validé : type, données liées aux cellules de l'onglet, couleurs et axes.

## Fonction `loom_ia.tools_builtin.excel.charts._height`

```python
def _height(spec: ChartSpec, count: int) -> int
```

Des barres horizontales prennent la place de leurs libellés, comme dans Word.

## Fonction `loom_ia.tools_builtin.excel.charts._pie`

```python
def _pie(chart: PieChart, count: int) -> None
```

Colore chaque part d'un camembert, la palette reprise en boucle, un trait blanc entre elles.

## Fonction `loom_ia.tools_builtin.excel.charts._axes`

```python
def _axes(chart: Axed, found: Plotted) -> None
```

Règle les axes d'un graphique à barres ou d'une courbe : visibles, format des nombres, grille, sens des barres horizontales.

## Fonction `loom_ia.tools_builtin.excel.charts._top`

```python
def _top(found: Plotted) -> float | None
```

Le bout de l'axe quand une étiquette doit tenir au bout des barres, comme dans Word.

Seulement si toutes les valeurs sont connues : une formule n'a sa valeur
qu'à l'ouverture, Excel choisit alors l'axe.
