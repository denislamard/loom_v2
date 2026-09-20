# SPDX-License-Identifier: Apache-2.0
"""Ledger : la consommation d'une session, appel par appel (J1 à J3, J5).

Projection des ``model.responded`` du journal : chaque appel de modèle
(orchestrateur, rôle, juge, sous-agent) avec son run, son agent, son rôle
(celui de l'enveloppe : ``main``, le nom du rôle, ``judge:<nom>``), son
modèle, son usage et son coût. La consommation d'un sous-agent recopiée dans
le ``tool.completed`` de son appel n'y entre pas : ses appels y sont déjà,
dans son propre run.

La ventilation (par run, rôle, modèle, session) et le rapport se calculent
à partir de ces lignes ; le budget de session (``loom.budget``) en fait la
somme pour les runs qui précèdent le run en cours.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from loom_ia.core.events import Event, ModelResponded
from loom_ia.core.model import MAIN_ROLE, RunId, SessionId, Spent, TenantId, Usage


@dataclass(frozen=True, slots=True, kw_only=True)
class LedgerEntry:
    """Un appel de modèle, tel que le journal le rapporte."""

    seq: int
    ts: datetime
    tenant_id: TenantId
    session_id: SessionId
    run_id: RunId
    root_run_id: RunId
    agent: str
    # ``main``, le nom d'un rôle, ou ``judge:<nom>``.
    role: str
    model_id: str
    provider: str
    usage: Usage
    cost: float
    # Appel d'outil servi (rôle délégué, juge d'un rôle).
    call_id: str | None = None
    judge: str | None = None

    @property
    def spent(self) -> Spent:
        return Spent(self.usage, self.cost, 1)


def ledger(events: Iterable[Event]) -> tuple[LedgerEntry, ...]:
    """Appels de modèle d'un ensemble d'événements, dans l'ordre du journal."""
    return tuple(
        LedgerEntry(
            seq=event.seq,
            ts=event.ts,
            tenant_id=event.tenant_id,
            session_id=event.session_id,
            run_id=event.run_id,
            root_run_id=event.root_run_id,
            agent=event.agent or "",
            role=event.role or MAIN_ROLE,
            model_id=payload.model_id,
            provider=payload.provider,
            usage=payload.usage,
            cost=payload.cost_usd,
            call_id=payload.call_id,
            judge=payload.judge,
        )
        for event in events
        if isinstance(payload := event.payload, ModelResponded)
    )


def spent(events: Iterable[Event]) -> Spent:
    """Consommation totale des appels de modèle d'un ensemble d'événements."""
    total = Spent()
    for entry in ledger(events):
        total += entry.spent
    return total
