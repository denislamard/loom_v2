# loom_ia.tools_builtin.excel.formulas

Module `loom_ia.tools_builtin.excel.formulas` de loom_v2 (fichier `src/loom_ia/tools_builtin/excel/formulas.py`).

Formules et noms d'onglets : ce qu'Excel et LibreOffice évalueront, vérifié sans eux.

Fonctions anglaises d'une liste blanche, séparateur « , », onglets cités qui
existent et bien quotés, références dans les bornes de la feuille. Les
fonctions postérieures à Excel 2007 reçoivent le préfixe `_xlfn.`.

## Constantes de `loom_ia.tools_builtin.excel.formulas`

- `ALLOWED_FUNCTIONS = frozenset({'SUM', 'SUMIF', 'SUMIFS', 'AVERAGE', 'AVERAGEIF', 'AVERAGEIFS', 'COUNT', 'COUNTA', 'COUNTBLANK', 'COUNTIF', …` : Fonctions permises : elles s'évaluent dans Excel 2010+ et dans LibreOffice.
- `PREFIXED_FUNCTIONS = frozenset({'TEXTJOIN', 'CONCAT', 'IFS', 'SWITCH', 'MAXIFS', 'MINIFS'})` : Postérieures à Excel 2007 : le fichier les nomme avec le préfixe _xlfn., ajouté ici.
- `SPILL_FUNCTIONS = frozenset({'XLOOKUP', 'XMATCH', 'FILTER', 'SORT', 'SORTBY', 'UNIQUE', 'SEQUENCE', 'RANDARRAY'})` : Fonctions à débordement : openpyxl n'écrit pas leurs métadonnées.
- `FRENCH_FUNCTIONS = {'SOMME': 'SUM', 'SOMME.SI': 'SUMIF', 'SOMME.SI.ENS': 'SUMIFS', 'MOYENNE': 'AVERAGE', 'NB': 'COUNT', 'NBVAL': 'COUNTA',…` : Fonctions d'Excel en français → leur nom anglais, celui que le fichier garde.
- `AGGREGATES = {'sum': 'SUM', 'average': 'AVERAGE', 'count': 'COUNT', 'min': 'MIN', 'max': 'MAX'}` : Agrégat d'une colonne → fonction d'Excel.

## Fonction `loom_ia.tools_builtin.excel.formulas.sheet_name_problem`

```python
def sheet_name_problem(name: str) -> str | None
```

Pourquoi Excel refuserait ce nom d'onglet, ou `None`.

## Fonction `loom_ia.tools_builtin.excel.formulas.quoted`

```python
def quoted(name: str) -> str
```

Le nom d'onglet tel qu'une formule doit l'écrire.

## Fonction `loom_ia.tools_builtin.excel.formulas.check_formula`

```python
def check_formula(formula: str, where: str, sheets: Mapping[str, str], problems: Problems) -> str
```

La formule telle qu'écrite dans le fichier (préfixes _xlfn. ajoutés), ou une erreur.

`sheets` : nom en minuscules → nom exact, des onglets du classeur.

## Fonction `loom_ia.tools_builtin.excel.formulas._prefixed`

```python
def _prefixed(formula: str) -> str
```

Préfixe de `_xlfn.` les fonctions récentes d'Excel qui l'exigent, hors des chaînes de la formule.
