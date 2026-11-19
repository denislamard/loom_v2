"""Éléments de la page : barre du haut, conversation, panneau des sessions et des runs."""

import asyncio
import json
from collections.abc import Sequence
from typing import ClassVar

from rich.table import Table
from rich.text import Text
from textual.binding import Binding, BindingType
from textual.containers import VerticalScroll
from textual.content import Content
from textual.timer import Timer
from textual.widgets import Static, Tree
from textual.widgets.tree import TreeNode

from loom_chat.engine import RunLine, SessionListing
from loom_chat.events import Line, ToolLine, duration, signature

ROLE_CLASS = {"Vous": "vous", "loom": "loom", "outil": "outil", "erreur": "erreur", "système": "systeme"}
ID_WIDTH = 13  # 8 premiers caractères et le début du suivant : l'horodatage UUIDv7 en millisecondes


def short(identifier: str) -> str:
    """Début d'un identifiant UUIDv7, assez pour distinguer les sessions et les runs.

    Args:
        identifier: L'identifiant.
    """
    return identifier[:ID_WIDTH]


def _css(role: str) -> str:
    return f"line {ROLE_CLASS.get(role, 'outil')}"


def _rendered(role: str, text: str) -> Text:
    return Text.assemble((f"{role}  ", "bold"), text)


SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


def _status(line: ToolLine, frame: int = 0) -> str:
    match line.status:
        case "running":
            return f"{SPINNER[frame % len(SPINNER)]} en cours"
        case "waiting":
            return f"⏸ {line.waiting_for or 'en attente'}"
        case "rejected":
            return "⊘ refusé"
        case "expired":
            return "⌛ expiré"
        case "error":
            return f"✗ erreur · {duration(line.latency_ms)}"
        case "done":
            # La durée d'une question est celle de l'outil, pas l'attente de la réponse : elle ne dit rien.
            return "✓ répondu" if line.name == "ask" else f"✓ {duration(line.latency_ms)}"


class ToolCard(Static, can_focus=True):
    """Un appel d'outil : l'appel en titre, son état en bas à droite, le résultat dedans.

    Replié, le bloc montre le résultat sur une ligne ; déplié (clic ou Entrée), les
    arguments et le résultat complets.

    Args:
        line: L'appel.
    """

    DEFAULT_CSS = """
    ToolCard {
        height: auto; margin-bottom: 1; padding: 0 1;
        border: round $primary; border-title-color: $text; border-subtitle-align: right;
        color: $text-muted;
    }
    ToolCard.-waiting { border: round $warning; }
    ToolCard.-done { border: round $success-darken-2; }
    ToolCard.-error { border: round $error; }
    ToolCard.-rejected, ToolCard.-expired { border: round $panel-lighten-2; }
    ToolCard:focus { background: $boost; }
    """
    BINDINGS: ClassVar[list[BindingType]] = [Binding("enter", "fold", "Déplier", show=False)]

    def __init__(self, line: ToolLine) -> None:
        super().__init__()
        self.line = line
        self.expanded = False
        self._frame = 0
        self._spin: Timer | None = None

    def on_mount(self) -> None:
        """Premier affichage."""
        self.set_line(self.line)

    def set_line(self, line: ToolLine) -> None:
        """Met le bloc à jour ; il reste déplié s'il l'était.

        Args:
            line: L'appel, dans son nouvel état.
        """
        self.line = line
        self.set_classes(f"-{line.status}")
        self.border_title = Content(f"⚙ {signature(line.name, line.arguments)}")
        if line.status == "running" and self._spin is None:
            self._spin = self.set_interval(0.1, self._tick)
        elif line.status != "running" and self._spin is not None:
            self._spin.stop()
            self._spin = None
        self.border_subtitle = Content(_status(line, self._frame))
        self.update(self._body())

    def _tick(self) -> None:
        self._frame += 1
        self.border_subtitle = Content(_status(self.line, self._frame))

    def _body(self) -> Text | Table:
        line = self.line
        if not self.expanded:
            if line.summary:
                return Text.assemble(("→ ", "bold"), line.summary)
            waiting = {"approbation": "en attente de votre décision", "question": "en attente de votre réponse"}
            return Text(waiting.get(line.waiting_for, "…"), style="italic")
        grid = Table.grid(padding=(0, 2))
        grid.add_column(style="bold")
        grid.add_column()
        grid.add_row("Arguments", json.dumps(line.arguments, ensure_ascii=False, indent=2))
        if line.name == "ask" and line.summary:
            grid.add_row("Réponse", line.summary)
        if line.detail or line.summary:
            grid.add_row("Résultat", Text(line.detail or line.summary))
        return grid

    def action_fold(self) -> None:
        """Déplie ou replie le bloc."""
        self.expanded = not self.expanded
        self.update(self._body())

    def on_click(self) -> None:
        """Un clic déplie ou replie le bloc."""
        self.action_fold()


def _widget(line: Line) -> Static:
    if isinstance(line, ToolLine):
        return ToolCard(line)
    role, text = line
    return Static(_rendered(role, text), classes=_css(role))


def _same_kind(widget: Static, line: Line) -> bool:
    return isinstance(widget, ToolCard) == isinstance(line, ToolLine)


class Conversation(VerticalScroll):
    """Les lignes affichées. Une ligne déjà montée est mise à jour sur place : un fragment de réponse ne remonte pas tout.

    Args:
        id: Identifiant du widget.
    """

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self._lines: list[Line] = []
        self._widgets: list[Static] = []
        # Deux affichages peuvent se croiser (run en cours et changement de session) : un seul à la fois.
        self._lock = asyncio.Lock()

    async def show(self, lines: Sequence[Line], *, pin: bool = False) -> None:
        """Affiche `lines`. Défile en bas si `pin`, ou si l'utilisateur y était déjà (il peut relire plus haut).

        Args:
            lines: Les lignes : ``(rôle, texte)``, ou un appel d'outil.
            pin: Défiler en bas quoi qu'il arrive.
        """
        async with self._lock:
            await self._sync(list(lines), pin=pin)

    async def _sync(self, lines: list[Line], *, pin: bool) -> None:
        follow = pin or not self._widgets or self.scroll_y >= self.max_scroll_y - 1
        # Au premier changement de nature (texte ↔ bloc d'outil), on remonte la suite.
        keep = 0
        while keep < min(len(self._widgets), len(lines)) and _same_kind(self._widgets[keep], lines[keep]):
            keep += 1
        for index in range(keep):
            if self._lines[index] == lines[index]:
                continue
            widget, line = self._widgets[index], lines[index]
            if isinstance(widget, ToolCard) and isinstance(line, ToolLine):
                widget.set_line(line)
            elif isinstance(line, tuple):
                role, text = line
                widget.set_classes(_css(role))
                widget.update(_rendered(role, text))
        surplus = self._widgets[keep:]
        del self._widgets[keep:]
        for widget in surplus:
            await widget.remove()
        fresh = [_widget(line) for line in lines[keep:]]
        if fresh:
            await self.mount_all(fresh)
            self._widgets.extend(fresh)
        self._lines = lines
        if follow:
            self.scroll_end(animate=False)


# Couleur de chaque état, dans la barre du haut et le panneau.
STATE_STYLE = {
    "inactif": "green3",
    "en cours": "deep_sky_blue1",
    "en attente d'approbation": "orange1",
    "en attente de réponse": "orange1",
}
RUN_STYLE = {"completed": "green3", "failed": "red3", "cancelled": "grey58", "paused": "orange1"}


class StatusBar(Static):
    """Barre du haut : état et agent à gauche, session et coût à droite."""

    DEFAULT_CSS = """
    StatusBar { height: 1; padding: 0 1; background: $panel; color: $text; }
    """

    def show_view(self, *, agent: str, model: str, state: str, session: str | None, cost: float) -> None:
        """Met la barre à jour.

        Args:
            agent: Agent.
            model: Libellé du modèle.
            state: État de la session.
            session: Session, ``None`` pour une nouvelle.
            cost: Coût cumulé de la session, en dollars.
        """
        color = STATE_STYLE.get(state, "white")
        left = Text.assemble(("● ", color), (agent, "bold"), ("  ", ""), (model, "dim"), ("   ", ""), (state, color))
        right = Text.assemble(("session ", "dim"), (short(session) if session else "nouvelle", ""), ("    ", ""), (f"{cost:.4f} $", "bold"))
        grid = Table.grid(expand=True)
        grid.add_column()
        grid.add_column(justify="right")
        grid.add_row(left, right)
        self.update(grid)


class SessionsPanel(Tree[str]):
    """Panneau de droite : les sessions, et leurs runs quand on les déplie.

    Une session dont les runs ne sont pas encore lus est repliée (▶) : les déplier
    les fait lire (``Tree.NodeExpanded``, traité par la page). Le curseur ne se voit
    que quand le panneau a le focus ; la session affichée est en gras.

    Attributes:
        expanded: Les sessions dépliées, gardées d'une reconstruction à l'autre.
    """

    DEFAULT_CSS = """
    SessionsPanel { height: 1fr; padding: 0 1; }
    SessionsPanel > .tree--cursor { background: transparent; text-style: none; }
    SessionsPanel > .tree--guides-selected { color: $block-cursor-blurred-background; }
    SessionsPanel:focus > .tree--cursor { background: $block-cursor-background; color: $block-cursor-foreground; }
    """

    def __init__(self) -> None:
        super().__init__("Sessions", id="sessions")
        self.show_root = False
        self.guide_depth = 3
        self.expanded: set[str] = set()

    def show(
        self,
        listing: list[SessionListing],
        unsaved: list[tuple[str, str]],
        runs: dict[str, tuple[RunLine, ...]],
        states: dict[str, str],
        active: str | None = None,
    ) -> None:
        """Reconstruit l'arbre ; le curseur reste sur la même session, et le défilement à sa place.

        Args:
            listing: Les sessions du journal.
            unsaved: Les nouvelles sessions pas encore écrites, ``(clé, libellé)``.
            runs: Les runs connus, par session ; absente, une session reste repliée jusqu'à ce qu'on la déplie.
            states: L'état des sessions ouvertes.
            active: Clé de la session affichée.
        """
        selected = self.cursor_node.data if self.cursor_node is not None else None
        scroll = self.scroll_y
        self.clear()
        target: TreeNode[str] | None = None
        for key, label in unsaved:
            leaf = self.root.add_leaf(Text(label, style="bold" if key == active else "italic"), data=key)
            target = leaf if key == selected else target
        for item in listing:
            sid = item.session_id
            label = Text(short(sid), style="bold" if sid == active else "")
            if state := states.get(sid, ""):
                label.append(f"  {state}", style=STATE_STYLE.get(state, "dim"))
            known = runs.get(sid)
            node = self.root.add(label, data=sid, allow_expand=known is None or bool(known))
            target = node if sid == selected else target
            for run in known or ():
                node.add_leaf(Text.assemble((short(run.run_id), "dim"), ("  ", ""), (run.status, RUN_STYLE.get(run.status, "deep_sky_blue1"))))
            if sid in self.expanded and known:
                node.expand()
        if target is not None:
            found = target
            # Les lignes de l'arbre ne sont calculées qu'au prochain rafraîchissement.
            self.call_after_refresh(self._restore, found, scroll)

    def _restore(self, node: TreeNode[str], scroll: float) -> None:
        self.move_cursor(node)
        self.scroll_to(y=scroll, animate=False)
