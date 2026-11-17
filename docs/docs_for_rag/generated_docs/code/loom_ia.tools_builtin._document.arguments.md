# loom_ia.tools_builtin._document.arguments

Module `loom_ia.tools_builtin._document.arguments` de loom_v2 (fichier `src/loom_ia/tools_builtin/_document/arguments.py`).

Ce que voit le modèle : un document décrit par ses blocs, commun à `word` et `pdf`.

Le modèle dit ce que **sont** les blocs — heading, paragraph, list, table aux
colonnes typées, note, quote — et jamais leur apparence : police, tailles,
couleurs, marges, sauts de page et numérotation des pages viennent du style.
Clés et vocabulaire en anglais, comme `excel` ; descriptions en français.

Pydantic seulement : ni disque, ni bibliothèque de rendu.

## Constantes de `loom_ia.tools_builtin._document.arguments`

- `MAX_BLOCKS = 1000` : Blocs au plus dans un document.
- `MAX_TEXT = 20000` : Longueur au plus d'un texte de bloc, en caractères.
- `MAX_ITEMS = 200` : Éléments au plus dans une liste.
- `MAX_TABLE_COLUMNS = 8` : Colonnes au plus dans un tableau.
- `MAX_TABLE_ROWS = 500` : Lignes au plus dans un tableau.
- `MAX_CATEGORIES = 50` : Libellés au plus dans un graphique.
- `MAX_SLICES = 12` : Parts au plus dans un camembert.
- `MAX_SERIES = 6` : Séries au plus dans un graphique.
- `MAX_IMAGE_BYTES = 10000000` : Images : par image, pour tout le document, nombre de blocs, pixels d'une image.
- `MAX_IMAGES_BYTES = 40000000` : Taille au plus de toutes les images d'un document, en octets.
- `MAX_IMAGES = 50` : Images au plus dans un document.
- `MAX_PIXELS = 40000000` : Pixels au plus dans une image.
- `MAX_SOURCE_BYTES = 20000000` : Taille au plus d'un fichier lu, en octets.
- `MAX_OUTPUT_BYTES = 50000000` : Taille au plus d'un fichier écrit, en octets.
- `MAX_WARNINGS = 20` : Avertissements rendus au plus.
- `MAX_ERRORS_SAID = 10` : Erreurs citées au plus dans un refus.
- `READ_CHARS = 40000` : Ce que rend une lecture, en caractères JSON : sous le seuil de déport par défaut (50 000), comme la lecture d'un classeur.
- `ChartKind = Literal['bar', 'horizontal_bar', 'line', 'pie']` : Type de graphique : barres, barres horizontales, courbe ou camembert.
- `ImageWidth = Literal['full', 'half', 'small']` : Largeur d'une image : toute la largeur, la moitié, ou petite.
- `MAX_SIGNERS = 4` : Signataires au plus d'un bloc signature.
- `MAX_FIELDS = 30` : Champs au plus d'un bloc fields.
- `MAX_FIELD_LABEL = 80` : Longueur au plus d'un libellé de champ.
- `MAX_FIELD_VALUE = 500` : Longueur au plus d'une valeur de champ.
- `MAX_CODE_LINES = 200` : Lignes au plus d'un bloc code.
- `MAX_PARTICIPANTS = 100` : Participants au plus d'une feuille d'émargement.
- `MAX_SESSIONS = 10` : Séances au plus d'une feuille d'émargement.
- `MAX_ATTENDANCE_LABEL = 80` : Longueur au plus d'un libellé de la feuille d'émargement.
- `Block = Annotated[Heading | Paragraph | ListBlock | Table | Note | Quote, Field(discriminator='type')]` : Les blocs que le modèle voit sans bloc facultatif.
- `WrittenBlock = Annotated[Heading | Paragraph | ListBlock | Table | Note | Quote | Chart | Image | Signature | Contents | Fields | Code…` : Tous les blocs : ce qu'une écriture porte, quel que soit l'outil. Une liste à cocher (`CheckList`) y passe comme liste : elle en est une.
- `STYLED_BLOCKS = ('heading', 'paragraph', 'list', 'table', 'note', 'quote')` : Les blocs qui prennent un style proposé (`style`), par leur type.
- `STYLE_SAID = 'Un style proposé pour ce bloc (voir Styles proposés) ; sinon, rien.'` : Description du champ `style` d'un bloc, lue par le modèle.
- `Proposed = tuple[tuple[str, tuple[str, ...]], ...]` : Styles proposés : (type de bloc, noms des styles).
- `Action = Literal['write', 'read']` : Action de l'outil : écrire ou lire.
- `TemplatedAction = Literal['write', 'read', 'template']` : Les actions d'un outil monté avec des modèles de document.
- `BLOCKS_SAID = "Pour write : les blocs, dans l'ordre."` : Description du champ `blocks`, lue par le modèle.
- `ACTION_SAID = 'write (par défaut) : écrire ; read : lire un fichier (droit read) ; template : lire un modèle de document.'` : Description du champ `action`, lue par le modèle.
- `TEMPLATE_SAID = 'Pour write : le modèle suivi, ou none (document libre) ; pour template : le modèle à lire.'` : Description du champ `template`, lue par le modèle.
- `SECTIONS_SAID = "Pour write avec un modèle : le contenu de chaque section, par son nom (voir action template) ; l'outil écrit les titre…` : Description du champ `sections`, lue par le modèle.
- `_SUFFIXES = {'chart': 'Chart', 'image': 'Image', 'signature': 'Signature', 'toc': 'Toc', 'checkboxes': 'Checkboxes', 'fields': 'Fie…` : Le nom de l'appel selon ses blocs facultatifs : `WordChartImageCall`…
- `WithCharts = variant(DocumentCall, blocks=('chart',))` : Le même appel, avec le bloc `chart` (tests du vocabulaire commun).

## Classe `loom_ia.tools_builtin._document.arguments._Model`

```python
class _Model(BaseModel)
```

La base des modèles d'arguments : figés, sans champ inconnu.

Sa docstring ne part pas dans le schéma : Pydantic ne la transmet pas aux sous-classes.

## Classe `loom_ia.tools_builtin._document.arguments.Heading`

```python
class Heading(_Model)
```

Bloc `heading` : un titre du document.

Pas de description dans le schéma : `_light` la retire, la description de l'outil explique le vocabulaire.

**Attributs :**

- `type` : `heading`.
- `text` : Le texte du titre (l'emphase seule).
- `level` : Le niveau, de 1 à 3.

## Classe `loom_ia.tools_builtin._document.arguments.Paragraph`

```python
class Paragraph(_Model)
```

Bloc `paragraph` : un paragraphe de texte, avec le balisage permis (gras, italique, souligné, liens, notes, renvois).

**Attributs :**

- `type` : `paragraph`.
- `text` : Le texte ; `\n` va à la ligne dans le même paragraphe.

## Classe `loom_ia.tools_builtin._document.arguments.SubItem`

```python
class SubItem(_Model)
```

Un élément de sous-liste qui porte à son tour une sous-liste : le troisième niveau.

## Classe `loom_ia.tools_builtin._document.arguments.Item`

```python
class Item(_Model)
```

Un élément de liste qui porte une sous-liste (deux niveaux en dessous au plus).

## Classe `loom_ia.tools_builtin._document.arguments.ListBlock`

```python
class ListBlock(_Model)
```

Bloc `list` : une liste à puces ou numérotée, jusqu'à trois niveaux (`Item`, `SubItem`).

**Attributs :**

- `type` : `list`.
- `items` : Les éléments : un texte, ou un élément qui porte une sous-liste.
- `ordered` : Vrai pour une liste numérotée.

## Classe `loom_ia.tools_builtin._document.arguments.CheckList`

```python
class CheckList(ListBlock)
```

Une liste, à cocher (`checkboxes`) : chaque élément commence par [x] ou [ ].

## Classe `loom_ia.tools_builtin._document.arguments.Column`

```python
class Column(_Model)
```

Une colonne d'un tableau.

**Attributs :**

- `header` : Son en-tête, qui la désigne aussi dans `totals`, `compute` et les graphiques.
- `type` : Son type de valeur (`text`, `integer`, `amount_eur`…) : l'outil écrit les valeurs à la française.
- `compute` : Une colonne calculée par l'outil, ligne par ligne, par une formule sur les nombres de la ligne (`"{Qté} * {PU HT}"`) : les lignes ne la donnent pas.

## Classe `loom_ia.tools_builtin._document.arguments.Group`

```python
class Group(_Model)
```

Un groupe de lignes (un lot, une pièce) : son libellé, puis ses lignes.

## Classe `loom_ia.tools_builtin._document.arguments.Table`

```python
class Table(_Model)
```

Bloc `table` : un tableau.

**Attributs :**

- `type` : `table`.
- `columns` : Ses colonnes.
- `rows` : Ses lignes, une liste de valeurs chacune, ou des groupes de lignes (`Group`).
- `totals` : En-tête → agrégat (`sum`, `average`, `count`, `min`, `max`), calculé par l'outil ; un sous-total par groupe.
- `caption` : Sa légende.

## Classe `loom_ia.tools_builtin._document.arguments.Note`

```python
class Note(_Model)
```

Bloc `note` : un encadré, ce que le lecteur doit retenir ou une mise en garde.

**Attributs :**

- `type` : `note`.
- `text` : Son texte.
- `title` : Son titre.

## Classe `loom_ia.tools_builtin._document.arguments.Quote`

```python
class Quote(_Model)
```

Bloc `quote` : une citation.

**Attributs :**

- `type` : `quote`.
- `text` : Le texte cité.
- `source` : Sa source.

## Classe `loom_ia.tools_builtin._document.arguments.Series`

```python
class Series(_Model)
```

Une série d'un graphique aux données en ligne.

**Attributs :**

- `name` : Son nom, dans la légende.
- `values` : Ses valeurs, une par libellé.

## Classe `loom_ia.tools_builtin._document.arguments.FromTable`

```python
class FromTable(_Model)
```

Les données du tableau qui précède le graphique : ses colonnes, par en-tête.

## Classe `loom_ia.tools_builtin._document.arguments.Chart`

```python
class Chart(_Model)
```

Bloc `chart` : un graphique, si l'outil le permet (`blocks`).

Ses données viennent du tableau qui le précède (`from_table`), ou sont données en ligne (`categories` et `series`) : l'un ou l'autre.

**Attributs :**

- `type` : `chart`.
- `kind` : `bar`, `horizontal_bar`, `line` ou `pie`.
- `title` : Son titre.
- `categories` : Les libellés, pour des données en ligne.
- `series` : Les séries, pour des données en ligne.
- `value_type` : Le type des valeurs en ligne ; `from_table` prend celui des colonnes.
- `from_table` : Les colonnes du tableau qui précède.
- `caption` : Sa légende.

## Classe `loom_ia.tools_builtin._document.arguments.Image`

```python
class Image(_Model)
```

Bloc `image` : une image PNG ou JPEG, si l'outil le permet (`blocks`).

**Attributs :**

- `type` : `image`.
- `path` : « <dossier>/<chemin> » d'un fichier d'un dossier qui a le droit `read` : l'outil le lit lui-même.
- `width` : `full` (par défaut), `half` ou `small` (un logo).
- `caption` : Sa légende, dessous.
- `alt` : Ce que montre l'image pour qui ne la voit pas ; la légende à défaut.

## Classe `loom_ia.tools_builtin._document.arguments.Signature`

```python
class Signature(_Model)
```

Bloc `signature` : la zone de signature, si l'outil le permet (`blocks`).

**Attributs :**

- `type` : `signature`.
- `signers` : Un libellé par signataire (« Le client »), `MAX_SIGNERS` au plus.
- `mention` : La mention au-dessus des signatures (« Bon pour accord »).

## Classe `loom_ia.tools_builtin._document.arguments.Contents`

```python
class Contents(_Model)
```

Bloc `toc` : le sommaire, tiré des titres, si l'outil le permet (`blocks`).

**Attributs :**

- `type` : `toc`.

## Classe `loom_ia.tools_builtin._document.arguments.Fields`

```python
class Fields(_Model)
```

Des libellés et leurs valeurs : une paire est une liste de deux textes (plutôt qu'un tuple, que tous les fournisseurs n'acceptent pas dans un schéma).

## Classe `loom_ia.tools_builtin._document.arguments.Code`

```python
class Code(_Model)
```

Bloc `code` : du texte préformaté (code, commande, journal), écrit tel quel, si l'outil le permet (`blocks`).

**Attributs :**

- `type` : `code`.
- `text` : Le texte, `MAX_CODE_LINES` lignes au plus.

## Classe `loom_ia.tools_builtin._document.arguments.TaxColumns`

```python
class TaxColumns(_Model)
```

Les colonnes du tableau qui précède, par en-tête : les montants HT et leurs taux de TVA (l'aide du bloc les décrit).

## Classe `loom_ia.tools_builtin._document.arguments.InvoiceTotals`

```python
class InvoiceTotals(_Model)
```

Bloc `invoice_totals` : le récapitulatif de TVA d'une facture, si l'outil le permet (`blocks`).

L'outil écrit le total HT, la TVA de chaque taux, le total TTC et, avec `paid`, l'acompte et le net à payer.

**Attributs :**

- `type` : `invoice_totals`.
- `from_table` : Les colonnes des montants HT et des taux du tableau qui précède.
- `paid` : L'acompte déjà versé.

## Classe `loom_ia.tools_builtin._document.arguments.Attendance`

```python
class Attendance(_Model)
```

Une feuille d'émargement : une ligne par participant, une case par session.

## Fonction `loom_ia.tools_builtin._document.arguments._styled`

```python
@cache
def _styled(kind: type[_Model], names: tuple[str, ...]) -> type[_Model]
```

Le bloc, avec un champ `style` : un des styles qui lui sont proposés.

## Fonction `loom_ia.tools_builtin._document.arguments.block_union`

```python
def block_union(blocks: Iterable[str], styles: Proposed=()) -> object
```

Les blocs du schéma pour ces blocs facultatifs, dans un ordre fixe.

`styles` : les styles proposés, par type de bloc (le champ `style` des blocs qui
en ont).

## Fonction `loom_ia.tools_builtin._document.arguments.style_of`

```python
def style_of(block: object) -> str | None
```

Le style proposé qu'un bloc a choisi ; `None` sans style.

## Classe `loom_ia.tools_builtin._document.arguments.WriteArguments`

```python
class WriteArguments(_Model)
```

Une écriture : le document, ses blocs (document libre) ou ses sections (modèle).

### Méthode `loom_ia.tools_builtin._document.arguments.WriteArguments.all_blocks`

```python
def all_blocks(self) -> list[WrittenBlock]
```

Tous les blocs, sections comprises, dans l'ordre où ils sont donnés.

## Classe `loom_ia.tools_builtin._document.arguments.ReadArguments`

```python
class ReadArguments(_Model)
```

Une lecture : où commencer, selon le format (bloc ou page).

## Classe `loom_ia.tools_builtin._document.arguments.TemplateArguments`

```python
class TemplateArguments(_Model)
```

La lecture d'un modèle de document déclaré (`action: template`).

## Classe `loom_ia.tools_builtin._document.arguments.DocumentCall`

```python
class DocumentCall(_Model)
```

Ce que voit le modèle : une écriture (par défaut) ou une lecture, à plat.

Chaque format ajoute ses champs de lecture et les nomme dans `READ_FIELDS`.

### Méthode `loom_ia.tools_builtin._document.arguments.DocumentCall.request`

```python
def request(self) -> WriteArguments | ReadArguments | TemplateArguments
```

La demande que l'appel porte : écrire (`WriteArguments`), lire (`ReadArguments`) ou lire un modèle (`TemplateArguments`).

## Fonction `loom_ia.tools_builtin._document.arguments.variant`

```python
def variant(base: type[DocumentCall], *, blocks: tuple[str, ...]=(), templates: tuple[str, ...]=(), styles: Mapping[str, Iterable[str]] | None=None) -> type[DocumentCall]
```

L'appel d'un format (`WordCall`…), élargi aux réglages de l'outil.

`blocks` (les blocs facultatifs déclarés, dans n'importe quel ordre) : `blocks` du
schéma gagne les leurs. `templates` (les noms des modèles déclarés) : l'action
`template`, le champ `template`, obligatoire à l'écriture, et `sections`.
`styles` (les styles proposés, par type de bloc) : le champ `style` de ces blocs.
Sans réglage, `base` lui-même. Le nom du modèle ne paraît pas dans le schéma allégé
(`input_schema`).

## Fonction `loom_ia.tools_builtin._document.arguments._variant`

```python
@cache
def _variant(base: type[DocumentCall], chosen: tuple[str, ...], templates: tuple[str, ...], proposed: Proposed=()) -> type[DocumentCall]
```

Les arguments de l'outil élargis aux blocs facultatifs choisis, aux modèles et aux styles proposés ; la base telle quelle sans rien de tout cela.

## Fonction `loom_ia.tools_builtin._document.arguments._template_rule`

```python
def _template_rule[M: DocumentCall](self: M) -> M
```

Avec des modèles : write nomme le sien (ou none), template nomme celui à lire.

## Fonction `loom_ia.tools_builtin._document.arguments.parse`

```python
def parse(model: type[DocumentCall], arguments: Mapping[str, JsonValue]) -> WriteArguments | ReadArguments | TemplateArguments
```

Les arguments validés ; `ToolError` qui dit chaque champ fautif (10 au plus).

## Fonction `loom_ia.tools_builtin._document.arguments.input_schema`

```python
def input_schema(model: type[DocumentCall]) -> dict[str, JsonValue]
```

Le schéma des arguments, allégé de ce qui ne sert pas au modèle.

Ni les titres que Pydantic ajoute à chaque champ, ni les docstrings des
modèles (écrites pour qui lit le code), ni les `default: null` des champs
facultatifs : le schéma part à chaque appel du modèle.

## Fonction `loom_ia.tools_builtin._document.arguments._light`

```python
def _light(node: object, *, names: bool=False) -> object
```

`names` : `node` est une table de propriétés, dont les clés sont des noms.

## Classe `loom_ia.tools_builtin._document.arguments.Problems`

```python
@dataclass(slots=True)
class Problems
```

Les fautes d'un passage de validation, dites ensemble ; et les avertissements.

### Méthode `loom_ia.tools_builtin._document.arguments.Problems.error`

```python
def error(self, said: str) -> None
```

Note une erreur : le document sera refusé.

### Méthode `loom_ia.tools_builtin._document.arguments.Problems.warn`

```python
def warn(self, said: str) -> None
```

Note un avertissement, une fois, `MAX_WARNINGS` au plus.

### Méthode `loom_ia.tools_builtin._document.arguments.Problems.raise_if_any`

```python
def raise_if_any(self) -> None
```

Lève `ToolError` s'il y a des erreurs : « Document refusé », puis les `MAX_ERRORS_SAID` premières.
