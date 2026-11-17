# loom_ia.tools_builtin.local_files_params

Module `loom_ia.tools_builtin.local_files_params` de loom_v2 (fichier `src/loom_ia/tools_builtin/local_files_params.py`).

Paramètres de `loom.local_files` : les dossiers permis, et pour chacun ses droits.

Module léger — Pydantic seulement — : la config l'importe pour valider la
référence au chargement, sans importer l'outil.

    - python: loom.local_files
      folders:
      - name: work
        path: /home/denis/temp/test
        rights: [ read, write, overwrite, delete ]
        approval: { overwrite: never, delete: never }   # rien ne bloque
      - name: devis
        path: devis
        rights: [ read, write ]        # approbations par défaut

`rights` liste les droits **accordés** ; un droit absent est refusé.
`approval`, facultatif, dit lesquels attendent un humain : `never` (l'appel
passe) ou `always` (validation humaine). Un droit qui n'y figure pas garde son
approbation par défaut : `read` et `write` à `never`, `overwrite` et
`delete` à `always`. Le modèle garde la forme que lit l'outil, `rights`
en table droit → approbation (`_rights`).

## Constantes de `loom_ia.tools_builtin.local_files_params`

- `Right = Literal['read', 'write', 'overwrite', 'delete']` : Droit sur un dossier : lire, écrire un fichier neuf, écraser, supprimer.
- `RightApproval = Literal['never', 'always']` : Approbation d'un droit : `never` ou `always`.
- `RIGHTS = ('read', 'write', 'overwrite', 'delete')` : Dans l'ordre où on les dit.
- `DEFAULT_APPROVAL = {'read': 'never', 'write': 'never', 'overwrite': 'always', 'delete': 'always'}` : Approbation par défaut de chaque droit : `read` et `write` à `never`, `overwrite` et `delete` à `always`.
- `DESTRUCTIVE = frozenset({'overwrite', 'delete'})` : Ceux qui détruisent ce qui existe : `loom validate` signale ceux qui passent sans approbation.
- `FOLDER_NAME_PATTERN = '^[a-z0-9][a-z0-9_-]{0,31}$'` : Nom d'un dossier pour le modèle : minuscules, chiffres, « _ » et « - ».

## Classe `loom_ia.tools_builtin.local_files_params.FolderSpec`

```python
class FolderSpec(BaseModel)
```

Un dossier permis : son nom pour le modèle, son chemin, ses droits.

**Attributs :**

- `name` : Ce que voit le modèle : il désigne un fichier par « <name>/<chemin> », et le chemin réel ne lui est jamais montré.
- `path` : Absolu, ou relatif au dossier du fichier de config. Ni « ~ » ni variable (« $HOME ») : la config n'interprète rien.
- `rights` : Droits accordés (`read`, `write`, `overwrite`, `delete`) et l'approbation de chacun. En entrée, une liste et `approval` ; ici, la table droit → approbation.

### Méthode `loom_ia.tools_builtin.local_files_params.FolderSpec._resolve`

```python
@model_validator(mode='before')
@classmethod
def _resolve(cls, data: object) -> object
```

Ramène `rights` et `approval` à la table droit → approbation.

### Méthode `loom_ia.tools_builtin.local_files_params.FolderSpec.approval`

```python
def approval(self, right: Right) -> RightApproval | None
```

L'approbation du droit, ou `None` s'il n'est pas accordé.

## Classe `loom_ia.tools_builtin.local_files_params.LocalFilesParams`

```python
class LocalFilesParams(BaseModel)
```

Paramètres de `loom.local_files`.

**Attributs :**

- `folders` : Dossiers permis, au moins un, de noms distincts.
