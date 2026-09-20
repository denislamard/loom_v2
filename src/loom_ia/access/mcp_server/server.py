# SPDX-License-Identifier: Apache-2.0
"""Serveur MCP : un agent, un outil (N3, D4).

Chaque agent publié (``expose.mcp``) devient un outil MCP qui prend un
message, éventuellement des images (argument ``attachments``, décrit dans le
module du même nom), et rend la réponse du run avec la liste de ses fichiers. Deux outils de
plus : ``run_status`` relit un run par son identifiant, ``run_report`` rend la
consommation d'un run (avec ses sous-runs) ou de toute une session.

Résultat (J3) : le texte est la réponse ; le résultat structuré y ajoute
``unverified``, l'usage et le coût, leur ventilation (``report``) et les
verdicts des juges. Une réponse gardée sans respecter son contrat ou son juge
est suivie d'un second texte qui le dit. Un run échoué rend un résultat
d'erreur (``isError``) dont le texte dit en clair ce qui l'a arrêté ; son
type (``guard.judge``, ``model.auth``…) est dans ``error_type``. Les juges
suivent leur ``when`` : l'accès MCP ne les force ni ne les retire.

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
from pydantic.json_schema import models_json_schema

from loom_ia.access.api import JudgeVerdict, Loom, RunResult, UnknownRun
from loom_ia.access.mcp_server.attachments import ATTACHMENTS_INPUT, AttachmentReader
from loom_ia.access.progress import Progress
from loom_ia.agents.registry import UnknownAgent
from loom_ia.core.events import Event
from loom_ia.core.model import (
    DEFAULT_TENANT,
    Attachment,
    RunId,
    RunStatus,
    SessionId,
    Usage,
    new_run_id,
)
from loom_ia.usage import UsageReport, render

SERVER_NAME: Final = "loom"
STATUS_TOOL: Final = "run_status"
REPORT_TOOL: Final = "run_report"
UNVERIFIED_NOTE: Final = (
    "Réponse non vérifiée : elle a été gardée sans respecter son contrat ou son juge."
)

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

REPORT_INPUT: Final[dict[str, Any]] = {
    "type": "object",
    "properties": {
        "run_id": {
            "type": "string",
            "description": "Run dont rendre la consommation, sous-runs compris",
        },
        "session_id": {
            "type": "string",
            "description": "Session dont rendre la consommation, ou journal du run",
        },
    },
    "anyOf": [{"required": ["run_id"]}, {"required": ["session_id"]}],
    "additionalProperties": False,
}

# Schémas de l'usage, du rapport et d'un verdict, rangés à la racine du schéma de sortie.
_REFS, _DEFS = models_json_schema(
    [(Usage, "serialization"), (UsageReport, "serialization"), (JudgeVerdict, "serialization")],
    ref_template="#/$defs/{model}",
)

REPORT_OUTPUT: Final[dict[str, Any]] = UsageReport.model_json_schema(mode="serialization")

RUN_OUTPUT: Final[dict[str, Any]] = {
    "type": "object",
    "properties": {
        "run_id": {"type": "string"},
        "session_id": {"type": "string"},
        "agent": {"type": "string"},
        "status": {"type": "string"},
        "text": {"type": "string"},
        # Échec : son type (``guard.judge``, ``model.auth``…) et son message lisible.
        "error_type": {"type": ["string", "null"]},
        "error": {"type": ["string", "null"]},
        "iterations": {"type": "integer"},
        "usage": _REFS[(Usage, "serialization")],
        "cost_usd": {"type": "number"},
        # Réponse structurée : l'objet JSON validé par le schéma de sortie.
        "data": {},
        # Réponse gardée sans respecter son contrat ou son juge.
        "unverified": {"type": "boolean"},
        # Consommation ventilée du run et de ses sous-runs.
        "report": {"anyOf": [_REFS[(UsageReport, "serialization")], {"type": "null"}]},
        "verdicts": {"type": "array", "items": _REFS[(JudgeVerdict, "serialization")]},
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
    **_DEFS,
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
        tools.append(
            types.Tool(
                name=REPORT_TOOL,
                description=(
                    "Consommation d'un run et de ses sous-runs, ou de toute une session : "
                    "appels, tokens et coût, par run, par rôle et par modèle"
                ),
                inputSchema=REPORT_INPUT,
                outputSchema=REPORT_OUTPUT,
            )
        )
        return tools

    @server.call_tool()
    async def call_tool(tool: str, arguments: dict[str, Any]) -> types.CallToolResult:
        session = arguments.get("session_id")
        session_id = SessionId(str(session)) if session else None
        run = arguments.get("run_id")
        run_id = RunId(str(run)) if run else None
        try:
            if tool == REPORT_TOOL:
                return await _report(loom, run_id, session_id)
            if tool == STATUS_TOOL:
                found = await loom.result(RunId(str(run)), session_id=session_id)
                return answer(found, ran=False)
            _published(loom, tool)
        except (UnknownAgent, UnknownRun) as exc:
            return _refused(str(exc.args[0]))
        attachments = await reader.read(arguments.get("attachments"))
        message = str(arguments["message"])
        return answer(await _run(server, loom, tool, message, attachments, session_id))

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


def answer(result: RunResult, *, ran: bool = True) -> types.CallToolResult:
    """Ce que rend l'outil d'un agent (``ran``) ou ``run_status``.

    Un run échoué est une erreur de l'outil qui l'a lancé ; ``run_status``,
    qui ne fait que le relire, rend son état sans erreur.
    """
    content: list[types.ContentBlock] = [types.TextContent(type="text", text=_text(result))]
    if result.unverified:
        content.append(types.TextContent(type="text", text=UNVERIFIED_NOTE))
    failed = ran and result.status is RunStatus.FAILED
    return types.CallToolResult(
        content=content, structuredContent=structured(result), isError=failed
    )


def structured(result: RunResult) -> dict[str, Any]:
    """Résultat d'un run tel que l'outil MCP le rend."""
    return {
        "run_id": result.run_id,
        "session_id": result.session_id,
        "agent": result.agent,
        "status": str(result.status),
        "text": result.text,
        "error_type": result.error_type,
        "error": result.error,
        "iterations": result.iterations,
        "usage": result.usage.model_dump(mode="json"),
        "cost_usd": result.cost_usd,
        "data": result.data,
        "unverified": result.unverified,
        "report": result.report.model_dump(mode="json") if result.report else None,
        "verdicts": [verdict.model_dump(mode="json") for verdict in result.verdicts],
        "artifacts": [
            artifact.model_dump(mode="json", exclude_none=True) for artifact in result.artifacts
        ],
    }


def _text(result: RunResult) -> str:
    """La réponse ; pour un échec, ce qui l'a arrêté, en clair."""
    if result.status is RunStatus.FAILED:
        return f"Échec de l'agent {result.agent} : {result.error}"
    return result.text or f"Run {result.run_id} : {result.status}"


async def _report(
    loom: Loom, run_id: RunId | None, session_id: SessionId | None
) -> types.CallToolResult:
    """Consommation d'un run ou d'une session : le rapport en texte et en structuré."""
    if run_id is None and session_id is None:
        return _refused("Donner un run_id ou un session_id")
    report = await loom.report(run_id, session_id=session_id)
    if not report.runs:
        return _refused(f"Session {session_id} inconnue")
    return types.CallToolResult(
        content=[types.TextContent(type="text", text="\n".join(render(report)))],
        structuredContent=report.model_dump(mode="json"),
    )


def _refused(message: str) -> types.CallToolResult:
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=message)], isError=True
    )


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
