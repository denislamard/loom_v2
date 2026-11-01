# SPDX-License-Identifier: Apache-2.0
"""``loom.ask`` : le modèle pose une question, le moteur attend la réponse (wait_answer, lot 1).

Distincte d'une approbation : la question vient du modèle, la réponse de
l'utilisateur, et elle revient au modèle comme résultat de l'appel. Ces essais
éprouvent le moteur : la réponse est écrite au journal comme le fera
``Loom.answer`` (lot 2), puis le run est repris.
"""

import asyncio
import json
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml

from loom_ia.access.api import InvalidAnswer, Loom, QuestionStale, UnknownQuestion
from loom_ia.adapters.stores import JsonlEventStore
from loom_ia.config import ConfigError, load_config
from loom_ia.core.events import (
    Event,
    QuestionAnswered,
    QuestionAsked,
    RunCancelled,
    ToolCompleted,
)
from loom_ia.core.model import (
    DEFAULT_TENANT,
    Approved,
    NoAnswer,
    PendingApproval,
    PendingQuestion,
    Question,
    QuestionOption,
    QuestionReply,
    RunId,
    RunState,
    RunStatus,
    SessionId,
)
from loom_ia.core.projections import ProjectionError, fold
from loom_ia.engine import SessionWriter, run_scope
from loom_ia.engine.pending import question_note
from loom_ia.engine.questions import OFF_TOPIC
from loom_ia.testing import RunJournal, assert_replays, tool_call_message

SESSION = SessionId("s1")
QUESTION: dict[str, Any] = {
    "questions": [
        {
            "question": "Pour quel client faut-il préparer le devis ?",
            "header": "Client",
            "options": [{"label": "Dupont"}, {"label": "Martin"}],
        }
    ]
}
SCRIPT: list[dict[str, Any]] = [
    {"text": "", "tool_calls": [{"name": "ask", "arguments": QUESTION}]},
    {"text": "Devis préparé."},
]
OUTILS = '''
from loom_ia.tools import tool


@tool
async def envoyer(destinataire: str) -> str:
    """Envoie le devis."""
    return f"envoyé à {destinataire}"
'''


def config(
    tmp_path: Path,
    script: list[dict[str, Any]] = SCRIPT,
    *,
    ask: dict[str, Any] | None = None,
    child: list[dict[str, Any]] | None = None,
) -> Path:
    (tmp_path / "agents").mkdir(exist_ok=True)
    (tmp_path / "outils_qa.py").write_text(OUTILS, encoding="utf-8")
    models = [{"id": "FAKE", "sdk": "fake", "model": "f1", "params": {"script": script}}]
    if child is not None:
        models.append({"id": "ENFANT", "sdk": "fake", "model": "f2", "params": {"script": child}})
    root = {
        "version": 1,
        "imports": ["outils_qa"],
        "models": models,
        "storage": {"events": {"backend": "jsonl", "path": "data"}},
        "telemetry": {"logging": {"level": "CRITICAL"}},
    }
    (tmp_path / "loom.yaml").write_text(yaml.safe_dump(root), encoding="utf-8")
    tools: list[dict[str, Any]] = [
        {"python": "loom.ask", **(ask or {})},
        {"python": "envoyer", "side_effects": "irreversible", "approval": "always"},
    ]
    agent: dict[str, Any] = {
        "name": "demo",
        "description": "Devis.",
        "main": {"model": "FAKE", "system": "Tu prépares des devis."},
        "tools": tools,
    }
    if child is not None:
        helper = {
            "name": "aide",
            "description": "Aide.",
            "main": {"model": "ENFANT", "system": "Tu aides."},
            "tools": tools,
            "expose": {"rest": False, "mcp": False},
        }
        (tmp_path / "agents" / "aide.yaml").write_text(yaml.safe_dump(helper), encoding="utf-8")
        agent = {**agent, "tools": [], "subagents": [{"agent": "aide", "description": "Aide."}]}
    (tmp_path / "agents" / "demo.yaml").write_text(yaml.safe_dump(agent), encoding="utf-8")
    return tmp_path / "loom.yaml"


@asynccontextmanager
async def mounted(path: Path) -> AsyncGenerator[tuple[Loom, JsonlEventStore]]:
    store = JsonlEventStore(path.parent / "data")
    async with Loom(load_config(path), store=store) as loom:
        yield loom, store


async def answer(
    store: JsonlEventStore,
    owner: RunState,
    *,
    answers: tuple[tuple[str, ...], ...] | None = None,
    text: str | None = None,
) -> None:
    """La réponse écrite au journal, comme le fera ``Loom.answer``."""
    [asked] = owner.asking
    writer = await SessionWriter.open(store, DEFAULT_TENANT, owner.session_id)
    draft = run_scope(owner).draft(
        QuestionAnswered(
            call_id=asked.call_id, tool_name="ask", answers=answers, text=text, by="denis"
        )
    )
    await writer.append([draft])


def types(events: list[Event]) -> list[str]:
    return [e.type for e in events]


def tool_text(events: list[Event]) -> str:
    [completed] = [e.payload for e in events if isinstance(e.payload, ToolCompleted)]
    return completed.output.as_text


# --- Pause et reprise -------------------------------------------------------------------


def test_a_question_pauses_the_run_without_running_the_tool(tmp_path: Path) -> None:
    async def go() -> None:
        async with mounted(config(tmp_path)) as (loom, _):
            result = await loom.run("demo", "Prépare un devis.", session_id=SESSION)
            state = await loom.state(result.run_id, session_id=SESSION)
            events = await loom.export_session(SESSION)
        assert result.status is RunStatus.PAUSED
        assert state.awaiting == ()
        [asked] = state.asking
        assert asked.questions[0].header == "Client"
        assert [o.label for o in asked.questions[0].options] == ["Dupont", "Martin"]
        seen = types(events)
        assert "question.asked" in seen
        # L'outil ne s'exécute pas : ni tool.called ni tool.completed.
        assert "tool.called" not in seen and "tool.completed" not in seen
        # La concession est rendue, comme pour une approbation.
        assert seen[-2:] == ["run.transitioned", "run.claimed"]

    asyncio.run(go())


def test_the_paused_result_keeps_what_the_model_wrote_with_its_question(tmp_path: Path) -> None:
    # Essai Haiku (ask-09) : l'heure donnée, puis la question reposée. Le texte
    # écrit avec l'appel à ask est la réponse du run en pause.
    script: list[dict[str, Any]] = [
        {"text": "Il est 12 h 43.", "tool_calls": [{"name": "ask", "arguments": QUESTION}]},
        {"text": "Devis préparé."},
    ]

    async def go() -> None:
        async with mounted(config(tmp_path, script)) as (loom, _):
            paused = await loom.run("demo", "Prépare un devis.", session_id=SESSION)
            again = await loom.result(paused.run_id, session_id=SESSION)
            done = await loom.run("demo", "Dupont", session_id=SESSION)
        assert paused.status is RunStatus.PAUSED and paused.pending_questions
        assert paused.text == again.text == "Il est 12 h 43."
        assert done.run_id == paused.run_id and done.text == "Devis préparé."
        # Sans read_content, ce texte est masqué comme une réponse.
        assert paused.masked().text == "" and "text" in paused.masked().redacted

    asyncio.run(go())


def test_the_answer_comes_back_as_the_tool_result(tmp_path: Path) -> None:
    async def go() -> None:
        async with mounted(config(tmp_path)) as (loom, store):
            first = await loom.run("demo", "Prépare un devis.", session_id=SESSION)
            owner = await loom.state(first.run_id, session_id=SESSION)
            # Sans réponse, la reprise ne fait rien : le run attend l'utilisateur.
            still = await loom.resume(first.run_id, session_id=SESSION)
            assert still.status is RunStatus.PAUSED
            await answer(store, owner, answers=(("Dupont",),))
            result = await loom.resume(first.run_id, session_id=SESSION)
            events = await loom.export_session(SESSION)
        assert result.status is RunStatus.COMPLETED
        assert result.run_id == first.run_id and result.text == "Devis préparé."
        seen = types(events)
        assert seen.index("question.answered") < seen.index("tool.completed")
        assert tool_text(events) == (
            "Réponse de l'utilisateur :\n- Pour quel client faut-il préparer le devis ? → Dupont"
        )

    asyncio.run(go())


def test_a_free_message_is_an_answer(tmp_path: Path) -> None:
    async def go() -> None:
        async with mounted(config(tmp_path)) as (loom, store):
            first = await loom.run("demo", "Prépare un devis.", session_id=SESSION)
            await answer(store, await loom.state(first.run_id, session_id=SESSION), text="Dupont")
            await loom.resume(first.run_id, session_id=SESSION)
            events = await loom.export_session(SESSION)
        assert tool_text(events) == (
            f"Réponse de l'utilisateur, en message libre : Dupont\n\n{OFF_TOPIC}"
        )

    asyncio.run(go())


def test_a_malformed_question_goes_back_to_the_model(tmp_path: Path) -> None:
    script = [
        {
            "text": "",
            "tool_calls": [{"name": "ask", "arguments": {"questions": [{"question": ""}]}}],
        },
        {"text": "Je reformule."},
    ]

    async def go() -> None:
        async with mounted(config(tmp_path, script)) as (loom, _):
            result = await loom.run("demo", "Prépare un devis.", session_id=SESSION)
            events = await loom.export_session(SESSION)
        assert result.status is RunStatus.COMPLETED
        assert "question.asked" not in types(events)
        assert tool_text(events).startswith("Question invalide (question)")

    asyncio.run(go())


# --- Échéance, annulation -------------------------------------------------------------


def test_an_unanswered_question_expires_and_the_model_learns_it(tmp_path: Path) -> None:
    async def go() -> None:
        async with mounted(config(tmp_path, ask={"expires_in": 0.2})) as (loom, _):
            first = await loom.run("demo", "Prépare un devis.", session_id=SESSION)
            [asked] = (await loom.state(first.run_id, session_id=SESSION)).asking
            assert asked.expire_at is not None
            await asyncio.sleep(0.3)
            result = await loom.resume(first.run_id, session_id=SESSION)
            events = await loom.export_session(SESSION)
        # Une question expirée ne fait pas échouer le run : le modèle continue.
        assert result.status is RunStatus.COMPLETED
        assert "question.expired" in types(events)
        assert "pas répondu dans le délai" in tool_text(events)

    asyncio.run(go())


def test_a_cancelled_run_withdraws_its_question(tmp_path: Path) -> None:
    async def go() -> None:
        async with mounted(config(tmp_path)) as (loom, _):
            first = await loom.run("demo", "Prépare un devis.", session_id=SESSION)
            [asked] = (await loom.state(first.run_id, session_id=SESSION)).asking
            assert await loom.cancel(first.run_id, session_id=SESSION, by="denis")
            events = await loom.export_session(SESSION)
            state = await loom.state(first.run_id, session_id=SESSION)
        cancelled = next(e.payload for e in events if isinstance(e.payload, RunCancelled))
        assert cancelled.withdrawn == (asked.call_id,)
        assert state.asking == ()

    asyncio.run(go())


# --- Avec une approbation, avec un sous-agent -------------------------------------------


def test_a_question_and_an_approval_are_two_waits(tmp_path: Path) -> None:
    """Répondre ne tranche pas l'approbation : le run attend encore la décision."""
    script = [
        {
            "text": "",
            "tool_calls": [
                {"name": "ask", "arguments": QUESTION},
                {"name": "envoyer", "arguments": {"destinataire": "a@example.com"}},
            ],
        },
        {"text": "Fini."},
    ]

    async def go() -> None:
        async with mounted(config(tmp_path, script)) as (loom, store):
            first = await loom.run("demo", "Prépare et envoie.", session_id=SESSION)
            state = await loom.state(first.run_id, session_id=SESSION)
            assert len(state.asking) == 1 and len(state.awaiting) == 1
            await answer(store, state, text="Dupont")
            mid = await loom.resume(first.run_id, session_id=SESSION)
            assert mid.status is RunStatus.PAUSED and len(mid.pending_approvals) == 1
            await loom.approve(first.run_id, by="denis", session_id=SESSION)
            await loom.drain()
            done = await loom.result(first.run_id, session_id=SESSION)
        assert done.status is RunStatus.COMPLETED

    asyncio.run(go())


def test_a_subagent_question_holds_its_parent(tmp_path: Path) -> None:
    parent = [
        {"text": "", "tool_calls": [{"name": "aide", "arguments": {"message": "Le devis."}}]},
        {"text": "C'est fait."},
    ]

    async def go() -> None:
        async with mounted(config(tmp_path, parent, child=SCRIPT)) as (loom, store):
            first = await loom.run("demo", "Prépare un devis.", session_id=SESSION)
            # Comme pour une approbation : la racine attend son enfant, en pause.
            assert first.status is RunStatus.WAITING_CHILD
            events = await loom.export_session(SESSION)
            child = next(e.run_id for e in events if isinstance(e.payload, QuestionAsked))
            await answer(
                store, fold([e for e in events if e.run_id == child], child), text="Dupont"
            )
            result = await loom.resume(first.run_id, session_id=SESSION)
        assert result.status is RunStatus.COMPLETED and result.text == "C'est fait."

    asyncio.run(go())


# --- Rejeu, note, config, projection ----------------------------------------------------


def test_an_answered_run_replays_identically(tmp_path: Path) -> None:
    async def go() -> None:
        path = config(tmp_path)
        async with mounted(path) as (loom, store):
            first = await loom.run("demo", "Prépare un devis.", session_id=SESSION)
            await answer(store, await loom.state(first.run_id, session_id=SESSION), text="Dupont")
            await loom.resume(first.run_id, session_id=SESSION)
        # Au rejeu, la réponse vient du journal, en ligne, sans pause.
        await assert_replays(path, tmp_path / "data" / "default" / "s1.jsonl")

    asyncio.run(go())


def test_a_new_turn_hears_that_a_question_still_waits() -> None:
    pending = PendingQuestion(
        call_id="c1", questions=(Question(question="Quel client ?", options=()),)
    )
    note = question_note([pending])
    assert note is not None
    assert "attend encore la réponse de l'utilisateur : Quel client ?" in note.text
    assert "ne la repose pas" in note.text
    assert question_note([]) is None


def test_ask_refuses_settings_it_has_no_use_for(tmp_path: Path) -> None:
    path = config(tmp_path, ask={"approval": "always"})
    with pytest.raises(ConfigError, match=r"loom\.ask : approval ne se règle pas sur cet outil"):
        load_config(path)


def test_the_tool_the_model_sees(tmp_path: Path) -> None:
    async def go() -> None:
        async with mounted(config(tmp_path)) as (loom, _):
            [tool] = [d for d in loom.context("demo").tools.definitions() if d.name == "ask"]
        assert "ce n'est pas une autorisation" in tool.description
        size = len(tool.description) + len(
            json.dumps(tool.input_schema, ensure_ascii=False, separators=(",", ":"))
        )
        # Environ 320 tokens par appel de modèle, pour un agent qui déclare l'outil.
        assert size < 1_300

    asyncio.run(go())


def _after_question(*payloads: QuestionAnswered) -> list[Event]:
    """Journal d'un run dont le modèle pose la question c1, suivi de ces réponses."""
    journal = RunJournal(session_id=SESSION)
    journal.start("Prépare un devis.").model_turn(tool_call_message(("c1", "ask", QUESTION)))
    asked = QuestionAsked(
        call_id="c1",
        tool_name="ask",
        questions=(Question(question="Quel client ?", options=(QuestionOption(label="A"),)),),
        expire_at=datetime.now(UTC) + timedelta(hours=1),
    )
    drafts = [*journal.take(), journal.scope.draft(asked)]
    drafts += [journal.scope.draft(p) for p in payloads]
    return [draft.to_event(seq) for seq, draft in enumerate(drafts, start=1)]


def test_a_question_takes_one_answer() -> None:
    reply = QuestionAnswered(call_id="c1", tool_name="ask", text="A")
    events = _after_question(reply)
    state = fold(events, events[0].run_id)
    asked = state.question("c1")
    assert state.asking == () and asked is not None and asked.reply is not None
    twice = _after_question(reply, reply)
    with pytest.raises(ProjectionError, match="déjà close"):
        fold(twice, twice[0].run_id)


# --- SDK : answer(), le message qui répond, le répondeur en ligne (lot 2) ---------------


def test_answer_resumes_the_same_run(tmp_path: Path) -> None:
    async def go() -> None:
        async with mounted(config(tmp_path)) as (loom, _):
            first = await loom.run("demo", "Prépare un devis.", session_id=SESSION)
            [asked] = first.pending_questions
            assert asked.questions[0].header == "Client"
            info = await loom.session(SESSION)
            assert [q.call_id for q in info.pending_questions] == [asked.call_id]
            call = await loom.answer(
                first.run_id, answers=[["Dupont"]], by="denis", session_id=SESSION
            )
            assert call == asked.call_id
            await loom.drain()
            result = await loom.result(first.run_id, session_id=SESSION)
            events = await loom.export_session(SESSION)
        assert result.status is RunStatus.COMPLETED and result.pending_questions == ()
        answered = next(e.payload for e in events if isinstance(e.payload, QuestionAnswered))
        assert (answered.answers, answered.by) == ((("Dupont",),), "denis")

    asyncio.run(go())


def test_a_message_in_the_session_is_the_answer(tmp_path: Path) -> None:
    """L'utilisateur répond en écrivant : le run qui a posé la question reprend (D-3)."""

    async def go() -> None:
        async with mounted(config(tmp_path)) as (loom, _):
            first = await loom.run("demo", "Prépare un devis.", session_id=SESSION)
            again = await loom.run("demo", "Dupont, celui de Lyon", session_id=SESSION)
            events = await loom.export_session(SESSION)
        assert again.run_id == first.run_id and again.status is RunStatus.COMPLETED
        answered = next(e.payload for e in events if isinstance(e.payload, QuestionAnswered))
        assert answered.text == "Dupont, celui de Lyon"
        # Aucun nouveau run : la réponse appartient au run qui a posé la question.
        assert types(events).count("run.started") == 1

    asyncio.run(go())


def test_a_streamed_message_is_the_answer_too(tmp_path: Path) -> None:
    async def go() -> None:
        async with mounted(config(tmp_path)) as (loom, _):
            first = await loom.run("demo", "Prépare un devis.", session_id=SESSION)
            seen = [
                item.type
                async for item in loom.stream("demo", "Dupont", session_id=SESSION)
                if isinstance(item, Event)
            ]
            result = await loom.result(first.run_id, session_id=SESSION)
        assert "question.answered" in seen and seen[-1] == "run.completed"
        assert result.status is RunStatus.COMPLETED

    asyncio.run(go())


def test_bad_answers_are_refused_and_nothing_is_written(tmp_path: Path) -> None:
    async def go() -> None:
        async with mounted(config(tmp_path)) as (loom, _):
            first = await loom.run("demo", "Prépare un devis.", session_id=SESSION)
            before = len(await loom.export_session(SESSION))
            with pytest.raises(InvalidAnswer, match="2 réponse"):
                await loom.answer(first.run_id, answers=[["a"], ["b"]], session_id=SESSION)
            with pytest.raises(InvalidAnswer, match="un seul choix"):
                await loom.answer(first.run_id, answers=[["Dupont", "Martin"]], session_id=SESSION)
            with pytest.raises(InvalidAnswer, match="soit 'answers'"):
                await loom.answer(first.run_id, session_id=SESSION)
            with pytest.raises(UnknownQuestion):
                await loom.answer(first.run_id, text="x", call_id="autre", session_id=SESSION)
            assert len(await loom.export_session(SESSION)) == before

    asyncio.run(go())


def test_an_inline_answerer_answers_without_pausing(tmp_path: Path) -> None:
    async def go() -> None:
        seen: list[PendingQuestion] = []

        async def answerer(question: PendingQuestion) -> QuestionReply | NoAnswer:
            seen.append(question)
            return QuestionReply(answers=(("Martin",),), by="script")

        async with mounted(config(tmp_path)) as (loom, _):
            result = await loom.run(
                "demo", "Prépare un devis.", session_id=SESSION, answerer=answerer
            )
            events = await loom.export_session(SESSION)
        assert result.status is RunStatus.COMPLETED and len(seen) == 1
        assert "paused" not in [e.facets.get("to_state") for e in events]

    asyncio.run(go())


def test_an_approver_does_not_answer(tmp_path: Path) -> None:
    """Deux attentes distinctes : un approbateur en ligne ne répond pas aux questions."""

    async def go() -> None:
        async def approver(_: PendingApproval) -> Approved:
            return Approved(by="script")

        async with mounted(config(tmp_path)) as (loom, _):
            result = await loom.run(
                "demo", "Prépare un devis.", session_id=SESSION, approver=approver
            )
        assert result.status is RunStatus.PAUSED and len(result.pending_questions) == 1

    asyncio.run(go())


def test_the_expiry_wakes_the_run_up(tmp_path: Path) -> None:
    async def go() -> None:
        async with mounted(config(tmp_path, ask={"expires_in": 0.2})) as (loom, _):
            first = await loom.run("demo", "Prépare un devis.", session_id=SESSION)
            # Le réveil différé des approbations ramène aussi le run d'une question.
            await asyncio.sleep(0.3)
            await loom.drain()
            result = await loom.result(first.run_id, session_id=SESSION)
            with pytest.raises(UnknownQuestion):
                await loom.answer(first.run_id, text="Dupont", session_id=SESSION)
        assert result.status is RunStatus.COMPLETED

    asyncio.run(go())


def test_a_stale_question_takes_no_answer(tmp_path: Path) -> None:
    """Sans réveil (instance arrêtée), l'échéance du journal fait foi."""

    async def go() -> None:
        path = config(tmp_path, ask={"expires_in": 0.2})
        async with mounted(path) as (loom, _):
            first = await loom.run("demo", "Prépare un devis.", session_id=SESSION)
        await asyncio.sleep(0.3)
        async with mounted(path) as (loom, _):
            with pytest.raises(QuestionStale):
                await loom.answer(first.run_id, text="Dupont", session_id=SESSION)
            result = await loom.resume(first.run_id, session_id=SESSION)
        assert result.status is RunStatus.COMPLETED

    asyncio.run(go())


def test_a_subagent_question_is_answered_on_the_root(tmp_path: Path) -> None:
    parent = [
        {"text": "", "tool_calls": [{"name": "aide", "arguments": {"message": "Le devis."}}]},
        {"text": "C'est fait."},
    ]

    async def go() -> None:
        async with mounted(config(tmp_path, parent, child=SCRIPT)) as (loom, _):
            first = await loom.run("demo", "Prépare un devis.", session_id=SESSION)
            [asked] = first.pending_questions
            assert asked.run_id != first.run_id
            again = await loom.run("demo", "Dupont", session_id=SESSION)
        assert again.run_id == first.run_id and again.status is RunStatus.COMPLETED

    asyncio.run(go())


def test_a_chosen_run_id_is_a_new_turn_and_the_question_still_waits(tmp_path: Path) -> None:
    """Un ``run_id`` choisi dit « un nouveau run » : pas de routage (D-4)."""

    async def go() -> None:
        async with mounted(config(tmp_path)) as (loom, _):
            first = await loom.run("demo", "Prépare un devis.", session_id=SESSION)
            again = await loom.run(
                "demo", "Autre chose", session_id=SESSION, run_id=RunId("tour-2")
            )
            info = await loom.session(SESSION)
        assert again.run_id == "tour-2"
        assert first.pending_questions[0].call_id in [q.call_id for q in info.pending_questions]

    asyncio.run(go())


def test_two_simultaneous_answers_keep_the_journal_readable(tmp_path: Path) -> None:
    async def go() -> None:
        async with mounted(config(tmp_path)) as (loom, _):
            first = await loom.run("demo", "Prépare un devis.", session_id=SESSION)
            results = await asyncio.gather(
                loom.answer(first.run_id, text="Dupont", session_id=SESSION),
                loom.answer(first.run_id, text="Martin", session_id=SESSION),
                return_exceptions=True,
            )
            await loom.drain()
            result = await loom.result(first.run_id, session_id=SESSION)
        assert sum(isinstance(r, UnknownQuestion) for r in results) == 1
        assert result.status is RunStatus.COMPLETED

    asyncio.run(go())


def test_without_read_content_the_question_is_masked(tmp_path: Path) -> None:
    async def go() -> None:
        async with mounted(config(tmp_path)) as (loom, _):
            first = await loom.run("demo", "Prépare un devis.", session_id=SESSION)
        masked = first.masked()
        assert masked.pending_questions[0].questions == ()
        assert masked.pending_questions[0].call_id == first.pending_questions[0].call_id
        assert "pending_questions[].questions" in masked.redacted

    asyncio.run(go())


def test_an_answered_conversation_replays_identically(tmp_path: Path) -> None:
    async def go() -> None:
        path = config(tmp_path)
        async with mounted(path) as (loom, _):
            await loom.run("demo", "Prépare un devis.", session_id=SESSION)
            await loom.run("demo", "Dupont", session_id=SESSION)
        await assert_replays(path, tmp_path / "data" / "default" / "s1.jsonl")

    asyncio.run(go())


def test_an_answer_is_filed_where_its_question_waited(tmp_path: Path) -> None:
    """Comme une décision : la réponse se range sur le span de la question, pas à la racine."""

    async def go() -> None:
        async with mounted(config(tmp_path)) as (loom, _):
            first = await loom.run("demo", "Prépare un devis.", session_id=SESSION)
            await loom.answer(first.run_id, text="Dupont", session_id=SESSION)
            events = await loom.export_session(SESSION)
        asked = next(e for e in events if e.type == "question.asked")
        answered = next(e for e in events if e.type == "question.answered")
        assert (answered.span_id, answered.parent_span_id) == (asked.span_id, asked.parent_span_id)

    asyncio.run(go())
