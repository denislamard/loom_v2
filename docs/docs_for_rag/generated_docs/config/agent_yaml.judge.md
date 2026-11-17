# agent.yaml : la clé judge

La clé `judge` de `agent.yaml` et tout ce qu'elle contient.

## `judge`

Juge d'une sortie (fonctions E3 et E6, point 21) : la réponse finale (agent) ou la sortie d'un rôle.

Modèle Pydantic : `loom_ia.agents.spec.JudgeSpec`.

- `judge.model` (type `str` ; obligatoire ; bornes : longueur ≥ 1) : Identifiant d'un modèle déclaré dans `models` ; s'il est celui qu'il évalue, un avertissement (une erreur en profil `prod`, fonction E6).
- `judge.fallbacks` (type `tuple[str, ...]` ; défaut : `[]`) : Modèles de secours, dans l'ordre (fonction B4, point 10).
- `judge.name` (type `str | null` ; défaut : `null` ; bornes : motif `^[A-Za-z0-9_-]{1,64}$`) : Nom dans le journal (tirage, rôle `judge:<nom>`) ; par défaut `output` pour la réponse finale, le nom du rôle pour un rôle.
- `judge.criteria` (type `tuple[Criterion, ...]` ; obligatoire ; bornes : longueur ≥ 1) : Les critères (`name`, `rule`, `min_score`, `blocking`) ; un au moins.
- `judge.context` (type `tuple[ContextItem, ...]` ; défaut : `[]`) : Contexte reçu : `user_input`, `caller_context`, `attachments`, `session_summary`, `tool_results`, `last_turns` ; pas `session_state` (décision D11 du state de session).
- `judge.when` (type `JudgeWhen` ; défaut : les défauts de la section) : Déclenchement : `sample`, `condition`, `tenants`, `profiles`.
- `judge.repair` (type `RepairSettings` ; défaut : les défauts de la section) : Réparations demandées à l'auteur de la sortie refusée (`max_attempts`, `tools`).
- `judge.on_failure` (type `OnFailure` ; défaut : `"fail"`) : Suite d'une sortie encore refusée après les réparations : `fail` (défaut), `unverified` ou `fallback`.
- `judge.fallback_message` (type `str | null` ; défaut : `null`) : Message qui remplace la sortie avec `on_failure: fallback`.
- `judge.llm` (type `LlmSettings` ; défaut : les défauts de la section) : Réglages d'appel du juge.
- `judge.timeout` (type `float | null` ; défaut : `null`) : Délai du juge, en secondes ; sans lui, ceux de son modèle et son retry le bornent.
- `judge.on_error` (type `"block" | "allow"` ; défaut : `"block"`) : Erreur du juge (modèle, verdict) : `block` (défaut) fait échouer le run, `allow` laisse passer.

## `judge.criteria[]`

Critère d'un juge : une règle, un seuil, bloquant ou non.

Modèle Pydantic : `loom_ia.core.model.judge.Criterion`.

- `judge.criteria[].name` (type `str` ; obligatoire ; bornes : motif `^[A-Za-z0-9_-]{1,64}$`) : Nom du critère, repris dans le verdict.
- `judge.criteria[].rule` (type `str` ; obligatoire ; bornes : longueur ≥ 1) : Ce que la sortie doit respecter, donné au juge tel quel.
- `judge.criteria[].min_score` (type `float` ; défaut : `0.8` ; bornes : ≥ 0.0, ≤ 1.0) : Seuil, entre 0 et 1. Défaut : 0,8.
- `judge.criteria[].blocking` (type `bool` ; défaut : `true`) : Sous son seuil, un critère bloquant fait refuser la sortie ; un critère non bloquant est seulement signalé. Défaut : vrai.

## `judge.context[]` (forme `ToolResultsContext`)

Contexte `tool_results` : résultats réussis des outils nommés.

Modèle Pydantic : `loom_ia.agents.spec.ToolResultsContext`.

- `judge.context[].tool_results` (type `tuple[str, ...]` ; obligatoire ; bornes : longueur ≥ 1) : Les outils dont les résultats sont reçus ; un au moins.
- `judge.context[].scope` (type `ContextScope` ; défaut : `"run"`) : `run` (défaut) : seulement ce que le run a obtenu. `session` : à défaut, le dernier résultat connu de la session — qui peut dater d'un tour ancien.

## `judge.context[]` (forme `LastTurnsContext`)

Contexte `last_turns` : derniers tours de la session (un tour = un run).

Modèle Pydantic : `loom_ia.agents.spec.LastTurnsContext`.

- `judge.context[].last_turns` (type `int` ; obligatoire ; bornes : > 0) : Nombre de tours reçus.

## `judge.when`

Déclenchement d'un juge (`when`) : toutes les clauses présentes doivent être vraies.

Modèle Pydantic : `loom_ia.core.model.judge.JudgeWhen`.

- `judge.when.sample` (type `float` ; défaut : `1.0` ; bornes : ≥ 0.0, ≤ 1.0) : Proportion des runs jugés, entre 0 et 1 (tirage déterministe par run et par juge). Défaut : 1, tous.
- `judge.when.condition` (type `str | null` ; défaut : `null` ; bornes : longueur ≥ 1) : Prédicat `(JudgeInput) -> bool`, synchrone ou non, donné par `module:attr`.
- `judge.when.tenants` (type `tuple[str, ...] | null` ; défaut : `null` ; bornes : longueur ≥ 1) : Clients dont les runs sont jugés, un au moins.
- `judge.when.profiles` (type `tuple[str, ...] | null` ; défaut : `null` ; bornes : longueur ≥ 1) : Profils où ce juge travaille (phase 5.5a) : ailleurs il est sauté, et le journal le dit (`other_profile`) — un juge déclaré ne disparaît pas en silence parce qu'on a changé de profil.

## `judge.repair`

Même forme que `output.repair` (page « agent.yaml : la clé output »).

## `judge.llm`

Même forme que `main.llm` (page « agent.yaml : la clé main »).
