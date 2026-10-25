# SPDX-License-Identifier: Apache-2.0
"""Outils fournis par loom-ia, référencés par leur nom ``loom.…`` ; aucun n'est actif d'office.

Un agent en active un en le listant dans ses outils : ``tools: [{python: loom.<outil>}]``.
Le modèle le voit sous son nom sans le préfixe ; les réglages de l'agent (``timeout``,
``approval``…) s'y appliquent comme à tout outil Python.

Un module par outil, au nom de l'outil. ``BUILTIN_TOOLS`` les recense sans les
importer : le module d'un outil, et ses dépendances, ne sont chargés que lorsqu'un
agent l'active.
"""

import importlib
from collections.abc import Mapping
from typing import Final

from loom_ia.core.model import RESERVED_PREFIX
from loom_ia.core.ports import Tool

# Référence ``loom.<outil>`` → ``module:attr`` de l'outil.
BUILTIN_TOOLS: Final[Mapping[str, str]] = {
    "loom.current_datetime": "loom_ia.tools_builtin.current_datetime:current_datetime",
    "loom.fetch_page": "loom_ia.tools_builtin.fetch_page:fetch_page",
}


def is_builtin(reference: str) -> bool:
    """Vrai si la référence désigne un outil fourni : ``loom.<outil>``.

    ``loom.x:y`` reste un chemin d'import : celui d'un paquet à soi nommé ``loom``.
    """
    return reference.startswith(RESERVED_PREFIX) and ":" not in reference


def builtin_tool(reference: str) -> Tool:
    """L'outil fourni ``reference``, son module importé à ce moment-là.

    ``KeyError`` s'il n'est pas au catalogue, ``ImportError`` si une dépendance
    de son module manque.
    """
    module_name, _, attribute = BUILTIN_TOOLS[reference].partition(":")
    found: object = getattr(importlib.import_module(module_name), attribute)
    if not isinstance(found, Tool):
        raise TypeError(f"{reference} : {BUILTIN_TOOLS[reference]} n'est pas un outil")
    return found
