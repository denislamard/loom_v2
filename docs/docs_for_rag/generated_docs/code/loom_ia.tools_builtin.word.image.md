# loom_ia.tools_builtin.word.image

Module `loom_ia.tools_builtin.word.image` de loom_v2 (fichier `src/loom_ia/tools_builtin/word/image.py`).

Une image dans un .docx : le dessin en ligne, centré, qui renvoie à sa partie `word/media`.

Une image est un `<w:drawing><wp:inline>` qui porte une image DrawingML
(`pic:pic`) ; ses octets sont une partie de l'archive (`word/media/imageN.png`),
nommée par une relation du corps (`r:embed`). La même image donnée deux fois
n'est stockée qu'une fois : les parties sont numérotées dans l'ordre où chaque
image paraît la première fois, ce qui rend le fichier reproductible.

L'adresse du fichier lu (« <dossier>/<chemin> ») est le nom du dessin
(`docPr name`) : la relecture la rend en `path`, et le document se réécrit
tel quel tant que le fichier est là.

## Constantes de `loom_ia.tools_builtin.word.image`

- `EMU = 360` : Un centième de millimètre en EMU (English Metric Units) : 360.

## Fonction `loom_ia.tools_builtin.word.image.inline`

```python
def inline(relation: str, drawing: int, image: ImageOut, *, keep_next: bool) -> str
```

Le paragraphe centré qui porte l'image ; `keep_next` la garde avec sa légende.

## Fonction `loom_ia.tools_builtin.word.image.run`

```python
def run(relation: str, drawing: int, cx: int, cy: int, name_text: str, alt: str) -> str
```

Le segment qui porte une image en ligne, de `cx` sur `cy` EMU (un logo, une photo).
