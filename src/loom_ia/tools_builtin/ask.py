# SPDX-License-Identifier: Apache-2.0
"""Outil fourni ``loom.ask`` : poser une question à l'utilisateur.

Le moteur ne l'exécute pas : il lit les questions dans les arguments, les
écrit au journal (``question.asked``) et met le run en pause jusqu'à la
réponse de l'utilisateur, qui revient au modèle comme résultat de l'appel.
``invoke`` n'est donc jamais appelé par le moteur ; il refuse s'il l'est.

Ce n'est pas une demande d'autorisation : une action à faire valider passe
par l'approbation (``approval``), que la config décide.
"""

from typing import Final

from pydantic import BaseModel, JsonValue

from loom_ia.core.model import ToolOutput, ToolSpec
from loom_ia.core.model.question import MAX_HEADER, MAX_OPTIONS, MAX_QUESTIONS
from loom_ia.core.model.tooling import AskSettings
from loom_ia.core.ports import ToolContext, ToolError
from loom_ia.tools_builtin import BuiltinContext
from loom_ia.tools_builtin.ask_params import AskParams

NAME: Final = "ask"

DESCRIPTION: Final = (
    "Pose une ou plusieurs questions à l'utilisateur et attend sa réponse, qui te revient "
    "comme résultat de cet appel. À utiliser quand une information indispensable manque "
    "et qu'aucun autre outil ne peut la donner (un choix entre plusieurs voies, une "
    "précision, une préférence) ; ne pas écrire la question dans ta réponse. Propose des "
    "choix ('options') quand les réponses possibles sont connues ; l'utilisateur peut "
    "toujours répondre autre chose. Pas pour faire valider une action : ce n'est pas une "
    "autorisation."
)

SCHEMA: Final[dict[str, JsonValue]] = {
    "type": "object",
    "properties": {
        "questions": {
            "type": "array",
            "minItems": 1,
            "maxItems": MAX_QUESTIONS,
            "items": {
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "La question, complète."},
                    "header": {
                        "type": "string",
                        "maxLength": MAX_HEADER,
                        "description": "Intitulé court (« Client », « Date »).",
                    },
                    "options": {
                        "type": "array",
                        "maxItems": MAX_OPTIONS,
                        "items": {
                            "type": "object",
                            "properties": {
                                "label": {"type": "string"},
                                "description": {"type": "string"},
                            },
                            "required": ["label"],
                            "additionalProperties": False,
                        },
                    },
                    "multiple": {"type": "boolean", "description": "Plusieurs choix permis."},
                },
                "required": ["question"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["questions"],
    "additionalProperties": False,
}


class AskTool:
    """La déclaration de ``ask`` ; le moteur fait le reste."""

    def __init__(self, expires_in: float | None) -> None:
        self._spec = ToolSpec(
            name=NAME,
            description=DESCRIPTION,
            input_schema=SCHEMA,
            kind="python",
            side_effects="none",
            idempotent=True,
            asks=AskSettings(expires_in=expires_in),
        )

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    async def invoke(self, arguments: dict[str, JsonValue], context: ToolContext) -> ToolOutput:
        raise ToolError("ask ne s'exécute pas : le moteur pose la question et attend la réponse.")


def build(params: BaseModel, context: BuiltinContext) -> AskTool:
    if not isinstance(params, AskParams):
        raise TypeError(f"paramètres de loom.{NAME} attendus, reçu {type(params).__name__}")
    return AskTool(params.expires_in)
