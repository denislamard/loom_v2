# loom_ia.tools_builtin.pdf.chart

Module `loom_ia.tools_builtin.pdf.chart` de loom_v2 (fichier `src/loom_ia/tools_builtin/pdf/chart.py`).

Un graphique dans un PDF : dessiné par reportlab (`reportlab.graphics`), en vectoriel.

Le même graphique que dans `word` — les couleurs du style (`chart` : séries, texte,
grille), mêmes étiquettes de valeur quand il y a peu de barres, légende seulement s'il y
a plusieurs séries ou des secteurs —, dans la police du texte. Les nombres s'écrivent
dans la langue du document, comme dans les tableaux.

## Constantes de `loom_ia.tools_builtin.pdf.chart`

- `HEIGHT = 9 * cm` : Hauteur d'un graphique dans la page (9 cm).
- `AXIS_LABEL = 18` : Un libellé plus long est raccourci sous l'axe : il chevaucherait ses voisins.

## Classe `loom_ia.tools_builtin.pdf.chart._Paint`

```python
@dataclass(frozen=True, slots=True)
class _Paint
```

Les couleurs du style, en couleurs de reportlab ; la police et la langue.

### Méthode `loom_ia.tools_builtin.pdf.chart._Paint.color`

```python
def color(self, index: int) -> Any
```

La couleur de la série `index`, la palette reprise en boucle.

## Fonction `loom_ia.tools_builtin.pdf.chart._axis`

```python
def _axis(kind: ColumnType, language: Language) -> Callable[[float], str]
```

Les graduations : des nombres ronds, sans décimale inutile.

## Fonction `loom_ia.tools_builtin.pdf.chart.height`

```python
def height(chart: ChartOut) -> float
```

La hauteur d'un graphique : fixe, sauf pour des barres horizontales, qui grandit avec le nombre de libellés (de 6 à 20 cm).

## Fonction `loom_ia.tools_builtin.pdf.chart.drawing`

```python
def drawing(chart: ChartOut, width: float, look: ChartLook, font: str, language: Language) -> Drawing
```

Le graphique, à la largeur donnée, aux couleurs du style `chart`, dans la police `font` et la langue du document.

## Fonction `loom_ia.tools_builtin.pdf.chart._pie`

```python
def _pie(found: Drawing, chart: ChartOut, width: float, tall: float, paint: _Paint) -> None
```

Dessine un camembert, chaque part étiquetée de son pourcentage.

## Fonction `loom_ia.tools_builtin.pdf.chart._legend`

```python
def _legend(found: Drawing, names: Sequence[str], x: float, y: float, anchor: str, paint: _Paint, *, columns: int | None=None) -> None
```

Dessine la légende des séries.
