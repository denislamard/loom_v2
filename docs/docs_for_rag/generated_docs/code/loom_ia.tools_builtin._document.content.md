# loom_ia.tools_builtin._document.content

Module `loom_ia.tools_builtin._document.content` de loom_v2 (fichier `src/loom_ia/tools_builtin/_document/content.py`).

Le document validé, prêt à rendre : texte découpé en segments, cellules mises en forme.

Ce qui ne dépend pas du format : les valeurs d'une colonne typée écrites dans la
langue du style (`1 250,50 €`, `02/10/2026`, `15,0 %` en français ;
`€1,250.50`, `10/02/2026`, `15.0%` en anglais), leur alignement, la ligne
de total (« Total », `labels.total`) calculée par l'outil (un
document n'a pas de formules), la largeur relative des colonnes. Chaque format
(`word`, `pdf`) ne fait plus que poser ces éléments sur la page. La taille
d'une image dans la page aussi (`placed`) ; ses octets sont lus avant, par
l'outil, et `prepare` ne touche jamais au disque.

Dans un texte, `**gras**`, `*italique*` et `<u>souligné</u>` marquent l'emphase ;
un saut de ligne reste un saut de ligne dans le même paragraphe. Un paragraphe, un élément
de liste, une note, une citation, une cellule de texte et la valeur d'un champ prennent
aussi un lien (`[texte](https://…)`), une note de bas de page (`^[texte]`) et un renvoi
à un titre du document (`§[Titre]`) : `rich`. Un titre, une légende, un libellé n'ont
que l'emphase, et un bloc `code` n'a aucun balisage.

Les calculs sont ceux de l'outil, jamais du modèle de langage, en `Decimal` et arrondis
au demi supérieur : une colonne calculée (`compute` : `+ - * /`, des nombres et des
`{colonnes}` de la ligne, lus par `ast`, jamais par `eval`), le sous-total de chaque
groupe de lignes, et le récapitulatif de TVA d'une facture (`invoice_totals` : la TVA de
chaque taux sur le total HT de ce taux). Un montant en euros s'arrondit au centime dès la
lecture : ce que le document additionne est ce qu'il affiche.

## Constantes de `loom_ia.tools_builtin._document.content`

- `MAX_TITLE = 300` : Longueur au plus d'un titre.
- `MIN_COLUMN_CHARS = 4` : Largeur d'une colonne, en caractères, pour en tirer la part de la page.
- `MAX_COLUMN_CHARS = 40` : Longueur au plus d'un en-tête de colonne.
- `COLUMN_PADDING = 2` : Ce qu'une colonne prend en plus de son texte : les marges de la cellule, et l'en-tête en gras, plus large que le corps.
- `PAGE_CHARS = 90` : Ce que tient la largeur utile d'une page A4 (17 cm), en caractères de tableau : 90 (Helvetica 9,5 pt en fait une centaine en moyenne ; Calibri 10 pt, davantage).
- `BOLD_WIDTH = 1.15` : Un en-tête est en gras : ses lettres sont plus larges que celles du corps.
- `SHORT_VALUE_CHARS = 14` : Une valeur jusqu'à cette longueur (un rang, un code, un montant, une date) n'est jamais coupée ; une plus longue (un nom, un texte) peut passer à la ligne.
- `NUMERIC = frozenset({'integer', 'decimal', 'amount_eur', 'amount_keur', 'percent'})` : Types de colonne qui portent des nombres.
- `ORDERED_TYPES = NUMERIC | frozenset[ColumnType]({'date', 'datetime'})` : Types de colonne qui se comparent : nombres et dates.
- `CENTERED = frozenset({'date', 'datetime', 'year', 'boolean'})` : Types de colonne centrés.
- `NBSP = '\xa0'` : Espace insécable : présente dans toutes les polices, y compris Helvetica (WinAnsi), contrairement à l'espace fine insécable (U+202F) de la typographie française.
- `_CONTROL = re.compile('[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]')` : Ce qu'un fichier XML ne peut pas contenir, et qu'aucun document n'affiche.
- `Align = Literal['left', 'center', 'right']` : Alignement d'une colonne.
- `Inline = tuple[Run, ...]` : Un texte en segments.
- `RowKind = Literal['body', 'group', 'subtotal', 'total']` : Une ligne de tableau : de données, de groupe, de sous-total, de total (en gras).
- `BlockOut = TitleOut | HeadingOut | ParagraphOut | ListOut | TableOut | NoteOut | QuoteOut | ChartOut | ImageOut | SignatureOut | C…` : Un bloc validé, de toute sorte.
- `Allowed = Mapping[str, Mapping[str, int]]` : Les styles proposés qu'un document peut choisir, par type de bloc : leur nom, et pour un titre son niveau (0 pour les autres blocs).
- `NO_STYLES = {}` : Aucun style proposé.
- `Pictures = Mapping[str, Picture | str]` : Ce qu'a donné la lecture de chaque image, par chemin tel que donné : l'image, ou ce qui l'empêche (droit, fichier absent, format…). Lue par l'outil, avant `prepare`.
- `NO_PICTURES = {}` : Aucune image.
- `_CHECKBOX = re.compile('^\\[([ xX])\\]\\s+')` : Une case, en tête d'un élément de liste à cocher : « [x] » cochée, « [ ] » vide.
- `MAX_SIGNER = 80` : Longueur au plus du nom d'un signataire.
- `MAX_MENTION = 200` : Longueur au plus de la mention d'une signature.
- `_BLANK_START = re.compile('\\A(?:[ \\t]*\\n)+')` : Les lignes vides (ou d'espaces) au début d'un texte.
- `MAX_GROUPS = 20` : Groupes au plus dans un tableau.
- `MAX_GROUP_LABEL = 200` : Longueur au plus du libellé d'un groupe.
- `MAX_FORMULA = 200` : Longueur au plus d'une formule de colonne calculée.
- `CENT = Decimal('0.01')` : Le centime : l'arrondi des montants.
- `_QUANTUM = {'integer': Decimal(1), 'decimal': CENT, 'amount_eur': CENT, 'amount_keur': Decimal(1), 'percent': Decimal('0.001')}` : Ce qu'un calcul garde de décimales, par type de colonne : ce que le document écrit.
- `_REFERENCE = re.compile('\\{([^{}]*)\\}')` : Une colonne citée dans une formule : son en-tête entre accolades.
- `MIN_NAME_CHARS = 12` : La colonne des participants : la place de leurs noms (en caractères, bornée), et au plus cette part de la page ; les sessions se partagent le reste, pour signer.
- `MAX_NAME_CHARS = 30` : Longueur au plus d'un nom de série ou de libellé dans un graphique.
- `MAX_NAME_SHARE = 0.4` : Part au plus de la largeur prise par les libellés d'un graphique.
- `MAX_LABEL = 60` : Longueur au plus d'un libellé de graphique.
- `IMAGE_WIDTHS = {'full': 17000, 'half': 8500, 'small': 4000}` : La largeur d'une image dans la page, en centièmes de millimètre : la largeur utile (17 cm), sa moitié, et celle d'un logo.
- `MAX_IMAGE_HEIGHT = 21000` : La hauteur au plus : la page utile (25,7 cm), moins la place d'une légende.
- `MIN_PPI = 96` : En deçà, une image s'affiche agrandie au-delà de ses pixels : floue à l'impression.
- `REF_MARK = '_Ref_loom_'` : L'ancre d'un titre que vise un renvoi, dans les deux formats : « _Ref » (un signet caché de Word), puis le rang du titre.
- `QUOTES = {'fr': ('«\xa0', '\xa0»'), 'en': ('“', '”')}` : Les guillemets d'un renvoi à un titre sans numéro, par langue.
- `MAX_NOTES = 100` : Notes de bas de page au plus dans un document.
- `MAX_NOTE = 500` : Longueur au plus d'une note de bas de page.
- `LINK_SCHEMES = ('https', 'http', 'mailto')` : Les schémas d'un lien : une adresse web, ou un courriel.
- `Markup = Literal['link', 'note', 'ref']` : Balisage d'un texte riche : lien, note ou renvoi.
- `EVERYTHING = frozenset({'link', 'note', 'ref'})` : Tout le balisage permis.
- `LINKS_ONLY = frozenset({'link'})` : Les liens seulement.
- `_MARKUP = re.compile('(?<!\\\\)\\^\\[|(?<!\\\\)§\\[|\\[[^\\[\\]\\n]+\\]\\([^()\\s]+\\)')` : Ce qui, dans un titre ou une légende, serait un lien, une note ou un renvoi.
- `_PLACE = 983040` : Les marques de place : un caractère d'usage privé par lien, note ou renvoi, le temps de lire l'emphase autour d'eux.

## Classe `loom_ia.tools_builtin._document.content.Run`

```python
@dataclass(frozen=True, slots=True)
class Run
```

Un segment de texte et son emphase ; `\n` y est un saut de ligne.

`link` : l'adresse d'un lien. `note` : une note de bas de page, son texte ; la
marque n'a pas de texte à elle (`text` vide), le format la numérote. `ref` : un
renvoi, le rang du titre visé parmi les titres du document (1 pour le premier) ;
`text` est ce titre, que le format écrit, ou remplace par son numéro.

**Attributs :**

- `text` : Le texte du segment.
- `bold` : En gras.
- `italic` : En italique.
- `underline` : Souligné.
- `link` : Adresse d'un lien, s'il en est un.
- `note` : Texte d'une note de bas de page appelée ici.
- `ref` : Rang du titre visé par un renvoi, parmi les titres du document (1 pour le premier).

## Classe `loom_ia.tools_builtin._document.content.TitleOut`

```python
@dataclass(frozen=True, slots=True)
class TitleOut
```

Le titre du document, validé.

**Attributs :**

- `runs` : Ses segments.

## Classe `loom_ia.tools_builtin._document.content.HeadingOut`

```python
@dataclass(frozen=True, slots=True)
class HeadingOut
```

Un titre validé.

**Attributs :**

- `level` : Son niveau, de 1 à 3.
- `runs` : Ses segments.
- `style` : Le style proposé que le modèle de langage a choisi (`style` du bloc).
- `section` : Le titre d'une section du modèle, que l'outil écrit : la place de la section.

## Classe `loom_ia.tools_builtin._document.content.ParagraphOut`

```python
@dataclass(frozen=True, slots=True)
class ParagraphOut
```

Un paragraphe validé.

**Attributs :**

- `runs` : Ses segments.
- `style` : Le style proposé choisi, s'il y en a un.

## Classe `loom_ia.tools_builtin._document.content.Entry`

```python
@dataclass(frozen=True, slots=True)
class Entry
```

Un élément de liste, et sa sous-liste (trois niveaux en tout).

**Attributs :**

- `runs` : Ses segments.
- `children` : Sa sous-liste.
- `checked` : Une liste à cocher : l'élément est coché.

## Classe `loom_ia.tools_builtin._document.content.ListOut`

```python
@dataclass(frozen=True, slots=True)
class ListOut
```

Une liste validée.

**Attributs :**

- `ordered` : Numérotée, ou à puces.
- `entries` : Ses éléments.
- `checkboxes` : Une liste à cocher (`blocks: [checkboxes]`) : une case devant chaque élément.
- `style` : Le style proposé choisi, s'il y en a un.

## Classe `loom_ia.tools_builtin._document.content.FieldsOut`

```python
@dataclass(frozen=True, slots=True)
class FieldsOut
```

Des libellés et leurs valeurs ; une valeur vide laisse une ligne à remplir.

**Attributs :**

- `items` : Les champs : libellé, et valeur en segments (vide : une ligne à remplir).

## Classe `loom_ia.tools_builtin._document.content.CodeOut`

```python
@dataclass(frozen=True, slots=True)
class CodeOut
```

Du texte préformaté : ses lignes telles quelles, espaces comprises.

**Attributs :**

- `text` : Le code, tel quel.

## Classe `loom_ia.tools_builtin._document.content.SignatureOut`

```python
@dataclass(frozen=True, slots=True)
class SignatureOut
```

La zone de signature : un libellé par signataire, et la mention qu'ils écrivent.

**Attributs :**

- `signers` : Les signataires.
- `mention` : La mention au-dessus des signatures, s'il y en a une.

## Classe `loom_ia.tools_builtin._document.content.ContentsOut`

```python
@dataclass(frozen=True, slots=True)
class ContentsOut
```

Le sommaire : la table des matières, tirée des titres par le format.

## Classe `loom_ia.tools_builtin._document.content.TableOut`

```python
@dataclass(frozen=True, slots=True)
class TableOut
```

Un tableau validé.

**Attributs :**

- `headers` : Les en-têtes des colonnes.
- `align` : L'alignement de chaque colonne.
- `rows` : Les lignes, en texte.
- `total` : La ligne de total, s'il y en a une.
- `widths` : La part de la largeur utile de chaque colonne ; leur somme vaut 1.
- `caption` : La légende, s'il y en a une.
- `cells` : Les cellules des lignes, en segments (l'emphase, les liens, les notes d'une colonne de texte) ; `rows` en garde le texte seul.
- `natural` : La largeur que le contenu demande, en part de la largeur utile (1 au plus) : celle d'un tableau qu'un style veut à sa largeur naturelle (`width: content`).
- `style` : Le style proposé choisi, s'il y en a un.
- `kinds` : La sorte de chaque ligne de `cells` (une ligne de groupe n'a qu'une cellule, son libellé) ; vide : toutes des lignes de données.
- `data` : Ce que la relecture retrouve d'un tableau que l'outil calcule (le récapitulatif de TVA : ses colonnes, l'acompte), en JSON.

## Classe `loom_ia.tools_builtin._document.content.NoteOut`

```python
@dataclass(frozen=True, slots=True)
class NoteOut
```

Un encadré de note validé.

**Attributs :**

- `title` : Son titre, s'il en a un.
- `runs` : Ses segments.
- `style` : Le style proposé choisi, s'il y en a un.

## Classe `loom_ia.tools_builtin._document.content.QuoteOut`

```python
@dataclass(frozen=True, slots=True)
class QuoteOut
```

Une citation validée.

**Attributs :**

- `runs` : Ses segments.
- `source` : Sa source, s'il y en a une.
- `style` : Le style proposé choisi, s'il y en a un.

## Classe `loom_ia.tools_builtin._document.content.ChartOut`

```python
@dataclass(frozen=True, slots=True)
class ChartOut
```

Un graphique : ses libellés, ses séries de nombres, et le type de ces nombres.

**Attributs :**

- `kind` : Le type de graphique.
- `title` : Son titre.
- `categories` : Ses libellés.
- `series` : (nom, valeurs) ; une valeur manquante est `None` : un trou dans la courbe.
- `value_type` : Le type des valeurs, pour leur format.
- `caption` : Sa légende, s'il y en a une.

## Classe `loom_ia.tools_builtin._document.content.ImageOut`

```python
@dataclass(frozen=True, slots=True)
class ImageOut
```

Une image posée dans le texte : ses octets, sa taille dans la page, ses textes.

`source` : l'adresse du fichier lu (« <dossier>/<chemin> »), que garde le .docx.

**Attributs :**

- `picture` : L'image reconnue.
- `source` : Son chemin, tel que le modèle l'a donné.
- `width` : Largeur dans la page, en centièmes de millimètre.
- `height` : Hauteur dans la page, en centièmes de millimètre.
- `alt` : Son texte de remplacement.
- `caption` : Sa légende, s'il y en a une.
- `wanted` : La largeur demandée : une grille d'images (style) pose côte à côte les « half ».

## Classe `loom_ia.tools_builtin._document.content.SectionOut`

```python
@dataclass(frozen=True, slots=True)
class SectionOut
```

Une section d'un document qui suit un modèle.

Sa place dans le modèle, son nom, son titre (`None` s'il n'est pas imprimé), ses
blocs ; `fixed` : un texte fixe.

**Attributs :**

- `position` : Sa place dans le modèle, à partir de 1.
- `name` : Son nom.
- `title` : Son titre écrit, s'il l'est.
- `blocks` : Ses blocs.
- `fixed` : Son texte fixe, s'il en a un.

## Classe `loom_ia.tools_builtin._document.content.Prepared`

```python
@dataclass(frozen=True, slots=True)
class Prepared
```

Le document validé : son titre (pour les propriétés), ses blocs, les avertissements.

`blocks` : tous les blocs, à plat, dans l'ordre du document (le titre du document,
puis le titre et les blocs de chaque section) ; `sections` : les mêmes, par section,
pour un document qui suit un modèle.

**Attributs :**

- `title` : Le titre du document, pour ses propriétés.
- `blocks` : Ses blocs, hors des sections.
- `warnings` : Les avertissements de la validation.
- `template` : Le modèle suivi, que le document garde dans ses propriétés ; `None` sans modèle.
- `sections` : Ses sections, s'il suit un modèle.

## Classe `loom_ia.tools_builtin._document.content._State`

```python
@dataclass(slots=True)
class _State
```

Ce que la validation garde d'un bloc à l'autre, sections comprises.

**Attributs :**

- `problems` : Les erreurs et avertissements.
- `zone` : Le fuseau des dates.
- `pictures` : Les images lues, par chemin.
- `labels` : Les libellés du style.
- `allowed` : Les styles proposés permis, par type de bloc.
- `level` : Le niveau du dernier titre.
- `last_table` : Le dernier tableau écrit : la source d'un graphique.
- `targets` : Les titres du document qu'un renvoi peut viser, et ceux qui ne s'impriment pas.
- `notes` : Les notes de bas de page déjà écrites.

## Fonction `loom_ia.tools_builtin._document.content.prepare`

```python
def prepare(arguments: WriteArguments, zone: ZoneInfo, pictures: Pictures=NO_PICTURES, template: Template | None=None, labels: Labels=FRENCH, allowed: Allowed=NO_STYLES) -> Prepared
```

Valide le document entier ; `ToolError` qui liste toutes les fautes trouvées.

Sans disque : les images sont lues avant, par l'outil (`pictures`). `template` :
le modèle que le document suit ; il s'écrit alors par sections (`sections`), et
l'outil pose les sections dans l'ordre du modèle, avec leurs titres et leur texte
fixe. `labels` : la langue du style, et le libellé de la ligne de total.
`allowed` : les styles proposés que les blocs peuvent choisir.

## Fonction `loom_ia.tools_builtin._document.content._sections`

```python
def _sections(arguments: WriteArguments, template: Template, state: _State) -> list[SectionOut]
```

Les sections du document, dans l'ordre du modèle.

Celles qu'il remplit, et celles au texte fixe ; les fautes du plan sont dites avec
les sections attendues.

## Fonction `loom_ia.tools_builtin._document.content._blocks`

```python
def _blocks(written: Sequence[WrittenBlock], label: str, state: _State, *, in_section: bool=False) -> list[BlockOut]
```

Les blocs validés ; `label` nomme leur place dans les messages (« bloc », « section « Message », bloc »).

## Fonction `loom_ia.tools_builtin._document.content._style`

```python
def _style(block: WrittenBlock, where: str, state: _State) -> str | None
```

Le style proposé qu'un bloc choisit.

Permis pour son type (et, pour un titre, son niveau) dans ce document ; sinon une
faute qui dit les styles permis.

## Fonction `loom_ia.tools_builtin._document.content.lines`

```python
def lines(text: str) -> str
```

Les fins de ligne ramenées à `\n`.

Un `\r\n` ou un `\r` venu du modèle (un texte collé d'un document Windows)
laisserait un `\r` brut dans le document (B-3).

## Fonction `loom_ia.tools_builtin._document.content._list`

```python
def _list(block: ListBlock, where: str, state: _State) -> ListOut
```

Une liste validée : éléments et sous-listes (trois niveaux en tout), cases d'une liste à cocher, nombre d'éléments borné.

## Fonction `loom_ia.tools_builtin._document.content._fields`

```python
def _fields(block: Fields, where: str, state: _State) -> FieldsOut
```

Des libellés et leurs valeurs.

Un libellé est du texte simple ; une valeur prend le balisage d'un paragraphe, et
vide, elle laisse une ligne à remplir.

## Fonction `loom_ia.tools_builtin._document.content._code`

```python
def _code(block: Code, where: str, problems: Problems) -> CodeOut
```

Du texte préformaté.

Ni balisage ni espaces retirées ; les lignes vides du début et de la fin seules sont
ôtées (et les espaces qui finissent la dernière).

## Fonction `loom_ia.tools_builtin._document.content._table`

```python
def _table(block: Table, where: str, state: _State) -> tuple[TableOut, list[list[Stored]]]
```

Un tableau validé, et ses valeurs converties : colonnes et lignes bornées, types, colonnes calculées, groupes, totaux, largeurs.

## Fonction `loom_ia.tools_builtin._document.content._groups`

```python
def _groups(block: Table, where: str, problems: Problems) -> list[tuple[str | None, str, list[list[Scalar]]]]
```

Les lignes du tableau, par groupe : le libellé du groupe (`None` sans groupe), sa place dans les messages, ses lignes.

Pas de lignes simples entre des groupes.

## Fonction `loom_ia.tools_builtin._document.content._row`

```python
def _row(row: Sequence[Scalar], columns: Sequence[Column], given: Sequence[int], formulas: Mapping[int, _Formula], at: str, state: _State) -> list[Stored]
```

Une ligne : ses valeurs, dans l'ordre des colonnes données, puis celles que l'outil calcule ; un montant en euros arrondi au centime.

## Classe `loom_ia.tools_builtin._document.content._Formula`

```python
@dataclass(frozen=True, slots=True)
class _Formula
```

Une formule lue : son arbre (chaque colonne citée devient `c0`, `c1`…), et la colonne de chacune.

**Attributs :**

- `tree` : L'expression lue.
- `sources` : Les colonnes qu'elle lit.

## Fonction `loom_ia.tools_builtin._document.content._formulas`

```python
def _formulas(columns: Sequence[Column], headers: Sequence[str], where: str, problems: Problems) -> dict[int, _Formula]
```

Les colonnes calculées, dans l'ordre : leur formule lue et vérifiée.

## Fonction `loom_ia.tools_builtin._document.content._formula`

```python
def _formula(text: str, index: int, columns: Sequence[Column], headers: Sequence[str], at: str, problems: Problems) -> _Formula | None
```

La formule d'une colonne calculée, lue : des colonnes de nombres à gauche, les quatre opérations et des nombres ; `None`, avec une erreur, sinon.

## Fonction `loom_ia.tools_builtin._document.content._allowed`

```python
def _allowed(node: ast.expr, count: int) -> bool
```

Seulement les quatre opérations, le signe, des nombres et les colonnes citées.

## Fonction `loom_ia.tools_builtin._document.content._computed`

```python
def _computed(formula: _Formula, cells: Sequence[Stored], kind: ColumnType, at: str, problems: Problems) -> Stored
```

La valeur d'une colonne calculée, arrondie à ce que le document écrit ; vide si une colonne citée l'est.

## Fonction `loom_ia.tools_builtin._document.content._evaluated`

```python
def _evaluated(node: ast.expr, values: Sequence[Decimal]) -> Decimal
```

La valeur d'une formule pour une ligne, en décimal ; `ZeroDivisionError` pour une division par zéro.

## Fonction `loom_ia.tools_builtin._document.content._cents`

```python
def _cents(value: Stored) -> Stored
```

Un montant au centime, au demi supérieur ; le même s'il y est déjà.

## Fonction `loom_ia.tools_builtin._document.content._invoice`

```python
def _invoice(block: InvoiceTotals, where: str, state: _State) -> TableOut | None
```

Total HT, la TVA de chaque taux (sur le total HT de ce taux, au centime), Total TTC, et, avec `paid`, l'acompte versé et le net à payer.

Tirés du tableau qui précède.

## Fonction `loom_ia.tools_builtin._document.content._attendance`

```python
def _attendance(block: Attendance, where: str, state: _State) -> TableOut
```

Une feuille d'émargement.

« Participant » (`labels.participant`) puis une colonne par session ; une ligne
par participant, ses cases vides, pour signer.

## Fonction `loom_ia.tools_builtin._document.content._places`

```python
def _places(table: Table) -> list[str]
```

La place de chaque ligne d'un tableau, dans l'ordre : « ligne 3 », ou « groupe 2 « Cuisine », ligne 1 ».

## Fonction `loom_ia.tools_builtin._document.content._rate`

```python
def _rate(rate: Decimal, language: Language) -> str
```

Un taux de TVA tel qu'un libellé l'écrit : « 10 % », « 5,5 % » (« 10% » en anglais).

## Fonction `loom_ia.tools_builtin._document.content._chart`

```python
def _chart(block: Chart, last_table: tuple[Table, list[list[Stored]]] | None, where: str, problems: Problems, zone: ZoneInfo, language: Language='fr') -> ChartOut
```

Le graphique validé, ses données en ligne ou tirées du tableau qui le précède.

## Fonction `loom_ia.tools_builtin._document.content._from_table`

```python
def _from_table(block: Chart, last_table: tuple[Table, list[list[Stored]]] | None, where: str, problems: Problems, language: Language) -> tuple[list[str], list[tuple[str, tuple[float | None, ...]]], ColumnType] | None
```

Libellés, séries et type tirés du tableau ; `None` si on ne peut pas les tirer.

## Fonction `loom_ia.tools_builtin._document.content._check_chart`

```python
def _check_chart(kind: ChartKind, categories: Sequence[str], series: Sequence[tuple[str, tuple[float | None, ...]]], where: str, problems: Problems) -> None
```

Vérifie un graphique : libellés et séries bornés, longueurs égales ; un camembert, une seule série de valeurs positives.

## Fonction `loom_ia.tools_builtin._document.content._image`

```python
def _image(block: Image, pictures: Pictures, where: str, problems: Problems) -> ImageOut | None
```

Une image validée.

Le fichier vu dans la conversation, sa taille dans la page selon la largeur
demandée, sa légende et son texte de remplacement ; `None`, avec une erreur,
sinon.

## Fonction `loom_ia.tools_builtin._document.content.placed`

```python
def placed(wanted: ImageWidth, pixels_wide: int, pixels_high: int) -> tuple[int, int]
```

Largeur et hauteur dans la page (centièmes de mm) : la largeur demandée, les proportions de l'image, la hauteur bornée à celle de la page.

## Fonction `loom_ia.tools_builtin._document.content._check_images`

```python
def _check_images(arguments: WriteArguments, pictures: Pictures, problems: Problems) -> None
```

Les bornes du document : nombre d'images, octets des images distinctes.

## Fonction `loom_ia.tools_builtin._document.content._check_totals`

```python
def _check_totals(block: Table, where: str, problems: Problems) -> None
```

Un total vise une colonne du tableau, et un agrégat qui va à son type.

## Fonction `loom_ia.tools_builtin._document.content._total_row`

```python
def _total_row(block: Table, stored: Sequence[Sequence[Stored]], label: str, language: Language) -> tuple[str, ...]
```

La ligne de total (ou de sous-total) de ces lignes : un agrégat par colonne qui en a un, `label` à la place de la première sans.

## Fonction `loom_ia.tools_builtin._document.content._total_label`

```python
def _total_label(block: Table) -> int | None
```

La colonne où écrire « Total » : la première sans agrégat, texte d'abord (comme excel).

## Fonction `loom_ia.tools_builtin._document.content._aggregate`

```python
def _aggregate(aggregate: Aggregate, values: Sequence[Stored], kind: ColumnType, language: Language) -> str
```

L'agrégat d'une colonne (somme, moyenne, compte, minimum, maximum), mis en forme selon son type et la langue.

## Fonction `loom_ia.tools_builtin._document.content._widths`

```python
def _widths(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> tuple[tuple[float, ...], float]
```

La part de la largeur utile de chaque colonne, d'après ce qu'elle contient ; et la part de la page que le tableau demande (1 au plus).

Chaque colonne a un **plancher** : son mot d'en-tête le plus long, en gras, et sa
valeur la plus longue si elle est courte (`SHORT_VALUE_CHARS`) ; ni l'un ni
l'autre ne se coupe. Ce qui reste de la page va aux colonnes qui veulent plus
(un nom, un texte, qui passent à la ligne entre deux mots), en proportion. Un
partage seulement proportionnel écrasait les petites colonnes à côté d'une large :
« Rang », « Code » et « C-429 » coupés (essai réel du 29/10).

## Fonction `loom_ia.tools_builtin._document.content.display`

```python
def display(value: Stored, kind: ColumnType, language: Language='fr') -> str
```

La valeur telle qu'un document l'écrit, dans sa langue (à la française par défaut).

## Fonction `loom_ia.tools_builtin._document.content._english`

```python
def _english(value: float, kind: ColumnType) -> str
```

« €1,250.50 », « 15.0% » ; le signe moins devant le symbole (« -€12.00 »).

## Fonction `loom_ia.tools_builtin._document.content._french`

```python
def _french(said: str) -> str
```

« 1,250.50 » → « 1 250,50 » ; le signe moins reste un trait d'union.

## Fonction `loom_ia.tools_builtin._document.content.inline`

```python
def inline(text: str) -> Inline
```

Les segments d'un texte : `**gras**`, `*italique*`, `<u>souligné</u>`, le reste tel quel.

Un marqueur ouvre seulement s'il est suivi d'un caractère visible et refermé
plus loin ; il ferme seulement s'il suit un caractère visible. Sinon il
reste du texte : « 2 * 3 » ne met rien en italique. `<u>` ouvre s'il est refermé
plus loin et `</u>` ferme ce qui est ouvert, sans imbrication ; sinon, la balise
reste du texte.

## Fonction `loom_ia.tools_builtin._document.content.plain`

```python
def plain(runs: Inline) -> str
```

Le texte seul : sans emphase ni marque de note ; un renvoi par le titre visé.

## Fonction `loom_ia.tools_builtin._document.content.runs_of`

```python
def runs_of(block: BlockOut) -> Iterator[Inline]
```

Les textes en segments d'un bloc : paragraphe, éléments de liste (sous-listes comprises), note, citation, valeurs de champs, cellules d'un tableau.

## Fonction `loom_ia.tools_builtin._document.content.referenced`

```python
def referenced(blocks: Iterable[BlockOut]) -> frozenset[int]
```

Les rangs des titres qu'un renvoi vise, notes comprises.

## Classe `loom_ia.tools_builtin._document.content.Targets`

```python
@dataclass(frozen=True, slots=True)
class Targets
```

Les titres du document qu'un renvoi peut viser.

Leur rang, par clé (`key`), et leur texte ; les sections dont le nom ne s'imprime
pas ; tous les titres, pour le dire.

**Attributs :**

- `by_key` : Rang et texte de chaque titre, par clé ; `None` pour une clé ambiguë.
- `hidden` : Les sections dont le nom ne s'imprime pas.
- `titles` : Tous les titres, pour les dire.

## Fonction `loom_ia.tools_builtin._document.content._targets`

```python
def _targets(arguments: WriteArguments, template: Template | None) -> Targets
```

Les titres du document, dans l'ordre où ils seront écrits.

Ceux des blocs d'un document libre ; pour un modèle, le nom de chaque section
imprimée, puis ses titres. Deux titres de même clé : aucun renvoi ne sait lequel
viser (`None`).

## Fonction `loom_ia.tools_builtin._document.content._bare`

```python
def _bare(text: str, where: str, problems: Problems) -> None
```

Un titre, une légende, un libellé : ni lien, ni note, ni renvoi.

## Fonction `loom_ia.tools_builtin._document.content.rich`

```python
def rich(text: str, where: str, state: _State, *, allow: frozenset[Markup]=EVERYTHING) -> Inline
```

Le texte d'un paragraphe, d'un élément de liste, d'une note, d'une citation, d'une cellule.

L'emphase (`inline`), et ce que `allow` permet — un lien `[texte](https://…)`,
une note `^[texte]`, un renvoi `§[Titre]`. Une faute (note non refermée, schéma
de lien refusé, titre inconnu) est dite avec `where`.

`\^[` et `\§[` s'écrivent tels quels. Un `[texte](…)` sans schéma (« (voir) »)
reste du texte ; un schéma autre que https, http et mailto est refusé.

## Fonction `loom_ia.tools_builtin._document.content._closing`

```python
def _closing(text: str, opening: int) -> int | None
```

La place du `]` qui ferme le `[` en `opening` (les crochets d'un lien dans une note s'y emboîtent) ; `None` sans lui.

## Fonction `loom_ia.tools_builtin._document.content._target`

```python
def _target(written: str, where: str, state: _State) -> Run
```

Le renvoi à un titre : comparé comme les sections d'un modèle (sans numérotation, casse, accents ni emphase).

## Fonction `loom_ia.tools_builtin._document.content._expanded`

```python
def _expanded(runs: Inline, places: Sequence[Run | tuple[str, Inline]]) -> list[Run]
```

Les segments, chaque marque de place remplacée par son lien, sa note ou son renvoi, dans l'emphase qui l'entoure.

## Fonction `loom_ia.tools_builtin._document.content._merged`

```python
def _merged(runs: Sequence[Run]) -> Inline
```

Les segments voisins de même emphase et même lien, réunis.
