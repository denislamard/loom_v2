# SPDX-License-Identifier: Apache-2.0
"""Politiques : points d'accroche, décisions typées, ce qu'une politique voit (points 1 et 2, fonction E7).

Une politique est du code branché sur un point d'accroche de ``step``. Elle
reçoit ce qui se passe à ce point (``PolicySubject``) et rend une décision.
Le code décide, le LLM propose : une politique est déterministe pour un état
donné.

Points d'accroche et décisions permises (matrice du point 2) :

============  ========  =========  =====  ====  =====  ====  ====
Point         Continue  Replace    Retry  Deny  Pause  Stop  Fail
============  ========  =========  =====  ====  =====  ====  ====
before_model  oui       requête                        oui   oui
after_model   oui                  oui                 oui   oui
before_tool   oui       arguments         oui   oui          oui
after_tool    oui       résultat   oui                       oui
on_output     oui       réponse    oui                       oui
============  ========  =========  =====  ====  =====  ====  ====

Une politique déclare les décisions qu'elle peut rendre : une décision non
permise à l'un de ses points est refusée au démarrage. ``Pause`` (un humain
tranche, phase 4.3) n'est permise qu'à ``before_tool``.

Une politique s'écrit avec le décorateur ``@policy`` de ``loom_ia.policies``
et se branche sous ``policies`` d'un agent. Toute décision autre que
``Continue`` est écrite au journal (``policy.decided``) avant son effet.

Exemple ::

    from loom_ia.policies import CONTINUE, BeforeTool, Decision, Deny, policy

    @policy(points={"before_tool"}, decisions={"deny"})
    async def montant_max(subject: BeforeTool) -> Decision:
        if subject.arguments.get("montant", 0) > 5000:
            return Deny("montant au-delà de 5 000 € : validation par téléphone")
        return CONTINUE
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import ClassVar, Final, Literal

from pydantic import JsonValue

from loom_ia.core.model.budget import Spent
from loom_ia.core.model.content import ToolOutput
from loom_ia.core.model.messages import Message
from loom_ia.core.model.run_state import PendingCall, RunState
from loom_ia.core.model.streaming import ModelRequest
from loom_ia.core.model.tooling import ToolSpec

type HookPoint = Literal["before_model", "after_model", "before_tool", "after_tool", "on_output"]
"""Point d'accroche d'une politique dans la boucle d'un run."""
type DecisionKind = Literal["continue", "replace", "retry", "deny", "pause", "stop", "fail"]
"""Genre de décision d'une politique."""

HOOK_POINTS: Final[tuple[HookPoint, ...]] = (
    "before_model",
    "after_model",
    "before_tool",
    "after_tool",
    "on_output",
)
"""Les points d'accroche, dans l'ordre de la boucle."""
DECISION_KINDS: Final[tuple[DecisionKind, ...]] = (
    "continue",
    "replace",
    "retry",
    "deny",
    "pause",
    "stop",
    "fail",
)
"""Les genres de décision."""

ALLOWED_DECISIONS: Final[Mapping[HookPoint, frozenset[DecisionKind]]] = {
    "before_model": frozenset({"continue", "replace", "stop", "fail"}),
    "after_model": frozenset({"continue", "retry", "stop", "fail"}),
    "before_tool": frozenset({"continue", "replace", "deny", "pause", "fail"}),
    "after_tool": frozenset({"continue", "replace", "retry", "fail"}),
    "on_output": frozenset({"continue", "replace", "retry", "fail"}),
}
"""Décisions permises à chaque point (matrice du point 2) ; la config refuse une politique qui en déclare d'autres."""

REPAIR_PREFIX: Final = "Réponse refusée par un contrôle"
"""Début du message qui demande une réparation au modèle orchestrateur (point 20)."""

FINALIZE_HINT: Final = (
    "Tu ne peux plus appeler d'outil. Réponds maintenant à la demande, en texte, avec "
    "les informations déjà obtenues ; si tu n'as pas pu tout faire, dis ce qui reste à faire."
)
"""Consigne de la réponse forcée (fonction A4) : dernier message de sa requête, jamais journalisé.

Sans elle, un modèle privé d'outils (``tool_choice: none``) peut écrire son
appel d'outil en texte (vu avec MiniMax-M3 en phase 3.4) ; en dernier message
plutôt que dans le prompt système, elle pèse plus et laisse le cache du
préfixe intact.
"""

RESERVED_PREFIX: Final = "loom."
"""Préfixe réservé aux politiques et outils fournis par loom-ia et aux règles du moteur (``loom.budget``, ``loom.fetch_page``…)."""
POLICY_NAME_PATTERN: Final = r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}$"
"""Forme d'un nom de politique : 1 à 64 caractères, lettres, chiffres, ``_``, ``.`` ou ``-``."""


# --- Décisions ---------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Continue:
    """Décision : rien à redire, la politique suivante s'exécute. Pas écrite au journal (sauf exception, voir ``policy.decided``)."""

    kind: ClassVar[DecisionKind] = "continue"


CONTINUE: Final = Continue()
"""L'instance de ``Continue``, à rendre telle quelle."""


@dataclass(frozen=True, slots=True)
class Replace:
    """Valeur remplacée, du même type que l'entrée ; transmise à la politique suivante.

    ``before_model`` : une ``ModelRequest`` ; ``before_tool`` : les arguments
    (objet JSON) ; ``after_tool`` : un ``ToolOutput`` ; ``on_output`` : un
    ``Message`` de l'assistant, ou un texte.

    Attributes:
        value: La valeur de remplacement.
        reason: Motif, écrit dans ``policy.decided``.
    """

    kind: ClassVar[DecisionKind] = "replace"
    value: object
    reason: str = ""


@dataclass(frozen=True, slots=True)
class Retry:
    """Sortie refusée : son auteur recommence, avec ce diagnostic.

    Permise à ``after_model``, ``after_tool`` et ``on_output``. Le diagnostic
    part au modèle dans un ``message.user`` de ``kind: repair``.

    Attributes:
        feedback: Le diagnostic, lu par le modèle.
        tools: Vrai si l'orchestrateur garde ses outils pendant la
            réparation ; faux pour un échec de forme, où il doit seulement
            réécrire sa réponse. Défaut : vrai.
    """

    kind: ClassVar[DecisionKind] = "retry"
    feedback: str
    tools: bool = True


@dataclass(frozen=True, slots=True)
class Deny:
    """Décision ``before_tool`` : appel d'outil refusé ; le motif revient au modèle comme résultat d'erreur.

    Attributes:
        reason: Le motif, lu par le modèle.
    """

    kind: ClassVar[DecisionKind] = "deny"
    reason: str


@dataclass(frozen=True, slots=True)
class Pause:
    """Décision ``before_tool`` : l'appel attend une décision humaine (approbation, phase 4.3).

    Écrit un ``approval.requested`` ; le run passe en ``PAUSED``. Exige un
    journal durable.

    Attributes:
        reason: Le motif, montré à l'approbateur.
    """

    kind: ClassVar[DecisionKind] = "pause"
    reason: str


@dataclass(frozen=True, slots=True)
class Stop:
    """Plus d'appel d'outil : le run passe en ``FINALIZING`` et donne une réponse forcée, sans outils.

    Permise à ``before_model`` et ``after_model`` ; sans effet pendant la
    réponse forcée elle-même.

    Attributes:
        reason: Le motif, écrit dans ``policy.decided``.
    """

    kind: ClassVar[DecisionKind] = "stop"
    reason: str


@dataclass(frozen=True, slots=True)
class Fail:
    """Le run échoue (``run.failed``) ; permise à tous les points.

    Attributes:
        error: Le message d'erreur.
    """

    kind: ClassVar[DecisionKind] = "fail"
    error: str


type Decision = Continue | Replace | Retry | Deny | Pause | Stop | Fail
"""Décision d'une politique."""


# --- Ce que voit une politique -------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class BeforeModel:
    """Ce que voit une politique à ``before_model`` : la requête prête à partir vers l'orchestrateur.

    Décisions permises : ``Continue``, ``Replace`` (une autre
    ``ModelRequest``), ``Stop``, ``Fail``. C'est le point du budget
    (``loom.budget``).

    Attributes:
        state: L'état du run.
        request: La requête.
        finalizing: Vrai pendant la réponse forcée sans outils
            (``FINALIZING``) : ``Stop`` n'y a plus d'effet.
        session: Consommation des runs précédents de la session (budget de
            session, fonction J4) ; vide pour un sous-run.
    """

    point: ClassVar[HookPoint] = "before_model"
    state: RunState
    request: ModelRequest
    finalizing: bool = False
    session: Spent = field(default_factory=Spent)


@dataclass(frozen=True, slots=True, kw_only=True)
class AfterModel:
    """Ce que voit une politique à ``after_model`` : la réponse de l'orchestrateur, déjà journalisée, avant ses appels d'outils.

    Décisions permises : ``Continue``, ``Retry``, ``Stop``, ``Fail``.

    Attributes:
        state: L'état du run.
        response: La réponse du modèle.
        finalizing: Vrai pendant la réponse forcée.
    """

    point: ClassVar[HookPoint] = "after_model"
    state: RunState
    response: Message
    finalizing: bool = False


@dataclass(frozen=True, slots=True, kw_only=True)
class BeforeTool:
    """Ce que voit une politique à ``before_tool`` : un appel accepté (références résolues, arguments conformes), avant son lancement.

    Décisions permises : ``Continue``, ``Replace`` (d'autres arguments, que le
    modèle lira ensuite dans la conversation), ``Deny``, ``Pause``, ``Fail``.

    Attributes:
        state: L'état du run.
        call: L'appel en attente.
        spec: La définition de l'outil.
        arguments: Arguments qui partiront : résolus, validés,
            éventuellement déjà remplacés par une politique précédente.
    """

    point: ClassVar[HookPoint] = "before_tool"
    state: RunState
    call: PendingCall
    spec: ToolSpec
    arguments: dict[str, JsonValue]


@dataclass(frozen=True, slots=True, kw_only=True)
class AfterTool:
    """Ce que voit une politique à ``after_tool`` : le résultat d'un outil, avant son écriture au journal.

    Décisions permises : ``Continue``, ``Replace`` (un autre ``ToolOutput``),
    ``Retry`` (pour un rôle), ``Fail``. C'est le point du contrat d'un outil
    ou d'un rôle.

    Attributes:
        state: L'état du run.
        call: L'appel.
        spec: La définition de l'outil.
        arguments: Les arguments partis.
        output: Le résultat.
    """

    point: ClassVar[HookPoint] = "after_tool"
    state: RunState
    call: PendingCall
    spec: ToolSpec
    arguments: dict[str, JsonValue]
    output: ToolOutput


@dataclass(frozen=True, slots=True, kw_only=True)
class OnOutput:
    """Ce que voit une politique à ``on_output`` : la réponse finale, avant la clôture du run.

    Décisions permises : ``Continue``, ``Replace`` (une autre réponse),
    ``Retry``, ``Fail``. C'est le point du contrat et du juge de la réponse
    finale.

    Attributes:
        state: L'état du run.
        output: La réponse finale.
        source: ``model`` pour la réponse de l'orchestrateur, ``terminal``
            pour la sortie d'un outil terminal (point 13).
        tool: L'outil terminal, avec ``source: terminal``.
        finalizing: Vrai pendant la réponse forcée sans outils : une
            réparation se fera sans outils.
    """

    point: ClassVar[HookPoint] = "on_output"
    state: RunState
    output: Message
    source: Literal["model", "terminal"] = "model"
    tool: str | None = None
    finalizing: bool = False


type PolicySubject = BeforeModel | AfterModel | BeforeTool | AfterTool | OnOutput
"""Ce que voit une politique, selon son point d'accroche."""


type CheckOutcome = Literal["passed", "failed", "skipped"]
"""Issue d'un contrôle de sortie."""
type CheckResolution = Literal["retry", "fail", "unverified", "fallback"]
"""Suite donnée à un contrôle échoué : réparation, ou décision une fois les réparations épuisées."""


@dataclass(frozen=True, slots=True, kw_only=True)
class GuardCheck:
    """Contrôle d'une sortie, journalisé en ``guard.checked`` avant la décision (#20).

    Attributes:
        guard: Nom du guard.
        target: ``output`` (réponse finale), ``role:<nom>`` ou ``tool:<nom>``.
        outcome: ``passed``, ``failed`` ou ``skipped``.
        reason: Pourquoi le contrôle a échoué ou a été sauté.
        normalized: Vrai si la sortie a été corrigée par la normalisation
            déterministe.
        resolution: Suite d'un échec ; ``None`` sinon.
    """

    guard: str
    target: str
    outcome: CheckOutcome
    reason: str = ""
    normalized: bool = False
    resolution: CheckResolution | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class PolicyContext:
    """Ce qu'une politique sait d'elle-même pour cet appel.

    Une politique qui contrôle une sortie (un guard) y enregistre ses contrôles
    (``record``) : ils sont journalisés avant sa décision, réussis ou non.

    Attributes:
        name: Nom sous lequel la politique est déclarée dans l'agent.
        params: Paramètres donnés par la config (``params`` de la politique).
        attempt: Réparations déjà demandées par cette politique pour cette
            sortie (``Retry``) : dans le run pour la réponse finale, dans
            l'appel pour un rôle.
        checks: Contrôles enregistrés par ``record``.
        turns: Historique de la session avant ce run, groupé par tour : ce
            qu'un juge reçoit s'il le déclare (point 12).
        summary: Dernier résumé de compaction, pour la même raison.
    """

    name: str
    params: Mapping[str, JsonValue] = field(default_factory=dict[str, JsonValue])
    attempt: int = 0
    checks: list[GuardCheck] = field(default_factory=list[GuardCheck])
    turns: tuple[tuple[Message, ...], ...] = ()
    summary: str | None = None

    def record(self, check: GuardCheck) -> None:
        """Enregistre un contrôle, journalisé en ``guard.checked``."""
        self.checks.append(check)
