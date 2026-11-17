# agent.yaml : la clé approval

La clé `approval` de `agent.yaml` et tout ce qu'elle contient.

## `approval`

Ce qu'un agent fait des approbations qu'il demande (`approval` de l'agent, points 17 et 28).

Modèle Pydantic : `loom_ia.core.model.tooling.ApprovalSettings`.

- `approval.expires_in` (type `float | null` ; défaut : `86400.0`) : Délai laissé à l'approbateur, en secondes. Défaut : 86 400 (24 h). `null` le retire — et une demande attend alors indéfiniment, ce qui est un choix, pas un défaut : rien d'autre ne borne cette attente, ni le délai de l'agent (qui ne compte que le pilotage) ni la reprise (qui ne fait que reconstater l'attente).
- `approval.on_expiry` (type `ExpiryAction` ; défaut : `"deny"`) : Ce qu'il advient d'une demande périmée : `deny` (défaut, un « non » prudent, personne n'ayant dit oui ; le modèle continue) ou `fail` (le run échoue).
