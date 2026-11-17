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

Modèle Pydantic : `loom_ia.core.model.budget.TenantBudget`.

- `budget.tenant.max_cost_per_day` (type `float | null` ; défaut : `null`) : Dollars par jour (UTC).
- `budget.tenant.max_tokens_per_day` (type `int | null` ; défaut : `null`) : Tokens par jour.
- `budget.tenant.max_cost_per_month` (type `float | null` ; défaut : `null`) : Dollars par mois.
- `budget.tenant.max_tokens_per_month` (type `int | null` ; défaut : `null`) : Tokens par mois.
