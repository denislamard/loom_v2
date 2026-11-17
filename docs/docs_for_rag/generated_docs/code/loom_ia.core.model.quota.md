# loom_ia.core.model.quota

Module `loom_ia.core.model.quota` de loom_v2 (fichier `src/loom_ia/core/model/quota.py`).

Débit : quotas d'un client, limitation d'une clé d'API (fonction L3, point 39, conception §17.8).

Un budget dit **combien** on peut dépenser, un quota **à quelle vitesse** on
peut demander. Les deux sont indépendants : un client peut avoir de l'argent
et pas le droit de lancer trente runs dans la minute.

Deux notions distinctes, malgré leur ressemblance :

- `Quotas` appartient au **client**, et vaut quel que soit le chemin
  emprunté — API Python, REST, MCP — **dans une même instance** `Loom` :
  sa fenêtre est tenue en mémoire, si bien qu'une commande `loom run`
  (un process par run) ne l'atteint jamais, et que N process (workers,
  `loom serve`) accordent chacun le quota entier. C'est une règle métier :
  ce que cet artisan a payé le droit de faire.
- `RateLimit` appartient à une **clé d'API**, et n'a de sens qu'en HTTP :
  c'est la protection du serveur contre un appelant qui s'emballe, et elle
  se répond par un 429 et un `Retry-After`.

## Constantes de `loom_ia.core.model.quota`

- `WINDOW = 60.0` : Fenêtre des deux limites, en secondes : elles se disent « par minute ».

## Classe `loom_ia.core.model.quota.Quotas`

```python
class Quotas(DomainModel)
```

Débit accordé à un client (fonction L3), déclaré sous `tenants[].quotas`.

Un run refusé lève `QuotaExceeded` ; l'API REST le rend en 429 avec
`Retry-After`.

**Attributs :**

- `runs_per_minute` : Runs qu'un client peut lancer par minute, tous accès confondus, par instance `Loom` (fenêtre en mémoire, voir le module). Un sous-run n'en est pas un : il est lancé par son parent, pas par le client. `None` (défaut) : pas de limite.

### Méthode `loom_ia.core.model.quota.Quotas.limited`

```python
@property
def limited(self) -> bool
```

Vrai si une limite est posée.

## Classe `loom_ia.core.model.quota.RateLimit`

```python
class RateLimit(DomainModel)
```

Débit d'une clé d'API, vérifié par l'accès HTTP (point 39) ; au-delà, 429 et `Retry-After`.

Se pose à la création de la clé (`loom keys create --rate-limit`).

**Attributs :**

- `per_minute` : Requêtes permises par minute à cette clé.
