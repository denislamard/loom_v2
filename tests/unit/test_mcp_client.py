# SPDX-License-Identifier: Apache-2.0
"""Client MCP (#19, D3) : traductions, sélection, appels, connexion et transports."""

import asyncio
import logging
import os
import signal
import socket
import sys
import threading
import time
from collections.abc import AsyncGenerator, Callable, Iterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("mcp", reason="extra 'mcp' absent")

import uvicorn
from mcp import ClientSession, McpError, types
from mcp.client.session import MessageHandlerFnT
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.lowlevel import Server
from mcp.shared.memory import create_connected_server_and_client_session as connected
from pydantic import ValidationError
from starlette.datastructures import Headers
from starlette.responses import Response
from starlette.types import Receive, Scope, Send

from loom_ia.adapters.mcp import (
    IDEMPOTENCY_META,
    ConnectionLost,
    McpConfigError,
    McpPool,
    McpSelection,
    McpServer,
    McpSource,
    SessionFactory,
    declared,
    description,
    is_connection_lost,
    session_factory,
    to_output,
)
from loom_ia.core.model import (
    DEFAULT_TENANT,
    InlineDataBlock,
    McpServerSpec,
    RunId,
    SessionId,
    TextBlock,
    ToolOverrides,
)
from loom_ia.core.ports import SourceContext, SourceUnavailable, ToolContext, ToolError

RUN = SourceContext(
    tenant_id=DEFAULT_TENANT, session_id=SessionId("s"), run_id=RunId("r1"), agent="demo"
)
CALL = ToolContext(
    tenant_id=DEFAULT_TENANT,
    session_id=SessionId("s"),
    run_id=RunId("r1"),
    call_id="c1",
    agent="demo",
)
READ_ONLY = types.ToolAnnotations(readOnlyHint=True)


def spec(name: str = "math", **changes: Any) -> McpServerSpec:
    return McpServerSpec.model_validate(
        {"name": name, "transport": "stdio", "command": "x", **changes}
    )


def memory(server: FastMCP) -> SessionFactory:
    """Session en mémoire vers un serveur FastMCP du test."""

    def factory(handler: MessageHandlerFnT) -> Any:
        return connected(server, message_handler=handler)

    return factory


def math_server() -> FastMCP:
    server = FastMCP("math", log_level="WARNING")

    @server.tool(annotations=READ_ONLY)
    def additionner(a: int, b: int) -> int:
        """Additionne deux entiers."""
        return a + b

    @server.tool(annotations=types.ToolAnnotations(destructiveHint=False))
    def noter(texte: str) -> str:
        """Note un texte."""
        return f"noté : {texte}"

    @server.tool(name="point.dans.le.nom")
    def pointe() -> str:
        """Nom hors du format des API."""
        return "."

    @server.tool(name="n" * 70)
    def trop_long() -> str:
        """Nom trop long une fois préfixé."""
        return "long"

    return server


def source(server: FastMCP, selection: McpSelection | None = None, **changes: Any) -> McpSource:
    factory = memory(server)
    return McpSource(
        spec(**changes),
        selection or McpSelection(prefix="math"),
        factory=factory,
        pool=McpPool(lambda _: factory),
    )


# --- Traductions -------------------------------------------------------------


@pytest.mark.parametrize(
    ("hints", "expected"),
    [
        (None, ("irreversible", False)),
        (types.ToolAnnotations(), ("irreversible", False)),
        (types.ToolAnnotations(readOnlyHint=True, idempotentHint=False), ("none", True)),
        (types.ToolAnnotations(destructiveHint=False), ("reversible", False)),
        (types.ToolAnnotations(destructiveHint=True, idempotentHint=True), ("irreversible", True)),
    ],
)
def test_annotations_give_default_declarations(
    hints: types.ToolAnnotations | None, expected: tuple[str, bool]
) -> None:
    assert declared(hints) == expected


def test_description_falls_back_on_the_title() -> None:
    schema: dict[str, Any] = {"type": "object"}
    assert description(types.Tool(name="a", description="Desc", inputSchema=schema)) == "Desc"
    assert description(types.Tool(name="a", title="Titre", inputSchema=schema)) == "Titre"
    titled = types.Tool(name="a", inputSchema=schema, annotations=types.ToolAnnotations(title="T"))
    assert description(titled) == "T"
    assert description(types.Tool(name="a", inputSchema=schema)) == ""


def test_results_are_translated() -> None:
    result = types.CallToolResult(
        content=[
            types.TextContent(type="text", text="bonjour"),
            types.ImageContent(type="image", mimeType="image/png", data="AAAA"),
            types.AudioContent(type="audio", mimeType="audio/wav", data="AAAAAAAA"),
            types.ResourceLink(type="resource_link", name="doc", uri="file:///doc.txt"),  # pyright: ignore[reportArgumentType]
            types.EmbeddedResource(
                type="resource",
                resource=types.TextResourceContents(uri="file:///a.txt", text="contenu"),  # pyright: ignore[reportArgumentType]
            ),
            types.EmbeddedResource(
                type="resource",
                resource=types.BlobResourceContents(uri="file:///b.bin", blob="AAAA"),  # pyright: ignore[reportArgumentType]
            ),
        ],
        structuredContent={"n": 1},
        isError=True,
    )
    output = to_output(result)
    assert output.is_error and output.data == {"n": 1}
    # Les contenus binaires deviennent des octets : le moteur les range ensuite.
    assert output.blocks == (
        TextBlock(text="bonjour"),
        InlineDataBlock(media_type="image/png", data=b"\x00\x00\x00"),
        InlineDataBlock(media_type="audio/wav", data=b"\x00" * 6),
        TextBlock(text="[ressource doc : file:///doc.txt]"),
        TextBlock(text="contenu"),
        InlineDataBlock(media_type="application/octet-stream", data=b"\x00" * 3, name="b.bin"),
    )
    broken = types.CallToolResult(
        content=[types.ImageContent(type="image", mimeType="image/png", data="%%%")]
    )
    assert to_output(broken).blocks == (
        TextBlock(text="[contenu image/png illisible : base64 invalide]"),
    )


def test_connection_errors_are_told_apart() -> None:
    lost = McpError(types.ErrorData(code=types.CONNECTION_CLOSED, message="Connection closed"))
    invalid = McpError(types.ErrorData(code=types.INVALID_PARAMS, message="bad"))
    # Le SDK rend ainsi la réponse 404 d'un serveur HTTP qui ne connaît plus la session.
    expired = McpError(types.ErrorData(code=32600, message="Session terminated"))
    refused = McpError(types.ErrorData(code=32600, message="Invalid request"))
    assert is_connection_lost(lost)
    assert is_connection_lost(expired)
    assert not is_connection_lost(refused)
    assert is_connection_lost(ConnectionResetError())
    assert not is_connection_lost(invalid)
    assert not is_connection_lost(ValueError())


# --- Sélection ---------------------------------------------------------------


async def test_tools_are_prefixed_and_declared(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="loom_ia.adapters.mcp.source"):
        async with source(math_server()).open(RUN) as tools:
            specs = {tool.spec.name: tool.spec for tool in tools}
    assert list(specs) == ["math__additionner", "math__noter"]
    add = specs["math__additionner"]
    assert (add.kind, add.side_effects, add.idempotent, add.description) == (
        "mcp",
        "none",
        True,
        "Additionne deux entiers.",
    )
    assert add.input_schema["required"] == ["a", "b"]
    # Title racine retiré, comme pour les outils Python ; titres des propriétés gardés.
    assert "title" not in add.input_schema
    assert add.input_schema["properties"] == {
        "a": {"title": "A", "type": "integer"},
        "b": {"title": "B", "type": "integer"},
    }
    assert specs["math__noter"].side_effects == "reversible"
    assert "Outil MCP 'point.dans.le.nom' du serveur math écarté" in caplog.text
    assert f"Outil MCP '{'n' * 70}' du serveur math écarté" in caplog.text


async def test_alias_filters_and_declarations(caplog: pytest.LogCaptureFixture) -> None:
    selection = McpSelection(
        prefix="m",
        include=("additionner", "noter", "absent"),
        tools={"noter": ToolOverrides(side_effects="irreversible", approval="always")},
        required=True,
    )
    chosen = source(
        math_server(),
        selection,
        tools={"noter": {"side_effects": "none", "idempotent": True, "timeout": 3}, "fantome": {}},
    )
    with caplog.at_level(logging.WARNING, logger="loom_ia.adapters.mcp.source"):
        async with chosen.open(RUN) as tools:
            specs = {tool.spec.name: tool.spec for tool in tools}
        async with chosen.open(RUN):
            pass
    assert list(specs) == ["m__additionner", "m__noter"]
    noter = specs["m__noter"]
    # Annotations, puis la config du serveur, puis la référence de l'agent.
    assert (noter.side_effects, noter.idempotent, noter.timeout, noter.approval) == (
        "irreversible",
        True,
        3,
        "always",
    )
    assert chosen.required and chosen.name == "math"
    assert "include cite des outils inconnus du serveur : absent" in caplog.text
    assert "tools (serveur) cite des outils inconnus du serveur : fantome" in caplog.text
    # Les avertissements ne sont donnés qu'une fois.
    assert caplog.text.count("include cite") == 1

    excluded = source(math_server(), McpSelection(prefix="math", exclude=("noter",)))
    async with excluded.open(RUN) as tools:
        assert [tool.spec.name for tool in tools] == ["math__additionner"]


async def test_the_config_rewrites_what_the_model_reads() -> None:
    """``description`` remplace celle du serveur ; celle de l'agent l'emporte."""
    selection = McpSelection(
        prefix="math", tools={"noter": ToolOverrides(description="Note pour l'atelier.")}
    )
    chosen = source(
        math_server(),
        selection,
        tools={
            "additionner": {"description": "Additionne deux montants."},
            "noter": {"description": "Note pour le serveur."},
        },
    )
    async with chosen.open(RUN) as tools:
        specs = {tool.spec.name: tool.spec for tool in tools}
    add, noter = specs["math__additionner"], specs["math__noter"]
    assert add.definition().description == "Additionne deux montants."
    assert noter.definition().description == "Note pour l'atelier."
    # Le reste de ce que l'outil déclare ne bouge pas.
    assert (add.input_schema["required"], add.side_effects) == (["a", "b"], "none")


def test_a_rewritten_description_cannot_be_empty() -> None:
    with pytest.raises(ValidationError, match="description"):
        ToolOverrides.model_validate({"description": ""})


# --- Appels ------------------------------------------------------------------


def calls_server(seen: dict[str, Any]) -> FastMCP:
    server = FastMCP("outils", log_level="WARNING")

    @server.tool(annotations=READ_ONLY)
    async def lire(ctx: Context[Any, Any, Any]) -> str:
        """Lit la clé d'idempotence."""
        meta = ctx.request_context.meta
        seen["meta"] = meta.model_dump() if meta else None
        return "lu"

    @server.tool()
    def structure() -> dict[str, int]:
        """Résultat structuré."""
        return {"total": 42}

    @server.tool()
    def casser() -> str:
        """Échoue."""
        raise ValueError("cassé")

    return server


async def test_calls_carry_the_idempotency_key_and_translate_results() -> None:
    seen: dict[str, Any] = {}
    chosen = source(calls_server(seen), McpSelection(prefix="o"), name="outils")
    async with chosen.open(RUN) as tools:
        by_name = {tool.spec.name: tool for tool in tools}
        read = await by_name["o__lire"].invoke({}, CALL)
        structured = await by_name["o__structure"].invoke({}, CALL)
        failed = await by_name["o__casser"].invoke({}, CALL)

    # FastMCP ajoute un contenu structuré ({"result": …}) aux valeurs simples.
    assert (read.as_text, read.data, read.is_error) == ("lu", {"result": "lu"}, False)
    assert seen["meta"][IDEMPOTENCY_META] == CALL.idempotency_key
    assert (structured.data, structured.is_error) == ({"total": 42}, False)
    assert failed.is_error and "cassé" in failed.as_text


async def test_call_errors_become_tool_errors() -> None:
    tools = McpSource(spec(scope="run"), McpSelection(prefix="math"), factory=memory(math_server()))
    async with tools.open(RUN) as opened:
        add = opened[0]
    # La source de portée run est refermée : l'outil ne peut plus joindre son serveur.
    with pytest.raises(ToolError, match="Serveur MCP math indisponible : connexion fermée"):
        await add.invoke({"a": 1, "b": 2}, CALL)


def bare(*, listing: bool) -> Server[Any, Any]:
    """Serveur bas niveau : liste un outil, mais ne sait pas l'appeler."""
    server: Server[Any, Any] = Server("nu")
    if listing:

        @server.list_tools()
        async def _tools() -> list[types.Tool]:  # pyright: ignore[reportUnusedFunction]
            schema: dict[str, Any] = {"type": "object"}
            return [types.Tool(name="seul", description="Seul outil.", inputSchema=schema)]

    return server


def bare_factory(server: Server[Any, Any]) -> SessionFactory:
    def factory(handler: MessageHandlerFnT) -> Any:
        return connected(server, message_handler=handler)

    return factory


async def test_protocol_errors() -> None:
    chosen = McpSource(
        spec("nu", scope="run"), McpSelection(prefix="nu"), factory=bare_factory(bare(listing=True))
    )
    async with chosen.open(RUN) as tools:
        [only] = tools
        with pytest.raises(ToolError, match="Erreur du serveur MCP nu : Method not found"):
            await only.invoke({}, CALL)

    mute = McpServer(spec("nu"), bare_factory(bare(listing=False)))
    with pytest.raises(SourceUnavailable, match="liste des outils : Method not found"):
        await mute.tools()
    await mute.aclose()


# --- Connexion ---------------------------------------------------------------


async def test_tool_list_follows_list_changed() -> None:
    server_side = FastMCP("t", log_level="WARNING")

    @server_side.tool(annotations=READ_ONLY)
    async def grandir(ctx: Context[Any, Any, Any]) -> str:
        """Ajoute un outil au serveur."""

        def neuf() -> str:
            """Nouvel outil."""
            return "neuf"

        server_side.add_tool(neuf)
        await ctx.session.send_tool_list_changed()
        return "ajouté"

    server = McpServer(spec("t"), memory(server_side))
    assert [t.name for t in await server.tools()] == ["grandir"]
    await server.call("grandir", {}, meta={}, retry=False)
    for _ in range(50):
        if len(await server.tools()) == 2:
            break
        await asyncio.sleep(0.01)
    assert [t.name for t in await server.tools()] == ["grandir", "neuf"]
    await server.aclose()


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


async def test_reconnection_waits_for_the_backoff() -> None:
    attempts: list[int] = []
    fixed = memory(math_server())

    def flaky(handler: MessageHandlerFnT) -> Any:
        attempts.append(1)
        if len(attempts) <= 2:
            return refused(handler)
        return fixed(handler)

    clock = Clock()
    server = McpServer(spec(), flaky, clock=clock, backoff=(1.0, 5.0))
    with pytest.raises(SourceUnavailable, match="ConnectionRefusedError: refusé"):
        await server.tools()
    with pytest.raises(SourceUnavailable, match="nouvelle tentative dans 1 s"):
        await server.tools()
    assert len(attempts) == 1
    clock.now += 1
    with pytest.raises(SourceUnavailable, match="refusé"):
        await server.tools()
    clock.now += 4
    with pytest.raises(SourceUnavailable, match="nouvelle tentative dans 1 s"):
        await server.tools()
    clock.now += 1
    assert len(await server.tools()) == 4
    assert server.connected
    await server.aclose()


@asynccontextmanager
async def refused(handler: MessageHandlerFnT) -> AsyncGenerator[ClientSession]:
    await asyncio.sleep(0)
    raise ConnectionRefusedError("refusé")
    yield  # pyright: ignore[reportUnreachable]


@asynccontextmanager
async def silent(handler: MessageHandlerFnT) -> AsyncGenerator[ClientSession]:
    await asyncio.sleep(30)
    yield  # pyright: ignore[reportReturnType]


async def test_connection_timeout() -> None:
    server = McpServer(spec(connect_timeout=0.05), silent)
    with pytest.raises(SourceUnavailable, match=r"pas de réponse en 0\.05 s"):
        await server.tools()
    await server.aclose()


async def test_idle_connection_is_closed_then_reopened() -> None:
    server = McpServer(spec(), memory(math_server()), idle_timeout=0.05)
    await server.tools()
    assert server.connected
    for _ in range(100):
        if not server.connected:
            break
        await asyncio.sleep(0.01)
    assert not server.connected
    result = await server.call("additionner", {"a": 2, "b": 3}, meta={}, retry=False)
    assert to_output(result).as_text == "5"
    await server.aclose()


async def reached(event: asyncio.Event) -> None:
    """Attend un point de passage du test ; échoue au lieu de bloquer s'il n'arrive pas."""
    async with asyncio.timeout(10):
        await event.wait()


async def test_cancelling_while_connecting_leaves_nothing_open() -> None:
    """Un appelant annulé pendant l'ouverture ne laisse pas la session tenue pour personne."""
    opening = asyncio.Event()
    cleaned: list[str] = []
    attempts: list[int] = []
    fixed = memory(math_server())

    @asynccontextmanager
    async def stuck(handler: MessageHandlerFnT) -> AsyncGenerator[ClientSession]:
        opening.set()
        try:
            await asyncio.Event().wait()
            yield  # pyright: ignore[reportReturnType]
        finally:
            cleaned.append("fermé")

    def factory(handler: MessageHandlerFnT) -> Any:
        attempts.append(1)
        return stuck(handler) if len(attempts) == 1 else fixed(handler)

    server = McpServer(spec(), factory)
    caller = asyncio.create_task(server.tools())
    await reached(opening)
    caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller
    # La tâche qui tient la session est partie avec l'appelant, et a tout refermé.
    assert [t for t in asyncio.all_tasks() if t.get_name().startswith("mcp:")] == []
    assert cleaned == ["fermé"]
    assert not server.connected
    # Le serveur n'a gardé aucune trace de l'échec : la tentative suivante réussit.
    assert len(await server.tools()) == 4
    await server.aclose()


class Dies:
    """Session factice dont la connexion numéro 1 est perdue ; les suivantes répondent."""

    def __init__(self, number: int, log: list[str], error: McpError) -> None:
        self.number = number
        self.log = log
        self.error = error
        self.closed = False

    async def send_ping(self) -> None:
        return None

    async def list_tools(self, params: Any = None) -> types.ListToolsResult:
        return types.ListToolsResult(tools=[])

    async def call_tool(self, name: str, arguments: Any, meta: Any = None) -> types.CallToolResult:
        self.log.append(f"{name} sur {self.number}")
        if self.number == 1:
            raise self.error
        text = f"{name} sur {self.number}"
        return types.CallToolResult(content=[types.TextContent(type="text", text=text)])


def dying_factory(log: list[str], error: McpError) -> tuple[SessionFactory, list[Dies]]:
    sessions: list[Dies] = []

    @asynccontextmanager
    async def open_session(handler: MessageHandlerFnT) -> AsyncGenerator[ClientSession]:
        session = Dies(len(sessions) + 1, log, error)
        sessions.append(session)
        try:
            yield session  # pyright: ignore[reportReturnType]
        finally:
            session.closed = True

    return open_session, sessions


async def test_a_late_failure_does_not_close_the_connection_that_replaced_it() -> None:
    """Deux appels perdent la même connexion : le second à échouer ne ferme pas la neuve."""
    slow_in_flight = asyncio.Event()
    slow_fails = asyncio.Event()
    fast_replayed = asyncio.Event()
    slow_replayed = asyncio.Event()
    answer = asyncio.Event()
    lost = McpError(types.ErrorData(code=types.CONNECTION_CLOSED, message="Connection closed"))
    sessions: list[Any] = []

    class Connection:
        def __init__(self) -> None:
            self.number = len(sessions) + 1
            self.closed = False

        async def send_ping(self) -> None:
            return None

        async def list_tools(self, params: Any = None) -> types.ListToolsResult:
            return types.ListToolsResult(tools=[])

        async def call_tool(
            self, name: str, arguments: Any, meta: Any = None
        ) -> types.CallToolResult:
            if self.number == 1:
                # La connexion 1 est tombée : « rapide » le voit aussitôt, « lent » plus tard.
                if name == "lent":
                    slow_in_flight.set()
                    await slow_fails.wait()
                raise lost
            (slow_replayed if name == "lent" else fast_replayed).set()
            await answer.wait()
            if self.closed:
                raise lost
            text = f"{name} sur {self.number}"
            return types.CallToolResult(content=[types.TextContent(type="text", text=text)])

    @asynccontextmanager
    async def open_session(handler: MessageHandlerFnT) -> AsyncGenerator[ClientSession]:
        connection = Connection()
        sessions.append(connection)
        try:
            yield connection  # pyright: ignore[reportReturnType]
        finally:
            connection.closed = True

    server = McpServer(spec(), open_session)
    await server.tools()
    slow = asyncio.create_task(server.call("lent", {}, meta={}, retry=True))
    await reached(slow_in_flight)
    fast = asyncio.create_task(server.call("rapide", {}, meta={}, retry=True))
    # « rapide » a perdu la connexion 1, en a ouvert une 2 et s'y rejoue.
    await reached(fast_replayed)
    slow_fails.set()
    # « lent » perd à son tour la connexion 1 (déjà remplacée) et se rejoue.
    await reached(slow_replayed)
    answer.set()
    assert to_output(await fast).as_text == "rapide sur 2"
    assert to_output(await slow).as_text == "lent sur 2"
    assert len(sessions) == 2
    await server.aclose()


@pytest.mark.parametrize("retry", [True, False])
async def test_an_expired_session_is_a_lost_connection(retry: bool) -> None:
    """« Session terminated » (404 du serveur HTTP) : reconnexion ; rejeu des seuls outils sûrs."""
    expired = McpError(types.ErrorData(code=32600, message="Session terminated"))
    log: list[str] = []
    factory, sessions = dying_factory(log, expired)
    server = McpServer(spec(), factory)
    await server.tools()
    if retry:
        assert to_output(await server.call("lire", {}, meta={}, retry=True)).as_text == "lire sur 2"
        assert log == ["lire sur 1", "lire sur 2"]
    else:
        with pytest.raises(ConnectionLost, match="Session terminated"):
            await server.call("ecrire", {}, meta={}, retry=False)
        # Pas de rejeu : rien ne prouve que le serveur n'a rien exécuté. Connexion refermée...
        assert log == ["ecrire sur 1"]
        assert sessions[0].closed and not server.connected
        # ... et l'appel suivant repart sur une session neuve.
        assert to_output(await server.call("ecrire", {}, meta={}, retry=False)).as_text == (
            "ecrire sur 2"
        )
    assert len(sessions) == 2
    await server.aclose()


# --- Transports réels ----------------------------------------------------------

STDIO_SERVER = '''
import os, sys
from pathlib import Path
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

mcp = FastMCP("proc", log_level="WARNING")
MARK = Path(sys.argv[1])

@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def pid() -> int:
    """Numéro du process serveur."""
    return os.getpid()

@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def fragile() -> str:
    """Plante au premier appel, répond au suivant."""
    if not MARK.exists():
        MARK.write_text("x")
        os._exit(1)
    return "réponse"

@mcp.tool()
def risque() -> str:
    """Plante à chaque appel ; effet de bord supposé."""
    os._exit(1)

@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def secret() -> str:
    """Variable reçue par env_from."""
    return os.environ.get("JETON", "absent")

mcp.run()
'''


STDIO_SLOW = """
import os, sys
from pathlib import Path

Path(sys.argv[1]).write_text(str(os.getpid()))
sys.stdin.read()  # ne parle jamais MCP : l'initialisation ne finit pas
"""


@pytest.fixture
def stdio_spec(tmp_path: Path) -> McpServerSpec:
    (tmp_path / "serveur.py").write_text(STDIO_SERVER, encoding="utf-8")
    return McpServerSpec(
        name="proc",
        transport="stdio",
        command=sys.executable,
        args=("serveur.py", str(tmp_path / "marque")),
        cwd=tmp_path,
        env_from={"JETON": "LOOM_JETON_TEST"},
        connect_timeout=20,
    )


async def test_stdio_crashes_are_handled(stdio_spec: McpServerSpec) -> None:
    per_run = stdio_spec.model_copy(update={"scope": "run"})
    factory = session_factory(per_run, environ={"LOOM_JETON_TEST": "s3cret"})
    chosen = McpSource(per_run, McpSelection(prefix="proc"), factory=factory)
    async with chosen.open(RUN) as tools:
        by_name = {tool.spec.name: tool for tool in tools}
        assert (await by_name["proc__secret"].invoke({}, CALL)).as_text == "s3cret"
        # Sans effet de bord : rejoué une fois après reconnexion.
        assert (await by_name["proc__fragile"].invoke({}, CALL)).as_text == "réponse"
        # Avec effet de bord supposé : pas rejoué, état inconnu pour le modèle.
        with pytest.raises(ToolError, match="l'outil a peut-être produit son effet"):
            await by_name["proc__risque"].invoke({}, CALL)
        # Le serveur repart pour l'appel suivant.
        assert int((await by_name["proc__pid"].invoke({}, CALL)).as_text) > 0


async def test_cancelling_while_a_stdio_server_starts_stops_the_process(tmp_path: Path) -> None:
    """Annulé pendant l'initialisation, l'appelant ne laisse pas le processus du serveur."""
    (tmp_path / "lent.py").write_text(STDIO_SLOW, encoding="utf-8")
    mark = tmp_path / "pid"
    slow = McpServerSpec(
        name="lent",
        transport="stdio",
        command=sys.executable,
        args=("lent.py", str(mark)),
        cwd=tmp_path,
        connect_timeout=30,
    )
    server = McpServer(slow, session_factory(slow, environ={}))
    caller = asyncio.create_task(server.tools())
    pid = 0
    try:
        for _ in range(2000):
            if mark.exists() and mark.read_text():
                break
            await asyncio.sleep(0.01)
        pid = int(mark.read_text())
        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await caller
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
    finally:
        if pid:
            with suppress(ProcessLookupError):
                os.kill(pid, signal.SIGKILL)
        await server.aclose()


async def test_health_check_reopens_a_dead_shared_connection(stdio_spec: McpServerSpec) -> None:
    pool = McpPool(lambda s: session_factory(s, environ={"LOOM_JETON_TEST": "x"}))
    server = pool.server(stdio_spec)
    await server.tools()
    first = int(to_output(await server.call("pid", {}, meta={}, retry=False)).as_text)
    os.kill(first, signal.SIGKILL)
    await asyncio.sleep(0.2)
    # Nouveau run : le ping échoue, la connexion est rouverte.
    assert len(await server.tools()) == 4
    second = int(to_output(await server.call("pid", {}, meta={}, retry=False)).as_text)
    assert second != first
    assert pool.server(stdio_spec) is server
    await pool.aclose()


def test_missing_secret_is_a_config_error(stdio_spec: McpServerSpec) -> None:
    with pytest.raises(McpConfigError, match="LOOM_JETON_TEST est absente ou vide"):
        session_factory(stdio_spec, environ={})
    http = McpServerSpec(
        name="h", transport="http", url="http://x/mcp", headers_env={"Authorization": "TOKEN"}
    )
    with pytest.raises(McpConfigError, match="TOKEN est absente ou vide"):
        session_factory(http, environ={"TOKEN": " "})


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture
def http_url() -> Iterator[str]:
    server = FastMCP("web", log_level="WARNING")

    @server.tool(annotations=READ_ONLY)
    def entete(ctx: Context[Any, Any, Any]) -> str:
        """En-tête Authorization reçu."""
        request = ctx.request_context.request
        return str(request.headers.get("authorization")) if request is not None else "?"  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType, reportUnknownArgumentType]

    port = free_port()
    config = uvicorn.Config(server.streamable_http_app(), port=port, log_level="warning")
    web = uvicorn.Server(config)
    thread = threading.Thread(target=web.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not web.started and time.monotonic() < deadline:
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}/mcp"
    web.should_exit = True
    thread.join(timeout=5)


async def test_http_transport_sends_the_headers(http_url: str) -> None:
    web = McpServerSpec(
        name="web",
        transport="http",
        url=http_url,
        headers_env={"Authorization": "WEB_TOKEN"},
        scope="run",
    )
    factory = session_factory(web, environ={"WEB_TOKEN": "Bearer abc"})
    chosen = McpSource(web, McpSelection(prefix="web"), factory=factory)
    async with chosen.open(RUN) as tools:
        [header] = tools
        assert (await header.invoke({}, CALL)).as_text == "Bearer abc"


@pytest.fixture
def forgetful_http() -> Iterator[tuple[str, Callable[[], None]]]:
    """Un serveur MCP HTTP (outil ``pong``) et de quoi lui faire oublier les sessions ouvertes."""
    server = FastMCP("web", log_level="WARNING")

    @server.tool(annotations=READ_ONLY)
    def pong() -> str:
        """Répond."""
        return "pong"

    app = server.streamable_http_app()
    seen: set[str] = set()
    forgotten: set[str] = set()

    async def forgetful(scope: Scope, receive: Receive, send: Send) -> None:
        session = Headers(scope=scope).get("mcp-session-id") if scope["type"] == "http" else None
        if session in forgotten:
            await Response(status_code=404)(scope, receive, send)
            return
        if session is not None:
            seen.add(session)
        await app(scope, receive, send)

    port = free_port()
    web = uvicorn.Server(uvicorn.Config(forgetful, port=port, log_level="warning"))
    thread = threading.Thread(target=web.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not web.started and time.monotonic() < deadline:
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}/mcp", lambda: forgotten.update(seen)
    web.should_exit = True
    thread.join(timeout=5)


async def test_an_http_session_the_server_forgot_is_a_lost_connection(
    forgetful_http: tuple[str, Callable[[], None]],
) -> None:
    """Le serveur répond 404 à la session (redémarrage) : appel perdu, puis connexion rouverte."""
    url, forget = forgetful_http
    remote = McpServerSpec(name="web", transport="http", url=url, scope="run")
    server = McpServer(remote, session_factory(remote, environ={}))
    try:
        await server.tools()
        assert to_output(await server.call("pong", {}, meta={}, retry=False)).as_text == "pong"
        forget()
        # Outil à effet de bord supposé : pas rejoué, et la connexion est refermée.
        with pytest.raises(ConnectionLost):
            await server.call("pong", {}, meta={}, retry=False)
        assert not server.connected
        # L'appel suivant rouvre une session ; un outil sûr est rejoué dans la foulée.
        assert to_output(await server.call("pong", {}, meta={}, retry=False)).as_text == "pong"
        forget()
        assert to_output(await server.call("pong", {}, meta={}, retry=True)).as_text == "pong"
        assert server.connected
    finally:
        await server.aclose()
