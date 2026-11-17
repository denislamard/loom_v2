# loom_ia.adapters.idempotency.memory

Module `loom_ia.adapters.idempotency.memory` de loom_v2 (fichier `src/loom_ia/adapters/idempotency/memory.py`).

Magasin d'idempotence en mémoire : tests et déploiement mono-process (points 18 et 49).

Il voit toutes les clés d'un process — contrairement au magasin `journal`,
qui ne voit que son run — mais seulement de ce process : deux workers ne
partagent rien, et rien ne survit à un redémarrage. Une clé métier, qui doit
valoir pour tout le monde et durer, lui est donc refusée au chargement ; c'est
ce que le magasin `sqlite` apporte en plus.

## Classe `loom_ia.adapters.idempotency.memory._Held`

```python
@dataclass(frozen=True, slots=True)
class _Held
```

Un enregistrement, de qui il est, et le détenteur qui l'a pris.

## Classe `loom_ia.adapters.idempotency.memory.InMemoryIdempotency`

```python
class InMemoryIdempotency
```

Magasin non durable, limité au process.

`reserve` ne contient aucun `await` : le contrôle et la prise de la
clé sont donc atomiques pour la boucle asyncio, ce que le port exige.

Une réservation périmée n'est **jamais** effacée d'elle-même : c'est la
trace d'un effet d'état inconnu, et l'effacer la ferait passer pour un
appel jamais lancé (point 18). Seuls les résultats hors rétention s'oublient.

### Méthode `loom_ia.adapters.idempotency.memory.InMemoryIdempotency.get`

```python
async def get(self, key: str) -> IdempotencyRecord | None
```

Ce que le magasin sait de cette clé, ou `None` (`IdempotencyStore.get`).

Un résultat hors de sa rétention n'est pas rendu ; une réservation
périmée, si.

### Méthode `loom_ia.adapters.idempotency.memory.InMemoryIdempotency.reserve`

```python
async def reserve(self, key: str, ttl: float, scope: KeyScope, *, holder: str | None=None) -> bool
```

Prend la clé pour `ttl` secondes ; faux si quelqu'un la tient déjà (`IdempotencyStore.reserve`).

Ce qui protège, c'est la date : une réservation tenue ou un résultat
encore mémorisé gardent la clé ; une clé périmée peut être reprise. Sans `await` : atomique pour la boucle asyncio.

**Arguments :**

- `key` : La clé.
- `ttl` : Durée de la réservation, en secondes.
- `scope` : De qui est la clé (client, session), pour l'oublier avec eux.
- `holder` : Jeton propre à cette prise de clé.

**Renvoie :**

Vrai si la clé est prise.

### Méthode `loom_ia.adapters.idempotency.memory.InMemoryIdempotency.complete`

```python
async def complete(self, key: str, result: object, ttl: float | None=None, *, holder: str | None=None) -> None
```

Enregistre ce que l'effet a rendu : la clé cesse d'être une réservation (`IdempotencyStore.complete`).

Les résultats périmés sont d'abord effacés ; les réservations, jamais.

**Arguments :**

- `key` : La clé réservée.
- `result` : Ce que l'outil a rendu.
- `ttl` : Durée de consultation du résultat, en secondes ; `None` : la rétention du magasin.
- `holder` : Avec un jeton, l'enregistrement n'a lieu que s'il est celui de la clé ; sinon, il est sans effet.

**Lève :**

- `KeyError` : Le magasin ne connaît plus la clé.

### Méthode `loom_ia.adapters.idempotency.memory.InMemoryIdempotency.release`

```python
async def release(self, key: str, *, holder: str | None=None) -> None
```

Rend une clé réservée dont l'effet ne s'est pas produit (`IdempotencyStore.release`).

Un résultat enregistré n'est jamais rendu ; avec un jeton, seule une
réservation encore à ce détenteur l'est.

**Arguments :**

- `key` : La clé réservée.
- `holder` : Le jeton de la prise de clé.

### Méthode `loom_ia.adapters.idempotency.memory.InMemoryIdempotency.forget`

```python
async def forget(self, tenant_id: TenantId, session_id: SessionId | None=None) -> int
```

Oublie les clés d'un client, ou de l'une de ses sessions (RGPD).

### Méthode `loom_ia.adapters.idempotency.memory.InMemoryIdempotency._forget`

```python
def _forget(self, now: datetime) -> None
```

Oublie les résultats hors rétention ; les réservations restent.
