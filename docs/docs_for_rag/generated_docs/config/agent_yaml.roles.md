# agent.yaml : la clé roles

La clé `roles` de `agent.yaml` et tout ce qu'elle contient.

## `roles[]`

Rôle délégué (fonction C1) : l'orchestrateur l'appelle comme un outil.

Déclaré dans `roles` de l'agent. Un seul appel de modèle, avec son propre
prompt et ses réglages ; voir `engine/roles.py`.

Modèle Pydantic : `loom_ia.agents.spec.RoleSpec`.

- `roles[].model` (type `str` ; obligatoire ; bornes : longueur ≥ 1) : (sans description)
- `roles[].fallbacks` (type `tuple[str, ...]` ; défaut : `[]`) : (sans description)
- `roles[].system` (type `str` ; défaut : `""`) : (sans description)
- `roles[].system_file` (type `Path | null` ; défaut : `null`) : (sans description)
- `roles[].llm` (type `LlmSettings` ; défaut : les défauts de la section (`params: {}`)) : (sans description)
- `roles[].name` (type `str` ; obligatoire ; bornes : motif `^[A-Za-z0-9_-]{1,64}$`) : Nom de l'outil vu par l'orchestrateur ; `main` est réservé.
- `roles[].description` (type `str` ; obligatoire ; bornes : longueur ≥ 1) : Description vue par l'orchestrateur ; le contexte reçu y est ajouté.
- `roles[].input_schema` (type `dict[str, JsonValue]` ; défaut : `{"type": "object", "properties": {}}`) : Schéma JSON des arguments que l'orchestrateur fournit ; objet vide par défaut. Sans `additionalProperties`, les arguments non prévus sont refusés.
- `roles[].input_template` (type `str | null` ; défaut : `null`) : Construction du message (point 12) : variables `args.<argument>` et `context.<contexte>` ; sans template, blocs de contexte puis arguments. Tout le contexte déclaré doit y être cité.
- `roles[].context` (type `tuple[RoleContextItem, ...]` ; défaut : `[]`) : Contexte ajouté aux arguments, pris dans une liste fixe (point 12) : `user_input`, `caller_context`, `attachments`, `session_summary`, `session_state`, `tool_results`, `last_turns`. Un rôle sans arguments ni contexte est refusé.
- `roles[].terminal` (type `bool` ; défaut : `false`) : Sortie transmise telle quelle comme réponse finale quand le rôle est appelé seul dans son tour (fonction C3, point 13).
- `roles[].timeout` (type `float | null` ; défaut : `null`) : Délai de l'appel, en secondes ; sans lui, ceux du modèle et son retry le bornent.
- `roles[].output` (type `OutputContract | null` ; défaut : `null`) : Contrat de sortie (fonction E5) : réparé par le modèle du rôle, puis `on_failure`.
- `roles[].judge` (type `JudgeSpec | null` ; défaut : `null`) : Juge de la sortie (fonction E5, point 21), après le contrat : réparée par le modèle du rôle.

## `roles[].llm`

Même forme que `main.llm` (page « agent.yaml : la clé main »).

## `roles[].context[]` (forme `ToolResultsContext`)

Même forme que `judge.context[]` (page « agent.yaml : la clé judge »).

## `roles[].context[]` (forme `LastTurnsContext`)

Même forme que `judge.context[]` (page « agent.yaml : la clé judge »).

## `roles[].context[]` (forme `SessionStateContext`)

Même forme que `main.context[]` (page « agent.yaml : la clé main »).

## `roles[].output`

Même forme que `output` (page « agent.yaml : la clé output »).

## `roles[].judge`

Même forme que `judge` (page « agent.yaml : la clé judge »).
