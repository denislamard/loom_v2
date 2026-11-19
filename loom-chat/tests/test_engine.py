"""Le moteur de la console et la traduction des événements, avec le modèle factice de la démo."""

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from loom_ia.access.api import Loom
from loom_ia.core.events.envelope import Event
from loom_ia.core.events.payloads import ApprovalRequested, RunCompleted
from loom_ia.core.model.content import JsonBlock, TextBlock, ToolOutput
from loom_ia.core.model.question import NoAnswer, QuestionReply
from loom_ia.core.model.run_state import Rejected
from loom_ia.core.model.streaming import TextDelta

from loom_chat import engine as engine_module
from loom_chat.engine import ChatEngine
from loom_chat.events import ApprovalAsked, QuestionAsked, RequestClosed, RunEnded, Seen, TextChunk, UserLine, signature, summarize, to_chat_events
from loom_chat.settings import AGENT, ANTHROPIC_MODEL, USER, create_project, load, model_label


def engine_for(folder: Path) -> tuple[Loom, ChatEngine]:
    create_project(folder, "fake")
    loom = Loom(load(folder))
    return loom, ChatEngine(loom, user=USER)


async def test_demo_run_goes_through_approval_and_question(tmp_path: Path) -> None:
    loom, engine = engine_for(tmp_path)
    async with loom:
        seen: list[object] = []

        async def emit(event: object) -> None:
            seen.append(event)
            if isinstance(event, ApprovalAsked):
                assert event.tool == "fetch_page"
                assert engine.settle(event.call_id, Rejected(reason="test"))
            if isinstance(event, QuestionAsked):
                assert engine.settle(event.call_id, QuestionReply(answers=(("Court",),), by=USER))

        await asyncio.wait_for(engine.run(AGENT, None, "bonjour", emit), 60)

        ended = [e for e in seen if isinstance(e, RunEnded)]
        assert ended and ended[-1].status == "completed"
        # Le message de l'utilisateur vient du journal, en direct aussi ; sa réponse ferme la question.
        assert [e.text for e in seen if isinstance(e, UserLine)] == ["bonjour"]
        assert [e.text for e in seen if isinstance(e, RequestClosed) and e.outcome == "answered"] == ["Court"]
        listing = await engine.sessions()
        assert len(listing) == 1
        detail = await engine.detail(listing[0].session_id)
        assert [e.text for e in detail.events if isinstance(e, UserLine)] == ["bonjour"]
        assert detail.approvals == () and detail.questions == ()


async def test_unanswered_question_stays_open_and_can_be_answered_later(tmp_path: Path) -> None:
    loom, engine = engine_for(tmp_path)
    async with loom:
        asked: list[QuestionAsked] = []

        async def emit(event: object) -> None:
            if isinstance(event, QuestionAsked):
                asked.append(event)
                assert engine.settle(event.call_id, NoAnswer(reason="plus tard"))
            if isinstance(event, ApprovalAsked):
                assert engine.settle(event.call_id, Rejected(reason="test"))

        await asyncio.wait_for(engine.run(AGENT, None, "bonjour", emit), 60)
        session_id = (await engine.sessions())[0].session_id
        detail = await engine.detail(session_id)
        assert len(detail.questions) == 1
        pending = detail.questions[0]
        # Plus tard : la question reste ouverte, hors du run en cours.
        assert not engine.settle(pending.call_id, QuestionReply(text="court", by=USER))
        await engine.answer_later(pending.run_id, pending.call_id, QuestionReply(answers=(("Court",),), by=USER))
        for _ in range(60):
            await asyncio.sleep(0.5)
            if not (await engine.detail(session_id)).questions:
                break
        assert (await engine.detail(session_id)).questions == ()


async def test_settle_refuses_unknown_call(tmp_path: Path) -> None:
    loom, engine = engine_for(tmp_path)
    async with loom:
        assert engine.settle("inconnu", Rejected(reason="x")) is False


class _OneApprovalLoom:
    """Un faux flux : un élément, puis la fin du run. L'approbation n'est jamais tranchée."""

    async def stream(self, agent: str, text: str, **_: Any) -> AsyncIterator[object]:
        yield object()


async def test_run_forgets_its_calls_when_it_ends(monkeypatch: pytest.MonkeyPatch) -> None:
    """Une demande restée sans réponse ne reste pas « en cours » une fois son run terminé."""
    approval = ApprovalAsked(session_id="s", run_id="r", call_id="c1", tool="fetch_page", arguments={}, reason="")

    def fake_events(item: object) -> tuple[ApprovalAsked, ...]:
        return (approval,)

    monkeypatch.setattr(engine_module, "to_chat_events", fake_events)
    engine = ChatEngine(_OneApprovalLoom(), user=USER)  # type: ignore[arg-type]

    async def emit(event: object) -> None:
        return None

    await engine.run(AGENT, None, "bonjour", emit)
    # Le run est fini : sa demande n'est plus en cours, une réponse tardive ne doit pas être acceptée ici.
    assert engine.settle("c1", Rejected(reason="trop tard")) is False


def test_chat_events_keep_model_text_and_ignore_the_rest() -> None:
    """Le texte du modèle devient un fragment ; ce qui n'intéresse pas l'écran donne une liste vide."""
    assert to_chat_events(TextDelta(text="Bonjour")) == (TextChunk(text="Bonjour"),)
    assert to_chat_events(object()) == ()  # type: ignore[arg-type]


def test_subrun_events_keep_the_root_run_and_never_end_it() -> None:
    """Un sous-run ne termine pas le run de la session ; ses demandes passent, avec leur propre run."""
    ended = Event.model_construct(session_id="s", run_id="child", root_run_id="root", payload=RunCompleted.model_construct(output=None, cost_usd=0.5))
    assert to_chat_events(ended) == (Seen(session_id="s", run_id="root"),)
    request = ApprovalRequested.model_construct(call_id="c", tool_name="fetch_page", arguments={}, reason="")
    asked = to_chat_events(Event.model_construct(session_id="s", run_id="child", root_run_id="root", payload=request))
    assert asked == (
        Seen(session_id="s", run_id="root"),
        ApprovalAsked(session_id="s", run_id="child", call_id="c", tool="fetch_page", arguments={}, reason=""),
    )


def test_tool_summary_is_readable() -> None:
    """Le résultat d'un outil se lit sur une ligne : texte, valeurs d'un JSON plat, coupé au besoin (l'erreur est dans l'état du bloc)."""
    assert summarize(ToolOutput(blocks=(TextBlock(text="Il est\n  midi"),))) == "Il est midi"
    assert summarize(ToolOutput(blocks=(JsonBlock(data={"zone": "Europe/Paris", "heure": "12:00"}),))) == "Europe/Paris · 12:00"
    assert summarize(ToolOutput(blocks=(JsonBlock(data={"a": [1, 2]}),))) == '{"a":[1,2]}'
    assert summarize(ToolOutput(blocks=(TextBlock(text="introuvable"),), is_error=True)) == "introuvable"
    assert summarize(ToolOutput()) == "ok"
    long = summarize(ToolOutput(blocks=(TextBlock(text="x" * 500),)), width=20)
    assert len(long) == 20 and long.endswith("…")


def test_anthropic_settings_use_the_declared_model(tmp_path: Path) -> None:
    """Le modèle Claude de la config est celui déclaré dans les réglages, une seule fois."""
    create_project(tmp_path, "anthropic")
    assert model_label(load(tmp_path), AGENT) == ANTHROPIC_MODEL


def test_signature_reads_like_a_function_call() -> None:
    assert signature("current_datetime", {}) == "current_datetime()"
    assert signature("fetch_page", {"url": "https://a", "n": 3}) == 'fetch_page(url="https://a", n=3)'
    long = signature("sql", {"query": "SELECT " + "x, " * 50}, width=30)
    assert len(long) == 30 and long.endswith("…)")
