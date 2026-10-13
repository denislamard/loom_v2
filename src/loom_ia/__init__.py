# SPDX-License-Identifier: Apache-2.0
"""loom-ia : moteur d'agents IA."""

import logging
from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("loom-ia")
except PackageNotFoundError:  # pragma: no cover - dépend de l'installation
    # Sources non installées. Seule lecture de la version : l'API REST, le
    # serveur MCP et le client MCP la prennent ici.
    __version__ = "0"

# Une librairie ne configure jamais les handlers de l'application hôte (#45).
# Le NullHandler évite que Python affiche les messages de loom-ia sur stderr
# (handler de dernier recours) quand l'application n'a rien configuré.
logging.getLogger(__name__).addHandler(logging.NullHandler())
