# loom_ia.adapters.firecracker.forge

Module `loom_ia.adapters.firecracker.forge` de loom_v2 (fichier `src/loom_ia/adapters/firecracker/forge.py`).

La source `forge` : des outils forgés par les agents, exécutés dans la VM (jalon J6.4c).

Un agent **forge** un outil — un nom, une description, un schéma d'entrée, le
code d'un module qui définit une fonction du même nom, des exemples — puis
l'**appelle**. Rien de ce code ne s'exécute sur l'hôte : l'hôte le lit
(syntaxe, signature), le range, et l'envoie à execd à chaque exécution. C'est
le principe d'execd : la VM ne garde aucun catalogue, chaque appel transporte
le code.

    tool_sources:
      - name: forge
        entry_point: forge
        params:
          vm_dir: ~/temp          # dossier de VM construit par make_vm.sh
          catalog_dir: outils     # catalogue, relatif au dossier de la config
          limits: {wall_ms: 10000}
          files: {max_files: 10}  # fichiers de out/ rapatriés ; false : aucun

La source fournit à chaque run :

- `forge` : forge (ou remplace) un outil. Les contrôles de l'hôte d'abord,
  puis chaque exemple exécuté dans la VM et comparé à ce qu'il doit rendre ;
  au premier écart, l'outil est refusé et le modèle apprend pourquoi.
  Accepté, il est écrit dans `catalog_dir/<client>/<nom>/` : `<nom>.py`
  et `outil.json`.
- `call` : appelle un outil du catalogue par son nom, avec ses arguments —
  le catalogue est lu à l'appel, donc un outil forgé plus tôt dans le run
  s'y trouve.
- chaque outil du catalogue du client, lu à l'ouverture du run : un outil
  forgé pendant un run n'y apparaît qu'au run suivant (la liste des outils
  d'un run est fixée à son ouverture).

La VM n'est démarrée qu'à la première exécution, et la session execd du run
ouverte à ce moment-là : `loom validate`, le rejeu et le montage d'essai de
`serve --reload` ouvrent la source sans rien exécuter.

`input_schema` et `examples` se donnent en objets JSON, ou en texte JSON :
certains fournisseurs (MiniMax) déforment les objets libres d'un appel d'outil
— une liste `required` devenue un objet, des `properties` emboîtées —,
alors qu'une chaîne arrive intacte. Un refus qui porte sur leur forme le
suggère, pour ceux venus en objets.

Le schéma est écrit par le modèle, et l'hôte le valide dans sa boucle
d'événements, pour les exemples comme pour chaque appel : ses motifs
(`pattern`, `patternProperties`…) s'exécutent avec un délai
(`core.bounded_regex`). Un motif trop coûteux refuse l'outil ou l'appel ; il
ne gèle pas l'hôte.

Fichiers produits : ce qu'un outil forgé écrit dans `out/` est rapatrié à
la fin de l'appel, dans la même prise de la session (un autre appel du run
viderait `out/`), puis rendu en blocs de données que le moteur range dans
le stockage d'artefacts : le modèle n'en voit qu'une mention (ou l'image,
s'il les voit), le journal l'URI. Chaque fichier est borné en nombre et en
taille avant transfert, d'après le manifeste d'execd, puis contrôlé à
l'arrivée (taille et sha256) : un fichier qui a changé entre-temps est
écarté, pas rendu à moitié. Les exemples de `forge` ne rapatrient rien.

## Constantes de `loom_ia.adapters.firecracker.forge`

- `FORGE = 'forge'` : Les deux outils fixes de la source ; aucun outil forgé ne peut porter leur nom.
- `CALL = 'call'` : Nom de l'outil `call` de la source (voir `FORGE`).
- `MANIFEST = 'outil.json'` : Ce qui accompagne le code d'un outil dans son dossier du catalogue.
- `NAME_PATTERN = '[a-z][a-z0-9_]{0,47}'` : Nom d'outil forgé : un identifiant Python en minuscules. Il devient le nom du module et de la fonction ; préfixé par loom, il tient dans les 64 caractères des API.
- `TENANT_PATTERN = '[A-Za-z0-9][A-Za-z0-9_.-]{0,63}'` : Identifiant de client admis comme nom de dossier.
- `MAX_EXAMPLES = 10` : Exemples qu'un outil forgé donne au plus ; il en faut au moins un.
- `SHOWN = 4000` : Ce que le modèle voit de stdout et de stderr, chacun (début et fin).
- `LIMIT_KEYS = ('wall_ms', 'cpu_ms', 'mem_bytes', 'fsize_bytes', 'nofile', 'nproc', 'out_files')` : Clés de `limits` qu'execd connaît.
- `DEFAULT_WALL_MS = 30000` : wall_ms qu'execd applique quand on ne demande rien.
- `BOOT_WAIT = 60.0` : Attente du boot de la VM et d'execd, au premier appel d'un run (secondes).
- `MAX_FILE_CEILING = 64 << 20` : Plafond de `files.max_file_bytes` : ce qu'un fichier rapatrié de out/ peut peser au plus.
- `MAX_TOTAL_CEILING = 256 << 20` : Plafond de `files.max_total_bytes` : ce que les fichiers rapatriés d'un appel peuvent peser ensemble.
- `MAX_FILES_CEILING = 512` : out_files : ce qu'execd accepte au plus dans out/ (son plafond).
- `FETCH_WAIT = 30.0` : Temps ajouté au délai de l'outil pour rapatrier les fichiers de out/, en secondes.
- `FETCH_BUDGET = 25.0` : Échéance du transfert lui-même, prise dans FETCH_WAIT (le reste : marge).
- `MAX_NAME = 200` : Longueur au plus d'un nom de fichier montré au modèle.
- `SHOWN_LEFT = 20` : Lignes « non rapatriés » montrées au plus au modèle.
- `_TEXT_FIELDS = ('input_schema', 'examples')` : Les champs de `forge` admis aussi en texte JSON.
- `DEFAULT_FILES = FilePolicy()` : Rapatriement sans `files` dans les params : 10 fichiers, 16 Mio chacun, 32 Mio en tout.
- `FORGE_DESCRIPTION = "Forge un nouvel outil Python, exécuté dans une VM isolée sans réseau (bibliothèque standard seulement). `code` est un …` : Description de l'outil `forge` lue par le modèle ; un mot sur les fichiers s'y ajoute quand ils sont rapatriés.
- `CALL_DESCRIPTION = "Appelle un outil forgé par son nom (`name`), avec ses arguments (`arguments`, selon son schéma) ; il s'exécute dans la…` : Description de l'outil `call` lue par le modèle.

## Classe `loom_ia.adapters.firecracker.forge.Jobs`

```python
class Jobs(Protocol)
```

Ce que la source demande d'une session execd (`Session` le fait).

### Méthode `loom_ia.adapters.firecracker.forge.Jobs.closed`

```python
@property
def closed(self) -> bool
```

Vrai si la session est fermée.

### Méthode `loom_ia.adapters.firecracker.forge.Jobs.hello`

```python
@property
def hello(self) -> Hello
```

Ce que l'invité a annoncé à l'ouverture.

### Méthode `loom_ia.adapters.firecracker.forge.Jobs.put_code`

```python
async def put_code(self, path: str, data: bytes) -> None
```

Dépose un module dans `code/` de la session.

### Méthode `loom_ia.adapters.firecracker.forge.Jobs.exec`

```python
async def exec(self, entrypoint: str, *, args: Mapping[str, object] | None=None, limits: Mapping[str, int] | None=None, env: Mapping[str, str] | None=None, wait: float | None=None) -> Execution
```

Exécute `module:fonction` dans la VM et rend son compte rendu.

### Méthode `loom_ia.adapters.firecracker.forge.Jobs.get_file`

```python
async def get_file(self, path: str, *, max_bytes: int) -> bytes
```

Rapatrie un fichier de la session, `max_bytes` octets au plus.

### Méthode `loom_ia.adapters.firecracker.forge.Jobs.close`

```python
async def close(self) -> None
```

Ferme la session : execd arrête le job et efface son dossier.

## Classe `loom_ia.adapters.firecracker.forge.Runner`

```python
class Runner(Protocol)
```

D'où viennent les sessions : la VM, ou un double dans les essais.

### Méthode `loom_ia.adapters.firecracker.forge.Runner.session`

```python
async def session(self) -> Jobs
```

Une session neuve ; la VM est démarrée si elle ne tourne pas.

### Méthode `loom_ia.adapters.firecracker.forge.Runner.aclose`

```python
async def aclose(self) -> None
```

Arrête ce qui doit l'être quand la source est démontée.

## Classe `loom_ia.adapters.firecracker.forge.VmRunner`

```python
class VmRunner
```

Les sessions d'un dossier de VM : démarrée au besoin, arrêtée si c'est nous.

### Méthode `loom_ia.adapters.firecracker.forge.VmRunner.session`

```python
async def session(self) -> Session
```

Une session execd neuve ; la VM est démarrée si elle ne tourne pas, et attendue `BOOT_WAIT` secondes au plus.

**Lève :**

- `VmError` : `EXECD_PORT` absent de `vm.env`, ou démarrage raté.

### Méthode `loom_ia.adapters.firecracker.forge.VmRunner.aclose`

```python
async def aclose(self) -> None
```

Arrête la VM si c'est cette source qui l'a démarrée.

## Classe `loom_ia.adapters.firecracker.forge.FilePolicy`

```python
@dataclass(frozen=True, slots=True)
class FilePolicy
```

Ce qu'un appel rapatrie de `out/` (params `files`).

**Attributs :**

- `max_files` : Fichiers rapatriés au plus par appel (plafond : 512).
- `max_file_bytes` : Taille au plus d'un fichier, en octets (plafond : 64 Mio).
- `max_total_bytes` : Taille au plus de l'ensemble, en octets (plafond : 256 Mio ; au moins `max_file_bytes`).

## Classe `loom_ia.adapters.firecracker.forge._Fetched`

```python
@dataclass(frozen=True, slots=True)
class _Fetched
```

Fichiers rapatriés d'une exécution, et ceux laissés dans la VM, avec la raison.

## Fonction `loom_ia.adapters.firecracker.forge._fetch`

```python
async def _fetch(jobs: Jobs, execution: Execution, policy: FilePolicy) -> _Fetched
```

Rapatrie les fichiers du manifeste, dans ses bornes ; jamais un fichier altéré.

Le manifeste vient de l'invité : il est trié (l'ordre d'`os.walk` entre
sous-dossiers n'est pas garanti), dédoublonné, et ses noms nettoyés avant
d'être montrés. Le transfert a son échéance, prise dans le délai de
l'outil : passée, la session est fermée et le reste listé, le résultat du
job gardé.

## Fonction `loom_ia.adapters.firecracker.forge._shown`

```python
def _shown(path: str) -> str
```

Un chemin de l'invité tel qu'on peut le montrer : sans caractère de contrôle, borné.

## Classe `loom_ia.adapters.firecracker.forge._Run`

```python
@dataclass(slots=True)
class _Run
```

Ce que les outils d'un run partagent : sa session, ouverte au premier appel.

**Attributs :**

- `runner` : D'où vient la session.
- `limits` : Limites d'un job, passées à execd.
- `files` : Ce qui est rapatrié de out/ ; `None` : rien.
- `jobs` : La session execd, ouverte au premier appel.
- `loaded` : Empreinte du code déposé par module, pour ne le renvoyer qu'au changement.
- `lock` : Un appel du run à la fois : dépôt, exécution et rapatriement d'un seul tenant.

### Méthode `loom_ia.adapters.firecracker.forge._Run.execute`

```python
async def execute(self, name: str, code: str, arguments: Mapping[str, object]) -> Execution
```

Dépose le module si besoin, puis appelle `name:name` avec ces arguments.

### Méthode `loom_ia.adapters.firecracker.forge._Run.produce`

```python
async def produce(self, name: str, code: str, arguments: Mapping[str, object]) -> tuple[Execution, _Fetched]
```

Comme `execute`, puis rapatrie ce que le job a écrit dans `out/`.

### Méthode `loom_ia.adapters.firecracker.forge._Run._execute`

```python
async def _execute(self, name: str, code: str, arguments: Mapping[str, object], *, fetch: bool) -> tuple[Execution, _Fetched]
```

Un appel d'outil forgé, d'un seul tenant, sous verrou.

Ouvre la session au premier appel (refusée si `limits` dépasse les
plafonds de la VM), dépose le module s'il a changé, exécute `name:name`,
puis rapatrie les fichiers de out/ si `fetch`.

### Méthode `loom_ia.adapters.firecracker.forge._Run.close`

```python
async def close(self) -> None
```

Ferme la session execd du run, si elle est ouverte.

## Classe `loom_ia.adapters.firecracker.forge.Forged`

```python
@dataclass(frozen=True, slots=True)
class Forged
```

Un outil du catalogue.

**Attributs :**

- `name` : Nom de l'outil, celui du module et de sa fonction.
- `description` : Description lue par le modèle.
- `input_schema` : Schéma JSON de ses arguments.
- `code` : Code du module.
- `examples` : Exemples passés à la forge : `arguments` et `expected`.
- `forged_by` : Run qui l'a forgé.
- `forged_at` : Quand il a été forgé (ISO 8601, UTC).

## Classe `loom_ia.adapters.firecracker.forge.Catalog`

```python
class Catalog
```

Les outils forgés, rangés sous `root/<client>/<nom>/` : `<nom>.py` et `outil.json`.

### Méthode `loom_ia.adapters.firecracker.forge.Catalog.folder`

```python
def folder(self, tenant: str) -> Path
```

Dossier du catalogue d'un client ; `ValueError` si son identifiant ne peut pas nommer un dossier.

### Méthode `loom_ia.adapters.firecracker.forge.Catalog.list`

```python
def list(self, tenant: str) -> list[Forged]
```

Les outils du client, par nom ; un dossier illisible est écarté et signalé.

### Méthode `loom_ia.adapters.firecracker.forge.Catalog.get`

```python
def get(self, tenant: str, name: str) -> Forged | None
```

Un outil du catalogue du client ; `None` s'il n'existe pas, ou si son nom est hors format.

### Méthode `loom_ia.adapters.firecracker.forge.Catalog.save`

```python
def save(self, tenant: str, forged: Forged) -> None
```

Écrit l'outil ; un outil du même nom est remplacé d'un bloc.

### Méthode `loom_ia.adapters.firecracker.forge.Catalog._read`

```python
def _read(self, entry: Path) -> Forged | None
```

Lit le dossier d'un outil (son code et `outil.json`) ; `None`, avec un avertissement, s'il est illisible.

## Classe `loom_ia.adapters.firecracker.forge._ShapeError`

```python
class _ShapeError(ToolError)
```

Un refus qui porte sur la forme d'`input_schema` ou d'`examples`.

## Fonction `loom_ia.adapters.firecracker.forge._decoded`

```python
def _decoded(value: JsonValue, field: str) -> JsonValue
```

Un champ venu en objet tel quel, ou venu en texte JSON, décodé.

## Fonction `loom_ia.adapters.firecracker.forge.check_forged`

```python
def check_forged(name: str, input_schema: dict[str, JsonValue], code: str, examples: Sequence[JsonValue]) -> list[tuple[dict[str, JsonValue], JsonValue]]
```

Ce que l'hôte peut dire d'un outil sans l'exécuter ; `ToolError` au premier défaut.

Rend les exemples sous forme (arguments, attendu). Un défaut de forme
d'`input_schema` ou d'`examples` — accord du code avec le schéma
compris — est un `_ShapeError`.

## Fonction `loom_ia.adapters.firecracker.forge._check_signature`

```python
def _check_signature(name: str, code: str, properties: set[str], required: set[str]) -> None
```

Vérifie, sans l'exécuter, la fonction `name` du code.

Elle doit être définie au premier niveau, recevoir ses arguments par nom
(ni `/` ni `*args`), recevoir chaque propriété d'`input_schema` (sauf
avec `**kwargs`), et n'exiger que des propriétés requises par le schéma.

**Lève :**

- `ToolError` : Erreur de syntaxe, fonction absente ou signature refusée.

## Fonction `loom_ia.adapters.firecracker.forge._json_kind`

```python
def _json_kind(value: object) -> str
```

Le type JSON d'une valeur, tel que le refus le nomme.

## Fonction `loom_ia.adapters.firecracker.forge._mismatch_hint`

```python
def _mismatch_hint(expected: object, result: object) -> str
```

Ce qu'un écart doit au type de `expected` ; vide s'il n'y a rien à en dire.

Vu au run réel de MiniMax (08/10) : `expected` donné en texte JSON, une
chaîne, quand la fonction rend un objet — puis encodé deux fois quand elle
rend une chaîne. Le refus ne montrait que les deux valeurs.

## Fonction `loom_ia.adapters.firecracker.forge._failure`

```python
def _failure(execution: Execution) -> str
```

Ce que le modèle lit d'un job en échec : genre et message de l'erreur, trace, détail, un rappel pour un import refusé, puis stdout et stderr.

## Fonction `loom_ia.adapters.firecracker.forge._output`

```python
def _output(execution: Execution, fetched: _Fetched) -> ToolOutput
```

Le résultat d'un appel d'outil forgé ; une erreur pour un job en échec.

Le texte porte la valeur JSON rendue, stdout et stderr, et les fichiers non
rapatriés ; suivent les fichiers rapatriés en blocs de données. `data`
porte la valeur rendue.

## Fonction `loom_ia.adapters.firecracker.forge._timeout`

```python
def _timeout(limits: Mapping[str, int], jobs: int=1, *, fetch: bool=False) -> float
```

Délai de loom pour un outil qui lance `jobs` exécutions, boot compris.

Sans lui, loom couperait au bout de son délai par défaut, avant qu'execd
ait rendu son verdict. `fetch` : le rapatriement de out/ en plus.

## Fonction `loom_ia.adapters.firecracker.forge._files_hint`

```python
def _files_hint(files: FilePolicy) -> str
```

Ce que la description de `forge` dit des fichiers, quand ils sont rapatriés.

## Classe `loom_ia.adapters.firecracker.forge._ForgeTool`

```python
@dataclass(frozen=True, slots=True)
class _ForgeTool
```

L'outil `forge` d'un run.

**Attributs :**

- `catalog` : Le catalogue des outils forgés.
- `run` : Ce que les outils du run partagent (session execd).
- `spec` : Définition de l'outil lue par le modèle.

### Méthode `loom_ia.adapters.firecracker.forge._ForgeTool.invoke`

```python
async def invoke(self, arguments: dict[str, JsonValue], context: ToolContext) -> ToolOutput
```

Forge un outil, ou le remplace.

Les contrôles de l'hôte d'abord, puis chaque exemple exécuté dans la VM et
comparé à `expected` ; au premier écart, l'outil est refusé et le modèle
apprend pourquoi. Accepté, il est écrit au catalogue du client.
`input_schema` et `examples` sont admis en texte JSON.

## Classe `loom_ia.adapters.firecracker.forge._CallTool`

```python
@dataclass(frozen=True, slots=True)
class _CallTool
```

L'outil `call` d'un run.

**Attributs :**

- `catalog` : Le catalogue des outils forgés.
- `run` : Ce que les outils du run partagent (session execd).
- `spec` : Définition de l'outil lue par le modèle.

### Méthode `loom_ia.adapters.firecracker.forge._CallTool.invoke`

```python
async def invoke(self, arguments: dict[str, JsonValue], context: ToolContext) -> ToolOutput
```

Appelle un outil du catalogue du client : arguments validés par son schéma, exécution dans la VM, fichiers de out/ rapatriés.

**Lève :**

- `ToolError` : Outil inconnu (le catalogue est cité), ou arguments refusés par son schéma.

## Classe `loom_ia.adapters.firecracker.forge._ForgedTool`

```python
@dataclass(frozen=True, slots=True)
class _ForgedTool
```

Un outil du catalogue, monté comme outil à part entière.

**Attributs :**

- `forged` : L'outil du catalogue.
- `run` : Ce que les outils du run partagent (session execd).
- `spec` : Définition de l'outil lue par le modèle.

### Méthode `loom_ia.adapters.firecracker.forge._ForgedTool.invoke`

```python
async def invoke(self, arguments: dict[str, JsonValue], context: ToolContext) -> ToolOutput
```

Exécute l'outil forgé dans la VM et rapatrie les fichiers de out/.

## Fonction `loom_ia.adapters.firecracker.forge._forge_spec`

```python
def _forge_spec(limits: Mapping[str, int], files: FilePolicy | None) -> ToolSpec
```

La définition de l'outil `forge` : description, schéma et délai.

La description dit les fichiers s'ils sont rapatriés ; le délai couvre le
boot et `MAX_EXAMPLES` exécutions.

## Fonction `loom_ia.adapters.firecracker.forge._call_spec`

```python
def _call_spec(limits: Mapping[str, int]) -> ToolSpec
```

La définition de l'outil `call` : nom et arguments, et le délai d'une exécution avec rapatriement.

## Fonction `loom_ia.adapters.firecracker.forge._run_start`

```python
def _run_start(run_id: str) -> datetime | None
```

Le début d'un run, lu dans son identifiant (UUIDv7, qui porte l'heure en ms).

## Classe `loom_ia.adapters.firecracker.forge.ForgeSource`

```python
class ForgeSource
```

La source `forge` : ses deux outils fixes et le catalogue du client.

Un run voit comme outils à part entière ceux du catalogue forgés avant son
début ; `call` voit tout le catalogue, au moment de l'appel.

### Méthode `loom_ia.adapters.firecracker.forge.ForgeSource.__init__`

```python
def __init__(self, *, name: str, catalog: Catalog, runner: Runner, limits: Mapping[str, int], files: FilePolicy | None=DEFAULT_FILES) -> None
```

Prépare la source, sans démarrer la VM.

**Arguments :**

- `name` : Nom de la source.
- `catalog` : Le catalogue des outils forgés.
- `runner` : D'où viennent les sessions execd.
- `limits` : Limites d'un job (`wall_ms`, `cpu_ms`…).
- `files` : Ce qui est rapatrié de out/ ; `None` : rien.

### Méthode `loom_ia.adapters.firecracker.forge.ForgeSource.open`

```python
@asynccontextmanager
async def open(self, context: SourceContext) -> AsyncGenerator[Sequence[Tool]]
```

Les outils d'un run : `forge`, `call`, et ceux du catalogue forgés avant le début du run.

La session execd du run s'ouvre au premier appel et se ferme à la sortie.

**Arguments :**

- `context` : Le contexte du run.

**Produit :**

Les outils du run.

**Lève :**

- `SourceUnavailable` : Catalogue illisible.

### Méthode `loom_ia.adapters.firecracker.forge.ForgeSource.aclose`

```python
async def aclose(self) -> None
```

Arrête la VM si la source l'a démarrée.

## Fonction `loom_ia.adapters.firecracker.forge._files`

```python
def _files(value: JsonValue) -> FilePolicy | None
```

`files` : absent, les bornes par défaut ; `false`, rien n'est rapatrié.

## Fonction `loom_ia.adapters.firecracker.forge.forge_source`

```python
def forge_source(*, name: str, params: Mapping[str, JsonValue], secrets: Mapping[str, str], base_dir: Path) -> ForgeSource
```

La fabrique du point d'entrée `forge` : vérifie `params`, ne démarre rien.
