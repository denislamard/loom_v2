# loom_ia.config.references

Module `loom_ia.config.references` de loom_v2 (fichier `src/loom_ia/config/references.py`).

Références vers du code Python (point 50).

Deux formes, le nom d'abord :

- un **nom enregistré** : `imports` charge les modules listés, et les
  outils (`@tool`) et politiques (`@policy`) qu'ils déclarent sont
  enregistrés sous leur nom ;
- un **chemin d'import** `module:attr`, qui ne demande aucun `imports`.

Le préfixe `loom.` est réservé à ce que fournit loom-ia (outils et politiques) :
aucun nom enregistré ne le porte (décision D5 de l'analyse des outils fournis,
`docs/tools-builtin-analyse.md`).

Les modules voisins du fichier de config sont importables : son dossier est
ajouté à `sys.path` le temps du chargement. Un module voisin est importé
sous son seul nom ; deux configs d'un même process peuvent pourtant avoir
chacune le sien sous le même nom (`outils.py`). Un module que loom a importé
comme voisin d'une config est donc retiré de `sys.modules` quand une autre
config, qui a le sien sous ce nom, est chargée, puis réimporté depuis le
dossier de celle-ci (point 50, décision du 06/10) : les objets que la première
config en a déjà pris restent les siens. Un module que loom n'a pas importé
comme voisin — installé, de la bibliothèque standard, importé par
l'application — n'est jamais touché.

## Constantes de `loom_ia.config.references`

- `_NEIGHBOURS = {}` : Modules voisins importés par loom : nom de premier niveau → dossier de la config pour laquelle ils l'ont été.

## Classe `loom_ia.config.references.Registry`

```python
class Registry
```

Objets Python nommés, disponibles pour la config.

Rempli par `import_modules` (outils `@tool` et politiques `@policy`
des modules de `imports`), ou par `Loom.register()`.

**Arguments :**

- `objects` : Objets à enregistrer d'emblée, par nom.

**Lève :**

- `ConfigError` : Un nom en `loom.`, ou deux objets du même nom.

### Méthode `loom_ia.config.references.Registry.add`

```python
def add(self, name: str, obj: object, *, source: str='') -> None
```

Enregistre un objet sous un nom ; le même objet deux fois ne change rien.

Une référence `loom.…` désigne ce que fournit loom-ia : un objet
enregistré sous ce nom ne serait jamais atteint (décision D5 de
l'analyse des outils fournis).

**Arguments :**

- `name` : Le nom.
- `obj` : L'objet (outil, politique).
- `source` : D'où il vient, pour le message (« module outils »).

**Lève :**

- `ConfigError` : Nom en `loom.`, ou déjà pris par un autre objet.

### Méthode `loom_ia.config.references.Registry.get`

```python
def get(self, name: str) -> object | None
```

L'objet de ce nom, ou `None`.

### Méthode `loom_ia.config.references.Registry.names`

```python
@property
def names(self) -> tuple[str, ...]
```

Les noms enregistrés, triés.

## Fonction `loom_ia.config.references.import_modules`

```python
def import_modules(names: Iterable[str], *, base_dir: Path | None=None) -> Registry
```

Importe les modules de `imports` et enregistre les outils et politiques trouvés.

**Arguments :**

- `names` : Les modules.
- `base_dir` : Dossier de la config : ses modules voisins sont importables.

**Renvoie :**

Le registre.

**Lève :**

- `ConfigError` : Module introuvable, nom réservé ou en double.

## Fonction `loom_ia.config.references.resolve`

```python
def resolve(reference: str, registry: Registry, *, base_dir: Path | None=None) -> object
```

Objet désigné par un nom enregistré ou par `module:attr`.

**Arguments :**

- `reference` : Le nom, ou `module:attr`.
- `registry` : Les objets enregistrés.
- `base_dir` : Dossier de la config.

**Renvoie :**

L'objet.

**Lève :**

- `ConfigError` : Nom inconnu sans `:`, module introuvable, ou attribut absent.

## Classe `loom_ia.config.references._importable`

```python
class _importable
```

Rend un dossier importable le temps du bloc, ses modules voisins d'abord.

## Fonction `loom_ia.config.references._neighbour`

```python
def _neighbour(base_dir: Path, name: str) -> bool
```

Vrai si `name` est un module ou un paquet voisin de ce dossier.

## Fonction `loom_ia.config.references._forget_others`

```python
def _forget_others(base_dir: Path) -> None
```

Retire les modules voisins d'une autre config que ce dossier a aussi (point 50).

Tous d'abord, avant d'importer quoi que ce soit : un module voisin qui en
importe un autre (`from outils import …`) doit trouver celui de ce dossier.

## Fonction `loom_ia.config.references._remember`

```python
def _remember(base_dir: Path) -> None
```

Note les modules voisins de ce dossier que le chargement a importés.

## Fonction `loom_ia.config.references._declared`

```python
def _declared(module: ModuleType) -> list[tuple[str, object]]
```

Outils et politiques déclarés dans un module, par leur nom.
