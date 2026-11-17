# loom_ia.guards

Paquet `loom_ia.guards` de loom_v2 (fichier `src/loom_ia/guards/__init__.py`).

Guards : contrôles des sorties, branchés comme des politiques (fonctions E1 à E6, points 20 et 21).

- `contract` : `loom.contract`, contrats de sortie (schéma, motifs,
  longueurs), réponse finale, rôles et outils ;
- `judge` : `loom.judge.<nom>`, juges LLM à critères notés ;
- `fidelity` : `loom.fidelity`, repères d'un résumé de compaction.

Le montage les place en tête des politiques de l'agent : contrats, puis
juges, puis budget.
