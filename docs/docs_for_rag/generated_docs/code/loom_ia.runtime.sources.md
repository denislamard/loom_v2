# loom_ia.runtime.sources

Module `loom_ia.runtime.sources` de loom_v2 (fichier `src/loom_ia/runtime/sources.py`).

Sources d'outils fournies par des paquets installés : le groupe `loom_ia.tools` (jalon J6.4b, fonction D12).

Un paquet déclare dans ses métadonnées un point d'entrée qui désigne une
fabrique (`ToolSourceFactory`) :

    [project.entry-points."loom_ia.tools"]
    carnet = "carnet_devis:fabrique"

La config la déclare dans `tool_sources` (`name`, `entry_point`,
`params`) et un agent la référence (`tools: [{source: carnet}]`).

**Rien n'est importé sans être demandé** : la liste des points d'entrée se lit
dans les métadonnées des paquets installés, sans importer aucun d'eux ; seul
le paquet d'une source qu'un agent référence est importé, au montage de cet
agent. Deux paquets qui déclarent le même nom sont refusés, chacun nommé.

La source rendue par la fabrique fournit ses outils sous leur nom court ;
`PackagedSource` les préfixe, les choisit et leur applique les
déclarations de la config, comme `McpSource` le fait pour un serveur.

## Constantes de `loom_ia.runtime.sources`

- `GROUP = 'loom_ia.tools'` : Le groupe des points d'entrée que loom lit.

## Classe `loom_ia.runtime.sources.Installed`

```python
@dataclass(frozen=True, slots=True)
class Installed
```

Un point d'entrée installé, lu dans les métadonnées de son paquet sans l'importer.

**Attributs :**

- `name` : Nom du point d'entrée, celui que cite `entry_point`.
- `value` : Ce qu'il désigne : `module:attribut`.
- `package` : Paquet qui le déclare ; vide si les métadonnées ne le disent pas.
- `version` : Version du paquet ; vide si inconnue.

## Fonction `loom_ia.runtime.sources.installed`

```python
def installed() -> list[Installed]
```

Les points d'entrée `loom_ia.tools` des paquets installés, par nom puis paquet.

## Fonction `loom_ia.runtime.sources.source_factory`

```python
def source_factory(entry_point: str) -> ToolSourceFactory
```

La fabrique qu'un point d'entrée désigne ; son paquet est importé à ce moment-là.

`ConfigError` s'il n'est pas installé, si deux paquets le déclarent, si
son paquet ne s'importe pas ou s'il ne désigne rien d'appelable.

**Arguments :**

- `entry_point` : Nom du point d'entrée (`tool_sources[].entry_point`).

**Renvoie :**

La fabrique, qui reçoit les `params` de la source et rend une
`ToolSource`.

**Lève :**

- `ConfigError` : Point d'entrée absent ou en double, paquet qui ne s'importe pas, cible non appelable.

## Fonction `loom_ia.runtime.sources.described`

```python
def described(entry_point: str) -> str
```

Le paquet et la cible d'un point d'entrée installé, pour un message ; vide sinon.

## Classe `loom_ia.runtime.sources.PackagedSource`

```python
class PackagedSource
```

Une source d'un paquet, telle qu'un agent la référence : préfixe, choix, déclarations.

Un outil de paquet est traité comme un outil Python (`kind: python`) ;
ce que la config fixe l'emporte sur l'évaluation par appel de l'outil.
Un nom cité dans `include`, `exclude` ou `tools` qui n'existe pas dans
la source donne un avertissement, une fois.

**Attributs :**

- `inner` : La source rendue par la fabrique du paquet.
- `name` : Nom de la source dans la config, repris au journal.
- `prefix` : Préfixe des outils (`alias`, sinon `name`).
- `include` : Outils exposés, ou `None` pour tous.
- `exclude` : Outils écartés.
- `required` : Vrai si le run échoue sans cette source.
- `declared` : Déclarations de `tool_sources[].tools`, par nom court.
- `chosen` : Déclarations de la référence de l'agent, appliquées par-dessus.

### Méthode `loom_ia.runtime.sources.PackagedSource.__init__`

```python
def __init__(self, inner: ToolSource, *, name: str, prefix: str, include: tuple[str, ...] | None=None, exclude: tuple[str, ...] | None=None, required: bool=False, declared: Mapping[str, ToolOverrides] | None=None, chosen: Mapping[str, ToolOverrides] | None=None) -> None
```

Enrobe la source du paquet ; les arguments sont décrits dans la docstring de la classe.

### Méthode `loom_ia.runtime.sources.PackagedSource.open`

```python
@asynccontextmanager
async def open(self, context: SourceContext) -> AsyncGenerator[Sequence[Tool]]
```

Ouvre la source du paquet pour un run.

**Arguments :**

- `context` : Contexte de l'ouverture (run, client, session).

**Produit :**

Les outils exposés à l'agent (voir `select`).

### Méthode `loom_ia.runtime.sources.PackagedSource.select`

```python
def select(self, provided: Sequence[Tool]) -> list[Tool]
```

Les outils exposés à l'agent, sous leur nom préfixé et avec leurs déclarations.

Un outil dont le nom préfixé sort du format des API (lettres, chiffres,
`_` et `-`, 64 au plus) est écarté avec un avertissement.

**Arguments :**

- `provided` : Les outils de la source, sous leur nom court.

**Renvoie :**

Les outils retenus, nommés `<préfixe>__<nom>`.

### Méthode `loom_ia.runtime.sources.PackagedSource._warn_unknown`

```python
def _warn_unknown(self, names: set[str]) -> None
```

Avertit pour chaque nom cité par la config (`include`, `exclude`, `tools`) que la source n'a pas.

### Méthode `loom_ia.runtime.sources.PackagedSource.aclose`

```python
async def aclose(self) -> None
```

Ferme la source du paquet si elle a de quoi l'être.
