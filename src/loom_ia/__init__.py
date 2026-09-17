# SPDX-License-Identifier: Apache-2.0
"""loom-ia : moteur d'agents IA."""

import logging

# Une librairie ne configure jamais les handlers de l'application hôte (#45).
# Le NullHandler évite que Python affiche les messages de loom-ia sur stderr
# (handler de dernier recours) quand l'application n'a rien configuré.
logging.getLogger(__name__).addHandler(logging.NullHandler())
