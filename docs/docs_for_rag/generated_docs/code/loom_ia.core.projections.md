# loom_ia.core.projections

Paquet `loom_ia.core.projections` de loom_v2 (fichier `src/loom_ia/core/projections/__init__.py`).

Projections pures du journal : état d'un run, arbre des runs, historique, ledger, state.

Le journal fait foi (point 22) ; tout le reste en est déduit ici, sans
entrée-sortie :

- `run_state` : l'état d'un run (`RunState`) rejoué événement par
  événement (`apply`, `fold`) — c'est ce qui permet la reprise après un
  plantage ;
- `history` : l'historique de la session envoyé au modèle, résumés de
  compaction compris ;
- `state` : le state de session ;
- `ledger` : la consommation, appel de modèle par appel ;
- `tree` : l'arbre d'un run et de ses sous-runs, et les résumés faits à
  son ouverture.
