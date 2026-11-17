# loom_ia.tools_builtin.word.chart

Module `loom_ia.tools_builtin.word.chart` de loom_v2 (fichier `src/loom_ia/tools_builtin/word/chart.py`).

Un graphique Word natif : la partie DrawingML (`word/charts/chartN.xml`) et son ancre.

Le graphique est celui de Word lui-même, pas une image : net à toute taille,
retouchable (couleurs, type, titre) dans Word, et ses nombres suivent la langue
du lecteur. Ses données sont écrites dans le cache de la partie (`numCache`,
`strCache`), sans classeur embarqué : Word et LibreOffice l'affichent tel quel,
mais « Modifier les données » ne trouve pas de classeur.

Les couleurs viennent du style (réglage `chart` : les séries, le texte, la grille, le
titre), la police de `text` ; le reste est la règle commune (`_chart_style`) : des
étiquettes de valeur quand il y a peu de barres, la légende seulement quand il y
a plusieurs séries (ou des secteurs). L'ordre des éléments suit le schéma
DrawingML, que Word applique strictement.

## Constantes de `loom_ia.tools_builtin.word.chart`

- `PAPER = 'FFFFFF'` : Le blanc du papier : le trait entre deux secteurs.
- `CM = 360000` : Mesures en EMU : 360 000 par centimètre.
- `WIDTH = 17 * CM` : Largeur d'un graphique dans la page, en EMU (17 cm).
- `HEIGHT = 9 * CM` : Hauteur d'un graphique dans la page, en EMU (9 cm).

## Fonction `loom_ia.tools_builtin.word.chart.height`

```python
def height(chart: ChartOut) -> int
```

La hauteur du graphique : des barres horizontales prennent la place de leurs libellés.

## Fonction `loom_ia.tools_builtin.word.chart.anchor`

```python
def anchor(relation: str, number: int, chart: ChartOut, drawing: int | None=None, width: int | None=None) -> str
```

Le paragraphe qui porte le graphique, à la largeur du texte.

`drawing` : l'identifiant du dessin, unique dans le document (images comprises) ;
sans image, le numéro du graphique. `width` : la place disponible, en EMU, quand
elle est moindre que la page (une colonne) : le graphique s'y réduit, dans ses
proportions.

## Classe `loom_ia.tools_builtin.word.chart.Paint`

```python
@dataclass(frozen=True, slots=True)
class Paint
```

Ce que le style donne au graphique : ses couleurs, la police du texte, la langue.

### Méthode `loom_ia.tools_builtin.word.chart.Paint.series`

```python
def series(self, index: int) -> str
```

La couleur de la série `index`, la palette reprise en boucle.

## Fonction `loom_ia.tools_builtin.word.chart._column`

```python
def _column(index: int) -> str
```

La lettre de colonne d'une série dans la feuille de référence  (B, C, D…).

## Fonction `loom_ia.tools_builtin.word.chart._labels`

```python
def _labels(paint: Paint, code: str, *, percent: bool=False, position: str | None=None) -> str
```

Étiquettes de valeur (ou de pourcentage, pour des secteurs).

## Fonction `loom_ia.tools_builtin.word.chart._labelled`

```python
def _labelled(chart: ChartOut) -> bool
```

Une valeur sur chaque barre : une seule série, et pas trop de barres.

## Fonction `loom_ia.tools_builtin.word.chart._scale`

```python
def _scale(chart: ChartOut) -> str
```

Les bornes de l'axe des valeurs, quand une étiquette doit tenir au bout des barres.

Sans elles, Word arrête l'axe juste après la plus grande barre, et son
étiquette déborde du graphique. Le bout de l'axe reste un nombre rond.

## Fonction `loom_ia.tools_builtin.word.chart._axes`

```python
def _axes(chart: ChartOut, paint: Paint) -> str
```

Les deux axes d'un graphique à barres ou d'une courbe, en XML : libellés, valeurs et leur format, grille.

## Fonction `loom_ia.tools_builtin.word.chart._plot`

```python
def _plot(chart: ChartOut, paint: Paint) -> str
```

Le tracé d'un graphique en XML : camembert, barres (verticales ou horizontales) ou courbe, avec ses couleurs et ses étiquettes.

## Fonction `loom_ia.tools_builtin.word.chart.part`

```python
def part(chart: ChartOut, look: ChartLook, font: str, language: str='fr-FR') -> str
```

La partie `chartN.xml` du graphique, aux couleurs et dans la police du style.
