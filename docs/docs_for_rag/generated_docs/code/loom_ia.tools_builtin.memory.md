# loom_ia.tools_builtin.memory

Paquet `loom_ia.tools_builtin.memory` de loom_v2 (fichier `src/loom_ia/tools_builtin/memory/__init__.py`).

Outil memory : une mémoire long terme où l'agent cherche et range des documents.

Recherche hybride (dense et lexicale, puis reranking) sur une base SQLite,
avec les modèles BGE-M3 (extra `memory`) ou des modèles factices. Le modèle
agit par une `action` parmi celles que la config permet ; les écritures
attendent une approbation humaine par défaut. Ses paramètres sont dans
`memory_params`, module léger que la config importe seul ; ce paquet n'est
importé qu'au montage d'un agent qui active l'outil.
