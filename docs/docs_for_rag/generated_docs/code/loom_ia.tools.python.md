# loom_ia.tools.python

Module `loom_ia.tools.python` de loom_v2 (fichier `src/loom_ia/tools/python.py`).

Outils écrits en Python (fonction D1).

Le schéma d'entrée est déduit de la signature ; la description vient de la
docstring. Un paramètre annoté `ToolContext` reçoit le contexte de l'appel
et n'apparaît pas dans le schéma.

    @tool
    def calculer(expr: str) -> str:
        '''Évalue une expression arithmétique.'''

    @tool(side_effects="irreversible", timeout=10)
    async def envoyer_email(to: str, body: str, ctx: ToolContext) -> None: ...

Conversion du résultat (point 15) : `str` donne un bloc texte ; `None` un
résultat vide ; un `ToolOutput` est repris tel quel ; une `Image` est
rangée dans le stockage d'artefacts par le moteur et le modèle en reçoit la
référence ; toute autre valeur sérialisable en JSON (`dict`, `list`,
nombre, modèle Pydantic…) donne un bloc JSON, recopié dans `data`.

    @tool
    def graphique(serie: list[float]) -> Image:
        '''Trace la série.'''
        return Image(tracer_png(serie))

Une fonction synchrone s'exécute dans un thread du pool des outils
(`in_thread`) : un délai dépassé rend la main au moteur, mais ne peut pas
interrompre le thread, qui va au bout. Le moteur le suit : un appel à effet est
alors d'état inconnu, et les appels suivants du tour attendent sa fin réelle.
Une fonction qui travaille par étapes peut regarder `ToolContext.cancelled`,
levé au délai et à l'annulation, et s'arrêter d'elle-même.

Effets de bord (`side_effects`) : sans déclaration, l'outil passe pour sans
effet (`none`). Un outil qui écrit doit le dire : c'est ce qui le fait
approuver par une politique, attendre ses voisins dans l'ordre des appels d'un
tour, et ne pas relancer à la reprise. `loom validate` signale un outil qui
ne déclare rien (`declares_effects`).

## Classe `loom_ia.tools.python.Image`

```python
@dataclass(frozen=True, slots=True)
class Image
```

Image renvoyée par un outil : rangée comme artefact, référencée dans le résultat (fonction G3).

Le type se déduit de la signature binaire (JPEG, PNG, GIF, WebP) ;
`media_type` ne sert que si elle n'est pas reconnue.

### Méthode `loom_ia.tools.python.Image.from_path`

```python
@classmethod
def from_path(cls, path: str | Path) -> Self
```

Image lue dans un fichier ; son nom est celui du fichier.

**Arguments :**

- `path` : Chemin du fichier.

### Méthode `loom_ia.tools.python.Image.block`

```python
def block(self) -> InlineDataBlock
```

Le bloc d'octets que le moteur range comme artefact.

**Lève :**

- `ValueError` : Format non reconnu et `media_type` absent.

## Classe `loom_ia.tools.python.FunctionTool`

```python
class FunctionTool
```

Outil construit à partir d'une fonction ; reste appelable comme elle.

### Méthode `loom_ia.tools.python.FunctionTool.__init__`

```python
def __init__(self, fn: Callable[P, R], *, name: str | None=None, description: str | None=None, side_effects: SideEffects | None=None, approval: Approval='never', idempotent: bool=False, on_unknown: UnknownState='error', timeout: float | None=None, offload_over: int | None=None) -> None
```

Construit l'outil : schéma d'entrée tiré de la signature, description de la docstring.

**Arguments :**

- `fn` : La fonction, synchrone ou asynchrone ; ses paramètres nommés deviennent les arguments, un paramètre annoté `ToolContext` reçoit le contexte.
- `name` : Nom de l'outil ; par défaut celui de la fonction.
- `description` : Description vue par le modèle ; par défaut la docstring.
- `side_effects` : `none`, `reversible` ou `irreversible` ; sans déclaration, `none` (`declares_effects` reste faux).
- `approval` : `never` (défaut), `always` ou `policy`.
- `idempotent` : Vrai si refaire l'appel est sans risque.
- `on_unknown` : Ce qu'il advient d'un appel interrompu dont l'effet est inconnu : `error` (défaut, le modèle est prévenu) ou `pause` (un humain tranche).
- `timeout` : Délai de l'outil, en secondes ; à défaut, celui de la config.
- `offload_over` : Seuil de déport du résultat, en caractères.

**Lève :**

- `ValueError` : Pas de description (docstring vide).
- `TypeError` : Paramètre positionnel seul ou variadique.

### Méthode `loom_ia.tools.python.FunctionTool.spec`

```python
@property
def spec(self) -> ToolSpec
```

Description de l'outil : nom, description, schéma, effets, approbation.

### Méthode `loom_ia.tools.python.FunctionTool.returns_data`

```python
@property
def returns_data(self) -> bool
```

Faux si la signature dit que l'outil ne rend jamais de données (`data`).

`str`, `None` et `Image` donnent un résultat sans données ; tout
autre type, un `ToolOutput` construit par l'outil, ou une signature
sans annotation de retour, peuvent en porter.

### Méthode `loom_ia.tools.python.FunctionTool.__call__`

```python
def __call__(self, *args: P.args, **kwargs: P.kwargs) -> R
```

Appelle la fonction d'origine directement, hors de la boucle d'agent.

### Méthode `loom_ia.tools.python.FunctionTool.invoke`

```python
async def invoke(self, arguments: dict[str, JsonValue], context: ToolContext) -> ToolOutput
```

Valide les arguments, appelle la fonction et traduit son résultat.

Une fonction synchrone s'exécute dans un thread du pool des outils.

**Arguments :**

- `arguments` : Arguments du modèle.
- `context` : Contexte de l'appel.

**Renvoie :**

Le résultat (voir `to_output`).

**Lève :**

- `ToolError` : Arguments invalides ; le message les liste pour le modèle.

## Fonction `loom_ia.tools.python.to_output`

```python
def to_output(result: object) -> ToolOutput
```

Traduit la valeur renvoyée par une fonction en `ToolOutput`.

**Arguments :**

- `result` : La valeur : `ToolOutput`, `Image`, `str`, `None`, ou toute valeur sérialisable en JSON.

**Renvoie :**

Le résultat ; une valeur JSON donne un bloc JSON et `data`.

## Fonction `loom_ia.tools.python.tool`

```python
@overload
def tool[**P, R](fn: Callable[P, R], /) -> FunctionTool[P, R]
```

(sans docstring)

## Fonction `loom_ia.tools.python.tool`

```python
@overload
def tool[**P, R](*, name: str | None=None, description: str | None=None, side_effects: SideEffects | None=None, approval: Approval='never', idempotent: bool=False, on_unknown: UnknownState='error', timeout: float | None=None, offload_over: int | None=None) -> Callable[[Callable[P, R]], FunctionTool[P, R]]
```

(sans docstring)

## Fonction `loom_ia.tools.python.tool`

```python
def tool[**P, R](fn: Callable[P, R] | None=None, /, *, name: str | None=None, description: str | None=None, side_effects: SideEffects | None=None, approval: Approval='never', idempotent: bool=False, on_unknown: UnknownState='error', timeout: float | None=None, offload_over: int | None=None) -> FunctionTool[P, R] | Callable[[Callable[P, R]], FunctionTool[P, R]]
```

Déclare une fonction comme outil, avec ou sans options.

**Arguments :**

- `fn` : La fonction, quand le décorateur s'emploie sans parenthèses.
- `name` : Nom de l'outil.
- `description` : Description vue par le modèle.
- `side_effects` : Effets de bord.
- `approval` : Approbation.
- `idempotent` : Vrai si refaire l'appel est sans risque.
- `on_unknown` : Suite d'un appel interrompu d'effet inconnu.
- `timeout` : Délai, en secondes.
- `offload_over` : Seuil de déport, en caractères.

**Renvoie :**

Une `FunctionTool` (`@tool`), ou un décorateur qui en rend une
(`@tool(...)`).

## Fonction `loom_ia.tools.python._arguments_model`

```python
def _arguments_model(fn: Callable[..., object], name: str) -> tuple[str | None, type[BaseModel]]
```

Modèle Pydantic des arguments, et nom du paramètre de contexte éventuel.

## Fonction `loom_ia.tools.python._may_return_data`

```python
def _may_return_data(fn: Callable[..., object]) -> bool
```

Vrai sauf si chaque type que la signature annonce donne un résultat sans données.

## Fonction `loom_ia.tools.python._without_data`

```python
def _without_data(kind: object) -> bool
```

Type que `to_output` traduit sans `data` : texte (énumération comprise), rien, image.

## Fonction `loom_ia.tools.python._members`

```python
def _members(annotation: object) -> list[object]
```

Types qu'une annotation peut rendre : ceux d'une union, d'un alias, d'un `Literal`.

## Fonction `loom_ia.tools.python._describe`

```python
def _describe(error: ValidationError) -> str
```

Message d'erreur de validation lisible par le modèle.
