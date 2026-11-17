# loom_ia.agents.registry

Module `loom_ia.agents.registry` de loom_v2 (fichier `src/loom_ia/agents/registry.py`).

Registre des agents d'une instance (fonction A9).

Une instance héberge plusieurs agents nommés ; chaque point d'accès (Python,
REST, MCP) y puise les agents qu'il publie.

## Constantes de `loom_ia.agents.registry`

- `Access = Literal['rest', 'mcp']` : Point d'accès qui publie des agents.

## Classe `loom_ia.agents.registry.UnknownAgent`

```python
class UnknownAgent(Unknown)
```

Aucun agent de ce nom dans le registre.

Le message liste les agents déclarés, sans les agents internes.

**Arguments :**

- `name` : Le nom demandé.
- `known` : Les noms à proposer.

**Attributs :**

- `name` : Le nom demandé.

## Classe `loom_ia.agents.registry.AgentRegistry`

```python
class AgentRegistry
```

Les agents d'une instance, par nom.

**Arguments :**

- `agents` : Les agents, dont les agents internes (`_compaction`).

**Lève :**

- `ValueError` : Deux agents du même nom.

### Méthode `loom_ia.agents.registry.AgentRegistry.from_config`

```python
@classmethod
def from_config(cls, config: LoomConfig) -> Self
```

Registre des agents d'une configuration, agents internes compris.

**Arguments :**

- `config` : La configuration.

### Méthode `loom_ia.agents.registry.AgentRegistry.get`

```python
def get(self, name: str) -> AgentSpec
```

L'agent de ce nom.

**Arguments :**

- `name` : Le nom.

**Lève :**

- `UnknownAgent` : Aucun agent de ce nom ; le message propose les agents déclarés, sans les agents internes.

### Méthode `loom_ia.agents.registry.AgentRegistry.exposed`

```python
def exposed(self, access: Access) -> tuple[AgentSpec, ...]
```

Agents publiés par un point d'accès (`expose.rest` ou `expose.mcp`).

**Arguments :**

- `access` : `rest` ou `mcp`.

### Méthode `loom_ia.agents.registry.AgentRegistry.names`

```python
@property
def names(self) -> tuple[str, ...]
```

Noms des agents, dans l'ordre de déclaration, agents internes compris.
