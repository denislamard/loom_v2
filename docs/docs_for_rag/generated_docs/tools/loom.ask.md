# Outil fourni loom.ask

L'outil `loom.ask` est fourni avec loom_v2. Un agent l'active dans son `agent.yaml` : `- python: loom.ask` dans `tools`.

## Ce que fait loom.ask

Outil fourni `loom.ask` : poser une question à l'utilisateur.

Le moteur ne l'exécute pas : il lit les questions dans les arguments, les
écrit au journal (`question.asked`) et met le run en pause jusqu'à la
réponse de l'utilisateur, qui revient au modèle comme résultat de l'appel.
`invoke` n'est donc jamais appelé par le moteur ; il refuse s'il l'est.

Ce n'est pas une demande d'autorisation : une action à faire valider passe
par l'approbation (`approval`), que la config décide.

## Comment loom.ask est monté

- cible : `loom_ia.tools_builtin.ask:build`
- réglages communs refusés sur sa référence : `approval`, `side_effects`, `idempotent`, `timeout`, `output`, car l'outil ne fait rien lui-même : il pose une question et attend la réponse de l'utilisateur ; une action à faire valider passe par l'approbation

## Les réglages de loom.ask dans agent.yaml

Ce sont des clés de sa référence dans `tools`, à côté de `python: loom.ask`.

### `tools[]` (référence `python: loom.ask`)

Paramètres de `loom.ask`.

Modèle Pydantic : `loom_ia.tools_builtin.ask_params.AskParams`.

- `tools[].expires_in` (type `float | null` ; défaut : `null`) : Délai, en secondes, après lequel une question sans réponse expire et le modèle reprend avec « pas de réponse ». Par défaut, elle attend indéfiniment : la conversation reprend quand l'utilisateur revient.
