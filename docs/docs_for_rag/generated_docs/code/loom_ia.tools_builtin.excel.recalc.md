# loom_ia.tools_builtin.excel.recalc

Module `loom_ia.tools_builtin.excel.recalc` de loom_v2 (fichier `src/loom_ia/tools_builtin/excel/recalc.py`).

Recalcul par LibreOffice : vérifier qu'aucune formule n'est en erreur, sur une copie.

LibreOffice réécrirait les formats du classeur : il ne recalcule qu'une copie,
dans un dossier temporaire, et le fichier livré reste celui d'openpyxl.

## Constantes de `loom_ia.tools_builtin.excel.recalc`

- `ERRORS = frozenset({'#NULL!', '#DIV/0!', '#VALUE!', '#REF!', '#NAME?', '#NUM!', '#N/A'})` : Les valeurs d'erreur d'Excel.

## Fonction `loom_ia.tools_builtin.excel.recalc.recalculate`

```python
def recalculate(data: bytes, formulas: Sequence[str], soffice: Path, timeout: float) -> JsonValue
```

Fait recalculer une copie par LibreOffice ; `ToolError` si une formule est en erreur.

Un profil LibreOffice neuf par appel : deux recalculs simultanés ne se
disputent pas le verrou du profil. Le fichier d'origine n'est pas touché.

## Classe `loom_ia.tools_builtin.excel.recalc.Recalc`

```python
@dataclass(frozen=True, slots=True)
class Recalc
```

Le recalcul, tel que la config le règle : l'exécutable et son délai.
