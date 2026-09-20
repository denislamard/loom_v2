# SPDX-License-Identifier: Apache-2.0
"""Budget d'un run et de sa session : politique fournie ``loom.budget`` (J4, #1).

Avant chaque appel de l'orchestrateur (``before_model``), la politique
compare la consommation aux limites de l'agent :

- **run** : coût, tokens (sous-agents compris) et appels de modèle du run
  (orchestrateur, rôles, juges) ; la part reçue d'un parent (``budget_share``)
  s'y ajoute, la limite la plus basse l'emporte ;
- **session** (run racine seulement) : consommation des runs précédents de la
  session, plus celle du run.

Une limite atteinte écrit un ``budget.exceeded``, une fois par limite et par
run. Avec ``on_exceed: stop``, la décision est ``Stop`` : le run passe en
``FINALIZING`` et produit une réponse forcée sans outils ; le dépassement est
borné à cette dernière génération. Avec ``warn``, le run continue.
"""

from typing import Final

from loom_ia.core.events import BudgetExceeded
from loom_ia.core.model import (
    CONTINUE,
    BeforeModel,
    BudgetLimit,
    Budgets,
    BudgetScope,
    Decision,
    DecisionKind,
    HookPoint,
    PolicyContext,
    PolicySubject,
    Stop,
)
from loom_ia.engine import Trace, TracingPolicy

BUDGET_POLICY: Final = "loom.budget"

type Reached = tuple[BudgetScope, BudgetLimit, float, float]


class BudgetGuard(TracingPolicy):
    """Politique ``loom.budget`` : limites du run et de la session, avant chaque appel."""

    def __init__(self, budgets: Budgets) -> None:
        self.budgets = budgets

    @property
    def name(self) -> str:
        return BUDGET_POLICY

    @property
    def points(self) -> frozenset[HookPoint]:
        return frozenset({"before_model"})

    @property
    def decisions(self) -> frozenset[DecisionKind]:
        return frozenset({"stop"})

    def __repr__(self) -> str:
        return f"BudgetGuard({self.budgets!r})"

    async def decide_traced(
        self, subject: PolicySubject, context: PolicyContext, trace: Trace
    ) -> Decision:
        if not isinstance(subject, BeforeModel) or subject.finalizing:
            return CONTINUE
        reached = self.reached(subject)
        if not reached:
            return CONTINUE
        action = self.budgets.on_exceed
        seen = subject.state.exceeded
        for scope, limit, value, used in reached:
            if f"{scope}.{limit}" not in seen:
                trace(
                    BudgetExceeded(
                        scope=scope,
                        limit=limit,
                        value=value,
                        spent=used,
                        action=action,
                        policy=context.name,
                    )
                )
        if action == "stop":
            return Stop("; ".join(describe(*r) for r in reached))
        return CONTINUE

    def reached(self, subject: BeforeModel) -> list[Reached]:
        """Limites atteintes : portée, limite, plafond, consommation."""
        state = subject.state
        run = self.budgets.run.tightest(state.budget)
        used = state.spent
        checks = [
            _check("run", "max_cost", run.max_cost, used.cost),
            _check("run", "max_tokens", run.max_tokens, used.tokens),
            _check("run", "max_calls", run.max_calls, used.calls),
        ]
        if state.parent_run_id is None:
            session = subject.session + used
            limits = self.budgets.session
            checks += [
                _check("session", "max_cost", limits.max_cost, session.cost),
                _check("session", "max_tokens", limits.max_tokens, session.tokens),
            ]
        return [
            (scope, limit, value, used)
            for scope, limit, value, used in checks
            if value is not None and used >= value
        ]


def _check(
    scope: BudgetScope, limit: BudgetLimit, value: float | None, used: float
) -> tuple[BudgetScope, BudgetLimit, float | None, float]:
    return scope, limit, value, used


def describe(scope: BudgetScope, limit: BudgetLimit, value: float, used: float) -> str:
    """Une limite atteinte, en clair : « budget du run atteint : 0,00231 $ (plafond 0,00200 $) »."""
    where = "du run" if scope == "run" else "de la session"
    return f"budget {where} atteint : {amount(limit, used)} (plafond {amount(limit, value)})"


def amount(limit: BudgetLimit, value: float) -> str:
    """Une quantité de budget : dollars, tokens ou appels."""
    match limit:
        case "max_cost":
            return f"{value:.5f} $".replace(".", ",")
        case "max_tokens":
            return f"{int(value)} tokens"
        case "max_calls":
            return f"{int(value)} appel{'s' if value > 1 else ''}"
