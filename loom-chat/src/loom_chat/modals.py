"""Fenêtres par-dessus la page : approbation d'un outil sensible, et question du modèle."""

import json
from typing import ClassVar, cast

from loom_ia.config.models import LoomConfig
from loom_ia.core.model.question import QuestionReply
from loom_ia.core.model.run_state import ApprovalDecision, Approved, Rejected
from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.content import Content
from textual.screen import ModalScreen
from textual.widgets import Button, Input, OptionList, RadioButton, RadioSet, SelectionList, Static
from textual.widgets.option_list import Option
from textual.widgets.selection_list import Selection

from loom_chat.events import ApprovalAsked, QuestionAsked
from loom_chat.settings import model_label
from loom_chat.widgets import short


class RequestModal[T](ModalScreen[T | None]):
    """Fenêtre d'une demande, que la page peut retirer si la demande est close ailleurs.

    Retirée, elle se ferme avec ``None`` et ``withdrawn`` vrai : tout de suite si elle
    est au premier plan, sinon dès qu'elle y revient (``dismiss`` ferme toujours
    l'écran du dessus, jamais un autre).
    """

    def __init__(self) -> None:
        super().__init__()
        self.withdrawn = False
        self._leaving = False

    def withdraw(self) -> None:
        """La demande n'attend plus : fermer la fenêtre sans décision."""
        self.withdrawn = True
        if self.is_active:  # au premier plan ; `is_current` vaut aussi pour une fenêtre visible dessous
            self._close()

    def on_screen_resume(self) -> None:
        """Revenue au premier plan après avoir été retirée : se fermer."""
        if self.withdrawn:
            self._close()

    def _close(self) -> None:
        # Une seule fermeture : le retour au premier plan peut se signaler plus d'une fois.
        if not self._leaving:
            self._leaving = True
            self.dismiss(None)


class ApprovalModal(RequestModal[ApprovalDecision]):
    """Un outil sensible attend une décision. Pas de fermeture sans choix : loom exige une décision.

    Args:
        ask: La demande.
        user: Qui décide.
    """

    DEFAULT_CSS = """
    ApprovalModal { align: center middle; }
    ApprovalModal #dialog { width: 72; height: auto; border: thick $warning; padding: 1 2; background: $surface; }
    ApprovalModal #buttons { height: auto; margin-top: 1; }
    """

    def __init__(self, ask: ApprovalAsked, user: str) -> None:
        super().__init__()
        self._ask = ask
        self._user = user

    def compose(self) -> ComposeResult:
        """L'outil, ses arguments, la raison, et les deux boutons."""
        with Vertical(id="dialog"):
            yield Static(f"Approbation requise · session {short(self._ask.session_id)}", markup=False)
            yield Static(f"Outil : {self._ask.tool}", markup=False)
            yield Static(f"Arguments : {json.dumps(self._ask.arguments, ensure_ascii=False)}", markup=False)
            if self._ask.reason:
                yield Static(f"Raison : {self._ask.reason}", markup=False)
            with Horizontal(id="buttons"):
                yield Button("Approuver", id="approve", variant="success")
                yield Button("Refuser", id="reject", variant="error")

    @on(Button.Pressed, "#approve")
    def _approve(self) -> None:
        self.dismiss(Approved(by=self._user))

    @on(Button.Pressed, "#reject")
    def _reject(self) -> None:
        self.dismiss(Rejected(reason="refusé dans la console", by=self._user))


class QuestionModal(RequestModal[QuestionReply]):
    """Questions du modèle. Échap : répondre plus tard (la question reste ouverte).

    Les libellés des choix sont affichés tels quels (sans markup) et relus par leur
    rang : la réponse envoyée est toujours le libellé exact de l'option.

    Args:
        ask: Les questions.
        user: Qui répond.
        prefill: Texte déjà saisi dans la page, placé dans la réponse libre.
    """

    DEFAULT_CSS = """
    QuestionModal { align: center middle; }
    QuestionModal #dialog { width: 72; height: auto; max-height: 90%; border: thick $accent; padding: 1 2; background: $surface; }
    QuestionModal .question { margin-top: 1; text-style: bold; }
    QuestionModal SelectionList { height: auto; max-height: 8; }
    """
    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "later", "Plus tard")]

    def __init__(self, ask: QuestionAsked, user: str, *, prefill: str = "") -> None:
        super().__init__()
        self._ask = ask
        self._user = user
        self._prefill = prefill

    def compose(self) -> ComposeResult:
        """Chaque question avec ses choix, puis la réponse libre et le bouton d'envoi."""
        with VerticalScroll(id="dialog"):
            yield Static(f"Question de loom · session {short(self._ask.session_id)}", markup=False)
            for index, question in enumerate(self._ask.questions):
                yield Static(question.question, classes="question", markup=False)
                if not question.options:
                    continue
                if question.multiple:
                    yield SelectionList[int](
                        *(Selection(Content(option.label), rank) for rank, option in enumerate(question.options)),
                        id=f"q{index}",
                    )
                else:
                    yield RadioSet(*(RadioButton(Content(option.label)) for option in question.options), id=f"q{index}")
            yield Input(value=self._prefill, placeholder="Ou une réponse libre", id="free")
            with Horizontal():
                yield Button("Envoyer", id="send", variant="primary")
            yield Static("Entrée dans la réponse libre : envoyer · Échap : répondre plus tard", markup=False)

    def on_mount(self) -> None:
        """Texte repris de la page : le placer dans le champ libre, prêt à être envoyé."""
        if self._prefill:
            self.query_one("#free", Input).focus()

    def action_later(self) -> None:
        """Répondre plus tard : la fenêtre se ferme sans réponse."""
        self.dismiss(None)

    @on(Input.Submitted, "#free")
    def _submit_free(self) -> None:
        self._send()

    @on(Button.Pressed, "#send")
    def _send(self) -> None:
        free = self.query_one("#free", Input).value.strip()
        if free:
            self.dismiss(QuestionReply(text=free, by=self._user))
            return
        answers: list[tuple[str, ...]] = []
        for index, question in enumerate(self._ask.questions):
            if not question.options:
                self.notify("Écrivez une réponse libre.", severity="warning")
                return
            labels = [option.label for option in question.options]
            if question.multiple:
                # Les valeurs sont les rangs des options (compose).
                selection = cast(SelectionList[int], self.query_one(f"#q{index}", SelectionList))
                chosen = tuple(labels[rank] for rank in selection.selected)
            else:
                rank = self.query_one(f"#q{index}", RadioSet).pressed_index
                chosen = (labels[rank],) if rank >= 0 else ()
            if not chosen:
                self.notify("Choisissez une réponse.", severity="warning")
                return
            answers.append(chosen)
        self.dismiss(QuestionReply(answers=tuple(answers), by=self._user))


class AgentModal(ModalScreen[str | None]):
    """Choix de l'agent parmi ceux de la config : Entrée choisit, Échap annule.

    Args:
        config: La config loom.
        current: L'agent choisi jusqu'ici, sélectionné à l'ouverture.
    """

    DEFAULT_CSS = """
    AgentModal { align: center middle; }
    AgentModal #dialog { width: 72; height: auto; max-height: 80%; border: thick $accent; padding: 1 2; background: $surface; }
    AgentModal OptionList { height: auto; max-height: 16; margin-top: 1; }
    """
    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "cancel", "Annuler")]

    def __init__(self, config: LoomConfig, current: str) -> None:
        super().__init__()
        self._config = config
        self._current = current

    def compose(self) -> ComposeResult:
        """Un agent par ligne : son nom, son modèle et sa description."""
        options: list[Option] = []
        for agent in self._config.agents:
            label = Text.assemble((agent.name, "bold"), ("  ", ""), (model_label(self._config, agent.name), "dim"))
            if agent.description:
                label.append("\n" + agent.description, style="italic")
            options.append(Option(label, id=agent.name))
        with Vertical(id="dialog"):
            yield Static("Avec quel agent parler ? Une nouvelle session s'ouvre avec lui.", markup=False)
            yield OptionList(*options, id="agents")
            yield Static("Entrée : choisir · Échap : annuler", markup=False, classes="hint")

    def on_mount(self) -> None:
        """L'agent courant est sélectionné, la liste a le focus."""
        agents = self.query_one(OptionList)
        names = [agent.name for agent in self._config.agents]
        if self._current in names:
            agents.highlighted = names.index(self._current)
        agents.focus()

    @on(OptionList.OptionSelected)
    def _chosen(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)

    def action_cancel(self) -> None:
        """Garder l'agent courant."""
        self.dismiss(None)
