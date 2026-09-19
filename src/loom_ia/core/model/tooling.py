# SPDX-License-Identifier: Apache-2.0
"""Description des outils (#13, #15, #17, #18).

``ToolDefinition`` est ce que voit le modèle ; ``ToolSpec`` y ajoute ce que
le moteur doit savoir pour exécuter l'outil sans risque.
"""

from typing import Final, Literal

from pydantic import Field, JsonValue, PositiveFloat

from loom_ia.core.model.base import DomainModel

type ToolKind = Literal["python", "mcp", "role", "agent", "builtin"]
type SideEffects = Literal["none", "reversible", "irreversible"]
type Approval = Literal["never", "always", "policy"]

# Contrainte commune aux API des fournisseurs sur les noms d'outils.
TOOL_NAME_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"

# Nom du rôle orchestrateur (C6) : réservé, porté par ses événements de modèle.
MAIN_ROLE: Final = "main"

# Mention ajoutée à la description d'un outil terminal (#13).
TERMINAL_HINT: Final = "À appeler seul : sa sortie est la réponse finale, transmise telle quelle."


def _empty_object_schema() -> dict[str, JsonValue]:
    return {"type": "object", "properties": {}}


class ToolDefinition(DomainModel):
    """Ce que le modèle voit d'un outil."""

    name: str = Field(pattern=TOOL_NAME_PATTERN)
    description: str
    input_schema: dict[str, JsonValue] = Field(default_factory=_empty_object_schema)


class ToolOverrides(DomainModel):
    """Déclarations d'un outil fixées par la config ; ``None`` garde celle de l'outil."""

    side_effects: SideEffects | None = None
    approval: Approval | None = None
    idempotent: bool | None = None
    timeout: PositiveFloat | None = None


class ToolSpec(ToolDefinition):
    """Définition complétée des déclarations utiles au moteur."""

    kind: ToolKind
    side_effects: SideEffects = "none"
    approval: Approval = "never"
    idempotent: bool = False
    # Timeout propre à l'outil ; sinon celui de l'exécuteur.
    timeout: PositiveFloat | None = None
    # Sortie transmise telle quelle comme réponse finale s'il est seul dans son tour (#13).
    terminal: bool = False

    @property
    def safe_to_retry(self) -> bool:
        """Vrai si une réexécution après interruption est sans risque (#18)."""
        return self.side_effects == "none" or self.idempotent

    def overridden(self, overrides: ToolOverrides | None) -> ToolSpec:
        """Déclarations remplacées par celles que ``overrides`` renseigne."""
        if overrides is None:
            return self
        changes = overrides.model_dump(exclude_none=True)
        return self.model_copy(update=changes) if changes else self

    def definition(self) -> ToolDefinition:
        """Ce que voit le modèle ; un outil terminal le dit dans sa description."""
        description = self.description
        if self.terminal:
            description = f"{description}\n\n{TERMINAL_HINT}" if description else TERMINAL_HINT
        return ToolDefinition(
            name=self.name, description=description, input_schema=self.input_schema
        )
