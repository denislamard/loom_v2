# SPDX-License-Identifier: Apache-2.0
"""Serveur MCP : un agent, un outil (N3, D4).

Chaque agent publié (``expose.mcp``) devient un outil MCP qui prend un
message, éventuellement des images (argument ``attachments``, décrit dans le
module du même nom), et rend la réponse du run avec la liste de ses fichiers. Trois outils
de plus : ``run_status`` relit un run par son identifiant, ``run_report`` rend
la consommation d'un run (avec ses sous-runs) ou de toute une session, et
``cancel`` arrête un run.

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

L'elicitation n'existe qu'en **stdio**. Le serveur HTTP est sans état
(``stateless=True``, ``http.py``) : chaque requête a sa propre session, qui ne
connaît pas les capacités que le client a déclarées à son initialisation, et la
réponse à un formulaire arriverait par une autre requête que celle qui l'attend.
En HTTP, un run qui demande une approbation se met donc toujours en pause, que
le client déclare l'elicitation ou non, et il se tranche par REST ou
``loom approve``.

Noms réservés : ``run_status``, ``run_report`` et ``cancel`` sont des outils du
serveur. Un agent publié en MCP sous l'un de ces noms serait masqué par l'outil
intégré, donc injoignable : le montage du serveur le refuse en le nommant.

Le transport du jalon J1 est stdio : le client lance le process et parle sur
son entrée et sa sortie standard. Les logs de loom vont sur la sortie
d'erreur, jamais sur stdout — le protocole y passe.

    claude mcp add loom -- uv run loom mcp

Le serveur est bâti sur l'API bas niveau du SDK MCP : les outils sont
déclarés dynamiquement, puisqu'ils viennent de la configuration.
"""

import json
from collections.abc import Callable
from typing import Any, Final

import mcp.types as types
from mcp.server.lowlevel import Server
from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.server.stdio import stdio_server
from mcp.shared.context import RequestContext
from pydantic import AnyUrl
from pydantic.json_schema import models_json_schema

from loom_ia import __version__
from loom_ia.access import audit
from loom_ia.access.api import JudgeSkip, JudgeVerdict, Loom, RunResult, UnknownRun
from loom_ia.access.authz import AccessDenied, Actor
from loom_ia.access.caller import Caller
from loom_ia.access.mcp_server.attachments import ATTACHMENTS_INPUT, AttachmentReader
from loom_ia.access.mcp_server.resources import ResourceReader
from loom_ia.access.progress import Progress
from loom_ia.agents.registry import UnknownAgent
from loom_ia.config import ConfigError
from loom_ia.config.models import Permission
from loom_ia.core.events import Event
from loom_ia.core.model import (
    DEFAULT_TENANT,
    Answerer,
    ApprovalDecision,
    Approved,
    Approver,
    Attachment,
    NoAnswer,
    PendingApproval,
    PendingQuestion,
    QuestionReply,
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
# Nommé `cancel` comme #40 l'annonce depuis J1, et non `run_cancel` : la
# famille des deux autres outils de contrôle ferait pourtant la paire.
CANCEL_TOOL: Final = "cancel"
# Ce qu'un formulaire d'elicitation peut rendre, par champ.
type FormValue = str | int | float | bool | list[str] | None

UNVERIFIED_NOTE: Final = (
    "Réponse non vérifiée : elle a été gardée sans respecter son contrat ou son juge."
)
FALLBACK_NOTE: Final = (
    "Réponse de repli : la sortie a été refusée par son contrat ou son juge, "
    "puis remplacée par son message de repli."
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

CANCEL_INPUT: Final[dict[str, Any]] = {
    "type": "object",
    "properties": {
        "run_id": {"type": "string", "description": "Identifiant du run à arrêter"},
        "session_id": {"type": "string", "description": "Journal du run, s'il en a un"},
    },
    "required": ["run_id"],
    "additionalProperties": False,
}

CANCEL_OUTPUT: Final[dict[str, Any]] = {
    "type": "object",
    "properties": {
        "run_id": {"type": "string"},
        # Faux si le run était déjà terminé : il n'y avait rien à arrêter.
        "cancelled": {"type": "boolean"},
    },
    "required": ["run_id", "cancelled"],
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
        (JudgeSkip, "serialization"),
        (PendingApproval, "serialization"),
        (PendingQuestion, "serialization"),
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
        # Réponse remplacée par le message de repli d'un contrat ou d'un juge.
        "fallback": {"type": "boolean"},
        # Consommation ventilée du run et de ses sous-runs.
        "report": {"anyOf": [_REFS[(UsageReport, "serialization")], {"type": "null"}]},
        "verdicts": {"type": "array", "items": _REFS[(JudgeVerdict, "serialization")]},
        # Juges déclarés qui n'ont pas jugé (appelant, échantillon, client, condition, profil).
        "skipped_judges": {"type": "array", "items": _REFS[(JudgeSkip, "serialization")]},
        # Approbations qu'il faut trancher pour que le run avance, celles de
        # ses sous-runs comprises : un run en ``paused`` n'a pas fini.
        "pending_approvals": {
            "type": "array",
            "items": _REFS[(PendingApproval, "serialization")],
        },
        # Questions posées à l'utilisateur qui attendent sa réponse (wait_answer) :
        # rappeler l'outil de l'agent avec la même session et la réponse en message.
        "pending_questions": {
            "type": "array",
            "items": _REFS[(PendingQuestion, "serialization")],
        },
        # Ce que le masquage a retiré (clé sans ``read_content``), par chemin.
        "redacted": {"type": "array", "items": {"type": "string"}},
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
    callers: CallerSource | None = None,
    user: str | None = None,
) -> Server[object, Any]:
    """Serveur MCP publiant les agents d'une instance, au nom d'un appelant.

    En **stdio** il n'y a pas de clé d'API, donc rien dans le protocole ne
    dirait au nom de qui une requête arrive : un serveur sert **un**
    utilisateur, nommé à son lancement (``user``, ``loom mcp --user``). Le
    client est le sien, et ses droits valent comme partout ; sans lui,
    ``TypeError`` (D-1). Jamais le modèle ne choisit l'identité : aucun outil
    ne prend d'utilisateur. ``AccessDenied`` pour un utilisateur inconnu.

    En **HTTP** (J5.2b), ``callers`` rend l'appelant de la requête en cours,
    tiré de sa clé : un même serveur sert alors tous les clients, publie à
    chacun ses agents, et refuse ce que sa clé ne permet pas.

    Lève ``ConfigError`` si un agent publié en MCP porte le nom d'un des outils
    du serveur (``run_status``, ``run_report``, ``cancel``) : l'outil intégré
    le masquerait.
    """
    for spec in loom.exposed("mcp"):
        if spec.name in (STATUS_TOOL, REPORT_TOOL, CANCEL_TOOL):
            raise ConfigError(
                f"Agent {spec.name!r} publié en MCP : son nom est celui d'un outil du serveur "
                f"({STATUS_TOOL}, {REPORT_TOOL}, {CANCEL_TOOL}), qui le masquerait. "
                "Renommer l'agent, ou le retirer du MCP (expose.mcp: false)."
            )
    if callers is not None:
        who, instructions = callers, _INSTRUCTIONS
    elif user is not None:
        fixed = Caller.acting(loom.config.security, user)
        who, instructions = _always(fixed), _instructions(loom, fixed.tenant)
    else:
        raise TypeError("create_server : user= (stdio) ou callers= (HTTP) — au nom de qui ?")
    server = Server[object, Any](name, version=__version__, instructions=instructions)

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
        tools.append(
            types.Tool(
                name=CANCEL_TOOL,
                description="Arrête un run en cours ; faux s'il était déjà terminé",
                inputSchema=CANCEL_INPUT,
                outputSchema=CANCEL_OUTPUT,
                annotations=types.ToolAnnotations(destructiveHint=True, idempotentHint=True),
            )
        )
        return tools

    def reader(caller: Caller) -> ResourceReader:
        return ResourceReader(loom, caller, policy=loom.config.execution.attachments)

    @server.list_resources()
    async def list_resources() -> list[types.Resource]:
        return reader(who()).listed()

    @server.list_resource_templates()
    async def list_resource_templates() -> list[types.ResourceTemplate]:
        return reader(who()).templates()

    @server.read_resource()
    async def read_resource(uri: AnyUrl) -> list[ReadResourceContents]:
        with audit.through("mcp", loom.config, audit.Aim(action=f"read {uri}")):
            return await reader(who()).read(str(uri))

    @server.call_tool()
    async def call_tool(tool: str, arguments: dict[str, Any]) -> types.CallToolResult:
        # Un refus qui sortira du SDK pendant cet appel dira qu'il est venu par MCP.
        run = arguments.get("run_id")
        aim = audit.Aim(
            action=tool,
            agent=tool if tool not in (STATUS_TOOL, REPORT_TOOL, CANCEL_TOOL) else None,
            run_id=str(run) if run else None,
        )
        with audit.through("mcp", loom.config, aim):
            return await called(tool, arguments)

    async def called(tool: str, arguments: dict[str, Any]) -> types.CallToolResult:
        caller = who()
        session = arguments.get("session_id")
        session_id = SessionId(str(session)) if session else None
        run = arguments.get("run_id")
        run_id = RunId(str(run)) if run else None
        # Lire et lancer ne demandent pas la même chose : relire un run est
        # une lecture, faire travailler un agent en est une autre (#39).
        needed: Permission = (
            "read"
            if tool in (STATUS_TOOL, REPORT_TOOL)
            else "cancel"
            if tool == CANCEL_TOOL
            else "run"
        )
        if not caller.may(needed):
            return _denied(caller, caller.lacking(needed), needed)
        try:
            if tool == CANCEL_TOOL:
                return await _cancel(loom, RunId(str(run)), session_id, caller)
            if tool == REPORT_TOOL:
                return await _report(loom, run_id, session_id, caller)
            if tool == STATUS_TOOL:
                found = await loom.result(
                    RunId(str(run)), session_id=session_id, tenant_id=caller.tenant
                )
                if not caller.allows(found.agent):
                    return _denied(caller, caller.lacking("read", found.agent), "read", found.agent)
                # Relire, c'est lire le journal : sans `read_content`, le
                # statut et les coûts passent, la correspondance non (J5.2a) ;
                # et le rapport ne nomme que les agents que la clé peut lire.
                shown = found.restricted(caller.allows)
                return answer(shown.masked() if caller.masks else shown, ran=False)
            if not caller.allows(tool, "run"):
                return _denied(caller, caller.lacking("run", tool), "run", tool)
            _published(loom, tool, caller.tenant)
        except (UnknownAgent, UnknownRun) as exc:
            return _refused(str(exc.args[0]))
        except AccessDenied as exc:
            audit.refused(exc, access="mcp", action=tool)
            return _refused(str(exc))
        attachments = await attachments_of(caller).read(arguments.get("attachments"))
        message = str(arguments["message"])
        # Ce qu'une clé lance, elle le reçoit : la réponse d'un run n'est
        # jamais masquée, c'est la relecture qui demande la portée.
        try:
            ran = await _run(
                server, loom, tool, message, attachments, session_id, caller.tenant, caller.actor
            )
        except AccessDenied as exc:
            audit.refused(exc, access="mcp", action=tool)
            return _refused(str(exc))
        return answer(ran)

    return server


async def _run(
    server: Server[object, Any],
    loom: Loom,
    agent: str,
    message: str,
    attachments: list[Attachment],
    session_id: SessionId | None,
    tenant: TenantId,
    user: Actor,
) -> RunResult:
    """Fait tourner le run ; suit sa progression et fait trancher, si le client le sait.

    L'élicitation agit pour l'utilisateur, qui a lancé le run : elle ne
    tranche que ce qu'il peut s'approuver, et le reste attend en pause
    (§ 6.2) ; elle ne répond que s'il a ``answer``. C'est le SDK qui l'applique.
    """
    ctx = server.request_context
    token = ctx.meta.progressToken if ctx.meta is not None else None
    approver = _elicited(ctx)
    answerer = _answering(ctx)
    if token is None:
        return await loom.run(
            agent,
            message,
            attachments=attachments,
            session_id=session_id,
            approver=approver,
            tenant=tenant,
            answerer=answerer,
            user=user,
        )
    # Sans session, le run a son identifiant d'avance ; avec, le message peut répondre
    # à une question qui attend, et le run suivi est alors celui qui l'a posée
    # (wait_answer) : son identifiant se lit sur ses événements.
    run_id = new_run_id() if session_id is None else None
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
        answerer=answerer,
        user=user,
    ):
        if isinstance(item, Event) and run_id is None:
            run_id = item.root_run_id
        if isinstance(item, Event) and (line := progress.line(item)) is not None:
            sent += 1
            await ctx.session.send_progress_notification(
                token, sent, message=line, related_request_id=str(ctx.request_id)
            )
    if run_id is None:
        raise RuntimeError("le flux du run n'a rendu aucun événement")
    return await loom.result(run_id, session_id=session_id, tenant_id=tenant)


def _answering(ctx: RequestContext[Any, Any, Any]) -> Answerer | None:
    """Répondeur en ligne bâti sur l'elicitation (wait_answer), si le client la déclare.

    Un formulaire MCP n'a qu'un niveau de propriétés, de types simples : une
    question à choix devient une liste, avec un champ libre à côté ; plusieurs
    choix s'écrivent dans un texte, séparés par « ; ». Formulaire refusé ou
    fermé : la question reste posée, et le run se met en pause.
    """
    capable = types.ClientCapabilities(elicitation=types.ElicitationCapability())
    if not ctx.session.check_client_capability(capable):
        return None
    client = ctx.session.client_params
    by = f"mcp:{client.clientInfo.name}" if client is not None else "mcp"

    async def respond(pending: PendingQuestion) -> QuestionReply | NoAnswer:
        schema, fields = _question_form(pending)
        # Le message est ce que le client montre d'abord : les questions elles-mêmes.
        intro = "\n".join(question.question for question in pending.questions)
        given = await ctx.session.elicit_form(intro, schema, related_request_id=str(ctx.request_id))
        if given.action != "accept":
            return NoAnswer(reason=_CLOSED[given.action])
        content: dict[str, FormValue] = given.content or {}
        answers: list[tuple[str, ...]] = []
        for (choice, free), question in zip(fields, pending.questions, strict=True):
            text = str(content.get(free) or "").strip()
            picked = str(content.get(choice) or "").strip() if choice else ""
            value = text or picked
            if not value:
                return NoAnswer(reason="formulaire incomplet")
            parts = [p.strip() for p in value.split(";")] if question.multiple else [value]
            answers.append(tuple(p for p in parts if p))
        return QuestionReply(answers=tuple(answers), by=by)

    return respond


def _question_form(
    pending: PendingQuestion,
) -> tuple[dict[str, Any], list[tuple[str | None, str]]]:
    """Le formulaire d'elicitation des questions, et ses champs (choix, texte) par question."""
    properties: dict[str, Any] = {}
    fields: list[tuple[str | None, str]] = []
    for number, question in enumerate(pending.questions, start=1):
        title = question.header or f"Question {number}"
        choice: str | None = None
        if question.options and not question.multiple:
            choice = f"choix_{number}"
            properties[choice] = {
                "type": "string",
                "title": title,
                "description": question.question,
                "enum": [option.label for option in question.options],
            }
        free = f"reponse_{number}"
        hint = (
            "Autre réponse (prioritaire sur le choix)"
            if choice
            else question.question
            + (
                " — choix : " + ", ".join(o.label for o in question.options) + " (séparés par ;)"
                if question.options
                else ""
            )
        )
        # À côté d'une liste, le champ libre a son propre titre : sinon deux champs
        # portaient le même intitulé (« Client », « Client »).
        free_title = f"{title} : autre réponse" if choice else title
        properties[free] = {"type": "string", "title": free_title, "description": hint}
        fields.append((choice, free))
    return {"type": "object", "properties": properties}, fields


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
    loom: Loom,
    *,
    user: str,
    name: str = SERVER_NAME,
) -> None:
    """Sert l'instance au nom de ``user``, sur l'entrée et la sortie standard."""
    server = create_server(loom, name=name, user=user)
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
    if result.fallback:
        content.append(types.TextContent(type="text", text=FALLBACK_NOTE))
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
        "fallback": result.fallback,
        "report": result.report.model_dump(mode="json") if result.report else None,
        "verdicts": [verdict.model_dump(mode="json") for verdict in result.verdicts],
        "skipped_judges": [skip.model_dump(mode="json") for skip in result.skipped_judges],
        "pending_approvals": [
            approval.model_dump(mode="json") for approval in result.pending_approvals
        ],
        "pending_questions": [
            question.model_dump(mode="json") for question in result.pending_questions
        ],
        "artifacts": [
            artifact.model_dump(mode="json", exclude_none=True) for artifact in result.artifacts
        ],
    }


def _text(result: RunResult) -> str:
    """La réponse ; pour un échec, ce qui l'a arrêté ; pour une pause, ce qui attend."""
    if result.status is RunStatus.FAILED:
        return f"Échec de l'agent {result.agent} : {result.error}"
    if result.pending_approvals and result.pending_questions:
        # Les deux attentes se cumulent : rendre l'approbation seule cachait la
        # question, que le client découvrait seulement après avoir fait approuver
        # (docs/multi-questions.md, § 6.1).
        return f"{_awaiting(result)}\n\n{_asking(result)}"
    if result.pending_approvals:
        return _awaiting(result)
    if result.pending_questions:
        return _asking(result)
    return result.text or f"Run {result.run_id} : {result.status}"


def _awaiting(result: RunResult) -> str:
    """Ce qu'un run arrêté sur une approbation dit au client : quoi, et comment reprendre."""
    asked = ", ".join(
        f"{a.tool_name} ({a.call_id})" + (f" — {a.reason}" if a.reason else "")
        for a in result.pending_approvals
    )
    return (
        f"Run {result.run_id} en attente d'approbation : {asked}. "
        "Un humain doit trancher (API REST ou « loom approve »), "
        f"puis {STATUS_TOOL} relit le run "
        f"(run_id={result.run_id}, session_id={result.session_id})."
    )


def _asking(result: RunResult) -> str:
    """Ce qu'un run arrêté sur une question dit au client : la question, et comment répondre.

    Le client est souvent un modèle, qui lit ce texte et rappelle l'agent. Avec
    plusieurs questions dans un même appel, chacune est numérotée avec son
    intitulé, ses choix séparés par « ; » (un libellé contient souvent une
    virgule), et la consigne demande les réponses en un seul message. Une
    question seule garde son texte d'avant.
    """
    total = sum(len(pending.questions) for pending in result.pending_questions)
    several = any(len(pending.questions) > 1 for pending in result.pending_questions)
    lines: list[str] = []
    for pending in result.pending_questions:
        count = len(pending.questions)
        for number, question in enumerate(pending.questions, start=1):
            if count > 1:
                header = (question.header or "").strip()
                named = f"Question {number}/{count}" + (f" · {header}" if header else "")
                choices = " ; ".join(o.label for o in question.options)
                multiple = ", plusieurs possibles" if question.multiple and choices else ""
                lines.append(
                    f"{named} : {question.question}"
                    + (f" (choix{multiple} : {choices})" if choices else "")
                )
            else:
                choices = ", ".join(o.label for o in question.options)
                lines.append(question.question + (f" (choix : {choices})" if choices else ""))
    said = f"{result.text}\n\n" if result.text else ""
    if several:
        tail = (
            f"\n\n[L'agent attend la réponse de l'utilisateur aux {total} questions : rappeler "
            f"{result.agent} avec session_id={result.session_id} et les réponses en un seul "
            "message.]"
        )
    else:
        tail = (
            f"\n\n[L'agent attend la réponse de l'utilisateur : rappeler {result.agent} avec "
            f"session_id={result.session_id} et la réponse en message.]"
        )
    return said + "\n".join(lines) + tail


async def _report(
    loom: Loom,
    run_id: RunId | None,
    session_id: SessionId | None,
    caller: Caller,
) -> types.CallToolResult:
    """Consommation d'un run ou d'une session : le rapport en texte et en structuré.

    Comme en REST, le droit vaut pour chaque agent dont un run figure au rapport :
    une clé limitée à certains agents ne lit pas la consommation des autres.
    """
    if run_id is None and session_id is None:
        return _refused("Donner un run_id ou un session_id")
    report = await loom.report(run_id, session_id=session_id, tenant_id=caller.tenant)
    if not report.runs:
        return _refused(f"Session {session_id} inconnue")
    for agent in dict.fromkeys(run.agent for run in report.runs):
        if not caller.allows(agent):
            return _denied(caller, caller.lacking("read", agent), "read", agent)
    return types.CallToolResult(
        content=[types.TextContent(type="text", text="\n".join(render(report)))],
        structuredContent=report.model_dump(mode="json"),
    )


async def _cancel(
    loom: Loom,
    run_id: RunId,
    session_id: SessionId | None,
    caller: Caller,
) -> types.CallToolResult:
    """Arrête un run : l'agent du run décide du droit, comme en REST.

    Il faut ``cancel`` sur **son** agent, vérifié après avoir lu de quel agent
    il est ; c'est l'utilisateur de l'appelant qui signe l'arrêt — jamais un
    nom que le modèle écrirait (D-5). Un run déjà terminé n'est pas une
    erreur : il n'y avait rien à arrêter, et la réponse le dit.
    """
    state = await loom.state(run_id, session_id=session_id, tenant_id=caller.tenant)
    if not caller.allows(state.agent, "cancel"):
        return _denied(caller, caller.lacking("cancel", state.agent), "cancel", state.agent)
    try:
        stopped = await loom.cancel(
            run_id, session_id=session_id, tenant_id=caller.tenant, user=caller.actor
        )
    except AccessDenied as exc:
        audit.refused(exc, access="mcp", action=CANCEL_TOOL)
        return _refused(str(exc))
    said = "arrêté" if stopped else "déjà terminé, rien à arrêter"
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=f"Run {run_id} : {said}.")],
        structuredContent={"run_id": run_id, "cancelled": stopped},
    )


def _always(caller: Caller) -> CallerSource:
    """L'appelant de toutes les requêtes d'un serveur en stdio : son utilisateur."""
    return lambda: caller


def _denied(
    caller: Caller, message: str, permission: Permission, agent: str | None = None
) -> types.CallToolResult:
    """Un refus d'accès : écrit au journal d'audit (D-9), puis rendu comme une erreur d'outil."""
    audit.denied(
        message,
        access="mcp",
        user=caller.user,
        via=caller.actor.via,
        key=caller.key.id if caller.key is not None else None,
        permission=permission,
        agent=agent,
        tenant=caller.tenant,
    )
    return _refused(message)


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
