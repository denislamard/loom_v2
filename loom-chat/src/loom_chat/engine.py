"""Moteur de la console : fait tourner les runs de loom et relaie les demandes à l'interface."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import datetime

from loom_ia.access.api import Loom, RunResult, UnknownRun
from loom_ia.agents.registry import UnknownAgent
from loom_ia.core.model.ids import RunId, SessionId
from loom_ia.core.model.question import NoAnswer, PendingQuestion, QuestionReply
from loom_ia.core.model.run_state import ApprovalDecision, Approved, PendingApproval, Rejected
from loom_ia.engine.writer import ClaimConflict

from loom_chat.events import ApprovalAsked, ChatEvent, QuestionAsked, RequestClosed, Seen, to_chat_events

type Emit = Callable[[ChatEvent], Awaitable[None]]
type Answer = ApprovalDecision | QuestionReply | NoAnswer


@dataclass(frozen=True, slots=True)
class SessionListing:
    """Une session du journal, pour le panneau.

    Attributes:
        session_id: La session.
        updated_at: Dernière écriture.
    """

    session_id: str
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class RunLine:
    """Un run d'une session, pour le panneau et « Reprendre ».

    Attributes:
        run_id: Le run.
        agent: Son agent.
        status: Son statut (``RunStatus``).
    """

    run_id: str
    agent: str
    status: str


@dataclass(frozen=True, slots=True)
class SessionDetail:
    """Une session relue dans le journal.

    Attributes:
        session_id: La session.
        runs: Ses runs racines, dans l'ordre.
        events: Son journal, traduit pour l'écran (``to_chat_events``).
        approvals: Les approbations qui attendent, d'après loom.
        questions: Les questions qui attendent, d'après loom.
    """

    session_id: str
    runs: tuple[RunLine, ...]
    events: tuple[ChatEvent, ...]
    approvals: tuple[ApprovalAsked, ...]
    questions: tuple[QuestionAsked, ...]


class ChatEngine:
    """Un utilisateur, ses runs sur les agents de la config, et les demandes en attente de l'interface.

    Args:
        loom: L'instance loom, ouverte par l'appelant.
        user: Utilisateur au nom duquel la console agit.
    """

    def __init__(self, loom: Loom, *, user: str) -> None:
        self._loom = loom
        self._user = user
        self._live: set[str] = set()
        self._waiting: dict[str, asyncio.Future[Answer]] = {}
        self._settled: dict[str, Answer] = {}

    async def close(self, *, wait: bool = True) -> None:
        """Ferme loom ; ``wait=False`` n'attend pas les tâches de fond (le journal fait foi).

        Args:
            wait: Attendre les tâches de fond de loom.
        """
        await self._loom.aclose(wait=wait)

    async def sessions(self) -> list[SessionListing]:
        """Les sessions du journal, les plus récentes d'abord."""
        records = await self._loom.sessions()
        listing = [SessionListing(session_id=str(r.session_id), updated_at=r.updated_at) for r in records]
        return sorted(listing, key=lambda s: s.updated_at, reverse=True)

    async def runs(self, session_id: str) -> tuple[RunLine, ...]:
        """Les runs d'une session, sans traduire son journal.

        Args:
            session_id: La session.
        """
        info = await self._loom.session(SessionId(session_id))
        return tuple(RunLine(run_id=str(r.run_id), agent=r.agent, status=str(r.status)) for r in info.runs)

    async def detail(self, session_id: str) -> SessionDetail:
        """Relit une session : runs, journal traduit et demandes en attente.

        Chaque approbation dit si l'utilisateur peut la trancher (``decidable``),
        et chaque question s'il peut y répondre (``answerable``), d'après loom :
        une session rouverte n'a pas d'approbateur ni de répondeur en ligne pour
        le dire.

        Args:
            session_id: La session.
        """
        sid = SessionId(session_id)
        info = await self._loom.session(sid, user=self._user)
        journal = await self._loom.export_session(sid)
        runs = tuple(RunLine(run_id=str(r.run_id), agent=r.agent, status=str(r.status)) for r in info.runs)
        events = tuple(chat for event in journal for chat in to_chat_events(event))
        approvals = tuple(
            ApprovalAsked(
                session_id=session_id,
                run_id=str(a.run_id),
                call_id=a.call_id,
                tool=a.tool_name,
                arguments=dict(a.arguments),
                reason=a.reason,
                decidable=a.call_id in info.decidable,
            )
            for a in info.pending_approvals
        )
        questions = tuple(
            QuestionAsked(
                session_id=session_id,
                run_id=str(q.run_id),
                call_id=q.call_id,
                questions=tuple(q.questions),
                answerable=q.call_id in info.answerable,
            )
            for q in info.pending_questions
        )
        return SessionDetail(session_id=session_id, runs=runs, events=events, approvals=approvals, questions=questions)

    async def run(self, agent: str, session_id: str | None, text: str, emit: Emit) -> None:
        """Lance un run et envoie ses événements à `emit` ; attend la décision humaine quand loom la demande.

        Chaque approbation dit si l'utilisateur peut la trancher (``decidable``),
        chaque question s'il peut y répondre (``answerable``). Si loom abandonne une attente en ligne parce qu'un autre accès a tranché,
        ``RequestClosed(outcome="elsewhere")`` le dit, avant la suite du run.

        Args:
            agent: Agent qui répond.
            session_id: Session à poursuivre ; ``None`` en ouvre une.
            text: Message de l'utilisateur.
            emit: Reçoit chaque événement de l'écran.
        """
        # Attentes en ligne que loom a abandonnées (décision prise ailleurs) : dites à l'écran
        # au prochain élément du flux, dans l'ordre, et non depuis la tâche du run.
        abandoned: list[str] = []

        async def approver(pending: PendingApproval) -> ApprovalDecision:
            try:
                answer = await self._wait(pending.call_id)
            except asyncio.CancelledError:
                abandoned.append(pending.call_id)
                raise
            if not isinstance(answer, Approved | Rejected):
                raise TypeError(f"Approbation {pending.call_id} : décision attendue, reçu {type(answer).__name__}")
            return answer

        async def answerer(pending: PendingQuestion) -> QuestionReply | NoAnswer:
            try:
                answer = await self._wait(pending.call_id)
            except asyncio.CancelledError:
                abandoned.append(pending.call_id)
                raise
            if not isinstance(answer, QuestionReply | NoAnswer):
                raise TypeError(f"Question {pending.call_id} : réponse attendue, reçu {type(answer).__name__}")
            return answer

        async def withdraw_abandoned() -> None:
            while abandoned:
                call_id = abandoned.pop(0)
                self._live.discard(call_id)
                self._settled.pop(call_id, None)
                await emit(RequestClosed(call_id=call_id, outcome="elsewhere"))

        sid = SessionId(session_id) if session_id else None
        last_seen: Seen | None = None
        asked: set[str] = set()
        try:
            async for item in self._loom.stream(agent, text, session_id=sid, user=self._user, approver=approver, answerer=answerer):
                await withdraw_abandoned()
                for event in to_chat_events(item):
                    if isinstance(event, Seen):
                        if event == last_seen:
                            continue
                        last_seen = event
                    if isinstance(event, ApprovalAsked | QuestionAsked):
                        event = await self._with_right(event)
                    if isinstance(event, ApprovalAsked | QuestionAsked):
                        self._live.add(event.call_id)
                        asked.add(event.call_id)
                    await emit(event)
            await withdraw_abandoned()
        finally:
            # Un run qui se termine ne reçoit plus de réponse : ses demandes ne sont plus en cours ici.
            for call_id in asked:
                self._live.discard(call_id)
                self._settled.pop(call_id, None)

    def settle(self, call_id: str, answer: Answer) -> bool:
        """Donne la réponse à une demande posée pendant un run en cours.

        Args:
            call_id: Appel de la demande.
            answer: Décision, réponse, ou ``NoAnswer`` (plus tard).

        Returns:
            False si ce run n'est pas en cours ici : la réponse passe alors par loom
            (``decide_later``, ``answer_later``).
        """
        if call_id not in self._live:
            return False
        self._live.discard(call_id)
        waiter = self._waiting.get(call_id)
        if waiter is not None and not waiter.done():
            waiter.set_result(answer)
        else:
            self._settled[call_id] = answer
        return True

    async def _wait(self, call_id: str) -> Answer:
        if call_id in self._settled:
            return self._settled.pop(call_id)
        waiter: asyncio.Future[Answer] = asyncio.get_running_loop().create_future()
        self._waiting[call_id] = waiter
        try:
            return await waiter
        finally:
            self._waiting.pop(call_id, None)

    async def last_seq(self, session_id: str) -> int:
        """Dernier numéro écrit au journal : on suit ensuite à partir de là.

        Args:
            session_id: La session.
        """
        return (await self._loom.session(SessionId(session_id))).last_seq

    async def follow(self, run_id: str, session_id: str, emit: Emit, after_seq: int) -> None:
        """Suit un run qui n'est pas en cours dans cette console (décidé ou repris après un redémarrage).

        Args:
            run_id: Run racine à suivre.
            session_id: Sa session.
            emit: Reçoit chaque événement de l'écran.
            after_seq: Suivre à partir de ce numéro du journal.
        """
        async for event in self._loom.follow(RunId(run_id), session_id=SessionId(session_id), after_seq=after_seq):
            for chat in to_chat_events(event):
                await emit(await self._with_right(chat) if isinstance(chat, ApprovalAsked | QuestionAsked) else chat)

    async def _with_right[T: ApprovalAsked | QuestionAsked](self, ask: T) -> T:
        """La demande, avec ce que loom dit du droit de l'utilisateur (``Loom.can``) : trancher une approbation, répondre à une question."""
        run_id, session_id = RunId(ask.run_id), SessionId(ask.session_id)
        try:
            if isinstance(ask, ApprovalAsked):
                return replace(ask, decidable=await self._loom.can(self._user, "approve", run_id=run_id, call_id=ask.call_id, session_id=session_id))
            return replace(ask, answerable=await self._loom.can(self._user, "answer", run_id=run_id, session_id=session_id))
        except UnknownRun, UnknownAgent:
            # Run ou agent introuvable : personne ne peut trancher ici.
            return ask

    async def decide_later(self, run_id: str, call_id: str, decision: ApprovalDecision) -> tuple[str, ...]:
        """Tranche une approbation d'un run qui n'est pas en cours dans cette console.

        Args:
            run_id: Run qui a posé la demande.
            call_id: Appel suspendu.
            decision: Approbation ou refus.

        Returns:
            Les appels tranchés ; vide si rien n'attendait plus (tranché ailleurs, run fini).
        """
        if isinstance(decision, Rejected):
            return await self._loom.reject(RunId(run_id), call_id=call_id, reason=decision.reason, user=self._user)
        return await self._loom.approve(RunId(run_id), call_id=call_id, user=self._user)

    async def answer_later(self, run_id: str, call_id: str, reply: QuestionReply) -> None:
        """Répond à une question d'un run en pause ; loom remet la reprise en file.

        Args:
            run_id: Run qui a posé la question.
            call_id: Appel de ``loom.ask``.
            reply: La réponse.
        """
        await self._loom.answer(RunId(run_id), call_id=call_id, answers=reply.answers, text=reply.text, user=self._user)

    async def held_elsewhere(self, run_id: str, session_id: str) -> float | None:
        """Secondes de concession qui restent à un autre process sur ce run, ``None`` s'il n'en tient pas.

        Après un arrêt brutal, la concession du process précédent court encore :
        loom reprend le run à sa fin. La console le dit, plutôt que de laisser
        croire que rien ne se passe.

        Args:
            run_id: Le run.
            session_id: Sa session.
        """
        claim = (await self._loom.state(RunId(run_id), session_id=SessionId(session_id))).claim
        if claim is None or claim.worker_id == self._loom.worker_id:
            return None
        left = (claim.lease_until - datetime.now(claim.lease_until.tzinfo)).total_seconds()
        return left if left > 0 else None

    async def resume(self, run_id: str) -> RunResult:
        """Reprend un run interrompu.

        Args:
            run_id: Le run.
        """
        return await self._loom.resume(RunId(run_id), user=self._user)

    async def resume_when_free(self, run_id: str, on_wait: Callable[[float], Awaitable[None]] | None = None, attempts: int = 5) -> RunResult:
        """Reprend un run que l'ancien processus tenait : attend la fin de sa concession, puis reprend.

        Sert à « Reprendre » (Ctrl+R), qui appelle ``resume()`` directement : loom
        y rend le conflit à l'appelant. Après une décision ou une réponse, il n'y
        a rien à faire : loom met lui-même la reprise en file, et la repose pour
        la fin de la concession du process précédent (``docs/analyse-memory.md``).

        Args:
            run_id: Le run.
            on_wait: Prévenu du délai avant chaque nouvel essai.
            attempts: Nombre d'essais.

        Raises:
            RuntimeError: Le run reste tenu par un autre processus après tous les essais.
        """
        for _ in range(attempts):
            try:
                return await self.resume(run_id)
            except ClaimConflict as conflict:
                delay = max((conflict.lease_until - datetime.now(conflict.lease_until.tzinfo)).total_seconds(), 0.0) + 0.5
                if on_wait is not None:
                    await on_wait(delay)
                await asyncio.sleep(delay)
        raise RuntimeError(f"Le run {run_id} reste tenu par un autre processus.")

    async def cancel(self, run_id: str) -> None:
        """Annule un run.

        Args:
            run_id: Le run racine.
        """
        await self._loom.cancel(RunId(run_id), user=self._user)
