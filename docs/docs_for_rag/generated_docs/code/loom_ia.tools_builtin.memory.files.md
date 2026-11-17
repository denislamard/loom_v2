# loom_ia.tools_builtin.memory.files

Module `loom_ia.tools_builtin.memory.files` de loom_v2 (fichier `src/loom_ia/tools_builtin/memory/files.py`).

Fichiers pour `add_file` : texte, Word et PDF, ramenés à du markdown.

- **Texte** (.md, .markdown, .txt) : UTF-8, tel quel. Titre : le premier titre
  « # », sinon le nom du fichier.
- **Word** (.docx, .docm, .dotx) : lu par le lecteur de `loom.word`, qui ne
  dépend que de la bibliothèque standard. Ses blocs deviennent du markdown :
  titres en « # » selon leur niveau (ce qui fait les sections du découpage),
  listes, tableaux, citations. Titre : le paragraphe au style Titre, sinon le nom
  du fichier — jamais les propriétés du document (`dc:title`), souvent restées
  celles du modèle dont il est parti.
- **PDF** : lu par le lecteur de `loom.pdf` (pypdf, extra `pdf`), dans un
  process borné en mémoire et en temps. Le texte vient page par page, sans titre
  repérable : le découpage se fait aux paragraphes et aux phrases. Un PDF scanné
  n'a pas de texte (pas d'OCR) : il est refusé. Titre : le nom du fichier.

Deux bornes : `MAX_FILE_BYTES` pour le fichier lu, `MAX_TEXT_CHARS` pour le
texte qu'on en tire — un PDF compressé grossit beaucoup, et chaque passage coûte
de l'ordre de 2 s de calcul sur CPU.

Les lecteurs de `loom.word` et `loom.pdf` ne sont importés qu'à la première
lecture d'un fichier de leur format : importer `loom_ia.tools_builtin.pdf`
importe reportlab.

## Constantes de `loom_ia.tools_builtin.memory.files`

- `TEXT_SUFFIXES = ('.md', '.markdown', '.txt')` : Suffixes des fichiers lus comme texte (Markdown ou texte brut).
- `WORD_SUFFIXES = ('.docx', '.docm', '.dotx')` : Ceux que lit `word.reader` (`READABLE`).
- `PDF_SUFFIXES = ('.pdf',)` : Suffixes des fichiers lus comme PDF.
- `MAX_FILE_BYTES = 20000000` : Le fichier lu : la borne des fichiers de `loom.word` et `loom.pdf`.
- `MAX_TEXT_CHARS = 300000` : Le texte qu'on en tire : environ 300 passages, de 7 à 10 minutes de calcul sur CPU.
- `PDF_EXTRA = 'pdf'` : L'extra de loom-ia qu'il faut pour lire les PDF.
- `MAX_TEXT_BYTES = 4 * MAX_TEXT_CHARS` : Un caractère UTF-8 tient en 4 octets au plus : un texte plus gros dépasse forcément.

## Fonction `loom_ia.tools_builtin.memory.files.unreadable`

```python
def unreadable() -> dict[str, str]
```

Les formats que ce process ne sait pas lire, et pourquoi : `{extension: raison}`.

## Fonction `loom_ia.tools_builtin.memory.files.text_title`

```python
def text_title(text: str, path: Path) -> str
```

Le titre d'un fichier texte : son premier titre « # », sinon le nom du fichier.

## Fonction `loom_ia.tools_builtin.memory.files.extract`

```python
async def extract(data: bytes, path: Path, address: str) -> Extracted
```

Le titre et le texte markdown du fichier ; `ToolError` qui dit pourquoi sinon.

## Fonction `loom_ia.tools_builtin.memory.files.word_markdown`

```python
def word_markdown(blocks: Sequence[JsonValue]) -> tuple[str | None, str]
```

Les blocs lus d'un .docx en markdown, et le titre du document (style Titre).

## Fonction `loom_ia.tools_builtin.memory.files._plain`

```python
def _plain(text: str) -> str
```

Un titre sans son emphase : « **Offre** » ferait un chemin de titres illisible.
