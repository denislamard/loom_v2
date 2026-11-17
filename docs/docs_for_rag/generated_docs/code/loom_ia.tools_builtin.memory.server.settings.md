# loom_ia.tools_builtin.memory.server.settings

Module `loom_ia.tools_builtin.memory.server.settings` de loom_v2 (fichier `src/loom_ia/tools_builtin/memory/server/settings.py`).

Les réglages de `loom-mem-gpu` : options, variables `LOOM_MEM_GPU_*`, YAML facultatif.

Les mêmes clés partout (`port` ↔ `--port` ↔ `LOOM_MEM_GPU_PORT`). Une option
donnée l'emporte sur la variable, la variable sur le fichier, le fichier sur le
défaut ; une variable vide compte comme absente. Une variable `LOOM_MEM_GPU_*`
inconnue (une faute de frappe) est refusée, sauf celle que nomme `token_env`.
Les réglages des modèles sont validés par `ModelsSpec`, comme dans loom, à
une différence près : le device vaut `cuda` par défaut. Une application
« gpu » qui retomberait en silence sur le CPU serait un piège ; `cpu` et
`auto` restent permis, à demander.

L'application ne lit jamais `loom.yaml` : ses réglages sont les siens.

## Constantes de `loom_ia.tools_builtin.memory.server.settings`

- `DEFAULT_HOST = '127.0.0.1'` : L'adresse d'écoute par défaut : la boucle locale.
- `DEFAULT_PORT = 8765` : Le port d'écoute par défaut.
- `ENV_PREFIX = 'LOOM_MEM_GPU_'` : Préfixe des variables d'environnement des réglages.
- `LogLevel = Literal['DEBUG', 'INFO', 'WARNING', 'ERROR']` : Les niveaux de journal permis.
- `LOG_LEVELS = get_args(LogLevel)` : Les mêmes niveaux, en tuple.
- `_EXAMPLES = {'token_env': 'LOOM_MEM_GPU_TOKEN', 'max_vram_fraction': '0.3'}` : Les réglages sans défaut, écrits en commentaire dans l'unité, avec cette valeur d'exemple.

## Classe `loom_ia.tools_builtin.memory.server.settings.SettingsError`

```python
class SettingsError(Exception)
```

Réglages refusés : le message dit lequel et pourquoi.

## Classe `loom_ia.tools_builtin.memory.server.settings.ServiceSettings`

```python
class ServiceSettings(BaseModel)
```

Ce que l'application sert, où, et avec quelles limites.

Chaque `description` sert deux fois : l'aide de l'option, le commentaire
de la variable dans l'unité systemd (`as_environment`).

**Attributs :**

- `host` : Adresse d'écoute : la boucle locale seulement.
- `port` : Port d'écoute.
- `device` : Périphérique de calcul. Le seul défaut qui diffère de `models` : `cuda`, pas `auto`.
- `fp16` : Demi-précision sur la carte.
- `offline` : Modèles lus dans le cache local de Hugging Face seulement, sans réseau.
- `embedder` : Le modèle de la famille BGE-M3.
- `reranker` : Le reranker.
- `token_env` : Nom de la variable qui porte le jeton exigé des clients. Jamais le jeton lui-même : il serait visible dans `ps` et `systemctl show`.
- `max_vram_fraction` : Part de la carte permise, à côté d'un LLM local.
- `log_level` : Niveau du journal.

### Méthode `loom_ia.tools_builtin.memory.server.settings.ServiceSettings.models`

```python
def models(self) -> ModelsSpec
```

Les réglages des modèles, tels que `BgeM3Embedder` et `BgeReranker` les lisent.

### Méthode `loom_ia.tools_builtin.memory.server.settings.ServiceSettings.token`

```python
def token(self, environ: Mapping[str, str] | None=None) -> str | None
```

Le jeton lu dans `token_env` ; `SettingsError` si la variable est vide.

## Fonction `loom_ia.tools_builtin.memory.server.settings.describe`

```python
def describe(field: str) -> str
```

Ce que fait un réglage, en une ligne : l'aide de l'option, le commentaire de l'unité.

## Fonction `loom_ia.tools_builtin.memory.server.settings.env_name`

```python
def env_name(field: str) -> str
```

La variable d'un réglage : `max_vram_fraction` → `LOOM_MEM_GPU_MAX_VRAM_FRACTION`.

## Fonction `loom_ia.tools_builtin.memory.server.settings.load_settings`

```python
def load_settings(config: Path | None, options: Mapping[str, Any], environ: Mapping[str, str] | None=None) -> ServiceSettings
```

Les réglages : le fichier `config`, puis les variables, puis les options données.

`options` ne porte que ce qui a été donné sur la ligne de commande ;
`environ` vaut `{}` par défaut (aucune variable lue).

## Fonction `loom_ia.tools_builtin.memory.server.settings.as_environment`

```python
def as_environment(settings: ServiceSettings) -> list[str]
```

Les réglages en lignes `Environment=` d'une unité systemd, une par variable.

Chaque variable est précédée d'un commentaire qui dit ce qu'elle fait. Un réglage
sans valeur est écrit en commentaire, avec une valeur d'exemple. Les valeurs sont
échappées pour systemd (`%` doublé ; guillemets si espace).

## Fonction `loom_ia.tools_builtin.memory.server.settings._from_file`

```python
def _from_file(config: Path | None) -> dict[str, Any]
```

Les réglages du fichier YAML `--config` ; aucun sans fichier.

**Lève :**

- `SettingsError` : Fichier illisible, YAML invalide, ou pas un objet.
