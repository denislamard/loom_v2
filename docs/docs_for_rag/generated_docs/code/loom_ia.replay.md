# loom_ia.replay

Paquet `loom_ia.replay` de loom_v2 (fichier `src/loom_ia/replay/__init__.py`).

Rejeu d'un run depuis son journal (fonction K6, point 31).

À l'identique (jalon J6.2a) : la logique de loom retourne, le monde est servi par le
journal, et la première divergence est dite — quel appel, et quelle partie de
sa requête a changé.

En variante (jalon J6.2b) : un autre modèle ou une autre config ; ce que le journal
connaît est servi, le reste part pour de vrai — jamais un outil à effets de
bord —, et le rapport compare les deux runs.

Les évals (jalon J6.3a) vivent ici aussi : une suite de cas joués par variante, des
contrôles et un juge, le même monde qu'en variante pour les outils à effets de
bord — doublés ou refusés, jamais exécutés.

La non-régression (jalon J6.3b) rejoue à l'identique les runs d'un journal exporté
(`read_journal`) — une session, ou le run d'une éval — : la config
d'aujourd'hui doit les reproduire requête par requête.
