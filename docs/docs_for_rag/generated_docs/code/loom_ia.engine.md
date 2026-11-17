# loom_ia.engine

Paquet `loom_ia.engine` de loom_v2 (fichier `src/loom_ia/engine/__init__.py`).

Moteur d'exécution : boucle d'un run, outils, rôles délégués, sous-agents, fichiers (point 3).

Modules :

- `loop` : `begin_run`, `step` et `drive`, la boucle d'un run ;
  `RunContext`, les dépendances d'un agent ; annulation et délai ;
- `executor` : `ToolExecutor`, les outils d'un run et l'exécution d'un
  lot d'appels (ordre, approbations, questions, politiques d'outils, state,
  fichiers, déport) ;
- `model_call` et `fallback` : appel d'un modèle (tentatives, délais,
  fenêtre) et chaîne de secours ; `circuit` : disjoncteurs ; `exchange` :
  échanges bruts gardés ;
- `hooks` : évaluation des politiques aux points d'accroche ;
- `delegated`, `roles`, `subagents` : outils délégués, rôles et
  sous-agents ;
- `refs` : références `$ref` aux résultats ; `offload` : déport des
  gros résultats et `artifact_read` ; `media` : fichiers dans les
  requêtes ;
- `session_state` : state de la session ; `questions`, `pending`,
  `amendments` : questions à l'utilisateur, actions en attente, arguments
  changés ; `transcript` : historique rendu en texte ;
- `writer` : écrivain d'une session ; `idempotency` : magasin
  d'idempotence adossé au journal.
