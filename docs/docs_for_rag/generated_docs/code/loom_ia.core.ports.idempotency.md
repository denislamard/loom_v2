# loom_ia.core.ports.idempotency

Module `loom_ia.core.ports.idempotency` de loom_v2 (fichier `src/loom_ia/core/ports/idempotency.py`).

Mémoire des effets déjà produits, par clé (points 18 et 49, fonction D11).

Un outil à effet de bord peut être exécuté deux fois : parce que son appel a
été interrompu entre l'effet et son enregistrement au journal, ou parce que le
modèle redemande la même action par un autre appel. Ce port donne de quoi s'en
prémunir : réserver une clé avant d'agir, enregistrer le résultat après, et le
retrouver au lieu de refaire.

`reserve` est le seul point délicat. `get` puis `reserve`, c'est un
check-then-act : entre les deux, un autre pilote peut passer. Ce n'est donc pas
`get` qui protège, c'est l'**atomicité** de `reserve` — il rend faux si la
clé est déjà tenue, et c'est ce faux qui fait foi.

**Jeton de détenteur.** Une réservation périmée est reprise par un autre, et
son premier détenteur, resté en vie, finit un jour par appeler `complete` ou
`release` : sans rien pour les distinguer, il écraserait la réservation ou le
résultat de son successeur. `reserve` reçoit donc un jeton opaque (`holder`)
que le magasin retient avec la clé ; `complete` et `release` ne s'appliquent
que si on leur présente ce jeton. Il est **optionnel** : sans lui, rien n'est
contrôlé, comme avant — un appelant ou un magasin qui n'en connaît pas reste
valable.

Implémentations fournies (`adapters/idempotency`) : en mémoire, SQLite,
Postgres et Redis ; et le magasin `journal` (défaut), qui écrit
`idempotency.recorded` dans le journal du run. Le magasin se déclare sous
`storage.idempotency` ; une clé **métier**, visible d'un run à l'autre,
exige un magasin partagé et durable.

## Classe `loom_ia.core.ports.idempotency.KeyScope`

```python
@dataclass(frozen=True, slots=True, kw_only=True)
class KeyScope
```

De qui est une clé d'idempotence : le client qui la porte, la session qui l'a créée.

Le client cadre la clé — deux artisans ne se partagent pas « relance du
devis D-2026-042 ». La session dit de quoi la clé est la trace, et c'est
par elle qu'on l'efface quand la session s'efface (RGPD).

**Attributs :**

- `tenant_id` : Client qui porte la clé.
- `session_id` : Session qui l'a créée.

## Classe `loom_ia.core.ports.idempotency.IdempotencyStore`

```python
@runtime_checkable
class IdempotencyStore(Protocol)
```

Magasin de clés d'idempotence (point 49).

### Méthode `loom_ia.core.ports.idempotency.IdempotencyStore.get`

```python
async def get(self, key: str) -> IdempotencyRecord | None
```

Ce que le magasin sait de cette clé, ou `None` s'il ne la connaît pas.

Un résultat hors de sa durée de vie n'est **pas** rendu : il n'a plus
cours, et le rendre ferait croire à un effet encore mémorisé. Une
réservation périmée, elle, est rendue telle quelle — c'est la trace
d'un effet d'état inconnu, et c'est à l'appelant d'en décider (point 18),
non au magasin de l'effacer.

**Arguments :**

- `key` : La clé.

**Renvoie :**

La fiche de la clé (réservée ou tenue, résultat), ou `None`.

### Méthode `loom_ia.core.ports.idempotency.IdempotencyStore.reserve`

```python
async def reserve(self, key: str, ttl: float, scope: KeyScope, *, holder: str | None=None) -> bool
```

Prend la clé pour `ttl` secondes ; faux si quelqu'un la tient déjà.

Atomique : de deux appelants simultanés, un seul obtient vrai. Une
réservation **périmée** peut être reprise — c'est le seul chemin par
lequel un effet d'état inconnu est rejoué, et il faut le vouloir.

**Arguments :**

- `key` : La clé.
- `ttl` : Durée de la réservation, en secondes ; passée, elle est périmée.
- `scope` : De qui est la clé : le magasin le retient pour pouvoir l'oublier avec sa session ou avec son client.
- `holder` : Jeton opaque, propre à cette prise de clé ; le magasin le retient, et la reprise d'une réservation périmée le remplace.

**Renvoie :**

Vrai si la clé est prise ; faux si quelqu'un la tient déjà.

### Méthode `loom_ia.core.ports.idempotency.IdempotencyStore.complete`

```python
async def complete(self, key: str, result: object, ttl: float | None=None, *, holder: str | None=None) -> None
```

Enregistre ce que l'effet a rendu : la clé cesse d'être une réservation.

**Arguments :**

- `key` : La clé, réservée par `reserve`.
- `result` : Ce que l'outil a rendu, tel qu'il sera rendu à un appel qui demande le même effet.
- `ttl` : Durée pendant laquelle le résultat reste consultable, en secondes ; `None` laisse le magasin décider.
- `holder` : Avec un jeton, l'enregistrement n'a lieu que s'il est celui de la clé ; celui d'un détenteur périmé, dont la clé a été reprise, n'a **aucun effet** (et ne lève pas).

**Lève :**

- `KeyError` : Le magasin ne connaît plus la clé, avec ou sans jeton.

### Méthode `loom_ia.core.ports.idempotency.IdempotencyStore.release`

```python
async def release(self, key: str, *, holder: str | None=None) -> None
```

Rend une clé réservée dont l'effet ne s'est **pas** produit.

À n'appeler que si l'on en est sûr : un échec avant tout effet de
bord. Dans le doute, on laisse la réservation expirer.

**Arguments :**

- `key` : La clé réservée.
- `holder` : Avec un jeton, la clé n'est rendue que si elle est encore à ce détenteur ; sinon elle est à quelqu'un d'autre, et n'est pas touchée.

### Méthode `loom_ia.core.ports.idempotency.IdempotencyStore.forget`

```python
async def forget(self, tenant_id: TenantId, session_id: SessionId | None=None) -> int
```

Oublie les clés d'un client, ou de l'une de ses sessions (RGPD).

Une clé oubliée rend son effet reproductible : c'est le prix de la
suppression, et il est assumé — la trace de cet effet disparaît de
toute façon avec la session.

**Arguments :**

- `tenant_id` : Le client.
- `session_id` : Une de ses sessions ; `None` : toutes.

**Renvoie :**

Le nombre de clés effacées.
