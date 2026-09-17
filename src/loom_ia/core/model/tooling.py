# SPDX-License-Identifier: Apache-2.0
"""Description des outils (#15, #17, #18).

``ToolDefinition`` est ce que voit le modèle ; ``ToolSpec`` y ajoute ce que
le moteur doit savoir pour exécuter l'outil sans risque.
"""

from typing import Literal

from pydantic import Field, JsonValue, PositiveFloat

from loom_ia.core.model.base import DomainModel

type ToolKind = Literal["python", "mcp", "role", "agent", "builtin"]
type SideEffects = Literal["none", "reversible", "irreversible"]
type Approval = Literal["never", "always", "policy"]

# Contrainte commune aux API des fournisseurs sur les noms d'outils.
TOOL_NAME_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"


def _empty_object_schema() -> dict[str, JsonValue]:
    return {"type": "object", "properties": {}}


class ToolDefinition(DomainModel):
    """Ce que le modèle voit d'un outil."""

    name: str = Field(pattern=TOOL_NAME_PATTERN)
    description: str
    input_schema: dict[str, JsonValue] = Field(default_factory=_empty_object_schema)


class ToolSpec(ToolDefinition):
    """Définition complétée des déclarations utiles au moteur."""

    kind: ToolKind
    side_effects: SideEffects = "none"
    approval: Approval = "never"
    idempotent: bool = False
    # Timeout propre à l'outil ; sinon celui de l'exécuteur.
    timeout: PositiveFloat | None = None

    @property
    def safe_to_retry(self) -> bool:
        """Vrai si une réexécution après interruption est sans risque (#18)."""
        return self.side_effects == "none" or self.idempotent

    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=self.name, description=self.description, input_schema=self.input_schema
        )
