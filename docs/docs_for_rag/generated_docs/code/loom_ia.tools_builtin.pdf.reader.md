# loom_ia.tools_builtin.pdf.reader

Module `loom_ia.tools_builtin.pdf.reader` de loom_v2 (fichier `src/loom_ia/tools_builtin/pdf/reader.py`).

Lire un PDF : le texte de ses pages, par un process à part (`worker`).

Le process est borné (1 Gio de mémoire adressable, le temps CPU de
l'échéance, aucun fichier écrit) et tué à l'échéance, ou quand l'appel est
annulé (délai de l'outil) : un PDF forgé ne peut ni bloquer l'agent, ni
épuiser la mémoire de loom. Le prix : environ 0,2 s de démarrage par lecture.

Le texte est celui que le PDF contient : un PDF scanné n'en a pas (pas
d'OCR), et le résultat le dit, page par page.

## Constantes de `loom_ia.tools_builtin.pdf.reader`

- `WORKER = Path(__file__).with_name('worker.py')` : Le script qui lit le PDF dans un process à part.
- `READ_DEADLINE = 20.0` : Durée au plus d'une lecture, en secondes.
- `MEMORY = 1 << 30` : Mémoire au plus du process de lecture, en octets (1 Gio).
- `HEADER_WINDOW = 1024` : Où chercher l'en-tête « %PDF- » : la norme tolère des octets avant lui.
- `PAGE_CUT = 'Une page plus longue que le budget a été coupée.'` : La note d'une page seule plus longue que le budget, coupée : `loom.memory` la refuse.

## Fonction `loom_ia.tools_builtin.pdf.reader.check_suffix`

```python
def check_suffix(path: str) -> None
```

Refuse un fichier qui n'est pas un .pdf.

## Fonction `loom_ia.tools_builtin.pdf.reader.page_range`

```python
def page_range(said: str | None) -> tuple[int, int | None]
```

« 3 », « 2-5 », « 4- » : la première page et la dernière (`None` : jusqu'au bout).

## Fonction `loom_ia.tools_builtin.pdf.reader.read`

```python
async def read(data: bytes, where: str, pages: str | None, budget: int=READ_CHARS) -> dict[str, JsonValue]
```

Le texte des pages demandées, dans un budget de taille ; `ToolError` sinon.

`budget` : celui d'un résultat d'outil par défaut ; `loom.memory`, qui lit
le document entier, en donne un plus grand.

## Fonction `loom_ia.tools_builtin.pdf.reader._shaped`

```python
def _shaped(found: dict[str, JsonValue], budget: int) -> dict[str, JsonValue]
```

Le résultat d'une lecture tel que le modèle le reçoit : pages, métadonnées, et la page où reprendre si la lecture a été coupée.
