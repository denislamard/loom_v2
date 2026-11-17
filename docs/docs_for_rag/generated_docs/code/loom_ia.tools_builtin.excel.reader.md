# loom_ia.tools_builtin.excel.reader

Module `loom_ia.tools_builtin.excel.reader` de loom_v2 (fichier `src/loom_ia/tools_builtin/excel/reader.py`).

Lecture d'un classeur .xlsx : ce qu'il contient, rendu en JSON pour le modèle.

Un classeur est un zip de XML qui vient de n'importe où. Avant openpyxl :

- le zip est contrôlé (taille décompressée, nombre de membres) : un fichier
  de 762 Ko qui se décompresse en 312 Mo bloquait openpyxl plus de deux
  minutes en mode normal ;
- openpyxl ne s'ouvre qu'en **lecture seule** (`read_only`), qui lit la
  feuille au fil de l'eau et s'arrête à la borne de lignes ; le mode normal
  charge tout en mémoire (456 Mo pour 100 000 lignes) ;
- les entités XML sont laissées au parseur de la bibliothèque standard
  (expat), qui refuse l'amplification (« billion laughs ») et ne charge pas
  d'entité externe.

Une passe en lecture seule pour les cellules et leurs formules ; une seconde,
seulement si la plage a des formules, pour les valeurs que le classeur a
gardées en cache. Un classeur écrit par openpyxl (donc par `excel` lui-même)
n'a pas de valeur en cache : ses formules sont rendues sans valeur, et le
résultat le dit.

## Constantes de `loom_ia.tools_builtin.excel.reader`

- `MAX_UNCOMPRESSED = 10 * MAX_SOURCE_BYTES` : Un .xlsx courant se compresse 4 à 10 fois (mesuré : 3,9 à 9,8). La borne porte sur la taille décompressée, pas sur le taux : une colonne de textes identiques se compresse 163 fois sans être une bombe, alors qu'une bombe dépasse toujours la taille.
- `MAX_WHOLE_MEMBER = 20000000` : Ce que load_workbook lit en entier avant de rendre la main, hors de toute échéance : textes partagés, styles, classeur, et toute feuille qui n'annonce pas sa dimension. Mesuré : 119 Mo de textes partagés, 5,7 s et 269 Mo par ouverture ; 46 Mo de styles, 14,1 s et 782 Mo. Au-delà, un membre n'est admis que s'il est une feuille qui annonce sa dimension : openpyxl la lit alors au fil de l'eau, sous l'échéance.
- `DIMENSION_WINDOW = 65536` : Où chercher <dimension> : il suit l'en-tête de la feuille, bien avant les données.
- `MAIN_NS = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'` : Espace de noms principal de SpreadsheetML.
- `SAFE_EXPAT = (2, 4, 0)` : Versions sûres face aux entités XML : expat refuse l'amplification depuis 2.4.0 ; libxml2 depuis 2.11 ; lxml ne résout plus que les entités internes depuis 5.0.
- `SAFE_LXML = (5, 0, 0)` : Version de lxml au moins, sûre contre l'expansion d'entités.
- `SAFE_LIBXML2 = (2, 11, 0)` : Version de libxml2 au moins, pour la même raison.
- `MAX_MEMBERS = 10000` : Parties au plus dans l'archive.
- `MAX_CELL_CHARS = 1000` : Caractères rendus au plus par cellule.
- `PREVIEW_ROWS = 5` : Lignes de l'aperçu d'un onglet.
- `PREVIEW_COLUMNS = 20` : Colonnes de l'aperçu d'un onglet.
- `PREVIEW_CHARS = 100` : Caractères par cellule dans l'aperçu.
- `READ_CHARS = 40000` : Ce que rend une lecture, en caractères JSON : sous le seuil de déport par défaut (50 000). Sans lui, 1 000 lignes de 200 textes de 1 000 caractères faisaient 200 millions de caractères pour un fichier de 423 Ko.
- `READ_DEADLINE = 20.0` : Sous le délai par défaut d'un outil (30 s) : au-delà, la lecture s'arrête d'elle-même.
- `READABLE = ('.xlsx', '.xlsm')` : Extensions lues.
- `OTHER_FORMATS = {'.xls': 'Excel 97-2003 (.xls)', '.xlsb': 'Excel binaire (.xlsb)', '.ods': 'OpenDocument (.ods)'}` : Formats qu'openpyxl ne lit pas : le dire plutôt que « pas un zip ».

## Classe `loom_ia.tools_builtin.excel.reader.Read`

```python
@dataclass(frozen=True, slots=True)
class Read
```

Les cellules lues d'une plage : valeurs, formules, et ce qu'il faut en dire.

**Attributs :**

- `first_row` : Première ligne de la plage.
- `first_column` : Première colonne de la plage.
- `rows` : Les valeurs, ligne par ligne.
- `formulas` : Les formules, par adresse de cellule.
- `without_value` : Formules sans valeur enregistrée (classeur jamais recalculé).
- `next_row` : Première ligne non rendue, s'il en reste une non vide plus bas.
- `columns_cut_after` : Dernière colonne rendue, si des colonnes plus à droite ont été laissées.
- `cut_cells` : Cellules dont le texte a été coupé.
- `budget_hit` : Vrai si la taille du résultat a arrêté la lecture.

## Fonction `loom_ia.tools_builtin.excel.reader._guarded`

```python
@contextmanager
def _guarded(where: str) -> Generator[None]
```

Toute faute du fichier devient une `ToolError` pour le modèle, jamais une erreur interne.

openpyxl lève ce que lève son parseur : `ParseError` (expat), mais aussi
`ValueError`, `IndexError`, `BadZipFile` (CRC faux), `AttributeError`
sur un onglet graphique, et `XMLSyntaxError` quand lxml est installé.

## Fonction `loom_ia.tools_builtin.excel.reader.check_suffix`

```python
def check_suffix(path: str) -> None
```

Refuse un fichier qui n'est pas un classeur lisible (.xlsx, .xlsm), en disant comment le convertir s'il en a un autre format.

## Fonction `loom_ia.tools_builtin.excel.reader.check_archive`

```python
def check_archive(data: bytes, where: str) -> None
```

Le zip avant openpyxl : un classeur, de taille raisonnable, qui s'ouvre vite.

Sous `_guarded` : un membre abîmé (`zlib.error`, membre chiffré) devient
une `ToolError`, pas une erreur interne.

## Fonction `loom_ia.tools_builtin.excel.reader._check_archive`

```python
def _check_archive(data: bytes, where: str) -> None
```

Vérifie l'archive d'un classeur avant de l'ouvrir : un zip, pas trop de parties, pas trop gros une fois décompressé ; `ToolError` sinon.

## Fonction `loom_ia.tools_builtin.excel.reader._sized_sheet`

```python
def _sized_sheet(archive: zipfile.ZipFile, member: zipfile.ZipInfo) -> bool
```

Vrai pour une feuille qui annonce sa dimension avant ses données.

Lu par le parseur, pas par une recherche d'octets : un commentaire
`<!--<dimension-->` ne compte pas, un `<x:dimension>` préfixé si. Le
nom du membre ne compte pas : openpyxl suit les rels, pas les chemins.

## Fonction `loom_ia.tools_builtin.excel.reader.xml_safety`

```python
def xml_safety() -> str | None
```

Pourquoi les classeurs ne peuvent pas être lus sans risque ici ; `None` s'ils le peuvent.

Les feuilles passent par expat ; avec lxml installé, openpyxl lit aussi
`workbook.xml` et les styles avec libxml2. Une version trop ancienne
laisserait passer l'amplification d'entités (« billion laughs »).

## Fonction `loom_ia.tools_builtin.excel.reader._sheet`

```python
def _sheet(book: Any, sheet: str | None, where: str) -> Any
```

L'onglet voulu (le premier si `None`) ; ni absent, ni graphique.

## Fonction `loom_ia.tools_builtin.excel.reader._reset`

```python
def _reset(ws: Any) -> None
```

La dimension déclarée peut mentir : sans ce reset, une feuille annoncée « A1 » ne rendait que A1 (test « dimension »).

## Fonction `loom_ia.tools_builtin.excel.reader.overview`

```python
def overview(data: bytes, where: str) -> dict[str, JsonValue]
```

Les onglets : nom, état, dimension déclarée, et le début de chacun.

## Fonction `loom_ia.tools_builtin.excel.reader._bounds`

```python
def _bounds(cells: str | None) -> tuple[int, int, int, int]
```

Première colonne, première ligne, dernière colonne, dernière ligne : dans l'ordre.

## Fonction `loom_ia.tools_builtin.excel.reader.read_range`

```python
def read_range(data: bytes, where: str, sheet: str, cells: str | None, max_rows: int) -> tuple[Read, str]
```

Les cellules d'une plage ; sans plage, la feuille depuis A1. Et l'état de l'onglet.

## Fonction `loom_ia.tools_builtin.excel.reader._size`

```python
def _size(cells: tuple[Any, ...]) -> int
```

La place d'une ligne dans le résultat, sans les cellules vides de droite.

## Fonction `loom_ia.tools_builtin.excel.reader._cached`

```python
def _cached(data: bytes, sheet: str, first_row: int, first_column: int, last_column: int, count: int, deadline: float, where: str) -> list[list[Any]]
```

Les valeurs en cache du classeur : une seconde passe, seulement s'il a des formules.

## Fonction `loom_ia.tools_builtin.excel.reader._iter`

```python
def _iter(ws: Any, first_row: int, first_column: int, last_column: int, last_row: int, deadline: float, where: str) -> Iterator[tuple[int, tuple[Any, ...]]]
```

Les lignes numérotées depuis `first_row`, l'échéance vérifiée toutes les 1 000 lignes.

Depuis la ligne 1 pour une plage plus bas : openpyxl parcourt les lignes
avant `min_row` sans rendre la main ; compter soi-même permet de s'arrêter.

## Fonction `loom_ia.tools_builtin.excel.reader._in_time`

```python
def _in_time(deadline: float, where: str) -> None
```

Un fil d'exécution ne s'annule pas : la lecture s'arrête d'elle-même à l'échéance.

## Classe `loom_ia.tools_builtin.excel.reader._Found`

```python
@dataclass(frozen=True, slots=True)
class _Found
```

Ce que la lecture d'une plage a trouvé.

**Attributs :**

- `rows` : Les valeurs, ligne par ligne.
- `formulas` : Les formules, par adresse de cellule.
- `without_value` : Formules sans valeur enregistrée.
- `cut_cells` : Cellules dont le texte a été coupé.

## Fonction `loom_ia.tools_builtin.excel.reader._read`

```python
def _read(raw: list[tuple[Any, ...]], cached: list[list[Any]], first_row: int, first_column: int) -> _Found
```

Les valeurs et les formules d'une plage, lues dans le classeur tel qu'écrit et dans ses valeurs enregistrées.

## Fonction `loom_ia.tools_builtin.excel.reader._is_formula`

```python
def _is_formula(cell: Any) -> bool
```

Une formule d'après le type de la cellule, pas d'après un « = » en tête de texte.

## Fonction `loom_ia.tools_builtin.excel.reader._json`

```python
def _json(value: object, limit: int=MAX_CELL_CHARS) -> tuple[JsonValue, bool]
```

Une valeur de cellule en JSON ; vrai si un texte a été coupé.

## Fonction `loom_ia.tools_builtin.excel.reader._trimmed`

```python
def _trimmed(rows: list[list[JsonValue]], written: list[list[bool]]) -> list[list[JsonValue]]
```

Sans les lignes vides de la fin, ni les colonnes vides de droite (selon ce qui est écrit).

## Fonction `loom_ia.tools_builtin.excel.reader.records`

```python
def records(data: bytes, where: str, sheet: str | None) -> list[dict[str, JsonValue]]
```

Un onglet comme source de `data` : la ligne 1 porte les en-têtes.

Les valeurs passent entières (pas de coupe à 1 000 caractères) ; une formule
sans valeur en cache est refusée plutôt que lue comme une cellule vide.

## Fonction `loom_ia.tools_builtin.excel.reader._source_rows`

```python
def _source_rows(ws: Any, deadline: float, where: str) -> list[tuple[Any, ...]]
```

En-tête et lignes, bornés : au-delà de la borne d'une source, inutile de tout lire.

## Fonction `loom_ia.tools_builtin.excel.reader._plain`

```python
def _plain(value: object) -> JsonValue
```

Une valeur de source, entière : nombres et booléens tels quels, le reste en texte.
