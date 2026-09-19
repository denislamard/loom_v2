# SPDX-License-Identifier: Apache-2.0
"""Serveur MCP : un agent, un outil (N3, D4).

Chaque agent publié (``expose.mcp``) devient un outil MCP qui prend un
message, éventuellement des images (argument ``attachments``, décrit dans le
module du même nom), et rend la réponse du run avec la liste de ses fichiers. Un outil de plus,
``run_status``, relit un run par son identifiant.

Progression (D4) : si le client en demande une (``progressToken``), chaque
étape visible du run — appels d'outils, fichiers rangés, sous-agents qui
démarrent et se terminent, avec leurs propres appels — lui est envoyée en
notification de progression, dans l'ordre du journal.

Le transport du jalon J1 est stdio : le client lance le process et parle sur
son entrée et sa sortie standard. Les logs de loom vont sur la sortie
d'erreur, jamais sur stdout — le protocole y passe.

    claude mcp add loom -- uv run loom mcp

Le serveur est bâti sur l'API bas niveau du SDK MCP : les outils sont
déclarés dynamiquement, puisqu'ils viennent de la configuration.
"""

from importlib.metadata import PackageNotFoundError, version
from typing import Any, Final

import mcp.types as types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from loom_ia.access.api import Loom, RunResult
from loom_ia.access.mcp_server.attachments import ATTACHMENTS_INPUT, AttachmentReader
from loom_ia.access.progress import Progress
from loom_ia.agents.registry import UnknownAgent
from loom_ia.core.events import Event
from loom_ia.core.model import DEFAULT_TENANT, Attachment, RunId, SessionId, new_run_id

SERVER_NAME: Final = "loom"
STATUS_TOOL: Final = "run_status"

AGENT_INPUT: Final[dict[str, Any]] = {
    "type": "object",
    "properties": {
        "message": {"type": "string", "description": "La demande adressée à l'agent"},
        "session_id": {
            "type": "string",
            "description": "Journal auquel rattacher le run, pour poursuivre un échange",
        },
        "attachments": ATTACHMENTS_INPUT,
    },
    "required": ["message"],
    "additionalProperties": False,
}

STATUS_INPUT: Final[dict[str, Any]] = {
    "type": "object",
    "properties": {
        "run_id": {"type": "string", "description": "Identifiant du run à relire"},
        "session_id": {"type": "string", "description": "Journal du run, s'il en a un"},
    },
    "required": ["run_id"],
    "additionalProperties": False,
}

RUN_OUTPUT: Final[dict[str, Any]] = {
    "type": "object",
    "properties": {
        "run_id": {"type": "string"},
        "session_id": {"type": "string"},
        "agent": {"type": "string"},
        "status": {"type": "string"},
        "text": {"type": "string"},
        "error": {"type": ["string", "null"]},
        "iterations": {"type": "integer"},
        # Fichiers du run : pièces jointes, fichiers produits, déports.
        "artifacts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "uri": {"type": "string"},
                    "media_type": {"type": "string"},
                    "size": {"type": "integer"},
                    "name": {"type": "string"},
                    "origin": {"type": "string"},
                    "call_id": {"type": "string"},
                },
                "required": ["uri", "media_type", "size", "origin"],
            },
        },
    },
    "required": ["run_id", "session_id", "agent", "status", "text"],
}


def create_server(loom: Loom, *, name: str = SERVER_NAME) -> Server[object, Any]:
    """Serveur MCP publiant les agents d'une instance."""
    server = Server[object, Any](name, version=_package_version(), instructions=_instructions(loom))
    # Le serveur MCP n'a qu'un client : celui par défaut (les clients viendront en J5).
    reader = AttachmentReader(
        loom.artifacts,
        loom.config.execution.attachments,
        tenant=DEFAULT_TENANT,
        roots=loom.config.server.mcp.file_roots,
    )

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        tools = [
            types.Tool(
                name=spec.name,
                description=spec.description or f"Agent loom {spec.name}",
                inputSchema=AGENT_INPUT,
                outputSchema=RUN_OUTPUT,
            )
            for spec in loom.exposed("mcp")
        ]
        tools.append(
            types.Tool(
                name=STATUS_TOOL,
                description="Statut et réponse d'un run déjà lancé",
                inputSchema=STATUS_INPUT,
                outputSchema=RUN_OUTPUT,
            )
        )
        return tools

    @server.call_tool()
    async def call_tool(
        tool: str, arguments: dict[str, Any]
    ) -> tuple[list[types.ContentBlock], dict[str, Any]]:
        session = arguments.get("session_id")
        session_id = SessionId(str(session)) if session else None
        if tool == STATUS_TOOL:
            result = await loom.result(RunId(str(arguments["run_id"])), session_id=session_id)
        else:
            _published(loom, tool)
            attachments = await reader.read(arguments.get("attachments"))
            message = str(arguments["message"])
            result = await _run(server, loom, tool, message, attachments, session_id)
        return [types.TextContent(type="text", text=_answer(result))], structured(result)

    return server


async def _run(
    server: Server[object, Any],
    loom: Loom,
    agent: str,
    message: str,
    attachments: list[Attachment],
    session_id: SessionId | None,
) -> RunResult:
    """Fait tourner le run ; suit sa progression si le client en demande une."""
    ctx = server.request_context
    token = ctx.meta.progressToken if ctx.meta is not None else None
    if token is None:
        return await loom.run(agent, message, attachments=attachments, session_id=session_id)
    run_id = new_run_id()
    progress = Progress()
    sent = 0
    async for item in loom.stream(
        agent, message, attachments=attachments, session_id=session_id, run_id=run_id
    ):
        if isinstance(item, Event) and (line := progress.line(item)) is not None:
            sent += 1
            await ctx.session.send_progress_notification(
                token, sent, message=line, related_request_id=str(ctx.request_id)
            )
    return await loom.result(run_id, session_id=session_id)


async def run_stdio(loom: Loom, *, name: str = SERVER_NAME) -> None:
    """Sert l'instance sur l'entrée et la sortie standard, jusqu'à la fin du flux."""
    server = create_server(loom, name=name)
    async with stdio_server() as (reader, writer):
        await server.run(reader, writer, server.create_initialization_options())


def structured(result: RunResult) -> dict[str, Any]:
    """Résultat d'un run tel que l'outil MCP le rend."""
    return {
        "run_id": result.run_id,
        "session_id": result.session_id,
        "agent": result.agent,
        "status": str(result.status),
        "text": result.text,
        "error": result.error,
        "iterations": result.iterations,
        "artifacts": [
            artifact.model_dump(mode="json", exclude_none=True) for artifact in result.artifacts
        ],
    }


def _answer(result: RunResult) -> str:
    if result.text:
        return result.text
    return result.error or f"Run {result.run_id} : {result.status}"


def _instructions(loom: Loom) -> str:
    published = ", ".join(spec.name for spec in loom.exposed("mcp")) or "aucun"
    return f"Agents loom disponibles : {published}."


def _published(loom: Loom, name: str) -> None:
    published = [spec.name for spec in loom.exposed("mcp")]
    if name not in published:
        raise UnknownAgent(name, published)


def _package_version() -> str | None:
    try:
        return version("loom-ia")
    except PackageNotFoundError:
        return None
