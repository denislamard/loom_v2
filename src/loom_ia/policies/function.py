# SPDX-License-Identifier: Apache-2.0
"""Politiques écrites en Python (#1, #2).

Une fonction reçoit ce qui se passe au point d'accroche, et éventuellement
son contexte (nom, paramètres de la config, réparations déjà demandées) ;
elle rend une décision. Elle peut être synchrone ou asynchrone.

    @policy(points=["before_tool"], decisions=["replace", "deny"])
    def numero_devis(subject: BeforeTool, context: PolicyContext) -> Decision:
        '''Normalise le numéro de devis, refuse un numéro mal formé.'''
        ...

``points`` : là où la politique sait s'appliquer ; un agent la branche sur
tout ou partie de ces points. ``decisions`` : ce qu'elle peut rendre en plus
de ``Continue``. Une décision non permise à l'un des points est refusée au
démarrage ; une décision non déclarée, rendue quand même, est une erreur de
la politique.

Une fonction synchrone s'exécute dans la boucle : elle doit être rapide et
ne pas faire d'entrée-sortie (sinon, l'écrire en ``async``).
"""

import inspect
import re
from collections.abc import Awaitable, Callable, Iterable
from typing import cast

from loom_ia.core.model import (
    DECISION_KINDS,
    HOOK_POINTS,
    POLICY_NAME_PATTERN,
    RESERVED_PREFIX,
    Decision,
    DecisionKind,
    HookPoint,
    PolicyContext,
    PolicySubject,
)

type PolicyFunction = Callable[..., Decision | Awaitable[Decision]]


class FunctionPolicy:
    """Politique construite à partir d'une fonction ; reste appelable comme elle."""

    def __init__(
        self,
        fn: PolicyFunction,
        *,
        points: Iterable[HookPoint],
        decisions: Iterable[DecisionKind],
        name: str | None = None,
        builtin: bool = False,
    ) -> None:
        self.fn = fn
        policy_name = name or fn.__name__
        if not re.fullmatch(POLICY_NAME_PATTERN, policy_name):
            raise ValueError(f"Politique {policy_name!r} : nom invalide ({POLICY_NAME_PATTERN})")
        if policy_name.startswith(RESERVED_PREFIX) != builtin:
            raise ValueError(
                f"Politique {policy_name!r} : le préfixe {RESERVED_PREFIX!r} est réservé "
                "aux politiques fournies par loom-ia"
            )
        self._name = policy_name
        self._points = cast(
            frozenset[HookPoint], _checked(points, HOOK_POINTS, "point d'accroche", policy_name)
        )
        if not self._points:
            raise ValueError(f"Politique {policy_name!r} : aucun point d'accroche déclaré")
        kinds = _checked(decisions, DECISION_KINDS, "décision", policy_name)
        self._decisions = cast(
            frozenset[DecisionKind], frozenset(kind for kind in kinds if kind != "continue")
        )
        self._with_context = _arity(fn, policy_name) == 2

    @property
    def name(self) -> str:
        return self._name

    @property
    def points(self) -> frozenset[HookPoint]:
        return self._points

    @property
    def decisions(self) -> frozenset[DecisionKind]:
        return self._decisions

    async def decide(self, subject: PolicySubject, context: PolicyContext) -> Decision:
        result = self.fn(subject, context) if self._with_context else self.fn(subject)
        if inspect.isawaitable(result):
            return await result
        return result

    def __call__(self, *args: object) -> Decision | Awaitable[Decision]:
        return self.fn(*args)

    def __repr__(self) -> str:
        return f"FunctionPolicy({self._name!r}, points={sorted(self._points)})"


def policy(
    *,
    points: Iterable[HookPoint],
    decisions: Iterable[DecisionKind],
    name: str | None = None,
) -> Callable[[PolicyFunction], FunctionPolicy]:
    """Déclare une fonction comme politique."""

    def wrap(fn: PolicyFunction) -> FunctionPolicy:
        return FunctionPolicy(fn, points=points, decisions=decisions, name=name)

    return wrap


def _checked[T: str](
    values: Iterable[T], known: tuple[T, ...], label: str, name: str
) -> frozenset[T]:
    found = frozenset(values)
    unknown = sorted(str(value) for value in found if value not in known)
    if unknown:
        raise ValueError(
            f"Politique {name!r} : {label} inconnu(e) : {', '.join(unknown)} "
            f"(connus : {', '.join(known)})"
        )
    return found


def _arity(fn: PolicyFunction, name: str) -> int:
    """Nombre de paramètres positionnels : le sujet, et éventuellement le contexte."""
    params = [
        p
        for p in inspect.signature(fn).parameters.values()
        if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD) and p.default is p.empty
    ]
    if len(params) not in (1, 2):
        raise TypeError(
            f"Politique {name!r} : la fonction reçoit le sujet, et éventuellement le "
            f"contexte ({len(params)} paramètre(s) obligatoire(s) trouvé(s))"
        )
    return len(params)
