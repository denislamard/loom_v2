# loom_ia.adapters.mcp.pool

Module `loom_ia.adapters.mcp.pool` de loom_v2 (fichier `src/loom_ia/adapters/mcp/pool.py`).

Connexions partagées entre les runs : une par clé de pool (points 19 et 34).

La clé vaut le nom du serveur en portée `shared` — une connexion pour tout
le process — et le nom du serveur **et du client** en portée `tenant` : le
serveur est le même, ses identifiants ne le sont pas, et deux clients ne
doivent jamais se retrouver sur la même session MCP.

## Classe `loom_ia.adapters.mcp.pool.McpPool`

```python
class McpPool
```

Serveurs partagés par les runs d'une instance, sous leur clé de pool.

### Méthode `loom_ia.adapters.mcp.pool.McpPool.server`

```python
def server(self, spec: McpServerSpec, key: str | None=None, factory: SessionFactory | None=None) -> McpServer
```

Le serveur partagé de `spec` sous cette clé ; créé au premier appel.

`factory` est celle de l'appelant : en portée `tenant`, elle porte
les identifiants de son client, que le pool ne connaît pas.

### Méthode `loom_ia.adapters.mcp.pool.McpPool.aclose`

```python
async def aclose(self) -> None
```

Ferme toutes les connexions du pool ; la première erreur remonte une fois toutes tentées.
