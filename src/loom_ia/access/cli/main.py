# SPDX-License-Identifier: Apache-2.0
"""Ligne de commande ``loom`` (N4).

    loom validate                  vérifie la config et monte les agents
    loom run demo "Bonjour"        lance un run (``--stream`` pour le direct,
                                   ``--attach photo.jpg`` pour joindre une image)
    loom resume <run_id>           reprend un run interrompu
    loom approve <run_id>          autorise ce que le run attend, et le reprend
    loom reject <run_id>           refuse ce que le run attend, et le reprend
    loom serve                     sert l'API REST
    loom mcp                       sert les agents en MCP, sur stdio
    loom keys create <nom>         fabrique une clé d'API
    loom schema                    JSON Schema du fichier de configuration

``--config`` désigne le fichier de configuration (``./loom.yaml`` par
défaut). Les codes de sortie : 0 tout va bien, 1 le run a échoué, 2 la
configuration ou la demande est en cause.
"""

import argparse
import asyncio
import json
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Final, cast

from pydantic import JsonValue

from loom_ia.access.api import (
    AgentNotAllowed,
    Loom,
    RunResult,
    SessionDeletion,
    StreamItem,
    UnknownApproval,
    UnknownRun,
    UnknownSession,
)
from loom_ia.access.progress import Progress, notes
from loom_ia.agents.registry import UnknownAgent
from loom_ia.agents.spec import AgentSpec
from loom_ia.config import ConfigError, config_json_schema, load_config
from loom_ia.config.keys import fingerprint, new_api_key
from loom_ia.core.events import Event
from loom_ia.core.model import (
    DEFAULT_TENANT,
    JUDGES_MODES,
    Attachment,
    Budgets,
    RunId,
    SessionId,
    StreamReset,
    TenantId,
    TextDelta,
    new_run_id,
)
from loom_ia.core.ports import Policy, SessionRecord, SourceContext, Tool
from loom_ia.engine import ToolExecutor
from loom_ia.runtime import apply_logging, load_registry
from loom_ia.tenancy import Tenant, UnknownTenant
from loom_ia.usage import UsageReport
from loom_ia.usage import render as render_report

PROG: Final = "loom"
DEFAULT_CONFIG: Final = Path("loom.yaml")
MISSING_EXTRA: Final = "{what} demande l'extra '{extra}' : uv sync --extra {extra}"

# Codes de sortie.
OK: Final = 0
FAILED: Final = 1
REFUSED: Final = 2


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except ConfigError as error:
        print(f"Configuration : {error}", file=sys.stderr)
        return REFUSED
    except (UnknownAgent, UnknownApproval, UnknownRun, UnknownTenant) as error:
        print(_message(error), file=sys.stderr)
        return REFUSED
    except AgentNotAllowed as error:
        print(_message(error), file=sys.stderr)
        return REFUSED
    except ValueError as error:
        print(f"Demande refusée : {error}", file=sys.stderr)
        return REFUSED
    except KeyboardInterrupt:
        print("Interrompu.", file=sys.stderr)
        return FAILED


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=PROG, description="Agents loom en ligne de commande")
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG,
        help=f"fichier de configuration (défaut : {DEFAULT_CONFIG})",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    def tenanted(command: argparse.ArgumentParser) -> argparse.ArgumentParser:
        """Ajoute ``--tenant`` : au nom de quel client la commande agit (L1)."""
        command.add_argument(
            "--tenant",
            type=str,
            default=None,
            help="client au nom duquel agir (défaut : celui de la config, sinon 'default')",
        )
        return command

    validate = commands.add_parser("validate", help="vérifie la config et monte les agents")
    validate.set_defaults(handler=cmd_validate)

    run = tenanted(commands.add_parser("run", help="lance un run et attend sa fin"))
    run.add_argument("agent")
    run.add_argument("message")
    run.add_argument("--stream", action="store_true", help="affiche la réponse au fil de l'eau")
    run.add_argument("--json", action="store_true", help="affiche le résultat en JSON")
    run.add_argument("--session", type=str, default=None, help="journal auquel rattacher le run")
    run.add_argument("--run-id", type=str, default=None, help="identifiant choisi pour le run")
    run.add_argument(
        "--attach",
        type=Path,
        action="append",
        default=None,
        metavar="FICHIER",
        help="image jointe à la demande (répétable)",
    )
    run.add_argument(
        "--judges",
        choices=JUDGES_MODES,
        default="auto",
        help="juges : selon leur 'when' (auto), tous (force) ou aucun (skip)",
    )
    run.set_defaults(handler=cmd_run)

    resume = tenanted(commands.add_parser("resume", help="reprend un run interrompu"))
    resume.add_argument("run_id")
    resume.add_argument("--json", action="store_true", help="affiche le résultat en JSON")
    resume.add_argument("--session", type=str, default=None, help="journal du run")
    resume.set_defaults(handler=cmd_resume)

    for verbe, aide in (
        ("approve", "autorise un appel que le run attend"),
        ("reject", "refuse un appel que le run attend"),
    ):
        decision = tenanted(commands.add_parser(verbe, help=aide))
        decision.add_argument("run_id")
        decision.add_argument(
            "--call",
            type=str,
            default=None,
            metavar="CALL_ID",
            help="appel visé ; sans lui, tout ce que le run attend est tranché",
        )
        decision.add_argument(
            "--by",
            type=str,
            default=None,
            metavar="NOM",
            help="qui tranche : inscrit au journal, et c'est tout l'audit qu'il y aura",
        )
        decision.add_argument("--reason", type=str, default="", help="motif de la décision")
        if verbe == "approve":
            decision.add_argument(
                "--arguments",
                type=str,
                default=None,
                metavar="JSON",
                help="arguments corrigés de l'appel (objet JSON) ; demande --call",
            )
        decision.add_argument("--session", type=str, default=None, help="journal du run")
        decision.add_argument(
            "--no-wait",
            action="store_true",
            help="écrit la décision et sort, sans piloter la reprise",
        )
        decision.add_argument("--json", action="store_true", help="affiche le résultat en JSON")
        decision.set_defaults(handler=cmd_decide, verdict=verbe)

    serve = commands.add_parser("serve", help="sert l'API REST")
    serve.add_argument("--host", default=None)
    serve.add_argument("--port", type=int, default=None)
    serve.set_defaults(handler=cmd_serve)

    mcp = tenanted(commands.add_parser("mcp", help="sert les agents en MCP, sur stdio"))
    mcp.set_defaults(handler=cmd_mcp)

    keys = commands.add_parser("keys", help="clés d'API de l'accès REST")
    actions = keys.add_subparsers(dest="action", required=True)
    create = actions.add_parser("create", help="fabrique une clé et son empreinte")
    create.add_argument("id", help="nom de la clé dans la configuration")
    create.add_argument(
        "--scope",
        action="append",
        default=None,
        help="portée accordée (répétable ; défaut : run et read)",
    )
    create.add_argument(
        "--agent",
        action="append",
        default=None,
        help="agent autorisé (répétable ; défaut : tous)",
    )
    create.set_defaults(handler=cmd_keys_create)

    report = commands.add_parser(
        "report", help="consommation d'un run (et de ses sous-runs) ou d'une session"
    )
    report.add_argument("run_id", nargs="?", default=None)
    report.add_argument("--session", type=str, default=None, help="journal du run, ou session")
    report.add_argument("--json", action="store_true", help="affiche le rapport en JSON")
    report.set_defaults(handler=cmd_report)

    sessions = commands.add_parser(
        "sessions", help="journaux de session : lister, exporter, supprimer"
    )
    session_actions = sessions.add_subparsers(dest="action", required=True)

    listing = session_actions.add_parser(
        "list", help="sessions du journal, la plus récente d'abord"
    )
    listing.add_argument("--json", action="store_true", help="affiche la liste en JSON")
    listing.set_defaults(handler=cmd_sessions_list)

    export = session_actions.add_parser(
        "export", help="écrit les événements d'une session en JSONL"
    )
    export.add_argument("session_id")
    export.add_argument(
        "--out",
        type=Path,
        default=None,
        metavar="FICHIER",
        help="fichier de sortie (défaut : la sortie standard)",
    )
    export.set_defaults(handler=cmd_sessions_export)

    remove = session_actions.add_parser(
        "delete", help="supprime une session : journal, fichiers et clés (RGPD)"
    )
    remove.add_argument("session_id")
    remove.add_argument("--yes", action="store_true", help="ne demande pas confirmation")
    remove.set_defaults(handler=cmd_sessions_delete)

    schema = commands.add_parser("schema", help="JSON Schema du fichier de configuration")
    schema.set_defaults(handler=cmd_schema)
    return parser


# --- Commandes ---------------------------------------------------------------


def cmd_validate(args: argparse.Namespace) -> int:
    return asyncio.run(_validate(args))


async def _validate(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    apply_logging(config)
    registry = load_registry(config)
    storage = config.storage
    events = storage.events
    journal = f"{events.backend} ({events.path})" if events.path else events.backend
    files = storage.artifacts_path
    artifacts = f"{storage.artifacts_backend} ({files})" if files else storage.artifacts_backend
    keys = ", ".join(key.id for key in config.security.api_keys)
    print(f"Config     : {args.config}")
    print(f"Modèles    : {_listed(spec.id for spec in config.models)}")
    print(f"Agents     : {_listed(agent.name for agent in config.agents)}")
    named = [(name, registry.get(name)) for name in registry.names]
    print(f"Outils     : {_listed(name for name, obj in named if isinstance(obj, Tool))}")
    policies = [name for name, obj in named if isinstance(obj, Policy)]
    if policies:
        print(f"Politiques : {_listed(policies)}")
    print(f"Journal    : {journal}")
    print(f"Artefacts  : {artifacts}")
    magasin = storage.idempotency
    cles = f"{magasin.backend} ({magasin.path})" if magasin.path else magasin.backend
    print(f"Idempotence: {cles}")
    print(f"Clés d'API : {keys or 'aucune (API REST ouverte)'}")
    if config.tenants:
        print(f"Clients    : {_listed(tenant.id for tenant in config.tenants)}")

    mounted = 0
    async with Loom(config, registry=registry) as loom:
        for tenant_id in config.tenant_ids:
            tenant = loom.tenant(tenant_id)
            if config.tenants:
                print(f"\n  client {tenant_id}")
                for line in _tenant_lines(tenant):
                    print(f"    {line}")
            for spec in config.agents:
                if not tenant.allows(spec.name):
                    continue
                mounted += 1
                await _show_agent(loom, tenant_id, spec, indent="  " if config.tenants else "")
    print(f"\n{mounted} agent(s) monté(s) sans erreur.")
    return OK


def _tenant_lines(tenant: Tenant) -> list[str]:
    """Ce qu'un client surcharge, en quelques lignes lisibles (L1, #34)."""
    spec = tenant.spec
    if spec is None:
        return []
    lines: list[str] = []
    if spec.agents:
        lines.append(f"agents : {_listed(spec.agents)}")
    if spec.models:
        lines.append(f"modèles : {', '.join(f'{a} → {b}' for a, b in spec.models.items())}")
    if spec.secrets:
        lines.append(f"secrets : {', '.join(f'{a} → {b}' for a, b in spec.secrets.items())}")
    if spec.tools_deny:
        lines.append(f"outils retirés : {_listed(spec.tools_deny)}")
    if spec.approvals:
        lines.append(f"approbations : {', '.join(f'{a} : {b}' for a, b in spec.approvals.items())}")
    if spec.variables:
        lines.append(f"variables : {_listed(spec.variables)}")
    if spec.storage is not None:
        events = spec.storage.events
        where = f" ({events.path})" if events.path else ""
        lines.append(f"stockage : {events.backend}{where}")
    return lines or ["rien de surchargé"]


async def _show_agent(loom: Loom, tenant_id: TenantId, spec: AgentSpec, *, indent: str) -> None:
    """Une fiche d'agent, montée pour un client."""
    config = loom.config
    context = loom.context(spec.name, tenant_id)
    roles = "".join(f", rôle {role.name} ({_chain(role.chain)})" for role in spec.roles)
    subagents = "".join(f", sous-agent {ref.tool_name} ({ref.agent})" for ref in spec.subagents)
    delay = f", délai {spec.timeout:g} s" if spec.timeout is not None else ""
    print(
        f"{indent}  {spec.name} : modèle {_chain(spec.main.chain)}, "
        f"{len(spec.python_tools)} outil(s) Python{roles}{subagents}{delay}"
    )
    for bound in context.policies.bound:
        print(f"{indent}    politique {bound.name} : {', '.join(sorted(bound.points))}")
    budgets = config.budget_of(spec.name)
    if budgets.limited:
        print(f"{indent}    budget : {_budget_line(budgets)}")
    for name, role, judge in spec.judges:
        target = f"rôle {role.name}" if role is not None else "réponse finale"
        sample = f", sample {judge.when.sample:g}" if judge.when.sample < 1 else ""
        print(
            f"{indent}    juge {name} ({target}) : modèle {_chain(judge.chain)}, "
            f"{len(judge.criteria)} critère(s){sample}"
        )
    await _show_sources(spec.name, context.tools)


def _chain(models: tuple[str, ...]) -> str:
    """Modèle et ses secours : ``M3_MAIN → SONNET``."""
    return " → ".join(models)


def cmd_run(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    apply_logging(config)
    session = SessionId(args.session) if args.session else None
    tenant = _tenant(args)
    run_id = RunId(args.run_id) if args.run_id else new_run_id()
    paths: list[Path] = args.attach or []
    try:
        attachments = [Attachment.from_path(path) for path in paths]
    except OSError as error:
        print(f"Pièce jointe illisible : {error}", file=sys.stderr)
        return REFUSED

    async def go() -> RunResult:
        async with Loom(config) as loom:
            if args.stream:
                live = _Live()
                async for item in loom.stream(
                    args.agent,
                    args.message,
                    attachments=attachments,
                    session_id=session,
                    run_id=run_id,
                    judges=args.judges,
                    tenant=tenant,
                ):
                    live.show(item)
                print()
                state = await loom.state(run_id, session_id=session, tenant_id=tenant)
                live = loom.context(args.agent, tenant).stream_output == "live"
                if live and state.replaced_output is not None and state.output is not None:
                    # Réponse remplacée par une politique après sa diffusion.
                    print(f"[Réponse retenue]\n{state.output.text}")
                return await loom.result(run_id, session_id=session, tenant_id=tenant)
            return await loom.run(
                args.agent,
                args.message,
                attachments=attachments,
                session_id=session,
                run_id=run_id,
                judges=args.judges,
                tenant=tenant,
            )

    return _report(asyncio.run(go()), as_json=args.json, quiet=args.stream)


def cmd_resume(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    apply_logging(config)
    session = SessionId(args.session) if args.session else None

    async def go() -> RunResult:
        async with Loom(config) as loom:
            return await loom.resume(
                RunId(args.run_id), session_id=session, tenant_id=_tenant(args)
            )

    return _report(asyncio.run(go()), as_json=args.json)


def cmd_decide(args: argparse.Namespace) -> int:
    """Tranche ce qu'un run attend, puis pilote sa reprise (#17).

    La décision met un travail ``resume`` en file **dans cette instance** :
    sans ``--no-wait``, c'est donc ce terminal qui mène le run à son terme et
    en affiche la réponse. Avec, la reprise revient à qui écoute ailleurs —
    ``loom resume``, ou un serveur qui tourne.
    """
    config = load_config(args.config)
    apply_logging(config)
    run_id = RunId(args.run_id)
    session = SessionId(args.session) if args.session else None
    tenant = _tenant(args)
    arguments = getattr(args, "arguments", None)
    if arguments is not None and args.call is None:
        print("--arguments corrige un appel désigné : ajouter --call.", file=sys.stderr)
        return REFUSED
    corrected = _json_object(arguments) if arguments is not None else None
    if arguments is not None and corrected is None:
        return REFUSED

    async def go() -> tuple[tuple[str, ...], RunResult | None]:
        async with Loom(config) as loom:
            if args.verdict == "approve":
                calls = await loom.approve(
                    run_id,
                    call_id=args.call,
                    by=args.by,
                    reason=args.reason,
                    arguments=corrected,
                    session_id=session,
                    tenant_id=tenant,
                )
            else:
                calls = await loom.reject(
                    run_id,
                    call_id=args.call,
                    by=args.by,
                    reason=args.reason,
                    session_id=session,
                    tenant_id=tenant,
                )
            if not calls or args.no_wait:
                return calls, None
            # Le travail de reprise est en file ici : on l'attend.
            await loom.drain()
            return calls, await loom.result(run_id, session_id=session, tenant_id=tenant)

    calls, result = asyncio.run(go())
    if not calls:
        print(f"Run {run_id} : rien n'attend de décision.", file=sys.stderr)
        return REFUSED
    verdict = "Accordé" if args.verdict == "approve" else "Refusé"
    print(f"{verdict} : {', '.join(calls)}", file=sys.stderr)
    if result is None:
        return OK
    return _report(result, as_json=args.json)


def _json_object(text: str) -> dict[str, JsonValue] | None:
    """Objet JSON d'un argument de ligne de commande, ou None avec un message."""
    try:
        value = cast(JsonValue, json.loads(text))
    except json.JSONDecodeError as error:
        print(f"--arguments : JSON invalide ({error.msg}).", file=sys.stderr)
        return None
    if not isinstance(value, dict):
        print("--arguments attend un objet JSON.", file=sys.stderr)
        return None
    return value


def cmd_report(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    apply_logging(config)
    if args.run_id is None and args.session is None:
        print("Donner un run_id ou --session.", file=sys.stderr)
        return REFUSED
    run_id = RunId(args.run_id) if args.run_id else None
    session = SessionId(args.session) if args.session else None

    async def go() -> UsageReport:
        async with Loom(config) as loom:
            return await loom.report(run_id, session_id=session, tenant_id=_tenant(args))

    try:
        report = asyncio.run(go())
    except UnknownRun as error:
        print(error.args[0], file=sys.stderr)
        return FAILED
    if args.json:
        print(report.model_dump_json(indent=2))
    else:
        print("\n".join(render_report(report)))
    return OK


def cmd_sessions_list(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    apply_logging(config)

    async def go() -> list[SessionRecord]:
        async with Loom(config) as loom:
            return await loom.sessions(tenant_id=_tenant(args))

    records = asyncio.run(go())
    if args.json:
        print(
            json.dumps([r.model_dump(mode="json") for r in records], ensure_ascii=False, indent=2)
        )
        return OK
    if not records:
        print("Aucune session.")
        return OK
    for record in records:
        moment = record.updated_at.astimezone().strftime("%Y-%m-%d %H:%M")
        print(f"{record.session_id:<24} {record.last_seq:>6} événements   {moment}")
    return OK


def cmd_sessions_export(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    apply_logging(config)
    session = SessionId(args.session_id)

    async def go() -> list[Event]:
        async with Loom(config) as loom:
            return await loom.export_session(session, tenant_id=_tenant(args))

    try:
        events = asyncio.run(go())
    except UnknownSession as error:
        print(error.args[0], file=sys.stderr)
        return FAILED
    lines = "".join(f"{event.model_dump_json()}\n" for event in events)
    if args.out is None:
        sys.stdout.write(lines)
    else:
        args.out.write_text(lines, encoding="utf-8")
        print(f"{len(events)} événements écrits dans {args.out}")
    return OK


def cmd_sessions_delete(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    apply_logging(config)
    session = SessionId(args.session_id)
    if not args.yes:
        asked = input(
            f"Supprimer définitivement la session {session} "
            "(journal, fichiers et clés d'idempotence) ? [o/N] "
        )
        if asked.strip().lower() not in {"o", "oui", "y", "yes"}:
            print("Rien n'a été supprimé.")
            return REFUSED

    async def go() -> SessionDeletion:
        async with Loom(config) as loom:
            return await loom.delete_session(session, tenant_id=_tenant(args))

    removed = asyncio.run(go())
    if not removed.events and not removed.artifacts and not removed.keys:
        print(f"Session {session} inconnue : rien à supprimer.", file=sys.stderr)
        return FAILED
    print(
        f"Session {session} supprimée : {removed.events} événement(s), "
        f"{removed.artifacts} fichier(s), {removed.keys} clé(s)."
    )
    return OK


def cmd_serve(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    apply_logging(config)
    try:
        from loom_ia.access.http import serve
    except ImportError:
        print(MISSING_EXTRA.format(what="'loom serve'", extra="http"), file=sys.stderr)
        return REFUSED
    http = config.server.http
    host = args.host or http.host
    port = args.port or http.port
    print(f"API REST   : http://{host}:{port}{http.base_path}/v1")
    print(f"Agents     : {_listed(agent.name for agent in config.agents if agent.expose.rest)}")
    serve(Loom(config), host=args.host, port=args.port)
    return OK


def cmd_mcp(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    apply_logging(config)
    try:
        from loom_ia.access.mcp_server import run_stdio
    except ImportError:
        print(MISSING_EXTRA.format(what="'loom mcp'", extra="mcp"), file=sys.stderr)
        return REFUSED

    async def go() -> None:
        # Rien ne doit aller sur stdout : le protocole y passe.
        async with Loom(config) as loom:
            await run_stdio(loom, tenant=_tenant(args) or DEFAULT_TENANT)

    asyncio.run(go())
    return OK


def cmd_keys_create(args: argparse.Namespace) -> int:
    key = new_api_key()
    scopes: list[str] = args.scope or ["run", "read"]
    agents: list[str] = args.agent or []
    print(f"Clé        : {key}")
    print("Elle n'est affichée qu'ici : la config ne garde que son empreinte.\n")
    print("À ajouter dans la configuration :\n")
    print("security:")
    print("  api_keys:")
    print(f"    - id: {args.id}")
    print(f"      hash: {fingerprint(key)}")
    print(f"      scopes: [{', '.join(scopes)}]")
    if agents:
        print(f"      agents: [{', '.join(agents)}]")
    return OK


def cmd_schema(args: argparse.Namespace) -> int:
    print(json.dumps(config_json_schema(), ensure_ascii=False, indent=2))
    return OK


# --- Affichage ---------------------------------------------------------------


class _Live:
    """Un run suivi en direct : le texte du modèle sur stdout, le déroulé sur stderr.

    Les événements des sous-runs arrivent dans le flux ; leurs lignes sont
    décalées selon leur profondeur.
    """

    def __init__(self) -> None:
        self._progress = Progress()
        # Du texte a été écrit sans fin de ligne : la ligne suivante doit en partir.
        self._open = False

    def show(self, item: StreamItem) -> None:
        if isinstance(item, TextDelta):
            sys.stdout.write(item.text)
            sys.stdout.flush()
            if item.text:
                self._open = not item.text.endswith("\n")
            return
        if isinstance(item, StreamReset):
            # Le texte déjà affiché est refusé ou relancé : une nouvelle réponse suit.
            if self._open:
                print(file=sys.stdout, flush=True)
                self._open = False
            print("· réponse reprise", file=sys.stderr)
            return
        if not isinstance(item, Event) or (line := self._progress.line(item)) is None:
            return
        if self._open:
            print(file=sys.stdout, flush=True)
            self._open = False
        print(line, file=sys.stderr)


def _tenant(args: argparse.Namespace) -> TenantId | None:
    """Client demandé par ``--tenant`` ; ``None`` laisse la config décider (#33)."""
    given = getattr(args, "tenant", None)
    return TenantId(given) if given else None


def _budget_line(budgets: Budgets) -> str:
    """Limites d'un agent, en une ligne : « run max_cost 0.05, max_calls 20 ; stop »."""
    parts: list[str] = []
    for scope, limits in (("run", budgets.run), ("session", budgets.session)):
        given = [
            f"{name} {value:g}" for name, value in limits.model_dump().items() if value is not None
        ]
        if given:
            parts.append(f"{scope} {', '.join(given)}")
    return f"{' ; '.join(parts)} ; {budgets.on_exceed}"


async def _show_sources(agent: str, tools: ToolExecutor) -> None:
    """Se connecte aux serveurs MCP de l'agent et liste leurs outils."""
    if not tools.sources:
        return
    context = SourceContext(
        tenant_id=DEFAULT_TENANT,
        session_id=SessionId("validate"),
        run_id=RunId("validate"),
        agent=agent,
    )
    async with tools.opened(context) as opened:
        found = [spec.name for spec in opened.tools.specs if spec.kind == "mcp"]
        print(f"    MCP : {_listed(found)}")
        for missing in opened.unavailable:
            required = " (requis)" if missing.required else ""
            print(f"    MCP {missing.source} indisponible{required} : {missing.error}")


def _report(result: RunResult, *, as_json: bool = False, quiet: bool = False) -> int:
    if as_json:
        print(result.model_dump_json(indent=2, exclude_none=True))
    else:
        if not quiet:
            print(result.text or result.error or "—")
        usage = result.usage
        print(
            f"\nStatut     : {result.status} · itérations : {result.iterations} "
            f"· tokens : {usage.input_tokens}/{usage.output_tokens} "
            f"· coût : {result.cost_usd:.4f} $",
            file=sys.stderr,
        )
        print(f"Run        : {result.run_id}", file=sys.stderr)
        for produced in result.produced:
            print(
                f"Fichier    : {produced.uri} ({produced.media_type}, {produced.size} octets)",
                file=sys.stderr,
            )
        for line in _verdicts(result):
            print(f"Juge       : {line}", file=sys.stderr)
        for asked in result.pending_approvals:
            print(
                f"En attente : {asked.tool_name} ({asked.call_id}) — "
                f"loom approve {result.run_id} --call {asked.call_id}",
                file=sys.stderr,
            )
        if result.unverified:
            print("Vérifiée   : non (gardée malgré son contrat ou son juge)", file=sys.stderr)
        if result.error:
            print(f"Erreur     : {result.error} ({result.error_type})", file=sys.stderr)
    return OK if result.ok else FAILED


def _verdicts(result: RunResult) -> list[str]:
    """Une ligne par verdict ; pour un rôle, le rang de l'appel jugé parmi ceux de son run."""
    calls: dict[tuple[str, str], list[str]] = {}
    lines: list[str] = []
    for verdict in result.verdicts:
        if verdict.target == "output":
            subject = "réponse finale"
        else:
            subject = verdict.target.replace("role:", "rôle ", 1)
        if verdict.call_id is not None:
            seen = calls.setdefault((verdict.run_id, verdict.target), [])
            if verdict.call_id not in seen:
                seen.append(verdict.call_id)
            subject += f", appel {seen.index(verdict.call_id) + 1}"
        outcome = "refusée" if verdict.blocked else "acceptée"
        lines.append(
            f"{verdict.judge} ({subject}), tentative {verdict.attempt} — {outcome} : "
            f"{notes(verdict.criteria)}"
        )
    return lines


def _listed(names: Iterable[str]) -> str:
    return ", ".join(names) or "aucun"


def _message(error: Exception) -> str:
    return str(error.args[0]) if error.args else str(error)
