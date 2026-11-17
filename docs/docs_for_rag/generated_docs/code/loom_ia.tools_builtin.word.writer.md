# loom_ia.tools_builtin.word.writer

Module `loom_ia.tools_builtin.word.writer` de loom_v2 (fichier `src/loom_ia/tools_builtin/word/writer.py`).

Écrire un .docx : le document validé posé dans son style, en WordprocessingML.

Sans bibliothèque : un .docx est un zip de quelques parties XML, que ce module
écrit lui-même. Le style vient de fichiers (`_style_params`) : `default.yaml`,
la charte de l'outil, le style du modèle suivi ; ce module n'a aucune valeur
visuelle, seulement la mécanique (marges intérieures des cellules, écarts entre
colonnes, place pour signer) et les règles de composition (nombres à droite,
en-tête de tableau répété, ligne jamais coupée). Le document est dans la langue
du style (correcteur, césure, libellés). Une image est une partie `word/media`
rangée telle quelle, une seule fois par image distincte (`word/image.py`).

Un document qui suit un modèle s'écrit **section par section**, dans l'ordre du modèle :
le titre de la section (sauf `show_title: false`), puis ses blocs ; son premier
paragraphe porte un signet caché (`_loom_s<n>`, n sa place dans le modèle) qui marque
son début, et que la relecture retrouve. Le signet est un point, pas une étendue, et
toujours dans un paragraphe (celui de la première cellule quand la section commence par
un tableau) : LibreOffice déplace un signet posé hors d'un paragraphe et dédouble les
fins de ceux qui couvrent plusieurs paragraphes. La
section choisit ses styles (`styles`) et sa mise en page : à côté de la précédente
(un tableau sans bordure, style « loom layout », que la relecture traverse), encadrée,
en colonnes, en paysage, ou page de garde. Paysage et colonnes sont des sections Word :
un saut de section les ouvre et les ferme.

Deux documents identiques donnent le même fichier, octet pour octet : les
dates des propriétés et de l'archive sont fixées, et rien ne dépend de
l'heure ni de la machine. L'ordre des éléments suit le schéma
WordprocessingML, que Word, contrairement à LibreOffice, applique strictement.

## Constantes de `loom_ia.tools_builtin.word.writer`

- `ZIP_DATE = (2000, 1, 1, 0, 0, 0)` : Date des parties de l'archive : fixe, pour qu'un même document donne les mêmes octets.
- `FIXED_DATE = '2000-01-01T00:00:00Z'` : Date des propriétés du document, fixe pour la même raison.
- `CM = 567` : Un centimètre en vingtièmes de point (twips) ; un twip en EMU.
- `EMU_PER_TWIP = 635` : EMU par twip.
- `_DATE_SPACE = 60` : L'espace sous « Date : », dans une signature, en twips (3 pt) : resté dans le code (la place pour signer, en dessous, est `signature.sign_height`).
- `SECTION_MARKS = 1000` : Les signets des sections : leurs identifiants commencent ici, loin de ceux du sommaire.
- `REF_MARKS = 2000` : Les identifiants des signets des titres que vise un renvoi (`REF_MARK` et leur rang).
- `CONTENT_TYPES = _HEAD + f'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" Content…` : La partie `[Content_Types].xml`.
- `PACKAGE_RELS = _HEAD + f'<Relationships xmlns="{_REL}"><Relationship Id="rId1" Type="{_TYPE}/officeDocument" Target="word/document.xml…` : Les relations du paquet (`_rels/.rels`).
- `DOCUMENT_RELS = _HEAD + f'<Relationships xmlns="{_REL}"><Relationship Id="rId1" Type="{_TYPE}/styles" Target="styles.xml"/><Relationshi…` : Le début des relations du document (`word/_rels/document.xml.rels`).
- `HEADER_PART = 'header1.xml'` : Les parties d'en-tête : l'en-tête des pages, et l'en-tête et le pied vides d'une première page sans en-tête (page de garde, `first_page: false`).
- `FIRST_HEADER_PART = 'header2.xml'` : Partie de l'en-tête de la première page.
- `FIRST_FOOTER_PART = 'footer2.xml'` : Partie du pied de la première page.
- `APP = _HEAD + '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"><Application>loo…` : La partie des propriétés étendues (`docProps/app.xml`).
- `SETTINGS = _HEAD + f'<w:settings xmlns:w="{_W}"><w:defaultTabStop w:val="708"/><w:characterSpacingControl w:val="doNotCompress"/><…` : La partie des réglages du document (`word/settings.xml`).
- `SETTINGS_NOTES = SETTINGS.replace('<w:compat>', '<w:footnotePr><w:footnote w:id="-1"/><w:footnote w:id="0"/></w:footnotePr><w:compat>')` : Avec des notes de bas de page : les deux séparateurs, que Word veut nommés ici.
- `STYLES = look.styles_xml(house())` : Le style maison, tel qu'écrit dans un document sans style.
- `PLAIN = _Layout()` : La mise en page d'un document libre.
- `_FIRST_PARAGRAPH = re.compile('<w:p>(?:<w:pPr>.*?</w:pPr>)?', re.DOTALL)` : Le début du premier paragraphe : après ses propriétés, s'il en a.

## Fonction `loom_ia.tools_builtin.word.writer._parts_listed`

```python
def _parts_listed(charts: int, media: list[Picture], body_media: int, bands: Sequence[tuple[str, str, str]]) -> tuple[str, str]
```

Types de contenu et relations du corps.

Ceux d'un document sans graphique, image ni en-tête, à l'octet près, plus une partie
et une relation par graphique, par image du corps (`body_media` premières), et par
en-tête ou pied ajouté (`bands` : identifiant, type, partie).

## Fonction `loom_ia.tools_builtin.word.writer._core`

```python
def _core(title: str | None, template: str | None=None, language: str='fr-FR') -> str
```

Les propriétés du document (`docProps/core.xml`) : titre, catégorie (le modèle suivi), auteur, langue, dates fixes.

## Fonction `loom_ia.tools_builtin.word.writer._half`

```python
def _half(points: float | None) -> int | None
```

Une taille en points, en demi-points ; `None` sans taille.

## Fonction `loom_ia.tools_builtin.word.writer._plain_runs`

```python
def _plain_runs(runs: Inline, *, bold: bool=False, size: int | None=None) -> str
```

Des segments sans lien, note ni renvoi : un titre, une légende, une case.

## Fonction `loom_ia.tools_builtin.word.writer._runs`

```python
def _runs(body: _Body, runs: Inline, *, bold: bool=False, size: int | None=None, part: str='document') -> str
```

Les segments d'un paragraphe, d'un élément de liste, d'une cellule.

L'emphase, les liens (une relation externe de `part`), les appels de note, les
renvois.

## Fonction `loom_ia.tools_builtin.word.writer._footnote`

```python
def _footnote(body: _Body, note: Inline) -> str
```

L'appel d'une note de bas de page ; son texte va dans `footnotes.xml`.

## Fonction `loom_ia.tools_builtin.word.writer._reference`

```python
def _reference(body: _Body, run: Run, *, bold: bool, size: int | None) -> str
```

Un renvoi.

Le numéro du titre visé (ses chiffres seuls), dans un champ `REF` que Word
recalcule ; un titre sans numéro, entre guillemets, en lien vers lui. Un article («
Article 2 » et son tiret) : son numéro en lien, LibreOffice recalculant le champ
avec le libellé et le tiret.

## Fonction `loom_ia.tools_builtin.word.writer._run`

```python
def _run(text: str, props: str='') -> str
```

Un segment : `\n` devient un saut de ligne, `\t` une tabulation.

## Fonction `loom_ia.tools_builtin.word.writer._spacer`

```python
def _spacer(twips: int) -> str
```

Un paragraphe vide de `twips` de haut : un espace avant, sans texte à relire.

## Fonction `loom_ia.tools_builtin.word.writer._after`

```python
def _after(points: float) -> str
```

Le paragraphe vide qui suit un tableau, une image, un graphique, des sections côte à côte (deux tableaux voisins ne fusionnent pas).

Une ligne de Normal, et `points` après elle (le `space_after` de leur style, de
`image`, `chart`, `page`).

## Fonction `loom_ia.tools_builtin.word.writer._list_indent`

```python
def _list_indent(style: Style) -> tuple[int, int]
```

Le retrait de chaque niveau d'une liste et la place de sa puce, en twips (`list`).

## Fonction `loom_ia.tools_builtin.word.writer._sides`

```python
def _sides(value: tuple[float, float, float, float] | None) -> tuple[int, int, int, int]
```

Un `padding` (cm, en haut, à droite, en bas, à gauche), en twips.

## Classe `loom_ia.tools_builtin.word.writer._Body`

```python
@dataclass(slots=True)
class _Body
```

Ce que le corps demande à l'archive : numérotations, graphiques, images, styles.

**Attributs :**

- `style` : Le style du document.
- `title` : Son titre.
- `template` : Le modèle suivi, s'il y en a un.
- `lists` : Les listes : numérotée ?, à trois niveaux ?
- `charts` : Les graphiques, en parties de l'archive.
- `media` : Les images distinctes, dans l'ordre où elles paraissent la première fois.
- `stored` : Le nom de chaque image dans l'archive, par empreinte.
- `drawings` : Les dessins (graphiques et images) : chacun son identifiant, unique dans le document.
- `used` : Les styles que seule une disposition demande, et que le corps emploie.
- `custom` : Les styles à toi que le corps emploie (`styles` d'une section, `style` d'un bloc).
- `numbers` : Les légendes numérotées : le dernier numéro, par sorte (« Tableau », « Figure »).
- `toc` : Le sommaire : ses entrées (niveau, texte, signet).
- `marks` : Le signet de chaque titre repris au sommaire.
- `ranks` : Les renvois : le rang de chaque titre, par bloc.
- `targeted` : Les titres qu'un renvoi vise.
- `ref_numbers` : Le numéro de chaque titre visé, si les titres sont numérotés.
- `articles` : Le numéro de chaque article, dans un document numéroté par articles.
- `footnotes` : Les notes de bas de page, en XML.
- `links` : Les liens de chaque partie : leurs adresses.

### Méthode `loom_ia.tools_builtin.word.writer._Body.word`

```python
def word(self, name: str) -> str
```

Le style Word d'un style nommé ; un style à toi est écrit s'il sert.

### Méthode `loom_ia.tools_builtin.word.writer._Body.relation`

```python
def relation(self, address: str, part: str) -> str
```

La relation d'un lien dans une partie (le corps, les notes) : une par adresse.

### Méthode `loom_ia.tools_builtin.word.writer._Body.picture`

```python
def picture(self, picture: Picture) -> int
```

Le numéro de la partie qui porte l'image : chaque image distincte une fois.

## Fonction `loom_ia.tools_builtin.word.writer._caption`

```python
def _caption(body: _Body, kind: str, runs: Inline, style_id: str='Caption') -> str
```

La légende.

Numérotée (« Figure 2 — ») si le style `caption` le veut, par un champ `SEQ` que
Word et LibreOffice recalculent, et que la relecture retire. `kind` : le libellé
(`labels.table`, `labels.figure`).

## Fonction `loom_ia.tools_builtin.word.writer._border`

```python
def _border(value: str | None) -> str
```

Un filet de tableau ou de cellule (« épaisseur couleur ») : ses attributs ; `nil` sans filet.

## Fonction `loom_ia.tools_builtin.word.writer._valign`

```python
def _valign(look: Look) -> str
```

Le texte dans la hauteur d'une cellule de tableau (`valign` de son style).

## Fonction `loom_ia.tools_builtin.word.writer._table_borders`

```python
def _table_borders(tables: Look) -> list[tuple[str, str]]
```

Les filets d'un tableau.

`borders` (grille, horizontales, verticales, aucun) au trait `line`, puis
`border_top` et `border_bottom` s'ils sont dits.

## Fonction `loom_ia.tools_builtin.word.writer._table`

```python
def _table(body: _Body, table: TableOut, width: int, tables: Look, *, cell_style: str='TableText', caption: str='Caption') -> str
```

Un tableau en XML : en-têtes, lignes, groupes, total, légende, avec les styles et les largeurs de colonnes.

## Fonction `loom_ia.tools_builtin.word.writer._image`

```python
def _image(body: _Body, block: ImageOut, width: int, caption: str='Caption') -> str
```

Une image, réduite à la place disponible (une colonne, une cellule), et sa légende.

## Fonction `loom_ia.tools_builtin.word.writer._layout`

```python
def _layout(body: _Body, cells: Sequence[str], widths: Sequence[int], *, after: float, boxed: Mapping[int, Look] | None=None) -> str
```

Un tableau de disposition : des contenus côte à côte (ou un seul, encadré), suivi d'un paragraphe vide et de `after` points.

Sans bordure, au style « loom layout », que la relecture reconnaît : elle rend les
blocs de chaque cellule, dans l'ordre, et jamais le tableau. `boxed` : les cellules
encadrées, et le style de leur cadre (`box`, ou celui que la section choisit).

## Fonction `loom_ia.tools_builtin.word.writer._cell_margins`

```python
def _cell_margins(box: Look | None, gap: int) -> str
```

Les marges d'une cellule de disposition.

Celles d'un cadre (le `padding` de son style), ou l'écart à la cellule suivante
(`gap`, `page.beside_gap`).

## Fonction `loom_ia.tools_builtin.word.writer._room`

```python
def _room(width: int, box: Look | None, gap: int) -> int
```

La largeur utile d'une cellule de disposition : sans le `padding` de son cadre, ou sans l'écart à la suivante.

## Classe `loom_ia.tools_builtin.word.writer._Layout`

```python
@dataclass(frozen=True, slots=True)
class _Layout
```

Ce qu'une section du modèle dit de sa mise en page et de ses styles ; un document libre est une seule section, sans rien de tout cela.

**Attributs :**

- `styles` : Les styles de ses éléments.
- `box` : Section encadrée.
- `beside` : À côté de la précédente.
- `width` : Sa largeur, en part du texte.
- `page_break` : Commencée sur une nouvelle page.
- `columns` : Colonnes de texte.
- `landscape` : En paysage.
- `cover` : Page de garde.
- `margin_top` : Espace avant la section, en twips.
- `align` : Une page de garde : son texte centré.

## Fonction `loom_ia.tools_builtin.word.writer._style_id`

```python
def _style_id(body: _Body, element: str, chosen: str | None, layout: _Layout) -> str
```

Le style Word d'un élément.

Celui que le bloc choisit (`style`), sinon celui que la section choisit
(`styles`), sinon le style prévu de l'élément.

## Fonction `loom_ia.tools_builtin.word.writer._blocks`

```python
def _blocks(body: _Body, blocks: Sequence[BlockOut], width: int, layout: _Layout=PLAIN) -> str
```

Les blocs d'une section, dans `width` twips, selon ses styles.

## Fonction `loom_ia.tools_builtin.word.writer._checkbox`

```python
def _checkbox(body: _Body, style: str, checked: bool, runs: Inline, size: int | None) -> str
```

Un élément de liste à cocher : la case (☒ ou ☐), une tabulation, le texte.

## Fonction `loom_ia.tools_builtin.word.writer._signature`

```python
def _signature(body: _Body, block: SignatureOut, width: int) -> str
```

La zone de signature : une colonne par signataire, sans cadre.

Au style de tableau « loom signature », que la relecture reconnaît : elle rend le
bloc `signature`, et non un tableau.

## Fonction `loom_ia.tools_builtin.word.writer._contents`

```python
def _contents(body: _Body, width: int) -> str
```

Le sommaire.

Un champ `TOC` que Word met à jour à l'ouverture (`dirty`), avec ses entrées
déjà écrites — les titres, et leurs numéros s'ils sont numérotés — pour qu'il se
lise avant toute mise à jour. Les numéros de page (`toc.pages`) sont des champs
`PAGEREF` vers le signet de chaque titre : seul le traitement de texte connaît les
pages, et LibreOffice les écrit à « Mettre à jour l'index ».

## Fonction `loom_ia.tools_builtin.word.writer._toc_entries`

```python
def _toc_entries(body: _Body, blocks: Sequence[BlockOut]) -> None
```

Les entrées du sommaire, s'il y en a un : les titres repris (jusqu'à `toc.levels`), leur numéro tel que Word l'écrira, et un signet.

## Fonction `loom_ia.tools_builtin.word.writer._ref_targets`

```python
def _ref_targets(body: _Body, blocks: Sequence[BlockOut]) -> None
```

Les titres qu'un renvoi vise.

Leur rang (l'ordre des titres, celui de la validation), et leur numéro, tel que le
champ `REF` l'écrit, si les titres sont numérotés (les chiffres seuls : « 2 », «
2.1 »).

## Fonction `loom_ia.tools_builtin.word.writer._footnotes`

```python
def _footnotes(notes: Sequence[str]) -> str
```

Les notes de bas de page, après les deux séparateurs que Word attend.

## Fonction `loom_ia.tools_builtin.word.writer._link_relations`

```python
def _link_relations(body: _Body, part: str, *, bare: bool=False) -> str
```

Les relations des liens d'une partie : vers leur adresse, hors du document ; `bare` : sans l'enveloppe (ajoutées aux relations du corps).

## Fonction `loom_ia.tools_builtin.word.writer._heading_number`

```python
def _heading_number(numbering: str, counters: Sequence[int], article: str) -> str
```

Le numéro d'un titre, comme la numérotation de `word/style.py` l'écrit.

## Fonction `loom_ia.tools_builtin.word.writer._list_items`

```python
def _list_items(body: _Body, number: int, level: int, entry: Entry, align: str, style: str) -> str
```

Un élément de liste, et sa sous-liste, au niveau suivant.

## Fonction `loom_ia.tools_builtin.word.writer._fields`

```python
def _fields(body: _Body, block: FieldsOut, width: int) -> str
```

Des libellés et leurs valeurs.

Un tableau sans bordure, au style « loom fields » que la relecture reconnaît ; une
valeur vide est une ligne à remplir (le trait des tableaux, en bas de sa cellule).

## Classe `loom_ia.tools_builtin.word.writer._Group`

```python
@dataclass(slots=True)
class _Group
```

Une section du document.

Sa place dans le modèle (`None` pour un document libre, ou le titre du document
avant les sections), sa ligne, sa mise en page, ses blocs.

## Classe `loom_ia.tools_builtin.word.writer._Unit`

```python
@dataclass(slots=True)
class _Unit
```

Ce qui se pose d'un tenant : une section, ou des sections côte à côte.

## Fonction `loom_ia.tools_builtin.word.writer._groups`

```python
def _groups(prepared: Prepared, template: Template | None) -> list[_Group]
```

Un document libre : un seul groupe. Un document qui suit un modèle : le titre du document, puis une section par groupe, dans l'ordre du modèle.

## Fonction `loom_ia.tools_builtin.word.writer._units`

```python
def _units(body: _Body, groups: Sequence[_Group]) -> list[_Unit]
```

Les groupes, posés.

Seuls, ou côte à côte quand ils sont sur la même ligne du modèle (une section
`beside` absente laisse l'autre seule, en pleine largeur).

## Fonction `loom_ia.tools_builtin.word.writer._marked`

```python
def _marked(group: _Group, content: str) -> str
```

Le contenu d'une section, son début marqué d'un signet dans son premier paragraphe (celui de la première cellule d'un tableau).

La relecture retrouve la section.

## Fonction `loom_ia.tools_builtin.word.writer._alone`

```python
def _alone(body: _Body, group: _Group, landscape: bool) -> _Unit
```

Une section seule : sa mise en page (colonnes, cadre, page de garde, espace avant) et ses blocs.

## Fonction `loom_ia.tools_builtin.word.writer._beside`

```python
def _beside(body: _Body, together: Sequence[_Group], landscape: bool) -> _Unit
```

Des sections côte à côte : une cellule chacune, à la part que dit `width` (des parts égales sans elle).

## Classe `loom_ia.tools_builtin.word.writer._Section`

```python
@dataclass(slots=True)
class _Section
```

Une section Word : son orientation, ses colonnes, son début, son contenu.

## Fonction `loom_ia.tools_builtin.word.writer._sections`

```python
def _sections(units: Sequence[_Unit], style: Style) -> list[_Section]
```

Les sections Word : une nouvelle à chaque changement d'orientation ou de colonnes, et après une page de garde.

Des colonnes ne s'équilibrent que suivies d'une section continue : une section en
colonnes que suit une nouvelle page est donc fermée par une section continue vide,
d'une colonne.

## Classe `loom_ia.tools_builtin.word.writer._Bands`

```python
@dataclass(frozen=True, slots=True)
class _Bands
```

Les en-têtes et pieds de page du document : leurs relations dans le corps.

## Fonction `loom_ia.tools_builtin.word.writer._section_properties`

```python
def _section_properties(style: Style, section: _Section, bands: _Bands, first: bool) -> str
```

Les propriétés d'une section Word : en-têtes et pieds, première page, format, marges et colonnes.

## Fonction `loom_ia.tools_builtin.word.writer._blank_first_page`

```python
def _blank_first_page(style: Style, sections: Sequence[_Section]) -> bool
```

La première page sans en-tête ni pied : page de garde, ou `first_page: false`.

## Fonction `loom_ia.tools_builtin.word.writer._document`

```python
def _document(prepared: Prepared, body: _Body) -> tuple[str, _Bands]
```

Le corps du document (`word/document.xml`) : ses sections, leurs blocs et leurs propriétés ; avec ce que l'en-tête et le pied demandent.

## Fonction `loom_ia.tools_builtin.word.writer._band_parts`

```python
def _band_parts(body: _Body, bands: _Bands) -> tuple[list[tuple[str, str | bytes]], list[tuple[str, str, str]]]
```

Les parties d'en-tête et de pied de page, leurs relations propres (un logo), et leurs relations dans le corps (identifiant, type, partie).

## Fonction `loom_ia.tools_builtin.word.writer.render`

```python
def render(prepared: Prepared, style: Style | None=None, template: Template | None=None) -> bytes
```

Le fichier .docx, le même octet pour octet pour un même document et un même style (le style maison, sans style).

`template` : le modèle que le document suit.
