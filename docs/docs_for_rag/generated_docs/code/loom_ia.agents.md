# loom_ia.agents

Paquet `loom_ia.agents` de loom_v2 (fichier `src/loom_ia/agents/__init__.py`).

Agents : définition et registre (fonction A9).

- `spec` : `AgentSpec` et ses blocs, tels que la config les déclare sous
  `agents.<nom>` (rôle `main`, rôles, juges, outils, politiques, guards,
  state, budgets, accès, exposition) ;
- `registry` : `AgentRegistry`, les agents d'une instance.
