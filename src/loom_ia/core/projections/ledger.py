# SPDX-License-Identifier: Apache-2.0
"""Ledger : la consommation d'une session, appel par appel (fonctions J1 à J3 et J5).

Projection des ``model.responded`` du journal : chaque appel de modèle
(orchestrateur, rôle, juge, sous-agent) avec son run, son agent, son rôle
(celui de l'enveloppe : ``main``, le nom du rôle, ``judge:<nom>``), son
modèle — son identifiant dans la config et son nom chez le fournisseur —, son
usage et son coût. La consommation d'un sous-agent recopiée dans
le ``tool.completed`` de son appel n'y entre pas : ses appels y sont déjà,
dans son propre run.

S'y ajoutent les tentatives ratées que le fournisseur a facturées
(``model.retried`` avec ``usage``, fonction J1) : ``failed``, elles comptent dans
l'usage et le coût, pas dans le nombre d'appels (``max_calls``).

La ventilation (par run, rôle, modèle, session) et le rapport se calculent
à partir de ces lignes ; le budget de session (``loom.budget``) en fait la
somme pour les runs qui précèdent le run en cours.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from loom_ia.core.events import Event, ModelResponded, ModelRetried
from loom_ia.core.model import MAIN_ROLE, RunId, SessionId, Spent, TenantId, Usage


@dataclass(frozen=True, slots=True, kw_only=True)
class LedgerEntry:
    """Un appel de modèle, tel que le journal le rapporte : une ligne du ledger.

    Attributes:
        seq: Position de l'événement dans le journal de la session.
        ts: Horodatage de l'événement.
        tenant_id: Client.
        session_id: Session.
        run_id: Run qui a fait l'appel.
        root_run_id: Run racine de son arbre.
        agent: Agent du run.
        role: ``main`` (l'orchestrateur), le nom d'un rôle délégué, ou
            ``judge:<nom>``.
        model_id: Nom du modèle chez le fournisseur (``claude-haiku-5-5``).
        model: Identifiant du modèle dans la config (``HAIKU``) ; ``None``
            pour un journal antérieur.
        provider: Fournisseur.
        usage: Tokens consommés.
        cost: Coût, en dollars.
        call_id: Appel d'outil servi (rôle délégué, juge d'un rôle).
        judge: Juge qui a fait l'appel.
        failed: Vrai pour une tentative ratée, facturée par le fournisseur :
            elle compte dans l'usage et le coût, pas comme un appel
            (fonction J1).
    """

    seq: int
    ts: datetime
    tenant_id: TenantId
    session_id: SessionId
    run_id: RunId
    root_run_id: RunId
    agent: str
    role: str
    model_id: str
    model: str | None
    provider: str
    usage: Usage
    cost: float
    call_id: str | None = None
    judge: str | None = None
    failed: bool = False

    @property
    def spent(self) -> Spent:
        """Consommation de la ligne : usage, coût, et 1 appel (0 pour une tentative ratée)."""
        return Spent(self.usage, self.cost, 0 if self.failed else 1)

    @property
    def model_key(self) -> str:
        """Modèle de l'appel, pour ventiler : son id dans la config, sinon son nom chez le fournisseur."""
        return self.model if self.model is not None else self.model_id


def ledger(events: Iterable[Event]) -> tuple[LedgerEntry, ...]:
    """Appels de modèle d'un ensemble d'événements, dans l'ordre du journal.

    Args:
        events: Événements d'une session, d'un run ou d'un arbre.

    Returns:
        Une ligne par ``model.responded``, et une par ``model.retried``
        facturé (``failed``).
    """
    entries: list[LedgerEntry] = []
    for event in events:
        match event.payload:
            case ModelResponded() as payload:
                usage, cost, failed = payload.usage, payload.cost_usd, False
            case ModelRetried(usage=Usage() as usage, cost_usd=cost) as payload:
                cost, failed = cost or 0.0, True
            case _:
                continue
        entries.append(
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
                model=payload.model,
                provider=payload.provider,
                usage=usage,
                cost=cost,
                call_id=payload.call_id,
                judge=payload.judge,
                failed=failed,
            )
        )
    return tuple(entries)


def spent(events: Iterable[Event]) -> Spent:
    """Consommation totale des appels de modèle d'un ensemble d'événements.

    Args:
        events: Événements à compter.

    Returns:
        La somme des lignes du ledger : usage, coût et nombre d'appels.
    """
    total = Spent()
    for entry in ledger(events):
        total += entry.spent
    return total
