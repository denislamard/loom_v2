# loom_ia.tools_builtin._templates

Module `loom_ia.tools_builtin._templates` de loom_v2 (fichier `src/loom_ia/tools_builtin/_templates.py`).

Les modèles de document d'un outil `word` ou `pdf` : un fichier YAML.

    description: Note courte à l'équipe ou aux clients.
    instructions: |
      Fichier : workspace/note-<objet>.docx ; n'invente rien.
    style: styles/note.yaml          # facultatif : un fichier de style, ou un style en ligne
    sections:
      Destinataires:
        optional: true
        show_title: false
        instructions: |
          Un paragraphe : « À : », « De : », « Le : ».
      Message:
        styles: { heading1: message_title }
        instructions: |
          Un ou deux paragraphes courts.
      Conditions:
        text: |
          Paiement à 30 jours par virement.

**Les sections sont celles du document**, dans l'ordre du fichier. Leur nom est à la
fois le titre que l'outil imprime (`show_title: false` pour ne pas l'imprimer) et la
clé que remplit le modèle de langage (`sections` dans l'appel) ; la comparaison ignore
la numérotation (« 1. »), la casse, les accents et les espaces : « 1. contexte » est la
section « Contexte ». Une section au texte fixe (`text`) est écrite par l'outil seul :
le modèle de langage n'en voit rien. Le reste règle la mise en page de la section
(`box`, `beside`, `width`, `page_break`, `columns`, `landscape`, `cover`,
`space_before`) et ses styles (`styles` : le style nommé de chaque élément).

Le modèle de langage ne voit que les noms et les descriptions des modèles ; il lit un
modèle à la demande (`action: template`) : ses consignes, et le nom et les consignes de
chaque section qu'il remplit.

Module léger — Pydantic et PyYAML — : le chargeur de config lit les fichiers et appelle
`parse` ; une faute de modèle se voit dès `loom validate`.

## Constantes de `loom_ia.tools_builtin._templates`

- `MAX_TEMPLATES = 20` : Modèles de document au plus par outil.
- `MAX_TEMPLATE_CHARS = 20000` : Taille au plus d'un fichier de modèle, en caractères.
- `MAX_DESCRIPTION = 200` : Longueur au plus de la description d'un modèle.
- `MAX_SECTIONS = 30` : Sections au plus dans un modèle.
- `MAX_SECTION_NAME = 80` : Longueur au plus du nom d'une section.
- `MAX_INSTRUCTIONS = 8000` : Longueur au plus des consignes d'un modèle ou d'une section.
- `MAX_FIXED_TEXT = 8000` : Longueur au plus du texte fixe d'une section.
- `NAME = re.compile('^[a-z0-9]+(-[a-z0-9]+)*$')` : Nom d'un modèle : minuscules et chiffres, mots séparés par « - ».
- `MAX_NAME = 40` : Longueur au plus du nom d'un modèle.
- `NONE = 'none'` : Le nom qui choisit un document libre, quand l'outil a des modèles.
- `MAX_BESIDE = 3` : Une section côte à côte : 3 au plus sur une ligne.
- `_NUMBERING = re.compile('^\\s*(?:\\d+(?:\\.\\d+)*\\s*[.)\\-\\u2013\\u2014:]?|[a-z]\\)|[ivx]{1,4}[.)])\\s+')` : « 1. », « 2.3 - », « 4) », « a) », « IV. » : la numérotation que le modèle de langage ajoute.
- `COLON = 'mapping values are not allowed here'` : Le message de PyYAML devant un texte qui contient « : » sans guillemets.
- `COLON_HINT = "un texte qui contient « : » ou « # » s'écrit entre guillemets, ou après | à la ligne"` : Conseil ajouté à une erreur de YAML qui vient d'un « : » ou d'un « # » dans un texte.
- `Element = Literal['heading1', 'heading2', 'heading3', 'paragraph', 'list', 'note', 'quote', 'caption', 'table', 'box']` : Les éléments dont une section choisit le style nommé (`styles`).
- `ELEMENTS = ('heading1', 'heading2', 'heading3', 'paragraph', 'list', 'note', 'quote', 'caption', 'table', 'box')` : Éléments qu'une section peut demander, dans l'ordre où on les dit.
- `Text = Annotated[str, Field(max_length=MAX_INSTRUCTIONS)]` : Un texte de consignes, de longueur bornée.
- `OLD_FORMAT = 'un modèle est un fichier YAML depuis 2.1.0 (description, instructions, sections) : chaque titre « # Section » devient …` : Message d'un modèle écrit dans l'ancien format Markdown.

## Classe `loom_ia.tools_builtin._templates._Spec`

```python
class _Spec(BaseModel)
```

Base des modèles d'un fichier de modèle : figés, sans clé inconnue.

## Classe `loom_ia.tools_builtin._templates.SectionSpec`

```python
class SectionSpec(_Spec)
```

Une section, telle qu'écrite dans le modèle.

**Attributs :**

- `instructions` : Consignes au modèle pour cette section.
- `optional` : Section facultative : le modèle peut la laisser de côté.
- `show_title` : Écrire le titre de la section.
- `text` : Texte fixe de la section, écrit tel quel.
- `styles` : Style nommé par élément de la section (`heading1`, `paragraph`…).
- `box` : Section encadrée.
- `beside` : Section posée à côté de la précédente.
- `width` : Largeur de la section, en pour cent de la largeur du texte : `55` ou `55%`.
- `page_break` : Section commencée sur une nouvelle page.
- `columns` : Colonnes de texte, de 1 à 3.
- `landscape` : Section en paysage.
- `cover` : Section en page de garde.
- `margin_top` : Marge au-dessus de la section, en cm (l'adresse d'un courrier dans la fenêtre d'une enveloppe) : de la mise en page, pas un style.

### Méthode `loom_ia.tools_builtin._templates.SectionSpec._renamed`

```python
@model_validator(mode='before')
@classmethod
def _renamed(cls, value: object) -> object
```

`space_before` d'une section, renommé : le même nom qu'une propriété de style (en points) aurait deux sens.

## Classe `loom_ia.tools_builtin._templates.TemplateSpec`

```python
class TemplateSpec(_Spec)
```

Le fichier du modèle.

**Attributs :**

- `description` : Description du modèle, montrée au modèle de langage.
- `instructions` : Consignes générales du modèle.
- `style` : Un fichier de style (chemin relatif à resources_dir), ou un style écrit en ligne.
- `sections` : Les sections, par nom, dans l'ordre du document.

## Classe `loom_ia.tools_builtin._templates.Section`

```python
@dataclass(frozen=True, slots=True)
class Section
```

Une section du modèle : son nom, sa place (à partir de 1), ce qu'elle dit.

**Attributs :**

- `name` : Nom de la section, son titre.
- `position` : Sa place, à partir de 1.
- `spec` : Ce qu'elle dit.

### Méthode `loom_ia.tools_builtin._templates.Section.key`

```python
@property
def key(self) -> str
```

La clé de la section : son nom normalisé, pour la comparer.

### Méthode `loom_ia.tools_builtin._templates.Section.optional`

```python
@property
def optional(self) -> bool
```

Vrai si la section est facultative.

### Méthode `loom_ia.tools_builtin._templates.Section.fixed`

```python
@property
def fixed(self) -> bool
```

Une section au texte fixe : écrite par l'outil, jamais par le modèle de langage.

### Méthode `loom_ia.tools_builtin._templates.Section.said`

```python
@property
def said(self) -> str
```

Son nom, et ce qui la distingue : « Photos (optional) », « Conditions (fixed) ».

## Classe `loom_ia.tools_builtin._templates.Template`

```python
@dataclass(frozen=True, slots=True)
class Template
```

Un modèle lu : son nom, sa description, ses consignes, son style, ses sections.

**Attributs :**

- `name` : Nom du modèle.
- `description` : Sa description.
- `instructions` : Ses consignes générales.
- `style` : Le style du modèle tel qu'écrit : un chemin, un style en ligne, ou rien.
- `sections` : Ses sections, dans l'ordre.
- `text` : Le texte du fichier lu.

### Méthode `loom_ia.tools_builtin._templates.Template.outline`

```python
def outline(self) -> str
```

« Contexte · Photos (optional) · Conditions (fixed) ».

### Méthode `loom_ia.tools_builtin._templates.Template.section`

```python
def section(self, name: str) -> Section | None
```

La section de ce nom (comparé sans numérotation, casse ni accents).

### Méthode `loom_ia.tools_builtin._templates.Template.rows`

```python
def rows(self) -> dict[int, int]
```

La ligne de chaque section, par sa place : la place de la première de la ligne (une section `beside` est sur la ligne de la précédente).

## Fonction `loom_ia.tools_builtin._templates.key`

```python
def key(title: str) -> str
```

Un titre comparable : sans numérotation, emphase, casse, accents ni espaces superflus.

## Fonction `loom_ia.tools_builtin._templates.check_name`

```python
def check_name(name: str) -> None
```

Refuse un nom de modèle hors format (minuscules, chiffres et tirets), trop long, ou `none`, qui choisit un document libre.

## Classe `loom_ia.tools_builtin._templates._Loader`

```python
class _Loader(yaml.SafeLoader)
```

Le chargeur YAML d'un modèle : une clé donnée deux fois est une faute.

## Fonction `loom_ia.tools_builtin._templates._old_format`

```python
def _old_format(text: str) -> bool
```

Un modèle markdown (avant 2.1.0) : un en-tête `---`, des titres `# Section`.

## Fonction `loom_ia.tools_builtin._templates.parse`

```python
def parse(name: str, text: str) -> Template
```

Le modèle et ses sections ; `ValueError` qui dit quoi corriger.

## Fonction `loom_ia.tools_builtin._templates._sections`

```python
def _sections(where: str, spec: TemplateSpec) -> list[Section]
```

Les sections d'un modèle, dans l'ordre : nombre borné, noms d'une ligne et distincts, styles d'éléments vérifiés.

## Fonction `loom_ia.tools_builtin._templates._check_layout`

```python
def _check_layout(where: str, sections: Sequence[Section]) -> None
```

La mise en page des sections : ce qui ne se pose pas est dit au chargement.

## Fonction `loom_ia.tools_builtin._templates.fixed_paragraphs`

```python
def fixed_paragraphs(text: str) -> list[str]
```

Le texte fixe d'une section, en paragraphes : une ligne vide les sépare ; un retour à la ligne simple reste dans le paragraphe.
