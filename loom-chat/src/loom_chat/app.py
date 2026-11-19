"""Application Textual : une page, barre du haut, conversation, panneau des sessions, saisie en bas."""

import asyncio
from collections.abc import Coroutine, Iterable
from dataclasses import dataclass, field, replace
from typing import Any, ClassVar

from loom_ia.access.api import ApprovalStale, Loom, QuestionStale, UnknownQuestion
from loom_ia.core.model.question import NoAnswer, QuestionReply
from loom_ia.core.model.run_state import ApprovalDecision, RunStatus
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer, Input, Static, Tree

from loom_chat.engine import ChatEngine, RunLine, SessionDetail, SessionListing
from loom_chat.events import (
    ApprovalAsked,
    ChatEvent,
    Line,
    ModelReply,
    QuestionAsked,
    RequestClosed,
    RunEnded,
    Seen,
    TextChunk,
    ToolDone,
    ToolLine,
    ToolStarted,
    UserLine,
)
from loom_chat.modals import AgentModal, ApprovalModal, QuestionModal, RequestModal
from loom_chat.settings import ChatSettings, is_billed, model_label
from loom_chat.widgets import Conversation, SessionsPanel, StatusBar

TERMINAL = frozenset({"completed", "failed", "cancelled"})
# Un run en pause attend une décision humaine : elle se donne dans les fenêtres, jamais par une reprise.
NOT_RESUMABLE = TERMINAL | {RunStatus.PAUSED.value}
# Ce que loom lève quand une demande n'attend plus : tranchée ailleurs, ou expirée.
CLOSED_ELSEWHERE = (ApprovalStale, QuestionStale, UnknownQuestion)


def resumable_runs(runs: Iterable[RunLine], current: str | None) -> list[RunLine]:
    """Les runs que « Reprendre » peut relancer : ni terminés, ni en pause, ni le run en cours.

    Args:
        runs: Les runs de la session.
        current: Le run en cours dans la console, s'il y en a un.
    """
    return [run for run in runs if run.status not in NOT_RESUMABLE and run.run_id != current]


@dataclass
class View:
    """Une session affichée (ou une nouvelle, pas encore écrite au journal).

    Le run en direct, le suivi d'un run repris et la relecture du journal passent
    tous par ``apply`` : la conversation est la même dans les trois cas.
    """

    key: str
    agent: str = ""
    session_id: str | None = None
    lines: list[Line] = field(default_factory=list[Line])
    tools: dict[str, int] = field(default_factory=dict[str, int])
    busy: bool = False
    run_id: str | None = None
    streaming: bool = False
    last_reply: str = ""
    cost: float = 0.0
    runs: tuple[RunLine, ...] = ()
    approvals: dict[str, ApprovalAsked] = field(default_factory=dict[str, ApprovalAsked])
    questions: dict[str, QuestionAsked] = field(default_factory=dict[str, QuestionAsked])
    shown: set[str] = field(default_factory=set[str])
    follower: asyncio.Task[None] | None = None

    def state(self) -> str:
        """L'état affiché : demande en attente, run en cours, ou inactif."""
        if self.approvals:
            return "en attente d'approbation"
        if self.questions:
            return "en attente de réponse"
        return "en cours" if self.busy else "inactif"

    def is_blank(self) -> bool:
        """Nouvelle session où rien n'a encore été dit."""
        return self.session_id is None and not self.lines and not self.busy

    def _tool(self, call_id: str, name: str, arguments: dict[str, Any], **changes: Any) -> None:
        """Crée ou met à jour le bloc d'un appel d'outil, à sa place dans la conversation."""
        self.streaming = False
        index = self.tools.get(call_id)
        if index is None:
            self.tools[call_id] = len(self.lines)
            self.lines.append(ToolLine(call_id=call_id, name=name, arguments=arguments, **changes))
            return
        line = self.lines[index]
        assert isinstance(line, ToolLine)
        self.lines[index] = replace(line, arguments=arguments or line.arguments, **changes)

    def _streamed(self) -> str | None:
        """Le texte de la réponse en cours de réception, s'il est en dernière ligne."""
        last = self.lines[-1] if self.lines else None
        if self.streaming and isinstance(last, tuple) and last[0] == "loom":
            return last[1]
        return None

    def _tool_line(self, call_id: str) -> ToolLine | None:
        index = self.tools.get(call_id)
        line = self.lines[index] if index is not None else None
        return line if isinstance(line, ToolLine) else None

    def apply(self, event: ChatEvent) -> None:
        """Reporte un événement de l'écran sur la session : lignes, demandes, coût.

        Args:
            event: L'événement.
        """
        match event:
            case Seen(run_id=run_id):
                self.run_id = run_id
            case UserLine(text=text):
                self.streaming = False
                self.lines.append(("Vous", text))
            case TextChunk(text=text):
                if (current := self._streamed()) is not None:
                    self.lines[-1] = ("loom", current + text)
                else:
                    self.lines.append(("loom", text))
                    self.streaming = True
            case ModelReply(text=text):
                if text:
                    # Le texte complet du journal remplace les morceaux reçus au fil de l'eau.
                    if self._streamed() is not None:
                        self.lines[-1] = ("loom", text)
                    else:
                        self.lines.append(("loom", text))
                    self.last_reply = text
                self.streaming = False
            case ToolStarted(name=name, call_id=call_id, arguments=arguments):
                known = self._tool_line(call_id)
                self._tool(call_id, name, arguments, status="running", waiting_for="", summary=known.summary if known else "")
            case ToolDone(name=name, call_id=call_id, summary=summary, detail=text, is_error=is_error, latency_ms=latency):
                known = self._tool_line(call_id)
                if known is not None and known.status == "rejected":
                    self._tool(call_id, name, {}, detail=text, latency_ms=latency)  # le motif du refus reste le résumé
                elif known is not None and name == "ask" and known.summary:
                    # La réponse donnée dit mieux le résultat que « Réponse de l'utilisateur : … ».
                    self._tool(call_id, name, {}, status="done", detail=text, latency_ms=latency)
                else:
                    self._tool(call_id, name, {}, status="error" if is_error else "done", summary=summary, detail=text, latency_ms=latency)
            case ApprovalAsked(call_id=call_id, tool=tool, arguments=arguments):
                self.approvals[call_id] = event
                self._tool(call_id, tool, arguments, status="waiting", waiting_for="approbation")
            case QuestionAsked(call_id=call_id, questions=questions):
                self.questions[call_id] = event
                asked: dict[str, Any] = {"question": questions[0].question} if len(questions) == 1 else {"questions": [q.question for q in questions]}
                self._tool(call_id, "ask", asked, status="waiting", waiting_for="question")
            case RequestClosed(call_id=call_id, outcome=outcome, text=text):
                self.approvals.pop(call_id, None)
                self.questions.pop(call_id, None)
                if (known := self._tool_line(call_id)) is not None:
                    match outcome:
                        case "rejected":
                            self._tool(call_id, known.name, {}, status="rejected", waiting_for="", summary=text)
                        case "expired":
                            self._tool(call_id, known.name, {}, status="expired", waiting_for="")
                        case "answered":
                            self._tool(call_id, known.name, {}, status="running", waiting_for="", summary=text)
                        case "granted":
                            self._tool(call_id, known.name, {}, status="running", waiting_for="")
            case RunEnded(status=status, text=text, cost_usd=cost, error=error):
                self.streaming = False
                self.run_id = None
                self.cost += cost
                if text and text != self.last_reply:
                    self.lines.append(("loom", text))  # réponse finale qui n'est pas passée par le modèle (repli)
                self.last_reply = ""
                if status != "completed":
                    self.lines.append(("système", f"Run {status}" + (f" : {error}" if error else "")))

    def reload(self, detail: SessionDetail) -> None:
        """Reconstruit la session depuis le journal ; les demandes en attente sont celles que loom donne.

        Args:
            detail: La session relue.
        """
        self.lines = []
        self.tools = {}
        self.cost = 0.0
        self.streaming = False
        self.last_reply = ""
        for event in detail.events:
            self.apply(event)
        self.run_id = None
        self.runs = detail.runs
        if detail.runs:
            self.agent = detail.runs[-1].agent
        self.approvals = {a.call_id: a for a in detail.approvals}
        self.questions = {q.call_id: q for q in detail.questions}


class ChatApp(App[None]):
    """La console : une session affichée à la fois, plusieurs runs possibles en parallèle.

    Args:
        settings: Les réglages de la console.
    """

    TITLE = "loom-chat"
    CSS = """
    #body { height: 1fr; }
    #left { width: 1fr; }
    #conversation { height: 1fr; padding: 0 1; }
    #composer { margin: 0 1 1 1; }
    #right { width: 40; border-left: vkey $panel-lighten-2; }
    #sessions-title { height: 1; padding: 0 1; text-style: bold; color: $text-muted; }
    .line { margin-bottom: 1; }
    .vous { color: $success; }
    .loom { color: $text; }
    .outil { color: $text-muted; }
    .erreur { color: $error; }
    .systeme { color: $warning; }
    """
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("ctrl+n", "new_session", "Nouvelle session"),
        Binding("ctrl+a", "choose_agent", "Agent"),
        # priority : la saisie a le focus, et son Ctrl+X (couper) passerait avant.
        Binding("ctrl+x", "cancel_run", "Annuler le run", priority=True),
        Binding("ctrl+r", "resume_run", "Reprendre"),
        Binding("ctrl+q", "quit", "Quitter"),
    ]

    def __init__(self, settings: ChatSettings) -> None:
        super().__init__()
        self._settings = settings
        self._agents = [agent.name for agent in settings.config.agents]
        self._agent = self._agents[0] if self._agents else ""
        self._loom: Loom | None = None
        self._engine: ChatEngine | None = None
        self._views: dict[str, View] = {}
        self._active: View | None = None
        self._listing: list[SessionListing] = []
        self._counter = 0
        self._tasks: set[asyncio.Task[None]] = set()
        self._modals: dict[str, RequestModal[Any]] = {}
        self._panel_snapshot: object = None
        # Runs lus en dépliant une session du panneau, pour celles qui ne sont pas ouvertes.
        self._runs: dict[str, tuple[RunLine, ...]] = {}

    def compose(self) -> ComposeResult:
        """La page : barre du haut, conversation et saisie à gauche, sessions à droite."""
        yield StatusBar()
        with Horizontal(id="body"):
            with Vertical(id="left"):
                yield Conversation(id="conversation")
                yield Input(placeholder="Tapez votre message…", id="composer")
            with Vertical(id="right"):
                yield Static("Sessions", id="sessions-title")
                yield SessionsPanel()
        yield Footer()

    async def on_mount(self) -> None:
        """Ouvre loom, liste les sessions et ouvre une nouvelle session."""
        self._loom = Loom(self._settings.config)
        await self._loom.__aenter__()
        self._engine = ChatEngine(self._loom, user=self._settings.user)
        await self.refresh_sessions()
        await self.new_session()
        self.query_one(Input).focus()
        if len(self._agents) > 1:
            self.action_choose_agent()
        else:
            self._warn_billed(self._agent)

    def _warn_billed(self, agent: str) -> None:
        if is_billed(self._settings.config, agent):
            label = model_label(self._settings.config, agent)
            self.notify(f"{agent} répond avec {label} : chaque message est facturé.", severity="warning", timeout=8)

    async def on_unmount(self) -> None:
        """Ferme loom sans attendre ses tâches : le journal fait foi, tout se reprend au redémarrage."""
        # Ne pas annuler les tâches : abandonner le flux d'un run l'annulerait.
        # Une question ouverte passe en pause proprement ; une approbation reste
        # en attente dans le journal, et sera rouverte au redémarrage.
        if self._engine is None:
            if self._loom is not None:
                await self._loom.aclose(wait=False)
            return
        for view in self._views.values():
            for call_id in list(view.questions):
                self._engine.settle(call_id, NoAnswer(reason="application fermée"))
        await self._engine.close(wait=False)

    # --- sessions -------------------------------------------------------------

    @property
    def engine(self) -> ChatEngine:
        """Le moteur, une fois loom ouvert."""
        assert self._engine is not None
        return self._engine

    @property
    def settings(self) -> ChatSettings:
        """Les réglages de la console : la config loom et l'utilisateur (lecture seule)."""
        return self._settings

    @property
    def active_view(self) -> View | None:
        """La session affichée (lecture seule, pour les tests et les outils)."""
        return self._active

    @property
    def listing(self) -> list[SessionListing]:
        """Les sessions du journal, les plus récentes d'abord (lecture seule)."""
        return self._listing

    async def new_session(self, agent: str | None = None) -> None:
        """Affiche une nouvelle session ; une nouvelle session encore vide est réutilisée.

        Args:
            agent: Agent de la session ; l'agent choisi en dernier par défaut.
        """
        blank = next((view for view in self._views.values() if view.is_blank()), None)
        if blank is None:
            self._counter += 1
            blank = View(key=f"nouvelle-{self._counter}")
            self._views[blank.key] = blank
        blank.agent = agent or self._agent
        self._panel_snapshot = None
        await self._activate(blank)

    async def _activate(self, view: View) -> None:
        self._active = view
        await self.query_one(Conversation).show(view.lines, pin=True)
        self._paint_chrome()
        self._paint_panel()

    async def refresh_sessions(self) -> None:
        """Relit la liste des sessions du journal."""
        self._listing = await self.engine.sessions()
        self._paint_panel()

    async def open_session(self, session_id: str) -> None:
        """Affiche une session du journal, relue sauf si un run y tourne, et rouvre ses demandes.

        Args:
            session_id: La session.
        """
        view = self._views.get(session_id)
        if view is None:
            view = View(key=session_id, session_id=session_id)
            view.reload(await self.engine.detail(session_id))
            self._views[session_id] = view
        elif not view.busy:
            # Le journal fait foi : une demande tranchée ailleurs (CLI, REST) disparaît ici aussi.
            view.reload(await self.engine.detail(session_id))
        else:
            view.runs = (await self.engine.detail(session_id)).runs
        await self._activate(view)
        self._show_pending(view)

    def _show_pending(self, view: View) -> None:
        for approval in list(view.approvals.values()):
            self._show_approval(view, approval)
        for question in list(view.questions.values()):
            self._show_question(view, question)

    def _rekey(self, view: View, session_id: str) -> None:
        self._views.pop(view.key, None)
        view.key = session_id
        view.session_id = session_id
        self._views[session_id] = view

    # --- affichage --------------------------------------------------------------

    def _paint_chrome(self) -> None:
        view = self._active
        if view is None or not self.is_running:
            return
        model = model_label(self._settings.config, view.agent)
        self.query_one(StatusBar).show_view(agent=view.agent, model=model, state=view.state(), session=view.session_id, cost=view.cost)
        field_ = self.query_one(Input)
        field_.placeholder = "Réponse à la question en attente…" if self._free_question(view) else "Tapez votre message…"

    def _paint_panel(self) -> None:
        if not self.is_running:
            return
        panel = self.query_one(SessionsPanel)
        states = {key: view.state() for key, view in self._views.items() if view.session_id}
        runs = dict(self._runs) | {key: view.runs for key, view in self._views.items() if view.session_id and view.runs}
        unsaved = [(view.key, "nouvelle session") for view in self._views.values() if view.session_id is None]
        active = self._active.key if self._active is not None else None
        snapshot = (tuple(self._listing), tuple(unsaved), tuple(runs.items()), tuple(states.items()), active, frozenset(panel.expanded))
        if snapshot == self._panel_snapshot:
            return  # rien ne change : inutile de reconstruire l'arbre
        self._panel_snapshot = snapshot
        panel.show(self._listing, unsaved, runs, states, active)

    async def _paint(self, view: View) -> None:
        if not self.is_running:
            return
        if view is self._active:
            await self.query_one(Conversation).show(view.lines)
            self._paint_chrome()
        self._paint_panel()

    def _report(self, view: View | None, message: str) -> None:
        """Montre une erreur : dans la conversation de la session, et en notification."""
        if not self.is_running:
            return
        self.notify(message, severity="error")
        if view is not None:
            view.lines.append(("erreur", message))
            self._spawn(self._paint(view))

    # --- événements d'un run ----------------------------------------------------

    async def _on_event(self, view: View, event: ChatEvent) -> None:
        if isinstance(event, Seen) and view.session_id is None:
            self._rekey(view, event.session_id)
            await self.refresh_sessions()
        view.apply(event)
        match event:
            case ApprovalAsked():
                self._show_approval(view, event)
            case QuestionAsked():
                self._show_question(view, event)
            case RequestClosed(call_id=call_id):
                self._withdraw(call_id)
            case _:
                pass
        await self._paint(view)

    async def _drive(self, view: View, text: str) -> None:
        view.busy = True
        view.streaming = False
        await self._paint(view)
        try:
            await self.engine.run(view.agent, view.session_id, text, lambda event: self._on_event(view, event))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            view.lines.append(("erreur", str(exc)))
        finally:
            view.busy = False
            view.streaming = False
            await self._refresh_runs(view)
            await self._paint(view)
            await self.refresh_sessions()

    async def _refresh_runs(self, view: View) -> None:
        """Relit les runs de la session après un run : le panneau montre leur statut à jour."""
        if view.session_id is None:
            return
        self._runs.pop(view.session_id, None)
        try:
            view.runs = await self.engine.runs(view.session_id)
        except Exception as exc:
            view.lines.append(("erreur", f"Runs illisibles : {exc}"))

    def _spawn(self, coroutine: Coroutine[Any, Any, None], *, view: View | None = None, what: str = "") -> asyncio.Task[None]:
        """Lance une tâche de fond ; son échec est montré, jamais perdu en silence.

        Args:
            coroutine: Le travail.
            view: Session où montrer l'échec.
            what: Ce que faisait la tâche, en tête du message.
        """
        task = asyncio.create_task(coroutine)
        self._tasks.add(task)

        def finished(done: asyncio.Task[None]) -> None:
            self._tasks.discard(done)
            if done.cancelled() or (error := done.exception()) is None:
                return
            self._report(view, f"{what} : {error}" if what else str(error))

        task.add_done_callback(finished)
        return task

    def _continue(self, view: View, run_id: str, after_seq: int) -> None:
        """Reprend hors ligne un run décidé ou interrompu, et le suit : attend la concession de l'ancien processus si besoin."""
        assert view.session_id is not None
        self._follow(view, run_id, after_seq)

        async def resume() -> None:
            async def waited(seconds: float) -> None:
                view.lines.append(("système", f"Reprise en attente : le run est encore tenu par le processus précédent (environ {int(seconds)} s)."))
                await self._paint(view)

            try:
                await self.engine.resume_when_free(run_id, on_wait=waited)
            except Exception as exc:
                view.lines.append(("erreur", f"Reprise impossible : {exc}"))
                # Le run n'avancera plus : ne pas laisser la session « en cours » pour rien.
                if view.follower is not None:
                    view.follower.cancel()
                await self._paint(view)

        self._spawn(resume(), view=view, what="Reprise")

    def _follow(self, view: View, run_id: str, after_seq: int) -> None:
        """Suit le run repris hors ligne : ses nouveaux événements s'affichent, et ses demandes s'ouvrent."""
        if view.follower is not None and not view.follower.done():
            return
        session_id = view.session_id
        assert session_id is not None

        async def follow() -> None:
            view.busy = True
            await self._paint(view)
            try:
                await self.engine.follow(run_id, session_id, lambda event: self._on_event(view, event), after_seq)
            except Exception as exc:
                view.lines.append(("erreur", f"Suivi interrompu : {exc}"))
            finally:
                view.busy = False
                view.streaming = False
                await self._refresh_runs(view)
                await self._paint(view)
                await self.refresh_sessions()

        view.follower = self._spawn(follow(), view=view, what="Suivi")

    # --- demandes à l'utilisateur -------------------------------------------------

    def _withdraw(self, call_id: str) -> None:
        """La demande est close (ici ou ailleurs) : retirer sa fenêtre si elle est ouverte."""
        modal = self._modals.pop(call_id, None)
        if modal is not None:
            modal.withdraw()

    def _show_approval(self, view: View, ask: ApprovalAsked) -> None:
        if ask.call_id in view.shown:
            return
        view.shown.add(ask.call_id)
        modal = ApprovalModal(ask, self._settings.user)

        def done(decision: ApprovalDecision | None) -> None:
            self._modals.pop(ask.call_id, None)
            if decision is None or modal.withdrawn:
                view.shown.discard(ask.call_id)
                self._paint_chrome()
                return
            # `shown` reste posé jusqu'à la fin du traitement : la demande ne se rouvre pas entre-temps.
            self._spawn(self._settle_approval(view, ask, decision), view=view, what="Approbation")

        self._modals[ask.call_id] = modal
        self.push_screen(modal, done)
        self._paint_chrome()

    async def _settle_approval(self, view: View, ask: ApprovalAsked, decision: ApprovalDecision) -> None:
        try:
            if self.engine.settle(ask.call_id, decision):
                view.approvals.pop(ask.call_id, None)
            else:
                before = await self.engine.last_seq(ask.session_id)
                try:
                    decided = await self.engine.decide_later(ask.run_id, ask.call_id, decision)
                except CLOSED_ELSEWHERE:
                    decided = ()
                if not decided:
                    await self._closed_elsewhere(view, ask.call_id, "Cette approbation n'attend plus : tranchée ailleurs, ou expirée.")
                    return
                view.approvals.pop(ask.call_id, None)
                self._continue(view, ask.run_id, before)
        finally:
            view.shown.discard(ask.call_id)
        await self._paint(view)

    def _show_question(self, view: View, ask: QuestionAsked, prefill: str = "") -> None:
        if ask.call_id in view.shown:
            return
        view.shown.add(ask.call_id)
        modal = QuestionModal(ask, self._settings.user, prefill=prefill)

        def done(reply: QuestionReply | None) -> None:
            self._modals.pop(ask.call_id, None)
            if modal.withdrawn:
                view.shown.discard(ask.call_id)
                self._paint_chrome()
                return
            if reply is None:
                view.shown.discard(ask.call_id)
                # Plus tard : le run en cours se met en pause, la question reste ouverte dans le journal.
                self.engine.settle(ask.call_id, NoAnswer(reason="réponse plus tard"))
                if prefill:
                    self.query_one("#composer", Input).value = prefill  # le texte saisi n'est pas perdu
                self._paint_chrome()
                return
            self._spawn(self._settle_question(view, ask, reply), view=view, what="Réponse")

        self._modals[ask.call_id] = modal
        self.push_screen(modal, done)
        self._paint_chrome()

    async def _settle_question(self, view: View, ask: QuestionAsked, reply: QuestionReply) -> None:
        # La ligne « Vous » vient du journal (question.answered), en direct comme en suivi.
        try:
            if self.engine.settle(ask.call_id, reply):
                view.questions.pop(ask.call_id, None)
            else:
                before = await self.engine.last_seq(ask.session_id)
                try:
                    await self.engine.answer_later(ask.run_id, ask.call_id, reply)
                except CLOSED_ELSEWHERE:
                    await self._closed_elsewhere(view, ask.call_id, "Cette question n'attend plus : répondue ailleurs, ou expirée.")
                    return
                view.questions.pop(ask.call_id, None)
                self._continue(view, ask.run_id, before)
        finally:
            view.shown.discard(ask.call_id)
        await self._paint(view)

    async def _closed_elsewhere(self, view: View, call_id: str, message: str) -> None:
        """La demande n'attend plus : relire la session (le journal fait foi) et le dire."""
        view.approvals.pop(call_id, None)
        view.questions.pop(call_id, None)
        if view.session_id is not None and not view.busy:
            view.reload(await self.engine.detail(view.session_id))
        view.lines.append(("système", message))
        await self._paint(view)

    def _free_question(self, view: View) -> list[QuestionAsked]:
        return [q for cid, q in view.questions.items() if cid not in view.shown]

    # --- actions ---------------------------------------------------------------

    @on(Input.Submitted, "#composer")
    async def _submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        view = self._active
        if view is None:
            return
        pending = self._free_question(view)
        if pending:
            # Une question attend : le texte ne lui répond jamais en silence. Il pré-remplit la fenêtre
            # de la question, à confirmer ; Entrée sur une saisie vide la rouvre simplement.
            event.input.value = ""
            self._show_question(view, pending[0], prefill=text)
            return
        if view.approvals:
            # Une approbation attend : la rouvrir si sa fenêtre a été fermée sans décision.
            for approval in view.approvals.values():
                self._show_approval(view, approval)
            self.notify("Une approbation attend votre décision.", severity="warning")
            return
        if not text:
            return
        if view.busy:
            self.notify("Un run tourne déjà dans cette session.", severity="warning")
            return
        event.input.value = ""
        view.busy = True  # posé ici, pas dans la tâche : une seconde validation avant son démarrage ne lance pas un second run
        self._spawn(self._drive(view, text), view=view, what="Run")

    @on(Tree.NodeExpanded)
    async def _expanded(self, event: Tree.NodeExpanded[str]) -> None:
        key = event.node.data
        if key is None or key not in {item.session_id for item in self._listing}:
            return
        self.query_one(SessionsPanel).expanded.add(key)
        view = self._views.get(key)
        if key not in self._runs and (view is None or not view.runs):
            try:
                self._runs[key] = await self.engine.runs(key)
            except Exception as exc:
                self._report(self._active, f"Session {key} illisible : {exc}")
                return
        self._paint_panel()

    @on(Tree.NodeCollapsed)
    def _collapsed(self, event: Tree.NodeCollapsed[str]) -> None:
        if event.node.data is not None:
            self.query_one(SessionsPanel).expanded.discard(event.node.data)
            self._paint_panel()

    @on(Tree.NodeSelected)
    async def _selected(self, event: Tree.NodeSelected[str]) -> None:
        key = event.node.data
        if key is None:
            return
        if key in self._views and self._views[key].session_id is None:
            await self._activate(self._views[key])
            return
        try:
            await self.open_session(key)
        except Exception as exc:
            self._report(self._active, f"Session {key} illisible : {exc}")

    async def action_new_session(self) -> None:
        """Ctrl+N : nouvelle session."""
        await self.new_session()

    def action_choose_agent(self) -> None:
        """Ctrl+A : choisit l'agent ; une nouvelle session s'ouvre avec lui."""
        if len(self._agents) < 2:
            self.notify(f"Un seul agent dans la config : {self._agent}.")
            return

        def chosen(agent: str | None) -> None:
            if agent is None:
                return
            self._agent = agent
            self._spawn(self.new_session(agent))
            self._warn_billed(agent)

        self.push_screen(AgentModal(self._settings.config, self._agent), chosen)

    def action_cancel_run(self) -> None:
        """Ctrl+X : annule le run en cours de la session affichée."""
        view = self._active
        if view is None or view.run_id is None:
            self.notify("Aucun run à annuler dans cette session.", severity="warning")
            return
        self._spawn(self.engine.cancel(view.run_id), view=view, what="Annulation")

    def action_resume_run(self) -> None:
        """Ctrl+R : reprend le dernier run interrompu de la session affichée."""
        view = self._active
        if view is None or view.session_id is None:
            return
        self._spawn(self._resume_latest(view), view=view, what="Reprise")

    async def _resume_latest(self, view: View) -> None:
        assert view.session_id is not None
        detail = await self.engine.detail(view.session_id)
        view.runs = detail.runs
        candidates = resumable_runs(detail.runs, view.run_id)
        if not candidates:
            self.notify("Aucun run à reprendre.")
            return
        before = await self.engine.last_seq(view.session_id)
        self._continue(view, candidates[-1].run_id, before)
