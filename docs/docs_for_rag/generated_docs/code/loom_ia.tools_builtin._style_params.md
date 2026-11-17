# loom_ia.tools_builtin._style_params

Module `loom_ia.tools_builtin._style_params` de loom_v2 (fichier `src/loom_ia/tools_builtin/_style_params.py`).

Le style d'un document `word` ou `pdf` : des styles nommés, dans des fichiers YAML.

Un fichier de style est une liste de **styles nommés** — `body`, `document_title`,
`heading1`… —, chacun avec ses propriétés (police, taille, couleur, espacements,
filets), et quelques **réglages** (`colors`, `page`, `numbering`, `language`,
`labels`, `image`, `chart`, `fonts`). Le contenu de l'en-tête, du pied et du
filigrane est un réglage (`page.header`, `page.footer`, `page.watermark`) ; les
styles `header`, `footer` et `watermark` n'en ont que l'apparence. Le format est
décrit dans `docs/style_format.md`. Trois fichiers, du plus général au plus
précis :

1. `styles/default.yaml`, livré avec loom : le style maison ;
2. la charte de l'outil (`style:`) ;
3. le style d'un modèle de document (`style:` dans le fichier du modèle).

**Les fichiers d'abord** : chaque style complète le même style des fichiers précédents,
propriété par propriété. **L'héritage ensuite** : un style prend les propriétés de sa
`base` (`body` sans base), déjà complétée par les fichiers, puis les siennes. Le
code des outils n'a aucune valeur visuelle : tout vient de ces fichiers, distances et
espaces compris (restent des unités et des mécaniques, que les essais nomment).

Un style à toi qui a une `description` est **proposé au modèle de langage**, sur le type
de bloc dont il descend (`important_note`, `base: note` : le champ `style` d'un bloc
`note`) ; il n'en voit que le nom et la description. Du reste, il ne voit rien.

`fonts` donne les fichiers TrueType d'une police, que le PDF embarque ; ils sont lus au
chargement, et vérifiés (`font_fault`) : contours TrueType, droits d'embarquement.

Module léger — Pydantic et PyYAML — : la config l'importe pour valider une référence,
sans importer l'outil.

## Constantes de `loom_ia.tools_builtin._style_params`

- `DEFAULT_FILE = Path(__file__).with_name('styles') / 'default.yaml'` : Le style de la maison, `styles/default.yaml` : celui d'un outil sans `style`.
- `MAX_STYLE_CHARS = 20000` : Taille au plus d'un fichier de style, en caractères.
- `MAX_LOGO_BYTES = 2000000` : Taille au plus d'un logo, en octets.
- `MAX_FONT_BYTES = 2000000` : Les polices embarquées dans le PDF : leurs fichiers, et leurs familles par fichier de style.
- `MAX_FONT_FAMILIES = 4` : Familles de polices au plus par fichier de style.
- `MAX_BAND_TEXT = 200` : Longueur au plus d'un texte d'en-tête ou de pied de page.
- `MAX_BAND_LINES = 4` : Lignes de texte au plus dans une zone d'en-tête ou de pied de page.
- `MAX_BAND_COLUMNS = 4` : La table d'un en-tête ou d'un pied de page : ses colonnes, ses lignes, la largeur d'une colonne `auto` au moins (en cm).
- `MAX_BAND_ROWS = 3` : Lignes au plus de la table d'un en-tête ou d'un pied de page.
- `MIN_AUTO_WIDTH = 1.0` : Largeur au moins d'une colonne `auto` de cette table, en cm.
- `MAX_STYLES = 80` : Styles nommés au plus dans un fichier de style.
- `MAX_LABEL = 40` : Longueur au plus d'un libellé (`labels`).
- `SAFE_FONTS = frozenset({'aptos', 'aptos display', 'arial', 'calibri', 'cambria', 'georgia', 'times new roman', 'verdana', 'tahoma', …` : Les polices présentes sur presque tous les postes (Windows, macOS, LibreOffice les remplace par des équivalentes métriques ; Aptos, celle de Word depuis Microsoft 365, s'installe avec Office) : une autre s'affiche ailleurs dans une police de remplacement, et `loom validate` le signale.
- `PDF_FONTS = {**dict.fromkeys(('aptos', 'aptos display', 'arial', 'helvetica', 'calibri', 'verdana', 'tahoma', 'trebuchet ms'), 'Hel…` : Les polices standard du PDF, qui ne s'embarquent pas : celle qui rend une police du style dont le style ne donne pas les fichiers (`fonts`), selon sa forme ; Helvetica sinon.
- `PDF_FALLBACK = 'Helvetica'` : Police standard du PDF quand aucune autre ne convient.
- `PLACEHOLDERS = frozenset({'page', 'pages', 'title'})` : Les champs d'un en-tête ou d'un pied de page.
- `NONE = 'none'` : Valeur qui retire une couleur, un fond ou un filet.
- `Align = Literal['left', 'center', 'right', 'justify']` : Alignement d'un paragraphe.
- `Position = Literal['left', 'center', 'right']` : Place d'un élément dans la largeur : gauche, centre ou droite.
- `Borders = Literal['none', 'horizontal', 'vertical', 'grid']` : Les filets d'un tableau : aucun, entre ses lignes, entre ses colonnes, les deux (grid).
- `VAlign = Literal['top', 'center', 'bottom']` : Alignement vertical dans une cellule.
- `TableWidth = Literal['full', 'content']` : Largeur d'un tableau : toute la page, ou celle de son contenu.
- `Numbering = Literal['none', 'decimal', 'article']` : Numérotation des titres : aucune, décimale (1, 1.1…) ou par articles.
- `PageSize = Literal['A4', 'A5', 'Letter']` : Format de page.
- `Orientation = Literal['portrait', 'landscape']` : Orientation de la page.
- `Language = Literal['fr', 'en']` : Langue des libellés produits par l'outil (« Tableau », « Figure »…).
- `CM = 567` : Un centimètre en vingtièmes de point (twips).
- `PAGE_SIZES = {'A4': (11906, 16838), 'A5': (8391, 11906), 'Letter': (12240, 15840)}` : Dimensions de chaque format de page, en twips (1/20 de point) : largeur, hauteur en portrait.
- `BASES = {'body': None, 'document_title': 'body', 'heading1': 'body', 'heading2': 'body', 'heading3': 'body', 'paragraph': 'body…` : Les styles prévus, et la base de chacun quand le fichier n'en donne pas.
- `ROW_STYLES = frozenset({'table_header', 'table_total', 'table_group', 'table_subtotal'})` : Les styles d'une ligne de tableau : leur espacement est celui de leurs cellules.
- `PROPERTIES = (Property('base', None, 'nom de style', 'Le style dont celui-ci part'), Property('description', None, 'texte', "Le sens…` : Le registre : la seule liste des propriétés. `Props` (le fichier lu) et `Look` (le style résolu) en ont exactement les noms, ce qu'un essai vérifie ; `_OWNERS` et le message d'une propriété inconnue en sont tirés.
- `PROPERTY_NAMES = frozenset((found.name for found in PROPERTIES))` : Noms des propriétés qu'un style nommé peut porter.
- `_OWNERS = {found.name: found.owners for found in PROPERTIES if found.owners is not None}` : Les propriétés propres à certains styles, et ceux (avec leurs dérivés) qui les portent.
- `SETTINGS = frozenset({'colors', 'page', 'numbering', 'language', 'labels', 'image', 'chart', 'fonts'})` : Les réglages : des clés du fichier qui ne sont pas des styles.
- `LAYOUT = frozenset({'sections', 'beside'})` : La disposition des sections d'un modèle : dans le modèle de document depuis 2.1.0.
- `BLOCK_STYLES = {'heading1': 'heading', 'heading2': 'heading', 'heading3': 'heading', 'paragraph': 'paragraph', 'list': 'list', 'note':…` : Les styles de bloc : un style à toi qui en descend peut être proposé au modèle de langage (`description`), sur ce type de bloc.
- `MAX_STYLE_DESCRIPTION = 200` : Longueur au plus de la description d'un style nommé.
- `OLD_KEYS = {'title': 'document_title', 'headings': 'heading1, heading2, heading3 (et numbering : decimal, article)', 'paragraphs':…` : Les clés du format de la 2.0, refusées avec ce qui les remplace.
- `LABEL_KEYS = ('table', 'figure', 'contents', 'date', 'signature', 'article', 'total', 'notes', 'subtotal', 'total_excl_tax', 'tax', …` : Les libellés que l'outil écrit dans le document, par langue.
- `TAX_FIELDS = frozenset({'rate', 'base'})` : Les champs du libellé d'une ligne de TVA : le taux (obligatoire) et sa base.
- `LABELS = {'fr': {'table': 'Tableau', 'figure': 'Figure', 'contents': 'Sommaire', 'date': 'Date :', 'signature': 'Signature :', '…` : Libellés produits par l'outil, par langue.
- `POINTS_PER = {'pt': 1.0, 'mm': 72 / 25.4, 'cm': 72 / 2.54, 'in': 72.0}` : Points par unité de longueur : `pt`, `mm`, `cm`, `in`.
- `in_points = _length_in('pt')` : Une longueur lue en points (la typographie, les espacements), en centimètres (la géométrie).
- `in_centimetres = _length_in('cm')` : Une longueur lue en centimètres : un nombre seul y est déjà, une longueur avec unité y est convertie.
- `Sides = tuple[float, float, float, float]` : Les quatre côtés d'un écart : en haut, à droite, en bas, à gauche (l'ordre de CSS).
- `Color = Annotated[str, BeforeValidator(_text_color), AfterValidator(_color)]` : Une couleur : six chiffres hexadécimaux (`1F3864`, « # » facultatif), ou le nom d'une couleur déclarée dans `colors`.
- `Fill = Annotated[str, BeforeValidator(_text_color), AfterValidator(_fill)]` : Une couleur de fond, comme `Color`, ou `none`.
- `Hex = Annotated[str, BeforeValidator(_text_color), AfterValidator(_hex)]` : Une couleur en six chiffres hexadécimaux seulement (`1F3864`).
- `Rule = Annotated[str, AfterValidator(_rule)]` : Un filet : épaisseur et couleur (`1pt 2F5496`, `3pt brand`), ou `none`.
- `FontName = Annotated[str, AfterValidator(_font)]` : Un nom de police : lettres, chiffres, espaces et tirets.
- `BandText = Annotated[str, AfterValidator(_band_text)]` : Le texte d'un en-tête ou d'un pied, avec ses champs (`{page}`, `{pages}`, `{title}`).
- `StyleName = Annotated[str, AfterValidator(_style_name)]` : Le nom d'un style nommé : minuscules, chiffres et « _ ».
- `ColorName = Annotated[str, AfterValidator(_color_name)]` : Le nom d'une couleur déclarée dans `colors` : minuscules, chiffres et « _ ».
- `Points = Annotated[float, BeforeValidator(in_points), Field(ge=0, le=72)]` : Une longueur en points, de 0 à 72 ; une autre unité est convertie.
- `RENAMED_STYLES = {'text': 'body'}` : Les styles renommés en 2.0.21 (docs/style_format.md), refusés avec leur nouveau nom.
- `BandWidth = Annotated[float | str, BeforeValidator(_band_width_read)]` : Une largeur de colonne : auto, une proportion (`50%`), une longueur (cm par défaut).
- `Margin = Annotated[float, BeforeValidator(in_centimetres), Field(ge=0.5, le=5)]` : Une marge en centimètres, de 0,5 à 5 ; une autre unité est convertie.
- `FONT_VARIANTS = ('regular', 'bold', 'italic', 'bold_italic')` : Les variantes d'une police, dans l'ordre de `FontFiles`.
- `_MOVED = {'left': 'left', 'center': 'center', 'right': 'right', 'lines': 'lines', 'table': 'table', 'logo': 'logo', 'logo_width'…` : Le contenu que portaient les styles `header`, `footer` et `watermark` avant 2.0.21, et où il va.
- `StyleField = Annotated[StyleRef | None, BeforeValidator(short_style)]` : La référence à un style nommé ; un nom seul est admis.
- `_RESTRICTED = 2` : Le droit d'embarquer une police (`fsType` de sa table OS/2) : ce qui l'interdit.
- `LOGO_WIDTH = 3.0` : Le logo d'un en-tête ou d'un pied sans largeur ni écart : 3 cm, à 4 pt de son texte.
- `LOGO_GAP = 4.0` : Écart entre un logo et son texte, en points, sans réglage.
- `FRENCH = Labels()` : Les libellés en français, ceux par défaut.

## Fonction `loom_ia.tools_builtin._style_params.cm_twips`

```python
def cm_twips(value: float) -> int
```

Une longueur en cm, en twips : la mesure de Word, que le PDF reprend.

## Classe `loom_ia.tools_builtin._style_params.Property`

```python
@dataclass(frozen=True, slots=True)
class Property
```

Une propriété de style, telle que la décrit le registre.

Son nom ; les styles qui la portent, avec ceux qui en partent (`None` : tous, une
propriété commune) ; sa valeur ; l'unité d'un nombre ; ses bornes ; son rôle.

## Fonction `loom_ia.tools_builtin._style_params._text_color`

```python
def _text_color(value: object) -> object
```

Les couleurs YAML écrites sans guillemets : `1F3864` reste une chaîne, `000000` aussi (un nombre pour YAML).

## Fonction `loom_ia.tools_builtin._style_params._color`

```python
def _color(value: str) -> str
```

Six chiffres hexadécimaux (en majuscules), ou le nom d'une couleur de `colors`.

## Fonction `loom_ia.tools_builtin._style_params._rule`

```python
def _rule(value: str) -> str
```

Un filet : « épaisseur couleur » (`1pt 2F5496`, `3pt brand`), ou `none`.

## Fonction `loom_ia.tools_builtin._style_params._length_in`

```python
def _length_in(unit: str) -> Callable[[object], object]
```

Une longueur écrite (`11pt`, `2cm`, `12mm`, `1in`, ou un nombre), dans `unit`.

## Fonction `loom_ia.tools_builtin._style_params.percent`

```python
def percent(value: object) -> object
```

Une proportion écrite (`55%`, ou un nombre), en pour cent.

## Fonction `loom_ia.tools_builtin._style_params._sides_in`

```python
def _sides_in(unit: str, high: float) -> Callable[[object], object]
```

Une à quatre longueurs (`4pt 8pt`), dans `unit`, de 0 à `high` chacune, dans l'ordre de CSS.

Une, les quatre côtés ; deux, en haut et en bas, puis à droite et à gauche ; trois,
en haut, à droite et à gauche, en bas ; quatre, en haut, à droite, en bas, à gauche.

## Classe `loom_ia.tools_builtin._style_params._Spec`

```python
class _Spec(BaseModel)
```

Base des modèles d'un fichier de style : figés, sans clé inconnue.

## Fonction `loom_ia.tools_builtin._style_params.refuse_renamed`

```python
def refuse_renamed(value: object, where: str, renamed: Mapping[str, str]) -> object
```

Une table où une clé renommée en 2.0.21 paraît : refusée, avec son nouveau nom.

## Classe `loom_ia.tools_builtin._style_params.BandElementSpec`

```python
class BandElementSpec(_Spec)
```

Un élément d'un en-tête ou d'un pied de page : une zone, une ligne, une cellule de sa table.

Un texte (`text`), ou des lignes (`lines`, dans une cellule seulement), avec
`{page}`, `{pages}`, `{title}` ; le nom du style qui le met en forme
(`style`, un style qui part de `header` ou de `footer`) ; le logo de l'en-tête
ou du pied (`logo: true`). Écrit en court : un texte, ou une liste de lignes.
Aucune propriété de style : elles vont dans le style qu'il nomme.

### Méthode `loom_ia.tools_builtin._style_params.BandElementSpec._short`

```python
@model_validator(mode='before')
@classmethod
def _short(cls, value: object) -> object
```

La forme courte d'un élément : un texte, ou une liste de lignes ; une propriété de style ou un style en ligne sont refusés.

## Classe `loom_ia.tools_builtin._style_params.BandLogoSpec`

```python
class BandLogoSpec(_Spec)
```

Le logo d'un en-tête ou d'un pied.

Son fichier (PNG ou JPEG, relatif à `resources_dir` ; ses octets une fois lu au
chargement), sa largeur (cm), l'écart à son texte (pt). Écrit en court : le fichier
seul.

## Fonction `loom_ia.tools_builtin._style_params._band_width_read`

```python
def _band_width_read(value: object) -> object
```

Une largeur de colonne.

`auto` ; une proportion (`50%`, de plus de 0 à 100 %) ; une longueur (`1.5cm`,
`15mm`, un nombre en cm, de plus de 0 à 30 cm).

## Classe `loom_ia.tools_builtin._style_params.BandTableSpec`

```python
class BandTableSpec(_Spec)
```

La table d'un en-tête ou d'un pied de page.

La largeur de chaque colonne (une longueur, une proportion, ou `auto` : la place
qui reste, partagée) ; ses lignes, chacune un élément par colonne ; le nom du style
qui l'habille (`style`, qui part de `header` ou de `footer` ; `header` ou
`footer` sans lui) : son cadre, son fond, ses filets intérieurs (`borders`,
`line`), l'écart entre ses colonnes (`column_gap`).

### Méthode `loom_ia.tools_builtin._style_params.BandTableSpec._styled`

```python
@model_validator(mode='before')
@classmethod
def _styled(cls, value: object) -> object
```

`gap`, `borders`, `line` d'une table : des propriétés de son style depuis 2.0.21.

## Classe `loom_ia.tools_builtin._style_params.BandSpec`

```python
class BandSpec(_Spec)
```

Ce qu'écrit un en-tête ou un pied de page (`page.header`, `page.footer`).

Trois zones sur une ligne, ou une table ; des lignes de toute la largeur ; le logo,
posé dans l'élément qui le demande ; `first_page: false` pour une première page
sans.

## Classe `loom_ia.tools_builtin._style_params.Props`

```python
class Props(_Spec)
```

Les propriétés d'un style nommé, telles qu'écrites ; toutes facultatives.

Leurs noms sont ceux du registre (`PROPERTIES`) ; une autre est dite par
`describe`.

**Attributs :**

- `base` : Style de base, dont il reprend les propriétés.
- `font` : Police.
- `size` : Taille du texte, en points (6 à 72).
- `color` : Couleur du texte.
- `fill` : Couleur du fond, ou `none`.
- `bold` : Gras.
- `italic` : Italique.
- `underline` : Souligné.
- `uppercase` : En capitales.
- `align` : Alignement : `left`, `center`, `right` ou `justify`.
- `space_before` : Espace avant, en points.
- `space_after` : Espace après, en points.
- `line_spacing` : Interligne, de 1 à 3.
- `indent` : Retrait à gauche, en cm.
- `border_top` : Filet au-dessus.
- `border_bottom` : Filet au-dessous.
- `border_left` : Filet à gauche.
- `border_right` : Filet à droite.
- `border` : Filet des quatre côtés.
- `border_space` : L'écart entre un filet (ou le fond) et le texte, en points, les quatre côtés.
- `valign` : Le tableau, et les tables d'en-tête et de pied : le texte dans la hauteur d'une cellule.
- `borders` : Le tableau : ses filets (`none`, `horizontal`, `vertical`, `grid`).
- `line` : Le tableau : le filet de ses traits.
- `bands` : Le tableau : le fond d'une ligne sur deux.
- `width` : Le tableau : toute la largeur (`full`) ou celle du contenu.
- `position` : Le tableau : sa place dans la largeur.
- `padding` : Le cadre d'une section, une cellule de tableau : l'écart au texte, en cm, les quatre côtés.
- `column_gap` : La table d'un en-tête ou d'un pied : l'écart entre deux colonnes, en cm.
- `numbered` : La légende, le sommaire : numérotés.
- `levels` : Le sommaire : niveaux de titres repris, de 1 à 3.
- `page_numbers` : Le sommaire : avec les numéros de page.
- `level_indent` : Une liste, le sommaire : le retrait de chaque niveau, en cm.
- `marker_space` : Une liste : la place de la puce, en cm.
- `sign_height` : La signature : la place pour signer, en cm.
- `label_width` : Les champs : la largeur de la colonne des libellés, en cm.
- `row_height` : La hauteur d'une ligne d'émargement, en cm : la place de signer.
- `description` : Le sens d'un style à toi : il est proposé au modèle de langage, sur son type de bloc.

## Classe `loom_ia.tools_builtin._style_params.PageSpec`

```python
class PageSpec(_Spec)
```

La page d'un fichier de style : format, marges, distances, en-tête, pied et filigrane.

**Attributs :**

- `paper` : Format : `A4`, `A5` ou `Letter`.
- `orientation` : `portrait` ou `landscape`.
- `margins` : Les quatre marges, en cm.
- `margin_top` : Marge du haut, en cm.
- `margin_bottom` : Marge du bas, en cm.
- `margin_left` : Marge de gauche, en cm.
- `margin_right` : Marge de droite, en cm.
- `header_distance` : La distance de l'en-tête au bord de la page, en cm.
- `footer_distance` : La distance du pied au bord de la page, en cm.
- `column_gap` : L'écart entre les colonnes d'une section, en cm.
- `beside_gap` : L'écart entre deux sections (ou deux blocs) côte à côte, en cm.
- `beside_space` : L'espace après des sections côte à côte ou encadrées, en points.
- `cover_margin_top` : Le haut d'une page de garde sans `margin_top`, en cm.
- `header` : Ce qu'écrit l'en-tête : du contenu, pas un style.
- `footer` : Ce qu'écrit le pied.
- `watermark` : Le texte du filigrane, 40 caractères au plus.

## Fonction `loom_ia.tools_builtin._style_params._tax_label`

```python
def _tax_label(label: str) -> str
```

Le libellé d'une ligne de TVA : {rate} (le taux), et au besoin {base} (sa base).

## Classe `loom_ia.tools_builtin._style_params.LabelsSpec`

```python
class LabelsSpec(_Spec)
```

Les libellés que l'outil écrit, s'ils diffèrent de ceux de la langue.

**Attributs :**

- `table` : Légende d'un tableau (« Tableau »).
- `figure` : Légende d'une figure (« Figure »).
- `contents` : Titre du sommaire (« Sommaire »).
- `date` : Libellé de la date d'une signature (« Date : »).
- `signature` : Libellé de la signature (« Signature : »).
- `article` : Numérotation par articles (« Article »).
- `total` : Ligne de total (« Total »).
- `notes` : Titre des notes (« Notes »).
- `subtotal` : Ligne de sous-total (« Sous-total »).
- `total_excl_tax` : Total hors taxes (« Total HT »).
- `tax` : Ligne de TVA : `{rate}` (le taux), et au besoin `{base}` (sa base) — « TVA {rate} sur {base} ».
- `total_incl_tax` : Total toutes taxes (« Total TTC »).
- `paid` : Acompte (« Acompte versé »).
- `due` : Reste à payer (« Net à payer »).
- `participant` : Colonne de la feuille d'émargement (« Participant »).

## Classe `loom_ia.tools_builtin._style_params.ImageSpec`

```python
class ImageSpec(_Spec)
```

Les images d'un fichier de style.

**Attributs :**

- `side_by_side` : Deux images `half` qui se suivent : côte à côte.
- `space_after` : L'espace après une image sans légende, en points.

## Classe `loom_ia.tools_builtin._style_params.FontFiles`

```python
class FontFiles(_Spec)
```

Les fichiers TrueType d'une police, relatifs à `resources_dir` ; leurs octets une fois lus au chargement.

`regular` seul suffit : le gras et l'italique le prennent.

## Classe `loom_ia.tools_builtin._style_params.ChartSpec`

```python
class ChartSpec(_Spec)
```

Les graphiques d'un fichier de style.

**Attributs :**

- `series` : La couleur de chaque série, dans l'ordre (12 au plus).
- `text_color` : La couleur du texte.
- `grid_color` : La couleur de la grille.
- `title_color` : La couleur du titre.
- `space_after` : L'espace après un graphique sans légende, en points.

## Classe `loom_ia.tools_builtin._style_params.StyleFile`

```python
class StyleFile(_Spec)
```

Un fichier de style lu : ses réglages et ses styles nommés.

### Méthode `loom_ia.tools_builtin._style_params.StyleFile._named`

```python
@model_validator(mode='before')
@classmethod
def _named(cls, value: object) -> object
```

Le fichier tel qu'écrit : les réglages, puis tout autre nom est un style.

## Fonction `loom_ia.tools_builtin._style_params._moved_content`

```python
def _moved_content(name: str, given: object) -> str
```

Ce qu'un style `header`, `footer` ou `watermark` écrit encore de son contenu, avec sa nouvelle place ; `""` s'il n'en écrit pas.

## Fonction `loom_ia.tools_builtin._style_params._old_fonts`

```python
def _old_fonts(value: object) -> bool
```

`fonts` au format de la 2.0 : `body` et `headings`, un nom ou `{name, size}`.

## Fonction `loom_ia.tools_builtin._style_params.describe`

```python
def describe(error: ValidationError, where: str) -> list[str]
```

Les fautes d'un fichier de style, une par ligne : « <where>.heading1.colour — … », ou « <where> : … » pour une faute de tout le fichier.

## Fonction `loom_ia.tools_builtin._style_params.load_style`

```python
def load_style(text: str) -> StyleFile
```

Le texte d'un fichier de style, lu ; `yaml.YAMLError` ou `ValidationError`.

## Fonction `loom_ia.tools_builtin._style_params.default_style`

```python
@cache
def default_style() -> StyleFile
```

`styles/default.yaml` : le style maison, livré avec loom.

## Classe `loom_ia.tools_builtin._style_params.StyleRef`

```python
class StyleRef(BaseModel)
```

Un style : son fichier (relatif à `resources_dir`), ou son contenu.

Le chargeur de config lit le fichier (et ses logos) et range le style dans
`spec` ; `file` n'est alors plus gardé. En config : `style: styles/charte.yaml`,
ou le style écrit en ligne (`style: { heading1: { color: 0B5D3B } }`).

## Fonction `loom_ia.tools_builtin._style_params.short_style`

```python
def short_style(value: object) -> object
```

`style: fichier.yaml` (un chemin), ou un style écrit en ligne (une table).

## Fonction `loom_ia.tools_builtin._style_params.logos`

```python
def logos(spec: StyleFile) -> list[tuple[str, str | bytes]]
```

Les logos d'un fichier de style : (où, fichier ou octets) ; `page.header`, `page.footer`.

## Fonction `loom_ia.tools_builtin._style_params.font_files`

```python
def font_files(spec: StyleFile) -> list[tuple[str, str, str | bytes]]
```

Les fichiers des polices d'un fichier de style : (famille, variante, fichier ou octets).

## Fonction `loom_ia.tools_builtin._style_params.font_fault`

```python
def font_fault(data: bytes) -> str | None
```

Ce qui empêche d'embarquer une police dans un PDF ; `None` si rien.

Une police TrueType (contours `glyf`), seule dans son fichier, dont la licence permet
l'embarquement, en sous-ensemble, pour un document qu'on peut modifier : `fsType` 0
(installable) ou 8 (modifiable). Une police « aperçu et impression » (4) est refusée :
un lecteur s'en servirait sans qu'on puisse modifier le document.

## Classe `loom_ia.tools_builtin._style_params.BandElement`

```python
@dataclass(frozen=True, slots=True)
class BandElement
```

Un élément résolu d'un en-tête ou d'un pied : une zone, une ligne, une cellule.

Ses lignes (une seule pour une zone ou une ligne), le style qui le met en forme
(`None` : celui de l'en-tête, du pied, ou de sa table), s'il porte le logo.

### Méthode `loom_ia.tools_builtin._style_params.BandElement.text`

```python
@property
def text(self) -> str
```

Le texte de l'élément : sa première ligne.

## Classe `loom_ia.tools_builtin._style_params.Share`

```python
@dataclass(frozen=True, slots=True)
class Share
```

Une largeur en proportion de la largeur de la table, en pour cent.

## Classe `loom_ia.tools_builtin._style_params.BandTable`

```python
@dataclass(frozen=True, slots=True)
class BandTable
```

La table résolue d'un en-tête ou d'un pied : les largeurs en cm (`None` : auto).

**Attributs :**

- `widths` : Largeur de chaque colonne : en cm, en proportion de la largeur de la table (`Share`), ou `None` (auto).
- `rows` : Ses lignes, chacune un élément par colonne.
- `style` : Le style qui l'habille (cadre, filets, écart entre colonnes) : `None`, celui de l'en-tête ou du pied.

## Classe `loom_ia.tools_builtin._style_params.BandRow`

```python
@dataclass(frozen=True, slots=True)
class BandRow
```

Une ligne de l'en-tête ou du pied en mode table : une ligne de la table, ou une des `lines`, sur toute la largeur (`line`).

## Fonction `loom_ia.tools_builtin._style_params._band`

```python
def _band(given: Mapping[str, Any]) -> Band | None
```

Ce qu'écrit un en-tête ou un pied (`page.header`, `page.footer`, fusionnés) ; `None` s'il n'écrit rien.

## Fonction `loom_ia.tools_builtin._style_params._fixed`

```python
def _fixed(width: float | Share, available: int) -> int
```

Une largeur de colonne en twips : en cm, ou en proportion de `available`.

## Fonction `loom_ia.tools_builtin._style_params.band_columns`

```python
def band_columns(table: BandTable, available: int) -> list[int]
```

La largeur de chaque colonne, en twips, pour `available` twips.

Les colonnes en cm ou en proportion, puis le reste partagé entre les `auto` (la
dernière prend l'arrondi).

## Fonction `loom_ia.tools_builtin._style_params.band_frame`

```python
def band_frame(look: Look) -> tuple[tuple[str | None, str | None, str | None, str | None], str | None]
```

Le cadre d'un en-tête ou d'un pied en mode table, autour de la table.

Ses filets (haut, bas, gauche, droite ; `None` sans filet), et son fond (`None`
sans fond).

## Fonction `loom_ia.tools_builtin._style_params.band_rows`

```python
def band_rows(band: Band, *, header: bool) -> list[BandRow]
```

Les lignes d'un en-tête ou d'un pied en mode table, du haut vers le bas.

La table, puis les `lines` dans un en-tête ; les `lines`, puis la table dans un
pied.

## Classe `loom_ia.tools_builtin._style_params.Look`

```python
@dataclass(frozen=True, slots=True)
class Look
```

Un style résolu : ses propriétés, héritées de sa base puis les siennes ; `None` pour ce que rien ne règle (la valeur de Word).

## Classe `loom_ia.tools_builtin._style_params.Labels`

```python
@dataclass(frozen=True, slots=True)
class Labels
```

Les libellés que l'outil écrit dans le document, et sa langue.

## Classe `loom_ia.tools_builtin._style_params.Logo`

```python
@dataclass(frozen=True, slots=True)
class Logo
```

Le logo d'un en-tête ou d'un pied, lu.

**Attributs :**

- `data` : Ses octets (PNG ou JPEG).
- `width` : Largeur dans la page, en cm.
- `gap` : L'écart à son texte, en points.

## Classe `loom_ia.tools_builtin._style_params.Band`

```python
@dataclass(frozen=True, slots=True)
class Band
```

Ce qu'écrit un en-tête ou un pied : trois zones, ou une table ; des lignes ; le logo, dans l'élément qui le demande.

**Attributs :**

- `left` : Zone de gauche.
- `center` : Zone du centre.
- `right` : Zone de droite.
- `lines` : Lignes de toute la largeur.
- `logo` : Le logo, s'il est lu.
- `first_page` : Écrit aussi sur la première page.
- `table` : La table, à la place des trois zones.
- `logo_given` : Un logo donné, même non lu (`logos` le dit au chargeur) : pour vérifier sa place.

### Méthode `loom_ia.tools_builtin._style_params.Band.zones`

```python
@property
def zones(self) -> dict[str, BandElement | None]
```

Les trois zones, par place.

### Méthode `loom_ia.tools_builtin._style_params.Band.elements`

```python
def elements(self) -> list[BandElement]
```

Tous ses éléments : zones, lignes, cellules.

### Méthode `loom_ia.tools_builtin._style_params.Band.empty`

```python
@property
def empty(self) -> bool
```

Vrai si l'en-tête ou le pied n'écrit rien.

## Classe `loom_ia.tools_builtin._style_params.ChartLook`

```python
@dataclass(frozen=True, slots=True)
class ChartLook
```

Les couleurs des graphiques : les séries, le texte, la grille, le titre ; l'espace après un graphique sans légende, en points.

## Classe `loom_ia.tools_builtin._style_params.PageLook`

```python
@dataclass(frozen=True, slots=True)
class PageLook
```

Les distances de la page, en twips (`cm_twips`).

L'en-tête et le pied au bord, l'écart entre les colonnes d'une section et entre deux
sections côte à côte, le haut d'une page de garde sans `margin_top` ; en points,
l'espace après des sections côte à côte ou encadrées et après une image sans
légende.

## Classe `loom_ia.tools_builtin._style_params.Style`

```python
@dataclass(frozen=True, slots=True)
class Style
```

Un style complet, prêt à rendre : chaque style nommé résolu, et les réglages.

**Attributs :**

- `looks` : Chaque style nommé, résolu (bases suivies).
- `declared` : Ce que chaque style écrit lui-même (les fichiers fusionnés, couleurs résolues) : un style Word reprend son style de base, et n'écrit que ce qui lui est propre.
- `bases` : Le style de base de chaque style nommé.
- `distances` : Les distances de la page (`page` et `image`, dans default.yaml).
- `page` : Format de page, en twips (largeur, hauteur en portrait).
- `landscape` : Page en paysage.
- `margins` : Haut, bas, gauche, droite, en twips.
- `numbering` : Numérotation des titres.
- `labels` : Les libellés, et la langue.
- `image_grid` : Deux images `half` qui se suivent : côte à côte.
- `chart` : Les couleurs et l'espacement des graphiques.
- `fonts` : Les fichiers TrueType des polices, par famille puis variante (`regular`…) : le PDF les embarque.
- `header_band` : Ce qu'écrit l'en-tête (`page.header`) ; `None` sans rien.
- `footer_band` : Ce qu'écrit le pied (`page.footer`) ; `None` sans rien.
- `watermark_text` : Le filigrane (`page.watermark`) ; `None` sans rien.

### Méthode `loom_ia.tools_builtin._style_params.Style.language`

```python
@property
def language(self) -> Language
```

La langue des libellés.

### Méthode `loom_ia.tools_builtin._style_params.Style.text_width`

```python
@property
def text_width(self) -> int
```

La largeur utile d'une page, en twips (couchée si le style l'est).

### Méthode `loom_ia.tools_builtin._style_params.Style.look`

```python
def look(self, name: str) -> Look
```

Un style nommé, résolu ; `KeyError` s'il n'existe pas.

### Méthode `loom_ia.tools_builtin._style_params.Style.chain`

```python
def chain(self, name: str) -> list[str]
```

Le style et ses bases, du plus précis à `body`.

### Méthode `loom_ia.tools_builtin._style_params.Style.between`

```python
def between(self, name: str, stop: str | None) -> dict[str, Any]
```

Ce que `name` écrit en plus de `stop` : les propriétés déclarées par `name` et ses bases jusqu'à `stop` (exclu), la plus précise gagnant.

Une propriété négative (`false`, `none`) que `stop` a déjà n'y est pas.

### Méthode `loom_ia.tools_builtin._style_params.Style.block_type`

```python
def block_type(self, name: str) -> tuple[str, int] | None
```

Le type de bloc d'un style et, pour un titre, son niveau.

(« note », 0), (« heading », 2) ; `None` pour un style qui ne part d'aucun
style de bloc.

### Méthode `loom_ia.tools_builtin._style_params.Style.proposed`

```python
def proposed(self) -> dict[str, list[tuple[str, str]]]
```

Les styles proposés au modèle de langage, par type de bloc : (nom, description).

### Méthode `loom_ia.tools_builtin._style_params.Style.element_fault`

```python
def element_fault(self, element: str, name: str) -> str | None
```

Ce qui empêche `name` d'être le style de `element` (`styles` d'une section) : un style inconnu, ou qui ne part pas de l'élément.

### Méthode `loom_ia.tools_builtin._style_params.Style.table_level`

```python
def table_level(self, name: str) -> bool
```

Un style de tableau (`table`, `invoice_totals`, un style à toi qui en part), et non d'une de ses lignes (`table_header`, `table_group`…).

Son espace avant et après vaut autour du tableau.

### Méthode `loom_ia.tools_builtin._style_params.Style.cell_spacing`

```python
def cell_spacing(self, name: str | None, table: str, family: str) -> tuple[float, float]
```

L'espace avant et après des paragraphes d'une cellule, en points.

Ce que son style (`name`) écrit en plus de celui de la table (`table`, ou
`family` qu'ils partagent : `table`, `header`, `footer`) ; 0 sans lui.
L'espacement du style de la table vaut autour d'elle, pas dans ses cellules.

### Méthode `loom_ia.tools_builtin._style_params.Style.band`

```python
def band(self, name: Literal['header', 'footer']) -> Band | None
```

L'en-tête ou le pied de page ; `None` s'il n'écrit rien.

### Méthode `loom_ia.tools_builtin._style_params.Style.element_look`

```python
def element_look(self, element: BandElement, base: str) -> Look
```

Le style d'un élément d'en-tête ou de pied : le sien, sinon `base`.

### Méthode `loom_ia.tools_builtin._style_params.Style.header`

```python
@property
def header(self) -> Band | None
```

Ce qu'écrit l'en-tête ; `None` sans rien.

### Méthode `loom_ia.tools_builtin._style_params.Style.footer`

```python
@property
def footer(self) -> Band | None
```

Ce qu'écrit le pied ; `None` sans rien.

### Méthode `loom_ia.tools_builtin._style_params.Style.watermark`

```python
@property
def watermark(self) -> str | None
```

Le filigrane ; `None` sans rien.

## Fonction `loom_ia.tools_builtin._style_params.resolve`

```python
def resolve(*files: StyleFile | None) -> Style
```

Le style complet : `default.yaml`, puis chaque fichier donné, propriété par propriété ; puis l'héritage.

`ValueError` qui dit la faute (couleur inconnue, base inconnue ou en boucle,
propriété hors de son style).

## Fonction `loom_ia.tools_builtin._style_params._check_bands`

```python
def _check_bands(style: Style, faults: list[str]) -> None
```

L'en-tête et le pied, dans le style résolu (tous les fichiers lus).

Une table sans les trois zones ; le logo dans un seul élément ; le style de chaque
élément et de la table, qui part de `header` ou de `footer` ; les largeurs de la
table.

## Fonction `loom_ia.tools_builtin._style_params._named_colors`

```python
def _named_colors(name: str, prop: str, value: Any, colors: Mapping[str, str], faults: list[str]) -> Any
```

Une couleur nommée remplacée par sa valeur ; une inconnue, dite.

## Fonction `loom_ia.tools_builtin._style_params._bases`

```python
def _bases(declared: Mapping[str, Mapping[str, Any]], faults: list[str]) -> dict[str, str | None]
```

Le style de base de chaque style, déclaré ou par défaut ; une base inconnue ou une boucle est notée dans `faults` et ramenée à `body`.

## Fonction `loom_ia.tools_builtin._style_params._check_owners`

```python
def _check_owners(declared: Mapping[str, Mapping[str, Any]], bases: Mapping[str, str | None], faults: list[str]) -> None
```

Une propriété propre (`borders`, `logo`…) ne va qu'à son style et ses dérivés.

## Fonction `loom_ia.tools_builtin._style_params._check_descriptions`

```python
def _check_descriptions(declared: Mapping[str, Mapping[str, Any]], bases: Mapping[str, str | None], faults: list[str]) -> None
```

Une description ne va qu'à un style à toi qui part d'un style de bloc.

## Fonction `loom_ia.tools_builtin._style_params._look`

```python
def _look(name: str, declared: Mapping[str, Mapping[str, Any]], bases: Mapping[str, str | None], looks: dict[str, Look]) -> Look
```

Un style nommé résolu : les propriétés de sa base (résolue d'abord), puis les siennes.

## Fonction `loom_ia.tools_builtin._style_params._page`

```python
def _page(given: Iterable[StyleFile]) -> dict[str, Any]
```

La page fusionnée des fichiers de style, dans l'ordre.

`margins` vaut pour les quatre côtés ; l'en-tête et le pied se fusionnent clé par
clé, `null` efface.

## Fonction `loom_ia.tools_builtin._style_params._chart`

```python
def _chart(given: Iterable[StyleFile], colors: Mapping[str, str], faults: list[str]) -> ChartLook
```

Les réglages des graphiques fusionnés des fichiers de style, couleurs nommées résolues.

## Fonction `loom_ia.tools_builtin._style_params.unsafe_fonts`

```python
def unsafe_fonts(style: Style) -> list[str]
```

Les polices du style hors de la liste sûre : remplacées chez qui ne les a pas.

## Fonction `loom_ia.tools_builtin._style_params.parse_rule`

```python
def parse_rule(value: str | None) -> tuple[float, str] | None
```

Un filet résolu : (épaisseur en points, couleur) ; `None` sans filet.

## Fonction `loom_ia.tools_builtin._style_params.house`

```python
@cache
def house() -> Style
```

Le style maison : `default.yaml` seul.
