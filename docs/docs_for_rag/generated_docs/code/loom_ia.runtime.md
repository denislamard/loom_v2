# loom_ia.runtime

Paquet `loom_ia.runtime` de loom_v2 (fichier `src/loom_ia/runtime/__init__.py`).

Cycle de vie des runs : assemblage de la config, lancement, reprise.

- `wiring` : de la config aux objets qui tournent — `build_agent` (un agent
  prêt à tourner), fabriques des stockages (journal, artefacts, idempotence,
  file, bus, trousseau), politiques, juges, avertissements de chargement ;
- `sources` : sources d'outils fournies par des paquets installés (groupe
  `loom_ia.tools`).

La façade `Loom` (`access/api.py`) assemble une instance à partir d'ici.
