# loom_ia.tenancy.period

Module `loom_ia.tenancy.period` de loom_v2 (fichier `src/loom_ia/tenancy/period.py`).

Fenêtres calendaires d'un budget de client (jalon J5.1b, conception §15).

Le jour et le mois, **en UTC**. Calendaire et non glissant : un compteur par
fenêtre suffit alors, il se reconstruit depuis le journal en lisant les dates
des événements, et une remise à zéro est une date qu'on peut annoncer à
l'appelant (`Retry-After`). Une fenêtre glissante serait plus juste au sens
strict, mais elle interdirait le compteur agrégé — il faudrait garder chaque
appel horodaté et le sommer à chaque contrôle.

UTC plutôt que le fuseau du client : un fuseau par client demanderait une clé
de plus dans sa fiche et une conversion à chaque contrôle, pour un gain qui se
résume à l'heure où la journée bascule. C'est un choix assumé, pas un oubli —
un artisan français voit sa journée changer à 2 h du matin l'été.

## Constantes de `loom_ia.tenancy.period`

- `PERIODS = ('day', 'month')` : Fenêtres, de la plus courte à la plus longue.

## Fonction `loom_ia.tenancy.period._utc`

```python
def _utc(now: datetime | None) -> datetime
```

L'instant en UTC ; `ValueError` s'il n'a pas de fuseau.

Un instant naïf serait lu à l'heure locale de la machine, et la fenêtre
dépendrait de l'endroit où tourne le process.

## Classe `loom_ia.tenancy.period.Period`

```python
@dataclass(frozen=True, slots=True)
class Period
```

Une fenêtre, par son genre et son début.

**Attributs :**

- `kind` : `day` ou `month`.
- `start` : Début de la fenêtre, à minuit UTC (le premier du mois pour `month`).

### Méthode `loom_ia.tenancy.period.Period.of`

```python
@classmethod
def of(cls, kind: BudgetPeriod, now: datetime | None=None) -> Self
```

La fenêtre qui contient `now` (l'instant présent par défaut).

**Arguments :**

- `kind` : `day` ou `month`.
- `now` : L'instant, avec un fuseau horaire.

**Lève :**

- `ValueError` : `now` sans fuseau horaire.

### Méthode `loom_ia.tenancy.period.Period.key`

```python
@property
def key(self) -> str
```

Clé de rangement, triable dans le temps : `day:2026-09-23`, `month:2026-09`.

### Méthode `loom_ia.tenancy.period.Period.end`

```python
@property
def end(self) -> datetime
```

Instant de la remise à zéro : le début de la fenêtre suivante.

### Méthode `loom_ia.tenancy.period.Period.resets_in`

```python
def resets_in(self, now: datetime | None=None) -> float
```

Secondes avant la remise à zéro ; jamais négatif.

**Arguments :**

- `now` : L'instant, avec un fuseau horaire ; maintenant par défaut.
