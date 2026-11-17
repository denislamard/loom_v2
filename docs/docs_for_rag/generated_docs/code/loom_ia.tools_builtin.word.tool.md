# loom_ia.tools_builtin.word.tool

Module `loom_ia.tools_builtin.word.tool` de loom_v2 (fichier `src/loom_ia/tools_builtin/word/tool.py`).

L'outil `word` monté : écrire et lire des .docx ; et sa fabrique.

## Constantes de `loom_ia.tools_builtin.word.tool`

- `NAME = 'word'` : Nom de l'outil.

## Classe `loom_ia.tools_builtin.word.tool.WordCall`

```python
class WordCall(DocumentCall)
```

Les arguments de `word` : l'écriture commune, et où commencer une lecture.

## Classe `loom_ia.tools_builtin.word.tool.WordTool`

```python
class WordTool(DocumentTool)
```

L'outil `word` monté avec ses dossiers et son fuseau.

## Fonction `loom_ia.tools_builtin.word.tool._warn_unwritten`

```python
def _warn_unwritten(found: dict[str, JsonValue], blocks: tuple[str, ...]) -> None
```

Dit les blocs lus que cet outil n'écrit pas, avant une réécriture que le schéma refuserait sans dire pourquoi.

Un graphique, une signature, un sommaire, une liste à cocher, des champs, du code,
un récapitulatif de TVA, un émargement.

## Fonction `loom_ia.tools_builtin.word.tool.build`

```python
def build(params: BaseModel, context: BuiltinContext) -> WordTool
```

La fabrique de `loom.word` : les dossiers comme `local_files`.

## Fonction `loom_ia.tools_builtin.word.tool._fonts_said`

```python
def _fonts_said(styles: Sequence[Style]) -> str | None
```

Les polices hors de la liste sûre : Word les nomme, sans les embarquer.
