# agent.yaml : la clé budget

La clé `budget` de `agent.yaml` et tout ce qu'elle contient.

## `budget`

Budgets d'un agent : défauts de `budgets` (racine), surchargés par son `budget`.

Modèle Pydantic : `loom_ia.core.model.budget.Budgets`.

- `budget.run` (type `RunBudget` ; défaut : les défauts de la section) : Limites d'un run.
- `budget.session` (type `SessionBudget` ; défaut : les défauts de la section) : Limites d'une session.
- `budget.tenant` (type `TenantBudget` ; défaut : les défauts de la section) : Plafonds du client sur une période (phase 5.1b) ; ils ne dépendent pas de l'agent, et c'est la fiche du client (`tenants[]`) qui les porte le plus souvent.
- `budget.on_exceed` (type `OnExceed` ; défaut : `"stop"`) : `stop` (défaut) ou `warn`.

## `budget.run`

Limites d'un run (`budgets.run`) ; `None` : pas de limite.

Modèle Pydantic : `loom_ia.core.model.budget.RunBudget`.

- `budget.run.max_cost` (type `float | null` ; défaut : `null`) : Coût maximal du run, en dollars.
- `budget.run.max_tokens` (type `int | null` ; défaut : `null`) : Tokens au plus (entrée, cache compris, et sortie) ; utile pour un modèle sans tarif.
- `budget.run.max_calls` (type `int | null` ; défaut : `null`) : Appels de modèle au plus : orchestrateur, rôles et juges du run ; un sous-agent a son propre budget.

## `budget.session`

Limites d'une session (`budgets.session`) : runs précédents et run en cours ; `None` : pas de limite.

Modèle Pydantic : `loom_ia.core.model.budget.SessionBudget`.

- `budget.session.max_cost` (type `float | null` ; défaut : `null`) : Coût maximal de la session, en dollars.
- `budget.session.max_tokens` (type `int | null` ; défaut : `null`) : Tokens au plus sur la session.

## `budget.tenant`

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

Modèle Pydantic : `loom_ia.core.model.budget.TenantBudget`.

- `budget.tenant.max_cost_per_day` (type `float | null` ; défaut : `null`) : Dollars par jour (UTC).
- `budget.tenant.max_tokens_per_day` (type `int | null` ; défaut : `null`) : Tokens par jour.
- `budget.tenant.max_cost_per_month` (type `float | null` ; défaut : `null`) : Dollars par mois.
- `budget.tenant.max_tokens_per_month` (type `int | null` ; défaut : `null`) : Tokens par mois.
