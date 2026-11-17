# agent.yaml : la clé main

La clé `main` de `agent.yaml` et tout ce qu'elle contient.

## `main`

Rôle orchestrateur : le modèle qui mène le run.

Modèle Pydantic : `loom_ia.agents.spec.MainRole`.

- `main.model` (type `str` ; obligatoire ; bornes : longueur ≥ 1) : (sans description)
- `main.fallbacks` (type `tuple[str, ...]` ; défaut : `[]`) : (sans description)
- `main.system` (type `str` ; défaut : `""`) : (sans description)
- `main.system_file` (type `Path | null` ; défaut : `null`) : (sans description)
- `main.llm` (type `LlmSettings` ; défaut : les défauts de la section) : (sans description)
- `main.context` (type `tuple[MainContextItem, ...]` ; défaut : `[]`) : Contexte ajouté à chaque appel de l'orchestrateur, à la fin de sa requête (décision D4 du state de session) : seulement `session_state`, entier ou par clés.

## `main.llm`

Réglages d'appel propres à un rôle (`llm` ; fonction B6).

Modèle Pydantic : `loom_ia.agents.spec.LlmSettings`.

- `main.llm.max_tokens` (type `int | null` ; défaut : `null`) : Remplace `max_tokens` du modèle.
- `main.llm.params` (type `dict[str, JsonValue]` ; défaut : `{}`) : Paramètres du fournisseur (température, raisonnement…), fusionnés clé par clé sur `params` du modèle.

## `main.context[]`

Contexte `session_state` limité à certaines clés du state de la session.

Modèle Pydantic : `loom_ia.agents.spec.SessionStateContext`.

- `main.context[].session_state` (type `tuple[str, ...]` ; obligatoire ; bornes : longueur ≥ 1) : Les clés reçues, déclarées dans `state` ; une au moins.
