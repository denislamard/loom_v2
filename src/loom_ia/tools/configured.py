# SPDX-License-Identifier: Apache-2.0
"""Surcharge des déclarations d'un outil par la config (#50).

Un outil déclare lui-même ses effets de bord, son idempotence, son délai et
son seuil de déport ; la config peut les remplacer, sans toucher au code de
l'outil.
"""

from dataclasses import dataclass

from pydantic import JsonValue

from loom_ia.core.model import Approval, SideEffects, ToolOutput, ToolSpec
from loom_ia.core.ports import Tool, ToolContext


@dataclass(frozen=True, slots=True)
class ConfiguredTool:
    """Outil dont la déclaration a été modifiée par la config."""

    tool: Tool
    spec: ToolSpec

    async def invoke(self, arguments: dict[str, JsonValue], context: ToolContext) -> ToolOutput:
        return await self.tool.invoke(arguments, context)


def configure(
    tool: Tool,
    *,
    timeout: float | None = None,
    side_effects: SideEffects | None = None,
    approval: Approval | None = None,
    idempotent: bool | None = None,
    offload_over: int | None = None,
) -> Tool:
    """Applique les valeurs données ; ``None`` garde ce que l'outil déclare."""
    changes = {
        "timeout": timeout,
        "side_effects": side_effects,
        "approval": approval,
        "idempotent": idempotent,
        "offload_over": offload_over,
    }
    applied = {key: value for key, value in changes.items() if value is not None}
    if not applied:
        return tool
    return ConfiguredTool(tool=tool, spec=tool.spec.model_copy(update=applied))
