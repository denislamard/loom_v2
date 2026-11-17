# loom_ia.tools_builtin.excel.sources

Module `loom_ia.tools_builtin.excel.sources` de loom_v2 (fichier `src/loom_ia/tools_builtin/excel/sources.py`).

Sources de données : les lignes d'un CSV ou d'un JSON, dans l'ordre des colonnes d'un onglet.

## Fonction `loom_ia.tools_builtin.excel.sources.read_rows`

```python
def read_rows(data: bytes, suffix: str, sheet: SheetSpec, where: str, tab: str | None=None) -> list[RowSpec]
```

Les lignes d'un CSV, d'un JSON ou d'un onglet .xlsx, dans l'ordre des colonnes.

## Fonction `loom_ia.tools_builtin.excel.sources._json_records`

```python
def _json_records(data: bytes, where: str) -> list[dict[str, Scalar]]
```

Les lignes d'un fichier JSON : une liste d'objets aux valeurs simples ; `ToolError` sinon.
