# SPDX-License-Identifier: Apache-2.0
"""Description des outils (#13, #15, #17, #18).

``ToolDefinition`` est ce que voit le modèle ; ``ToolSpec`` y ajoute ce que
le moteur doit savoir pour exécuter l'outil sans risque.
"""

import json
from datetime import datetime
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


# Une journée : de quoi couvrir une nuit et un jour ouvré, sans qu'un run
# oublié attende pour toujours (#17).
DEFAULT_APPROVAL_DELAY: Final = 24 * 3600.0


type IdempotencyStatus = Literal["in_progress", "completed"]
# Ce qu'il advient d'un appel interrompu dont l'effet est peut-être produit (#18).
type UnknownState = Literal["error", "pause"]

# Au-delà, un résultat n'est pas mémorisé : l'enregistrement échoue avec un
# message à l'auteur de l'outil. Le décorateur agit avant le déport de
# l'exécuteur, donc rien ne réduira ce résultat pour nous.
MAX_RECORDED: Final = 50_000

# Durée pendant laquelle un résultat mémorisé reste consultable, quand ni
# l'outil ni le magasin n'en fixent d'autre. Une journée : au-delà, l'appel
# qui le redemanderait est hors de portée de toute reprise.
DEFAULT_RETENTION: Final = 24 * 3600.0


class IdempotencyRecord(DomainModel):
    """Ce qu'un magasin d'idempotence sait d'une clé (#18, #49).

    ``in_progress`` : quelqu'un a réservé la clé et n'a pas encore fini —
    l'effet est peut-être en train de se produire. ``completed`` : il s'est
    produit, et ``result`` est ce qu'il a rendu.
    """

    key: str
    status: IdempotencyStatus
    # Ce que l'outil avait rendu ; absent tant que la clé est réservée.
    result: JsonValue = None
    expires_at: datetime

    def alive(self, now: datetime) -> bool:
        """Réservation encore tenue : personne d'autre ne doit y toucher."""
        return now < self.expires_at


class ResultTooLarge(ValueError):
    """Résultat trop volumineux pour être mémorisé sous une clé d'idempotence."""


def recordable(result: object) -> JsonValue:
    """Le résultat, prêt à être mémorisé ; refuse ce qui ne l'est pas.

    Mémoriser est un service rendu à l'outil, pas un fourre-tout : le résultat
    voyage vers un magasin partagé et y reste. Au-delà de ``MAX_RECORDED``
    caractères il est refusé, bruyamment, à l'auteur de l'outil — un gros
    résultat se range dans un artefact, et c'est sa référence qui se mémorise.

    Un résultat que JSON ne porte pas lève ``TypeError`` : le message du
    module ``json`` nomme déjà le type fautif.
    """
    encoded = json.dumps(result, ensure_ascii=False)
    if len(encoded) > MAX_RECORDED:
        raise ResultTooLarge(
            f"Résultat de {len(encoded)} caractères non mémorisé : "
            f"la limite est de {MAX_RECORDED}. Rangez-le dans un artefact et "
            f"mémorisez sa référence."
        )
    # json.dumps a déjà prouvé que la valeur est du JSON.
    return result  # pyright: ignore[reportReturnType]


class ApprovalSettings(DomainModel):
    """Ce qu'un agent fait des approbations qu'il demande (#17, #28)."""

    # Délai laissé à l'approbateur. ``null`` le retire — et une demande attend
    # alors indéfiniment, ce qui est un choix, pas un défaut : rien d'autre ne
    # borne cette attente, ni le délai de l'agent (qui ne compte que le
    # pilotage) ni la reprise (qui ne fait que reconstater l'attente).
    expires_in: PositiveFloat | None = DEFAULT_APPROVAL_DELAY
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
    # Sort d'un appel repris dont l'effet est peut-être produit (#18) : le
    # dire au modèle, ou suspendre le run pour qu'un humain vérifie.
    on_unknown: UnknownState = "error"
    # L'outil mémorise ses effets sous une clé **métier**, donc visible d'un
    # run à l'autre (#49). Il lui faut alors un magasin partagé et durable,
    # ce que le chargement vérifie : le journal d'un run ne l'est pas.
    business_key: bool = False
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
