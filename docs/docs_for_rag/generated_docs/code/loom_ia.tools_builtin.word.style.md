# loom_ia.tools_builtin.word.style

Module `loom_ia.tools_builtin.word.style` de loom_v2 (fichier `src/loom_ia/tools_builtin/word/style.py`).

Le style d'un .docx en WordprocessingML : ses styles, ses numérotations, ses en-têtes.

Tout part d'un `Style` résolu (`_style_params`) : `default.yaml`, la charte de
l'outil, le style du modèle suivi. Chaque style nommé devient un style Word, basé sur
le style Word de sa base, qui n'écrit que ce qui lui est propre : Word reprend le reste
de sa base. Sans charte, ce que donne `default.yaml` est le même, octet pour octet,
qu'avant les fichiers de style : `styles.xml`, `numbering.xml` et `footer1.xml`
d'un document sans style ne changent pas.

Ce module ne fixe aucune valeur visuelle : il traduit les propriétés des styles
(police, taille, couleur, espacements, filets) en éléments WordprocessingML, dans
l'ordre du schéma, et ajoute la mécanique (un titre reste avec ce qui le suit, son
niveau de plan, la numérotation des titres, les retraits des listes).

Un style à toi que le document emploie (un modèle le choisit pour une section, ou le
modèle de langage pour un bloc) devient un style Word du même nom, basé sur le style
Word de sa base : il paraît dans la galerie de Word. Les styles que seule une
disposition demande (en-tête, tableau de disposition) ne sont écrits que si le document
s'en sert.

## Constantes de `loom_ia.tools_builtin.word.style`

- `HEADING_NUMBERING = 900` : La numérotation des titres (réglage `numbering`), à part des listes.
- `HEADER = 'Header'` : Les styles que seule une disposition demande, par identifiant.
- `LAYOUT = 'LoomLayout'` : Identifiant du style de tableau de disposition.
- `LAYOUT_NAME = 'loom layout'` : Le nom du style de tableau de disposition : la relecture le reconnaît.
- `SIGNATURE = 'LoomSignature'` : Les blocs signature et toc, et la liste à cocher : leurs styles, que la relecture reconnaît par leur nom.
- `SIGNATURE_NAME = 'loom signature'` : Nom du style de la signature.
- `SIGNER = 'LoomSigner'` : Identifiant du style d'un signataire.
- `SIGNER_NAME = 'loom signer'` : Nom du style d'un signataire.
- `MENTION = 'LoomMention'` : Identifiant du style de la mention.
- `MENTION_NAME = 'loom mention'` : Nom du style de la mention.
- `CHECK = 'LoomCheck'` : Identifiant du style d'un élément à cocher.
- `CHECK_2 = 'LoomCheck2'` : Identifiant du style d'un élément à cocher de second niveau.
- `CHECK_NAMES = {CHECK: 'loom checkbox', CHECK_2: 'loom checkbox 2'}` : Noms des styles des éléments à cocher.
- `CHECK_FONT = 'Segoe UI Symbol'` : La case : un glyphe, et la police de symboles de Windows qui l'a (LibreOffice en prend une autre qui l'a). Une mécanique, comme les puces : pas une valeur de charte.
- `CHECKED = '☒'` : Case cochée (☒).
- `UNCHECKED = '☐'` : Case vide (☐).
- `CODE = 'LoomCode'` : Le sommaire : les styles intégrés de Word (« TOC Heading », « toc 1 »…), qu'il reprend à la mise à jour du champ. Le bloc code, les champs (un tableau sans bordure, son libellé, sa valeur), le lien, la note de bas de page (son texte, son appel).
- `FIELDS = 'LoomFields'` : Identifiant du style de tableau des champs.
- `FIELDS_NAME = 'loom fields'` : Nom du style de tableau des champs.
- `FIELD_LABEL = 'LoomFieldLabel'` : Identifiant du style d'un libellé de champ.
- `FIELD_VALUE = 'LoomFieldValue'` : Identifiant du style d'une valeur de champ.
- `HYPERLINK = 'Hyperlink'` : Identifiant du style intégré d'un lien.
- `FOOTNOTE_TEXT = 'FootnoteText'` : Identifiant du style intégré du texte d'une note.
- `FOOTNOTE_REFERENCE = 'FootnoteReference'` : Identifiant du style intégré de l'appel d'une note.
- `TABLE_GROUP = 'LoomTableGroup'` : Les lignes d'un groupe et de son sous-total, et le récapitulatif de TVA : un style de paragraphe à leur nom, que la relecture reconnaît.
- `TABLE_SUBTOTAL = 'LoomTableSubtotal'` : Identifiant du style d'une ligne de sous-total.
- `INVOICE_TOTALS = 'LoomInvoiceTotals'` : Identifiant du style du récapitulatif de TVA.
- `ATTENDANCE = 'LoomAttendance'` : La feuille d'émargement : le style de paragraphe de ses cellules.
- `TOC_HEADING = 'TOCHeading'` : Identifiant du style intégré du titre du sommaire.
- `TOC_LEVELS = ('TOC1', 'TOC2', 'TOC3')` : Identifiants des styles intégrés des niveaux du sommaire.
- `BODY = 'BodyText'` : Le corps du texte, quand le style `paragraph` dit autre chose que `text`.
- `LANGUAGES = {'fr': 'fr-FR', 'en': 'en-US'}` : La langue déclarée du document (correcteur, césure).
- `_PPR = ('pStyle', 'keepNext', 'keepLines', 'pageBreakBefore', 'numPr', 'pBdr', 'shd', 'tabs', 'spacing', 'ind', 'jc', 'outline…` : L'ordre des enfants de `pPr` et de `rPr` que le schéma impose.
- `_SIDE_OF = {'top': 0, 'right': 1, 'bottom': 2, 'left': 3}` : Les côtés d'un filet de paragraphe, et leur place dans `border_space` (l'ordre de CSS).
- `_DEFAULT_RUN = frozenset({'font', 'color', 'size'})` : Ce que `text` met dans les valeurs par défaut du document, plutôt que dans Normal.
- `Pieces = list[tuple[str, str]]` : Propriétés en XML, dans l'ordre du schéma : (nom de l'élément, son XML).
- `_CELL_PROPS = frozenset({'border', 'border_top', 'border_bottom', 'border_left', 'border_right', 'fill'})` : Ce qu'un style de tableau dit du tableau et de ses cellules, et pas de leur texte.
- `WORD_IDS = {'body': 'Normal', 'document_title': 'Title', 'heading1': 'Heading1', 'heading2': 'Heading2', 'heading3': 'Heading3', '…` : Le style Word de chaque style prévu qui en a un (un paragraphe) : la base Word d'un style à toi qui en part.
- `_LEVELS = {False: (('bullet', '•'), ('bullet', '–'), ('bullet', '▪')), True: (('decimal', '%1.'), ('lowerLetter', '%2)'), ('lower…` : Puces et numéros : deux niveaux, la sous-liste en retrait ; un troisième pour une liste qui en a un (une définition à part : les documents à deux niveaux restent les mêmes).
- `_DEEP = 3` : Les définitions à trois niveaux : à puces, numérotée.
- `_BAND_END = 20` : La hauteur du paragraphe qui clôt un en-tête ou un pied après sa table : un point.

## Classe `loom_ia.tools_builtin.word.style.Watermark`

```python
@dataclass(frozen=True, slots=True)
class Watermark
```

Le filigrane : son texte, sa police, sa couleur.

## Fonction `loom_ia.tools_builtin.word.style.watermark`

```python
def watermark(style: Style) -> Watermark | None
```

Le filigrane : son texte (`page.watermark`), la police et la couleur du style `watermark` ; `None` sans texte.

## Fonction `loom_ia.tools_builtin.word.style.jc`

```python
def jc(align: str) -> str
```

L'alignement WordprocessingML d'un alignement de style (« justify » → « both »).

## Fonction `loom_ia.tools_builtin.word.style.half_points`

```python
def half_points(size: float) -> int
```

Une taille en demi-points, l'unité de Word.

## Fonction `loom_ia.tools_builtin.word.style.twips`

```python
def twips(points: float) -> int
```

Une longueur en twips (1/20 de point), l'unité de Word.

## Fonction `loom_ia.tools_builtin.word.style.fonts`

```python
def fonts(name: str) -> str
```

La police d'un texte en XML, pour toutes les écritures.

## Fonction `loom_ia.tools_builtin.word.style.run_pieces`

```python
def run_pieces(props: Mapping[str, Any]) -> Pieces
```

Les propriétés de caractère d'un style : police, gras, italique, majuscules, couleur, taille, soulignement.

## Fonction `loom_ia.tools_builtin.word.style._side`

```python
def _side(side: str, value: str, space: Sides | None) -> str
```

Un filet de paragraphe ; son écart au texte, `border_space` de son côté, en points entiers (Word n'en écrit pas d'autres).

## Fonction `loom_ia.tools_builtin.word.style.paragraph_pieces`

```python
def paragraph_pieces(props: Mapping[str, Any], space: Sides | None=None) -> Pieces
```

Les propriétés de paragraphe d'un style : filets (à `space` du texte, le `border_space` du style résolu), fond, espacements, retrait, alignement.

## Fonction `loom_ia.tools_builtin.word.style.ordered`

```python
def ordered(pieces: Pieces, order: tuple[str, ...]) -> str
```

Les éléments dans l'ordre du schéma (un tri stable : les mêmes, dans l'ordre donné).

## Fonction `loom_ia.tools_builtin.word.style.run_xml`

```python
def run_xml(props: Mapping[str, Any]) -> str
```

Les propriétés de caractère d'un style en XML, dans l'ordre du schéma.

## Fonction `loom_ia.tools_builtin.word.style._style`

```python
def _style(style_id: str, name: str, *, kind: str='paragraph', based: str | None='Normal', next_style: str | None=None, paragraph: str='', run: str='', default: bool=False) -> str
```

Un style Word en XML : type, identifiant, nom, base, style suivant, propriétés de paragraphe et de caractère.

## Fonction `loom_ia.tools_builtin.word.style._named`

```python
def _named(style: Style, style_id: str, word_name: str, loom: str, *, based: str='Normal', stop: str='body', next_style: str | None=None, mechanics: Pieces | None=None) -> str
```

Un style Word tiré d'un style nommé : ce qu'il écrit en plus de `stop`, le style loom de son style Word de base, et la mécanique (`mechanics`).

Les filets et le fond d'un tableau sont ceux du tableau et de ses cellules, pas du
texte d'une cellule.

## Fonction `loom_ia.tools_builtin.word.style._heading`

```python
def _heading(style: Style, level: int) -> str
```

Le style d'un titre de niveau `level`, numéroté si le style numérote les titres.

## Fonction `loom_ia.tools_builtin.word.style.paragraph_style`

```python
def paragraph_style(style: Style) -> str
```

Le style Word des paragraphes : Normal, ou « Body Text » si `paragraph` le règle.

## Fonction `loom_ia.tools_builtin.word.style.word_id`

```python
def word_id(style: Style, name: str) -> str
```

Le style Word d'un style nommé.

Celui d'un style prévu, ou son nom pour un style à toi (`paragraph` : Normal, ou «
Body Text » s'il dit autre chose que `text`).

## Fonction `loom_ia.tools_builtin.word.style._custom`

```python
def _custom(style: Style, used: Iterable[str]) -> str
```

Les styles à toi que le document emploie, et leurs bases à toi : chacun basé sur le style Word de sa base, n'écrivant que ce qui lui est propre.

## Fonction `loom_ia.tools_builtin.word.style.styles_xml`

```python
def styles_xml(style: Style, used: Iterable[str]=(), custom: Iterable[str]=()) -> str
```

`styles.xml` : un style Word par style nommé que l'outil emploie, et ceux que le corps demande ; `custom` : les styles à toi qu'il emploie.

## Fonction `loom_ia.tools_builtin.word.style._character`

```python
def _character(style_id: str, name: str, run: str) -> str
```

Un style de caractère : le lien, l'appel de note.

## Fonction `loom_ia.tools_builtin.word.style._extra_styles`

```python
def _extra_styles(style: Style, wanted: set[str]) -> str
```

Les styles des blocs facultatifs que le document emploie.

Ceux des blocs signature, toc, code et champs, de la liste à cocher, du lien et des
notes, des groupes de tableau, du récapitulatif de TVA et de l'émargement.

## Fonction `loom_ia.tools_builtin.word.style._toc_entry`

```python
def _toc_entry(style: Style, style_id: str, level: int, step: int) -> str
```

Une entrée du sommaire (« toc 1 » à « toc 3 »).

Le style `toc1` à `toc3`, à gauche, en retrait de `toc.level_indent` par
niveau (et de son `indent`).

## Fonction `loom_ia.tools_builtin.word.style._heading_texts`

```python
def _heading_texts(numbering: Numbering, article: str) -> tuple[str, str, str]
```

Le numéro de chaque niveau de titre : « 1. », ou « Article 1 » et un tiret (`labels.article`).

## Fonction `loom_ia.tools_builtin.word.style._abstract`

```python
def _abstract(number: int, ordered: bool, indent: tuple[int, int], depth: int=2) -> str
```

Une numérotation de liste.

Chaque niveau en retrait de `indent[0]` (twips), la puce ou le numéro
`indent[1]` avant le texte (`list.level_indent`, `marker_space`).

## Fonction `loom_ia.tools_builtin.word.style._heading_abstract`

```python
def _heading_abstract(numbering: Numbering, article: str) -> str
```

Les titres numérotés : un numéro suivi d'une espace, lié aux styles de titre.

## Fonction `loom_ia.tools_builtin.word.style.numbering_xml`

```python
def numbering_xml(lists: Iterable[tuple[bool, bool]], indent: tuple[int, int], headings: Numbering='none', article: str='Article') -> str
```

Une numérotation par liste (numérotée ?, à trois niveaux ?) : chaque liste numérotée repart de 1.

Les définitions (`abstractNum`) précèdent toutes les numérotations (`num`) :
Word l'exige.

## Fonction `loom_ia.tools_builtin.word.style._text`

```python
def _text(text: str, title: str, run: str='') -> str
```

Un texte d'en-tête.

`{page}` et `{pages}` en champs, `{title}` le titre du document, le reste en
segments ; `run` : leurs propriétés de caractère (le style d'une zone).

## Fonction `loom_ia.tools_builtin.word.style._logo_gap`

```python
def _logo_gap(band: Band | None) -> str
```

L'écart entre le logo et son texte (`logo.gap`).

Une espace insécable d'un point, suivie de cet espacement de caractères — ce que
Word et LibreOffice font tous deux (LibreOffice ignore l'espacement d'un caractère
sans largeur, et `effectExtent`).

## Fonction `loom_ia.tools_builtin.word.style._with_logo`

```python
def _with_logo(logo: str, band: Band | None, text: str) -> str
```

Le logo, et son écart s'il est suivi d'un texte.

## Fonction `loom_ia.tools_builtin.word.style.band_xml`

```python
def band_xml(kind: str, band: Band | None, *, title: str, width: int, logo: str='', watermark: Watermark | None=None, style: Style | None=None, word: Callable[[str], str] | None=None) -> str
```

Un en-tête (`hdr`) ou un pied de page (`ftr`).

`logo` : le segment qui porte le logo, déjà écrit (sa relation est propre à la
partie). Trois zones sur une ligne — gauche, centre, droite, par des taquets —, et
des lignes centrées : sous la ligne des zones dans un en-tête, au-dessus dans un
pied de page (les mentions, puis le numéro de page). Avec une table (`table`), la
table remplace la ligne des zones, et les lignes y deviennent des lignes de toute la
largeur (`_band_table`) ; `style` : le style résolu, `word` : le style Word d'un
style nommé (un style de cellule à toi y est écrit).

## Fonction `loom_ia.tools_builtin.word.style._cell_paragraph`

```python
def _cell_paragraph(style_id: str, look: Look, align: str | None, content: str, spacing: tuple[float, float]) -> str
```

Un paragraphe d'une cellule.

Le style de l'en-tête, du pied ou de la cellule, sans les filets, le fond ni le
retrait du style, que porte la table ; son espace avant et après, `spacing`
(`Style.cell_spacing`), s'il n'est pas celui de son style.

## Fonction `loom_ia.tools_builtin.word.style._band_table`

```python
def _band_table(kind: str, band: Band, style: Style, *, title: str, width: int, logo: str, word: Callable[[str], str] | None) -> str
```

La table d'un en-tête ou d'un pied : ses lignes, et les `lines` en lignes de toute la largeur (`band_rows`).

Le cadre et le fond du style vont sur la table, l'écart au texte en marges des
cellules du bord ; l'écart entre colonnes (`gap`) en deux moitiés, de part et
d'autre du filet intérieur.

## Fonction `loom_ia.tools_builtin.word.style._band_space`

```python
def _band_space(kind: str, style: Style, height: int, *, before: int=0, mark: str='') -> str
```

Un paragraphe vide d'un en-tête ou d'un pied, haut de `height` twips, sans filet ni fond.

L'espace avant une table, ou le paragraphe qui la clôt (Word en veut un).

## Fonction `loom_ia.tools_builtin.word.style._band_end`

```python
def _band_end(kind: str, style: Style, watermark: Watermark | None, *, before: int=0) -> str
```

Le paragraphe qui clôt un en-tête ou un pied après sa table.

Haut d'un point, à `before` twips d'elle (l'espace après du style de la table) ;
il porte le filigrane.

## Fonction `loom_ia.tools_builtin.word.style._watermark`

```python
def _watermark(watermark: Watermark) -> str
```

Le filigrane : un texte en diagonale, derrière le texte de chaque page (le style `watermark` : sa police et sa couleur, à demi transparente).

La forme VML qu'écrit Word lui-même (« PowerPlusWaterMarkObject ») ; LibreOffice la
lit et l'imprime.
