# loom_ia.tools_builtin.word

Paquet `loom_ia.tools_builtin.word` de loom_v2 (fichier `src/loom_ia/tools_builtin/word/__init__.py`).

Outil word : écrire un document .docx mis en forme d'après ses blocs, et lire un .docx.

Le modèle dit **ce que sont** les blocs — titres, paragraphes, listes,
tableaux aux colonnes typées, notes, citations — et jamais à quoi ils
ressemblent : le style maison est dans `writer`. Deux appels identiques
donnent deux fichiers identiques, octet pour octet.

Aucune dépendance : un .docx est un zip de XML, écrit et lu avec la
bibliothèque standard (`zipfile`, expat). Les fichiers sont désignés comme
dans `loom.local_files` et soumis aux mêmes règles (`_document.tool`).
