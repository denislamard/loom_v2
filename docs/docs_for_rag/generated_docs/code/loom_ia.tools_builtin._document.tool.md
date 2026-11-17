# loom_ia.tools_builtin._document.tool

Module `loom_ia.tools_builtin._document.tool` de loom_v2 (fichier `src/loom_ia/tools_builtin/_document/tool.py`).

Ce que `word` et `pdf` ont en commun une fois montés : dossiers, approbation, appels.

Les fichiers lus et écrits sont désignés comme dans `loom.local_files` —
`<dossier>/<chemin>` — et soumis aux règles de `_folders` : dossiers
déclarés, droits, approbation par appel, liens qui sortent du dossier
refusés, écriture atomique, fichier existant remplacé seulement avec
`overwrite`. Chaque format ne fournit que son rendu, sa lecture, et ce que
sa description en dit. Le style des documents aussi est commun (`read_styles`) : la
charte, et le style de chaque modèle, résolus et vérifiés au montage.

## Constantes de `loom_ia.tools_builtin._document.tool`

- `WRITING_HELP = (f"Blocs ('blocks', dans l'ordre ; 'type' en tête de chacun) : heading (text, level 1 à 3), paragraph (text), list (ite…` : Le mode d'emploi de l'écriture, commun aux formats ; il ne part qu'à qui peut écrire.
- `OPTIONAL_HELP = {'chart': f"Graphique ('type': 'chart') : kind (bar, horizontal_bar, line, pie), title, caption ; ses données viennent …` : Le mode d'emploi de chaque bloc facultatif, seulement s'il est déclaré (`blocks`).
- `HOUSE_STYLES = Styles()` : Les styles de la maison : default.yaml, sans modèle.

## Classe `loom_ia.tools_builtin._document.tool.Styles`

```python
@dataclass(frozen=True, slots=True)
class Styles
```

Les styles résolus d'un outil : la charte, et charte + style du modèle, par modèle ; le style maison sans charte (`default` vide).

**Attributs :**

- `default` : La charte : le style des documents libres.
- `by_template` : Charte et style du modèle, par modèle.
- `said` : Ce que `loom validate` en dit.

### Méthode `loom_ia.tools_builtin._document.tool.Styles.of`

```python
def of(self, template: str | None) -> Style
```

Le style d'un document : celui de son modèle, sinon la charte.

### Méthode `loom_ia.tools_builtin._document.tool.Styles.all`

```python
def all(self) -> list[Style]
```

Tous les styles de l'outil : la charte (ou la maison), puis ceux des modèles.

## Classe `loom_ia.tools_builtin._document.tool.StyledParams`

```python
class StyledParams(Protocol)
```

Les paramètres d'un outil de document : sa charte et ses modèles.

### Méthode `loom_ia.tools_builtin._document.tool.StyledParams.style`

```python
@property
def style(self) -> StyleRef | None
```

La charte de l'outil, si elle est déclarée.

### Méthode `loom_ia.tools_builtin._document.tool.StyledParams.templates`

```python
@property
def templates(self) -> Mapping[str, TemplateRef]
```

Les modèles de document, par nom.

## Fonction `loom_ia.tools_builtin._document.tool.read_styles`

```python
def read_styles(params: StyledParams, templates: Mapping[str, Template], reference: str, fonts_said: Callable[[Sequence[Style]], str | None]) -> Styles
```

Les styles de l'outil, vérifiés et résolus une fois, au montage.

Le chargeur de config a lu les fichiers (`spec`), leurs logos et leurs polices ; une
référence construite en Python sans passer par lui garde `file` : c'est une faute de
montage. Un modèle qui écrit son style en ligne le donne ici. Les logos sont reconnus
(PNG ou JPEG), les polices vérifiées (TrueType, droits d'embarquement), la cascade
résolue (couleurs nommées, bases), et les styles que choisissent les sections cherchés :
une faute se voit dès `loom validate`. `fonts_said` : ce que le format dit des
polices de ses styles (`None` : rien).

## Fonction `loom_ia.tools_builtin._document.tool._check_files`

```python
def _check_files(spec: StyleFile, reference: str) -> None
```

Les logos et les polices d'un style : lus (par le chargeur), et reconnus.

## Fonction `loom_ia.tools_builtin._document.tool._written`

```python
def _written(template: Template, where: str) -> StyleFile | None
```

Le style qu'un modèle écrit en ligne ; un chemin doit avoir été lu au chargement.

## Classe `loom_ia.tools_builtin._document.tool.DocumentTool`

```python
class DocumentTool(ABC)
```

Un outil de document monté avec ses dossiers, son fuseau, ses modèles et ses styles.

**Attributs :**

- `NAME` : Nom de l'outil.
- `CALL` : Les arguments du format, sans graphique ni image : `variant` les élargit.
- `SUFFIX` : Le suffixe écrit (« .docx »).
- `LABEL` : Le nom du fichier pour le modèle (« document Word »).
- `MAX_SOURCE` : Taille au plus d'un fichier lu, en octets.

### Méthode `loom_ia.tools_builtin._document.tool.DocumentTool.__init__`

```python
def __init__(self, space: Folders, zone: ZoneInfo, *, blocks: Iterable[str]=(), templates: Mapping[str, Template] | None=None, styles: Styles=HOUSE_STYLES) -> None
```

Prépare l'outil : dossiers, fuseau, blocs facultatifs, modèles et styles ; ses arguments et sa description en découlent.

**Arguments :**

- `space` : Les dossiers permis.
- `zone` : Le fuseau des dates.
- `blocks` : Les blocs facultatifs déclarés.
- `templates` : Les modèles de document, par nom.
- `styles` : La charte, et les styles des modèles.

### Méthode `loom_ia.tools_builtin._document.tool.DocumentTool.spec`

```python
@property
def spec(self) -> ToolSpec
```

La définition de l'outil.

### Méthode `loom_ia.tools_builtin._document.tool.DocumentTool.settings`

```python
def settings(self) -> Settings
```

Les dossiers, puis les réglages du format, pour `loom validate`.

### Méthode `loom_ia.tools_builtin._document.tool.DocumentTool.assess_call`

```python
def assess_call(self, arguments: Mapping[str, JsonValue]) -> CallAssessment
```

Approbation et effets de cet appel : les droits qu'il exerce, dossier par dossier.

### Méthode `loom_ia.tools_builtin._document.tool.DocumentTool._assess_read`

```python
@staticmethod
def _assess_read(source: Located) -> CallAssessment
```

Une lecture : aucun effet ; l'approbation du droit read, si le dossier la demande.

### Méthode `loom_ia.tools_builtin._document.tool.DocumentTool._assess_write`

```python
@staticmethod
def _assess_write(request: WriteArguments, located: Located, reads: Iterable[tuple[Located, Right]]=()) -> CallAssessment
```

Les droits de la sortie, et le droit read de chaque image lue.

### Méthode `loom_ia.tools_builtin._document.tool.DocumentTool.invoke`

```python
async def invoke(self, arguments: dict[str, JsonValue], context: ToolContext) -> ToolOutput
```

Mène l'action : lire un modèle (`template`), lire un fichier (`read`), ou écrire un document (`write`, hors de la boucle asyncio).

**Lève :**

- `ToolError` : Arguments, chemin, droit ou document refusés.

### Méthode `loom_ia.tools_builtin._document.tool.DocumentTool._image_source`

```python
def _image_source(self, path: str) -> Located
```

Le fichier d'une image : un dossier qui a le droit read, sans rien lire.

### Méthode `loom_ia.tools_builtin._document.tool.DocumentTool._write`

```python
def _write(self, request: WriteArguments, located: Located) -> dict[str, JsonValue]
```

Écrit le document.

Validé, rendu, borné à `MAX_OUTPUT_BYTES`, puis écrit dans son dossier ; rend
le chemin, ce que le document contient et les avertissements.

### Méthode `loom_ia.tools_builtin._document.tool.DocumentTool._template`

```python
def _template(self, name: str) -> dict[str, JsonValue]
```

Un modèle déclaré, tel que le modèle de langage le remplit.

Sa description, ses consignes, et chaque section qu'il écrit (son nom, ses
consignes) ; jamais le texte fixe ni la mise en page.

### Méthode `loom_ia.tools_builtin._document.tool.DocumentTool._pictures`

```python
def _pictures(self, request: WriteArguments) -> Pictures
```

Chaque image du document, lue et reconnue, ou ce qui l'en empêche.

Les fautes ne sont pas levées : `prepare` les dit avec celles du document,
au bloc où l'image est demandée. La lecture s'arrête quand les images lues
dépassent la borne du document : les suivantes ne sont pas lues.

### Méthode `loom_ia.tools_builtin._document.tool.DocumentTool._proposed_styles`

```python
def _proposed_styles(self) -> Mapping[str, Iterable[str]]
```

Les styles proposés au modèle de langage, par type de bloc : la charte et tous les modèles (le schéma) ; aucun sans style.

### Méthode `loom_ia.tools_builtin._document.tool.DocumentTool._allowed`

```python
def _allowed(self, template: str | None) -> Allowed
```

Les styles proposés qu'un document (celui de son modèle) peut choisir.

### Méthode `loom_ia.tools_builtin._document.tool.DocumentTool._template_styles`

```python
def _template_styles(self, name: str) -> dict[str, JsonValue]
```

Les styles que propose le style d'un modèle, en plus de la charte.

### Méthode `loom_ia.tools_builtin._document.tool.DocumentTool._styles_help`

```python
def _styles_help(self) -> list[str]
```

Les styles que propose la charte : dans la description, à chaque appel.

### Méthode `loom_ia.tools_builtin._document.tool.DocumentTool._labels`

```python
def _labels(self, template: str | None) -> Labels
```

La langue et les libellés d'un document (celui de son modèle) : ceux du style.

### Méthode `loom_ia.tools_builtin._document.tool.DocumentTool._render`

```python
@abstractmethod
def _render(self, prepared: Prepared) -> tuple[bytes, dict[str, JsonValue], list[str]]
```

Le fichier, ce que le résultat en dit (pages…), et les avertissements du rendu.

### Méthode `loom_ia.tools_builtin._document.tool.DocumentTool._read`

```python
@abstractmethod
async def _read(self, request: ReadArguments, where: str, data: bytes) -> dict[str, JsonValue]
```

Ce qu'un fichier contient, en JSON ; `ToolError` s'il est illisible.

### Méthode `loom_ia.tools_builtin._document.tool.DocumentTool._check_readable`

```python
@abstractmethod
def _check_readable(self, path: str) -> None
```

`ToolError` si le suffixe n'est pas un format que l'outil lit.

### Méthode `loom_ia.tools_builtin._document.tool.DocumentTool._reading_help`

```python
@abstractmethod
def _reading_help(self, folders: str) -> str
```

La ligne de la description qui dit la lecture.

### Méthode `loom_ia.tools_builtin._document.tool.DocumentTool._writing_notes`

```python
def _writing_notes(self) -> tuple[str, ...]
```

Ce que le format ajoute au mode d'emploi de l'écriture.

### Méthode `loom_ia.tools_builtin._document.tool.DocumentTool._templates_help`

```python
def _templates_help(self) -> list[str]
```

Les modèles déclarés : leur nom et leur description, jamais leur texte.

### Méthode `loom_ia.tools_builtin._document.tool.DocumentTool._description`

```python
def _description(self) -> str
```

La description de l'outil : ce qu'il écrit et lit, les dossiers et leurs droits, les blocs, les modèles et les styles proposés.

## Fonction `loom_ia.tools_builtin._document.tool._image_paths`

```python
def _image_paths(request: WriteArguments) -> list[str]
```

Les chemins des images, chacun une fois, dans l'ordre du document.
