# loom_ia.access.mcp_server.resources

Module `loom_ia.access.mcp_server.resources` de loom_v2 (fichier `src/loom_ia/access/mcp_server/resources.py`).

Ressources MCP : le journal en lecture seule (fonction N5, point 32).

Un outil fait travailler ; une ressource se lit. Ce module expose donc en
ressources ce que l'API REST rend par ses routes de lecture (jalon J5.4a) — les runs
d'un client, leurs traces, les sessions — plus les **octets** d'un fichier du
run, qui n'étaient jusqu'ici désignés que par leur URI.

Deux index se listent (`resources/list`) :

- `loom://runs` : les runs du client, du plus récemment écrit au plus ancien,
  c'est-à-dire la page de `Loom.runs()` ;
- `loom://sessions` : ses journaux de session, le plus récent d'abord.

Le reste se construit depuis un gabarit (`resources/templates/list`), faute
de pouvoir énumérer sans tout ouvrir :

- `loom://runs/{run_id}` : le résultat d'un run, comme `run_status` ;
- `loom://runs/{run_id}/events` : son journal, sous-runs compris ;
- `loom://traces/{run_id}` : sa trace — ses spans, sous-runs compris (jalon J6.2c) ;
- `loom://sessions/{session_id}` : la fiche d'une session ;
- `loom://sessions/{session_id}/events` : son journal entier ;
- `loom://artifacts/{client}/{session}/{fichier}` : les octets d'un fichier.

**Rien dans une URI ne nomme un client** : c'est la clé de la requête qui le
dit (point 34). L'URI d'un fichier porte bien un client, puisque celle du journal
le porte — il est alors *vérifié*, et un fichier d'un autre client est
« introuvable », comme un fichier absent : on n'apprend pas qu'il existe.

Portées (point 39, jalon J5.2a) : lire une ressource demande `read`, et sans
`read_content` le contenu est masqué exactement comme une relecture par
REST — ce sont les mêmes lectures. `loom://sessions` est refusé à une clé
limitée à certains agents, comme `GET /v1/sessions` : une liste de sessions
ne dit pas de quels agents sont leurs runs. `loom://runs`, elle, se filtre
honnêtement, chaque run nommant son agent.

Pas d'abonnement : le serveur déclare `subscribe: false`. Un client qui veut
le direct prend le flux SSE de REST ; MCP n'a que des notifications de
progression, et cette limite-là n'a pas changé.

Les octets d'un fichier sont bornés par `execution.attachments.max_bytes`,
faute d'une borne de lecture à part : un fichier plus gros n'est pas lisible
par ressource, et reste accessible par le stockage.

## Classe `loom_ia.access.mcp_server.resources.ResourceReader`

```python
class ResourceReader
```

Lit les ressources d'une instance, au nom d'un appelant.

Une instance par requête : l'appelant ne change pas en cours de lecture.

### Méthode `loom_ia.access.mcp_server.resources.ResourceReader.listed`

```python
def listed(self) -> list[types.Resource]
```

Les index lisibles par cet appelant, ou aucun s'il ne peut pas lire.

### Méthode `loom_ia.access.mcp_server.resources.ResourceReader.templates`

```python
def templates(self) -> list[types.ResourceTemplate]
```

Les gabarits, ou aucun si l'appelant ne peut pas lire.

### Méthode `loom_ia.access.mcp_server.resources.ResourceReader.read`

```python
async def read(self, raw: str) -> list[ReadResourceContents]
```

Contenu de la ressource, ou une erreur MCP qui dit ce qui manque.

### Méthode `loom_ia.access.mcp_server.resources.ResourceReader._run`

```python
async def _run(self, rest: str, session_id: SessionId | None) -> list[ReadResourceContents]
```

Le résultat d'un run (`loom://runs/{run_id}`), ou ses événements (`…/events`).

Le droit est vérifié sur chaque agent concerné ; le résultat est masqué
sans `read_content`.

### Méthode `loom_ia.access.mcp_server.resources.ResourceReader._trace`

```python
async def _trace(self, rest: str, session_id: SessionId | None) -> list[ReadResourceContents]
```

La trace d'un run (`loom://traces/{run_id}`), bâtie sans contenu sans `read_content` ; le droit vaut pour chaque agent de l'arbre.

### Méthode `loom_ia.access.mcp_server.resources.ResourceReader._session`

```python
async def _session(self, rest: str) -> list[ReadResourceContents]
```

La fiche d'une session (`loom://sessions/{session_id}`), ou ses événements (`…/events`), après le droit sur chacun de ses agents.

### Méthode `loom_ia.access.mcp_server.resources.ResourceReader._artifact`

```python
async def _artifact(self, uri: str) -> list[ReadResourceContents]
```

Les octets d'un fichier rangé par loom (`artifact://…`) : du même client, avec `read_content`, et lisible comme la session qui le porte.

### Méthode `loom_ia.access.mcp_server.resources.ResourceReader._whole`

```python
def _whole(self) -> bool
```

Vrai si la clé n'est pas limitée à certains agents.

### Méthode `loom_ia.access.mcp_server.resources.ResourceReader._denied`

```python
def _denied(self, message: str, permission: Permission, agent: str | None=None) -> McpError
```

Un refus d'accès : écrit au journal d'audit (décision D-9 des utilisateurs et rôles), puis rendu en erreur MCP.

### Méthode `loom_ia.access.mcp_server.resources.ResourceReader._shown`

```python
def _shown(self, events: Sequence[Event]) -> list[dict[str, Any]]
```

Événements en JSON, privés de leur contenu sans `read_content`.

## Fonction `loom_ia.access.mcp_server.resources._split`

```python
def _split(uri: str) -> tuple[str, SessionId | None]
```

L'URI sans sa requête, et la session qu'elle nomme s'il y en a une.

## Fonction `loom_ia.access.mcp_server.resources._media`

```python
def _media(name: str) -> str
```

Type d'un fichier, deviné sur son extension.
