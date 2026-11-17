# agent.yaml : la clé policies

La clé `policies` de `agent.yaml` et tout ce qu'elle contient.

## `policies[]`

Politique branchée sur l'agent (points 1 et 2, conception §17.4).

`hook` : un nom enregistré (module de `imports`), un chemin
`module:attr`, ou une politique fournie (`loom.require_tool`).
`points` : ceux où l'agent la branche, parmi ceux qu'elle déclare ; par
défaut, tous. Les politiques d'un point s'exécutent dans l'ordre déclaré.

Modèle Pydantic : `loom_ia.agents.spec.PolicyRef`.

- `policies[].hook` (type `str` ; obligatoire ; bornes : longueur ≥ 1) : La politique.
- `policies[].name` (type `str | null` ; défaut : `null` ; bornes : motif `^[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}$`) : Nom dans le journal ; par défaut celui de la politique. Le préfixe `loom.` est réservé.
- `policies[].points` (type `tuple[HookPoint, ...] | null` ; défaut : `null` ; bornes : longueur ≥ 1) : Points où l'agent la branche ; par défaut, tous ceux qu'elle déclare.
- `policies[].params` (type `dict[str, JsonValue]` ; défaut : `{}`) : Paramètres donnés à la politique (`context.params`).
- `policies[].timeout` (type `float | null` ; défaut : `5.0`) : Délai de la politique, en secondes (5 par défaut) ; `null` le retire.
- `policies[].on_error` (type `"block" | "allow"` ; défaut : `"block"`) : Erreur de la politique : `block` (défaut) fait échouer le run, `allow` l'ignore.
- `policies[].max_attempts` (type `int` ; défaut : `1` ; bornes : > 0) : Réparations (`Retry`) que la politique peut demander pour une sortie ; au-delà, sa décision devient `Fail`.
