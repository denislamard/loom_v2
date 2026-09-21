# SPDX-License-Identifier: Apache-2.0
"""Description des outils (#13, #15, #17, #18).

``ToolDefinition`` est ce que voit le modèle ; ``ToolSpec`` y ajoute ce que
le moteur doit savoir pour exécuter l'outil sans risque.
"""

from typing import Final, Literal

from pydantic import Field, JsonValue, PositiveFloat, PositiveInt

from loom_ia.core.model.base import DomainModel
from loom_ia.core.model.contract import OutputContract

type ToolKind = Literal["python", "mcp", "role", "agent", "builtin"]
type SideEffects = Literal["none", "reversible", "irreversible"]
type Approval = Literal["never", "always", "policy"]
# Ce qu'il advient d'une demande d'approbation laissée sans réponse (#17).
type ExpiryAction = Literal["deny", "fail"]

# Contrainte commune aux API des fournisseurs sur les noms d'outils.
TOOL_NAME_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"

# Nom du rôle orchestrateur (C6) : réservé, porté par ses événements de modèle.
MAIN_ROLE: Final = "main"

# Mention ajoutée à la description d'un outil terminal (#13).
TERMINAL_HINT: Final = "À appeler seul : sa sortie est la réponse finale, transmise telle quelle."


def _empty_object_schema() -> dict[str, JsonValue]:
    return {"type": "object", "properties": {}}


class ApprovalSettings(DomainModel):
    """Ce qu'un agent fait des approbations qu'il demande (#17, #28)."""

    # Délai laissé à l'approbateur ; sans lui, une demande attend indéfiniment.
    expires_in: PositiveFloat | None = None
    # Ce qu'il advient d'une demande périmée : un « non » prudent par défaut,
    # personne n'ayant dit oui.
    on_expiry: ExpiryAction = "deny"
    # Droit exigé de l'approbateur, côté REST (N2).
    scope: str = "approve"


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
    offload_over: PositiveInt | None = None
    # Contrat de sortie propre à cet outil (E5).
    output: OutputContract | None = None


class ToolSpec(ToolDefinition):
    """Définition complétée des déclarations utiles au moteur."""

    kind: ToolKind
    side_effects: SideEffects = "none"
    approval: Approval = "never"
    idempotent: bool = False
    # Timeout propre à l'outil ; sinon celui de l'exécuteur.
    timeout: PositiveFloat | None = None
    # Taille en caractères au-delà de laquelle le résultat est déporté (#16) ;
    # sinon le seuil de l'exécuteur.
    offload_over: PositiveInt | None = None
    # Sortie transmise telle quelle comme réponse finale s'il est seul dans son tour (#13).
    terminal: bool = False
    # Contrat de sortie (E5) : contrôlé après chaque appel ; un rôle se répare, un outil non.
    output: OutputContract | None = None

    @property
    def safe_to_retry(self) -> bool:
        """Vrai si une réexécution après interruption est sans risque (#18)."""
        return self.side_effects == "none" or self.idempotent

    def overridden(self, overrides: ToolOverrides | None) -> ToolSpec:
        """Déclarations remplacées par celles que ``overrides`` renseigne."""
        if overrides is None:
            return self
        # Valeurs prises telles quelles (pas de dump) : le contrat reste un modèle.
        changes = {
            name: value
            for name in type(overrides).model_fields
            if (value := getattr(overrides, name)) is not None
        }
        return self.model_copy(update=changes) if changes else self

    def definition(self) -> ToolDefinition:
        """Ce que voit le modèle ; un outil terminal le dit dans sa description."""
        description = self.description
        if self.terminal:
            description = f"{description}\n\n{TERMINAL_HINT}" if description else TERMINAL_HINT
        return ToolDefinition(
            name=self.name, description=description, input_schema=self.input_schema
        )
