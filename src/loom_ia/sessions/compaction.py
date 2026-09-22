# SPDX-License-Identifier: Apache-2.0
"""Compaction d'une session : résumer les échanges anciens (#23, §11.3).

Compacter ne réécrit rien. On ajoute un ``session.compacted`` qui porte le
résumé et la position qu'il couvre ; l'historique repart de ce résumé, et les
``keep_last`` derniers tours restent intacts. Un tour est un run : la coupe
tombe donc toujours sur une frontière de run, et la séquence envoyée au
modèle reste valide.

Le résumé est produit par un agent ordinaire, ``_compaction``, monté comme
les autres : il hérite du retry, du modèle de secours, du suivi des coûts et
de la réparation. Son run vit dans le journal de la session (``kind:
compaction``, ``triggered_by``) mais n'entre pas dans son historique.

Après le marqueur, le job écrit un snapshot rafraîchi qui contient déjà le
résumé : c'est ce qui fait que « le marqueur au plus grand ``up_to_seq``
gagne » reste vrai. Un plantage entre les deux laisse un historique non
réduit mais juste, et la compaction sera refaite.

Filet de sécurité : au démarrage d'un run, si l'historique dépasse
``hard_tokens``, ``ensure_fits`` compacte en synchrone ; si cela échoue, les
tours les plus anciens sont retirés (``session.trimmed``), avec un
avertissement dans le journal.
"""

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Final

from loom_ia.core.events import (
    Event,
    RunScope,
    RunStarted,
    SessionCompacted,
    SessionSnapshot,
    SessionTrimmed,
)
from loom_ia.core.model import (
    Message,
    RunId,
    RunState,
    RunStatus,
    SessionId,
    TenantId,
    new_run_id,
)
from loom_ia.core.ports import EventStore
from loom_ia.core.projections import history
from loom_ia.engine import (
    RunContext,
    SessionWriter,
    SessionWriters,
    begin_run,
    drive,
    rendered,
)
from loom_ia.sessions.snapshot import boundary, estimate_tokens

logger = logging.getLogger(__name__)

# L'agent interne, monté pour le client dont on résume la session (L1) : deux
# clients ne résument pas leurs conversations avec le même modèle ni les mêmes
# identifiants.
type SummaryResolver = Callable[[str, TenantId], RunContext]

# Résultat d'outil tronqué dans le segment : le résumé n'a pas besoin du
# détail, et le contrôle de fidélité compare au segment tel qu'il est envoyé.
MAX_RESULT_CHARS: Final = 800
CUT_MARK: Final = "… (résultat tronqué)"


@dataclass(frozen=True, slots=True, kw_only=True)
class CompactionPlan:
    """Réglages de la compaction, tirés de la config par le câblage."""

    agent: str
    over_tokens: int
    hard_tokens: int
    keep_last: int
    fidelity_check: bool = True


def run_starts(events: Sequence[Event]) -> list[int]:
    """Position du ``run.started`` de chaque run ordinaire de la session."""
    return [
        event.seq
        for event in events
        if isinstance(payload := event.payload, RunStarted)
        and payload.parent_run_id is None
        and payload.kind == "normal"
    ]


def summarised(events: Sequence[Event]) -> int:
    """Position déjà couverte par un résumé ou une coupe, 0 sinon."""
    covered = 0
    for event in events:
        payload = event.payload
        if isinstance(payload, SessionCompacted | SessionTrimmed):
            covered = max(covered, payload.up_to_seq)
    return covered


def cut(events: Sequence[Event], keep_last: int) -> int:
    """Position jusqu'à laquelle résumer, en gardant les derniers tours intacts."""
    limit = boundary(events)
    if keep_last <= 0:
        return limit
    starts = run_starts(events)
    if len(starts) <= keep_last:
        return 0
    return min(limit, starts[-keep_last] - 1)


def oversized(events: Sequence[Event], limit: int) -> bool:
    """Vrai si l'historique de la session dépasse ce nombre de tokens estimé."""
    return estimate_tokens(history(events)) > limit


class CompactionJob:
    """Résume un segment de session, puis écrit le marqueur et un snapshot."""

    def __init__(
        self,
        resolve: SummaryResolver,
        store: EventStore,
        writers: SessionWriters,
        plan: CompactionPlan,
    ) -> None:
        self._resolve = resolve
        self._store = store
        self._writers = writers
        self.plan = plan

    def __repr__(self) -> str:
        return f"CompactionJob(agent={self.plan.agent!r}, keep_last={self.plan.keep_last})"

    def key(self, session_id: SessionId, up_to_seq: int) -> str:
        """Clé de dédoublonnage : un seul résumé par session et par position."""
        return f"compaction:{session_id}:{up_to_seq}"

    def pending(self, events: Sequence[Event], *, limit: int | None = None) -> int | None:
        """Position à résumer, ou ``None`` s'il n'y a rien à faire.

        ``limit`` : seuil de déclenchement ; ``over_tokens`` par défaut.
        """
        if not oversized(events, self.plan.over_tokens if limit is None else limit):
            return None
        up_to_seq = cut(events, self.plan.keep_last)
        return up_to_seq if up_to_seq > summarised(events) else None

    async def compact(
        self,
        tenant_id: TenantId,
        session_id: SessionId,
        *,
        triggered_by: RunId | None = None,
        limit: int | None = None,
    ) -> SessionCompacted | None:
        """Résume la session si un segment le mérite ; rend le marqueur écrit."""
        events = await self._store.read(tenant_id, session_id)
        up_to_seq = self.pending(events, limit=limit)
        if up_to_seq is None:
            return None
        segment = history([event for event in events if event.seq <= up_to_seq])
        if not segment:
            return None
        before = estimate_tokens(segment)
        final = await self._summarise(tenant_id, session_id, rendered(segment), triggered_by)
        if final is None or final.output is None or not final.output.text.strip():
            return None
        summary = final.output.text.strip()
        kept = sum(1 for start in run_starts(events) if start > up_to_seq)
        payload = SessionCompacted(
            up_to_seq=up_to_seq,
            summary=summary,
            kept=kept,
            tokens_before=before,
            tokens_after=estimate_tokens([Message.user(summary)]),
            cost_usd=final.cost_usd,
            fidelity=("warning" if final.unverified else "ok")
            if self.plan.fidelity_check
            else "skipped",
        )
        writer = await self._writers.open(self._store, tenant_id, session_id)
        # Sans contrôle de séquence : le marqueur ne couvre que des événements
        # anciens, et le worker ne partage pas l'écrivain des runs en cours.
        marker = await writer.append([_scope(final).draft(payload)], checked=False)
        await self._refresh(writer, final, marker[0])
        logger.info(
            "Session %s compactée jusqu'à %d : %d → %d tokens estimés (%s)",
            session_id,
            up_to_seq,
            payload.tokens_before,
            payload.tokens_after,
            payload.fidelity,
        )
        return payload

    async def ensure_fits(self, tenant_id: TenantId, session_id: SessionId) -> None:
        """Filet de sécurité : compacte en synchrone, ou retire les vieux tours."""
        events = await self._store.read(tenant_id, session_id)
        if not oversized(events, self.plan.hard_tokens):
            return
        compacted = await self.compact(tenant_id, session_id, limit=self.plan.hard_tokens)
        if compacted is not None:
            events = await self._store.read(tenant_id, session_id)
            if not oversized(events, self.plan.hard_tokens):
                return
        await self._trim(tenant_id, session_id, events)

    async def _trim(
        self, tenant_id: TenantId, session_id: SessionId, events: Sequence[Event]
    ) -> None:
        """Retire les tours les plus anciens jusqu'à repasser sous la limite.

        Si même le dernier tour seul dépasse la limite, on coupe quand même
        tout ce qui le précède : un historique trop gros n'est pas envoyable,
        alors qu'un historique amputé l'est.
        """
        before = len(history(events))
        limit = boundary(events)
        covered = summarised(events)
        cuts = [min(start - 1, limit) for start in run_starts(events)]
        usable = [up_to_seq for up_to_seq in cuts if up_to_seq > covered]
        if not usable:
            logger.warning("Session %s : rien à retirer de plus", session_id)
            return

        # Après la coupe, l'historique repart du marqueur : les snapshots plus
        # récents ne s'appliquent plus, on les retire donc du calcul.
        def after(up_to_seq: int) -> list[Event]:
            return [e for e in events if e.seq > up_to_seq and e.category != "session"]

        fitting = (
            up_to_seq
            for up_to_seq in usable
            if not oversized(after(up_to_seq), self.plan.hard_tokens)
        )
        # À défaut d'une coupe qui suffise, la plus large : le dernier tour seul.
        up_to_seq = next(fitting, usable[-1])
        kept = after(up_to_seq)
        writer = await self._writers.open(self._store, tenant_id, session_id)
        payload = SessionTrimmed(
            up_to_seq=up_to_seq,
            dropped=before - len(history(kept)),
            reason=f"historique au-delà de {self.plan.hard_tokens} tokens sans résumé",
        )
        await writer.append([_session_scope(tenant_id, session_id).draft(payload)])
        logger.warning(
            "Session %s : %d message(s) retiré(s) faute de résumé%s",
            session_id,
            payload.dropped,
            "" if not oversized(kept, self.plan.hard_tokens) else ", et c'est encore trop long",
        )

    async def _summarise(
        self,
        tenant_id: TenantId,
        session_id: SessionId,
        segment: str,
        triggered_by: RunId | None,
    ) -> RunState | None:
        """Fait tourner l'agent interne sur le segment ; ``None`` s'il échoue."""
        context = self._resolve(self.plan.agent, tenant_id)
        writer = await self._writers.open(context.store, tenant_id, session_id)
        run_id = new_run_id()
        state = await begin_run(
            context,
            Message.user(segment),
            session_id=session_id,
            run_id=run_id,
            writer=writer,
            kind="compaction",
            triggered_by=triggered_by,
        )
        final = await drive(
            context, state.run_id, session_id=session_id, tenant_id=tenant_id, writer=writer
        )
        if final.status is not RunStatus.COMPLETED:
            logger.warning(
                "Compaction de %s : le run %s s'est arrêté en %s (%s)",
                session_id,
                final.run_id,
                final.status,
                final.error or "sans message",
            )
            return None
        return final

    async def _refresh(self, writer: SessionWriter, final: RunState, marker: Event) -> None:
        """Snapshot qui contient déjà le résumé : il prend le pas sur le marqueur.

        Il couvre le marqueur lui-même, sinon il le devancerait et l'historique
        repartirait de l'ancien snapshot. Si un run tourne encore dans la
        session, on s'abstient : le marqueur seul laisse un historique non
        réduit mais juste, et la compaction sera refaite.
        """
        events = await self._store.read(final.context.tenant_id, final.session_id)
        if not events or events[-1].seq < marker.seq:
            return
        plain = max((e.seq for e in events if e.category != "session"), default=0)
        if boundary(events) < plain:
            return
        messages = tuple(history(events))
        if not messages:
            return
        await writer.append(
            [
                _scope(final).draft(
                    SessionSnapshot(
                        up_to_seq=events[-1].seq,
                        messages=messages,
                        tokens=estimate_tokens(messages),
                    )
                )
            ]
        )


def _scope(state: RunState) -> RunScope:
    return RunScope(
        tenant_id=state.context.tenant_id,
        session_id=state.session_id,
        run_id=state.run_id,
        root_run_id=state.root_run_id,
        agent=state.agent,
        span_id=state.span_id,
    )


def _session_scope(tenant_id: TenantId, session_id: SessionId) -> RunScope:
    """Portée d'un marqueur écrit hors de tout run (coupe de sécurité)."""
    run_id = RunId(session_id)
    return RunScope(
        tenant_id=tenant_id,
        session_id=session_id,
        run_id=run_id,
        root_run_id=run_id,
        agent="",
    )
