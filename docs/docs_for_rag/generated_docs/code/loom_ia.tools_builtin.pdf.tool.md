# loom_ia.tools_builtin.pdf.tool

Module `loom_ia.tools_builtin.pdf.tool` de loom_v2 (fichier `src/loom_ia/tools_builtin/pdf/tool.py`).

L'outil `pdf` monté : écrire des PDF (reportlab), lire leur texte (pypdf) ; et sa fabrique.

## Constantes de `loom_ia.tools_builtin.pdf.tool`

- `NAME = 'pdf'` : Nom de l'outil.

## Classe `loom_ia.tools_builtin.pdf.tool.PdfCall`

```python
class PdfCall(DocumentCall)
```

Les arguments de `pdf` : l'écriture commune, et les pages d'une lecture.

## Classe `loom_ia.tools_builtin.pdf.tool.PdfTool`

```python
class PdfTool(DocumentTool)
```

L'outil `pdf` monté avec ses dossiers et son fuseau.

## Fonction `loom_ia.tools_builtin.pdf.tool.build`

```python
def build(params: BaseModel, context: BuiltinContext) -> PdfTool
```

La fabrique de `loom.pdf` : les dossiers comme `local_files`, pypdf vérifié.

## Fonction `loom_ia.tools_builtin.pdf.tool._fonts_said`

```python
def _fonts_said(styles: Sequence[Style]) -> str | None
```

Les polices du style que le PDF n'écrit pas telles quelles : embarquées (`fonts`), ou rendues par une famille standard du PDF.
