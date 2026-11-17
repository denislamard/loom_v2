# loom_ia.config.env_file

Module `loom_ia.config.env_file` de loom_v2 (fichier `src/loom_ia/config/env_file.py`).

Le fichier d'environnement d'un projet (`.env`), lu au démarrage de `loom`.

Les clés d'API, les DSN, `LOOM_PROFILE`… vivent dans l'environnement : la
configuration ne fait qu'en nommer les variables (conception §16.3). Un fichier `.env`
les y pose, sans `export` ni `uv run --env-file`.

Le fichier lu, du plus fort au plus faible :

1. celui que donne l'appelant (`--env-file`) ;
2. `env_file:` à la racine de `loom.yaml`, relatif à son dossier ;
3. `.env` dans le dossier de `loom.yaml`, s'il existe.

Un fichier demandé (1 ou 2) et absent est une erreur ; le `.env` par défaut
absent n'en est pas une, et il n'est cherché que si `loom.yaml` existe.
Jamais le dossier courant, jamais un dossier parent : un projet rangé sous un
dépôt n'hérite pas des clés de sa racine.

Une variable déjà définie — même vide — l'emporte sur le fichier : ce que le
shell, le service ou `uv run --env-file` ont posé n'est jamais écrasé. Le
fichier est lu tel quel, sans interpolation (`${A}` reste `${A}`). Les
valeurs ne sortent jamais d'ici : ni message d'erreur, ni rapport, seulement
les noms.

La librairie ne lit rien d'elle-même : `load_env_file("loom.yaml")` le fait
à la demande, avant `Loom.from_config`.

Fonction M6. Options de la CLI : `--env-file` et `--no-env-file`.

## Constantes de `loom_ia.config.env_file`

- `DEFAULT_NAME = '.env'` : Le fichier cherché à côté de loom.yaml quand rien d'autre n'est désigné.
- `ENV_FILE_KEY = 'env_file'` : La clé de loom.yaml qui désigne le fichier : à la racine seulement, car il est lu avant le choix du profil.
- `EnvSource = Literal['option', 'config', 'défaut', 'désactivé']` : D'où vient le fichier lu : `--env-file`, `env_file:`, le `.env` voisin, ou `--no-env-file`.
- `DISABLED = EnvFileReport(path=None, source='désactivé')` : Rapport de `--no-env-file` : rien n'est lu.

## Classe `loom_ia.config.env_file.EnvFileReport`

```python
@dataclass(frozen=True, slots=True)
class EnvFileReport
```

Ce qu'une lecture a fait : le fichier, d'où il vient, et les noms — jamais les valeurs.

**Attributs :**

- `path` : Fichier retenu ; `None` quand rien n'est à lire (lecture désactivée, ou pas de loom.yaml à côté duquel chercher).
- `source` : D'où vient le fichier.
- `found` : Vrai si le fichier existait et a été lu.
- `loaded` : Variables posées depuis le fichier, dans son ordre.
- `kept` : Variables du fichier déjà définies : l'environnement l'emporte.

### Méthode `loom_ia.config.env_file.EnvFileReport.describe`

```python
def describe(self, *, shown: str | None=None) -> str
```

Une ligne pour `loom validate` et le bandeau de `loom serve`.

`shown` remplace le chemin du fichier à l'affichage (`loom validate`
l'écrit relatif au dossier de la config).

### Méthode `loom_ia.config.env_file.EnvFileReport.prod_warning`

```python
def prod_warning(self) -> str | None
```

L'avertissement du profil prod quand des variables viennent du fichier, sinon None.

Un avertissement, pas un refus : un `EnvironmentFile` de systemd ne
fait pas autre chose, et le service démarre.

## Fonction `loom_ia.config.env_file.load_env_file`

```python
def load_env_file(config: str | Path, *, env_file: str | Path | None=None, environ: MutableMapping[str, str] | None=None) -> EnvFileReport
```

Pose dans `environ` (par défaut `os.environ`) les variables du fichier d'environnement.

`config` est le chemin de `loom.yaml` : il dit où chercher. `env_file`
désigne un autre fichier, qui l'emporte sur `env_file:` de la config et
sur le `.env` voisin. Une variable déjà définie n'est jamais remplacée.

**Arguments :**

- `config` : Chemin de `loom.yaml`.
- `env_file` : Fichier désigné par l'appelant (`--env-file`).
- `environ` : Environnement à compléter ; `os.environ` par défaut.

**Renvoie :**

Le rapport : fichier, source, variables posées et gardées.

**Lève :**

- `ConfigError` : Fichier désigné absent, illisible ou mal formé ; le message dit le fichier et la ligne, jamais une valeur.

## Fonction `loom_ia.config.env_file.reload_env_file`

```python
def reload_env_file(config: str | Path, previous: EnvFileReport, *, environ: MutableMapping[str, str] | None=None) -> EnvFileReport
```

Relit le fichier pour un process neuf de `loom serve --reload`.

Ce process hérite de l'environnement du superviseur, où `previous` a
posé ses variables : elles sont retirées, puis le fichier — peut-être
modifié, peut-être désigné autrement par la config — est relu. Ce que le
shell avait défini reste, comme à la première lecture.

**Arguments :**

- `config` : Chemin de `loom.yaml`.
- `previous` : Le rapport de la lecture précédente.
- `environ` : Environnement à compléter ; `os.environ` par défaut.

**Renvoie :**

Le rapport de la nouvelle lecture.

**Lève :**

- `ConfigError` : Comme `load_env_file`.

## Fonction `loom_ia.config.env_file.env_file_path`

```python
def env_file_path(config: str | Path, given: str | Path | None=None) -> tuple[Path | None, EnvSource]
```

Le fichier à lire et d'où il vient ; `None` sans loom.yaml à côté duquel chercher.

**Arguments :**

- `config` : Chemin de `loom.yaml`.
- `given` : Fichier désigné par l'appelant, qui l'emporte.

**Lève :**

- `ConfigError` : `env_file:` n'est pas un chemin.

## Fonction `loom_ia.config.env_file.declared_env_file`

```python
def declared_env_file(config: Path) -> Path | None
```

`env_file:` de `loom.yaml`, lu avant la config elle-même, en chemin absolu.

Lu à part parce que le fichier qu'il désigne peut donner `LOOM_PROFILE` :
il doit l'être avant que le profil ne soit choisi. Un `loom.yaml`
illisible rend `None` — `load_config` le dira, avec ses mots.

**Arguments :**

- `config` : Chemin de `loom.yaml`.

**Lève :**

- `ConfigError` : `env_file:` n'est pas un chemin non vide.

## Fonction `loom_ia.config.env_file._load`

```python
def _load(path: Path | None, source: EnvSource, replace: Iterable[str], environ: MutableMapping[str, str]) -> EnvFileReport
```

Retire `replace` de l'environnement, puis lit le fichier sans écraser une variable définie.

**Lève :**

- `ConfigError` : Fichier désigné absent (le `.env` par défaut peut manquer) ou mal formé.

## Fonction `loom_ia.config.env_file._read`

```python
def _read(path: Path) -> dict[str, str]
```

Les variables du fichier ; une ligne illisible est une erreur, qui dit son numéro.
