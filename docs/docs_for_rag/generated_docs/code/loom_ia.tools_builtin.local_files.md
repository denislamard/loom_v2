# loom_ia.tools_builtin.local_files

Module `loom_ia.tools_builtin.local_files` de loom_v2 (fichier `src/loom_ia/tools_builtin/local_files.py`).

Outil local_files : lire, lister, écrire et supprimer des fichiers dans des dossiers.

Les dossiers, leurs droits et l'approbation de chaque droit viennent de la
config (`local_files_params`). Le modèle désigne un fichier par
`<dossier>/<chemin>` : il ne voit jamais un chemin réel, et ne nomme que
les dossiers déclarés.

Les règles des dossiers — chemin tel qu'écrit pour juger droits et
approbation, chemin résolu qui doit rester dans son dossier, écriture
atomique — sont dans `_folders`, partagé avec `loom.excel` ; sa
docstring dit aussi la course connue.

`write` écrit du texte (`content`), ou copie un fichier que l'agent a vu
passer (`file`) : une pièce jointe, un fichier rendu par un outil — un
outil forgé, un sous-agent. Le fichier est relu dans le stockage d'artefacts
par son nom, parmi ceux de la conversation seulement (`ToolContext.files`).

## Constantes de `loom_ia.tools_builtin.local_files`

- `__all__ = ['MAX_COPY_BYTES', 'MAX_READ_BYTES', 'MAX_WRITE_BYTES', 'NAME', 'TEMP_PREFIX', 'LocalFilesTool', 'build']` : TEMP_PREFIX, préfixe des fichiers temporaires d'une écriture, reste importable d'ici.
- `NAME = 'local_files'` : Nom de l'outil.
- `MAX_READ_BYTES = 1000000` : Octets lus au plus par lecture.
- `MAX_WRITE_BYTES = 1000000` : Octets écrits au plus par écriture.
- `MAX_ENTRIES = 500` : Entrées rendues au plus par une liste.
- `MAX_COPY_BYTES = 16 << 20` : Copie d'un fichier de la conversation (write avec file) : binaire permis.
- `Action = Literal['list', 'read', 'write', 'delete']` : Action de l'outil : lister, lire, écrire ou supprimer.

## Classe `loom_ia.tools_builtin.local_files._Arguments`

```python
class _Arguments(BaseModel)
```

Arguments d'un appel : ceux que voit le modèle.

## Fonction `loom_ia.tools_builtin.local_files._effects`

```python
def _effects(request: _Arguments) -> tuple[SideEffects, bool]
```

Effets de l'appel, et s'il est idempotent : réécrire le même contenu ne change rien.

## Classe `loom_ia.tools_builtin.local_files.LocalFilesTool`

```python
class LocalFilesTool
```

L'outil `local_files` monté avec ses dossiers.

### Méthode `loom_ia.tools_builtin.local_files.LocalFilesTool.__init__`

```python
def __init__(self, folders: tuple[Folder, ...]) -> None
```

Prépare l'outil pour ces dossiers : sa description dit les dossiers et leurs droits.

**Arguments :**

- `folders` : Les dossiers permis.

**Lève :**

- `ValueError` : Aucun dossier.

### Méthode `loom_ia.tools_builtin.local_files.LocalFilesTool.spec`

```python
@property
def spec(self) -> ToolSpec
```

La définition de l'outil.

### Méthode `loom_ia.tools_builtin.local_files.LocalFilesTool.settings`

```python
def settings(self) -> Settings
```

Les dossiers et leurs droits, pour `loom validate`.

### Méthode `loom_ia.tools_builtin.local_files.LocalFilesTool.assess_call`

```python
def assess_call(self, arguments: Mapping[str, JsonValue]) -> CallAssessment
```

Approbation et effets de cet appel, d'après ses arguments et les droits du dossier.

### Méthode `loom_ia.tools_builtin.local_files.LocalFilesTool.invoke`

```python
async def invoke(self, arguments: dict[str, JsonValue], context: ToolContext) -> ToolOutput
```

Mène l'action demandée (`list`, `read`, `write`, `delete`) après le contrôle du chemin et des droits, hors de la boucle asyncio.

**Lève :**

- `ToolError` : Chemin hors des dossiers, droit absent, fichier introuvable, trop gros, ou erreur du système.

### Méthode `loom_ia.tools_builtin.local_files.LocalFilesTool._locate`

```python
def _locate(self, request: _Arguments) -> Located
```

Le dossier qui gouverne le chemin, tel qu'écrit : ni disque, ni lien suivi.

### Méthode `loom_ia.tools_builtin.local_files.LocalFilesTool._perform`

```python
def _perform(self, request: _Arguments, located: Located) -> dict[str, JsonValue]
```

L'action sur le disque ; une erreur du système devient un `ToolError`, sans le chemin réel.

### Méthode `loom_ia.tools_builtin.local_files.LocalFilesTool._listed`

```python
def _listed(self, target: Path, located: Located) -> dict[str, JsonValue]
```

Le contenu d'un dossier : fichiers et sous-dossiers, sans les cachés ni les liens qui en sortent, `MAX_ENTRIES` au plus.

### Méthode `loom_ia.tools_builtin.local_files.LocalFilesTool._unlinked`

```python
def _unlinked(self, located: Located) -> dict[str, JsonValue]
```

Supprimer un lien, c'est supprimer le lien, pas ce qu'il désigne.

## Fonction `loom_ia.tools_builtin.local_files._read`

```python
def _read(target: Path, located: Located) -> dict[str, JsonValue]
```

Le texte d'un fichier : UTF-8, `MAX_READ_BYTES` octets au plus ; `ToolError` sinon.

## Fonction `loom_ia.tools_builtin.local_files._copied`

```python
async def _copied(name: str, context: ToolContext) -> bytes
```

Octets d'un fichier de la conversation, désigné par son nom.

## Fonction `loom_ia.tools_builtin.local_files._description`

```python
def _description(space: Folders) -> str
```

La description de l'outil : les dossiers et leurs droits, les actions, et les bornes.

## Fonction `loom_ia.tools_builtin.local_files.build`

```python
def build(params: BaseModel, context: BuiltinContext) -> LocalFilesTool
```

La fabrique de `loom.local_files` : vérifie les dossiers, ne lit ni n'écrit rien.
