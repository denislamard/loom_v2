# loom_ia.tools_builtin._office_params

Module `loom_ia.tools_builtin._office_params` de loom_v2 (fichier `src/loom_ia/tools_builtin/_office_params.py`).

Paramètres communs des outils de documents (`loom.excel`, `loom.word`, `loom.pdf`).

Leurs dossiers suivent la forme de `loom.local_files` (`name`, `path`,
`rights`, `approval`), sans le droit `delete` : ces outils ne suppriment
rien. Leur fuseau est un nom IANA, vérifié au chargement.

Module léger — Pydantic, `local_files_params`, `_templates` (la bibliothèque
standard) et `_style_params` seulement — : la config
l'importe pour valider une référence, sans importer aucun des outils.

## Constantes de `loom_ia.tools_builtin._office_params`

- `DEFAULT_TIMEZONE = 'Europe/Paris'` : Fuseau des dates rendues, sans `timezone` dans les paramètres.
- `OptionalBlock = Literal['chart', 'image', 'signature', 'toc', 'checkboxes', 'fields', 'code', 'invoice_totals', 'attendance']` : Chacun coûte des tokens à chaque appel du modèle : il n'est dans le schéma et la description que si la config de l'outil le nomme dans `blocks`.
- `OPTIONAL_BLOCKS = ('chart', 'image', 'signature', 'toc', 'checkboxes', 'fields', 'code', 'invoice_totals', 'attendance')` : Blocs facultatifs, dans l'ordre où on les dit.
- `OLD_KEYS = {'charts': 'blocks: [chart]', 'images': 'blocks: [image]', 'extra_blocks': 'blocks: [signature, toc, checkboxes]'}` : Les clés d'avant `blocks` (2.0.x), refusées avec la forme qui les remplace.
- `Blocks = Annotated[tuple[OptionalBlock, ...], AfterValidator(_unique_blocks)]` : Liste `blocks` des paramètres : des blocs facultatifs, sans doublon.
- `Templates = Annotated[dict[str, TemplateRef], BeforeValidator(_short_form), AfterValidator(_checked_templates)]` : Table `templates` des paramètres : nom → modèle de document ; la forme courte `nom: fichier.yaml` est admise.

## Fonction `loom_ia.tools_builtin._office_params.no_delete`

```python
def no_delete(reference: str) -> Callable[[object], object]
```

Avant les règles communes : `delete` dans `rights` ou `approval` est refusé.

Sinon, `approval: { delete: … }` sans `delete` accordé ferait conseiller
de l'ajouter à `rights`, où il est refusé. `reference` : « loom.excel »…

## Fonction `loom_ia.tools_builtin._office_params.checked_folders`

```python
def checked_folders(reference: str) -> Callable[[tuple[FolderSpec, ...]], tuple[FolderSpec, ...]]
```

Après la validation des dossiers : au moins un, noms uniques, jamais `delete`.

## Fonction `loom_ia.tools_builtin._office_params.check_timezone`

```python
def check_timezone(name: str) -> None
```

`ValueError` si `name` n'est pas un fuseau IANA connu.

## Fonction `loom_ia.tools_builtin._office_params._keys`

```python
def _keys(value: object) -> set[object]
```

Les clés d'une table YAML ; aucune pour une autre valeur.

## Fonction `loom_ia.tools_builtin._office_params.no_old_keys`

```python
def no_old_keys(value: object) -> object
```

Avant la validation.

`charts`, `images` et `extra_blocks` (2.0.x) refusés, avec la forme qui les
remplace, plutôt qu'un champ « extra » sans explication.

## Classe `loom_ia.tools_builtin._office_params.TemplateRef`

```python
class TemplateRef(BaseModel)
```

Un modèle de document : son fichier YAML (relatif à `resources_dir`), puis son texte.

Le chargeur de config lit le fichier et range son texte dans `text`, comme un
`skill_file` dans `skill` ; `file` n'est alors plus gardé. Le style que nomme le
modèle (`style:` dans son fichier) est lu en même temps et rangé dans `style`. En
config, le chemin seul : `note: templates/note.yaml`.

**Attributs :**

- `file` : Fichier YAML du modèle, relatif à `resources_dir` ; plus gardé une fois lu.
- `text` : Texte du modèle, rangé par le chargeur de config.
- `style` : Le style du modèle, lu par le chargeur de config (`loom.word`, `loom.pdf`).

### Méthode `loom_ia.tools_builtin._office_params.TemplateRef._style_in_the_template`

```python
@model_validator(mode='before')
@classmethod
def _style_in_the_template(cls, value: object) -> object
```

La forme longue de la 2.0 (`{file, style}`) : le modèle nomme son style.

## Fonction `loom_ia.tools_builtin._office_params._short_form`

```python
def _short_form(value: object) -> object
```

`nom: fichier.yaml` : la forme courte d'un modèle.

## Fonction `loom_ia.tools_builtin._office_params.read_templates`

```python
def read_templates(templates: Mapping[str, TemplateRef], reference: str) -> dict[str, Template]
```

Les modèles d'une référence, lus et analysés ; `ValueError` si l'un n'a pas été lu.

Le chargeur de config range le texte de chaque fichier dans `text` ; une référence
construite en Python sans passer par lui garde `file` : c'est une faute de montage.

## Fonction `loom_ia.tools_builtin._office_params.check_section_styles`

```python
def check_section_styles(template: Template, style: Style, where: str) -> None
```

Les styles que choisissent les sections (`styles`).

Connus du style du modèle, et partis de l'élément qu'ils mettent en forme ;
`ValueError` qui dit lequel.
