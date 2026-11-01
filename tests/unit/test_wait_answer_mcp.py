# SPDX-License-Identifier: Apache-2.0
"""``loom.ask`` par le MCP : elicitation, ou question rendue en texte (wait_answer, lot 4)."""

import asyncio
from pathlib import Path
from typing import Any

import pytest
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
            async with connected(create_server(loom), client_info=CLIENT) as client:
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
            async with connected(create_server(loom), client_info=CLIENT) as client:
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
        assert any("réponse de l'utilisateur reçue" in line for line in lines)

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
                create_server(loom), client_info=CLIENT, elicitation_callback=elicit
            ) as client:
                result = await client.call_tool(
                    "demo", {"message": "Prépare un devis.", "session_id": "s1"}
                )
                events = await loom.export_session(SessionId("s1"))
        assert result.structuredContent is not None
        assert result.structuredContent["status"] == "completed"
        answered = [e for e in events if e.type == "question.answered"]
        assert answered[0].facets.get("by") == "mcp:atelier-client"
        assert "paused" not in [e.facets.get("to_state") for e in events]

    asyncio.run(go())
    [(message, form)] = forms
    assert message == "Pour quel client faut-il préparer le devis ?"
    assert form["properties"]["choix_1"]["enum"] == ["Dupont", "Martin"]
    assert form["properties"]["choix_1"]["title"] == "Client"
    assert "reponse_1" in form["properties"]


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
                create_server(loom), client_info=CLIENT, elicitation_callback=elicit
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


def test_a_dismissed_form_leaves_the_question_waiting(tmp_path: Path) -> None:
    async def elicit(
        context: RequestContext[ClientSession, Any], params: ElicitRequestParams
    ) -> ElicitResult:
        return ElicitResult(action="cancel")

    async def go() -> None:
        async with Loom.from_config(config(tmp_path)) as loom:
            async with connected(
                create_server(loom), client_info=CLIENT, elicitation_callback=elicit
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
            async with connected(create_server(loom), client_info=CLIENT) as client:
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
