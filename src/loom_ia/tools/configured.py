# SPDX-License-Identifier: Apache-2.0
"""Surcharge des déclarations d'un outil par la config (#50).

Un outil déclare lui-même ses effets de bord, son idempotence, son délai et
son seuil de déport ; la config peut les remplacer, et lui donner un contrat
de sortie, sans toucher au code de l'outil.

Un outil qui évalue chacun de ses appels (``assess_call``) garde cette
évaluation sous l'enveloppe : sans elle, régler son délai suffirait à lui
faire perdre ses approbations par appel. Ce que la config fixe —
approbation, effets, idempotence — l'emporte, champ par champ, sur ce que
l'outil évalue.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from pydantic import JsonValue

from loom_ia.core.model import Approval, OutputContract, SideEffects, ToolOutput, ToolSpec
from loom_ia.core.ports import CallAssessment, Tool, ToolContext, assessed

# Déclarations qu'une évaluation par appel peut donner, et que la config peut fixer.
ASSESSED: Final = frozenset({"approval", "side_effects", "idempotent"})


@dataclass(frozen=True, slots=True)
class ConfiguredTool:
    """Outil dont la déclaration a été modifiée par la config."""

    tool: Tool
    spec: ToolSpec
    # Celles de ``ASSESSED`` que la config a données : elles valent pour tous les appels.
    fixed: frozenset[str] = frozenset()

    async def invoke(self, arguments: dict[str, JsonValue], context: ToolContext) -> ToolOutput:
        return await self.tool.invoke(arguments, context)

    def assess_call(self, arguments: Mapping[str, JsonValue]) -> CallAssessment | None:
        """L'évaluation de l'outil enveloppé, corrigée de ce que la config fixe."""
        found = assessed(self.tool, arguments)
        if found is None:
            return None
        fixed = self.fixed & ASSESSED
        if not fixed:
            return found
        imposed = "approval" in fixed
        # ``policy`` laisse les politiques décider : pas d'approbation d'office.
        approval = ("always" if self.spec.approval == "always" else "never") if imposed else None
        return CallAssessment(
            approval=found.approval if approval is None else approval,
            side_effects=self.spec.side_effects if "side_effects" in fixed else found.side_effects,
            idempotent=self.spec.idempotent if "idempotent" in fixed else found.idempotent,
            # Fixée par la config, l'approbation dit d'où elle vient, pas le motif de
            # l'outil (test D7 du 18/10 : le motif doit nommer la bonne source).
            reason=(
                ("outil déclaré à approbation obligatoire" if approval == "always" else None)
                if imposed
                else found.reason
            ),
        )


def configure(
    tool: Tool,
    *,
    timeout: float | None = None,
    side_effects: SideEffects | None = None,
    approval: Approval | None = None,
    idempotent: bool | None = None,
    offload_over: int | None = None,
    output: OutputContract | None = None,
) -> Tool:
    """Applique les valeurs données ; ``None`` garde ce que l'outil déclare."""
    changes = {
        "timeout": timeout,
        "side_effects": side_effects,
        "approval": approval,
        "idempotent": idempotent,
        "offload_over": offload_over,
        "output": output,
    }
    applied = {key: value for key, value in changes.items() if value is not None}
    if not applied:
        return tool
    return ConfiguredTool(
        tool=tool,
        spec=tool.spec.model_copy(update=applied),
        fixed=frozenset(applied) & ASSESSED,
    )
