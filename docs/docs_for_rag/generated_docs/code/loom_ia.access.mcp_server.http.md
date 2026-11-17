# loom_ia.access.mcp_server.http

Module `loom_ia.access.mcp_server.http` de loom_v2 (fichier `src/loom_ia/access/mcp_server/http.py`).

Serveur MCP en HTTP, monté dans l'application REST (fonction N5, point 39, jalon J5.2b).

Ce module demande les deux extras, `http` et `mcp` : il monte le serveur
MCP dans l'application REST. `loom_ia.access.mcp_server` ne l'importe donc
pas — le stdio se contente de l'extra `mcp`.

Ce que le transport HTTP change, et c'est l'essentiel : **la clé arrive à
chaque requête**. En stdio, rien dans le protocole ne dit au nom de qui on
parle, donc un serveur sert un client, choisi à son lancement. Ici, un seul
serveur sert tous les clients — il publie à chacun les agents que sa clé lui
ouvre, et refuse ce qu'elle ne permet pas.

L'authentification a lieu **avant** le protocole, dans une couche ASGI : une
clé absente, inconnue ou expirée reçoit un 401 ordinaire, avec son
`WWW-Authenticate`, plutôt qu'une erreur d'outil que le LLM du client
essaierait d'interpréter. L'appelant reconnu est posé dans une variable de
contexte, où les gestionnaires du serveur le relisent.

Protection du transport (spec MCP) : le SDK valide `Origin` et `Host`
contre le rebinding DNS. Une requête **sans** `Origin` passe — un client
natif n'en envoie pas ; avec un `Origin`, il doit être déclaré. Le `Host`,
lui, doit toujours figurer dans la liste : loom y met son adresse d'écoute,
et `server.mcp.allowed_hosts` l'étend.

Le serveur est **sans état** (`stateless=True`) : chaque requête a sa propre
session. Conséquence : l'elicitation n'y marche jamais — la session ne connaît
pas les capacités du client, et la réponse à un formulaire n'arriverait pas par
la requête qui l'attend. Un run qui demande une approbation se met en pause,
et se tranche par REST ou `loom approve` (voir `server.py`).

## Constantes de `loom_ia.access.mcp_server.http`

- `MCP_PATH = '/mcp'` : Chemin du serveur MCP sous le préfixe commun de l'application.
- `LOCAL_HOSTS = ('127.0.0.1:*', 'localhost:*', '[::1]:*', '127.0.0.1', 'localhost')` : Hôtes toujours acceptés : l'instance servie depuis la machine même.

## Fonction `loom_ia.access.mcp_server.http.current_caller`

```python
def current_caller() -> Caller
```

Appelant de la requête MCP en cours, posé par la couche ASGI.

## Fonction `loom_ia.access.mcp_server.http.allowed_hosts`

```python
def allowed_hosts(mcp: McpAccess, http: HttpServer) -> list[str]
```

Hôtes acceptés : l'adresse d'écoute, plus ce que la config ajoute.

Une liste vide ferait tout refuser par le SDK : loom la remplit donc
lui-même, pour qu'un essai local marche sans rien régler.

## Fonction `loom_ia.access.mcp_server.http.transport_security`

```python
def transport_security(mcp: McpAccess, http: HttpServer) -> TransportSecuritySettings
```

Protection du transport contre le rebond DNS : hôtes et origines acceptés.

## Classe `loom_ia.access.mcp_server.http.McpHttp`

```python
class McpHttp
```

Le serveur MCP d'une instance, servi en HTTP sous `<base_path>/mcp`.

**Attributs :**

- `path` : Chemin de montage, sous le préfixe commun de l'application.

### Méthode `loom_ia.access.mcp_server.http.McpHttp.running`

```python
@asynccontextmanager
async def running(self) -> AsyncGenerator[None]
```

Cycle de vie du gestionnaire de session, accroché à celui de l'app.

### Méthode `loom_ia.access.mcp_server.http.McpHttp.__call__`

```python
async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None
```

Sert une requête MCP : clé identifiée et débit contrôlé d'abord, puis le gestionnaire de session, au nom de l'appelant.

Un refus d'authentification ou de débit est un 401 ou un 429 ordinaire,
pas une erreur d'outil.
