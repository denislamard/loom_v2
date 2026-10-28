# SPDX-License-Identifier: Apache-2.0
"""Paramètres de ``loom.state`` : aucun.

Ce que l'outil écrit se déclare sur les clés du state de l'agent
(``writers: [model]`` et ``extract:``), pas sur sa référence :

    tools:
    - python: loom.state

Module léger — Pydantic seulement — : la config l'importe pour valider la
référence au chargement, sans importer l'outil.
"""

from pydantic import BaseModel, ConfigDict


class StateParams(BaseModel):
    """Aucun paramètre : une clé inconnue est refusée."""

    model_config = ConfigDict(frozen=True, extra="forbid")
