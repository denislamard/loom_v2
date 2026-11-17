# SPDX-License-Identifier: Apache-2.0
"""Description des outils : ce que voit le modèle, et ce que le moteur doit savoir (points 13, 15, 17 et 18, fonctions D1 et D10).

``ToolDefinition`` est ce que voit le modèle ; ``ToolSpec`` y ajoute ce que
le moteur doit savoir pour exécuter l'outil sans risque : effets, approbation,
idempotence, délai, déport, contrat, mode d'emploi. ``ToolOverrides`` porte ce
que la config déclare sur un outil (sous ``tools`` d'un agent ou d'un serveur
MCP), et remplace ce que l'outil en dit.

Approbations : ``ApprovalSettings`` (sous ``approval`` d'un agent) règle le
délai et la suite d'une demande sans réponse ; ``Approvers`` fige qui peut la
trancher. Idempotence : ``IdempotencyRecord`` est ce qu'un magasin sait d'une
clé.

Exemple, sous ``tools`` d'un agent ::

    tools:
    - python: outils:envoyer_relance
      side_effects: irreversible
      approval: always
      timeout: 20
      skill: "N'envoie une relance qu'après avoir relu le devis."
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Annotated, Final, Literal, Self

from pydantic import AfterValidator, Field, JsonValue, PositiveFloat, PositiveInt, model_validator

from loom_ia.core.model.base import DomainModel
from loom_ia.core.model.contract import OutputContract

type ToolKind = Literal["python", "mcp", "role", "agent", "builtin"]
"""Nature d'un outil : Python, MCP, rôle délégué, sous-agent, ou outil fourni ``loom.*``."""
type SideEffects = Literal["none", "reversible", "irreversible"]
"""Effets d'un outil : aucun, réversibles ou irréversibles ; ils décident si un appel interrompu peut être refait."""
type Approval = Literal["never", "always", "policy"]
"""Approbation d'un outil : jamais (défaut), à chaque appel, ou selon une politique ``before_tool``."""
type ExpiryAction = Literal["deny", "fail"]
"""Ce qu'il advient d'une demande d'approbation laissée sans réponse (point 17) : refus, ou échec du run."""

EFFECTS_SAID: Final[dict[SideEffects, str]] = {
    "none": "sans effet",
    "reversible": "réversible",
    "irreversible": "irréversible",
}
"""Les effets d'un appel, dits à l'approbateur dans le motif d'une demande."""

EVERY_CALL: Final = "la config exige une approbation pour chaque appel de cet outil"
"""Motif d'une demande pour un outil en ``approval: always`` : la source, que l'approbateur doit pouvoir retrouver (essai de la fonction D7 du 18/10)."""

TOOL_NAME_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"
"""Forme d'un nom d'outil : la contrainte commune aux API des fournisseurs (1 à 64 lettres, chiffres, ``_``, ``-``)."""

MAIN_ROLE: Final = "main"
"""Nom du rôle orchestrateur (fonction C6) : réservé, porté par ses événements de modèle."""

SKILL_SEPARATOR: Final = "\n\n"
"""Ce qui sépare la description d'un outil du skill que la config lui ajoute."""
SKILL_WARN_CHARS: Final = 8_000
"""Taille d'un skill, en caractères, au-delà de laquelle ``loom validate`` et le montage avertissent : il part avec chaque appel de modèle de l'agent."""

TERMINAL_HINT: Final = "À appeler seul : sa sortie est la réponse finale, transmise telle quelle."
"""Mention ajoutée à la description d'un outil terminal, vue par le modèle (point 13)."""


def checked_skill(text: str) -> str:
    """Le skill tel qu'il partira : sans espaces autour, non vide, sans ``{{ }}`` (fonction D14).

    Un skill appartient à la configuration, comme un prompt (conception §6),
    mais il n'est pas rendu avec les variables des clients : un ``{{
    entreprise }}`` partirait tel quel vers le modèle. Il est donc refusé.

    Args:
        text: Le texte du skill.

    Returns:
        Le texte sans espaces autour.

    Raises:
        ValueError: Skill vide, ou qui contient ``{{``.
    """
    stripped = text.strip()
    if not stripped:
        raise ValueError("skill vide")
    if "{{" in stripped:
        raise ValueError("les variables {{ }} ne sont pas rendues dans un skill — écrire le texte voulu")
    return stripped


type SkillText = Annotated[str, AfterValidator(checked_skill)]
"""Texte d'un skill, contrôlé par ``checked_skill``."""


def exclusive_skill(skill: str | None, skill_file: Path | None, owner: str = "") -> None:
    """Refuse ``skill`` et ``skill_file`` donnés ensemble, comme ``system`` et ``system_file``.

    Args:
        skill: Le skill en ligne.
        skill_file: Le fichier du skill.
        owner: Début du message, qui nomme l'outil concerné.

    Raises:
        ValueError: Les deux sont donnés.
    """
    if skill is not None and skill_file is not None:
        raise ValueError(f"{owner}'skill' et 'skill_file' ne peuvent pas être donnés ensemble")


def _empty_object_schema() -> dict[str, JsonValue]:
    """Schéma d'arguments par défaut : un objet sans propriété."""
    return {"type": "object", "properties": {}}


DEFAULT_APPROVAL_DELAY: Final = 24 * 3600.0
"""Délai laissé à un approbateur, en secondes : une journée (point 17).

De quoi couvrir une nuit et un jour ouvré, sans qu'un run oublié attende pour
toujours.
"""


type IdempotencyStatus = Literal["in_progress", "completed"]
"""État d'une clé d'idempotence : réservée (l'effet est peut-être en cours) ou tenue (l'effet a eu lieu)."""
type UnknownState = Literal["error", "pause"]
"""Ce qu'il advient d'un appel interrompu dont l'effet est peut-être produit (point 18) : l'erreur au modèle, ou une pause pour un humain."""

MAX_RECORDED: Final = 50_000
"""Taille maximale, en caractères de JSON, d'un résultat mémorisé sous une clé d'idempotence.

Au-delà, le magasin refuse le résultat (``ResultTooLarge``, avec un message à
l'auteur de l'outil). Le décorateur agit avant le déport de l'exécuteur, donc
rien ne le réduira pour nous : ``@idempotent`` en mémorise une forme réduite,
plutôt que de faire échouer un appel dont l'effet a eu lieu.
"""

DEFAULT_RETENTION: Final = 24 * 3600.0
"""Durée, en secondes, pendant laquelle un résultat mémorisé reste consultable, quand ni l'outil ni le magasin n'en fixent d'autre.

Une journée : au-delà, l'appel qui le redemanderait est hors de portée de
toute reprise.
"""


class IdempotencyRecord(DomainModel):
    """Ce qu'un magasin d'idempotence sait d'une clé (#18, #49).

    ``in_progress`` : quelqu'un a réservé la clé et n'a pas encore fini —
    l'effet est peut-être en train de se produire. ``completed`` : il s'est
    produit, et ``result`` est ce qu'il a rendu.

    Attributes:
        key: La clé.
        status: ``in_progress`` ou ``completed``.
        result: Ce que l'outil avait rendu ; absent tant que la clé est
            réservée.
        expires_at: Fin de la réservation, ou de la conservation du
            résultat.
    """

    key: str
    status: IdempotencyStatus
    result: JsonValue = None
    expires_at: datetime

    def alive(self, now: datetime) -> bool:
        """Vrai tant que l'échéance n'est pas passée : personne d'autre ne doit toucher à la clé."""
        return now < self.expires_at


class ResultTooLarge(ValueError):
    """Résultat trop volumineux pour être mémorisé sous une clé d'idempotence (plus de 50 000 caractères de JSON)."""


def recordable(result: object) -> JsonValue:
    """Le résultat, prêt à être mémorisé ; refuse ce qui ne l'est pas.

    Mémoriser est un service rendu à l'outil, pas un fourre-tout : le résultat
    voyage vers un magasin partagé et y reste. Au-delà de ``MAX_RECORDED``
    caractères il est refusé, bruyamment, à l'auteur de l'outil — un gros
    résultat se range dans un artefact, et c'est sa référence qui se mémorise.

    Args:
        result: Ce que l'outil a rendu.

    Returns:
        Le même résultat, dont la forme JSON est prouvée.

    Raises:
        ResultTooLarge: Plus de ``MAX_RECORDED`` caractères en JSON.
        TypeError: Le résultat n'est pas du JSON ; le message du module
            ``json`` nomme le type fautif.
    """
    encoded = json.dumps(result, ensure_ascii=False)
    if len(encoded) > MAX_RECORDED:
        raise ResultTooLarge(
            f"Résultat de {len(encoded)} caractères non mémorisé : la limite est de {MAX_RECORDED}. Rangez-le dans un artefact et mémorisez sa référence."
        )
    # json.dumps a déjà prouvé que la valeur est du JSON.
    return result  # pyright: ignore[reportReturnType]


class ApprovalSettings(DomainModel):
    """Ce qu'un agent fait des approbations qu'il demande (``approval`` de l'agent, points 17 et 28).

    Attributes:
        expires_in: Délai laissé à l'approbateur, en secondes. Défaut :
            86 400 (24 h). ``null`` le retire — et une demande attend alors
            indéfiniment, ce qui est un choix, pas un défaut : rien d'autre ne
            borne cette attente, ni le délai de l'agent (qui ne compte que le
            pilotage) ni la reprise (qui ne fait que reconstater l'attente).
        on_expiry: Ce qu'il advient d'une demande périmée : ``deny`` (défaut,
            un « non » prudent, personne n'ayant dit oui ; le modèle
            continue) ou ``fail`` (le run échoue).
    """

    expires_in: PositiveFloat | None = DEFAULT_APPROVAL_DELAY
    on_expiry: ExpiryAction = "deny"


class Approvers(DomainModel):
    """Qui tranche les demandes d'approbation d'un agent, tel que chacune le fige (§ 5.3).

    Tiré du bloc ``access`` de l'agent au montage : le moteur ne lit pas la
    config. Chaque demande écrite emporte la liste de son outil et
    ``self_approval`` ; une config changée ensuite ne change pas qui peut
    trancher une demande déjà posée.

    Attributes:
        default: Rôles approbateurs de toute demande ; ``None`` : tout rôle
            qui a ``approve`` sur l'agent.
        tools: Rôles approbateurs par outil, sous le nom que voit le modèle.
        self_approval: Faux si l'agent interdit l'auto-approbation
            (``access.self_approval: false``, décision D-7b des utilisateurs
            et rôles). Défaut : vrai.
    """

    default: tuple[str, ...] | None = None
    tools: dict[str, tuple[str, ...]] = Field(default_factory=dict[str, tuple[str, ...]])
    self_approval: bool = True

    def of(self, tool: str) -> tuple[str, ...] | None:
        """Les rôles approbateurs d'un outil : les siens, à défaut ceux de l'agent ; ``None`` : tout rôle qui a ``approve``."""
        return self.tools.get(tool, self.default)


class AskSettings(DomainModel):
    """Ce que le moteur fait d'une question à l'utilisateur (outil ``loom.ask``, ``docs/wait_answer.md``).

    Attributes:
        expires_in: Sans réponse après ce délai, en secondes, la question
            expire et le modèle l'apprend ; ``None`` (défaut) : elle attend.
    """

    expires_in: PositiveFloat | None = None


class ToolDefinition(DomainModel):
    """Ce que le modèle voit d'un outil : son nom, sa description et le schéma de ses arguments.

    Attributes:
        name: Nom de l'outil, 1 à 64 lettres, chiffres, ``_`` ou ``-`` ;
            préfixé pour un outil MCP (``crm__rechercher``).
        description: Description lue par le modèle ; c'est elle qui lui dit
            quand appeler l'outil.
        input_schema: JSON Schema des arguments. Défaut : un objet vide.
    """

    name: str = Field(pattern=TOOL_NAME_PATTERN)
    description: str
    input_schema: dict[str, JsonValue] = Field(default_factory=_empty_object_schema)


class ToolOverrides(DomainModel):
    """Déclarations d'un outil fixées par la config ; ``None`` garde celle de l'outil.

    Se déclarent sur la référence de l'outil dans ``tools`` d'un agent, ou
    sous ``mcp_servers[].tools.<outil>``. Un outil fourni à paramètres en
    refuse certaines (``approval``, ``side_effects``, ``idempotent``), que
    ses paramètres expriment déjà.

    Attributes:
        description: Ce que le modèle lit de l'outil, à la place de ce que
            l'outil en dit : une description écrite pour un autre usage se
            réécrit sans toucher à l'outil.
        side_effects: ``none``, ``reversible`` ou ``irreversible``.
        approval: ``never``, ``always`` ou ``policy``.
        idempotent: Vrai si refaire un appel ne double pas son effet.
        timeout: Délai d'un appel, en secondes ; sinon celui de
            ``execution.tools.timeout`` (30 s).
        offload_over: Taille d'un résultat, en caractères, au-delà de
            laquelle il est déporté ; sinon celle de
            ``execution.tools.offload_over`` (50 000).
        output: Contrat de sortie propre à cet outil (fonction E5).
        skill: Mode d'emploi ajouté à la description (fonction D14), en
            ligne.
        skill_file: Le même, dans un fichier relatif à ``resources_dir``,
            que le chargement lit et range dans ``skill``. Exclusif de
            ``skill``.

    Raises:
        ValueError: ``skill`` et ``skill_file`` donnés ensemble.
    """

    description: str | None = Field(default=None, min_length=1)
    side_effects: SideEffects | None = None
    approval: Approval | None = None
    idempotent: bool | None = None
    timeout: PositiveFloat | None = None
    offload_over: PositiveInt | None = None
    output: OutputContract | None = None
    skill: SkillText | None = None
    skill_file: Path | None = None

    @model_validator(mode="after")
    def _one_skill(self) -> Self:
        """Refuse ``skill`` et ``skill_file`` ensemble."""
        exclusive_skill(self.skill, self.skill_file)
        return self


# Champs de ``ToolOverrides`` qui ne se recopient pas sur ``ToolSpec``.
_NOT_COPIED: Final = frozenset({"skill_file"})


class ToolSpec(ToolDefinition):
    """Définition d'un outil complétée des déclarations utiles au moteur (``Tool.spec``).

    Ce que voit le modèle est ``definition()`` : la description, le skill,
    puis la mention d'un outil terminal.

    Attributes:
        kind: Nature de l'outil : ``python``, ``mcp``, ``role``, ``agent`` ou
            ``builtin``.
        side_effects: Effets de l'outil. Défaut : ``none``. Sans
            déclaration, un outil Python passe pour sans effet, et ``loom
            validate`` le signale.
        approval: ``never`` (défaut), ``always`` ou ``policy``.
        idempotent: Vrai si refaire un appel ne double pas son effet.
            Défaut : faux.
        timeout: Délai propre à l'outil, en secondes ; sinon celui de
            l'exécuteur (``execution.tools.timeout``).
        offload_over: Taille en caractères au-delà de laquelle le résultat
            est déporté (point 16) ; sinon le seuil de l'exécuteur.
        terminal: Vrai si la sortie est transmise telle quelle comme
            réponse finale quand l'outil est seul dans son tour (point 13).
        on_unknown: Sort d'un appel repris dont l'effet est peut-être
            produit (point 18) : ``error`` (défaut), le dire au modèle ;
            ``pause``, suspendre le run pour qu'un humain vérifie.
        business_key: Vrai si l'outil mémorise ses effets sous une clé
            **métier**, donc visible d'un run à l'autre (point 49). Il lui
            faut alors un magasin partagé et durable, ce que le chargement
            vérifie : le journal d'un run ne l'est pas.
        output: Contrat de sortie (fonction E5) : contrôlé après chaque
            appel ; un rôle se répare, un outil non.
        skill: Mode d'emploi donné par la config, ajouté à la description
            vue par le modèle ; ``description`` reste celle de l'outil.
        asks: Présent pour un outil qui pose une question à l'utilisateur
            (``loom.ask``) : le moteur ne l'exécute pas, il écrit la question
            et attend la réponse.
    """

    kind: ToolKind
    side_effects: SideEffects = "none"
    approval: Approval = "never"
    idempotent: bool = False
    timeout: PositiveFloat | None = None
    offload_over: PositiveInt | None = None
    terminal: bool = False
    on_unknown: UnknownState = "error"
    business_key: bool = False
    output: OutputContract | None = None
    skill: SkillText | None = None
    asks: AskSettings | None = None

    @property
    def safe_to_retry(self) -> bool:
        """Vrai si une réexécution après interruption est sans risque : sans effet, ou idempotent (point 18)."""
        return self.side_effects == "none" or self.idempotent

    def overridden(self, overrides: ToolOverrides | None) -> ToolSpec:
        """La définition, ses déclarations remplacées par celles que ``overrides`` renseigne.

        Args:
            overrides: Les déclarations de la config, ou ``None``.

        Returns:
            Une copie modifiée ; la même définition si rien ne change.

        Raises:
            ValueError: ``skill_file`` n'a pas été lu : un skill se lit au
                chargement de la config.
        """
        if overrides is None:
            return self
        # Valeurs prises telles quelles (pas de dump) : le contrat reste un modèle.
        if overrides.skill_file is not None:
            # Seul le chargement lit le fichier. Recopié tel quel, le champ
            # serait posé sur la spec sans y exister, et le skill perdu sans un mot.
            raise ValueError(f"skill_file {overrides.skill_file} non lu : un skill se lit au chargement (load_config)")
        changes = {name: value for name in type(overrides).model_fields if name not in _NOT_COPIED and (value := getattr(overrides, name)) is not None}
        if overrides.skill is not None:
            # ``model_copy`` ne valide rien : le contrôle se fait là où le skill sert.
            changes["skill"] = checked_skill(overrides.skill)
        return self.model_copy(update=changes) if changes else self

    def definition(self) -> ToolDefinition:
        """Ce que voit le modèle : la description, le skill, puis la mention d'un outil terminal.

        Returns:
            La définition à exposer, avec ``TERMINAL_HINT`` en fin de
            description pour un outil terminal.
        """
        description = self.description
        if self.skill:
            description = f"{description}{SKILL_SEPARATOR}{self.skill}" if description else self.skill
        if self.terminal:
            description = f"{description}\n\n{TERMINAL_HINT}" if description else TERMINAL_HINT
        return ToolDefinition(name=self.name, description=description, input_schema=self.input_schema)
