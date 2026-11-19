"""La page Textual : elle s'ouvre, reçoit un message, et montre les fenêtres d'approbation et de question."""

import asyncio
import io
import os
import sys
import textwrap
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from loom_ia.core.model.question import Question, QuestionReply
from textual.app import App
from textual.pilot import Pilot
from textual.widgets import Input, RadioButton, SelectionList

from loom_chat import __main__ as cli
from loom_chat.app import ChatApp, View, resumable_runs
from loom_chat.engine import RunLine
from loom_chat.events import ApprovalAsked, QuestionAsked, RequestClosed, ToolDone, ToolLine, ToolStarted
from loom_chat.modals import AgentModal, ApprovalModal, QuestionModal, RequestModal
from loom_chat.settings import AGENT, BOOT, ChatSettings, ModelKind, create_project, load
from loom_chat.widgets import Conversation, SessionsPanel, ToolCard

FINAL = "Merci. C'est la fin de la démonstration : vous pouvez poser une autre question."


async def _until(pilot: Pilot[Any], predicate: Callable[[], bool], seconds: float = 60.0) -> None:
    for _ in range(int(seconds / 0.1)):
        if predicate():
            return
        await pilot.pause(0.1)
    raise AssertionError("condition non atteinte")


def settings_for(folder: Path) -> ChatSettings:
    """Les réglages d'un dossier d'agent de démo, créé au premier appel."""
    if not (folder / BOOT).is_file():
        create_project(folder, "fake")
    return ChatSettings(config=load(folder), user="local")


def _roles(view: View) -> list[str]:
    return [line[0] for line in view.lines if isinstance(line, tuple)]


def _tool(view: View, name: str) -> ToolLine:
    return next(line for line in view.lines if isinstance(line, ToolLine) and line.name == name)


def _view(app: ChatApp) -> View:
    view = app.active_view
    assert view is not None
    return view


async def _to_deferred_question(app: ChatApp, pilot: Pilot[Any]) -> View:
    """Envoie « bonjour », refuse l'approbation, puis remet la question à plus tard."""
    await pilot.press(*"bonjour")
    await pilot.press("enter")
    await _until(pilot, lambda: isinstance(app.screen, ApprovalModal))
    await pilot.click("#reject")
    await _until(pilot, lambda: isinstance(app.screen, QuestionModal))
    await pilot.press("escape")
    await _until(pilot, lambda: bool(_view(app).questions) and not _view(app).busy)
    return _view(app)


async def test_page_runs_approval_then_question(tmp_path: Path) -> None:
    app = ChatApp(settings_for(tmp_path))
    async with app.run_test(size=(140, 45)) as pilot:
        view = await _to_deferred_question(app, pilot)
        assert view.session_id is not None
        assert view.lines[0] == ("Vous", "bonjour")  # le message de l'utilisateur s'affiche
        assert view.state() == "en attente de réponse"
        # Un texte saisi pendant la question ouvre la fenêtre, pré-rempli : il ne répond jamais en silence.
        await pilot.press(*"court")
        await pilot.press("enter")
        await _until(pilot, lambda: isinstance(app.screen, QuestionModal))
        assert app.screen.query_one("#free", Input).value == "court"
        # Entrée dans la réponse libre envoie la réponse ; le run repris va au bout, et sa réponse s'affiche.
        await pilot.press("enter")
        await _until(pilot, lambda: ("loom", FINAL) in view.lines and not view.busy)
        assert _tool(view, "ask").summary == "court"  # la réponse est dans le bloc de l'appel
        assert _tool(view, "fetch_page").status == "rejected"
        assert not view.questions


async def test_reopened_session_shows_what_was_shown_live(tmp_path: Path) -> None:
    """Relue depuis le journal, une session montre les mêmes lignes qu'en direct."""
    app = ChatApp(settings_for(tmp_path))
    async with app.run_test(size=(140, 45)) as pilot:
        view = await _to_deferred_question(app, pilot)
        await pilot.press("enter")
        await _until(pilot, lambda: isinstance(app.screen, QuestionModal))
        app.screen.query(RadioButton).first().value = True
        await pilot.click("#send")
        await _until(pilot, lambda: ("loom", FINAL) in view.lines and not view.busy)
        live, session_id = list(view.lines), view.session_id
    assert session_id is not None
    again = ChatApp(settings_for(tmp_path))
    async with again.run_test(size=(140, 45)):
        await again.open_session(session_id)
        assert _view(again).lines == live


async def test_unopened_sessions_are_folded_until_unfolded(tmp_path: Path) -> None:
    """Une session pas encore ouverte est repliée (▶) ; la déplier lit ses runs."""
    app = ChatApp(settings_for(tmp_path))
    async with app.run_test(size=(140, 45)) as pilot:
        await _to_deferred_question(app, pilot)
    again = ChatApp(settings_for(tmp_path))
    async with again.run_test(size=(140, 45)) as pilot:
        panel = again.query_one(SessionsPanel)
        node = next(n for n in panel.root.children if n.data is not None and n.data in {i.session_id for i in again.listing})
        assert node.allow_expand and not node.is_expanded and not node.children
        node.expand()
        await _until(pilot, lambda: bool(next(n for n in panel.root.children if n.data == node.data).children))
        unfolded = next(n for n in panel.root.children if n.data == node.data)
        assert unfolded.is_expanded


async def test_ctrl_x_cancels_from_the_composer(tmp_path: Path) -> None:
    """Le focus est dans la saisie, dont Ctrl+X coupe le texte : l'annulation passe quand même."""
    app = ChatApp(settings_for(tmp_path))
    calls: list[bool] = []
    async with app.run_test(size=(140, 45)) as pilot:
        app.action_cancel_run = lambda: calls.append(True)  # type: ignore[method-assign]
        assert isinstance(app.focused, Input)
        await pilot.press("ctrl+x")
        await pilot.pause(0.2)
    assert calls == [True]


async def test_ctrl_n_reuses_an_empty_new_session(tmp_path: Path) -> None:
    app = ChatApp(settings_for(tmp_path))
    async with app.run_test(size=(140, 45)) as pilot:
        first = _view(app)
        await pilot.press("ctrl+n")
        await pilot.press("ctrl+n")
        assert _view(app) is first


async def test_failed_deferred_answer_keeps_the_question_and_says_why(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Une réponse que loom n'a pas pu écrire ne fait pas disparaître la question, et l'erreur s'affiche."""
    app = ChatApp(settings_for(tmp_path))
    async with app.run_test(size=(140, 45)) as pilot:
        view = await _to_deferred_question(app, pilot)

        async def broken(*_: object) -> None:
            raise RuntimeError("journal indisponible")

        monkeypatch.setattr(app.engine, "answer_later", broken)
        await pilot.press("enter")
        await _until(pilot, lambda: isinstance(app.screen, QuestionModal))
        app.screen.query(RadioButton).first().value = True
        await pilot.click("#send")
        await _until(pilot, lambda: "erreur" in _roles(view))
        assert ("erreur", "Réponse : journal indisponible") in view.lines
        assert len(view.questions) == 1


async def test_question_answered_elsewhere_is_not_lost(tmp_path: Path) -> None:
    """Répondue ailleurs (CLI, REST), la question se ferme ici avec un message, et la session est relue."""
    app = ChatApp(settings_for(tmp_path))
    async with app.run_test(size=(140, 45)) as pilot:
        view = await _to_deferred_question(app, pilot)
        ask = next(iter(view.questions.values()))
        await app.engine.answer_later(ask.run_id, ask.call_id, QuestionReply(text="ailleurs", by="local"))
        await pilot.press("enter")
        await _until(pilot, lambda: isinstance(app.screen, QuestionModal))
        app.screen.query(RadioButton).first().value = True
        await pilot.click("#send")
        await _until(pilot, lambda: "système" in _roles(view))
        assert not view.questions
        assert _tool(view, "ask").summary == "ailleurs"


def _asked(*labels: str, multiple: bool = False) -> QuestionAsked:
    question = Question.model_validate({"question": "Choix ?", "header": "C", "multiple": multiple, "options": [{"label": label} for label in labels]})
    return QuestionAsked(session_id="s" * 13, run_id="r", call_id="c", questions=(question,))


class _Host(App[None]):
    def __init__(self, modal: RequestModal[Any]) -> None:
        super().__init__()
        self.modal = modal
        self.results: list[object] = []

    def on_mount(self) -> None:
        self.push_screen(self.modal, self.results.append)


@pytest.mark.parametrize("multiple", [False, True])
async def test_option_labels_are_sent_exactly(multiple: bool) -> None:
    """Un libellé avec des crochets s'affiche et part tel quel, sans être lu comme du markup."""
    labels = ("Prix [b]HT", "Fin [/]")
    host = _Host(QuestionModal(_asked(*labels, multiple=multiple), "local"))
    async with host.run_test() as pilot:
        await pilot.pause(0.2)
        if multiple:
            host.screen.query_one(SelectionList).select_all()
        else:
            host.screen.query(RadioButton).first().value = True
        await pilot.click("#send")
        await pilot.pause(0.2)
    reply = host.results[0]
    assert isinstance(reply, QuestionReply)
    assert reply.answers == ((labels if multiple else labels[:1]),)


async def test_withdrawn_modal_closes_now_or_when_back_on_top() -> None:
    """Une demande close ailleurs ferme sa fenêtre ; sous une autre fenêtre, elle se ferme en revenant au premier plan."""
    below = QuestionModal(_asked("A"), "local")
    host = _Host(below)
    async with host.run_test() as pilot:
        await pilot.pause(0.1)
        above = QuestionModal(_asked("B"), "local")
        host.push_screen(above, host.results.append)
        await pilot.pause(0.1)
        below.withdraw()
        await pilot.pause(0.1)
        assert host.screen is above  # pas fermée par erreur : ce n'est pas elle qui est au premier plan
        await pilot.press("escape")  # « plus tard » sur la fenêtre du dessus
        await pilot.pause(0.3)
        assert host.results == [None, None] and below.withdrawn
        assert not isinstance(host.screen, QuestionModal)


def test_tool_call_is_one_line_updated_in_place() -> None:
    """Un appel d'outil est un seul bloc : en attente, refusé ou terminé, à la même place."""
    view = View(key="k")
    view.apply(ApprovalAsked(session_id="s", run_id="r", call_id="c1", tool="fetch_page", arguments={"url": "https://a"}, reason=""))
    assert view.lines == [ToolLine(call_id="c1", name="fetch_page", arguments={"url": "https://a"}, status="waiting", waiting_for="approbation")]
    view.apply(RequestClosed(call_id="c1", outcome="rejected"))
    view.apply(ToolDone(name="fetch_page", call_id="c1", summary="refusé", detail="refusé", is_error=True, latency_ms=1))
    assert len(view.lines) == 1 and _tool(view, "fetch_page").status == "rejected"
    view.apply(ToolStarted(name="current_datetime", call_id="c2", arguments={}))
    view.apply(ToolDone(name="current_datetime", call_id="c2", summary="12:00", detail="{}", latency_ms=12))
    assert view.lines[1] == ToolLine(call_id="c2", name="current_datetime", arguments={}, status="done", summary="12:00", detail="{}", latency_ms=12)


async def test_tool_card_folds_and_unfolds(tmp_path: Path) -> None:
    """Le bloc montre l'appel et son état ; Entrée le déplie, et il reste déplié quand il change."""
    app = ChatApp(settings_for(tmp_path))
    async with app.run_test(size=(140, 45)) as pilot:
        conversation = app.query_one(Conversation)
        line = ToolLine(call_id="c", name="fetch_page", arguments={"url": "https://a"}, status="running")
        await conversation.show([line])
        card = conversation.query_one(ToolCard)
        assert str(card.border_title) == '⚙ fetch_page(url="https://a")'
        assert "en cours" in str(card.border_subtitle)
        card.focus()
        await pilot.press("enter")
        assert card.expanded
        await conversation.show([ToolLine(call_id="c", name="fetch_page", arguments={"url": "https://a"}, status="done", summary="ok", latency_ms=2100)])
        assert conversation.query_one(ToolCard) is card and card.expanded
        assert str(card.border_subtitle) == "✓ 2,1 s"


async def test_conversation_updates_lines_in_place(tmp_path: Path) -> None:
    """Un fragment de réponse met à jour la ligne montée au lieu de remonter toute la conversation."""
    app = ChatApp(settings_for(tmp_path))
    async with app.run_test(size=(140, 45)):
        conversation = app.query_one(Conversation)
        await conversation.show([("Vous", "bonjour")])
        first = conversation.children[0]
        await conversation.show([("Vous", "bonjour"), ("loom", "Bon")])
        await conversation.show([("Vous", "bonjour"), ("loom", "Bonjour !")])
        children = list(conversation.children)
        assert len(children) == 2
        assert children[0] is first
        await conversation.show([])
        assert list(conversation.children) == []


def test_resumable_runs_skips_paused_terminal_and_current() -> None:
    """« Reprendre » ignore les runs en pause, les runs terminés et le run en cours."""
    runs = (
        RunLine("r1", "assistant", "completed"),
        RunLine("r2", "assistant", "paused"),
        RunLine("r3", "assistant", "awaiting_tools"),
        RunLine("r4", "assistant", "failed"),
        RunLine("r5", "assistant", "ready_for_model"),
    )
    # r2 attend une décision humaine (pas une reprise), r5 est le run en cours.
    assert [run.run_id for run in resumable_runs(runs, current="r5")] == ["r3"]


def test_env_file_of_the_current_directory_is_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Sans --env-file ni amorce, c'est le .env du dossier courant qui est lu."""
    (tmp_path / ".env").write_text("LOOM_CHAT_ESSAI=oui\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(os, "environ", {})
    monkeypatch.setattr(sys, "stdin", io.StringIO())  # pas un terminal : le lancement s'arrête avant la création
    assert cli.main(["--model", "fake"]) == 2
    assert os.environ["LOOM_CHAT_ESSAI"] == "oui"


def test_malformed_env_file_is_a_clean_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    env = tmp_path / "bad.env"
    env.write_text("=sans_nom\n", encoding="utf-8")
    assert cli.main(["--env-file", str(env)]) == 2
    assert "bad.env" in capsys.readouterr().err


class _Tty(io.StringIO):
    def isatty(self) -> bool:
        return True


def _answer(monkeypatch: pytest.MonkeyPatch, folder: Path, reply: str | None) -> None:
    """Lancement depuis `folder`, dans un terminal, avec `reply` tapé à la question (None : Ctrl+D)."""

    def typed(prompt: str = "") -> str:
        if reply is None:
            raise EOFError
        return reply

    monkeypatch.chdir(folder)
    monkeypatch.setattr(sys, "stdin", _Tty())
    monkeypatch.setattr("builtins.input", typed)


def test_aborted_creation_is_a_clean_exit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _answer(monkeypatch, tmp_path, None)
    assert cli.main(["--model", "fake"]) == cli.ABORTED
    assert list(tmp_path.iterdir()) == []


def test_refused_creation_writes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _answer(monkeypatch, tmp_path, "n")
    assert cli.main(["--model", "fake"]) == 1
    assert list(tmp_path.iterdir()) == []


def _no_screen(monkeypatch: pytest.MonkeyPatch, opened: list[ChatSettings]) -> None:
    """La console ne s'ouvre pas pour de bon : on garde seulement ce qu'elle aurait chargé."""

    def run(self: ChatApp, *_: object, **__: object) -> None:
        opened.append(self.settings)

    monkeypatch.setattr(ChatApp, "run", run)


def test_accepted_creation_writes_loom_files_then_opens(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Entrée crée les fichiers loom ordinaires du dossier, puis la console s'ouvre sur eux."""
    opened: list[ChatSettings] = []
    _no_screen(monkeypatch, opened)
    _answer(monkeypatch, tmp_path, "")
    assert cli.main(["--model", "fake"]) == 0
    assert sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*") if p.is_file()) == [
        "agents/assistant.yaml",
        "loom-boot.yaml",
        "loom.yaml",
        "prompts/system.md",
    ]
    assert [a.name for a in opened[0].config.agents] == [AGENT] and opened[0].user == "local"


def test_edited_files_are_what_the_console_uses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Les fichiers appartiennent à l'utilisateur : la console relit ce qu'il y a mis, sans rien réécrire."""
    create_project(tmp_path, "fake")
    agent = tmp_path / "agents" / "assistant.yaml"
    agent.write_text(agent.read_text(encoding="utf-8").replace("max_iterations: 6", "max_iterations: 3"), encoding="utf-8")
    opened: list[ChatSettings] = []
    _no_screen(monkeypatch, opened)
    monkeypatch.chdir(tmp_path)
    assert cli.main([]) == 0
    assert opened[0].config.agents[0].max_iterations == 3
    assert "max_iterations: 3" in agent.read_text(encoding="utf-8")


def test_loom_files_without_boot_are_never_overwritten(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "loom.yaml").write_text("à moi\n", encoding="utf-8")
    _answer(monkeypatch, tmp_path, "")
    assert cli.main(["--model", "fake"]) == 2
    assert (tmp_path / "loom.yaml").read_text(encoding="utf-8") == "à moi\n"
    assert "loom.yaml" in capsys.readouterr().err


def test_several_users_need_a_choice(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    create_project(tmp_path, "fake")
    loom = tmp_path / "loom.yaml"
    loom.write_text(
        loom.read_text(encoding="utf-8").replace(
            "  - id: local\n    roles: [owner]\n", "  - id: local\n    roles: [owner]\n  - id: marie\n    roles: [owner]\n"
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    assert cli.main([]) == 2
    assert "--user" in capsys.readouterr().err


async def test_agent_is_chosen_in_the_console(tmp_path: Path) -> None:
    """Avec plusieurs agents, la console demande lequel au démarrage ; la session prend cet agent."""
    create_project(tmp_path, "fake")
    second = (tmp_path / "agents" / "assistant.yaml").read_text(encoding="utf-8").replace("name: assistant", "name: second")
    (tmp_path / "agents" / "second.yaml").write_text(second, encoding="utf-8")
    app = ChatApp(settings_for(tmp_path))
    async with app.run_test(size=(140, 45)) as pilot:
        await _until(pilot, lambda: isinstance(app.screen, AgentModal))
        names = sorted(a.name for a in app.settings.config.agents)
        await pilot.press("down" if names[1] != _view(app).agent else "up", "enter")
        await _until(pilot, lambda: not isinstance(app.screen, AgentModal))
        await pilot.pause(0.2)
        assert _view(app).agent in names
        assert _view(app).agent != app.settings.config.agents[0].name


CRASH = textwrap.dedent(
    """
    import asyncio, os
    from pathlib import Path
    from loom_ia.access.api import Loom
    from loom_chat.engine import ChatEngine
    from loom_chat.events import ApprovalAsked
    from loom_chat.settings import AGENT, load

    async def main():
        loom = Loom(load(Path(os.environ["DATA"])))
        await loom.__aenter__()
        engine = ChatEngine(loom, user="local")

        async def emit(event):
            if isinstance(event, ApprovalAsked):
                os._exit(0)  # arrêt brutal, sans aucun nettoyage : comme une coupure

        await engine.run(AGENT, None, "bonjour", emit)

    asyncio.run(main())
    """
)


async def test_restart_reopens_pending_approval(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    create_project(tmp_path, "fake")
    env = {**os.environ, "DATA": str(tmp_path)}
    crash = await asyncio.create_subprocess_exec(sys.executable, "-c", CRASH, env=env)
    assert await asyncio.wait_for(crash.wait(), 120) == 0

    app = ChatApp(settings_for(tmp_path))
    async with app.run_test(size=(140, 45)) as pilot:
        await _until(pilot, lambda: bool(app.listing))
        sid = app.listing[0].session_id
        await app.open_session(sid)
        await _until(pilot, lambda: isinstance(app.screen, ApprovalModal) and bool(app.screen.query("#approve")))

        async def no_relaunch(*_: object, **__: object) -> None:
            raise AssertionError("après une décision, c'est loom qui reprend le run, pas la console")

        monkeypatch.setattr(app.engine, "resume_when_free", no_relaunch)
        await pilot.click("#approve")
        # Le process tué tient encore le run : la console dit que la reprise attend sa concession.
        await _until(pilot, lambda: any(line[1].startswith("Reprise à la fin de la concession") for line in _view(app).lines if isinstance(line, tuple)))
        # La reprise tourne en arrière-plan : la question du modèle reste en attente.
        await _until(pilot, lambda: bool(_view(app).questions), seconds=120)
        assert _view(app).approvals == {}
        assert _view(app).state() == "en attente de réponse"
        # Le texte du modèle écrit pendant la reprise s'affiche, bien qu'il n'y ait pas de morceaux en suivi.
        assert ("loom", "Une question avant de répondre.") in _view(app).lines


def test_welcome_says_what_to_put_in_env(tmp_path: Path) -> None:
    """L'accueil dit quoi mettre dans .env, et si la clé est déjà là — sans jamais montrer sa valeur."""
    from rich.console import Console

    def shown(kind: ModelKind, environ: dict[str, str]) -> str:
        console = Console(width=100, record=True)
        console.print(cli.env_help(tmp_path, kind, environ))
        return console.export_text()

    missing = shown("anthropic", {})
    assert "ANTHROPIC_API_KEY=sk-ant-" in missing and "à renseigner" in missing and "à créer" in missing
    (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=sk-ant-secret\n", encoding="utf-8")
    present = shown("anthropic", {"ANTHROPIC_API_KEY": "sk-ant-secret"})
    assert "déjà définie" in present and "trouvé" in present and "secret" not in present
    assert "sdk: anthropic" in shown("fake", {})
