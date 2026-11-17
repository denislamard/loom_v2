# SPDX-License-Identifier: Apache-2.0
"""Paramètres de ``loom.ask`` : le délai laissé à l'utilisateur.

    tools:
    - python: loom.ask
      expires_in: 86400        # facultatif : sans réponse après ce délai (s), le modèle l'apprend

Module léger — Pydantic seulement — : la config l'importe pour valider la
référence au chargement, sans importer l'outil.
"""

from pydantic import BaseModel, ConfigDict, PositiveFloat


class AskParams(BaseModel):
    """Paramètres de ``loom.ask``.

    Attributes:
        expires_in: Délai, en secondes, après lequel une question sans réponse
            expire et le modèle reprend avec « pas de réponse ». Par défaut,
            elle attend indéfiniment : la conversation reprend quand
            l'utilisateur revient.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    expires_in: PositiveFloat | None = None
