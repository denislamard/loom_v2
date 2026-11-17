# SPDX-License-Identifier: Apache-2.0
"""Exécution des politiques d'un agent aux points d'accroche de ``step`` (points 1 et 2).

Les politiques d'un point s'exécutent dans l'ordre déclaré. ``Replace``
transmet la valeur remplacée à la suivante ; toute autre décision que
``Continue`` ou ``Replace`` arrête la chaîne. Chaque décision autre que
``Continue`` donne un ``policy.decided``, que le moteur écrit avant l'effet.

Garde-fous :

- chaque politique a un délai ; une exception, un dépassement du délai, une
  décision non déclarée ou non permise à ce point, ou une valeur de
  remplacement du mauvais type sont des erreurs de la politique : selon son
  ``on_error``, le run échoue (``block``, par défaut) ou la politique est
  ignorée (``allow``), ce qui est journalisé aussi ;
- ``Retry`` est borné : au-delà de ``max_attempts`` réparations demandées par
  une même politique pour une même sortie (compteur du ``RunState`` pour la
  réponse finale, compteur de l'appel pour un rôle), sa décision devient
  ``Fail``. Un guard ou un juge qui borne lui-même ses réparations (contrat
  de sortie, juge, fidélité d'un résumé) n'a pas de borne ici
  (``max_attempts: None``) : la sienne est son ``repair.max_attempts``.

Guards : une politique peut enregistrer ses contrôles dans son contexte
(``record``) ; ils deviennent des ``guard.checked``, écrits avant sa décision.

Juges (point 21) : une politique fournie par loom-ia peut aussi journaliser son
propre travail (``TracingPolicy``) : l'appel du modèle d'un juge et son
verdict, écrits avant ses contrôles. Le type est nominal et interne : une
politique écrite par l'utilisateur n'écrit pas dans le journal.

Ce module ne fait qu'évaluer : ce que chaque décision change au run (requête
remplacée, appel refusé, réparation, arrêt, échec) est appliqué par la boucle
et l'exécuteur d'outils.

Points d'accroche : ``before_model``, ``after_model``, ``before_tool``,
``after_tool``, ``on_output`` (``HookPoint``). Config : ``agents.<nom>.policies``
(nom, ``params``, ``timeout``, ``on_error``, ``max_attempts``).
"""

import asyncio
import dataclasses
import logging
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Final, Literal, cast

from pydantic import JsonValue, TypeAdapter, ValidationError

from loom_ia.core.events import (
    BudgetExceeded,
    CircuitOpened,
    GuardChecked,
    JudgeEvaluated,
    ModelExchanged,
    ModelFellBack,
    ModelResponded,
    ModelRetried,
    PolicyDecided,
)
from loom_ia.core.model import (
    ALLOWED_DECISIONS,
    CONTINUE,
    AfterTool,
    BeforeModel,
    BeforeTool,
    Continue,
    Decision,
    DecisionKind,
    Deny,
    Fail,
    GuardCheck,
    HookPoint,
    Message,
    ModelRequest,
    OnOutput,
    Pause,
    PolicyContext,
    PolicySubject,
    Replace,
    Retry,
    Stop,
    TextBlock,
    ToolOutput,
    Usage,
)
from loom_ia.core.ports import Policy, stopped_by_client

logger = logging.getLogger(__name__)

DEFAULT_POLICY_TIMEOUT: Final = 5.0
"""Délai par défaut d'une politique, en secondes."""

type OnError = Literal["block", "allow"]
"""Conduite face à une politique en erreur : ``block`` fait échouer le run, ``allow`` l'ignore."""
type ArgumentsCheck = Callable[[dict[str, JsonValue]], str | None]
"""Vérifie des arguments remplacés ; renvoie le problème, ou ``None`` s'ils conviennent."""

_ARGUMENTS: Final = TypeAdapter(dict[str, JsonValue])


class PolicyFailure(Exception):
    """Erreur d'une politique : exception, délai, décision ou valeur invalide.

    Elle ne sort pas de ``Policies.run`` : selon ``on_error``, le run échoue
    (``Fail``) ou la politique est ignorée, et un ``policy.decided`` marqué
    ``error`` est écrit.
    """


@dataclass(frozen=True, slots=True, kw_only=True)
class BoundPolicy:
    """Une politique telle qu'un agent la branche : points, paramètres, garde-fous.

    Attributes:
        policy: La politique.
        name: Nom dans le journal ; par défaut celui de la politique.
        points: Points d'accroche où elle s'exécute.
        params: Paramètres donnés à la politique (``PolicyContext.params``).
        timeout: Délai de sa décision, en secondes (5 par défaut) ; ``None`` :
            sans délai.
        on_error: ``block`` (le run échoue) ou ``allow`` (la politique est
            ignorée) quand elle est en erreur.
        max_attempts: Réparations (``Retry``) que la politique peut demander
            pour une sortie ; au-delà, sa décision devient ``Fail``. ``None`` :
            la politique borne elle-même ses réparations.
    """

    policy: Policy
    name: str
    points: frozenset[HookPoint]
    params: Mapping[str, JsonValue] = field(default_factory=dict[str, JsonValue])
    timeout: float | None = DEFAULT_POLICY_TIMEOUT
    on_error: OnError = "block"
    max_attempts: int | None = 1


type TracedEvent = ModelExchanged | ModelRetried | ModelFellBack | CircuitOpened | ModelResponded | JudgeEvaluated | BudgetExceeded
"""Ce qu'une politique fournie journalise de son propre travail.

Appel et verdict d'un juge (secours et disjoncteur compris), limite de budget
atteinte.
"""
type Trace = Callable[[TracedEvent], None]
"""Reçoit un événement à écrire, dans l'ordre."""
type PolicyEvent = PolicyDecided | GuardChecked | TracedEvent
"""Événement produit par l'évaluation des politiques d'un point."""


class TracingPolicy(ABC):
    """Politique fournie par loom-ia qui journalise son travail (juge, point 21 ; budget, fonction J4).

    ``decide_traced`` remplace ``decide`` quand le moteur l'exécute : ``trace``
    reçoit les événements à écrire (appel de modèle, verdict), dans l'ordre,
    avant les contrôles et la décision de la politique.
    """

    @abstractmethod
    async def decide_traced(self, subject: PolicySubject, context: PolicyContext, trace: Trace) -> Decision:
        """Décision de la politique, ses événements remis à ``trace`` à mesure.

        Args:
            subject: Le sujet du point d'accroche.
            context: Contexte de la politique (nom, paramètres, tentative,
                contrôles, historique).
            trace: Reçoit les événements à écrire.

        Returns:
            La décision.
        """
        ...

    async def decide(self, subject: PolicySubject, context: PolicyContext) -> Decision:
        """Décision seule, sans journal (hors du moteur).

        Args:
            subject: Le sujet du point d'accroche.
            context: Contexte de la politique.

        Returns:
            La décision ; les événements sont perdus.
        """
        return await self.decide_traced(subject, context, lambda _: None)


@dataclass(frozen=True, slots=True)
class Verdict:
    """Issue de la chaîne des politiques d'un point.

    ``decision`` est ``Continue`` si aucune politique n'a arrêté la chaîne
    (``subject`` porte alors les valeurs éventuellement remplacées), sinon la
    décision qui l'a arrêtée, et ``by`` la politique qui l'a rendue.
    ``events`` : les événements à écrire, dans l'ordre (contrôles, puis décisions).

    Attributes:
        decision: ``Continue``, ou la décision qui a arrêté la chaîne.
        subject: Le sujet, valeurs remplacées comprises.
        events: Les événements à écrire, dans l'ordre.
        by: Nom de la politique qui a arrêté la chaîne, ou ``None``.
    """

    decision: Decision
    subject: PolicySubject
    events: tuple[PolicyEvent, ...] = ()
    by: str | None = None

    @property
    def decided(self) -> tuple[PolicyDecided, ...]:
        """Les ``policy.decided`` des événements."""
        return tuple(e for e in self.events if isinstance(e, PolicyDecided))

    @property
    def checked(self) -> tuple[GuardChecked, ...]:
        """Les ``guard.checked`` des événements."""
        return tuple(e for e in self.events if isinstance(e, GuardChecked))

    @property
    def replaced(self) -> bool:
        """Vrai si une politique a remplacé la valeur (``Replace``)."""
        return any(d.decision == "replace" for d in self.decided)

    @property
    def unverified(self) -> bool:
        """Vrai si un contrôle a gardé la sortie sans qu'elle respecte son contrat."""
        return any(c.resolution == "unverified" for c in self.checked)

    @property
    def fallback(self) -> bool:
        """Vrai si un contrôle a remplacé la sortie par son message de repli."""
        return any(c.resolution == "fallback" for c in self.checked)

    @property
    def spent(self) -> tuple[Usage, float]:
        """Consommation des appels de modèle faits par les politiques (juges).

        Returns:
            Les tokens et le coût en dollars, sommés sur les
            ``model.responded`` des événements.
        """
        usage, cost = Usage(), 0.0
        for event in self.events:
            if isinstance(event, ModelResponded):
                usage, cost = usage + event.usage, cost + event.cost_usd
        return usage, cost


class Policies:
    """Politiques d'un agent, dans l'ordre déclaré.

    Args:
        bound: Les politiques branchées.

    Attributes:
        bound: Les politiques branchées, dans l'ordre.
    """

    def __init__(self, bound: Iterable[BoundPolicy] = ()) -> None:
        self.bound: tuple[BoundPolicy, ...] = tuple(bound)

    def __bool__(self) -> bool:
        return bool(self.bound)

    def __repr__(self) -> str:
        return f"Policies({', '.join(b.name for b in self.bound)})"

    def at(self, point: HookPoint) -> tuple[BoundPolicy, ...]:
        """Politiques branchées à ce point, dans l'ordre déclaré."""
        return tuple(b for b in self.bound if point in b.points)

    async def run(
        self,
        subject: PolicySubject,
        *,
        call_id: str | None = None,
        ignore: frozenset[DecisionKind] = frozenset(),
        check_arguments: ArgumentsCheck | None = None,
        attempts: Mapping[str, int] | None = None,
        turns: tuple[tuple[Message, ...], ...] = (),
        summary: str | None = None,
    ) -> Verdict:
        """Évalue la chaîne du point de ``subject``.

        ``ignore`` : décisions sans effet dans la situation (``Stop`` pendant
        la réponse forcée), traitées comme ``Continue`` et non journalisées.
        ``check_arguments`` vérifie des arguments remplacés (``before_tool``).
        ``attempts`` : réparations déjà demandées par politique, quand elles se
        comptent ailleurs que dans le run (par appel, pour un rôle).

        Args:
            subject: Le sujet ; son ``point`` désigne les politiques.
            call_id: Appel d'outil concerné, recopié dans les événements.
            ignore: Décisions traitées comme ``Continue``.
            check_arguments: Contrôle des arguments remplacés.
            attempts: Réparations déjà demandées, par politique ; à défaut,
                ``RunState.retries``.
            turns: Historique de la session, par tour, pour le contexte des
                politiques.
            summary: Dernier résumé de compaction.

        Returns:
            Le verdict. Une politique en erreur donne ``Fail`` avec
            ``on_error: block``, ou est sautée avec ``allow`` ; un ``Retry``
            au-delà de ``max_attempts`` devient ``Fail``.
        """
        point = subject.point
        current: PolicySubject = subject
        decided: list[PolicyEvent] = []
        for bound in self.at(point):
            state = current.state
            counts = attempts if attempts is not None else state.retries
            context = PolicyContext(
                name=bound.name,
                params=bound.params,
                attempt=counts.get(bound.name, 0),
                turns=turns,
                summary=summary,
            )
            traced: list[TracedEvent] = []
            try:
                try:
                    decision = await self._decide(bound, current, context, traced.append)
                finally:
                    decided += traced
                    decided += [_checked(bound, check, call_id, context) for check in context.checks]
                if decision.kind in ignore:
                    continue
                if isinstance(decision, Replace):
                    current = _replaced(current, decision.value, check_arguments)
                limit = bound.max_attempts
                if isinstance(decision, Retry) and limit is not None and context.attempt >= limit:
                    decision = Fail(f"{limit} réparation(s) demandée(s) sans succès ; dernier diagnostic : {decision.feedback}")
            except PolicyFailure as exc:
                # Un juge que le rejeu arrête n'est pas en panne (jalon J6.2b).
                logger.log(
                    logging.DEBUG if stopped_by_client(exc) else logging.WARNING,
                    "Politique %s (%s) en erreur : %s",
                    bound.name,
                    point,
                    exc,
                    extra={"run_id": state.run_id},
                )
                if bound.on_error == "allow":
                    decided.append(_event(bound, point, CONTINUE, call_id, context, str(exc), error=True))
                    continue
                failed = Fail(f"politique {bound.name} en erreur : {exc}")
                decided.append(_event(bound, point, failed, call_id, context, error=True))
                return Verdict(failed, current, tuple(decided), bound.name)
            if isinstance(decision, Continue):
                continue
            event = _event(bound, point, decision, call_id, context, subject=current)
            decided.append(event)
            # Sans la raison : elle peut citer un texte du modèle ou d'un outil, et les
            # logs INFO n'ont aucun contenu (la raison reste dans ``policy.decided``).
            logger.info(
                "Politique %s (%s) : %s",
                bound.name,
                point,
                decision.kind,
                extra={"run_id": state.run_id},
            )
            if isinstance(decision, Replace):
                continue
            return Verdict(decision, current, tuple(decided), bound.name)
        return Verdict(CONTINUE, current, tuple(decided))

    async def _decide(self, bound: BoundPolicy, subject: PolicySubject, context: PolicyContext, trace: Trace) -> Decision:
        """Décision de la politique, contrôlée.

        Raises:
            PolicyFailure: Délai dépassé, exception de la politique, valeur
                qui n'est pas une décision, décision non déclarée par la
                politique (``decisions``) ou non permise à ce point.
        """
        scope = asyncio.timeout(bound.timeout)
        policy = bound.policy
        try:
            async with scope:
                # Une politique écrite à la main peut rendre n'importe quoi.
                if isinstance(policy, TracingPolicy):
                    decided = await policy.decide_traced(subject, context, trace)
                else:
                    decided = await policy.decide(subject, context)
                decision = cast(object, decided)
        except TimeoutError as exc:
            if scope.expired():
                raise PolicyFailure(f"délai de {bound.timeout:g} s dépassé") from exc
            raise PolicyFailure(f"{type(exc).__name__}: {exc}") from exc
        except PolicyFailure:
            # Erreur déjà décrite par la politique (juge : modèle en échec, verdict invalide).
            raise
        except Exception as exc:
            raise PolicyFailure(f"{type(exc).__name__}: {exc}") from exc
        if not isinstance(decision, _DECISION_TYPES):
            raise PolicyFailure(f"décision attendue, reçu {type(decision).__name__}")
        kind = decision.kind
        if kind != "continue" and kind not in bound.policy.decisions:
            raise PolicyFailure(f"décision {kind!r} non déclarée par la politique")
        if kind not in ALLOWED_DECISIONS[subject.point]:
            raise PolicyFailure(f"décision {kind!r} non permise au point {subject.point}")
        return decision


_DECISION_TYPES: Final = (Continue, Replace, Retry, Deny, Pause, Stop, Fail)
"""Types de décision qu'une politique peut rendre."""


def _replaced(subject: PolicySubject, value: object, check: ArgumentsCheck | None) -> PolicySubject:
    """Sujet dont la valeur est remplacée.

    ``before_model`` : une ``ModelRequest`` ; ``before_tool`` : un objet JSON
    accepté par le schéma de l'outil ; ``after_tool`` : un ``ToolOutput`` ;
    ``on_output`` : un message de l'assistant ou un texte.

    Raises:
        PolicyFailure: Valeur du mauvais type, arguments refusés, ou point
            sans remplacement.
    """
    match subject:
        case BeforeModel():
            if not isinstance(value, ModelRequest):
                raise PolicyFailure(f"Replace : ModelRequest attendue, reçu {type(value).__name__}")
            return dataclasses.replace(subject, request=value)
        case BeforeTool():
            try:
                arguments = _ARGUMENTS.validate_python(value)
            except ValidationError as exc:
                raise PolicyFailure(f"Replace : objet JSON attendu ({exc.error_count()} erreur(s))") from exc
            problem = check(arguments) if check is not None else None
            if problem is not None:
                raise PolicyFailure(f"Replace : arguments refusés par le schéma de l'outil. {problem}")
            return dataclasses.replace(subject, arguments=arguments)
        case AfterTool():
            if not isinstance(value, ToolOutput):
                raise PolicyFailure(f"Replace : ToolOutput attendu, reçu {type(value).__name__}")
            return dataclasses.replace(subject, output=value)
        case OnOutput():
            if isinstance(value, str):
                value = Message(role="assistant", blocks=(TextBlock(text=value),))
            if not isinstance(value, Message) or value.role != "assistant":
                raise PolicyFailure("Replace : message de l'assistant ou texte attendu")
            return dataclasses.replace(subject, output=value)
        case _:
            raise PolicyFailure(f"Replace impossible au point {subject.point}")


def _checked(bound: BoundPolicy, check: GuardCheck, call_id: str | None, context: PolicyContext) -> GuardChecked:
    """``guard.checked`` d'un contrôle enregistré par une politique."""
    return GuardChecked(
        guard=check.guard,
        target=check.target,
        outcome=check.outcome,
        reason=check.reason,
        attempt=context.attempt + 1,
        normalized=check.normalized,
        resolution=check.resolution,
        policy=bound.name,
        call_id=call_id,
    )


def _event(
    bound: BoundPolicy,
    point: HookPoint,
    decision: Decision,
    call_id: str | None,
    context: PolicyContext,
    reason: str | None = None,
    *,
    subject: PolicySubject | None = None,
    error: bool = False,
) -> PolicyDecided:
    """``policy.decided`` d'une décision.

    Avec ``Replace``, les arguments (``before_tool``) ou la sortie
    (``on_output``) remplacés ; avec ``Retry``, le rang de la réparation et les
    outils permis.

    Args:
        bound: La politique.
        point: Le point d'accroche.
        decision: La décision.
        call_id: Appel d'outil concerné.
        context: Contexte de la politique.
        reason: Raison à écrire à la place de celle de la décision.
        subject: Le sujet, valeurs remplacées comprises.
        error: Vrai si la politique est en erreur.
    """
    fields: dict[str, object] = {}
    match decision:
        case Replace(reason=text):
            if isinstance(subject, BeforeTool):
                fields["arguments"] = subject.arguments
            if isinstance(subject, OnOutput):
                fields["output"] = subject.output
        case Retry(feedback=text, tools=tools):
            fields |= {"attempt": context.attempt + 1, "tools": tools}
        case Fail(error=text):
            pass
        case Continue():
            text = ""
        case _:
            text = decision.reason
    return PolicyDecided.model_validate(
        {
            "policy": bound.name,
            "point": point,
            "decision": decision.kind,
            "reason": reason if reason is not None else text,
            "call_id": call_id,
            "error": error,
            **fields,
        }
    )
