# loom_ia.usage.budget

Module `loom_ia.usage.budget` de loom_v2 (fichier `src/loom_ia/usage/budget.py`).

Budget d'un run et de sa session : politique fournie `loom.budget` (fonction J4, point 1).

Avant chaque appel de l'orchestrateur (`before_model`), la politique
compare la consommation aux limites de l'agent :

- **run** : coût, tokens (sous-agents compris) et appels de modèle du run
  (orchestrateur, rôles, juges) ; la part reçue d'un parent (`budget_share`)
  s'y ajoute, la limite la plus basse l'emporte ;
- **session** (run racine seulement) : consommation des runs précédents de la
  session, plus celle du run.

Une limite atteinte écrit un `budget.exceeded`, une fois par limite et par
run. Avec `on_exceed: stop`, la décision est `Stop` : le run passe en
`FINALIZING` et produit une réponse forcée sans outils ; le dépassement est
borné à cette dernière génération. Avec `warn`, le run continue.

Config : `budgets` de la racine, surchargé clé par clé par `budget` de
l'agent (`run` : `max_cost`, `max_tokens`, `max_calls` ; `session` :
`max_cost`, `max_tokens` ; `on_exceed`). Le montage branche la politique
dès qu'une limite est déclarée.

## Constantes de `loom_ia.usage.budget`

- `BUDGET_POLICY = 'loom.budget'` : Nom de la politique de budget dans le journal.
- `Reached = tuple[BudgetScope, BudgetLimit, float, float]` : Une limite atteinte : portée (`run`, `session`), limite, plafond, consommation.

## Classe `loom_ia.usage.budget.BudgetGuard`

```python
class BudgetGuard(TracingPolicy)
```

Politique `loom.budget` : limites du run et de la session, avant chaque appel.

**Arguments :**

- `budgets` : Les limites de l'agent et `on_exceed`.

**Attributs :**

- `budgets` : Les limites de l'agent.

### Méthode `loom_ia.usage.budget.BudgetGuard.name`

```python
@property
def name(self) -> str
```

`loom.budget`.

### Méthode `loom_ia.usage.budget.BudgetGuard.points`

```python
@property
def points(self) -> frozenset[HookPoint]
```

`before_model` seulement.

### Méthode `loom_ia.usage.budget.BudgetGuard.decisions`

```python
@property
def decisions(self) -> frozenset[DecisionKind]
```

`stop` seulement.

### Méthode `loom_ia.usage.budget.BudgetGuard.decide_traced`

```python
async def decide_traced(self, subject: PolicySubject, context: PolicyContext, trace: Trace) -> Decision
```

Compare la consommation aux limites ; écrit un `budget.exceeded` par limite nouvellement atteinte.

**Arguments :**

- `subject` : Le sujet `before_model` ; sans effet pendant la réponse forcée.
- `context` : Contexte de la politique (son nom va dans l'événement).
- `trace` : Reçoit les `budget.exceeded`.

**Renvoie :**

`Stop` avec les limites atteintes en clair si `on_exceed` vaut
`stop` ; `CONTINUE` sinon, ou si rien n'est atteint.

### Méthode `loom_ia.usage.budget.BudgetGuard.reached`

```python
def reached(self, subject: BeforeModel) -> list[Reached]
```

Limites atteintes : portée, limite, plafond, consommation.

Le run : sa limite la plus basse entre celle de l'agent et la part
reçue d'un parent. La session : pour un run racine, la consommation
des runs précédents plus celle du run.

**Arguments :**

- `subject` : Le sujet `before_model`.

**Renvoie :**

Les limites atteintes ou dépassées.

## Fonction `loom_ia.usage.budget.describe`

```python
def describe(scope: BudgetScope, limit: BudgetLimit, value: float, used: float) -> str
```

Une limite atteinte, en clair : « budget du run atteint : 0,00231 $ (plafond 0,00200 $) ».

**Arguments :**

- `scope` : `run` ou `session`.
- `limit` : `max_cost`, `max_tokens` ou `max_calls`.
- `value` : Le plafond.
- `used` : La consommation.

## Fonction `loom_ia.usage.budget.amount`

```python
def amount(limit: BudgetLimit, value: float) -> str
```

Une quantité de budget : dollars, tokens ou appels.

**Arguments :**

- `limit` : La limite, qui dit l'unité.
- `value` : La quantité.

**Renvoie :**

« 0,00231 $ » (5 décimales, virgule), « 1200 tokens » ou « 3 appels ».
