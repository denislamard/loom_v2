# SPDX-License-Identifier: Apache-2.0
"""Arbre des sous-runs par les trois accès, et déroulé en direct (J2.5).

Le flux d'un run montre aussi ses sous-runs : API Python (``stream``,
``follow``, ``events``), SSE de l'API REST, notifications de progression du
serveur MCP, direct de la CLI.
"""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from conftest import TREE_ANSWER, TREE_QUESTION, ConfigFactory

from loom_ia.access import Loom
from loom_ia.access.cli import main
from loom_ia.access.progress import Progress
from loom_ia.adapters.stores import InMemoryEventStore, NotifyingEventStore
from loom_ia.core.events import (
    DurablePayload,
    Event,
    RunFailed,
    RunScope,
    RunStarted,
    UserMessage,
)
from loom_ia.core.model import DEFAULT_TENANT, Message, RunId, SessionId, new_run_id
from loom_ia.core.projections import RunTree

# Suite des événements de l'arbre : la racine, puis l'enfant au milieu de l'appel.
TREE_TYPES = [
    "run.started",
    "message.user",
    "step.started",
    "model.responded",
    "step.completed",
    "run.transitioned",
    "step.started",
    "tool.called",
    # L'enfant, écrit dans le même journal.
    "run.started",
    "message.user",
    "step.started",
    "model.responded",
    "step.completed",
    "run.transitioned",
    "step.started",
    "tool.called",
    "tool.completed",
    "step.completed",
    "run.transitioned",
    "step.started",
    "model.responded",
    "step.completed",
    "run.transitioned",
    "run.completed",
    # Retour dans la racine.
    "tool.completed",
    "step.completed",
    "run.transitioned",
    "step.started",
    "model.responded",
    "step.completed",
    "run.transitioned",
    "run.completed",
]

# Déroulé attendu, tel que la CLI et le serveur MCP le décrivent.
PROGRESS = [
    "· verifier(message='Vérifie 2 + 2.')",
    "  · sous-agent verificateur : démarré",
    "  · calculer(expr='2+2')",
    "  · calculer : fait",
    "  · sous-agent verificateur : terminé",
    "· verifier : fait",
]


# --- Arbre et abonnement ---------------------------------------------------------------


def scope(run: str) -> RunScope:
    return RunScope(
        tenant_id=DEFAULT_TENANT,
        session_id=SessionId("s1"),
        run_id=RunId(run),
        root_run_id=RunId("r0"),
        agent="demo",
    )


def journal(*items: tuple[str, DurablePayload]) -> list[Event]:
    drafts = [scope(run).draft(payload) for run, payload in items]
    return [draft.to_event(seq) for seq, draft in enumerate(drafts, start=1)]


def test_a_tree_grows_with_the_runs_it_starts() -> None:
    asked = UserMessage(message=Message.user("?"))
    events = journal(
        ("r0", RunStarted()),
        ("r1", RunStarted()),
        ("r2", RunStarted(parent_run_id=RunId("r0"), depth=1)),
        ("r2", asked),
        ("r3", RunStarted(parent_run_id=RunId("r2"), depth=2)),
        ("r1", asked),
    )
    tree = RunTree(RunId("r0"))
    assert [e.run_id for e in tree.select(events)] == ["r0", "r2", "r2", "r3"]
    assert tree.runs == {"r0", "r2", "r3"}
    alone = RunTree(RunId("r0"), subruns=False)
    assert [e.run_id for e in alone.select(events)] == ["r0"]


async def test_a_listener_can_filter_what_it_receives() -> None:
    store = NotifyingEventStore(InMemoryEventStore())
    seen: list[str] = []
    drafts = [
        scope("r0").draft(RunStarted()),
        scope("r0").draft(UserMessage(message=Message.user("?"))),
    ]
    with store.listen(lambda e: seen.append(e.type), accept=lambda e: e.type == "run.started"):
        await store.append(drafts, expected_seq=0)
    assert seen == ["run.started"]


def test_progress_lines_of_a_failed_subrun() -> None:
    progress = Progress()
    events = journal(
        ("r0", RunStarted()),
        ("r2", RunStarted(parent_run_id=RunId("r0"), depth=1)),
        ("r2", RunFailed(error_type="model.timeout", error="délai dépassé")),
        ("r0", RunFailed(error_type="model.timeout", error="délai dépassé")),
    )
    assert [progress.line(e) for e in events] == [
        None,
        "  · sous-agent demo : démarré",
        "  · sous-agent demo : échec — délai dépassé",
        None,
    ]


# --- API Python --------------------------------------------------------------------------


async def test_stream_shows_the_subruns(tree: ConfigFactory) -> None:
    async with Loom.from_config(tree()) as loom:
        items = [
            item async for item in loom.stream("demo", TREE_QUESTION) if isinstance(item, Event)
        ]
        alone = [
            item
            async for item in loom.stream("demo", TREE_QUESTION, subruns=False)
            if isinstance(item, Event)
        ]

    assert [e.type for e in items] == TREE_TYPES
    assert [e.seq for e in items] == sorted(e.seq for e in items)
    root = items[0].run_id
    assert len({e.run_id for e in items}) == 2 and {e.root_run_id for e in items} == {root}
    assert {e.run_id for e in alone} == {alone[0].run_id}
    assert [e.type for e in alone][-1] == "run.completed"


async def test_follow_and_events_give_the_tree(tree: ConfigFactory) -> None:
    async with Loom.from_config(tree()) as loom:
        result = await loom.run("demo", TREE_QUESTION)
        followed = [e async for e in loom.follow(result.run_id)]
        listed = await loom.events(result.run_id)
        child = next(e.run_id for e in listed if e.run_id != result.run_id)
        # Un sous-run se suit aussi, dans le journal de sa session.
        own = [e async for e in loom.follow(child, session_id=result.session_id)]
        tail = await loom.events(result.run_id, after_seq=listed[-3].seq)
        # Une reprise après la clôture rend la main tout de suite.
        async with asyncio.timeout(2):
            after = [e async for e in loom.follow(result.run_id, after_seq=listed[-1].seq)]

    assert result.text == TREE_ANSWER
    assert followed == listed and [e.type for e in listed] == TREE_TYPES
    assert {e.run_id for e in own} == {child} and own[-1].type == "run.completed"
    assert tail == listed[-2:] and after == []


async def test_follow_stops_on_its_own_run_while_it_is_running(tree: ConfigFactory) -> None:
    run_id = new_run_id()
    async with Loom.from_config(tree()) as loom:
        running = asyncio.Event()
        with loom.store.listen(lambda _: running.set(), run_id):
            task = asyncio.create_task(loom.run("demo", TREE_QUESTION, run_id=run_id))
            async with asyncio.timeout(5):
                await running.wait()
                followed = [e async for e in loom.follow(run_id)]
        await task

    # Le flux ne s'arrête pas sur la clôture de l'enfant.
    assert [e.type for e in followed] == TREE_TYPES


# --- API REST ----------------------------------------------------------------------------


async def test_sse_shows_the_subruns(tree: ConfigFactory) -> None:
    pytest.importorskip("fastapi", reason="extra 'http' absent")
    httpx2 = pytest.importorskip("httpx2", reason="client HTTP de test absent")
    from loom_ia.access.http import create_app

    async with Loom.from_config(tree()) as loom:
        transport = httpx2.ASGITransport(create_app(loom))
        async with httpx2.AsyncClient(transport=transport, base_url="http://loom.test") as http:
            started = await http.post("/v1/agents/demo/runs", json={"message": TREE_QUESTION})
            run_id = started.json()["run_id"]
            names: dict[str, list[str]] = {}
            for query in ("", "?subruns=false"):
                async with http.stream("GET", f"/v1/runs/{run_id}/events{query}") as response:
                    lines = [line async for line in response.aiter_lines()]
                names[query] = [line[6:].strip() for line in lines if line.startswith("event:")]

    assert started.status_code == 201 and started.json()["text"] == TREE_ANSWER
    assert names[""] == TREE_TYPES
    assert names["?subruns=false"].count("run.started") == 1


# --- Serveur MCP -------------------------------------------------------------------------


@asynccontextmanager
async def mcp_client(path: Path) -> AsyncGenerator[tuple[Loom, object]]:
    pytest.importorskip("mcp", reason="extra 'mcp' absent")
    from mcp.shared.memory import create_connected_server_and_client_session as connected

    from loom_ia.access.mcp_server import create_server

    async with Loom.from_config(path) as loom:
        async with connected(create_server(loom)) as client:
            yield loom, client


async def test_mcp_reports_progress_with_the_subruns(tree: ConfigFactory) -> None:
    pytest.importorskip("mcp", reason="extra 'mcp' absent")
    from mcp import ClientSession

    notes: list[tuple[float, str | None]] = []

    async def on_progress(progress: float, total: float | None, message: str | None) -> None:
        notes.append((progress, message))

    async with mcp_client(tree()) as (loom, client):
        assert isinstance(client, ClientSession)
        followed = await client.call_tool(
            "demo", {"message": TREE_QUESTION}, progress_callback=on_progress
        )
        quiet = await client.call_tool("demo", {"message": TREE_QUESTION})
        assert followed.structuredContent is not None
        events = await loom.events(RunId(followed.structuredContent["run_id"]))

    assert followed.structuredContent["text"] == TREE_ANSWER
    assert [message for _, message in notes] == PROGRESS
    assert [progress for progress, _ in notes] == list(range(1, len(PROGRESS) + 1))
    assert [e.type for e in events] == TREE_TYPES
    assert not quiet.isError and len(notes) == len(PROGRESS)


# --- Les trois accès ---------------------------------------------------------------------


async def test_the_three_accesses_give_the_same_tree(tree: ConfigFactory) -> None:
    pytest.importorskip("fastapi", reason="extra 'http' absent")
    httpx2 = pytest.importorskip("httpx2", reason="client HTTP de test absent")
    from loom_ia.access.http import create_app

    path = tree()
    runs: dict[str, RunId] = {}
    async with Loom.from_config(path) as loom:
        runs["python"] = (await loom.run("demo", TREE_QUESTION)).run_id
        transport = httpx2.ASGITransport(create_app(loom))
        async with httpx2.AsyncClient(transport=transport, base_url="http://loom.test") as http:
            posted = await http.post("/v1/agents/demo/runs", json={"message": TREE_QUESTION})
            runs["rest"] = RunId(posted.json()["run_id"])
    async with mcp_client(path) as (_, client):
        from mcp import ClientSession

        assert isinstance(client, ClientSession)
        called = await client.call_tool("demo", {"message": TREE_QUESTION})
        assert called.structuredContent is not None
        runs["mcp"] = RunId(called.structuredContent["run_id"])

    async with Loom.from_config(path) as loom:
        trees = {name: await loom.events(run_id) for name, run_id in runs.items()}
    assert all([e.type for e in events] == TREE_TYPES for events in trees.values())
    assert all(len({e.run_id for e in events}) == 2 for events in trees.values())


# --- CLI ---------------------------------------------------------------------------------


def test_cli_stream_shows_the_subruns(
    tree: ConfigFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--config", str(tree()), "run", "demo", TREE_QUESTION, "--stream"]) == 0
    captured = capsys.readouterr()
    assert captured.out.startswith("Je fais vérifier.\n")
    assert TREE_ANSWER in captured.out
    assert [
        line for line in captured.err.splitlines() if line.lstrip().startswith("· ")
    ] == PROGRESS


def test_a_session_run_keeps_its_tree(tree: ConfigFactory) -> None:
    async def go() -> list[Event]:
        async with Loom.from_config(tree()) as loom:
            session = SessionId("atelier")
            await loom.run("demo", TREE_QUESTION, session_id=session)
            second = await loom.run("demo", TREE_QUESTION, session_id=session)
            return await loom.events(second.run_id, session_id=session)

    events = asyncio.run(go())
    # Seuls le second run et son enfant : pas le premier arbre de la session.
    assert [e.type for e in events] == TREE_TYPES
