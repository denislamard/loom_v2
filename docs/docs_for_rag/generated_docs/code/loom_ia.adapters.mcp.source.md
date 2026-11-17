# loom_ia.adapters.mcp.source

Module `loom_ia.adapters.mcp.source` de loom_v2 (fichier `src/loom_ia/adapters/mcp/source.py`).

Un serveur MCP référencé par un agent : source d'outils (point 19, fonction D3).

La référence choisit les outils exposés (`include` ou `exclude`), leur
préfixe (`serveur__outil`, ou `alias__outil`) et leurs déclarations :
annotations du serveur, puis `mcp_servers[].tools`, puis la référence de
l'agent, de la plus faible à la plus forte.

Un outil irréversible — annoncé destructeur, ou sans annotation, ce que la
spec MCP tient pour destructeur — qu'aucune déclaration ne soumet à une
approbation est exécuté sans accord humain : c'est dit une fois, au premier
listage des outils, avec ce qu'il faut écrire dans la config.

Un outil dont le nom préfixé ne respecte pas le format des API (lettres,
chiffres, `_`, `-`, 64 caractères au plus) est écarté avec un
avertissement. La clé d'idempotence de l'appel part dans `_meta`.

## Constantes de `loom_ia.adapters.mcp.source`

- `IDEMPOTENCY_META = 'loom-ia/idempotency_key'` : Clé de `_meta` qui porte la clé d'idempotence d'un appel (conception §9.5).
- `POOLED = frozenset({'shared', 'tenant'})` : Portées dont la connexion vit dans le pool, au-delà d'un run.
- `LOST = "Connexion au serveur perdue pendant l'appel : l'outil a peut-être produit son effet. Il n'a pas été relancé automatiqu…` : Erreur rendue au modèle quand la connexion tombe pendant un appel qui ne peut pas être relancé sans risque.

## Classe `loom_ia.adapters.mcp.source.McpSelection`

```python
@dataclass(frozen=True, kw_only=True)
class McpSelection
```

Ce qu'un agent prend d'un serveur.

**Attributs :**

- `prefix` : Préfixe des noms d'outils exposés à l'agent.
- `include` : Seuls ces outils du serveur ; `None` : tous.
- `exclude` : Outils du serveur écartés.
- `required` : Le run ne peut pas se passer de ce serveur.
- `tools` : Déclarations propres à l'agent, par nom d'outil du serveur.

## Classe `loom_ia.adapters.mcp.source.McpTool`

```python
@dataclass(frozen=True, slots=True)
class McpTool
```

Outil d'un serveur MCP, sous son nom préfixé.

**Attributs :**

- `spec` : Définition de l'outil, sous son nom préfixé.
- `server` : Le serveur qui l'exécute.
- `remote_name` : Nom de l'outil chez le serveur.

### Méthode `loom_ia.adapters.mcp.source.McpTool.invoke`

```python
async def invoke(self, arguments: dict[str, JsonValue], context: ToolContext) -> ToolOutput
```

Appelle l'outil chez le serveur, avec la clé d'idempotence de l'appel dans `_meta`.

Une perte de connexion est rejouée une fois si l'outil peut l'être sans
risque (`safe_to_retry`).

**Lève :**

- `ToolError` : Serveur indisponible, connexion perdue pendant l'appel, ou erreur MCP ; le modèle en lit le message.

## Fonction `loom_ia.adapters.mcp.source.unguarded_spec`

```python
def unguarded_spec(spec: ToolSpec) -> bool
```

Vrai si l'outil est irréversible et qu'aucune déclaration n'en fait approuver les appels.

L'approbation qu'un client impose (`tenants[].approvals`) n'est pas vue ici :
elle se décide au run.

## Classe `loom_ia.adapters.mcp.source.McpSource`

```python
class McpSource
```

Source d'outils : un serveur, tel qu'un agent le référence.

### Méthode `loom_ia.adapters.mcp.source.McpSource.__init__`

```python
def __init__(self, spec: McpServerSpec, selection: McpSelection, *, factory: SessionFactory, pool: McpPool | None=None, pool_key: str | None=None) -> None
```

Prépare la source, sans se connecter.

**Arguments :**

- `spec` : Le serveur déclaré.
- `selection` : Ce que l'agent en prend.
- `factory` : Ouvre une session selon le transport.
- `pool` : Pool des connexions partagées ; requis en portée `shared` ou `tenant`.
- `pool_key` : Clé de la connexion dans le pool : le nom du serveur en portée `shared`, son nom et son client en portée `tenant`.

**Lève :**

- `ValueError` : Portée partagée sans pool.

### Méthode `loom_ia.adapters.mcp.source.McpSource.name`

```python
@property
def name(self) -> str
```

Nom du serveur, repris dans le journal.

### Méthode `loom_ia.adapters.mcp.source.McpSource.required`

```python
@property
def required(self) -> bool
```

Vrai si le run ne peut pas se passer de ce serveur (`required`).

### Méthode `loom_ia.adapters.mcp.source.McpSource.open`

```python
@asynccontextmanager
async def open(self, context: SourceContext) -> AsyncGenerator[Sequence[Tool]]
```

Ouvre les outils du serveur pour un run.

En portée `shared` ou `tenant`, la connexion vient du pool et lui
survit ; sinon elle est ouverte pour le run et fermée à la sortie.

**Arguments :**

- `context` : Le contexte du run.

**Produit :**

Les outils retenus, sous leur nom préfixé.

### Méthode `loom_ia.adapters.mcp.source.McpSource.select`

```python
def select(self, server: McpServer, listed: Sequence[types.Tool]) -> list[Tool]
```

Outils exposés à l'agent, sous leur nom préfixé et avec leurs déclarations.

### Méthode `loom_ia.adapters.mcp.source.McpSource._warn_unguarded`

```python
def _warn_unguarded(self, tools: Sequence[McpTool]) -> None
```

Les outils irréversibles sans approbation : le dire, avec la ligne de config.

### Méthode `loom_ia.adapters.mcp.source.McpSource._spec`

```python
def _spec(self, tool: types.Tool) -> ToolSpec | None
```

La définition d'un outil du serveur ; `None` pour un nom hors du format des API.

Nom préfixé, description, schéma, effets lus dans ses annotations, puis
les déclarations du serveur et de l'agent.

### Méthode `loom_ia.adapters.mcp.source.McpSource._warn_unknown`

```python
def _warn_unknown(self, names: set[str]) -> None
```

Signale les outils cités par `include`, `exclude` ou `tools` que le serveur ne connaît pas.
