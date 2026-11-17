# agent.yaml : la clé execution

La clé `execution` de `agent.yaml` et tout ce qu'elle contient.

## `execution`

Exécution propre à l'agent (`execution`) : remplace, clé par clé, celle de loom.yaml.

Modèle Pydantic : `loom_ia.agents.spec.AgentExecution`.

- `execution.tools` (type `AgentToolsExecution` ; défaut : les défauts de la section) : Exécution des outils de l'agent.

## `execution.tools`

Exécution des outils propre à l'agent ; `None` garde le réglage de la racine.

Modèle Pydantic : `loom_ia.agents.spec.AgentToolsExecution`.

- `execution.tools.order` (type `"effects" | "free" | null` ; défaut : `null`) : Ordre des appels d'un même tour : `effects` ou `free` (`execution.tools.order` de loom.yaml).
