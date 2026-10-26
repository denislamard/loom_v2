# SPDX-License-Identifier: Apache-2.0
"""Outil excel : écrire un classeur .xlsx mis en forme, d'après une description sémantique.

Le modèle dit **ce que sont** les données — onglets, colonnes et leur type,
rôle des lignes (saisie, hypothèse, total…), formules — et jamais à quoi
elles ressemblent : police, couleurs, formats de nombre, largeurs, volets
gelés et filtre viennent du style maison (``workbook``). Deux appels
identiques donnent deux fichiers identiques, octet pour octet.

Les fichiers lus (CSV, JSON) et écrits (.xlsx) sont désignés comme dans
``loom.local_files`` — ``<dossier>/<chemin>`` — et soumis aux mêmes règles,
celles de ``_folders`` : dossiers déclarés, droits, approbation par appel,
liens qui sortent du dossier refusés, écriture atomique, fichier existant
remplacé seulement avec ``overwrite``.

Avant d'écrire, la description est validée sans dépendance (``arguments``,
``values``, ``formulas``). Avec ``recalc``, LibreOffice recalcule une copie et
toute formule en erreur fait échouer l'appel (``recalc``).

openpyxl vient de l'extra ``office`` ; ce paquet n'est importé qu'au montage
d'un agent qui active l'outil. Ses paramètres sont dans ``excel_params``,
module léger que la config importe seul.
"""

from loom_ia.tools_builtin.excel.arguments import NAME
from loom_ia.tools_builtin.excel.tool import ExcelTool, build

__all__ = ["NAME", "ExcelTool", "build"]
