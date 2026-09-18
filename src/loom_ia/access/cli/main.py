# SPDX-License-Identifier: Apache-2.0
"""Ligne de commande ``loom`` (N4).

    loom validate                  vérifie la config et monte les agents
    loom run demo "Bonjour"        lance un run (``--stream`` pour le direct)
    loom resume <run_id>           reprend un run interrompu
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
from typing import Final

from loom_ia.access.api import Loom, RunResult, StreamItem, UnknownRun
from loom_ia.agents.registry import UnknownAgent
from loom_ia.config import ConfigError, config_json_schema, load_config
from loom_ia.config.keys import fingerprint, new_api_key
from loom_ia.core.events import Event, ToolCalled, ToolCompleted
from loom_ia.core.model import RunId, SessionId, TextDelta, new_run_id
from loom_ia.runtime import apply_logging, load_registry

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
    except (UnknownAgent, UnknownRun) as error:
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

    validate = commands.add_parser("validate", help="vérifie la config et monte les agents")
    validate.set_defaults(handler=cmd_validate)

    run = commands.add_parser("run", help="lance un run et attend sa fin")
    run.add_argument("agent")
    run.add_argument("message")
    run.add_argument("--stream", action="store_true", help="affiche la réponse au fil de l'eau")
    run.add_argument("--json", action="store_true", help="affiche le résultat en JSON")
    run.add_argument("--session", type=str, default=None, help="journal auquel rattacher le run")
    run.add_argument("--run-id", type=str, default=None, help="identifiant choisi pour le run")
    run.set_defaults(handler=cmd_run)

    resume = commands.add_parser("resume", help="reprend un run interrompu")
    resume.add_argument("run_id")
    resume.add_argument("--json", action="store_true", help="affiche le résultat en JSON")
    resume.add_argument("--session", type=str, default=None, help="journal du run")
    resume.set_defaults(handler=cmd_resume)

    serve = commands.add_parser("serve", help="sert l'API REST")
    serve.add_argument("--host", default=None)
    serve.add_argument("--port", type=int, default=None)
    serve.set_defaults(handler=cmd_serve)

    mcp = commands.add_parser("mcp", help="sert les agents en MCP, sur stdio")
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
    events = config.storage.events
    journal = f"{events.backend} ({events.path})" if events.path else events.backend
    keys = ", ".join(key.id for key in config.security.api_keys)
    print(f"Config     : {args.config}")
    print(f"Modèles    : {_listed(spec.id for spec in config.models)}")
    print(f"Agents     : {_listed(agent.name for agent in config.agents)}")
    print(f"Outils     : {_listed(registry.names)}")
    print(f"Journal    : {journal}")
    print(f"Clés d'API : {keys or 'aucune (API REST ouverte)'}")

    async with Loom(config, registry=registry) as loom:
        for spec in config.agents:
            context = loom.context(spec.name)
            roles = "".join(f", rôle {role.name} ({role.model})" for role in spec.roles)
            print(
                f"  {spec.name} : modèle {context.model_spec.id}, {len(spec.tools)} outil(s){roles}"
            )
    print(f"\n{len(config.agents)} agent(s) monté(s) sans erreur.")
    return OK


def cmd_run(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    apply_logging(config)
    session = SessionId(args.session) if args.session else None
    run_id = RunId(args.run_id) if args.run_id else new_run_id()

    async def go() -> RunResult:
        async with Loom(config) as loom:
            if args.stream:
                async for item in loom.stream(
                    args.agent, args.message, session_id=session, run_id=run_id
                ):
                    _show(item)
                print()
                state = await loom.state(run_id, session_id=session)
                if state.terminal_call_id is not None and state.output is not None:
                    # Sortie d'un outil terminal : elle n'est pas passée par le flux du modèle.
                    print(state.output.text)
                return await loom.result(run_id, session_id=session)
            return await loom.run(args.agent, args.message, session_id=session, run_id=run_id)

    return _report(asyncio.run(go()), as_json=args.json, quiet=args.stream)


def cmd_resume(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    apply_logging(config)
    session = SessionId(args.session) if args.session else None

    async def go() -> RunResult:
        async with Loom(config) as loom:
            return await loom.resume(RunId(args.run_id), session_id=session)

    return _report(asyncio.run(go()), as_json=args.json)


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
            await run_stdio(loom)

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


def _show(item: StreamItem) -> None:
    """Un morceau de flux ou un événement, pendant un run suivi en direct."""
    if isinstance(item, TextDelta):
        sys.stdout.write(item.text)
        sys.stdout.flush()
    elif isinstance(item, Event) and isinstance(item.payload, ToolCalled):
        print(f"\n· {item.payload.tool_name}({_arguments(item.payload)})", file=sys.stderr)
    elif isinstance(item, Event) and isinstance(item.payload, ToolCompleted):
        issue = " (erreur)" if item.payload.output.is_error else ""
        print(f"· {item.payload.tool_name} : fait{issue}", file=sys.stderr)


def _arguments(called: ToolCalled) -> str:
    return ", ".join(f"{name}={value!r}" for name, value in called.arguments.items())


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
        if result.error:
            print(f"Erreur     : {result.error}", file=sys.stderr)
    return OK if result.ok else FAILED


def _listed(names: Iterable[str]) -> str:
    return ", ".join(names) or "aucun"


def _message(error: Exception) -> str:
    return str(error.args[0]) if error.args else str(error)
