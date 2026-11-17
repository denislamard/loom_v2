# loom_ia.tools_builtin.excel.workbook

Module `loom_ia.tools_builtin.excel.workbook` de loom_v2 (fichier `src/loom_ia/tools_builtin/excel/workbook.py`).

Le classeur : description validée, puis mise en forme par le style maison, puis octets.

Le style est figé ici : police, couleurs par rôle, formats par type, largeurs,
volets gelés, filtre, légende ; les graphiques, dans `charts`. Deux descriptions
identiques donnent le même fichier, octet pour octet : les dates du classeur et
de l'archive sont fixées.

## Constantes de `loom_ia.tools_builtin.excel.workbook`

- `FIXED_DATE = dt.datetime(2000, 1, 1)` : Dates du classeur (docProps/core.xml) et de l'archive : fixes, pour que deux appels identiques donnent le même fichier (reprise idempotente, empreinte).
- `ZIP_DATE = (2000, 1, 1, 0, 0, 0)` : Date des parties de l'archive : fixe, pour qu'un même classeur donne les mêmes octets.
- `FONT_NAME = 'Calibri'` : Police du classeur.
- `FONT_SIZE = 11` : Taille de la police, en points.
- `BLUE = '0000FF'` : Couleur du texte d'une saisie et d'une hypothèse.
- `GREEN = '008000'` : Couleur du texte d'une cellule de rôle `link`.
- `GREY = '7F7F7F'` : Couleur du texte d'une note.
- `HEADER_FILL = 'D9E1F2'` : Fond des en-têtes.
- `INPUT_FILL = 'FFF2CC'` : Fond d'une cellule de saisie.
- `ASSUMPTION_FILL = 'FFFF99'` : Fond d'une hypothèse.
- `MIN_WIDTH = 8` : Largeur au moins d'une colonne, en caractères.
- `MAX_WIDTH = 60` : Largeur au plus d'une colonne, en caractères.
- `PORTRAIT_WIDTH = 90` : Largeur totale (en caractères) au-delà de laquelle un onglet s'imprime en paysage : à peu près ce que tient une page A4 en portrait, marges comprises.
- `CENTERED = frozenset({'date', 'datetime', 'year', 'boolean'})` : Types centrés : une date alignée à droite colle au texte de la colonne suivante.
- `NUMBER_FORMATS = {'text': '@', 'integer': '#,##0', 'decimal': '#,##0.00', 'amount_eur': '#,##0.00 "€"', 'amount_keur': '#,##0 "k€"', 'pe…` : Formats en syntaxe neutre : Excel applique les séparateurs de la locale du lecteur. Dates : formats intégrés n° 14 et 22, qui suivent la locale (D5 : adaptatif) ; négatifs avec le signe moins, zéro affiché 0.
- `FORMULA_WIDTH = {'text': 20, 'integer': 10, 'decimal': 12, 'amount_eur': 14, 'amount_keur': 12, 'percent': 9, 'date': 11, 'datetime': 1…` : Largeur d'une colonne de formules, dont la valeur n'est connue qu'au recalcul.

## Classe `loom_ia.tools_builtin.excel.workbook.Written`

```python
@dataclass(slots=True)
class Written
```

Ce que l'on retient d'un onglet écrit, pour le résumé et le recalcul.

## Classe `loom_ia.tools_builtin.excel.workbook.Prepared`

```python
@dataclass(frozen=True, slots=True)
class Prepared
```

La description validée, ses lignes chargées : prête à écrire.

**Attributs :**

- `sheets` : Chaque onglet, et ses lignes chargées.
- `warnings` : Les avertissements de la validation.
- `zone` : Le fuseau où passent les dates-heures qui en portent un.
- `charts` : Les graphiques validés de chaque onglet, dans l'ordre des onglets.

## Fonction `loom_ia.tools_builtin.excel.workbook.prepare`

```python
def prepare(arguments: ExcelArguments, sources: Mapping[str, list[RowSpec]], zone: ZoneInfo) -> Prepared
```

Valide la description entière ; `ToolError` qui liste toutes les fautes trouvées.

`sources` : les lignes lues des fichiers, par chemin donné.

## Fonction `loom_ia.tools_builtin.excel.workbook._check_sheet`

```python
def _check_sheet(sheet: SheetSpec, rows: list[RowSpec], problems: Problems) -> None
```

Vérifie un onglet : colonnes et lignes bornées, en-têtes distincts, cellules du bon type.

## Fonction `loom_ia.tools_builtin.excel.workbook.build_workbook`

```python
def build_workbook(prepared: Prepared) -> tuple[Workbook, list[Written], list[str]]
```

Le classeur mis en forme ; les formules sont déjà validées par `prepare`.

## Fonction `loom_ia.tools_builtin.excel.workbook._write_sheet`

```python
def _write_sheet(ws: Worksheet, sheet: SheetSpec, rows: list[RowSpec], names: Mapping[str, str], problems: Problems, zone: ZoneInfo) -> Written
```

Écrit un onglet : en-têtes, lignes, formules, totaux et mise en forme ; rend ce qui a été écrit.

## Fonction `loom_ia.tools_builtin.excel.workbook._write_totals`

```python
def _write_totals(ws: Worksheet, sheet: SheetSpec, at: int, done: Written) -> None
```

Écrit la ligne de total d'un onglet : une formule d'agrégat sous chaque colonne qui en demande un.

## Fonction `loom_ia.tools_builtin.excel.workbook._total_label`

```python
def _total_label(sheet: SheetSpec) -> int | None
```

La colonne (à partir de 1) où écrire « Total » : la première sans agrégat, texte d'abord.

Une date en première colonne ne laisse plus le total sans libellé ; `None`
si toutes les colonnes ont leur agrégat.

## Fonction `loom_ia.tools_builtin.excel.workbook._finish`

```python
def _finish(ws: Worksheet, sheet: SheetSpec, rows: int, last: int, widths: list[int], has_input: bool, has_assumption: bool) -> None
```

Termine un onglet : volets gelés, filtre, largeurs, impression (paysage au besoin) et légende des couleurs.

## Fonction `loom_ia.tools_builtin.excel.workbook._page_setup`

```python
def _page_setup(ws: Worksheet) -> None
```

À l'impression et à l'export PDF : une page de large, en paysage si l'onglet est large.

Sans ce réglage, une colonne de trop part seule sur une deuxième page.

## Fonction `loom_ia.tools_builtin.excel.workbook._cell`

```python
def _cell(ws: Worksheet, row: int, column: int, value: Stored=None) -> Cell
```

Une cellule à écrire : jamais fusionnée, l'outil ne fusionne rien.

## Fonction `loom_ia.tools_builtin.excel.workbook._style`

```python
def _style(cell: Cell, role: str | None, kind: ColumnType) -> None
```

Met une cellule en forme selon son rôle (saisie, hypothèse, lien, total, note) et le format de son type.

## Fonction `loom_ia.tools_builtin.excel.workbook.to_bytes`

```python
def to_bytes(workbook: Workbook) -> bytes
```

Le fichier .xlsx, le même octet pour octet pour un même classeur.
