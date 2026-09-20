# SPDX-License-Identifier: Apache-2.0
"""Accès MCP : les agents publiés en outils, client et serveur en process."""

from collections.abc import AsyncGenerator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from conftest import ANSWER, QUESTION, ConfigFactory, demo_agent

from loom_ia.access import Loom
from loom_ia.core.model import SessionId

pytest.importorskip("mcp", reason="extra 'mcp' absent")

from mcp import ClientSession
from mcp.shared.memory import create_connected_server_and_client_session as connected
from mcp.types import ContentBlock, TextContent

from loom_ia.access.mcp_server import REPORT_TOOL, STATUS_TOOL, create_server


@asynccontextmanager
async def serving(path: Path) -> AsyncGenerator[tuple[Loom, ClientSession]]:
    """Une instance servie en MCP, et un client branché dessus."""
    async with Loom.from_config(path) as loom:
        async with connected(create_server(loom)) as client:
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


def _text(content: Sequence[ContentBlock]) -> str:
    return " ".join(part.text for part in content if isinstance(part, TextContent))
