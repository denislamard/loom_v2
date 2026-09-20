# SPDX-License-Identifier: Apache-2.0
"""Politiques : points d'accroche, décisions typées, ce qu'une politique voit (#1, #2).

Une politique est du code branché sur un point d'accroche de ``step``. Elle
reçoit ce qui se passe à ce point (``PolicySubject``) et rend une décision.
Le code décide, le LLM propose : une politique est déterministe pour un état
donné.

Points d'accroche et décisions permises (matrice du #2) :

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
permise à l'un de ses points est refusée au démarrage. ``Pause`` arrive avec
les approbations (J4.3).
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
type DecisionKind = Literal["continue", "replace", "retry", "deny", "pause", "stop", "fail"]

HOOK_POINTS: Final[tuple[HookPoint, ...]] = (
    "before_model",
    "after_model",
    "before_tool",
    "after_tool",
    "on_output",
)
DECISION_KINDS: Final[tuple[DecisionKind, ...]] = (
    "continue",
    "replace",
    "retry",
    "deny",
    "pause",
    "stop",
    "fail",
)

# Décisions permises à chaque point (#2).
ALLOWED_DECISIONS: Final[Mapping[HookPoint, frozenset[DecisionKind]]] = {
    "before_model": frozenset({"continue", "replace", "stop", "fail"}),
    "after_model": frozenset({"continue", "retry", "stop", "fail"}),
    "before_tool": frozenset({"continue", "replace", "deny", "pause", "fail"}),
    "after_tool": frozenset({"continue", "replace", "retry", "fail"}),
    "on_output": frozenset({"continue", "replace", "retry", "fail"}),
}

# Décisions prévues pour plus tard, refusées au démarrage.
LATER_DECISIONS: Final[Mapping[DecisionKind, str]] = {"pause": "J4.3 (approbations)"}

# Début du message qui demande une réparation au modèle orchestrateur (#20).
REPAIR_PREFIX: Final = "Réponse refusée par un contrôle"

# Consigne de la réponse forcée (A4) : dernier message de sa requête, jamais
# journalisé. Sans elle, un modèle privé d'outils (``tool_choice: none``) peut
# écrire son appel d'outil en texte (vu avec MiniMax-M3 en 3.4) ; en dernier
# message plutôt que dans le prompt système, elle pèse plus et laisse le cache
# du préfixe intact.
FINALIZE_HINT: Final = (
    "Tu ne peux plus appeler d'outil. Réponds maintenant à la demande, en texte, avec "
    "les informations déjà obtenues ; si tu n'as pas pu tout faire, dis ce qui reste à faire."
)

# Préfixe réservé aux politiques fournies par loom-ia et aux règles du moteur.
RESERVED_PREFIX: Final = "loom."
POLICY_NAME_PATTERN: Final = r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}$"


# --- Décisions ---------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Continue:
    """Rien à redire : la politique suivante s'exécute."""

    kind: ClassVar[DecisionKind] = "continue"


CONTINUE: Final = Continue()


@dataclass(frozen=True, slots=True)
class Replace:
    """Valeur remplacée, du même type que l'entrée ; transmise à la politique suivante.

    ``before_model`` : une ``ModelRequest`` ; ``before_tool`` : les arguments
    (objet JSON) ; ``after_tool`` : un ``ToolOutput`` ; ``on_output`` : un
    ``Message`` de l'assistant, ou un texte.
    """

    kind: ClassVar[DecisionKind] = "replace"
    value: object
    reason: str = ""


@dataclass(frozen=True, slots=True)
class Retry:
    """Sortie refusée : son auteur recommence, avec ce diagnostic.

    ``tools`` : l'orchestrateur garde ses outils pendant la réparation ; faux
    pour un échec de forme, où il doit seulement réécrire sa réponse.
    """

    kind: ClassVar[DecisionKind] = "retry"
    feedback: str
    tools: bool = True


@dataclass(frozen=True, slots=True)
class Deny:
    """Appel d'outil refusé : le motif revient au modèle comme résultat d'erreur."""

    kind: ClassVar[DecisionKind] = "deny"
    reason: str


@dataclass(frozen=True, slots=True)
class Pause:
    """Run mis en pause en attendant une décision humaine (J4.3)."""

    kind: ClassVar[DecisionKind] = "pause"
    reason: str


@dataclass(frozen=True, slots=True)
class Stop:
    """Plus d'appel d'outil : le run passe en ``FINALIZING`` (réponse forcée)."""

    kind: ClassVar[DecisionKind] = "stop"
    reason: str


@dataclass(frozen=True, slots=True)
class Fail:
    """Le run échoue."""

    kind: ClassVar[DecisionKind] = "fail"
    error: str


type Decision = Continue | Replace | Retry | Deny | Pause | Stop | Fail


# --- Ce que voit une politique -------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class BeforeModel:
    """Requête prête à partir vers le modèle orchestrateur."""

    point: ClassVar[HookPoint] = "before_model"
    state: RunState
    request: ModelRequest
    # Réponse forcée sans outils (``FINALIZING``) : ``Stop`` n'y a plus d'effet.
    finalizing: bool = False
    # Consommation des runs précédents de la session (budget de session, J4) ;
    # vide pour un sous-run.
    session: Spent = field(default_factory=Spent)


@dataclass(frozen=True, slots=True, kw_only=True)
class AfterModel:
    """Réponse du modèle orchestrateur, déjà journalisée, avant ses appels d'outils."""

    point: ClassVar[HookPoint] = "after_model"
    state: RunState
    response: Message
    finalizing: bool = False


@dataclass(frozen=True, slots=True, kw_only=True)
class BeforeTool:
    """Appel d'outil accepté (références résolues, arguments conformes), avant son lancement."""

    point: ClassVar[HookPoint] = "before_tool"
    state: RunState
    call: PendingCall
    spec: ToolSpec
    # Arguments qui partiront : résolus, validés, éventuellement déjà remplacés.
    arguments: dict[str, JsonValue]


@dataclass(frozen=True, slots=True, kw_only=True)
class AfterTool:
    """Résultat d'un outil, avant son écriture dans le journal."""

    point: ClassVar[HookPoint] = "after_tool"
    state: RunState
    call: PendingCall
    spec: ToolSpec
    arguments: dict[str, JsonValue]
    output: ToolOutput


@dataclass(frozen=True, slots=True, kw_only=True)
class OnOutput:
    """Réponse finale, avant la clôture du run.

    ``source`` : ``model`` pour la réponse de l'orchestrateur, ``terminal``
    pour la sortie d'un outil terminal (#13), alors nommé par ``tool``.
    """

    point: ClassVar[HookPoint] = "on_output"
    state: RunState
    output: Message
    source: Literal["model", "terminal"] = "model"
    tool: str | None = None
    # Réponse forcée sans outils (``FINALIZING``) : une réparation se fera sans outils.
    finalizing: bool = False


type PolicySubject = BeforeModel | AfterModel | BeforeTool | AfterTool | OnOutput


type CheckOutcome = Literal["passed", "failed", "skipped"]
# Suite donnée à un contrôle échoué : réparation, ou décision une fois les réparations épuisées.
type CheckResolution = Literal["retry", "fail", "unverified", "fallback"]


@dataclass(frozen=True, slots=True, kw_only=True)
class GuardCheck:
    """Contrôle d'une sortie, journalisé en ``guard.checked`` avant la décision (#20).

    ``target`` : ``output`` (réponse finale), ``role:<nom>`` ou ``tool:<nom>``.
    """

    guard: str
    target: str
    outcome: CheckOutcome
    reason: str = ""
    # La sortie a été corrigée par la normalisation déterministe.
    normalized: bool = False
    resolution: CheckResolution | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class PolicyContext:
    """Ce qu'une politique sait d'elle-même pour cet appel.

    Une politique qui contrôle une sortie (un guard) y enregistre ses contrôles
    (``record``) : ils sont journalisés avant sa décision, réussis ou non.
    """

    # Nom sous lequel la politique est déclarée dans l'agent.
    name: str
    # Paramètres donnés par la config.
    params: Mapping[str, JsonValue] = field(default_factory=dict[str, JsonValue])
    # Réparations déjà demandées par cette politique pour cette sortie (``Retry``) :
    # dans le run pour la réponse finale, dans l'appel pour un rôle.
    attempt: int = 0
    checks: list[GuardCheck] = field(default_factory=list[GuardCheck])

    def record(self, check: GuardCheck) -> None:
        """Enregistre un contrôle, journalisé en ``guard.checked``."""
        self.checks.append(check)
