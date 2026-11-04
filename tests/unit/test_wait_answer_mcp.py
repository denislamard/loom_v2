# SPDX-License-Identifier: Apache-2.0
"""``loom.ask`` par le MCP : elicitation, ou question rendue en texte (wait_answer, lot 4)."""

import asyncio
from pathlib import Path
from typing import Any

import pytest
from conftest import DEV
from test_wait_answer import config

from loom_ia.access import Loom
from loom_ia.core.model import SessionId

pytest.importorskip("mcp", reason="extra 'mcp' absent")

from mcp import ClientSession
from mcp.shared.context import RequestContext
from mcp.shared.memory import create_connected_server_and_client_session as connected
from mcp.types import ElicitRequestParams, ElicitResult, Implementation, TextContent

from loom_ia.access.mcp_server import create_server

CLIENT = Implementation(name="atelier-client", version="1.0")
MULTIPLE: dict[str, Any] = {
    "questions": [
        {"question": "Quel client ?", "options": [{"label": "Dupont"}, {"label": "Martin"}]},
        {
            "question": "Quelles options ?",
            "options": [{"label": "Pose"}, {"label": "Dépose"}],
            "multiple": True,
        },
    ]
}


def _texts(content: list[Any]) -> str:
    return "\n".join(block.text for block in content if isinstance(block, TextContent))


def test_without_elicitation_the_question_comes_back_as_text(tmp_path: Path) -> None:
    async def go() -> None:
        async with Loom.from_config(config(tmp_path)) as loom:
            async with connected(create_server(loom, user=DEV), client_info=CLIENT) as client:
                paused = await client.call_tool(
                    "demo", {"message": "Prépare un devis.", "session_id": "s1"}
                )
                assert not paused.isError
                said = _texts(paused.content)
                assert (
                    "Pour quel client faut-il préparer le devis ? (choix : Dupont, Martin)" in said
                )
                assert "rappeler demo avec session_id=s1" in said
                assert paused.structuredContent is not None
                assert paused.structuredContent["status"] == "paused"
                [asked] = paused.structuredContent["pending_questions"]
                assert asked["questions"][0]["header"] == "Client"
                # Le client répond comme l'utilisateur l'a dit : un message dans la session.
                again = await client.call_tool("demo", {"message": "Dupont", "session_id": "s1"})
                assert again.structuredContent is not None
                assert again.structuredContent["run_id"] == paused.structuredContent["run_id"]
                assert again.structuredContent["status"] == "completed"

    asyncio.run(go())


def test_with_progress_the_answered_run_is_the_one_followed(tmp_path: Path) -> None:
    """Avec ``progressToken``, le ``run_id`` se lit sur le flux : c'est celui de la question."""
    lines: list[str] = []

    async def progress(progress: float, total: float | None, message: str | None) -> None:
        lines.append(message or "")

    async def go() -> None:
        async with Loom.from_config(config(tmp_path)) as loom:
            async with connected(create_server(loom, user=DEV), client_info=CLIENT) as client:
                paused = await client.call_tool(
                    "demo",
                    {"message": "Prépare un devis.", "session_id": "s1"},
                    progress_callback=progress,
                )
                again = await client.call_tool(
                    "demo", {"message": "Dupont", "session_id": "s1"}, progress_callback=progress
                )
        assert paused.structuredContent is not None and again.structuredContent is not None
        assert again.structuredContent["run_id"] == paused.structuredContent["run_id"]
        assert again.structuredContent["status"] == "completed"
        assert any("question à l'utilisateur" in line for line in lines)
        assert any(f"réponse de {DEV} reçue" in line for line in lines)

    asyncio.run(go())


def test_elicitation_answers_in_the_loop(tmp_path: Path) -> None:
    forms: list[tuple[str, dict[str, Any]]] = []

    async def elicit(
        context: RequestContext[ClientSession, Any], params: ElicitRequestParams
    ) -> ElicitResult:
        forms.append((params.message, params.requestedSchema))  # type: ignore[union-attr]
        return ElicitResult(action="accept", content={"choix_1": "Martin"})

    async def go() -> None:
        async with Loom.from_config(config(tmp_path)) as loom:
            async with connected(
                create_server(loom, user=DEV), client_info=CLIENT, elicitation_callback=elicit
            ) as client:
                result = await client.call_tool(
                    "demo", {"message": "Prépare un devis.", "session_id": "s1"}
                )
                events = await loom.export_session(SessionId("s1"))
        assert result.structuredContent is not None
        assert result.structuredContent["status"] == "completed"
        answered = [e for e in events if e.type == "question.answered"]
        # L'élicitation répond pour le lanceur : c'est lui qui signe (§ 6.2).
        assert answered[0].facets.get("by") == DEV
        assert "paused" not in [e.facets.get("to_state") for e in events]

    asyncio.run(go())
    [(message, form)] = forms
    assert message == "Pour quel client faut-il préparer le devis ?"
    assert form["properties"]["choix_1"]["enum"] == ["Dupont", "Martin"]
    assert form["properties"]["choix_1"]["title"] == "Client"
    # Le champ libre a son propre titre à côté de la liste (lot 4).
    assert form["properties"]["reponse_1"]["title"] == "Client : autre réponse"


def test_a_free_text_wins_and_multiple_choices_are_split(tmp_path: Path) -> None:
    script = [
        {"text": "", "tool_calls": [{"name": "ask", "arguments": MULTIPLE}]},
        {"text": "Noté."},
    ]
    forms: list[dict[str, Any]] = []

    async def elicit(
        context: RequestContext[ClientSession, Any], params: ElicitRequestParams
    ) -> ElicitResult:
        forms.append(params.requestedSchema)  # type: ignore[union-attr]
        content: dict[str, str | int | float | bool | list[str] | None] = {
            "choix_1": "Dupont",
            "reponse_1": "Durand",
            "reponse_2": "Pose ; Dépose",
        }
        return ElicitResult(action="accept", content=content)

    async def go() -> None:
        async with Loom.from_config(config(tmp_path, script)) as loom:
            async with connected(
                create_server(loom, user=DEV), client_info=CLIENT, elicitation_callback=elicit
            ) as client:
                await client.call_tool("demo", {"message": "Devis.", "session_id": "s1"})
                events = await loom.export_session(SessionId("s1"))
        [answered] = [e.payload for e in events if e.type == "question.answered"]
        assert answered.answers == (("Durand",), ("Pose", "Dépose"))  # type: ignore[union-attr]

    asyncio.run(go())
    [form] = forms
    # Plusieurs choix : pas de liste (un formulaire MCP n'a pas de tableau), un texte.
    assert "choix_2" not in form["properties"]
    assert "Pose, Dépose (séparés par ;)" in form["properties"]["reponse_2"]["description"]
    # Sans liste, le champ libre est le seul de sa question : il garde l'intitulé.
    assert form["properties"]["reponse_2"]["title"] == "Question 2"


def test_a_dismissed_form_leaves_the_question_waiting(tmp_path: Path) -> None:
    async def elicit(
        context: RequestContext[ClientSession, Any], params: ElicitRequestParams
    ) -> ElicitResult:
        return ElicitResult(action="cancel")

    async def go() -> None:
        async with Loom.from_config(config(tmp_path)) as loom:
            async with connected(
                create_server(loom, user=DEV), client_info=CLIENT, elicitation_callback=elicit
            ) as client:
                result = await client.call_tool(
                    "demo", {"message": "Prépare un devis.", "session_id": "s1"}
                )
        assert result.structuredContent is not None
        assert result.structuredContent["status"] == "paused"
        assert len(result.structuredContent["pending_questions"]) == 1

    asyncio.run(go())


def test_run_status_shows_the_question(tmp_path: Path) -> None:
    async def go() -> None:
        async with Loom.from_config(config(tmp_path)) as loom:
            async with connected(create_server(loom, user=DEV), client_info=CLIENT) as client:
                paused = await client.call_tool(
                    "demo", {"message": "Prépare un devis.", "session_id": "s1"}
                )
                assert paused.structuredContent is not None
                status = await client.call_tool(
                    "run_status", {"run_id": paused.structuredContent["run_id"], "session_id": "s1"}
                )
        assert status.structuredContent is not None
        assert len(status.structuredContent["pending_questions"]) == 1
        assert "Pour quel client" in _texts(status.content)

    asyncio.run(go())


TWO: dict[str, Any] = {
    "questions": [
        {
            "question": "Les relances ont-elles été faites ?",
            "header": "Relances",
            "options": [{"label": "Oui, par e-mail"}, {"label": "Non"}],
        },
        {
            "question": "Quelles pièces changer ?",
            "header": "Pièces",
            "options": [{"label": "Joint"}, {"label": "Siphon"}],
            "multiple": True,
        },
    ]
}


def test_without_elicitation_several_questions_are_numbered(tmp_path: Path) -> None:
    # Essai réel t-2000 (02/11) : deux questions ; le client doit savoir les distinguer
    # et répondre aux deux en un seul message (docs/multi-questions.md, § 2.8).
    script = [{"text": "", "tool_calls": [{"name": "ask", "arguments": TWO}]}, {"text": "Noté."}]
    lines: list[str] = []

    async def progress(progress: float, total: float | None, message: str | None) -> None:
        lines.append(message or "")

    async def go() -> str:
        async with Loom.from_config(config(tmp_path, script)) as loom:
            async with connected(create_server(loom, user=DEV), client_info=CLIENT) as client:
                paused = await client.call_tool(
                    "demo",
                    {"message": "La lettre.", "session_id": "s1"},
                    progress_callback=progress,
                )
                return _texts(paused.content)

    said = asyncio.run(go())
    assert (
        "Question 1/2 · Relances : Les relances ont-elles été faites ? "
        "(choix : Oui, par e-mail ; Non)" in said
    )
    assert (
        "Question 2/2 · Pièces : Quelles pièces changer ? "
        "(choix, plusieurs possibles : Joint ; Siphon)" in said
    )
    assert "réponse de l'utilisateur aux 2 questions : rappeler demo avec session_id=s1" in said
    assert "les réponses en un seul message" in said
    assert any("questions à l'utilisateur (2) : Relances ; Pièces" in line for line in lines)


def test_without_elicitation_an_approval_does_not_hide_the_question(tmp_path: Path) -> None:
    # Avant, un run qui attendait les deux ne montrait que l'approbation (§ 6.1).
    question = {
        "questions": [
            {"question": "Pour quel client ?", "options": [{"label": "Dupont"}]},
        ]
    }
    script = [
        {
            "text": "",
            "tool_calls": [
                {"name": "ask", "arguments": question},
                {"name": "envoyer", "arguments": {"destinataire": "a@example.com"}},
            ],
        },
        {"text": "Fini."},
    ]

    async def go() -> str:
        async with Loom.from_config(config(tmp_path, script)) as loom:
            async with connected(create_server(loom, user=DEV), client_info=CLIENT) as client:
                paused = await client.call_tool("demo", {"message": "Envoie.", "session_id": "s1"})
                assert paused.structuredContent is not None
                assert len(paused.structuredContent["pending_approvals"]) == 1
                assert len(paused.structuredContent["pending_questions"]) == 1
                return _texts(paused.content)

    said = asyncio.run(go())
    approval = said.index("en attente d'approbation : envoyer")
    asked = said.index("Pour quel client ? (choix : Dupont)")
    assert approval < asked
    assert "rappeler demo avec session_id=s1" in said
