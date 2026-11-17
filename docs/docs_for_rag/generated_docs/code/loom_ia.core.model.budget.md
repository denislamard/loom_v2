# loom_ia.core.model.budget

Module `loom_ia.core.model.budget` de loom_v2 (fichier `src/loom_ia/core/model/budget.py`).

Budgets d'un run, d'une session et d'un client (fonction J4, point 1, conception §15).

Un budget borne la consommation : en dollars (`max_cost`), en tokens
(`max_tokens`, utile pour un modèle sans tarif) et, pour un run, en appels
de modèle (`max_calls` : orchestrateur, rôles et juges du run ; un
sous-agent a son propre budget). Il est vérifié avant chaque appel de
l'orchestrateur (politique fournie `loom.budget`) : une limite atteinte
écrit un `budget.exceeded` ; avec `on_exceed: stop`, le run passe en
`FINALIZING` et donne une réponse forcée sans outils, et le dépassement est
borné à cette dernière génération.

Le budget de session compte la consommation des runs précédents de la
session, plus celle du run en cours.

Le budget d'un **client** (`tenant`, phase 5.1b) est d'une autre nature : il
borne ce qu'il a le droit de lancer sur une journée ou un mois, et se lit
donc une fois, avant d'ouvrir le run, et non avant chaque appel.

Sous-agent (fonction C5) : `budget_share` lui donne une part de ce qui reste au run
parent au moment de l'appel ; cette part est écrite dans le `run.started`
de l'enfant (`RunBudget`), et s'ajoute au budget propre de son agent (la
limite la plus basse l'emporte).

Les budgets se déclarent sous `budgets` à la racine de `loom.yaml`
(défauts pour tous les agents), sous `budget` d'un agent (surcharge clé par
clé), et sous `tenants[].budgets` pour un client.

Exemple :

```
budgets:
  run: { max_cost: 0.50, max_calls: 20 }
  session: { max_tokens: 500000 }
  tenant: { max_cost_per_day: 5.0 }
  on_exceed: stop
```

## Constantes de `loom_ia.core.model.budget`

- `BudgetScope = Literal['run', 'session']` : Portée d'un budget vérifié avant chaque appel : le run, ou la session.
- `BudgetLimit = Literal['max_cost', 'max_tokens', 'max_calls']` : Limite d'un budget : dollars, tokens, ou appels de modèle.
- `OnExceed = Literal['warn', 'stop']` : Suite d'une limite atteinte : `warn` (le run continue, `budget.exceeded` écrit) ou `stop` (réponse forcée sans outils).
- `BudgetPeriod = Literal['day', 'month']` : Fenêtre calendaire d'un budget de client (phase 5.1b), en UTC.

## Classe `loom_ia.core.model.budget.RunBudget`

```python
class RunBudget(DomainModel)
```

Limites d'un run (`budgets.run`) ; `None` : pas de limite.

**Attributs :**

- `max_cost` : Coût maximal du run, en dollars.
- `max_tokens` : Tokens au plus (entrée, cache compris, et sortie) ; utile pour un modèle sans tarif.
- `max_calls` : Appels de modèle au plus : orchestrateur, rôles et juges du run ; un sous-agent a son propre budget.

### Méthode `loom_ia.core.model.budget.RunBudget.limited`

```python
@property
def limited(self) -> bool
```

Vrai si au moins une limite est posée.

### Méthode `loom_ia.core.model.budget.RunBudget.tightest`

```python
def tightest(self, other: RunBudget | None) -> RunBudget
```

Limites les plus basses des deux budgets, limite par limite ; `None` laisse ce budget tel quel.

### Méthode `loom_ia.core.model.budget.RunBudget.share`

```python
def share(self, part: float, spent: Spent) -> RunBudget | None
```

Part de ce qui reste au run, pour un sous-agent (`budget_share`).

**Arguments :**

- `part` : Proportion de ce qui reste, entre 0 et 1.
- `spent` : Ce que le run a déjà consommé.

**Renvoie :**

Le budget du sous-agent : la part du reste de chaque limite (un
appel et un token au moins pour une limite entière) ; `None` si
une limite du run est déjà atteinte.

## Classe `loom_ia.core.model.budget.SessionBudget`

```python
class SessionBudget(DomainModel)
```

Limites d'une session (`budgets.session`) : runs précédents et run en cours ; `None` : pas de limite.

**Attributs :**

- `max_cost` : Coût maximal de la session, en dollars.
- `max_tokens` : Tokens au plus sur la session.

### Méthode `loom_ia.core.model.budget.SessionBudget.limited`

```python
@property
def limited(self) -> bool
```

Vrai si au moins une limite est posée.

## Classe `loom_ia.core.model.budget.TenantBudget`

```python
class TenantBudget(DomainModel)
```

Plafonds d'un client sur une fenêtre calendaire, en UTC (`budgets.tenant`, fonctions J4 et L3).

Ce n'est pas un budget de run : il ne borne pas ce qu'un run dépense, il
borne ce qu'un client a le droit de **lancer**. Il est donc lu une fois,
avant d'ouvrir le run — la journée d'un artisan est finie ou elle ne l'est
pas, et l'avertir en cours de route par une réponse forcée lui ferait
payer une génération pour l'apprendre.

Pas de `max_calls` : compter les appels de modèle d'une journée n'apprend
rien de plus que ses tokens, et ce que le schéma annonce (conception §17.7)
est le coût et les tokens.

Un run refusé lève `BudgetExhausted` ; l'API REST le rend en 429, avec
un `Retry-After` jusqu'à la bascule de la période.

**Attributs :**

- `max_cost_per_day` : Dollars par jour (UTC).
- `max_tokens_per_day` : Tokens par jour.
- `max_cost_per_month` : Dollars par mois.
- `max_tokens_per_month` : Tokens par mois.

### Méthode `loom_ia.core.model.budget.TenantBudget.limited`

```python
@property
def limited(self) -> bool
```

Vrai si au moins un plafond est posé.

### Méthode `loom_ia.core.model.budget.TenantBudget.periods`

```python
@property
def periods(self) -> tuple[BudgetPeriod, ...]
```

Fenêtres qui portent au moins une limite ; vide si le client n'en a aucune.

### Méthode `loom_ia.core.model.budget.TenantBudget.limits`

```python
def limits(self, period: BudgetPeriod) -> tuple[tuple[BudgetLimit, float], ...]
```

Limites d'une fenêtre, par exemple `(("max_cost", 5.0), ("max_tokens", 200000.0))`.

**Arguments :**

- `period` : `day` ou `month`.

**Renvoie :**

Les couples (limite, valeur) posés pour cette fenêtre.

## Classe `loom_ia.core.model.budget.Budgets`

```python
class Budgets(DomainModel)
```

Budgets d'un agent : défauts de `budgets` (racine), surchargés par son `budget`.

**Attributs :**

- `run` : Limites d'un run.
- `session` : Limites d'une session.
- `tenant` : Plafonds du client sur une période (phase 5.1b) ; ils ne dépendent pas de l'agent, et c'est la fiche du client (`tenants[]`) qui les porte le plus souvent.
- `on_exceed` : `stop` (défaut) ou `warn`.

### Méthode `loom_ia.core.model.budget.Budgets.limited`

```python
@property
def limited(self) -> bool
```

Vrai si le run ou la session a une limite ; les plafonds du client ne comptent pas ici.

### Méthode `loom_ia.core.model.budget.Budgets.in_dollars`

```python
@property
def in_dollars(self) -> bool
```

Vrai si une limite est en dollars : run, session, ou plafond de client par période.

### Méthode `loom_ia.core.model.budget.Budgets.merged`

```python
def merged(self, override: Budgets | None) -> Self
```

Ce budget, surchargé clé par clé par les champs donnés dans `override`.

Seuls les champs écrits dans la surcharge comptent : ``budget: {run:
{max_calls: 5}}` change `max_calls` et garde le `max_cost`` de la
racine.

**Arguments :**

- `override` : Le `budget` de l'agent, ou `None`.

**Renvoie :**

Le budget fusionné.

## Classe `loom_ia.core.model.budget.Spent`

```python
@dataclass(frozen=True, slots=True)
class Spent
```

Consommation : usage, coût et nombre d'appels de modèle ; deux `Spent` s'additionnent.

**Attributs :**

- `usage` : Tokens consommés.
- `cost` : Coût, en dollars.
- `calls` : Appels de modèle (une tentative ratée facturée n'en est pas un).

### Méthode `loom_ia.core.model.budget.Spent.tokens`

```python
@property
def tokens(self) -> int
```

Total des tokens, entrée (cache compris) et sortie.

### Méthode `loom_ia.core.model.budget.Spent.__add__`

```python
def __add__(self, other: Spent) -> Spent
```

Somme de deux consommations.

## Fonction `loom_ia.core.model.budget._lowest`

```python
def _lowest[T: (float, int)](a: T | None, b: T | None) -> T | None
```

La plus basse de deux limites ; `None` ne borne rien.
