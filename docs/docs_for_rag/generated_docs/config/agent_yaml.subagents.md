# agent.yaml : la clé subagents

La clé `subagents` de `agent.yaml` et tout ce qu'elle contient.

## `subagents[]`

Sous-agent (fonction C5) : un autre agent de la config, appelé comme un outil.

Modèle Pydantic : `loom_ia.agents.spec.SubAgentRef`.

- `subagents[].agent` (type `str` ; obligatoire ; bornes : motif `^[A-Za-z0-9_-]{1,64}$`) : Nom de l'agent appelé.
- `subagents[].name` (type `str | null` ; défaut : `null` ; bornes : motif `^[A-Za-z0-9_-]{1,64}$`) : Nom de l'outil vu par l'orchestrateur ; par défaut, celui de l'agent.
- `subagents[].description` (type `str | null` ; défaut : `null`) : Description de l'outil ; par défaut, celle de l'agent.
- `subagents[].budget_share` (type `float | null` ; défaut : `null` ; bornes : > 0.0, ≤ 1.0) : Part (entre 0 exclu et 1) de ce qui reste au budget du run appelant au moment de l'appel (fonction J4).
- `subagents[].side_effects` (type `SideEffects` ; défaut : `"irreversible"`) : Effets du sous-agent, ceux des outils de son agent : `irreversible` par défaut. Ils décident de l'ordre des appels d'un même tour : déclaré `none`, il tourne en même temps que les autres appels sans effet.
