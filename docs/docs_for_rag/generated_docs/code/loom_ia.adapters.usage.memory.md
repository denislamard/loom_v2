# loom_ia.adapters.usage.memory

Module `loom_ia.adapters.usage.memory` de loom_v2 (fichier `src/loom_ia/adapters/usage/memory.py`).

Compteur de consommation en mémoire (J5.1b, port `UsageCounter`).

Une valeur par run racine, et non une somme qui s'incrémente : c'est ce qui
rend `record` idempotent et permet au réchauffage depuis le journal de
chevaucher la vie courante sans compter deux fois (voir le port).

Ce que ça coûte : une entrée par run de la période, en mémoire, dans ce
process. Le nombre de périodes retenues par client est borné **pour chaque genre
de période** (jours d'un côté, mois de l'autre) — les clés étant triables dans le
temps, ce sont les plus anciennes du genre qui partent. Un compteur partagé entre
workers et durable attend J5.3 (Postgres, Redis).

## Constantes de `loom_ia.adapters.usage.memory`

- `MAX_PERIODS = 8` : Périodes gardées par client et par genre de période (jour, mois) : la courante, plus de quoi traverser un changement de fenêtre sans perdre la précédente.

## Classe `loom_ia.adapters.usage.memory.InMemoryUsageCounter`

```python
class InMemoryUsageCounter
```

Consommation par client et par période, dans ce process.

### Méthode `loom_ia.adapters.usage.memory.InMemoryUsageCounter.consumed`

```python
async def consumed(self, tenant_id: TenantId, period: str) -> Spent
```

Somme de ce qu'ont coûté les runs d'un client sur une période (`UsageCounter.consumed`).

**Arguments :**

- `tenant_id` : Le client.
- `period` : La période (`day:…`, `month:…`).

### Méthode `loom_ia.adapters.usage.memory.InMemoryUsageCounter.record`

```python
async def record(self, tenant_id: TenantId, period: str, run_id: RunId, spent: Spent) -> None
```

Pose ce qu'un run a coûté ; réenregistrer le même run remplace, sans doubler.

Seules les `MAX_PERIODS` périodes les plus récentes de chaque genre (jour,
mois) sont gardées.

**Arguments :**

- `tenant_id` : Le client.
- `period` : La période.
- `run_id` : Le run.
- `spent` : Ce qu'il a coûté.

### Méthode `loom_ia.adapters.usage.memory.InMemoryUsageCounter.aclose`

```python
async def aclose(self) -> None
```

Oublie tous les compteurs.
