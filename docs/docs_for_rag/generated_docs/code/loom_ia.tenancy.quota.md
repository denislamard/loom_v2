# loom_ia.tenancy.quota

Module `loom_ia.tenancy.quota` de loom_v2 (fichier `src/loom_ia/tenancy/quota.py`).

Limitation de débit : quota d'un client, débit d'une clé d'API (fonction L3, point 39).

Fenêtre **glissante** de soixante secondes, et non calendaire comme les
budgets : un plafond de débit qui laisse passer le double à cheval sur la
minute n'en est pas un. Le prix est modeste — on garde au plus `limit`
horodatages par clé de comptage, et on jette ce qui est sorti de la fenêtre.

Le même compteur sert deux choses qu'il ne faut pas confondre. Le quota d'un
**client** (`quotas.runs_per_minute`) est une règle métier : il est vérifié
à la façade, donc il vaut par les trois accès — mais **par instance**
`Loom` : la fenêtre vit en mémoire, une commande `loom run` (un process
par run) ne l'atteint jamais, et deux workers ou deux `loom serve` tiennent
chacun la leur (essai de la fonction L3, 22/10). Le débit d'une **clé d'API**
(`rate_limit.per_minute`) protège le serveur : il est vérifié par l'accès
HTTP, qui répond 429 avec un `Retry-After`, et tient lui aussi une fenêtre
par application.

## Constantes de `loom_ia.tenancy.quota`

- `Counted = str` : Ce qu'on compte : un client, ou une clé d'API.

## Classe `loom_ia.tenancy.quota.QuotaExceeded`

```python
class QuotaExceeded(RuntimeError)
```

Le débit accordé est dépassé : la demande n'est pas servie.

`retry_after` porte les secondes au bout desquelles la plus ancienne
demande de la fenêtre en sortira, donc l'instant où la suivante passera.

**Arguments :**

- `what` : Ce qui est limité, pour le message (« Client 'dupont' »).
- `limit` : Demandes permises par minute.
- `retry_after` : Secondes avant que la suivante passe.

**Attributs :**

- `limit` : Demandes permises par minute.
- `retry_after` : Secondes avant que la suivante passe.

## Classe `loom_ia.tenancy.quota.RateWindow`

```python
class RateWindow
```

Fenêtre glissante partagée, par clé de comptage.

**Arguments :**

- `window` : Durée de la fenêtre, en secondes (60 par défaut).

**Attributs :**

- `window` : Durée de la fenêtre, en secondes.

### Méthode `loom_ia.tenancy.quota.RateWindow.take`

```python
def take(self, key: Counted, limit: int, now: float | None=None) -> float | None
```

Compte une demande et rend `None` ; sinon l'attente, sans rien compter.

**Arguments :**

- `key` : Clé de comptage (`tenant:<id>`, clé d'API).
- `limit` : Demandes permises dans la fenêtre.
- `now` : Instant monotone, en secondes ; `time.monotonic()` par défaut.

**Renvoie :**

`None` si la demande passe ; sinon les secondes à attendre.

## Classe `loom_ia.tenancy.quota.Quota`

```python
class Quota
```

Quota d'un client : runs lancés par minute, quel que soit l'accès.

Config : `quotas.runs_per_minute` du client. Vérifié par la façade
`Loom` au lancement d'un run, par instance.

**Arguments :**

- `window` : Fenêtre partagée ; une neuve par défaut.

### Méthode `loom_ia.tenancy.quota.Quota.check`

```python
def check(self, tenant_id: TenantId, limit: int | None) -> None
```

Compte un run du client, ou refuse s'il a déjà lancé son compte de la minute.

**Arguments :**

- `tenant_id` : Le client.
- `limit` : Runs permis par minute ; `None` : pas de quota.

**Lève :**

- `QuotaExceeded` : Le quota est atteint.
