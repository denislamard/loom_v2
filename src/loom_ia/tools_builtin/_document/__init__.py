# SPDX-License-Identifier: Apache-2.0
"""Le socle commun de ``loom.word`` et ``loom.pdf`` : arguments, contenu validé, outil monté.

Un document est décrit par ses blocs sémantiques (``arguments``) ; l'outil le
valide et met ses valeurs en forme (``content``), puis chaque format le pose
sur la page. Aucune dépendance hors de la bibliothèque standard et de Pydantic :
``word`` n'en a pas d'autre, ``pdf`` apporte les siennes par l'extra ``pdf``.
"""
