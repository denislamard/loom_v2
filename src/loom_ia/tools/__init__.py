# SPDX-License-Identifier: Apache-2.0
"""Outils fournis aux agents : fonctions Python (fonction D1), images produites (fonction G3).

- ``python`` : ``@tool`` et ``FunctionTool``, un outil tiré d'une fonction
  (schéma depuis la signature, description depuis la docstring) ; ``Image`` ;
- ``idempotent`` : ``@idempotent``, un effet produit une seule fois par clé ;
- ``configured`` : les déclarations d'un outil remplacées par la config.

Les outils fournis par loom-ia (``loom.<outil>``) sont dans ``tools_builtin``.
"""

from loom_ia.tools.configured import ConfiguredTool, configure, innermost
from loom_ia.tools.idempotent import DEFAULT_RESERVATION, IdempotentTool, idempotent
from loom_ia.tools.python import FunctionTool, Image, to_output, tool

__all__ = [
    "DEFAULT_RESERVATION",
    "ConfiguredTool",
    "FunctionTool",
    "IdempotentTool",
    "Image",
    "configure",
    "idempotent",
    "innermost",
    "to_output",
    "tool",
]
