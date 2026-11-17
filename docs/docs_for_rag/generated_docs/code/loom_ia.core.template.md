# loom_ia.core.template

Module `loom_ia.core.template` de loom_v2 (fichier `src/loom_ia/core/template.py`).

Templates `{{ var }}` des prompts et des entrées de rôles (points 12 et 50, fonction M7).

Moteur interne minimal, sans Jinja : une variable est un chemin pointé
(`args.devis`, `context.user_input`, `args.lignes.0`), sans boucle,
condition ni filtre. Un segment numérique indexe une liste. Au rendu, une
chaîne est insérée telle quelle, toute autre valeur en JSON (sans échappement
ASCII), et une valeur absente donne une chaîne vide.

L'analyse est séparée du rendu : la config analyse chaque template au
chargement (`Template.parse`) et vérifie que ses variables existent ; une
variable inconnue est une erreur de config. Le rendu ne peut donc plus
échouer.

Contenu : `Template` (un template analysé), `TemplateError` (template mal
formé), `lookup` (valeur au bout d'un chemin) et `render_value` (texte
inséré pour une valeur).

## Classe `loom_ia.core.template.TemplateError`

```python
class TemplateError(ValueError)
```

Template mal formé, levé par `Template.parse`.

Deux cas : une variable dont l'expression n'est pas un chemin pointé
(`{{ a + b }}`, `{{ }}`), ou un `{{` sans `}}` correspondant. Le
message cite la variable fautive et rappelle la forme attendue.

## Classe `loom_ia.core.template.Template`

```python
@dataclass(frozen=True, slots=True)
class Template
```

Un template `{{ var }}` analysé, prêt à rendre.

**Attributs :**

- `source` : Texte du template, tel qu'écrit dans la config.
- `parts` : Le template découpé, dans l'ordre : une chaîne est du texte littéral, un tuple est le chemin d'une variable (`("args", "devis")`).

### Méthode `loom_ia.core.template.Template.parse`

```python
@classmethod
def parse(cls, source: str) -> Self
```

Analyse un template.

**Arguments :**

- `source` : Texte du template. Une variable s'écrit `{{ chemin }}`, espaces permis autour du chemin ; le chemin est fait de noms (lettres, chiffres, `_`, `-`) séparés par des points.

**Renvoie :**

Le template, découpé en texte et en chemins.

**Lève :**

- `TemplateError` : Une variable n'est pas un chemin pointé, ou un `{{` n'est pas fermé.

### Méthode `loom_ia.core.template.Template.variables`

```python
@property
def variables(self) -> tuple[Path, ...]
```

Chemins des variables, dans l'ordre du texte ; la config les vérifie au chargement.

### Méthode `loom_ia.core.template.Template.render`

```python
def render(self, values: Mapping[str, JsonValue]) -> str
```

Rend le template avec des valeurs ; ne lève jamais.

**Arguments :**

- `values` : Valeurs de premier niveau (`args`, `context`…), où chaque chemin est cherché par `lookup`.

**Renvoie :**

Le texte, où chaque variable est remplacée par `render_value` de
sa valeur : une chaîne telle quelle, `None` ou une valeur absente
par une chaîne vide, le reste en JSON.

## Fonction `loom_ia.core.template.lookup`

```python
def lookup(values: Mapping[str, JsonValue], path: Path) -> JsonValue
```

Valeur au bout d'un chemin, ou `None` si une étape manque.

Chaque segment est une clé d'objet, ou l'indice d'une liste s'il est fait
de chiffres (18 au plus). Un indice hors de la liste, une clé absente ou une
étape qui tombe sur une valeur simple donnent `None`.

**Arguments :**

- `values` : Valeurs de premier niveau.
- `path` : Chemin découpé, par exemple `("args", "lignes", "0")`.

**Renvoie :**

La valeur trouvée, ou `None`.

## Fonction `loom_ia.core.template.render_value`

```python
def render_value(value: JsonValue) -> str
```

Texte inséré dans un template pour une valeur.

**Arguments :**

- `value` : Valeur trouvée par `lookup`.

**Renvoie :**

Une chaîne vide pour `None`, la chaîne telle quelle pour du texte, et
sinon le JSON de la valeur (caractères non ASCII gardés tels quels).
