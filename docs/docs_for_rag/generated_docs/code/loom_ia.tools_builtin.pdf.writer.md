# loom_ia.tools_builtin.pdf.writer

Module `loom_ia.tools_builtin.pdf.writer` de loom_v2 (fichier `src/loom_ia/tools_builtin/pdf/writer.py`).

Écrire un PDF : le document validé, posé dans son style, avec reportlab.

Le même style que `word` — `default.yaml`, la charte, le style du modèle — et la
même mise en page des sections : côte à côte, encadrées, en colonnes, en paysage, page
de garde. Le code n'a aucune valeur visuelle : polices, tailles, couleurs, filets et
espacements viennent du style résolu (`Style`) ; il n'ajoute que la mécanique (un
paragraphe encadré est une cellule de tableau, un en-tête est dessiné à chaque page).

**Les polices.** Le PDF a trois familles standard, qui ne s'embarquent pas : une police
du style s'y rend par son équivalent standard (`PDF_FONTS`, `PDF_FALLBACK`). Une
police dont le style donne les fichiers TrueType (`fonts`) est embarquée, en
sous-ensemble. Un caractère qu'une police n'a pas s'imprimerait en carré noir : le
résultat le signale.

**Ce que le PDF fait mieux que Word** : le sommaire a ses vrais numéros de page dès
l'écriture, et « Page n / N » aussi, par des rendus successifs (le premier compte les
pages et repère les titres, le suivant les écrit).

Deux documents identiques donnent le même fichier, octet pour octet : le mode
`invariant` de reportlab fixe la date et l'identifiant du PDF.

## Constantes de `loom_ia.tools_builtin.pdf.writer`

- `_split = cast(Callable[[str, str, float, float], list[str]], reportlab_utils.simpleSplit)` : Le texte coupé à une largeur, ligne par ligne, dans une police et une taille.
- `MAX_MISSING_SAID = 10` : Caractères absents d'une police cités au plus dans l'avertissement.
- `TWIP = 1 / 20` : Un twip (vingtième de point) en points : les mesures du style résolu.
- `LEADING = 1.2` : L'interligne simple, en part de la taille du texte.
- `BAND_GAP = 6.0` : L'écart entre l'en-tête (le pied) et le corps : une mécanique du PDF, que Word n'a pas (son corps commence à la marge) ; sans clé, pour que les deux rendus restent les mêmes.
- `UNDERLINE_OFFSET = -0.125` : Le soulignement d'un texte d'en-tête ou de pied, en fraction de sa taille : comme `Paragraph` (sous la ligne de base, d'un vingtième de la taille).
- `UNDERLINE_WIDTH = 0.05` : Épaisseur d'un soulignement, en part de la taille du texte.
- `CHECK_COLUMN = 18.0` : Une liste à cocher : la largeur d'une colonne de case (la géométrie de la case, comme son côté et son trait ; Word a la sienne, dans le style de la case).
- `CHART_TITLE_SPACE = (6.0, 4.0)` : Le titre d'un graphique : l'espace avant et après, comme Word le dessine dans le sien.
- `DATE_SPACE = 3.0` : L'espace sous « Date : », dans une signature (`word.writer._DATE_SPACE`).
- `WATERMARK_WIDTH = 440.0` : Le filigrane : la place qu'il prend, en diagonale, et son opacité.
- `WATERMARK_HEIGHT = 110.0` : Hauteur de la place du filigrane, en points (voir `WATERMARK_WIDTH`).
- `WATERMARK_ALPHA = 0.5` : Opacité du filigrane.
- `IMAGE_HEIGHT = 0.85` : La part de la hauteur utile d'une page qu'une image peut prendre, avec sa légende dessous.
- `CHECK_SIDE = 0.62` : La case d'une liste à cocher : son côté, son trait.
- `CHECK_LINE = 0.6` : Épaisseur du trait de la case, en points (voir `CHECK_SIDE`).
- `MARK = 'loom_mark'` : Le rappel qui note la page d'un titre du sommaire, quand il se dessine.
- `NOTE_MARK = 'loom_note_'` : L'ancre d'une note, en fin de document : `NOTE_MARK` et son numéro.
- `BULLETS = ('•', '–', '▪')` : Les puces des trois niveaux d'une liste, et la numérotation de chaque niveau (1., a), i.).
- `NUMBERS = (('1', '%s.'), ('a', '%s)'), ('i', '%s.'))` : Numérotation de chaque niveau d'une liste : son type, et le motif de son numéro (1., a), i.).
- `SMALL_BULLET = 0.7` : Une puce qu'une police n'a pas (le carré du troisième niveau) : la première, plus petite.
- `PLAIN = _Layout()` : La mise en page d'un document libre.
- `_VALIGN = {'top': 'TOP', 'center': 'MIDDLE', 'bottom': 'BOTTOM'}` : Le texte dans la hauteur d'une cellule de tableau (`valign`), pour reportlab.

## Fonction `loom_ia.tools_builtin.pdf.writer._twips`

```python
def _twips(value: float) -> float
```

Une longueur en cm, en points, au twip près : celle de Word.

## Fonction `loom_ia.tools_builtin.pdf.writer._pads`

```python
def _pads(value: Sides | None, unit: str) -> Sides
```

Un écart de chaque côté (en haut, à droite, en bas, à gauche), en points.

Un `padding` en cm, au twip près comme dans Word ; un `border_space` en points.

## Fonction `loom_ia.tools_builtin.pdf.writer._registered`

```python
def _registered(family: str, files: Mapping[str, bytes]) -> str
```

Les fichiers TrueType d'une police, enregistrés une fois dans reportlab : le nom de sa graisse normale, qui nomme aussi sa famille (`<b>`, `<i>`).

## Fonction `loom_ia.tools_builtin.pdf.writer.register_fonts`

```python
def register_fonts(styles: Iterable[Style]) -> None
```

Les polices embarquées des styles, enregistrées au montage : un fichier que reportlab ne sait pas lire se voit dès `loom validate` (`ValueError`).

## Classe `loom_ia.tools_builtin.pdf.writer._Fonts`

```python
class _Fonts
```

Les polices PDF d'un style : une police dont le style donne les fichiers est embarquée ; une autre se rend par son équivalent standard.

### Méthode `loom_ia.tools_builtin.pdf.writer._Fonts.name`

```python
def name(self, family: str | None, bold: bool=False, italic: bool=False) -> str
```

La police PDF d'une police du style, dans sa graisse et son style.

## Fonction `loom_ia.tools_builtin.pdf.writer.substitutions`

```python
def substitutions(style: Style) -> dict[str, str]
```

Les polices du style qui ne s'écrivent pas telles quelles : la famille standard qui les remplace, ou « embarquée ».

## Fonction `loom_ia.tools_builtin.pdf.writer._on_draw`

```python
def _on_draw(canvas: Canvas, name: str, callback: Callable[[Canvas, str, str], None]) -> None
```

Le rappel d'un `<onDraw name=…>` : reportlab 5 le cherche par `getNamedCB` ; avant (4.4, la borne basse), par un attribut du canevas.

## Fonction `loom_ia.tools_builtin.pdf.writer._merged`

```python
def _merged(look: Look, props: Mapping[str, Any]) -> Look
```

Un style, avec les propriétés qu'un autre écrit en plus (l'en-tête d'un tableau).

## Fonction `loom_ia.tools_builtin.pdf.writer._framed`

```python
def _framed(look: Look) -> bool
```

Un style qui dessine autour du texte : un filet, un fond.

## Classe `loom_ia.tools_builtin.pdf.writer._Layout`

```python
@dataclass(frozen=True, slots=True)
class _Layout
```

Ce qu'une section du modèle dit de sa mise en page et de ses styles ; un document libre est une seule section, sans rien de tout cela.

**Attributs :**

- `styles` : Les styles de ses éléments.
- `box` : Section encadrée.
- `width` : Sa largeur, en part du texte.
- `page_break` : Commencée sur une nouvelle page.
- `columns` : Colonnes de texte.
- `landscape` : En paysage.
- `cover` : Page de garde.
- `margin_top` : Espace avant la section, en points.
- `align` : Une page de garde : son texte centré.

## Classe `loom_ia.tools_builtin.pdf.writer._Group`

```python
@dataclass(slots=True)
class _Group
```

Une section du document.

Sa ligne dans le modèle (`None` pour un document libre, ou le titre du document
avant les sections), sa mise en page, ses blocs.

## Fonction `loom_ia.tools_builtin.pdf.writer._groups`

```python
def _groups(prepared: Prepared, template: Template | None) -> list[_Group]
```

Un document libre : un seul groupe. Un document qui suit un modèle : le titre du document, puis une section par groupe, dans l'ordre du modèle.

## Classe `loom_ia.tools_builtin.pdf.writer._Unit`

```python
@dataclass(slots=True)
class _Unit
```

Ce qui se pose d'un tenant : une section, ou des sections côte à côte.

## Classe `loom_ia.tools_builtin.pdf.writer._Check`

```python
class _Check(Flowable)
```

La case d'une liste à cocher, dessinée : les polices standard n'ont pas de case.

### Méthode `loom_ia.tools_builtin.pdf.writer._Check.wrap`

```python
def wrap(self, aW: float, aH: float) -> tuple[float, float]
```

La place d'une case : un carré de côté `side`.

### Méthode `loom_ia.tools_builtin.pdf.writer._Check.draw`

```python
def draw(self) -> None
```

Dessine la case, cochée d'une croix si l'élément l'est.

## Classe `loom_ia.tools_builtin.pdf.writer._Contents`

```python
class _Contents(Flowable)
```

Le sommaire.

Ses entrées (le titre, et son numéro s'il est numéroté), un lien vers chacune, et sa
page, connue d'un rendu précédent, après une ligne de points.

### Méthode `loom_ia.tools_builtin.pdf.writer._Contents.wrap`

```python
def wrap(self, aW: float, aH: float) -> tuple[float, float]
```

Construit le tableau du sommaire, avec le numéro de page de chaque entrée, et rend sa place.

### Méthode `loom_ia.tools_builtin.pdf.writer._Contents.split`

```python
def split(self, aW: float, aH: float) -> list[Flowable]
```

Coupe le sommaire sur plusieurs pages.

### Méthode `loom_ia.tools_builtin.pdf.writer._Contents.drawOn`

```python
def drawOn(self, canvas: Canvas, x: float, y: float, _sW: float=0) -> None
```

Dessine le sommaire.

## Classe `loom_ia.tools_builtin.pdf.writer._Pass`

```python
@dataclass(slots=True)
class _Pass
```

Ce qu'un rendu apprend pour le suivant : le nombre de pages, la page de chaque titre du sommaire.

## Classe `loom_ia.tools_builtin.pdf.writer._Body`

```python
class _Body
```

Les blocs du document en flowables, selon le style ; les caractères de chaque police, pour dire ceux qu'elle n'a pas.

### Méthode `loom_ia.tools_builtin.pdf.writer._Body.__init__`

```python
def __init__(self, prepared: Prepared, style: Style, template: Template | None, pages: Mapping[str, int]) -> None
```

Prépare la mise en flowables du document.

**Arguments :**

- `prepared` : Le document validé.
- `style` : Le style résolu.
- `template` : Le modèle suivi, s'il y en a un.
- `pages` : La page de chaque titre, connue d'un rendu précédent.

### Méthode `loom_ia.tools_builtin.pdf.writer._Body.look`

```python
def look(self, element: str, chosen: str | None, layout: _Layout) -> tuple[str, Look]
```

Le style d'un élément : celui que le bloc choisit (`style`), sinon celui que la section choisit (`styles`), sinon le style prévu de l'élément.

### Méthode `loom_ia.tools_builtin.pdf.writer._Body.paragraph_style`

```python
def paragraph_style(self, name: str, look: Look, *, align: str | None=None, keep: bool=False, bare: bool=False) -> ParagraphStyle
```

Le style reportlab d'un style nommé. `bare` : sans retrait ni espacement, que porte le cadre qui l'entoure (un filet, un fond).

### Méthode `loom_ia.tools_builtin.pdf.writer._Body.text`

```python
def text(self, runs: Inline, pstyle: ParagraphStyle, look: Look, *, prefix: str='') -> str
```

Le balisage de `Paragraph`.

Le texte échappé, l'emphase en `<b>`, `<i>`, `<u>` ; un lien dans le style
`link` ; l'appel d'une note, en exposant, vers la note ; un renvoi : le numéro
du titre visé (ou son titre entre guillemets), vers lui. Ses caractères comptés
pour la police.

### Méthode `loom_ia.tools_builtin.pdf.writer._Body.plain`

```python
def plain(self, value: str, pstyle: ParagraphStyle) -> str
```

Un texte sans balisage, échappé ; ses caractères comptés pour la police.

### Méthode `loom_ia.tools_builtin.pdf.writer._Body.paragraph`

```python
def paragraph(self, name: str, look: Look, runs: Inline, width: float, *, align: str | None=None, keep: bool=False, prefix: str='', anchor: str='') -> Flowable
```

Un paragraphe dans son style ; dans un cadre si le style a un filet ou un fond.

### Méthode `loom_ia.tools_builtin.pdf.writer._Body.frame`

```python
def frame(self, content: list[Flowable], look: Look, width: float) -> Flowable
```

Des paragraphes dans le cadre d'un style : ses filets, son fond, son retrait et ses espacements, comme Word les pose autour d'un paragraphe.

### Méthode `loom_ia.tools_builtin.pdf.writer._Body._headings`

```python
def _headings(self, blocks: Sequence[BlockOut]) -> None
```

Le numéro de chaque titre (si le style numérote), et les entrées du sommaire (s'il y en a un), avec l'ancre de chaque titre repris.

### Méthode `loom_ia.tools_builtin.pdf.writer._Body.heading`

```python
def heading(self, block: HeadingOut, width: float, layout: _Layout) -> Flowable
```

Un titre : son style, son numéro, son ancre pour le sommaire et les renvois.

### Méthode `loom_ia.tools_builtin.pdf.writer._Body.endnotes`

```python
def endnotes(self, width: float) -> list[Flowable]
```

Les notes, en fin de document.

Sous un titre (`labels.notes`) au style des titres de premier niveau, chacune
dans le style `footnote`, après son numéro.

### Méthode `loom_ia.tools_builtin.pdf.writer._Body.contents`

```python
def contents(self, width: float, pages: Mapping[str, int]) -> list[Flowable]
```

Le sommaire : son titre, puis ses entrées dans les styles `toc1` à `toc3`.

### Méthode `loom_ia.tools_builtin.pdf.writer._Body.caption`

```python
def caption(self, kind: str, runs: Inline, width: float, layout: _Layout) -> Flowable
```

La légende ; numérotée (« Figure 2 — ») si le style `caption` le veut.

### Méthode `loom_ia.tools_builtin.pdf.writer._Body.after`

```python
def after(self, points: float) -> Flowable
```

L'espace sous un tableau, une image ou un graphique sans légende, des sections côte à côte.

Le paragraphe vide qui les suit dans Word, une ligne de `body`, et `points`
après elle (le `space_after` de leur style, de `image`, `chart`).

### Méthode `loom_ia.tools_builtin.pdf.writer._Body.blocks`

```python
def blocks(self, blocks: Sequence[BlockOut], width: float, layout: _Layout=PLAIN) -> list[Flowable]
```

Les flowables d'une suite de blocs ; deux images `half` qui se suivent côte à côte si le style le veut.

### Méthode `loom_ia.tools_builtin.pdf.writer._Body.list`

```python
def list(self, block: ListOut, width: float, layout: _Layout) -> Flowable
```

Une liste et ses sous-listes : puce ou numéro à chaque niveau (1., a), i.).

### Méthode `loom_ia.tools_builtin.pdf.writer._Body.checklist`

```python
def checklist(self, block: ListOut, width: float, layout: _Layout) -> Flowable
```

Une liste à cocher : une case dessinée devant chaque élément, à la place d'une puce, et chaque sous-élément en retrait.

### Méthode `loom_ia.tools_builtin.pdf.writer._Body.quote`

```python
def quote(self, block: QuoteOut, width: float, layout: _Layout) -> Flowable
```

La citation et sa source, dans le cadre de la citation (un seul filet).

### Méthode `loom_ia.tools_builtin.pdf.writer._Body.table`

```python
def table(self, block: TableOut, width: float, layout: _Layout) -> list[Flowable]
```

Un tableau : en-têtes, lignes, groupes, total, légende, largeurs et styles.

### Méthode `loom_ia.tools_builtin.pdf.writer._Body.fields`

```python
def fields(self, block: FieldsOut, width: float) -> list[Flowable]
```

Des libellés et leurs valeurs, en deux colonnes sans filet (le libellé large de `label_width`).

Une valeur vide est une ligne à remplir, au trait des tableaux.

### Méthode `loom_ia.tools_builtin.pdf.writer._Body.code`

```python
def code(self, block: CodeOut, width: float) -> Flowable
```

Du code.

Ses lignes telles quelles (les espaces gardées, une ligne trop longue reprise à
la suivante), dans le style `code` et son cadre (un fond).

### Méthode `loom_ia.tools_builtin.pdf.writer._Body.image_flowable`

```python
def image_flowable(self, block: ImageOut, width: float) -> Flowable
```

L'image, à la taille décidée pour les deux formats, réduite à la place qu'elle a.

### Méthode `loom_ia.tools_builtin.pdf.writer._Body.image`

```python
def image(self, block: ImageOut, width: float, layout: _Layout) -> Flowable
```

Une image, et sa légende ou l'espace qui la suit, gardées ensemble.

### Méthode `loom_ia.tools_builtin.pdf.writer._Body.image_pair`

```python
def image_pair(self, first: ImageOut, second: ImageOut, width: float, layout: _Layout) -> Flowable
```

Deux images `half` côte à côte (`image.side_by_side`), chacune avec sa légende.

### Méthode `loom_ia.tools_builtin.pdf.writer._Body.chart`

```python
def chart(self, block: ChartOut, width: float, layout: _Layout) -> Flowable
```

Le titre, le dessin et la légende du graphique, sur une même page.

### Méthode `loom_ia.tools_builtin.pdf.writer._Body.signature`

```python
def signature(self, block: SignatureOut, width: float) -> Flowable
```

La zone de signature : une colonne par signataire, sans cadre.

### Méthode `loom_ia.tools_builtin.pdf.writer._Body.box`

```python
def box(self, layout: _Layout) -> tuple[Any, Any, Sides]
```

Le cadre d'une section encadrée : son filet, son fond et son écart au texte (`box`, ou celui que la section choisit).

### Méthode `loom_ia.tools_builtin.pdf.writer._Body.layout_table`

```python
def layout_table(self, cells: Sequence[list[Flowable]], widths: Sequence[float], boxed: Mapping[int, _Layout]) -> Flowable
```

Des contenus côte à côte, ou un seul, encadré : un tableau sans bordure.

### Méthode `loom_ia.tools_builtin.pdf.writer._Body.units`

```python
def units(self, groups: Sequence[_Group], widths: Callable[[bool], float]) -> list[_Unit]
```

Les groupes, posés.

Seuls, ou côte à côte quand ils sont sur la même ligne du modèle (une section
`beside` absente laisse l'autre seule, en pleine largeur).

### Méthode `loom_ia.tools_builtin.pdf.writer._Body.alone`

```python
def alone(self, group: _Group, width: float, landscape: bool) -> _Unit
```

Une section seule : sa mise en page (colonnes, cadre, page de garde, espace avant) et ses blocs.

### Méthode `loom_ia.tools_builtin.pdf.writer._Body.beside`

```python
def beside(self, together: Sequence[_Group], width: float, landscape: bool) -> _Unit
```

Des sections côte à côte : une cellule chacune, à la part que dit `width` (des parts égales sans elle).

## Fonction `loom_ia.tools_builtin.pdf.writer._table_lines`

```python
def _table_lines(tables: Look) -> list[tuple[Any, ...]]
```

Les filets d'un tableau.

`borders` (grille, horizontales, verticales, aucun) au trait `line`, puis
`border_top` et `border_bottom` s'ils sont dits.

## Fonction `loom_ia.tools_builtin.pdf.writer._spaced`

```python
def _spaced(line: str) -> str
```

Une ligne de code, ses espaces gardées.

Celles du début, et celles d'une série (la dernière reste une espace, où la ligne
peut se couper) ; une tabulation, quatre.

## Fonction `loom_ia.tools_builtin.pdf.writer._heading_number`

```python
def _heading_number(numbering: str, counters: Sequence[int], article: str) -> str
```

Le numéro d'un titre, comme Word l'écrit : « 2. », « 2.1 », « Article 2 » et un tiret.

## Classe `loom_ia.tools_builtin.pdf.writer._Zone`

```python
@dataclass(frozen=True, slots=True)
class _Zone
```

Une ligne d'en-tête ou de pied de page déjà mesurée : sa hauteur, et de quoi la dessiner.

## Classe `loom_ia.tools_builtin.pdf.writer._Box`

```python
@dataclass(frozen=True, slots=True)
class _Box
```

Les lignes d'un en-tête ou d'un pied dans le cadre de son style, comme Word groupe les paragraphes qui se suivent dans un même style.

L'espace avant le premier, puis les filets et le fond autour du groupe (à l'écart du
texte de `border_space`), entre deux lignes l'espace après l'une et avant l'autre,
l'espace après la dernière ; le retrait à gauche et à droite.

**Attributs :**

- `zones` : Les lignes, chacune avec sa hauteur et de quoi la dessiner.
- `look` : Le style du cadre.
- `padded` : Sans écart au cadre : une table d'en-tête ou de pied le met dans ses cellules du bord.

### Méthode `loom_ia.tools_builtin.pdf.writer._Box._pads`

```python
def _pads(self) -> tuple[float, float, float, float]
```

L'écart du texte au cadre : en haut, en bas, à gauche, à droite.

### Méthode `loom_ia.tools_builtin.pdf.writer._Box.height`

```python
@property
def height(self) -> float
```

La hauteur du cadre et de ses lignes, espacements compris.

### Méthode `loom_ia.tools_builtin.pdf.writer._Box.draw`

```python
def draw(self, canvas: Canvas, top: float, left: float, right: float, page: int, total: int | None) -> None
```

Dessine le cadre et ses lignes sur la page.

## Fonction `loom_ia.tools_builtin.pdf.writer._leading`

```python
def _leading(look: Look) -> float
```

La hauteur d'une ligne de texte d'un style, interligne compris.

## Classe `loom_ia.tools_builtin.pdf.writer._GridCell`

```python
@dataclass(frozen=True, slots=True)
class _GridCell
```

Une cellule de table d'en-tête ou de pied, posée.

Sa colonne (x0, x1, depuis le bord gauche de la table), ses marges (haut, gauche,
bas, droite), ses paragraphes coupés (et s'ils portent le logo), la hauteur de son
contenu ; l'espace avant et après de ses paragraphes (`Style.cell_spacing`).

## Classe `loom_ia.tools_builtin.pdf.writer._GridRow`

```python
@dataclass(frozen=True, slots=True)
class _GridRow
```

Une ligne d'une table d'en-tête ou de pied.

**Attributs :**

- `line` : Un filet sous la ligne.
- `cells` : Ses cellules.
- `height` : Sa hauteur, en points.

## Classe `loom_ia.tools_builtin.pdf.writer._Bands`

```python
class _Bands
```

L'en-tête, le pied de page et le filigrane, dessinés à chaque page.

À 1 cm du bord, comme Word ; trois zones (gauche, centre, droite) et des lignes
centrées — sous les zones dans un en-tête, au-dessus dans un pied de page.

### Méthode `loom_ia.tools_builtin.pdf.writer._Bands._logo`

```python
def _logo(self, band: Band) -> tuple[Any, float, float, float]
```

Le logo d'un en-tête ou d'un pied : son image, sa largeur, sa hauteur, l'écart à son texte ; pas d'image sans logo.

### Méthode `loom_ia.tools_builtin.pdf.writer._Bands.lines`

```python
def lines(self, band: Band | None, look: Look, *, header: bool) -> list[_Zone]
```

Les lignes d'un en-tête ou d'un pied, du haut vers le bas.

La ligne des zones, chacune à sa place et au style de son élément (`center`
toujours centré) ; les `lines`, chacune un paragraphe au style de son élément,
centrée dans un en-tête si ce style ne dit pas d'alignement.

### Méthode `loom_ia.tools_builtin.pdf.writer._Bands.box`

```python
def box(self, band: Band | None, look: Look, *, header: bool) -> _Box
```

Les lignes d'un en-tête ou d'un pied dans le cadre de son style.

Avec une table, comme dans Word, l'espacement de son style vaut autour d'elle,
et l'écart au cadre est dans ses cellules du bord.

### Méthode `loom_ia.tools_builtin.pdf.writer._Bands._grid`

```python
def _grid(self, band: Band, *, header: bool) -> _Zone
```

La table d'un en-tête ou d'un pied, comme dans Word (`word.style._band_table`).

Les colonnes de `band_columns` ; dans chaque cellule, ses paragraphes, chacun
avec l'espace avant et après de son style, coupés à la largeur de la cellule ;
l'écart entre colonnes en deux moitiés, de part et d'autre du filet intérieur ;
l'écart au cadre dans les cellules du bord. La hauteur se mesure une fois, à la
largeur du texte, avec des numéros de page du plus grand nombre de chiffres.

### Méthode `loom_ia.tools_builtin.pdf.writer._Bands.heights`

```python
def heights(self) -> tuple[float, float]
```

La place que prennent l'en-tête et le pied, depuis le bord de la page.

### Méthode `loom_ia.tools_builtin.pdf.writer._Bands.draw`

```python
def draw(self, canvas: Canvas, size: tuple[float, float], page: int, total: int | None, *, header: bool, footer: bool) -> None
```

Dessine l'en-tête, le pied et le filigrane d'une page.

### Méthode `loom_ia.tools_builtin.pdf.writer._Bands._watermark`

```python
def _watermark(self, canvas: Canvas, size: tuple[float, float]) -> None
```

Le filigrane : un texte en diagonale, derrière le texte de la page, dans la police et la couleur du style `watermark`, à demi transparent.

## Fonction `loom_ia.tools_builtin.pdf.writer._build`

```python
def _build(prepared: Prepared, style: Style, template: Template | None, known: _Pass) -> tuple[bytes, _Pass, _Body]
```

Un rendu du document : les octets du PDF, ce qu'il apprend pour le rendu suivant (pages), et le corps.

## Fonction `loom_ia.tools_builtin.pdf.writer.missing_glyphs`

```python
def missing_glyphs(body: _Body) -> list[str]
```

Les caractères qu'une police du document n'a pas : ils s'imprimeraient en carré noir.

## Fonction `loom_ia.tools_builtin.pdf.writer.render`

```python
def render(prepared: Prepared, style: Style | None=None, template: Template | None=None) -> tuple[bytes, int, list[str]]
```

Le PDF, son nombre de pages, et les avertissements du rendu.

`style` : le style du document (le style maison sans lui) ; `template` : le
modèle qu'il suit.
