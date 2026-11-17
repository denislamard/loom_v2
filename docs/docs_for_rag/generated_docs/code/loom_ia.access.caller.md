# loom_ia.access.caller

Module `loom_ia.access.caller` de loom_v2 (fichier `src/loom_ia/access/caller.py`).

Au nom de qui une requête agit : sa clé, son utilisateur, ce qu'elle peut (point 39, décision D-4 des utilisateurs et rôles).

Deux accès portent une clé d'API — REST depuis J1, et le serveur MCP en HTTP
depuis J5.2b. Ils partagent donc cette description de l'appelant, et avec elle
les mêmes règles : le client vient de l'**utilisateur de la clé** et de nulle
part ailleurs, une permission manquante refuse, et sans `read_content` une
relecture est masquée.

Une clé identifie un utilisateur déclaré (décision D-4 des utilisateurs et rôles) : son `actor`, dont les
permissions sont celles de ses rôles d'accès, que les `scopes` et les
`agents` de la clé restreignent. Les actions (lancer, trancher, répondre,
arrêter) passent cet acteur au SDK, qui applique toute la matrice
(`access/authz.py`) ; `may` et `allows` ne servent qu'aux contrôles de
forme d'un accès — refuser tôt, filtrer une liste.

Sans clé, l'acteur est l'utilisateur que l'instance désigne —
`server.http.user` pour une instance locale (décision D-1b des utilisateurs et rôles), `loom mcp --user` en
stdio — et ses droits valent comme partout. Il n'y a plus d'appelant anonyme
(décision D-1 des utilisateurs et rôles) : une instance qui ne sait pas au nom de qui elle agit ne démarre pas.

## Classe `loom_ia.access.caller.Caller`

```python
@dataclass(frozen=True, slots=True)
class Caller
```

Une clé reconnue et son utilisateur, ou l'utilisateur que l'instance désigne.

**Attributs :**

- `actor` : L'utilisateur au nom duquel la requête agit : celui de la clé, ou celui que l'instance désigne sans clé.
- `key` : La clé reconnue ; `None` sans clé.

### Méthode `loom_ia.access.caller.Caller.identified`

```python
@classmethod
def identified(cls, key: ApiKey, security: SecurityConfig) -> Self
```

L'appelant d'une clé reconnue : son utilisateur, avec la clé qui le restreint.

La config garantit que l'utilisateur de la clé est déclaré
(`LoomConfig.check`).

### Méthode `loom_ia.access.caller.Caller.acting`

```python
@classmethod
def acting(cls, security: SecurityConfig, user: str) -> Self
```

L'appelant sans clé d'une instance qui désigne son utilisateur (décision D-1b des utilisateurs et rôles, stdio).

### Méthode `loom_ia.access.caller.Caller.user`

```python
@property
def user(self) -> str
```

Identifiant de l'utilisateur au nom duquel la requête agit.

### Méthode `loom_ia.access.caller.Caller.tenant`

```python
@property
def tenant(self) -> TenantId
```

Client au nom duquel cet appelant agit : celui de son utilisateur.

### Méthode `loom_ia.access.caller.Caller.granted`

```python
@property
def granted(self) -> frozenset[Permission]
```

Permissions de l'utilisateur, tous agents confondus, restreintes par la clé (décision D-4 des utilisateurs et rôles).

### Méthode `loom_ia.access.caller.Caller.may`

```python
def may(self, permission: Permission) -> bool
```

L'un des rôles porte la permission, et la clé la laisse.

### Méthode `loom_ia.access.caller.Caller.allows`

```python
def allows(self, agent: str, permission: Permission='read') -> bool
```

La clé laisse cet agent, et l'un des rôles y a la permission.

Les listes `access` d'un agent ne comptent pas ici : elles ne bornent
que `run`, `answer` et `cancel`, que le SDK vérifie en agissant.

### Méthode `loom_ia.access.caller.Caller.lacking`

```python
def lacking(self, permission: Permission, agent: str | None=None) -> str
```

Pourquoi cet appelant n'a pas `permission` (sur `agent`) : ses rôles, ou sa clé.

Le message d'un refus de `may` ou d'`allows`, dans les mots du SDK
(`access/authz.py`) : un rôle qui n'a pas le droit et une clé qui le
retire ne se corrigent pas au même endroit.

### Méthode `loom_ia.access.caller.Caller.masks`

```python
@property
def masks(self) -> bool
```

Vrai si les lectures de cet appelant doivent être privées de contenu.
