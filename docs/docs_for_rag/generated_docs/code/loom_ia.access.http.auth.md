# loom_ia.access.http.auth

Module `loom_ia.access.http.auth` de loom_v2 (fichier `src/loom_ia/access/http/auth.py`).

Clés d'API de l'accès REST (fonction N2, point 39).

La configuration ne garde que l'empreinte des clés (`sha256:…`) : la clé
elle-même n'existe qu'une fois, à sa création. Une instance sans clé déclarée
agit au nom de `server.http.user`, sur la boucle locale (décision D-1b des utilisateurs et rôles) ; sans l'un
ni l'autre, `create_app` refuse de la servir (décision D-1 des utilisateurs et rôles).

Permissions vérifiées : `run` pour lancer un run, `read` pour lire agents,
statuts, événements et rapports, `admin` pour lancer un run sans ses juges
(`judges: skip`, J3). Ce sont celles des rôles de l'utilisateur de la clé,
que la clé peut restreindre (`scopes`, `agents`, décision D-4 des utilisateurs et rôles) ; un refus dit
lequel des deux manque.

Client (fonction L1, point 34) : une clé agit **au nom du client de son utilisateur**, et
c'est la seule chose qui le dit en REST — rien dans le corps d'une requête ne
peut le changer. Toute lecture est donc bornée au client de la clé : on ne peut pas
demander le journal d'un autre, faute de façon de le nommer.

Débit (point 39, jalon J5.1b) : une clé peut porter un `rate_limit`. Il protège le
serveur, il ne dit rien du métier — c'est le quota du client qui le fait, et
lui vaut par tous les accès. Vérifié à l'identification, donc sur **toutes**
les routes REST et sur le MCP HTTP, dans une même fenêtre par clé, et répondu
par un 429 avec un `Retry-After`.

Expiration et contenu (jalon J5.2a) : une clé peut porter une date de fin — elle
est alors reconnue puis **refusée** (401), pour que le message dise « expirée »
et non « inconnue ». Et la permission `read_content` décide de ce qu'une lecture
montre : sans elle, les routes répondent, mais privées de ce qu'un utilisateur
a écrit et de ce qu'un modèle a répondu (conception §14.2). Ce qu'une clé **lance**, elle
le reçoit : c'est la relecture qui demande la permission.

## Constantes de `loom_ia.access.http.auth`

- `BEARER = 'bearer '` : Préfixe de l'en-tête `Authorization` qui porte une clé, en minuscules.
- `API_KEY_HEADER = 'x-api-key'` : En-tête qui porte la clé, à défaut d'`Authorization: Bearer`.

## Fonction `loom_ia.access.http.auth.presented`

```python
def presented(request: Request) -> str | None
```

Clé présentée par l'appelant, dans `Authorization` ou `X-API-Key`.

## Fonction `loom_ia.access.http.auth.refusal`

```python
def refusal(code: int, detail: str, *, caller: Caller | None=None, actor: Actor | None=None, key: str | None=None, access: Access='rest', permission: Permission | None=None, agent: str | None=None, call_id: str | None=None, headers: dict[str, str] | None=None) -> HTTPException
```

Le refus d'une requête, écrit au journal d'audit (décision D-9 des utilisateurs et rôles) : à lever par l'accès.

`actor` nomme l'utilisateur refusé quand ce n'est pas celui de la clé :
l'utilisateur qu'une passerelle endosse (`as_user`). La route, l'agent et
le run viennent de ce que l'accès a posé à l'entrée de la requête, sauf à
les nommer ici.

## Fonction `loom_ia.access.http.auth.identify`

```python
def identify(security: SecurityConfig, request: Request, local_user: str | None=None, *, access: Access='rest') -> Caller
```

Reconnaît l'appelant, ou refuse la requête.

Sans clé déclarée, `local_user` (`server.http.user`, décision D-1b des utilisateurs et rôles) est
l'appelant de toute requête : une instance locale qui sait au nom de qui
elle agit. `create_app` refuse une instance qui n'a ni l'un ni l'autre ;
appelé quand même, c'est un 401. `access` dit, au journal d'audit, par
où la requête est arrivée (REST, ou le MCP qu'il monte).

## Fonction `loom_ia.access.http.auth.throttle`

```python
def throttle(caller: Caller, window: RateWindow, *, access: Access='rest') -> None
```

Compte la requête de cette clé, ou refuse en 429 avec son `Retry-After`.

## Fonction `loom_ia.access.http.auth.require`

```python
def require(caller: Caller, permission: Permission, agent: str | None=None, *, call_id: str | None=None) -> None
```

Vérifie la permission demandée, et sur l'agent visé s'il y en a un.

Le refus dit ce qui manque — un rôle qui n'a pas la permission, ou une clé
qui la retire (`Caller.lacking`) — et la ligne d'audit, ce que la requête
visait : la route, l'agent et le run que l'accès a posés à son entrée
(`audit.Aim`), et `call_id`, l'appel que le corps désigne.
