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

Approbations (#17, #39, J4.5) : ``approve`` n'est **jamais** un outil MCP —
le LLM du client validerait lui-même les effets qu'il demande. Deux chemins,
selon ce que le client sait faire :

- il déclare l'``elicitation`` : chaque demande part en formulaire (accorder
  ou refuser, avec un motif) et la réponse de l'humain est tranchée **dans la
  boucle** — le run ne passe pas par ``PAUSED``, et le journal garde qui a
  décidé (``mcp:<client>``, faute d'identité plus précise sur stdio). Un
  formulaire ne corrige pas les arguments d'un appel : cela reste à l'API ;
- il ne la déclare pas : le run s'arrête en ``PAUSED`` et l'outil **rend la
  main aussitôt**, sans erreur — le texte dit ce qui attend, le résultat
  structuré porte ``run_id`` et ``pending_approvals``. Un humain tranche
  ailleurs (API REST ou ``loom approve``), et ``run_status`` relit le run.

Le transport du jalon J1 est stdio : le client lance le process et parle sur
son entrée et sa sortie standard. Les logs de loom vont sur la sortie
d'erreur, jamais sur stdout — le protocole y passe.

    claude mcp add loom -- uv run loom mcp

Le serveur est bâti sur l'API bas niveau du SDK MCP : les outils sont
déclarés dynamiquement, puisqu'ils viennent de la configuration.
"""

import json
from collections.abc import Callable
from importlib.metadata import PackageNotFoundError, version
from typing import Any, Final

import mcp.types as types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from mcp.shared.context import RequestContext
from pydantic.json_schema import models_json_schema

from loom_ia.access.api import JudgeVerdict, Loom, RunResult, UnknownRun
from loom_ia.access.caller import Caller
from loom_ia.access.mcp_server.attachments import ATTACHMENTS_INPUT, AttachmentReader
from loom_ia.access.progress import Progress
from loom_ia.agents.registry import UnknownAgent
from loom_ia.config.models import Scope
from loom_ia.core.events import Event
from loom_ia.core.model import (
    DEFAULT_TENANT,
    ApprovalDecision,
    Approved,
    Approver,
    Attachment,
    PendingApproval,
    Rejected,
    RunId,
    RunStatus,
    SessionId,
    TenantId,
    Usage,
    new_run_id,
)
from loom_ia.usage import UsageReport, render

SERVER_NAME: Final = "loom"
# Un serveur HTTP sert tous les clients : ce qu'il publie dépend de la clé,
# donc ses instructions ne peuvent pas nommer des agents à l'avance.
_INSTRUCTIONS: Final = (
    "Agents loom : ceux que la clé d'API de la requête peut lancer (outil par agent)."
)

# Ce qui rend l'appelant de la requête en cours ; absent en stdio.
type CallerSource = Callable[[], Caller]
STATUS_TOOL: Final = "run_status"
REPORT_TOOL: Final = "run_report"
# Ce qu'un formulaire d'elicitation peut rendre, par champ.
type FormValue = str | int | float | bool | list[str] | None

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

# Schémas de l'usage, du rapport, d'un verdict et d'une demande d'approbation,
# rangés à la racine du schéma de sortie.
_REFS, _DEFS = models_json_schema(
    [
        (Usage, "serialization"),
        (UsageReport, "serialization"),
        (JudgeVerdict, "serialization"),
        (PendingApproval, "serialization"),
    ],
    ref_template="#/$defs/{model}",
)

# Ce qu'un client capable d'elicitation montre à l'humain : accorder ou
# refuser, et pourquoi. Un formulaire MCP n'a qu'un niveau de propriétés :
# corriger les arguments d'un appel n'y entre pas, et reste à l'API.
DECISION_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "properties": {
        "decision": {
            "type": "string",
            "title": "Décision",
            "description": "Autoriser l'appel, ou le refuser",
            "enum": ["accorder", "refuser"],
        },
        "motif": {
            "type": "string",
            "title": "Motif",
            "description": "Ce qui motive la décision ; inscrit au journal",
        },
    },
    "required": ["decision"],
}

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
        # Approbations qu'il faut trancher pour que le run avance, celles de
        # ses sous-runs comprises : un run en ``paused`` n'a pas fini.
        "pending_approvals": {
            "type": "array",
            "items": _REFS[(PendingApproval, "serialization")],
        },
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


def create_server(
    loom: Loom,
    *,
    name: str = SERVER_NAME,
    tenant: TenantId = DEFAULT_TENANT,
    callers: CallerSource | None = None,
) -> Server[object, Any]:
    """Serveur MCP publiant les agents d'une instance, au nom d'un appelant.

    En **stdio** il n'y a pas de clé d'API, donc rien dans le protocole ne
    dirait au nom de qui une requête arrive : un serveur sert **un** client,
    choisi à son lancement (``loom mcp --tenant``), et tout lui est permis.

    En **HTTP** (J5.2b), ``callers`` rend l'appelant de la requête en cours,
    tiré de sa clé : un même serveur sert alors tous les clients, publie à
    chacun ses agents, et refuse ce que sa clé ne permet pas.
    """
    fixed = Caller(without_key=tenant)

    def who() -> Caller:
        return fixed if callers is None else callers()

    instructions = _INSTRUCTIONS if callers is not None else _instructions(loom, tenant)
    server = Server[object, Any](name, version=_package_version(), instructions=instructions)

    def attachments_of(caller: Caller) -> AttachmentReader:
        return AttachmentReader(
            loom.artifacts,
            loom.config.execution.attachments,
            tenant=caller.tenant,
            roots=loom.config.server.mcp.file_roots,
        )

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        caller = who()
        tools = [
            types.Tool(
                name=spec.name,
                description=spec.description or f"Agent loom {spec.name}",
                inputSchema=AGENT_INPUT,
                outputSchema=RUN_OUTPUT,
            )
            for spec in loom.exposed("mcp", caller.tenant)
            if caller.allows(spec.name)
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
        caller = who()
        session = arguments.get("session_id")
        session_id = SessionId(str(session)) if session else None
        run = arguments.get("run_id")
        run_id = RunId(str(run)) if run else None
        # Lire et lancer ne demandent pas la même chose : relire un run est
        # une lecture, faire travailler un agent en est une autre (#39).
        needed: Scope = "read" if tool in (STATUS_TOOL, REPORT_TOOL) else "run"
        if not caller.may(needed):
            return _refused(f"Clé sans la portée {needed!r}")
        try:
            if tool == REPORT_TOOL:
                return await _report(loom, run_id, session_id, caller.tenant)
            if tool == STATUS_TOOL:
                found = await loom.result(
                    RunId(str(run)), session_id=session_id, tenant_id=caller.tenant
                )
                if not caller.allows(found.agent):
                    return _refused(f"Clé non autorisée sur l'agent {found.agent!r}")
                # Relire, c'est lire le journal : sans `read_content`, le
                # statut et les coûts passent, la correspondance non (J5.2a).
                return answer(found.masked() if caller.masks else found, ran=False)
            if not caller.allows(tool):
                return _refused(f"Clé non autorisée sur l'agent {tool!r}")
            _published(loom, tool, caller.tenant)
        except (UnknownAgent, UnknownRun) as exc:
            return _refused(str(exc.args[0]))
        attachments = await attachments_of(caller).read(arguments.get("attachments"))
        message = str(arguments["message"])
        # Ce qu'une clé lance, elle le reçoit : la réponse d'un run n'est
        # jamais masquée, c'est la relecture qui demande la portée.
        ran = await _run(server, loom, tool, message, attachments, session_id, caller.tenant)
        return answer(ran)

    return server


async def _run(
    server: Server[object, Any],
    loom: Loom,
    agent: str,
    message: str,
    attachments: list[Attachment],
    session_id: SessionId | None,
    tenant: TenantId = DEFAULT_TENANT,
) -> RunResult:
    """Fait tourner le run ; suit sa progression et fait trancher, si le client le sait."""
    ctx = server.request_context
    token = ctx.meta.progressToken if ctx.meta is not None else None
    approver = _elicited(ctx)
    if token is None:
        return await loom.run(
            agent,
            message,
            attachments=attachments,
            session_id=session_id,
            approver=approver,
            tenant=tenant,
        )
    run_id = new_run_id()
    progress = Progress()
    sent = 0
    async for item in loom.stream(
        agent,
        message,
        attachments=attachments,
        session_id=session_id,
        run_id=run_id,
        approver=approver,
        tenant=tenant,
    ):
        if isinstance(item, Event) and (line := progress.line(item)) is not None:
            sent += 1
            await ctx.session.send_progress_notification(
                token, sent, message=line, related_request_id=str(ctx.request_id)
            )
    return await loom.result(run_id, session_id=session_id, tenant_id=tenant)


def _elicited(ctx: RequestContext[Any, Any, Any]) -> Approver | None:
    """Approbateur en ligne bâti sur l'elicitation, si le client la déclare (#17).

    Sans elle, rien n'est monté : le run se mettra en pause, et l'outil rendra
    la main avec ce qu'il faut trancher.
    """
    capable = types.ClientCapabilities(elicitation=types.ElicitationCapability())
    if not ctx.session.check_client_capability(capable):
        return None
    client = ctx.session.client_params
    by = f"mcp:{client.clientInfo.name}" if client is not None else "mcp"

    async def decide(asked: PendingApproval) -> ApprovalDecision:
        answered = await ctx.session.elicit_form(
            _question(asked), DECISION_SCHEMA, related_request_id=str(ctx.request_id)
        )
        content: dict[str, FormValue] = answered.content or {}
        motif = str(content.get("motif") or "")
        if answered.action != "accept":
            # Refusé, ou fermé sans répondre : dans les deux cas l'effet
            # n'a pas été autorisé, et c'est tout ce que le journal dira.
            return Rejected(by=by, reason=motif or _CLOSED[answered.action])
        if content.get("decision") == "accorder":
            return Approved(by=by, reason=motif)
        return Rejected(by=by, reason=motif)

    return decide


_CLOSED: Final[dict[str, str]] = {
    "decline": "refusé par le client MCP",
    "cancel": "demande fermée sans réponse",
}


def _question(asked: PendingApproval) -> str:
    """Ce que l'humain lit avant de trancher : l'appel, ses arguments, le motif."""
    lines = [f"Approbation demandée pour l'outil {asked.tool_name}."]
    if asked.arguments:
        lines.append(f"Arguments : {json.dumps(asked.arguments, ensure_ascii=False)}")
    if asked.reason:
        lines.append(f"Motif : {asked.reason}")
    if asked.policy:
        lines.append(f"Exigée par la politique {asked.policy}.")
    return "\n".join(lines)


async def run_stdio(
    loom: Loom, *, name: str = SERVER_NAME, tenant: TenantId = DEFAULT_TENANT
) -> None:
    """Sert l'instance sur l'entrée et la sortie standard, jusqu'à la fin du flux."""
    server = create_server(loom, name=name, tenant=tenant)
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
        "pending_approvals": [
            approval.model_dump(mode="json") for approval in result.pending_approvals
        ],
        "artifacts": [
            artifact.model_dump(mode="json", exclude_none=True) for artifact in result.artifacts
        ],
    }


def _text(result: RunResult) -> str:
    """La réponse ; pour un échec, ce qui l'a arrêté ; pour une pause, ce qui attend."""
    if result.status is RunStatus.FAILED:
        return f"Échec de l'agent {result.agent} : {result.error}"
    if result.pending_approvals:
        return _awaiting(result)
    return result.text or f"Run {result.run_id} : {result.status}"


def _awaiting(result: RunResult) -> str:
    """Ce qu'un run arrêté sur une approbation dit au client : quoi, et comment reprendre."""
    asked = ", ".join(f"{a.tool_name} ({a.call_id})" for a in result.pending_approvals)
    return (
        f"Run {result.run_id} en attente d'approbation : {asked}. "
        "Un humain doit trancher (API REST ou « loom approve »), "
        f"puis {STATUS_TOOL} relit le run "
        f"(run_id={result.run_id}, session_id={result.session_id})."
    )


async def _report(
    loom: Loom,
    run_id: RunId | None,
    session_id: SessionId | None,
    tenant: TenantId = DEFAULT_TENANT,
) -> types.CallToolResult:
    """Consommation d'un run ou d'une session : le rapport en texte et en structuré."""
    if run_id is None and session_id is None:
        return _refused("Donner un run_id ou un session_id")
    report = await loom.report(run_id, session_id=session_id, tenant_id=tenant)
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


def _instructions(loom: Loom, tenant: TenantId = DEFAULT_TENANT) -> str:
    published = ", ".join(spec.name for spec in loom.exposed("mcp", tenant)) or "aucun"
    return f"Agents loom disponibles : {published}."


def _published(loom: Loom, name: str, tenant: TenantId = DEFAULT_TENANT) -> None:
    published = [spec.name for spec in loom.exposed("mcp", tenant)]
    if name not in published:
        raise UnknownAgent(name, published)


def _package_version() -> str | None:
    try:
        return version("loom-ia")
    except PackageNotFoundError:
        return None
