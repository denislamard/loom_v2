# SPDX-License-Identifier: Apache-2.0
"""JSON Schema de la config, pour l'éditeur (#35).

loom-ia ne lit jamais ce schéma : il valide avec Pydantic. Le fichier sert à
la complétion et aux erreurs dans l'éditeur :

    # yaml-language-server: $schema=./loom.schema.json
"""

from typing import Any

from pydantic import TypeAdapter

from loom_ia.agents.spec import AgentSpec
from loom_ia.config.models import LoomConfig


def config_json_schema() -> dict[str, Any]:
    """Schéma du fichier racine ``loom.yaml``."""
    return TypeAdapter(LoomConfig).json_schema(mode="validation")


def agent_json_schema() -> dict[str, Any]:
    """Schéma d'un fichier d'agent (``agents/*.yaml``)."""
    return TypeAdapter(AgentSpec).json_schema(mode="validation")
