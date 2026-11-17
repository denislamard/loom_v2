# loom_ia.tools_builtin._folders

Module `loom_ia.tools_builtin._folders` de loom_v2 (fichier `src/loom_ia/tools_builtin/_folders.py`).

Dossiers permis des outils fournis : désigner un fichier, juger ses droits, l'écrire.

Partagé par `loom.local_files` et `loom.excel` : un seul endroit décide
de ce qu'un outil fourni peut atteindre sur le disque.

Le modèle désigne un fichier par `<dossier>/<chemin>` : il ne voit jamais
un chemin réel, et ne nomme que les dossiers déclarés.

Ce qui décide d'un appel ne dépend que de ses arguments et de la config : le
dossier qui gouverne le chemin est le dossier déclaré le plus profond qui le
contient, **chemin pris tel qu'écrit** (`Folders.locate`). C'est sur lui que
se jugent les droits et l'approbation, à l'évaluation de l'appel comme à son
exécution. À l'exécution, le chemin est résolu (liens suivis,
`Folders.resolved`) et doit rester dans ce même dossier déclaré : un lien
qui mène ailleurs — hors des dossiers permis, ou dans un autre dossier
déclaré, qui aurait ses propres droits — est refusé. Ainsi un lien posé entre
l'évaluation et l'exécution ne peut pas faire passer un appel sous une
approbation qui n'était pas la sienne.

Reste une course, documentée : entre le contrôle du chemin résolu et
l'accès, un autre programme qui remplacerait un dossier par un lien pourrait
faire sortir l'accès du dossier. Les outils ne s'en gardent pas (pas
d'`openat`) ; qu'aucun autre programme n'écrive de liens dans les dossiers
exposés.

## Constantes de `loom_ia.tools_builtin._folders`

- `TEMP_PREFIX = '.loom-'` : Fichier temporaire d'une écriture : caché, donc invisible du modèle.
- `RIGHT_WORDS = {'read': 'lire', 'write': 'écrire', 'overwrite': 'remplacer un fichier existant', 'delete': 'supprimer'}` : Ce qu'un droit permet, dit à l'approbateur.

## Classe `loom_ia.tools_builtin._folders.Folder`

```python
@dataclass(frozen=True, slots=True)
class Folder
```

Un dossier permis, tel que l'outil l'utilise : sa racine résolue et ses droits.

**Attributs :**

- `name` : Nom du dossier pour le modèle.
- `root` : Sa racine, résolue.
- `rights` : Ses droits, et l'approbation de chacun.

### Méthode `loom_ia.tools_builtin._folders.Folder.granted`

```python
def granted(self) -> str
```

« read, write » : ses droits, dans l'ordre où on les dit.

### Méthode `loom_ia.tools_builtin._folders.Folder.asking`

```python
def asking(self) -> list[Right]
```

Ses droits qui attendent une approbation.

## Classe `loom_ia.tools_builtin._folders.Located`

```python
@dataclass(frozen=True, slots=True)
class Located
```

Un chemin donné par le modèle, rapporté à son dossier, sans rien lire sur le disque.

**Attributs :**

- `lexical` : Le chemin, sans lien suivi.
- `folder` : Le dossier permis où il se trouve.

### Méthode `loom_ia.tools_builtin._folders.Located.address`

```python
@property
def address(self) -> str
```

L'adresse que voit le modèle : `<dossier>/<chemin>`, ou le nom du dossier seul pour sa racine.

## Classe `loom_ia.tools_builtin._folders.Folders`

```python
class Folders
```

Les dossiers permis d'un outil monté.

### Méthode `loom_ia.tools_builtin._folders.Folders.places`

```python
def places(self) -> tuple[Place, ...]
```

Pour `loom validate` : chaque dossier et ses droits accordés.

Un droit refusé n'est pas listé. `granted()`, que lit le modèle, reste à part.

### Méthode `loom_ia.tools_builtin._folders.Folders.described`

```python
def described(self) -> list[str]
```

Une ligne par dossier, pour la description que lit le modèle : jamais le chemin.

### Méthode `loom_ia.tools_builtin._folders.Folders.locate`

```python
def locate(self, path: str, *, folder_allowed: bool=False) -> Located
```

Le dossier qui gouverne le chemin, tel qu'écrit : ni disque, ni lien suivi.

`folder_allowed` : « <dossier> » seul est permis (un listage).

### Méthode `loom_ia.tools_builtin._folders.Folders.require`

```python
@staticmethod
def require(located: Located, rights: Iterable[Right]) -> None
```

`ToolError` si le dossier qui gouverne le chemin n'accorde pas ces droits.

### Méthode `loom_ia.tools_builtin._folders.Folders.asked`

```python
@staticmethod
def asked(located: Located, rights: Iterable[Right]) -> list[Right]
```

Parmi ces droits, ceux que le dossier soumet à une approbation.

### Méthode `loom_ia.tools_builtin._folders.Folders.rule`

```python
@staticmethod
def rule(asked: Iterable[tuple[Located, Right]]) -> str
```

La règle de config qui exige l'approbation, en clair, dossier par dossier.

« la config exige une approbation pour écrire et remplacer un fichier
existant dans sortie » : l'approbateur lit ce que la règle protège, pas
le nom du droit (essai du 01/11 : « overwrite sur … » ne disait rien).

### Méthode `loom_ia.tools_builtin._folders.Folders.resolved`

```python
def resolved(self, located: Located) -> Path
```

Le chemin réel, liens suivis, qui doit rester dans le dossier qui le gouverne.

### Méthode `loom_ia.tools_builtin._folders.Folders.real`

```python
def real(self, path: Path, located: Located) -> Path
```

`path` résolu, refusé s'il sort du dossier qui gouverne `located`.

### Méthode `loom_ia.tools_builtin._folders.Folders.outside`

```python
def outside(self, real: Path, folder: Folder) -> Folder | Literal['outside', 'hidden'] | None
```

Pourquoi un chemin réel sort de `folder` : hors de tout, caché, ou un autre dossier.

## Fonction `loom_ia.tools_builtin._folders.mount`

```python
def mount(specs: Iterable[FolderSpec], context: BuiltinContext) -> tuple[Folder, ...]
```

Les dossiers de la config, vérifiés : présents, distincts, sans rien exposer de loom.

`ValueError` dit ce qui ne va pas ; rien n'est lu ni écrit.

## Fonction `loom_ia.tools_builtin._folders._exposes`

```python
def _exposes(root: Path, protected: tuple[Protected, ...]) -> str | None
```

Ce que le dossier exposerait de loom, ou `None`.

## Fonction `loom_ia.tools_builtin._folders.write_bytes`

```python
def write_bytes(target: Path, located: Located, data: bytes, overwrite: bool) -> dict[str, JsonValue]
```

Écrit `data` en `target` (déjà résolu) : création exclusive, ou remplacement.

Le même contenu déjà là n'est pas réécrit (`written` faux) : c'est ce qui
rend l'écriture sûre à refaire après une interruption. Un fichier existant
n'est remplacé qu'avec `overwrite`, et jamais s'il est en lecture seule.

## Fonction `loom_ia.tools_builtin._folders.atomic_write`

```python
def atomic_write(target: Path, data: bytes, mode: int | None, *, replace: bool) -> None
```

Fichier temporaire voisin, puis mise en place : jamais de fichier à moitié écrit.

`replace` : le fichier existant est remplacé (renommage). Sinon la mise
en place est **exclusive** — un lien physique, qui échoue si la cible existe
déjà (`FileExistsError`) : deux écritures parallèles ne s'écrasent pas.
Sans liens physiques (certains systèmes de fichiers), le fichier est créé
directement, exclusif lui aussi, mais sans l'atomicité du renommage.

Un fichier remplacé garde ses permissions ; un nouveau prend celles que
donne le masque du process (`0o666` filtré par l'umask).

## Fonction `loom_ia.tools_builtin._folders.write_new`

```python
def write_new(path: Path, data: bytes) -> None
```

Crée `path` et l'écrit ; un échec en cours d'écriture ne laisse rien derrière.

`FileExistsError` si le fichier existe : il n'est alors pas touché.

## Fonction `loom_ia.tools_builtin._folders._sync_directory`

```python
def _sync_directory(directory: Path) -> None
```

Pour que la mise en place survive à une coupure ; sans échouer là où c'est impossible.
