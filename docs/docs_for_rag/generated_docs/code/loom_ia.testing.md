# loom_ia.testing

Paquet `loom_ia.testing` de loom_v2 (fichier `src/loom_ia/testing/__init__.py`).

Kit de test de loom-ia : faux modèles, faux outils, journaux fictifs (fonction O2).

`Bench` (jalon J6.3c) met un agent de sa config au banc d'essai : faux modèles par
identifiant, faux outils par nom, les contrôles d'un cas d'éval.
`assert_replays` (jalon J6.3b) rejoue des journaux enregistrés : la non-régression
d'une config se teste comme le reste.
