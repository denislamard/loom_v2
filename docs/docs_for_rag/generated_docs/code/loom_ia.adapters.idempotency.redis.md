# loom_ia.adapters.idempotency.redis

Module `loom_ia.adapters.idempotency.redis` de loom_v2 (fichier `src/loom_ia/adapters/idempotency/redis.py`).

Magasin d'idempotence dans Redis : partagé, rapide, oublieux (points 18 et 49).

Même contrat que les magasins SQL, avec une différence qu'il faut avoir en
tête : **Redis oublie tout seul**. Une clé porte une durée de vie, et passée
la rétention elle disparaît — y compris une réservation périmée, que SQLite et
Postgres gardent indéfiniment comme la trace d'un effet d'état inconnu. Au
bout d'un jour (la rétention par défaut), cet effet redevient donc rejouable.
C'est le prix d'un magasin qui ne grossit jamais tout seul, et il est assumé :
une clé métier sert à ne pas renvoyer deux fois le même e-mail dans la
journée, pas à s'en souvenir un an.

`reserve` tient en un **script Lua**, exécuté d'un bloc par Redis : lire la
clé, la reprendre si sa date est passée, ne rien faire sinon. C'est le même
arbitrage que le `ON CONFLICT … WHERE` des magasins SQL — ce qui protège,
c'est l'atomicité, pas le `get` qui précède.

Ce qu'une clé appartient (client, session) est tenu à part, dans un ensemble :
Redis ne sait pas chercher, et l'oubli RGPD doit pouvoir nommer les clés d'une
session sans balayer la base. L'ensemble vit **au moins aussi longtemps que la
plus longue de ses clés** : `reserve` et `complete` ne font que le
rallonger, jamais le raccourcir, faute de quoi `forget` ne retrouverait plus
un enregistrement qui lui survit. L'enregistrement porte donc le nom de son
ensemble (`owner`), que `complete` y relit : il n'a pas la session.

Le jeton du détenteur (`holder`) est écrit dans l'enregistrement, et
`complete` comme `release` le contrôlent **dans** leur script : lire la
clé puis la modifier hors de Redis laisserait passer un autre détenteur entre
les deux, ce qu'on veut justement exclure. Un enregistrement sans jeton (écrit
avant lui, ou sans) ne répond à aucun jeton.

Le nom de l'ensemble porte le client et la session **encodés** (encodage
« pourcent » d'URL) : ni `:` ni caractère de motif (`*`, `?`, `[`) n'y
restent. Sans cela, `forget` du client `a` emportait aussi les clés du
client `a:b` — et celles de tous les clients pour `*` —, et la session
`b:s` du client `a` se confondait avec la session `s` du client `a:b`.
Un identifiant fait de lettres, de chiffres et de `_.-~` s'écrit tel quel :
les ensembles existants de ces identifiants ne changent pas de nom. Ceux
d'identifiants à caractères spéciaux, écrits avant, ne sont plus retrouvés par
`forget` ; ils expirent avec leurs clés (la rétention).

`forget` vide chaque ensemble d'un bloc (script Lua) et ne supprime que les
clés qui lui appartiennent encore : une clé expirée, puis reprise par une autre
session, reste listée dans l'ancien ensemble, mais son `owner` dit à qui elle
est.

Un Redis qui ne répond plus fait échouer l'appel au bout de `SOCKET_TIMEOUT`
au lieu de le suspendre. **Redis Cluster n'est pas pris en charge** : les
scripts touchent l'enregistrement et son ensemble, qui n'ont pas de raison
d'être dans le même emplacement (`CROSSSLOT`).

Le SDK `redis` laisse des `Unknown` dans ses signatures asynchrones (des
`**kwargs` non typés) : les trois règles pyright concernées sont levées pour
ce module, comme pour le bus Redis.

## Constantes de `loom_ia.adapters.idempotency.redis`

- `RECORD = 'loom:idem:k:'` : Préfixe des clés d'enregistrement.
- `OWNER = 'loom:idem:o:'` : Préfixe des ensembles d'appartenance : les clés d'une session d'un client.
- `SOCKET_TIMEOUT = 5.0` : Délai d'une connexion et d'une réponse de Redis, en secondes.
- `_RESERVE = "\nlocal raw = redis.call('GET', KEYS[1])\nif raw then\n local held = cjson.decode(raw)\n if tonumber(held.expires_at) …` : Prendre la clé, ou la reprendre si sa date est passée. Rend 1 si elle est à nous, 0 si quelqu'un la tient encore. L'ensemble d'appartenance survit à la clé : sa durée de vie ne fait que croître (PTTL, pas `PEXPIRE … GT`, qui exige Redis 7), pour qu'une clé plus courte ne raccourcisse pas la sienne.
- `_COMPLETE = '\nlocal raw = redis.call(\'GET\', KEYS[1])\nif not raw then\n return 0\nend\nlocal held = cjson.decode(raw)\nif ARGV[3…` : Enregistrer ce que l'effet a rendu : seulement si la clé est bien tenue. Rend 1 si c'est fait, 0 si la clé n'existe plus, 2 si elle est à un autre détenteur que celui dont le jeton est donné (ARGV[3], absent sans jeton). L'ensemble d'appartenance que la réservation a nommé (`owner`) est rallongé si le résultat vit plus longtemps que lui ; l'enregistrement garde ce nom, collé au JSON tel quel (le recoder abîmerait le résultat : `[]` deviendrait `{}`). Une réservation d'avant `owner` n'a rien à rallonger, comme avant.
- `_RELEASE = "\nlocal raw = redis.call('GET', KEYS[1])\nif not raw then\n return 0\nend\nlocal held = cjson.decode(raw)\nif held.sta…` : Rendre une réservation dont l'effet ne s'est pas produit : jamais un résultat, et, avec un jeton (ARGV[1], absent sans jeton), seulement si elle est encore à lui.
- `_FORGET = "\nlocal removed = 0\nfor _, key in ipairs(redis.call('SMEMBERS', KEYS[1])) do\n local raw = redis.call('GET', key)\n i…` : Oublier un ensemble d'appartenance (KEYS[1]) d'un bloc, avec les clés qui lui appartiennent encore. Une clé sans `owner` (écrite avant lui) est à l'ensemble qui la liste ; une clé illisible aussi : l'oubli ne la laisse pas derrière lui.

## Fonction `loom_ia.adapters.idempotency.redis._owner`

```python
def _owner(tenant_id: TenantId, session_id: SessionId) -> str
```

Nom de l'ensemble d'une session : client et session encodés, sans `:` ni motif.

## Fonction `loom_ia.adapters.idempotency.redis._ms`

```python
def _ms(seconds: float) -> str
```

Durée de vie en millisecondes, jamais nulle : Redis refuse un PX à 0.

## Classe `loom_ia.adapters.idempotency.redis.RedisIdempotency`

```python
class RedisIdempotency
```

Magasin partagé par tous les process qui parlent au même Redis.

`retention` : durée de vie d'un résultat mémorisé quand l'outil n'en
fixe pas — et, ici, durée au bout de laquelle Redis oublie la clé, quel
que soit son état.

### Méthode `loom_ia.adapters.idempotency.redis.RedisIdempotency.get`

```python
async def get(self, key: str) -> IdempotencyRecord | None
```

Un résultat hors de sa rétention n'est pas rendu ; une réservation périmée, si.

### Méthode `loom_ia.adapters.idempotency.redis.RedisIdempotency.reserve`

```python
async def reserve(self, key: str, ttl: float, scope: KeyScope, *, holder: str | None=None) -> bool
```

Prend la clé pour `ttl` secondes ; faux si quelqu'un la tient déjà (`IdempotencyStore.reserve`).

Ce qui protège, c'est la date : une réservation tenue ou un résultat
encore mémorisé gardent la clé ; une clé périmée peut être reprise. Un script Lua prend la clé et l'inscrit dans
l'ensemble d'appartenance de sa session, d'un seul tenant ; la clé Redis
vit au moins le temps de la réservation.

**Arguments :**

- `key` : La clé.
- `ttl` : Durée de la réservation, en secondes.
- `scope` : De qui est la clé (client, session), pour l'oublier avec eux.
- `holder` : Jeton propre à cette prise de clé.

**Renvoie :**

Vrai si la clé est prise.

### Méthode `loom_ia.adapters.idempotency.redis.RedisIdempotency.complete`

```python
async def complete(self, key: str, result: object, ttl: float | None=None, *, holder: str | None=None) -> None
```

Enregistre ce que l'effet a rendu : la clé cesse d'être une réservation (`IdempotencyStore.complete`).

Un script Lua vérifie le détenteur et écrit le résultat, avec sa durée de
vie.

**Arguments :**

- `key` : La clé réservée.
- `result` : Ce que l'outil a rendu.
- `ttl` : Durée de consultation du résultat, en secondes ; `None` : la rétention du magasin.
- `holder` : Avec un jeton, l'enregistrement n'a lieu que s'il est celui de la clé ; sinon, il est sans effet.

**Lève :**

- `KeyError` : Le magasin ne connaît plus la clé.

### Méthode `loom_ia.adapters.idempotency.redis.RedisIdempotency.release`

```python
async def release(self, key: str, *, holder: str | None=None) -> None
```

Rend une clé réservée dont l'effet ne s'est pas produit.

### Méthode `loom_ia.adapters.idempotency.redis.RedisIdempotency.forget`

```python
async def forget(self, tenant_id: TenantId, session_id: SessionId | None=None) -> int
```

Oublie les clés d'un client, ou de l'une de ses sessions (RGPD).

Les appartenances sont tenues à part, faute de quoi il faudrait
balayer Redis pour retrouver les clés d'une session.

### Méthode `loom_ia.adapters.idempotency.redis.RedisIdempotency.aclose`

```python
async def aclose(self) -> None
```

Ferme la connexion à Redis, si elle est ouverte.
