# SPDX-License-Identifier: Apache-2.0
"""Accès MCP : les agents publiés en outils, client et serveur en process."""

from collections.abc import AsyncGenerator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

import pytest
from conftest import ANSWER, QUESTION, ConfigFactory, demo_agent

from loom_ia.access import Loom
from loom_ia.core.events import Event
from loom_ia.core.model import SessionId

pytest.importorskip("mcp", reason="extra 'mcp' absent")

from mcp import ClientSession
from mcp.client.session import ElicitationFnT
from mcp.shared.context import RequestContext
from mcp.shared.memory import create_connected_server_and_client_session as connected
from mcp.types import (
    ContentBlock,
    ElicitRequestParams,
    ElicitResult,
    Implementation,
    TextContent,
)

from loom_ia.access.mcp_server import REPORT_TOOL, STATUS_TOOL, create_server

# Le rappel qu'un client branche pour répondre aux formulaires du serveur.
type Elicitation = ElicitationFnT

CLIENT = Implementation(name="atelier-client", version="1.0")

type ElicitAction = Literal["accept", "decline", "cancel"]


@asynccontextmanager
async def serving(
    path: Path, *, elicitation: Elicitation | None = None
) -> AsyncGenerator[tuple[Loom, ClientSession]]:
    """Une instance servie en MCP, et un client branché dessus.

    Avec ``elicitation``, le client déclare la capacité : c'est ce que le
    serveur regarde pour faire trancher une approbation dans la boucle.
    """
    async with Loom.from_config(path) as loom:
        async with connected(
            create_server(loom), client_info=CLIENT, elicitation_callback=elicitation
        ) as client:
            yield loom, client


async def test_each_published_agent_is_a_tool(demo: ConfigFactory) -> None:
    path = demo(agents=[demo_agent(), demo_agent(name="cache", expose={"mcp": False})])
    async with serving(path) as (_, client):
        listed = await client.list_tools()

    tools = {tool.name: tool for tool in listed.tools}
    assert set(tools) == {"demo", STATUS_TOOL, REPORT_TOOL}
    assert tools["demo"].description == "Répond aux questions de calcul."
    assert tools["demo"].inputSchema["required"] == ["message"]
    assert "run_id" in (tools["demo"].outputSchema or {})["properties"]


async def test_calling_an_agent_runs_it(demo: ConfigFactory) -> None:
    async with serving(demo()) as (loom, client):
        result = await client.call_tool("demo", {"message": QUESTION})
        assert result.structuredContent is not None
        events = await loom.events(result.structuredContent["run_id"])

    assert not result.isError
    [content] = result.content
    assert isinstance(content, TextContent) and content.text == ANSWER
    assert result.structuredContent["status"] == "completed"
    assert [event.type for event in events][-1] == "run.completed"


async def test_run_status_reads_a_finished_run(demo: ConfigFactory) -> None:
    async with serving(demo()) as (_, client):
        first = await client.call_tool("demo", {"message": QUESTION})
        assert first.structuredContent is not None
        run_id = first.structuredContent["run_id"]
        again = await client.call_tool(STATUS_TOOL, {"run_id": run_id})

    assert again.structuredContent == first.structuredContent


async def test_a_session_gathers_two_calls(demo: ConfigFactory) -> None:
    session = SessionId("atelier")
    async with serving(demo()) as (loom, client):
        first = await client.call_tool("demo", {"message": QUESTION, "session_id": session})
        second = await client.call_tool("demo", {"message": "Et encore ?", "session_id": session})
        assert first.structuredContent is not None and second.structuredContent is not None
        events = await loom.events(second.structuredContent["run_id"], session_id=session)

    assert first.structuredContent["session_id"] == "atelier"
    assert events[0].seq > 1


async def test_unknown_agent_and_unknown_run_are_errors(demo: ConfigFactory) -> None:
    path = demo(agents=[demo_agent(), demo_agent(name="cache", expose={"mcp": False})])
    async with serving(path) as (_, client):
        absent = await client.call_tool("absent", {"message": QUESTION})
        hidden = await client.call_tool("cache", {"message": QUESTION})
        unknown = await client.call_tool(STATUS_TOOL, {"run_id": "run-absent"})
        missing = await client.call_tool("demo", {})

    assert absent.isError and hidden.isError and unknown.isError
    assert missing.isError and "message" in _text(missing.content)


async def test_the_journal_is_the_same_as_by_the_python_access(demo: ConfigFactory) -> None:
    path = demo()
    async with serving(path) as (loom, client):
        result = await client.call_tool("demo", {"message": QUESTION})
        assert result.structuredContent is not None
        by_mcp = await loom.events(result.structuredContent["run_id"])

    async with Loom.from_config(path) as loom:
        direct = await loom.run("demo", QUESTION)
        by_python = await loom.events(direct.run_id)

    assert [event.type for event in by_mcp] == [event.type for event in by_python]


# --- Approbations : elicitation, ou pause (J4.5) ------------------------------


def _answers(decision: str, *, action: ElicitAction = "accept", motif: str = "") -> Elicitation:
    """Client qui répond au formulaire comme le ferait un humain."""

    async def callback(
        context: RequestContext[ClientSession, Any], params: ElicitRequestParams
    ) -> ElicitResult:
        assert "envoyer_email" in params.message
        if action != "accept":
            return ElicitResult(action=action)
        return ElicitResult(action="accept", content={"decision": decision, "motif": motif})

    return callback


async def test_an_approval_is_elicited_when_the_client_can(atelier: ConfigFactory) -> None:
    async with serving(atelier(), elicitation=_answers("accorder", motif="devis vérifié")) as (
        loom,
        client,
    ):
        result = await client.call_tool("demo", {"message": "Relance."})
        assert result.structuredContent is not None
        events = await loom.events(result.structuredContent["run_id"])

    assert result.structuredContent["status"] == "completed"
    assert result.structuredContent["pending_approvals"] == []
    types = [event.type for event in events]
    # Le run n'est jamais passé par la pause : la décision est venue dans la boucle.
    assert "approval.requested" in types and "approval.granted" in types
    assert "paused" not in [event.facets.get("to_state") for event in events]
    assert _by(events, "approval.granted") == ["mcp:atelier-client"]
    assert _reason(events, "approval.granted") == "devis vérifié"


async def test_an_elicited_refusal_never_calls_the_tool(atelier: ConfigFactory) -> None:
    async with serving(atelier(), elicitation=_answers("refuser", motif="mauvais montant")) as (
        loom,
        client,
    ):
        result = await client.call_tool("demo", {"message": "Relance."})
        assert result.structuredContent is not None
        events = await loom.events(result.structuredContent["run_id"])

    assert result.structuredContent["status"] == "completed"
    assert _by(events, "approval.rejected") == ["mcp:atelier-client"]
    assert "tool.called" not in [event.type for event in events]


async def test_a_dismissed_form_does_not_authorise_the_effect(atelier: ConfigFactory) -> None:
    async with serving(atelier(), elicitation=_answers("accorder", action="cancel")) as (
        loom,
        client,
    ):
        result = await client.call_tool("demo", {"message": "Relance."})
        assert result.structuredContent is not None
        events = await loom.events(result.structuredContent["run_id"])

    # Fermer le formulaire n'est pas refuser, mais l'effet n'a pas été autorisé.
    assert _reason(events, "approval.rejected") == "demande fermée sans réponse"
    assert "tool.called" not in [event.type for event in events]


async def test_without_elicitation_the_tool_hands_back_a_paused_run(
    atelier: ConfigFactory,
) -> None:
    async with serving(atelier()) as (loom, client):
        paused = await client.call_tool("demo", {"message": "Relance."})
        assert paused.structuredContent is not None
        run_id = paused.structuredContent["run_id"]
        relu = await client.call_tool(STATUS_TOOL, {"run_id": run_id})

        await loom.approve(run_id, by="denis")
        await loom.drain()
        fini = await client.call_tool(STATUS_TOOL, {"run_id": run_id})
        assert fini.structuredContent is not None

    # Un run qui attend un humain n'est pas un échec : l'outil rend la main.
    assert not paused.isError
    assert paused.structuredContent["status"] == "paused"
    assert [a["tool_name"] for a in paused.structuredContent["pending_approvals"]] == [
        "envoyer_email"
    ]
    dit = _text(paused.content)
    assert "en attente d'approbation" in dit and run_id in dit and STATUS_TOOL in dit
    assert relu.structuredContent == paused.structuredContent
    assert fini.structuredContent["status"] == "completed"


def _by(events: Sequence[Event], type_: str) -> list[str]:
    return [str(event.facets.get("by")) for event in events if event.type == type_]


def _reason(events: Sequence[Event], type_: str) -> str:
    [payload] = [event.payload for event in events if event.type == type_]
    return str(getattr(payload, "reason", ""))


def _text(content: Sequence[ContentBlock]) -> str:
    return " ".join(part.text for part in content if isinstance(part, TextContent))
