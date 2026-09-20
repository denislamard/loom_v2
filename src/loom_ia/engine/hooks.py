# SPDX-License-Identifier: Apache-2.0
"""Exécution des politiques d'un agent aux points d'accroche de ``step`` (#1, #2).

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
  ``Fail``. Un guard qui gère lui-même ses réparations (contrat de sortie)
  n'a pas de borne ici (``max_attempts: None``).

Guards : une politique peut enregistrer ses contrôles dans son contexte
(``record``) ; ils deviennent des ``guard.checked``, écrits avant sa décision.

Juges (#21) : une politique fournie par loom-ia peut aussi journaliser son
propre travail (``TracingPolicy``) : l'appel du modèle d'un juge et son
verdict, écrits avant ses contrôles. Le type est nominal et interne : une
politique écrite par l'utilisateur n'écrit pas dans le journal.

Ce module ne fait qu'évaluer : ce que chaque décision change au run (requête
remplacée, appel refusé, réparation, arrêt, échec) est appliqué par la boucle
et l'exécuteur d'outils.
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
    GuardChecked,
    JudgeEvaluated,
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
from loom_ia.core.ports import Policy

logger = logging.getLogger(__name__)

# Délai par défaut d'une politique, en secondes.
DEFAULT_POLICY_TIMEOUT: Final = 5.0

type OnError = Literal["block", "allow"]
# Vérifie des arguments remplacés ; renvoie le problème, ou None s'ils conviennent.
type ArgumentsCheck = Callable[[dict[str, JsonValue]], str | None]

_ARGUMENTS: Final = TypeAdapter(dict[str, JsonValue])


class PolicyFailure(Exception):
    """Erreur d'une politique : exception, délai, décision ou valeur invalide."""


@dataclass(frozen=True, slots=True, kw_only=True)
class BoundPolicy:
    """Une politique telle qu'un agent la branche : points, paramètres, garde-fous."""

    policy: Policy
    # Nom dans le journal ; par défaut celui de la politique.
    name: str
    points: frozenset[HookPoint]
    params: Mapping[str, JsonValue] = field(default_factory=dict[str, JsonValue])
    timeout: float | None = DEFAULT_POLICY_TIMEOUT
    on_error: OnError = "block"
    # Réparations (``Retry``) que la politique peut demander pour une sortie ;
    # None : la politique borne elle-même ses réparations.
    max_attempts: int | None = 1


# Ce qu'une politique fournie journalise de son propre travail (appel et verdict d'un juge).
type TracedEvent = ModelRetried | ModelResponded | JudgeEvaluated
type Trace = Callable[[TracedEvent], None]
type PolicyEvent = PolicyDecided | GuardChecked | TracedEvent


class TracingPolicy(ABC):
    """Politique fournie par loom-ia qui journalise son travail (juge, #21).

    ``decide_traced`` remplace ``decide`` quand le moteur l'exécute : ``trace``
    reçoit les événements à écrire (appel de modèle, verdict), dans l'ordre,
    avant les contrôles et la décision de la politique.
    """

    @abstractmethod
    async def decide_traced(
        self, subject: PolicySubject, context: PolicyContext, trace: Trace
    ) -> Decision: ...

    async def decide(self, subject: PolicySubject, context: PolicyContext) -> Decision:
        """Décision seule, sans journal (hors du moteur)."""
        return await self.decide_traced(subject, context, lambda _: None)


@dataclass(frozen=True, slots=True)
class Verdict:
    """Issue de la chaîne des politiques d'un point.

    ``decision`` est ``Continue`` si aucune politique n'a arrêté la chaîne
    (``subject`` porte alors les valeurs éventuellement remplacées), sinon la
    décision qui l'a arrêtée, et ``by`` la politique qui l'a rendue.
    ``events`` : les événements à écrire, dans l'ordre (contrôles, puis décisions).
    """

    decision: Decision
    subject: PolicySubject
    events: tuple[PolicyEvent, ...] = ()
    by: str | None = None

    @property
    def decided(self) -> tuple[PolicyDecided, ...]:
        return tuple(e for e in self.events if isinstance(e, PolicyDecided))

    @property
    def checked(self) -> tuple[GuardChecked, ...]:
        return tuple(e for e in self.events if isinstance(e, GuardChecked))

    @property
    def replaced(self) -> bool:
        return any(d.decision == "replace" for d in self.decided)

    @property
    def unverified(self) -> bool:
        """Vrai si un contrôle a gardé la sortie sans qu'elle respecte son contrat."""
        return any(c.resolution == "unverified" for c in self.checked)

    @property
    def spent(self) -> tuple[Usage, float]:
        """Consommation des appels de modèle faits par les politiques (juges)."""
        usage, cost = Usage(), 0.0
        for event in self.events:
            if isinstance(event, ModelResponded):
                usage, cost = usage + event.usage, cost + event.cost_usd
        return usage, cost


class Policies:
    """Politiques d'un agent, dans l'ordre déclaré."""

    def __init__(self, bound: Iterable[BoundPolicy] = ()) -> None:
        self.bound: tuple[BoundPolicy, ...] = tuple(bound)

    def __bool__(self) -> bool:
        return bool(self.bound)

    def __repr__(self) -> str:
        return f"Policies({', '.join(b.name for b in self.bound)})"

    def at(self, point: HookPoint) -> tuple[BoundPolicy, ...]:
        return tuple(b for b in self.bound if point in b.points)

    async def run(
        self,
        subject: PolicySubject,
        *,
        call_id: str | None = None,
        ignore: frozenset[DecisionKind] = frozenset(),
        check_arguments: ArgumentsCheck | None = None,
        attempts: Mapping[str, int] | None = None,
    ) -> Verdict:
        """Évalue la chaîne du point de ``subject``.

        ``ignore`` : décisions sans effet dans la situation (``Stop`` pendant
        la réponse forcée), traitées comme ``Continue`` et non journalisées.
        ``check_arguments`` vérifie des arguments remplacés (``before_tool``).
        ``attempts`` : réparations déjà demandées par politique, quand elles se
        comptent ailleurs que dans le run (par appel, pour un rôle).
        """
        point = subject.point
        current: PolicySubject = subject
        decided: list[PolicyEvent] = []
        for bound in self.at(point):
            state = current.state
            counts = attempts if attempts is not None else state.retries
            context = PolicyContext(
                name=bound.name, params=bound.params, attempt=counts.get(bound.name, 0)
            )
            traced: list[TracedEvent] = []
            try:
                try:
                    decision = await self._decide(bound, current, context, traced.append)
                finally:
                    decided += traced
                    decided += [
                        _checked(bound, check, call_id, context) for check in context.checks
                    ]
                if decision.kind in ignore:
                    continue
                if isinstance(decision, Replace):
                    current = _replaced(current, decision.value, check_arguments)
                limit = bound.max_attempts
                if isinstance(decision, Retry) and limit is not None and context.attempt >= limit:
                    decision = Fail(
                        f"{limit} réparation(s) demandée(s) sans succès ; "
                        f"dernier diagnostic : {decision.feedback}"
                    )
            except PolicyFailure as exc:
                logger.warning(
                    "Politique %s (%s) en erreur : %s",
                    bound.name,
                    point,
                    exc,
                    extra={"run_id": state.run_id},
                )
                if bound.on_error == "allow":
                    decided.append(
                        _event(bound, point, CONTINUE, call_id, context, str(exc), error=True)
                    )
                    continue
                failed = Fail(f"politique {bound.name} en erreur : {exc}")
                decided.append(_event(bound, point, failed, call_id, context, error=True))
                return Verdict(failed, current, tuple(decided), bound.name)
            if isinstance(decision, Continue):
                continue
            event = _event(bound, point, decision, call_id, context, subject=current)
            decided.append(event)
            logger.info(
                "Politique %s (%s) : %s%s",
                bound.name,
                point,
                decision.kind,
                f" — {event.reason}" if event.reason else "",
                extra={"run_id": state.run_id},
            )
            if isinstance(decision, Replace):
                continue
            return Verdict(decision, current, tuple(decided), bound.name)
        return Verdict(CONTINUE, current, tuple(decided))

    async def _decide(
        self, bound: BoundPolicy, subject: PolicySubject, context: PolicyContext, trace: Trace
    ) -> Decision:
        """Décision de la politique, contrôlée ; lève ``PolicyFailure``."""
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


def _replaced(subject: PolicySubject, value: object, check: ArgumentsCheck | None) -> PolicySubject:
    """Sujet dont la valeur est remplacée ; lève ``PolicyFailure`` si elle ne convient pas."""
    match subject:
        case BeforeModel():
            if not isinstance(value, ModelRequest):
                raise PolicyFailure(f"Replace : ModelRequest attendue, reçu {type(value).__name__}")
            return dataclasses.replace(subject, request=value)
        case BeforeTool():
            try:
                arguments = _ARGUMENTS.validate_python(value)
            except ValidationError as exc:
                raise PolicyFailure(
                    f"Replace : objet JSON attendu ({exc.error_count()} erreur(s))"
                ) from exc
            problem = check(arguments) if check is not None else None
            if problem is not None:
                raise PolicyFailure(
                    f"Replace : arguments refusés par le schéma de l'outil. {problem}"
                )
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


def _checked(
    bound: BoundPolicy, check: GuardCheck, call_id: str | None, context: PolicyContext
) -> GuardChecked:
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
    """``policy.decided`` d'une décision."""
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
