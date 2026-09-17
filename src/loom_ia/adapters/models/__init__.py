# SPDX-License-Identifier: Apache-2.0
"""Adaptateurs de modèles : ``anthropic``, ``openai`` (``chat``) et ``fake`` (#9).

Ce package n'importe aucun SDK : chaque adaptateur le charge à la demande.
"""

from loom_ia.adapters.models.factory import NO_API_KEY, ModelConfigError, create_model_client

__all__ = ["NO_API_KEY", "ModelConfigError", "create_model_client"]
