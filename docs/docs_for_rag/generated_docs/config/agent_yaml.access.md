# agent.yaml : la clé access

La clé `access` de `agent.yaml` et tout ce qu'elle contient.

## `access`

Ce que l'agent exige de qui agit sur lui (décisions D-2 et D-7b des utilisateurs et rôles, `docs/loom_role_user.md` §5.2).

Modèle Pydantic : `loom_ia.agents.spec.AgentAccess`.

- `access.run` (type `tuple[str, ...] | null` ; défaut : `null`) : Rôles qui lancent l'agent.
- `access.answer` (type `tuple[str, ...] | null` ; défaut : `null`) : Rôles qui répondent à ses questions (`loom.ask`).
- `access.cancel` (type `tuple[str, ...] | null` ; défaut : `null`) : Rôles qui arrêtent ses runs.
- `access.approve` (type `ApproveAccess` ; défaut : les défauts de la section) : Qui tranche ses demandes d'approbation.
- `access.self_approval` (type `bool` ; défaut : `true`) : `false` interdit l'auto-approbation sur cet agent, même à un rôle qui a `self_approve` (décision D-7b). L'agent ne fait que durcir : `true` laisse décider les rôles, il n'ouvre rien.

## `access.approve`

Qui tranche les demandes d'approbation d'un agent (décision D-2 des utilisateurs et rôles, `docs/loom_role_user.md` §5.2).

Modèle Pydantic : `loom_ia.agents.spec.ApproveAccess`.

- `access.approve.default` (type `tuple[str, ...] | null` ; défaut : `null`) : Approbateurs de toute demande de l'agent, à défaut d'une liste par outil.
- `access.approve.tools` (type `dict[str, tuple[str, ...]]` ; défaut : `{}`) : Approbateurs par outil, sous le nom que voit le modèle (préfixe MCP compris), comme `tenants[].tools_deny` : c'est le `tool_name` de `approval.requested`.
