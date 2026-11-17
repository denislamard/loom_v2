# agent.yaml : les clés de premier niveau

Un agent (fonction A9) : son orchestrateur, ses outils, rôles et sous-agents, ses contrôles et réglages.

Modèle Pydantic : `loom_ia.agents.spec.AgentSpec`.

- `name` (type `str` ; obligatoire ; bornes : motif `^[A-Za-z0-9_-]{1,64}$`) : Nom de l'agent : lettres, chiffres, `_` et `-`, 64 au plus. Un nom qui commence par `_` est réservé aux agents internes.
- `description` (type `str` ; défaut : `""`) : Description de l'agent (sous-agent, outil MCP, `/v1/agents`).
- `expose` (type `Expose` ; défaut : les défauts de la section (`rest: true`, `mcp: true`)) : Points d'accès qui le publient.
- `main` (type `MainRole` ; obligatoire) : L'orchestrateur : modèle, secours, prompt, `llm`, `context`.
- `max_iterations` (type `int` ; défaut : `10` ; bornes : > 0) : Appels de l'orchestrateur avant la réponse forcée sans outils (10 par défaut).
- `timeout` (type `float | null` ; défaut : `null`) : Délai maximal d'un run, en secondes (fonction A6, backlog #006). Il borne le temps de pilotage cumulé, pas l'horloge : l'attente en file, une pause et le temps entre un plantage et sa reprise ne comptent pas. Un dépassement écrit `run.failed` avec `error_type: timeout` : c'est une clôture, que `resume` ne relance pas, même avec un délai relevé. Sans valeur, pas de délai.
- `approval` (type `ApprovalSettings` ; défaut : les défauts de la section (`expires_in: 86400.0`, `on_expiry: "deny"`)) : Approbations de l'agent (point 17) : délai laissé à l'approbateur (`expires_in`), effet d'une demande périmée (`on_expiry`). Un outil les demande par sa déclaration (`approval: always`) ou par une politique ; qui les tranche, c'est `access.approve`.
- `access` (type `AgentAccess` ; défaut : les défauts de la section (`approve.tools: {}`, `self_approval: true`)) : Qui lance, répond, arrête et approuve (décision D-2 des utilisateurs et rôles) : des rôles d'accès de loom.yaml.
- `output` (type `OutputContract | null` ; défaut : `null`) : Contrat de la réponse finale (fonctions A7 et E1) : réparée par l'orchestrateur.
- `judge` (type `JudgeSpec | null` ; défaut : `null`) : Juge de la réponse finale (fonction E3, point 21), après son contrat.
- `budget` (type `Budgets | null` ; défaut : `null`) : Budgets de l'agent (fonction J4 ; `run`, `session`, `on_exceed`) : surchargent ceux de la racine (`budgets`), clé par clé.
- `stream_output` (type `StreamOutput | null` ; défaut : `null`) : Diffusion de la réponse finale ; par défaut `after_guards` si elle est contrôlée (contrat, juge, politique `on_output`, rôle terminal sous contrat ou jugé), sinon `live` (point 11).
- `tools` (type `tuple[PythonTool | Builtin_local_files | Builtin_excel | Builtin_word | Builtin_pdf | Builtin_sql | Builtin_memory | Builtin_state | Builtin_ask | McpTools | SourceTools, ...]` ; défaut : `[]`) : Outils Python (`python`), serveurs MCP (`mcp`) et sources d'outils (`source`), dans l'ordre de déclaration.
- `roles` (type `tuple[RoleSpec, ...]` ; défaut : `[]`) : Rôles délégués.
- `subagents` (type `tuple[SubAgentRef, ...]` ; défaut : `[]`) : Sous-agents.
- `policies` (type `tuple[PolicyRef, ...]` ; défaut : `[]`) : Politiques, dans l'ordre d'exécution à chaque point (point 2).
- `max_depth` (type `int` ; défaut : `1` ; bornes : > 0) : Profondeur maximale d'un run de cet agent pour appeler ses sous-agents : à 1, un run racine les appelle, mais un sous-run de cet agent ne le peut pas.
- `state` (type `dict[str, StateKey]` ; défaut : `{}`) : State de la session (option C) : les clés qu'elle peut porter (décision D6 du state de session).
- `execution` (type `AgentExecution` ; défaut : les défauts de la section) : Exécution des outils : remplace, clé par clé, `execution.tools` de loom.yaml.
