# SPDX-License-Identifier: Apache-2.0
"""Outil memory : une mémoire long terme où l'agent cherche et range des documents.

Recherche hybride (dense et lexicale, puis reranking) sur une base SQLite,
avec les modèles BGE-M3 (extra ``memory``) ou des modèles factices. Le modèle
agit par une ``action`` parmi celles que la config permet ; les écritures
attendent une approbation humaine par défaut. Ses paramètres sont dans
``memory_params``, module léger que la config importe seul ; ce paquet n'est
importé qu'au montage d'un agent qui active l'outil.
"""

from loom_ia.tools_builtin.memory.arguments import NAME
from loom_ia.tools_builtin.memory.tool import MemoryTool, build

__all__ = ["NAME", "MemoryTool", "build"]
