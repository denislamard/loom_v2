# loom_ia.tools_builtin.word.reader

Module `loom_ia.tools_builtin.word.reader` de loom_v2 (fichier `src/loom_ia/tools_builtin/word/reader.py`).

Lire un .docx : ses blocs dans l'ordre, rendus dans le vocabulaire de l'écriture.

Un document Word est un zip de XML qui vient de n'importe où. Comme pour un
classeur (`excel/reader`) :

- le zip est contrôlé avant tout (nombre de membres, taille décompressée,
  taille de chaque partie lue) : une bombe zip est refusée sans être ouverte ;
- le XML passe par le parseur de la bibliothèque standard (expat), qui refuse
  l'amplification d'entités depuis 2.4.0 et ne charge aucune entité externe ;
- le corps est lu **au fil de l'eau** (`XMLPullParser`) et chaque bloc est
  libéré une fois rendu : la mémoire ne suit pas la taille du document ;
- la lecture s'arrête d'elle-même à une échéance.

Ce qui revient au modèle reprend le vocabulaire de l'écriture : `titre`
(`level` 1 à 3 et plus, 0 pour le titre du document), `paragraphe`,
`liste` (`items` : textes, ou `{text, items}`), `tableau` (`rows`).
L'emphase revient en `**gras**`, `*italique*` et `<u>souligné</u>` : ce qui est lu peut être
réécrit tel quel. Ce qu'un style de document a posé ne revient pas, puisque le style
le repose à la réécriture : un tableau de disposition (style « loom layout » : des
sections côte à côte, un encadré, une grille d'images) rend les blocs de ses
cellules, dans l'ordre ; une légende numérotée (« Figure 2 — Avant ») rend son texte
sans le numéro. Un document qui suit un modèle revient par sections (`template`,
`sections`) : chaque section commence à son signet (`_loom_s<n>`) et court jusqu'au
signet de la suivante ; son titre,
que l'outil écrit, ne revient pas, ni une section au texte fixe ; un style proposé au
modèle de langage revient dans `style`. Une zone de signature revient en
bloc `signature`, un sommaire en bloc `sommaire` (ses entrées ne reviennent pas),
une liste à cocher en `liste` avec `checkboxes` et ses cases en [x] ou [ ], des
champs (tableau « loom fields ») en bloc `fields`, du code (style « loom code ») en bloc
`code`. Dans un texte, un lien revient en `[texte](adresse)`, une note de bas de
page (ou de fin) en `^[texte]`, un renvoi à un titre (un champ `REF`, ou un lien
interne, vers un signet de ce titre) en `§[Titre]` ; dans un tableau, l'emphase d'une
ligne d'en-tête ou d'une ligne toute en gras (un total) ne revient pas. Un tableau à
groupes revient en `{group, rows}`, sans ses sous-totaux, que l'outil recalcule ; un
récapitulatif de TVA, en bloc `invoice_totals` (ce que l'outil en a gardé dans sa
description), une feuille d'émargement en bloc `attendance`. Avec `shown`
(`loom.memory`), tout cela revient tel qu'affiché, en lignes de tableau. Avec
`images`, une image du corps revient en bloc `image` (`path` : l'adresse du
fichier lu à l'écriture, gardée dans le nom du dessin) ; sans, elle est comptée. Les
en-têtes, pieds de page et commentaires ne sont pas lus ; le résultat le dit.

## Constantes de `loom_ia.tools_builtin.word.reader`

- `READABLE = ('.docx', '.docm', '.dotx')` : Extensions lues.
- `OTHER_FORMATS = {'.doc': 'Word 97-2003 (.doc)', '.odt': 'OpenDocument (.odt)', '.rtf': 'RTF (.rtf)'}` : Formats qui ne sont pas un zip OOXML : le dire plutôt que « pas un zip ».
- `MAX_MEMBERS = 10000` : Parties au plus dans l'archive.
- `MAX_UNCOMPRESSED = 10 * MAX_SOURCE_BYTES` : Taille au plus de l'archive décompressée.
- `MAX_BODY = 100000000` : Le corps se lit au fil de l'eau ; styles, numérotation et relations, en entier.
- `MAX_PART = 10000000` : Taille au plus d'une partie décompressée, en octets.
- `MAX_CELL_CHARS = 1000` : Caractères rendus au plus par cellule de tableau.
- `MAX_COLUMNS = 50` : Colonnes rendues au plus par tableau.
- `MAX_OUTLINE = 100` : Titres rendus au plus dans le plan.
- `MAX_OUTLINE_CHARS = 8000` : Le plan s'ajoute aux blocs (READ_CHARS) : à eux deux, sous le seuil de déport (50 000).
- `READ_DEADLINE = 20.0` : Durée au plus d'une lecture, en secondes.
- `SAFE_EXPAT = (2, 4, 0)` : Version d'expat au moins, sûre contre l'expansion d'entités.
- `_NAMESPACES = ('http://schemas.openxmlformats.org/wordprocessingml/2006/main', 'http://purl.oclc.org/ooxml/wordprocessingml/main')` : WordprocessingML transitionnel (ce qu'écrivent Word et LibreOffice), et strict.
- `LAYOUT_STYLE = 'loom layout'` : Le nom du style de tableau de disposition qu'écrit l'outil (`word/style.py`).
- `FIELDS_STYLE = 'loom fields'` : Nom du style de tableau des champs, que la lecture reconnaît.
- `GROUP_STYLE = 'loom table group'` : Les lignes d'un groupe et de son sous-total, et le récapitulatif de TVA : le style de paragraphe de leurs cellules.
- `SUBTOTAL_STYLE = 'loom table subtotal'` : Nom du style des sous-totaux, que la lecture reconnaît.
- `INVOICE_STYLE = 'loom invoice totals'` : Nom du style du récapitulatif de TVA, que la lecture reconnaît.
- `ATTENDANCE_STYLE = 'loom attendance'` : Nom du style de la feuille d'émargement, que la lecture reconnaît.
- `SIGNATURE_STYLE = 'loom signature'` : Les styles des blocs signature et toc, et de la liste à cocher (`word/style.py`), par leur nom.
- `SIGNER_STYLE = 'loom signer'` : Nom du style d'un signataire, que la lecture reconnaît.
- `MENTION_STYLE = 'loom mention'` : Nom du style de la mention d'une signature, que la lecture reconnaît.
- `CHECK_STYLES = {'loom checkbox': 0, 'loom checkbox 2': 1}` : Noms des styles de liste à cocher, et leur niveau.
- `_CHECKED = frozenset('☒☑✓✔')` : Une case cochée (☒, ☑, ✓, ✔) ou vide (☐), en tête d'un élément de liste à cocher.
- `MAX_LAYOUT_DEPTH = 4` : Un tableau de disposition dans un autre (une grille d'images dans une colonne) : borné.
- `_CAPTION_SEPARATORS = ' —–-:.'` : Ce qui sépare le numéro d'une légende de son texte (« Figure 2 — Avant »).
- `_MC_FALLBACK = '{http://schemas.openxmlformats.org/markup-compatibility/2006}Fallback'` : La version de secours d'un contenu « compatible » (`mc:AlternateContent`) : Word y range le même dessin en VML, que `mc:Choice` porte déjà ; il ne compte pas deux fois.
- `_SKIPPED = frozenset({'del', 'delText', 'moveFrom', 'drawing', 'pict', 'object', 'commentReference', 'pPr', 'rPr'})` : Sous-arbres d'un paragraphe qui ne sont pas son texte.
- `_Segment = tuple[str, bool, bool, bool, bool]` : Un segment de texte : son texte, gras, italique, souligné ; ou déjà en balisage (un lien, une note, un renvoi : `raw`), que l'emphase n'enveloppe pas.
- `_PENDING_REF = re.compile('\x00([^\x00\x01]*)\x01([^\x00]*)\x00')` : Un renvoi lu avant de connaître les titres du document : le signet qu'il vise et le texte qu'il affiche, entre deux caractères qu'un XML ne peut pas porter ; remplacé à la fin de la lecture par `§[Titre]`, ou par son texte si le signet n'est pas celui d'un titre.
- `_SECTION_MARK = re.compile('^_loom_s(\\d+)$')` : Le signet d'une section du modèle : sa place.
- `_TOC_ENTRY = re.compile('^toc [1-9]$')` : Une entrée de sommaire (« toc 1 » à « toc 9 »).
- `_KIND = {'loom code': 'code', 'quote': 'quote', 'intense quote': 'quote', 'quote source': 'quote', 'note': 'note'}` : Les styles qui disent un autre bloc que le paragraphe : ceux qu'écrit l'outil, et leurs équivalents intégrés de Word.
- `MAX_CHART_POINTS = 200` : Points rendus au plus par graphique.

## Fonction `loom_ia.tools_builtin.word.reader.check_suffix`

```python
def check_suffix(path: str) -> None
```

Refuse un fichier qui n'est pas un document Word lisible (.docx, .docm, .dotx), en disant comment le convertir s'il en a un autre format.

## Fonction `loom_ia.tools_builtin.word.reader.xml_safety`

```python
def xml_safety() -> str | None
```

Pourquoi un .docx ne peut pas être lu sans risque ici ; `None` s'il le peut.

## Fonction `loom_ia.tools_builtin.word.reader._local`

```python
def _local(tag: str) -> str | None
```

Le nom WordprocessingML d'une balise, `None` pour un autre espace de noms.

## Fonction `loom_ia.tools_builtin.word.reader._on`

```python
def _on(element: Element | None) -> bool
```

Une propriété booléenne (`<w:b/>`, `<w:b w:val="0"/>`) : posée et vraie.

## Fonction `loom_ia.tools_builtin.word.reader._guarded`

```python
@contextmanager
def _guarded(where: str) -> Generator[None]
```

Toute faute du fichier devient une `ToolError`, jamais une erreur interne.

## Classe `loom_ia.tools_builtin.word.reader._Style`

```python
@dataclass(frozen=True, slots=True)
class _Style
```

Un style de paragraphe du document, tel que la lecture s'en sert.

**Attributs :**

- `name` : Son nom.
- `based_on` : Son style de base.
- `outline` : Son niveau de plan, pour un titre.
- `num_id` : La numérotation que le style porte (un style de liste), ou `None`.
- `numbering_off` : Le style annule la numérotation de son parent (``numId 0``) : un titre masqué d'un document aux titres numérotés, l'en-tête de table des matières de Word.

## Classe `loom_ia.tools_builtin.word.reader._Package`

```python
@dataclass(slots=True)
class _Package
```

Le document ouvert : ses parties, lues une fois.

**Attributs :**

- `archive` : L'archive du document.
- `body` : Le corps (`word/document.xml`).
- `styles` : Les styles de paragraphe, par identifiant.
- `tables` : Les styles de tableau, par identifiant : leur nom (« loom layout »…), en minuscules.
- `numbering` : numId → numéroté (vrai) ou à puces, par niveau.
- `title` : Le titre du document, dans ses propriétés.
- `category` : La catégorie du document : le modèle suivi, pour un document écrit avec un modèle.
- `comments` : Les commentaires, par identifiant.
- `charts` : Identifiant de relation → partie d'un graphique (`word/charts/chartN.xml`).
- `media` : Identifiant de relation → partie d'une image (`word/media/imageN.png`).
- `inline` : De quoi lire les liens et les notes du corps.

## Classe `loom_ia.tools_builtin.word.reader._Inline`

```python
@dataclass(frozen=True, slots=True)
class _Inline
```

Ce que demande le texte enrichi d'une partie.

L'adresse de ses liens (par relation), et le texte des notes du document (« f3 » :
la note de bas de page 3, « e1 » : la note de fin 1).

**Attributs :**

- `links` : Identifiant de relation → adresse d'un lien.
- `notes` : Identifiant → texte d'une note de bas de page.

## Fonction `loom_ia.tools_builtin.word.reader._open`

```python
def _open(data: bytes, where: str) -> _Package
```

Ouvre l'archive d'un document et en lit les parties utiles ; `ToolError` pour un fichier illisible, protégé, ou qui se décompresse trop.

## Fonction `loom_ia.tools_builtin.word.reader._external`

```python
def _external(archive: zipfile.ZipFile, names: set[str], part: str) -> dict[str, str]
```

Les liens d'une partie : l'adresse de chacun, par identifiant de relation.

## Fonction `loom_ia.tools_builtin.word.reader._note_texts`

```python
def _note_texts(archive: zipfile.ZipFile, names: set[str], related: Mapping[str, str], where: str) -> dict[str, str]
```

Le texte de chaque note de bas de page et de fin (sans ses séparateurs), ses liens et ses renvois compris, ses paragraphes en lignes.

## Fonction `loom_ia.tools_builtin.word.reader._whole`

```python
def _whole(archive: zipfile.ZipFile, name: str, where: str) -> Element | None
```

Une partie lue en entier, si elle est là et de taille raisonnable.

## Fonction `loom_ia.tools_builtin.word.reader._relations`

```python
def _relations(archive: zipfile.ZipFile, names: set[str], body: str, *, by_id: str | None=None) -> dict[str, str]
```

Les parties liées au corps, par type (« styles », « numbering »…) ; avec `by_id`, celles de ce type, par identifiant de relation (les graphiques).

## Fonction `loom_ia.tools_builtin.word.reader._styles`

```python
def _styles(root: Element | None) -> dict[str, _Style]
```

Les styles de paragraphe du document, par identifiant.

## Fonction `loom_ia.tools_builtin.word.reader._table_styles`

```python
def _table_styles(root: Element | None) -> dict[str, str]
```

Les styles de tableau : leur nom, par identifiant (« loom layout », « loom signature »).

## Fonction `loom_ia.tools_builtin.word.reader._numbering`

```python
def _numbering(root: Element | None) -> dict[str, dict[int, bool]]
```

Les numérotations du document : par `numId`, numéroté ou à puces pour chaque niveau.

## Fonction `loom_ia.tools_builtin.word.reader._properties`

```python
def _properties(archive: zipfile.ZipFile, names: set[str], where: str) -> tuple[str | None, str | None]
```

Le titre et la catégorie du document (où l'écriture range le modèle suivi).

## Classe `loom_ia.tools_builtin.word.reader._Reading`

```python
@dataclass(slots=True)
class _Reading
```

Ce que la lecture rend, et ce qu'elle a vu en passant.

**Attributs :**

- `start` : Le premier bloc rendu.
- `budget` : Taille du résultat, en caractères JSON : au-delà, la lecture s'arrête (`next_start`).
- `blocks` : Les blocs rendus.
- `used` : La taille déjà prise.
- `full` : Vrai quand le budget est atteint.
- `next_start` : Le bloc où reprendre, si la lecture s'est arrêtée.
- `count` : Les blocs vus.
- `outline` : Les titres du document.
- `images` : Rendre les images.
- `revisions` : Les révisions suivies vues.
- `cut` : Ce qui a été coupé.
- `pending` : Une liste en cours : ses éléments s'ajoutent jusqu'au bloc suivant.
- `pending_id` : Sa numérotation : deux listes voisines (puces, puis numéros) restent deux listes.
- `pictures` : Avec `images` : les images rendues en blocs.
- `last_image` : La dernière image rendue : un paragraphe de légende qui la suit devient sa caption.
- `in_toc` : Dans un sommaire : ses entrées ne sont pas des blocs, le bloc `sommaire` est rendu.
- `template` : Le modèle que le document suit (si l'outil le déclare).
- `proposed` : Les styles proposés du modèle.
- `section` : La section en cours (sa place dans le modèle), par son signet.
- `seen` : Les sections vues.
- `where` : La section de chaque bloc rendu (`None` hors des sections).
- `anchors` : Les signets des titres : le texte du titre, que vise un renvoi à ce signet.
- `shown` : Ce qui s'affiche, plutôt que ce qui se réécrit : les groupes, leurs sous-totaux et le récapitulatif de TVA en lignes de tableau.

## Fonction `loom_ia.tools_builtin.word.reader.read`

```python
def read(data: bytes, where: str, start: int, budget: int=READ_CHARS, *, images: bool=False, templates: Mapping[str, Template] | None=None, styles: frozenset[str]=frozenset(), shown: bool=False) -> dict[str, JsonValue]
```

Les blocs du document à partir de `start`, dans un budget de taille.

`budget` : celui d'un résultat d'outil par défaut ; `loom.memory`, qui lit
le document entier, en donne un plus grand. `images` : les images du corps
reviennent en blocs `image` (un outil monté avec `images`) ; sinon, comptées.
`templates` : les modèles de l'outil ; un document qui en suit un revient par
sections (`template`, `sections`). Sans modèles (`None`), il revient à plat.
`styles` : les styles proposés au modèle de langage, rendus dans `style`.
`shown` : les tableaux tels qu'affichés (`loom.memory`, qui indexe le texte et ne
réécrit rien).

## Fonction `loom_ia.tools_builtin.word.reader._resolved`

```python
def _resolved(value: JsonValue, anchors: Mapping[str, str]) -> JsonValue
```

Les renvois lus, une fois les titres connus : `§[Titre]` vers un titre, le texte affiché vers un autre signet.

## Fonction `loom_ia.tools_builtin.word.reader._by_section`

```python
def _by_section(result: dict[str, JsonValue], reading: _Reading, template: Template, notes: list[JsonValue]) -> None
```

Un document qui suit un modèle.

Ses blocs rangés par section, dans l'ordre ; le titre du document est déjà dans
`title` (il est dans la section d'une page de garde, qui le porte : il n'en est
pas un bloc).

## Fonction `loom_ia.tools_builtin.word.reader._mark`

```python
def _mark(reading: _Reading, element: Element) -> None
```

Un signet : le début d'une section du modèle (`_loom_s<n>`), qui court jusqu'au début de la suivante.

Sa fin ne compte pas : LibreOffice, qui reprend un document, dédouble un signet en
plusieurs, certains vides ; le premier début est à sa place.

## Fonction `loom_ia.tools_builtin.word.reader._marks_in`

```python
def _marks_in(reading: _Reading, element: Element) -> None
```

Les signets de section d'un tableau (dans le premier paragraphe d'une cellule, quand la section commence par lui).

Avant le tableau, qui est de la section.

## Fonction `loom_ia.tools_builtin.word.reader._walk`

```python
def _walk(package: _Package, reading: _Reading, deadline: float, where: str) -> None
```

Lit le corps du document au fil de l'eau, bloc par bloc, dans le délai donné.

## Fonction `loom_ia.tools_builtin.word.reader._block`

```python
def _block(package: _Package, reading: _Reading, element: Element, depth: int=0) -> None
```

Un élément du corps : paragraphe, tableau (signature, champs, émargement, récapitulatif…), signet ou section.

## Fonction `loom_ia.tools_builtin.word.reader._table_style`

```python
def _table_style(package: _Package, table: Element) -> str | None
```

Le nom du style d'un tableau, en minuscules ; `None` sans style.

## Fonction `loom_ia.tools_builtin.word.reader._table_style_name`

```python
def _table_style_name(package: _Package, reading: _Reading, table: Element) -> str | None
```

Le style proposé d'un tableau : celui du texte de sa première ligne de données (pas d'une ligne de groupe ni de sous-total).

## Fonction `loom_ia.tools_builtin.word.reader._row_style`

```python
def _row_style(package: _Package, row: Element) -> str
```

Le nom du style de paragraphe d'une ligne de tableau : celui de son premier paragraphe ; « » sans paragraphe ni style.

## Fonction `loom_ia.tools_builtin.word.reader._attendance`

```python
def _attendance(package: _Package, table: Element) -> dict[str, JsonValue] | None
```

Une feuille d'émargement écrite par l'outil.

Ses sessions (l'en-tête, sans « Participant »), ses participants (la première
cellule de chaque ligne) ; `None` pour un autre tableau.

## Fonction `loom_ia.tools_builtin.word.reader._invoice`

```python
def _invoice(package: _Package, table: Element) -> dict[str, JsonValue] | None
```

Un récapitulatif de TVA écrit par l'outil.

Le bloc `invoice_totals` que sa description garde (ses colonnes, l'acompte) ;
`None` pour un autre tableau, ou un récapitulatif dont la description manque.

## Fonction `loom_ia.tools_builtin.word.reader._fields`

```python
def _fields(package: _Package, table: Element) -> dict[str, JsonValue]
```

Des champs : le libellé et la valeur de chaque ligne (la valeur avec son emphase, ses liens, ses notes).

## Fonction `loom_ia.tools_builtin.word.reader._signature`

```python
def _signature(package: _Package, table: Element) -> dict[str, JsonValue]
```

La zone de signature : le libellé de chaque signataire, et la mention.

## Fonction `loom_ia.tools_builtin.word.reader._paragraph`

```python
def _paragraph(package: _Package, reading: _Reading, element: Element) -> None
```

Un paragraphe : titre, élément de liste, légende, code, ou paragraphe, selon son style et sa numérotation.

## Fonction `loom_ia.tools_builtin.word.reader._checkbox`

```python
def _checkbox(reading: _Reading, text: str, level: int) -> None
```

Un élément de liste à cocher : « [x] » ou « [ ] » à la place de la case.

## Fonction `loom_ia.tools_builtin.word.reader._without_number`

```python
def _without_number(paragraph: Element) -> Element
```

Une légende numérotée sans son numéro : ce qui suit le champ `SEQ` (« Figure 2 »).

Le numéro vient du style (`captions.numbered`) : relu, il se doublerait à la
réécriture. Un champ simple (`fldSimple`) ou complexe (`fldChar`) ; un paragraphe
sans champ `SEQ` est rendu tel quel.

## Fonction `loom_ia.tools_builtin.word.reader._chart_ids`

```python
def _chart_ids(element: Element) -> list[str]
```

Les graphiques ancrés dans un paragraphe : l'identifiant de relation de chacun.

## Fonction `loom_ia.tools_builtin.word.reader._chart`

```python
def _chart(package: _Package, relation: str) -> dict[str, JsonValue]
```

Un graphique Word, rendu dans le vocabulaire de l'écriture, d'après son cache.

Un graphique qu'on ne sait pas lire (partie absente, type inconnu) reste un bloc
`graphique` sans données : sa place dans le document est connue.

## Fonction `loom_ia.tools_builtin.word.reader._points`

```python
def _points(source: Element) -> list[str | None]
```

Les points d'un cache (`strCache`, `numCache`) ou d'un littéral, à leur rang.

## Fonction `loom_ia.tools_builtin.word.reader._pictures`

```python
def _pictures(package: _Package, element: Element) -> list[dict[str, JsonValue]]
```

Les images d'un paragraphe, dans le vocabulaire de l'écriture.

`path` est l'adresse du fichier que l'outil a lu à l'écriture (le nom du dessin) ;
une image insérée ailleurs n'en a pas. `width` est celle des trois qui donne la
taille lue, d'après les pixels de l'image ; la plus proche sinon.

## Fonction `loom_ia.tools_builtin.word.reader._width`

```python
def _width(package: _Package, frame: Element, relation: str) -> ImageWidth
```

La largeur d'une image dans la page, ramenée à `full`, `half` ou `small`.

## Fonction `loom_ia.tools_builtin.word.reader._style_meaning`

```python
def _style_meaning(styles: dict[str, _Style], style_id: str | None) -> tuple[bool, int | None, str | None]
```

Le style d'un paragraphe : titre du document ? niveau de titre ? élément de liste ?

Suivi de `basedOn` en `basedOn` (borné : une boucle ne bloque pas) ;
les noms intégrés de Word sont en anglais dans le fichier, quelle que soit
la langue de l'interface (« heading 1 », « Title »).

## Fonction `loom_ia.tools_builtin.word.reader._segments`

```python
def _segments(element: Element, inline: _Inline | None=None) -> list[_Segment]
```

Le texte d'un paragraphe, en segments (texte, gras, italique, souligné), dans l'ordre.

Avec `inline` : un lien, une note, un renvoi en balisage (`raw`) ; sans, le
texte qu'ils affichent (une note, rien).

## Classe `loom_ia.tools_builtin.word.reader._Field`

```python
@dataclass(slots=True)
class _Field
```

Un champ complexe en cours : son instruction ; passé son séparateur, ce qu'il affiche.

**Attributs :**

- `instr` : L'instruction du champ.
- `result` : Vrai une fois passé le séparateur : le texte qui suit est ce que le champ affiche.
- `shown` : Ce que le champ affiche, en morceaux de texte.

## Fonction `loom_ia.tools_builtin.word.reader._ref_target`

```python
def _ref_target(instruction: str) -> str | None
```

Le signet que vise un champ `REF` ; `None` pour un autre champ.

## Fonction `loom_ia.tools_builtin.word.reader._reference`

```python
def _reference(target: str, shown: str, inline: _Inline | None) -> _Segment
```

Un renvoi : en attente des titres (`_resolved`) ; sans `inline`, son texte.

## Fonction `loom_ia.tools_builtin.word.reader._linkable`

```python
def _linkable(address: str) -> bool
```

Une adresse qu'un lien peut réécrire : https, http ou mailto.

## Fonction `loom_ia.tools_builtin.word.reader._underlined`

```python
def _underlined(element: Element | None) -> bool
```

`<w:u w:val="single"/>` (ou double, pointillé…) : souligné ; `none` : non.

## Fonction `loom_ia.tools_builtin.word.reader._markdown`

```python
def _markdown(segments: list[_Segment]) -> str
```

Les segments joints, l'emphase en `**`, `*` et `<u>` comme à l'écriture.

Un `^[` ou un `§[` du texte échappé, pour ne pas se relire en note ou en renvoi.

## Fonction `loom_ia.tools_builtin.word.reader._table`

```python
def _table(package: _Package, reading: _Reading, element: Element) -> list[JsonValue]
```

Les lignes d'un tableau.

Chaque cellule en texte, son emphase, ses liens, ses notes et ses renvois compris ;
l'emphase d'une ligne d'en-tête, ou d'une ligne toute en gras (un total), est celle
de son style, que la réécriture repose : elle ne revient pas.

## Fonction `loom_ia.tools_builtin.word.reader._all_bold`

```python
def _all_bold(segments: list[_Segment]) -> bool
```

Tout le texte (hors liens, notes, renvois) en gras ; et du texte.

## Fonction `loom_ia.tools_builtin.word.reader._cell_markdown`

```python
def _cell_markdown(paragraphs: list[list[_Segment]], *, emphasis: bool) -> str
```

Le texte d'une cellule, ses paragraphes en lignes ; sans `emphasis`, sans l'emphase.

## Fonction `loom_ia.tools_builtin.word.reader._cell_text`

```python
def _cell_text(cell: Element) -> str
```

Le texte d'une cellule : ses paragraphes, et ceux d'un tableau imbriqué, en lignes.

## Fonction `loom_ia.tools_builtin.word.reader._list_item`

```python
def _list_item(reading: _Reading, text: str, level: int, ordered: bool, style: str | None=None) -> None
```

Ajoute un élément à la liste en cours (ou en ouvre une) ; une sous-liste va sous l'élément précédent, trois niveaux en tout.

## Fonction `loom_ia.tools_builtin.word.reader._styled`

```python
def _styled(block: dict[str, JsonValue], style: str | None) -> dict[str, JsonValue]
```

Le bloc, avec le style proposé que le document lui a donné.

## Fonction `loom_ia.tools_builtin.word.reader._section_title`

```python
def _section_title(reading: _Reading, text: str) -> bool
```

Un titre de niveau 1 dans une section : son titre, si c'est son nom.

## Fonction `loom_ia.tools_builtin.word.reader._kind`

```python
def _kind(styles: dict[str, _Style], style_id: str | None) -> str
```

Le bloc que dit un style, en suivant ses bases : un style à toi qui part de Note est une note.

## Fonction `loom_ia.tools_builtin.word.reader._emit`

```python
def _emit(reading: _Reading, block: dict[str, JsonValue], heading: tuple[int, str] | None=None) -> None
```

Rend un bloc, s'il est dans la fenêtre lue et que le budget le permet ; un titre va aussi au plan.

## Fonction `loom_ia.tools_builtin.word.reader._shortened`

```python
def _shortened(block: dict[str, JsonValue], budget: int) -> dict[str, JsonValue]
```

Un bloc seul plus grand que le budget : coupé, pour que la lecture avance.

## Fonction `loom_ia.tools_builtin.word.reader._bounded`

```python
def _bounded(outline: list[JsonValue]) -> list[JsonValue]
```

Le plan, dans sa propre borne : avec les blocs, le résultat reste sous le déport.

## Fonction `loom_ia.tools_builtin.word.reader._notes`

```python
def _notes(package: _Package, reading: _Reading, start: int) -> list[JsonValue]
```

Ce que la lecture doit dire en plus : `start` hors du document, lecture coupée, images non lues, révisions, etc.
