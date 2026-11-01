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
    model_config = ConfigDict(frozen=True, extra="forbid")

    # Sans réponse après ce délai, la question expire et le modèle reprend avec
    # « pas de réponse ». Par défaut, elle attend indéfiniment : la conversation
    # reprend quand l'utilisateur revient.
    expires_in: PositiveFloat | None = None
