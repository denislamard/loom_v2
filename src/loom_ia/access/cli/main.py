# SPDX-License-Identifier: Apache-2.0
"""Ligne de commande ``loom`` (N4).

    loom validate                  vérifie la config et monte les agents
    loom run demo "Bonjour"        lance un run (``--stream`` pour le direct,
                                   ``--attach photo.jpg`` pour joindre une image)
    loom resume <run_id>           reprend un run interrompu
    loom replay <run_id>           rejoue un run, à l'identique ou en variante
                                   (``--journal F`` : les runs d'un journal exporté)
    loom inspect <run_id>          relit un run : son arbre, sa réponse, son bilan
    loom eval suite.yaml           joue une suite d'évals, variante par variante
    loom approve <run_id>          autorise ce que le run attend, et le reprend
    loom reject <run_id>           refuse ce que le run attend, et le reprend
    loom serve                     sert l'API REST (``--reload`` : relancé quand
                                   un fichier de la config change)
    loom mcp                       sert les agents en MCP, sur stdio
    loom keys create <nom>         fabrique une clé d'API
    loom worker                    consomme la file des tâches de fond
    loom storage sql               SQL du stockage Postgres déclaré
    loom schema                    JSON Schema du fichier de configuration

``--config`` désigne le fichier de configuration (``./loom.yaml`` par
défaut). Les codes de sortie : 0 tout va bien, 1 le run a échoué, 2 la
configuration ou la demande est en cause. Un refus d'exploitation sort lui aussi
en 2, sur une ligne de ``stderr`` et sans trace : budget ou quota atteint, run
mené par un autre worker, fichier ou réseau inaccessible, entrée standard fermée
quand une réponse était attendue.
"""

import argparse
import asyncio
import json
import math
import os
import signal
import sys
from collections.abc import Callable, Iterable, Sequence
from datetime import UTC, datetime, timedelta
from importlib.util import find_spec
from pathlib import Path
from typing import Final, cast

from pydantic import JsonValue

from loom_ia.access.api import (
    AgentNotAllowed,
    Loom,
    RetentionReport,
    RunResult,
    SessionDeletion,
    StreamItem,
    TenantConsumption,
    UnknownApproval,
    UnknownRun,
    UnknownSession,
)
from loom_ia.access.evals import evaluate
from loom_ia.access.progress import Progress, notes
from loom_ia.access.resources import RUNS as MCP_RUNS
from loom_ia.access.resources import SESSIONS as MCP_SESSIONS
from loom_ia.access.resources import TEMPLATES as MCP_TEMPLATES
from loom_ia.adapters.models.fake import FakeModel
from loom_ia.agents.registry import UnknownAgent
from loom_ia.agents.spec import AgentSpec
from loom_ia.config import ConfigError, LoomConfig, config_json_schema, load_config
from loom_ia.config.compaction import compaction_agent
from loom_ia.config.keys import fingerprint, new_api_key
from loom_ia.config.loader import PROFILE_ENV, chosen_profile
from loom_ia.config.models import (
    PROFILES,
    BusStorage,
    EventsStorage,
    IdempotencyStorage,
    QueueStorage,
)
from loom_ia.config.references import resolve
from loom_ia.core.events import Event
from loom_ia.core.model import (
    DEFAULT_TENANT,
    JUDGES_MODES,
    MCP_PREFIX_SEPARATOR,
    Attachment,
    BudgetPeriod,
    Budgets,
    RunId,
    SessionId,
    StreamReset,
    TenantId,
    TextDelta,
    new_run_id,
)
from loom_ia.core.ports import Policy, SealError, SessionRecord, SourceContext, Tool
from loom_ia.engine import ClaimConflict, ToolExecutor
from loom_ia.replay import (
    Comparison,
    Double,
    EvalError,
    JournalReplay,
    ReplayError,
    ReplayReport,
    RunSide,
    ToolFate,
    fate_label,
    load_suite,
    render_eval,
)
from loom_ia.runtime import (
    apply_logging,
    create_keyring,
    encryption_warnings,
    load_registry,
    postgres_ddl,
    storage_warnings,
)
from loom_ia.runtime.sources import GROUP as SOURCES_GROUP
from loom_ia.runtime.sources import PackagedSource, installed
from loom_ia.telemetry import Trace, render_trace
from loom_ia.tenancy import BudgetExhausted, QuotaExceeded, Tenant, UnknownTenant
from loom_ia.usage import UsageReport, amount
from loom_ia.usage import render as render_report

PROG: Final = "loom"
DEFAULT_CONFIG: Final = Path("loom.yaml")
MISSING_EXTRA: Final = "{what} demande l'extra '{extra}' : uv sync --extra {extra}"

# Codes de sortie.
OK: Final = 0
FAILED: Final = 1
REFUSED: Final = 2
# Délai au-delà duquel `loom validate` ne dit plus qu'une clé expire bientôt.
_BIENTOT: Final = timedelta(days=7)


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
    except BudgetExhausted as error:
        # Rien n'a été lancé ni écrit : la même demande passera quand la période repart.
        print(f"{_message(error)} — réessayer dans {_delay(error.retry_after)}", file=sys.stderr)
        return REFUSED
    except QuotaExceeded as error:
        # Son message dit déjà dans combien de temps réessayer.
        print(_message(error), file=sys.stderr)
        return REFUSED
    except ClaimConflict as error:
        print(
            f"{_message(error)} — ce run est mené ailleurs : réessayer après l'échéance",
            file=sys.stderr,
        )
        return REFUSED
    except EOFError:
        print(
            "Entrée standard fermée : une réponse était attendue "
            "(sans terminal, --yes la donne d'avance).",
            file=sys.stderr,
        )
        return REFUSED
    except OSError as error:
        # Après AgentNotAllowed, qui est une PermissionError.
        print(f"Erreur d'entrée-sortie : {error}", file=sys.stderr)
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
    parser.add_argument(
        "--profile",
        choices=PROFILES,
        default=None,
        help=f"profil actif ; l'emporte sur {PROFILE_ENV} et sur la config",
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
    validate.add_argument(
        "--sans-cles",
        action="store_true",
        help="monte les agents sans créer leurs clients de modèle : "
        "une clé d'API (ou un SDK) absente ne fait pas échouer",
    )
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

    replay = tenanted(
        commands.add_parser(
            "replay",
            help="rejoue un run fini, à l'identique (sans appel) ou en variante, "
            "et dit où il s'écarte",
        )
    )
    replay.add_argument("run_id", nargs="?", default=None)
    replay.add_argument("--session", type=str, default=None, help="journal du run")
    replay.add_argument(
        "--journal",
        type=Path,
        default=None,
        metavar="FICHIER",
        help="rejoue depuis un journal JSONL exporté (sessions export, eval --export), "
        "hors du journal de la config ; sans run_id, chacun de ses runs finis",
    )
    replay.add_argument(
        "--mode",
        choices=("exact", "variant"),
        default="exact",
        help="exact : tout vient du journal ; variant : ce qu'il ne connaît pas part "
        "pour de vrai, et les deux runs sont comparés",
    )
    replay.add_argument(
        "--model",
        action="append",
        default=[],
        metavar="ETAPE=MODELE",
        help="variante : autre modèle déclaré pour une étape (main, un rôle, judge:<nom>)",
    )
    replay.add_argument(
        "--double",
        action="append",
        default=[],
        metavar="OUTIL=REF",
        help="variante : doublure d'un outil (nom enregistré ou module:fonction), "
        "appelée à la place d'un appel absent du journal",
    )
    replay.add_argument(
        "--export",
        type=Path,
        default=None,
        metavar="FICHIER",
        help="garde le journal du rejeu en JSONL (rien n'est écrit dans le journal)",
    )
    replay.add_argument("--json", action="store_true", help="affiche le rapport en JSON")
    replay.set_defaults(handler=cmd_replay)

    inspect = tenanted(
        commands.add_parser(
            "inspect",
            help="relit un run : son arbre (étapes, modèles, outils, sous-runs), "
            "sa réponse et son bilan",
        )
    )
    inspect.add_argument("run_id")
    inspect.add_argument("--session", type=str, default=None, help="journal du run")
    inspect.add_argument(
        "--full", action="store_true", help="arguments et résultats en entier, sans coupe"
    )
    inspect.add_argument(
        "--json", action="store_true", help="la trace en JSON, celle de GET /v1/traces/{run_id}"
    )
    inspect.set_defaults(handler=cmd_inspect)

    evaluation = commands.add_parser(
        "eval",
        help="joue une suite d'évals : des cas et leurs attendus, pour chaque variante, "
        "en mémoire (outils à effets de bord doublés ou refusés)",
    )
    evaluation.add_argument("suite", type=Path, help="fichier YAML de la suite")
    evaluation.add_argument(
        "--case", action="append", default=[], metavar="NOM", help="ne joue que ce cas (répétable)"
    )
    evaluation.add_argument(
        "--variant",
        action="append",
        default=[],
        metavar="NOM",
        help="ne joue que cette variante (répétable)",
    )
    evaluation.add_argument(
        "--export",
        type=Path,
        default=None,
        metavar="DOSSIER",
        help="garde le journal de chaque run, un JSONL par run (<variante>--<cas>--<n>.jsonl)",
    )
    evaluation.add_argument("--json", action="store_true", help="affiche le rapport en JSON")
    evaluation.set_defaults(handler=cmd_eval)

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
    serve.add_argument(
        "--reload",
        action="store_true",
        help=(
            "relance le serveur quand un fichier de la config change (dev ; refusé en prod) ; "
            "une config cassée est refusée et l'ancien process continue de servir"
        ),
    )
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
    create.add_argument(
        "--tenant",
        default=None,
        metavar="CLIENT",
        help="client au nom duquel la clé agit (défaut : default)",
    )
    create.add_argument(
        "--expires",
        default=None,
        metavar="QUAND",
        help="fin de validité : une date (2027-01-01) ou une durée (90j, 12h)",
    )
    create.add_argument(
        "--rate-limit",
        type=int,
        default=None,
        metavar="N",
        help="requêtes par minute accordées à cette clé (défaut : aucune limite)",
    )
    create.set_defaults(handler=cmd_keys_create)

    report = tenanted(
        commands.add_parser(
            "report",
            help="consommation d'un run, d'une session, ou d'un client sur une période",
        )
    )
    report.add_argument("run_id", nargs="?", default=None)
    report.add_argument("--session", type=str, default=None, help="journal du run, ou session")
    report.add_argument(
        "--periode",
        choices=("jour", "mois"),
        default=None,
        help="consommation du client sur la journée ou le mois en cours (J5.1b)",
    )
    report.add_argument("--json", action="store_true", help="affiche le rapport en JSON")
    report.set_defaults(handler=cmd_report)

    sessions = commands.add_parser(
        "sessions", help="journaux de session : lister, exporter, supprimer"
    )
    session_actions = sessions.add_subparsers(dest="action", required=True)

    listing = tenanted(
        session_actions.add_parser("list", help="sessions du journal, la plus récente d'abord")
    )
    listing.add_argument("--json", action="store_true", help="affiche la liste en JSON")
    listing.set_defaults(handler=cmd_sessions_list)

    export = tenanted(
        session_actions.add_parser("export", help="écrit les événements d'une session en JSONL")
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

    remove = tenanted(
        session_actions.add_parser(
            "delete", help="supprime une session : journal, fichiers et clés (RGPD)"
        )
    )
    remove.add_argument("session_id")
    remove.add_argument("--yes", action="store_true", help="ne demande pas confirmation")
    remove.set_defaults(handler=cmd_sessions_delete)

    worker = commands.add_parser(
        "worker", help="consomme la file des tâches de fond (runs, reprises, résumés)"
    )
    worker.add_argument(
        "--jobs",
        type=int,
        default=1,
        metavar="N",
        help="tâches menées de front (défaut : 1)",
    )
    worker.set_defaults(handler=cmd_worker)

    retention = commands.add_parser(
        "retention",
        help="efface les sessions dormantes au-delà de la borne déclarée (RGPD)",
    )
    retention.add_argument(
        "--tenant",
        type=str,
        default=None,
        help="un seul client (défaut : tous les clients déclarés)",
    )
    retention.add_argument(
        "--yes",
        action="store_true",
        help="supprime pour de bon ; sans cette option, rien n'est touché",
    )
    retention.add_argument("--json", action="store_true", help="affiche le rapport en JSON")
    retention.set_defaults(handler=cmd_retention)

    storage = commands.add_parser("storage", help="stockages de service : le SQL à appliquer")
    storage_actions = storage.add_subparsers(dest="action", required=True)
    ddl = storage_actions.add_parser(
        "sql", help="imprime le SQL du stockage Postgres déclaré (tables, rôle, politiques)"
    )
    ddl.set_defaults(handler=cmd_storage_sql)

    schema = commands.add_parser("schema", help="JSON Schema du fichier de configuration")
    schema.set_defaults(handler=cmd_schema)
    return parser


# --- Commandes ---------------------------------------------------------------


def cmd_validate(args: argparse.Namespace) -> int:
    return asyncio.run(_validate(args))


async def _validate(args: argparse.Namespace) -> int:
    config = load_config(args.config, profile=args.profile)
    apply_logging(config)
    registry = load_registry(config)
    storage = config.storage
    journal = _storage_line(storage.events)
    files = storage.artifacts_path
    artifacts = f"{storage.artifacts_backend} ({files})" if files else storage.artifacts_backend
    keys = ", ".join(key.id for key in config.security.api_keys)
    print(f"Config     : {args.config}")
    print(f"Profil     : {_profile_line(config, args.profile)}")
    print(f"Modèles    : {_listed(spec.id for spec in config.models)}")
    if args.sans_cles:
        # Les clients de modèle ne sont pas créés : ce que le montage aurait
        # vérifié d'eux — clé d'API présente, SDK installé — ne l'est pas.
        print("    clients de modèle non créés (--sans-cles) : clés d'API et SDK non vérifiés")
    print(f"Agents     : {_listed(agent.name for agent in config.agents)}")
    named = [(name, registry.get(name)) for name in registry.names]
    print(f"Outils     : {_listed(name for name, obj in named if isinstance(obj, Tool))}")
    policies = [name for name, obj in named if isinstance(obj, Policy)]
    if policies:
        print(f"Politiques : {_listed(policies)}")
    for line in _source_lines(config):
        print(line)
    print(f"Journal    : {journal}")
    print(f"Artefacts  : {artifacts}")
    print(f"Idempotence: {_storage_line(storage.idempotency)}")
    print(f"File       : {_queue_line(storage.queue)}")
    print(f"Bus        : {_bus_line(storage.bus)}")
    print(f"Chiffrement: {_sealing_line(config)}")
    for line in _sealing_report(config):
        print(f"    {line}")
    print(f"Rétention  : {_retention_line(config)}")
    if storage.queue.brokered:
        # Le piège de la file servie : tout se met en file, rien ne tourne.
        print("    les tâches de fond attendent un worker : loom worker")
    for warning in storage_warnings(config):
        print(f"    {warning}")
    print(f"Clés d'API : {keys or 'aucune (API REST ouverte)'}")
    mcp = config.server.mcp
    if mcp.http:
        origines = _listed(mcp.allowed_origins) if mcp.allowed_origins else "aucune déclarée"
        print(f"MCP HTTP   : monté sous {config.server.http.base_path}/mcp, origines : {origines}")
        gabarits = len(MCP_TEMPLATES)
        print(
            f"    ressources en lecture seule : {MCP_RUNS}, {MCP_SESSIONS}, et {gabarits} gabarits"
        )
    for line in _key_lines(config):
        print(f"    {line}")
    if config.triggers:
        base = config.server.http.base_path
        print(f"Portes     : {_listed(trigger.name for trigger in config.triggers)}")
        for trigger in config.triggers:
            print(f"    POST {base}/v1/hooks/{trigger.name} → agent {trigger.agent}")
            details = [f"session {trigger.session}"] if trigger.session else ["session par run"]
            if trigger.delivery_header:
                details.append(f"livraison sur {trigger.delivery_header}")
            else:
                # Sans en-tête de livraison, une plateforme qui réessaie ouvre
                # un second run : le dire ici, c'est le dire avant la panne.
                details.append("aucun en-tête de livraison : une relivraison rouvre un run")
            print(f"    {'':<4}{', '.join(details)}")
    if config.tenants:
        print(f"Clients    : {_listed(tenant.id for tenant in config.tenants)}")

    mounted = 0
    clients = {spec.id: FakeModel(spec) for spec in config.models} if args.sans_cles else None
    async with Loom(config, registry=registry, models=clients) as loom:
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
            if config.sessions.compaction is not None:
                # L'agent interne n'est pas déclaré, mais il est monté au premier
                # résumé : une clé absente pour son modèle se dit ici, pas dans une
                # tâche de fond qui n'échoue pas le run. Il ne compte pas dans les
                # agents montés : ce sont ceux que la config déclare.
                internal = compaction_agent(config.sessions.compaction)
                await _show_agent(loom, tenant_id, internal, indent="  " if config.tenants else "")
    print(f"\n{mounted} agent(s) monté(s) sans erreur.")
    return OK


def _source_lines(config: LoomConfig) -> list[str]:
    """Les sources déclarées, et les points d'entrée installés — lus sans rien importer.

    Rien n'est dit sans source déclarée ni paquet installé : la plupart des
    configs n'en ont pas.
    """
    lines: list[str] = []
    for declared in config.tool_sources:
        lines.append(f"Source     : {declared.name} → point d'entrée {declared.entry_point}")
    found = installed()
    if found or config.tool_sources:
        used = {declared.entry_point for declared in config.tool_sources}
        listed = [
            f"{point.name} ({' '.join(filter(None, (point.package, point.version)))}"
            f"{', utilisé' if point.name in used else ''})"
            for point in found
        ]
        lines.append(f"Paquets    : {_listed(listed)} (groupe {SOURCES_GROUP})")
    return lines


def _profile_line(config: LoomConfig, given: str | None) -> str:
    """Le profil actif, d'où il vient, et ce qu'il change ; ``given`` est celui de l'option.

    Un profil qu'on ne voit pas est un profil qu'on oublie : un déploiement
    qui croit être en prod doit pouvoir le lire ici.
    """
    active, source = chosen_profile(given, config.profile)
    declared = _listed(sorted(config.profiles)) if config.profiles else "aucune"
    if active is None:
        return f"aucun (ni --profile, ni {PROFILE_ENV}, ni 'profile:') ; surcharges : {declared}"
    effet = "les avertissements sont des erreurs" if active == "prod" else "assoupli"
    return f"{active} (par {source}) — {effet} ; surcharges : {declared}"


def _retention_line(config: LoomConfig) -> str:
    """La borne de rétention, et les clients qui n'ont pas la même.

    Une règle qui efface des journaux doit se lire ici : elle ne se rappelle à
    personne avant d'avoir supprimé quelque chose.
    """
    racine = config.storage.retention.events_days
    propres = [
        f"{tenant}: {days} jour(s)" if days is not None else f"{tenant}: aucune"
        for tenant in config.tenant_ids
        if (days := config.retention_days(tenant)) != racine
    ]
    if racine is None and not propres:
        return "aucune (rien ne s'efface)"
    commune = f"sessions effacées après {racine} jour(s)" if racine else "aucune à la racine"
    dit = f"{commune} ; {', '.join(propres)}" if propres else commune
    return f"{dit} — par « loom retention », que la plateforme met à l'heure"


def _sealing_line(config: LoomConfig) -> str:
    """L'état du sceau : quels secrets portent les clés, lequel ferme."""
    declared = config.storage.encryption
    if declared is None:
        return "aucun (contenus en clair au repos)"
    ferme, *ouvrent = declared.keys
    secrets = f"secret {ferme!r} ferme"
    if ouvrent:
        secrets += f", {_listed(ouvrent)} ouvre(nt) encore"
    return f"charges et fichiers scellés — {secrets}"


def _sealing_report(config: LoomConfig) -> list[str]:
    """Une ligne par client — l'empreinte de sa clé ou son absence —, puis les
    avertissements du trousseau.

    L'empreinte est imprimée, jamais la clé : elle suffit à voir que deux
    clients scellent avec la même, ou que celle d'un client a changé. Un
    client sans clé ne fait pas échouer la commande : c'est justement l'état
    d'un client dont on a effacé la clé, et le reste de la config reste à
    valider.
    """
    if config.storage.encryption is None:
        return []
    try:
        keyring = create_keyring(config)
    except ConfigError as error:  # extra 'crypto' absent
        return [str(error)]
    if keyring is None:
        return []
    lines: list[str] = []
    for tenant_id in config.tenant_ids:
        try:
            ciphers = keyring.ciphers(tenant_id)
        except SealError:
            lines.append(f"{tenant_id} : SANS CLÉ")
            continue
        lines.append(f"{tenant_id} : clé {', '.join(cipher.key_id for cipher in ciphers)}")
    return lines + encryption_warnings(config, keyring)


def _storage_line(declared: EventsStorage | IdempotencyStorage) -> str:
    """Un stockage tel que ``loom validate`` l'affiche.

    Le DSN n'est jamais imprimé : seulement le nom de la variable qui le
    porte, et si elle est renseignée ici et maintenant — c'est ce qui manque
    le plus souvent quand un service refuse de démarrer.
    """
    if declared.dsn_env is not None:
        lue = "renseignée" if os.environ.get(declared.dsn_env) else "ABSENTE"
        role = declared.role or "aucun (propriétaire)"
        return f"{declared.backend} (DSN dans {declared.dsn_env} : {lue}, rôle : {role})"
    return f"{declared.backend} ({declared.path})" if declared.path else declared.backend


def _key_lines(config: LoomConfig) -> list[str]:
    """Ce que chaque clé d'API permet, et ce qui cloche (J5.2a, #39)."""
    now = datetime.now(UTC)
    lines: list[str] = []
    for key in config.security.api_keys:
        details = [f"client {key.tenant}", f"portées {', '.join(key.scopes)}"]
        if key.agents:
            details.append(f"agents {_listed(key.agents)}")
        if key.rate_limit is not None:
            details.append(f"débit {key.rate_limit.per_minute}/min")
        if key.expires is not None:
            reste = key.expires - now
            if key.expired(now):
                etat = "EXPIRÉE"
            elif reste <= _BIENTOT:
                etat = f"expire dans {reste.days} j"
            else:
                etat = f"expire le {key.expires:%Y-%m-%d}"
            details.append(etat)
        lines.append(f"{key.id} : {', '.join(details)}")
        if "approve" in key.scopes and "read_content" not in key.scopes:
            # Approuver sans lire, c'est trancher à l'aveugle : les arguments
            # de l'appel en attente lui sont masqués.
            lines.append(f"  {key.id} peut approuver sans lire : ajouter 'read_content'")
    return lines


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
    par_periode = tenant.budget
    if par_periode.limited:
        posees = [
            f"{limit} par {'jour' if kind == 'day' else 'mois'} {amount(limit, value)}"
            for kind in par_periode.periods
            for limit, value in par_periode.limits(kind)
        ]
        lines.append(f"budget : {', '.join(posees)}")
    if tenant.quotas.runs_per_minute is not None:
        lines.append(f"quota : {tenant.quotas.runs_per_minute} run(s) par minute")
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
    config = load_config(args.config, profile=args.profile)
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
    config = load_config(args.config, profile=args.profile)
    apply_logging(config)
    session = SessionId(args.session) if args.session else None

    async def go() -> RunResult:
        async with Loom(config) as loom:
            return await loom.resume(
                RunId(args.run_id), session_id=session, tenant_id=_tenant(args)
            )

    return _report(asyncio.run(go()), as_json=args.json)


def cmd_replay(args: argparse.Namespace) -> int:
    """Rejoue un run ; rend 1 s'il diverge (ou si la variante échoue), 2 s'il ne peut pas l'être.

    Le code de sortie fait de la commande un garde-fou : une config qui ne
    reproduit plus un run de référence se voit en CI, sans lire la sortie. En
    variante, s'écarter est le but : le code dit seulement si la variante est
    allée au bout (0) ou a échoué (1). Avec ``--journal`` (J6.3b), chaque run
    fini du fichier est rejoué ; un seul qui diverge, ou un run inachevé,
    rend 1.
    """
    config = load_config(args.config, profile=args.profile)
    apply_logging(config)
    session = SessionId(args.session) if args.session else None
    try:
        if args.journal is None and args.run_id is None:
            raise ReplayError("nommer le run à rejouer, ou le journal (--journal FICHIER)")
        if args.journal is not None and (args.session or args.tenant):
            raise ReplayError(
                "--session et --tenant ne servent pas avec --journal : le journal porte la "
                "session et le client de chaque run"
            )
        models = _pairs(args.model, "--model", "ETAPE=MODELE")
        references = _pairs(args.double, "--double", "OUTIL=REF")
        registry = load_registry(config)
        doubles: dict[str, Double] = {}
        for name, reference in references.items():
            found = resolve(reference, registry, base_dir=config.base_dir)
            if not callable(found):
                raise ReplayError(f"--double {name}={reference} : ce n'est pas une fonction")
            doubles[name] = found
    except (ReplayError, ConfigError) as error:
        print(f"Rejeu impossible : {error}", file=sys.stderr)
        return REFUSED
    if args.journal is not None:
        return _replay_journal(args, config, models, doubles)

    async def go() -> ReplayReport:
        async with Loom(config) as loom:
            return await loom.replay(
                RunId(args.run_id),
                session_id=session,
                tenant_id=_tenant(args),
                mode=args.mode,
                models=models,
                doubles=doubles,
                export=args.export,
            )

    try:
        report = asyncio.run(go())
    except ReplayError as error:
        print(f"Rejeu impossible : {error}", file=sys.stderr)
        return REFUSED
    if args.json:
        print(json.dumps(report.as_json(), ensure_ascii=False, indent=2))
        return _replay_code(report)
    return _print_replay(report, args.export)


def _replay_journal(
    args: argparse.Namespace,
    config: LoomConfig,
    models: dict[str, str],
    doubles: dict[str, Double],
) -> int:
    """``loom replay --journal`` : les runs d'un fichier, l'un après l'autre, puis le bilan."""

    async def go() -> JournalReplay:
        async with Loom(config) as loom:
            return await loom.replay_journal(
                args.journal,
                run_id=RunId(args.run_id) if args.run_id else None,
                mode=args.mode,
                models=models,
                doubles=doubles,
                export=args.export,
            )

    try:
        replayed = asyncio.run(go())
    except ReplayError as error:
        print(f"Rejeu impossible : {error}", file=sys.stderr)
        return REFUSED
    codes = [_replay_code(report) for report in replayed.reports]
    code = FAILED if replayed.unfinished or FAILED in codes else OK
    if args.json:
        print(json.dumps(replayed.as_json(), ensure_ascii=False, indent=2))
        return code
    print(f"Journal    : {args.journal} — {len(replayed.reports)} run(s) à rejouer")
    for report in replayed.reports:
        print()
        _print_replay(report, args.export)
    for run_id in replayed.unfinished:
        print(f"\nNon rejoué : run {run_id}, inachevé au journal — un rejeu compare un run fini")
    said = "identique(s)" if args.mode == "exact" else "allé(s) au bout"
    print(
        f"\nBilan      : {codes.count(OK)}/{len(codes)} run(s) {said}"
        + (f", {len(replayed.unfinished)} inachevé(s) non rejoué(s)" if replayed.unfinished else "")
    )
    return code


def _print_replay(report: ReplayReport, export: Path | None) -> int:
    """Le rapport d'un rejeu, en texte ; rend son code de sortie."""
    if report.comparison is not None:
        _print_variant(report, report.comparison, export)
        return _replay_code(report)
    print(f"Run        : {report.run_id} (agent {report.agent}, client {report.tenant_id})")
    print(f"Session    : {report.session_id}")
    print(f"Issue      : {report.original_end} au journal, {report.replay_end} au rejeu")
    print(
        f"Modèles    : {report.model_calls[0]} appel(s) au journal, "
        f"{report.model_calls[1]} servi(s) au rejeu"
    )
    print(
        f"Outils     : {report.tool_calls[0]} résultat(s) au journal (rôles à part), "
        f"{report.tool_calls[1]} servi(s) au rejeu"
    )
    if export is not None:
        print(f"Journal    : {export} ({len(report.events)} événement(s))")
    divergence = report.divergence
    if divergence is None:
        print("Identique  : le run se rejoue tel qu'il a été, appel par appel.")
        return OK
    print(f"Divergence : {divergence.where}")
    if divergence.detail:
        print(f"             {divergence.detail}")
    if divergence.expected_hash and divergence.actual_hash:
        print(
            f"             empreinte {divergence.expected_hash[:12]} au journal, "
            f"{divergence.actual_hash[:12]} au rejeu"
        )
    return FAILED


def cmd_inspect(args: argparse.Namespace) -> int:
    """Relit un run et ses sous-runs, lisiblement ; 2 s'il est introuvable.

    En local, la commande lit le journal elle-même : elle montre le contenu.
    ``--json`` rend la trace telle que l'API la rend à une clé ``read_content``.
    """
    config = load_config(args.config, profile=args.profile)
    apply_logging(config)
    session = SessionId(args.session) if args.session else None

    async def go() -> Trace:
        async with Loom(config) as loom:
            return await loom.trace(RunId(args.run_id), session_id=session, tenant_id=_tenant(args))

    try:
        trace = asyncio.run(go())
    except UnknownRun:
        where = f" dans la session {args.session}" if args.session else ""
        print(f"Run {args.run_id} introuvable{where}", file=sys.stderr)
        return REFUSED
    if args.json:
        print(trace.model_dump_json(indent=2))
    else:
        print("\n".join(render_trace(trace, full=args.full)))
    return OK


def cmd_eval(args: argparse.Namespace) -> int:
    """Joue une suite d'évals ; 0 si tout passe, 1 si un attendu tombe, 2 si elle est injouable.

    Un run non joué (plafond de dépense atteint) rend aussi 1 : ce qu'il
    devait éprouver ne l'a pas été. Sans ``config`` dans la suite, c'est
    ``--config`` qui sert.
    """
    suite = load_suite(args.suite)
    path = suite.resolved(suite.config) or args.config
    config = load_config(path, profile=args.profile)
    apply_logging(config)
    try:
        report = asyncio.run(
            evaluate(
                suite.model_copy(update={"config": None}),
                config=config,
                profile=args.profile,
                cases=args.case,
                variants=args.variant,
                export=args.export,
            )
        )
    except EvalError as error:
        print(f"Éval impossible : {error}", file=sys.stderr)
        return REFUSED
    if args.json:
        print(json.dumps(report.as_json(), ensure_ascii=False, indent=2))
    else:
        print("\n".join(render_eval(report)))
    return OK if report.passed else FAILED


def _pairs(values: list[str], option: str, form: str) -> dict[str, str]:
    """``nom=valeur`` répétés, en table ; un nom donné deux fois est refusé."""
    pairs: dict[str, str] = {}
    for value in values:
        name, sep, target = value.partition("=")
        if not sep or not name or not target:
            raise ReplayError(f"{option} {value!r} : attendu {form}")
        if name in pairs:
            raise ReplayError(f"{option} : {name!r} donné deux fois")
        pairs[name] = target
    return pairs


def _replay_code(report: ReplayReport) -> int:
    """Identique : 0 si le run se rejoue tel quel ; variante : 0 si elle est allée au bout."""
    if report.mode == "variant":
        return OK if report.replay_end == "completed" else FAILED
    return OK if report.identical else FAILED


# Ordre dans lequel le rapport dit le sort des appels d'outil de la variante.
_FATE_ORDER: Final[tuple[ToolFate, ...]] = ("journal", "run", "relaunched", "double", "refused")


def _print_variant(report: ReplayReport, comparison: Comparison, export: Path | None) -> None:
    """Le run d'origine et sa variante, côte à côte."""
    original, variant = comparison.original, comparison.variant
    print(f"Run        : {report.run_id} (agent {report.agent}, client {report.tenant_id})")
    print(f"Session    : {report.session_id}")
    swapped = ", ".join(f"{step} → {model}" for step, model in comparison.swapped.items())
    print(f"Variante   : {swapped or 'la config, telle quelle'}")
    divergence = report.divergence
    if divergence is None:
        print("Écart      : aucun — la variante n'a rien demandé que le journal ne connaisse")
    else:
        print(f"Écart      : {divergence.where}")
        if divergence.detail:
            print(f"             {divergence.detail}")
    print(f"{'':13}{'origine':<24}variante")
    print(f"{'Issue':<11}: {_ended(original):<24}{_ended(variant)}")
    print(
        f"{'Modèles':<11}: {f'{original.model_calls} appel(s)':<24}"
        f"{variant.model_calls} appel(s) : "
        f"{comparison.served_models} servi(s) par le journal, "
        f"{comparison.real_models} parti(s) pour de vrai"
    )
    fates = ", ".join(
        f"{comparison.tools[fate]} {fate_label(fate)}"
        for fate in _FATE_ORDER
        if comparison.tools.get(fate)
    )
    print(
        f"{'Outils':<11}: {f'{original.tool_calls} appel(s)':<24}{variant.tool_calls} appel(s)"
        + (f" : {fates}" if fates else "")
    )
    print(f"{'Tokens':<11}: {_tokens(original):<24}{_tokens(variant)} (entrée / sortie)")
    print(
        f"{'Coût':<11}: {f'{original.cost_usd:.6f} $':<24}{variant.cost_usd:.6f} $ "
        f"(dépensé pour de vrai : {comparison.spent_usd:.6f} $)"
    )
    print(
        f"{'Durée':<11}: {f'{original.active_ms / 1000:.1f} s':<24}{variant.active_ms / 1000:.1f} s"
    )
    for label, side in (("origine", original), ("variante", variant)):
        for verdict in side.verdicts:
            notes = ", ".join(f"{name} {score:.2f}" for name, score in verdict.scores)
            said = "accepté" if verdict.passed else "refusé"
            print(
                f"Juge       : {label}, {verdict.judge} (essai {verdict.attempt}) : "
                f"{notes} — {said}"
            )
    print(f"Réponse    : {'la même' if comparison.same_answer else 'différente'}")
    if export is not None:
        print(f"Journal    : {export} ({len(report.events)} événement(s))")
    if not comparison.same_answer:
        print(f"--- au journal\n{original.text or '(aucune)'}")
        print(f"--- en variante\n{variant.text or '(aucune)'}")


def _ended(side: RunSide) -> str:
    return side.end if side.error_type is None else f"{side.end} ({side.error_type})"


def _tokens(side: RunSide) -> str:
    return f"{side.usage.prompt_tokens} / {side.usage.output_tokens}"


def cmd_decide(args: argparse.Namespace) -> int:
    """Tranche ce qu'un run attend, puis pilote sa reprise (#17).

    La décision met un travail ``resume`` en file **dans cette instance** :
    sans ``--no-wait``, c'est donc ce terminal qui mène le run à son terme et
    en affiche la réponse. Avec, la décision est écrite au journal et la
    commande sort sans rien reprendre ni attendre : la reprise revient à qui
    écoute ailleurs — un worker (la file d'un courtier a reçu le travail),
    ``loom resume``, ou la reprise au démarrage d'un worker.
    """
    config = load_config(args.config, profile=args.profile)
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
        loom = Loom(config)
        try:
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
        finally:
            # Avec ``--no-wait``, la reprise mise en file ici ne doit ni se faire ni
            # s'attendre : la décision est au journal, le run reste à reprendre.
            await loom.aclose(wait=not args.no_wait)

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
    config = load_config(args.config, profile=args.profile)
    apply_logging(config)
    if args.periode is not None:
        return _cmd_consumption(args, config)
    if args.run_id is None and args.session is None:
        print("Donner un run_id, --session ou --periode.", file=sys.stderr)
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


def _cmd_consumption(args: argparse.Namespace, config: LoomConfig) -> int:
    """``loom report --periode jour|mois`` : la dépense d'un client et ce qui lui reste."""
    period: BudgetPeriod = "day" if args.periode == "jour" else "month"

    async def go() -> TenantConsumption:
        async with Loom(config) as loom:
            return await loom.consumption(_tenant(args), period=period)

    found = asyncio.run(go())
    if args.json:
        print(json.dumps(_consumption_json(found), ensure_ascii=False, indent=2))
        return OK
    fenetre = "journée" if period == "day" else "mois"
    print(f"Client     : {found.tenant_id}")
    print(f"Période    : {fenetre} en cours, depuis {found.period.start:%Y-%m-%d %H:%M} UTC")
    print(f"Runs       : {found.runs}")
    print(f"Dépense    : {found.spent.cost:.6f} $, {found.spent.tokens} tokens")
    for limit, value in found.limits:
        left = found.left(limit)
        reste = "" if left is None else f" — reste {amount(limit, left)}"
        print(f"  plafond {limit} : {amount(limit, value)}{reste}")
    if not found.limits:
        print("  aucun plafond sur cette période")
    print(f"Remise à 0 : {found.resets_at:%Y-%m-%d %H:%M} UTC")
    return OK


def _consumption_json(found: TenantConsumption) -> dict[str, JsonValue]:
    return {
        "tenant_id": found.tenant_id,
        "period": found.period.key,
        "since": found.period.start.isoformat(),
        "resets_at": found.resets_at.isoformat(),
        "runs": found.runs,
        "cost_usd": found.spent.cost,
        "tokens": found.spent.tokens,
        "limits": {limit: value for limit, value in found.limits},
        "left": {limit: found.left(limit) for limit, _ in found.limits},
    }


def cmd_sessions_list(args: argparse.Namespace) -> int:
    config = load_config(args.config, profile=args.profile)
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
    config = load_config(args.config, profile=args.profile)
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
    config = load_config(args.config, profile=args.profile)
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


def cmd_retention(args: argparse.Namespace) -> int:
    """Balaie les sessions dormantes ; sans ``--yes``, dit seulement ce qui partirait.

    L'essai à blanc est le défaut, et il n'y a pas de question posée : cette
    commande est faite pour être mise à l'heure par une plateforme, et une
    question sans personne pour y répondre bloquerait le balayage.
    """
    config = load_config(args.config, profile=args.profile)
    apply_logging(config)

    async def go() -> RetentionReport:
        async with Loom(config) as loom:
            return await loom.apply_retention(tenant_id=_tenant(args), dry_run=not args.yes)

    report = asyncio.run(go())
    if args.json:
        print(report.model_dump_json(indent=2))
        return OK
    for tenant, days in report.days.items():
        borne = f"{days} jour(s)" if days is not None else "aucune règle"
        print(f"{tenant} : {borne}")
    for session in report.swept:
        parts = [f"{session.events} événement(s)"]
        if session.artifacts is not None:
            parts.append(f"{session.artifacts} fichier(s)")
        if session.keys is not None:
            parts.append(f"{session.keys} clé(s)")
        verbe = "à effacer" if report.dry_run else "effacée"
        print(
            f"  {session.session_id} ({session.tenant_id}) {verbe} — dernière écriture "
            f"{session.last_write:%Y-%m-%d %H:%M} UTC, {', '.join(parts)}"
        )
    faites = "seraient effacées" if report.dry_run else "effacées"
    print(
        f"{len(report.swept)} session(s) {faites}, {report.events} événement(s) ; "
        f"{report.kept} gardée(s) sur {report.scanned} regardée(s)."
    )
    if report.dry_run:
        print("Essai à blanc : rien n'a été supprimé. Ajouter --yes pour effacer.")
    return OK


def cmd_serve(args: argparse.Namespace) -> int:
    config = load_config(args.config, profile=args.profile)
    apply_logging(config)
    try:
        from loom_ia.access.http import serve
    except ImportError:
        print(MISSING_EXTRA.format(what="'loom serve'", extra="http"), file=sys.stderr)
        return REFUSED
    http = config.server.http
    host = args.host or http.host
    port = args.port or http.port
    if args.reload:
        return _serve_reloading(config, args, host, port)
    serving_banner(config, args.profile, host, port)
    serve(Loom(config), host=args.host, port=args.port)
    return OK


def _serve_reloading(config: LoomConfig, args: argparse.Namespace, host: str, port: int) -> int:
    """``loom serve --reload`` : un superviseur, et un process qui sert par version de la config."""
    if config.strict:
        print(
            "loom serve --reload est refusé en profil prod : un service ne se relance pas "
            "sur une modification de fichier.",
            file=sys.stderr,
        )
        return REFUSED
    if find_spec("watchfiles") is None:
        print(MISSING_EXTRA.format(what="'loom serve --reload'", extra="http"), file=sys.stderr)
        return REFUSED
    from loom_ia.access.http.serve import serve_reloading, watched

    # Refusé avant d'ouvrir quoi que ce soit : des données qui contiennent
    # le dossier surveillé relanceraient le serveur à chaque écriture.
    watched(config)
    return serve_reloading(
        Path(args.config), profile=args.profile, host=host, port=port, banner=serving_banner
    )


def serving_banner(config: LoomConfig, given: str | None, host: str, port: int) -> None:
    """Le bandeau de ``loom serve`` : profil, adresses, agents, portes.

    ``--reload`` le fait imprimer par chaque process qui prend la main : ce
    qu'on lit après un rechargement est ce que sert le nouveau.
    """
    http = config.server.http
    print(f"Profil     : {_profile_line(config, given)}")
    print(f"API REST   : http://{host}:{port}{http.base_path}/v1")
    print(f"Agents     : {_listed(agent.name for agent in config.agents if agent.expose.rest)}")
    if config.server.mcp.http:
        # Même port, même authentification : la clé dit le client à chaque
        # requête, ce que le stdio ne peut pas faire (J5.2b).
        publies = _listed(agent.name for agent in config.agents if agent.expose.mcp)
        print(f"MCP HTTP   : http://{host}:{port}{http.base_path}/mcp")
        print(f"Outils MCP : {publies}")
        # Les ressources ne dépendent d'aucune config : elles sont le journal,
        # en lecture seule, et la clé de la requête dit ce qu'elle en voit.
        print(f"Ressources : {MCP_RUNS}, {MCP_SESSIONS}, et {len(MCP_TEMPLATES)} gabarits")
    for trigger in config.triggers:
        # L'adresse qu'on donne au planificateur de la plateforme : loom ne
        # tient pas de cron, il attend qu'on sonne à la porte.
        print(f"Porte      : POST http://{host}:{port}{http.base_path}/v1/hooks/{trigger.name}")


def cmd_mcp(args: argparse.Namespace) -> int:
    config = load_config(args.config, profile=args.profile)
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
    try:
        expires = _deadline(args.expires)
    except ValueError as error:
        print(error, file=sys.stderr)
        return REFUSED
    print(f"Clé        : {key}")
    print("Elle n'est affichée qu'ici : la config ne garde que son empreinte.\n")
    print("À ajouter dans la configuration :\n")
    print("security:")
    print("  api_keys:")
    print(f"    - id: {args.id}")
    print(f"      hash: {fingerprint(key)}")
    if args.tenant:
        print(f"      tenant: {args.tenant}")
    print(f"      scopes: [{', '.join(scopes)}]")
    if agents:
        print(f"      agents: [{', '.join(agents)}]")
    if args.rate_limit is not None:
        print(f"      rate_limit: {{per_minute: {args.rate_limit}}}")
    if expires is not None:
        print(f"      expires: {expires.isoformat().replace('+00:00', 'Z')}")
    if "approve" in scopes and "read_content" not in scopes:
        print(
            "\nCette clé peut approuver sans pouvoir lire : sans 'read_content', "
            "les arguments d'un appel en attente lui sont masqués.",
            file=sys.stderr,
        )
    return OK


def _deadline(given: str | None) -> datetime | None:
    """Une date (``2027-01-01``) ou une durée à partir de maintenant (``90j``)."""
    if not given:
        return None
    unites = {"j": "days", "h": "hours", "m": "minutes"}
    if given[-1] in unites and given[:-1].isdigit():
        return datetime.now(UTC) + timedelta(**{unites[given[-1]]: int(given[:-1])})
    try:
        moment = datetime.fromisoformat(given)
    except ValueError:
        raise ValueError(
            f"--expires : date ISO (2027-01-01) ou durée (90j, 12h) attendue, reçu {given!r}"
        ) from None
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def cmd_worker(args: argparse.Namespace) -> int:
    return asyncio.run(_worker(args))


async def _worker(args: argparse.Namespace) -> int:
    """Consomme la file jusqu'à Ctrl-C, après avoir repris ce qui traînait.

    La reprise d'abord : un worker qui démarre est souvent celui qui remplace
    un worker mort, et ce qui restait en plan n'est dans aucune file — il est
    au journal (H3).
    """
    config = load_config(args.config, profile=args.profile)
    apply_logging(config)
    if args.jobs < 1:
        raise ValueError(f"--jobs : au moins 1, reçu {args.jobs}")
    queue = config.storage.queue
    print(f"Config   : {args.config}")
    print(f"File     : {_queue_line(queue)}")
    if not queue.brokered:
        # Refusé avant d'ouvrir quoi que ce soit : ni journal lu, ni run repris.
        # Même texte que ``Loom.work``, qui garde le refus pour la librairie.
        raise ConfigError(
            "Un worker demande une file servie par un courtier "
            f"('storage.queue.backend: rabbitmq'), pas {queue.backend!r} "
            "— qui exécute déjà ses tâches dans le process qui les met en file"
        )
    print(f"Agents   : {_listed(agent.name for agent in config.agents)}")
    print(f"Clients  : {_listed(config.tenant_ids)}")
    async with Loom(config) as loom:
        repris = [
            run for tenant in config.tenant_ids for run in await loom.recover(tenant_id=tenant)
        ]
        print(f"Reprise  : {len(repris)} run(s) remis en file")
        stop = _on_signals(loom)
        print(f"En écoute, {args.jobs} tâche(s) de front. Ctrl-C pour arrêter.", flush=True)
        try:
            await loom.work(jobs=args.jobs)
        finally:
            stop()
    print("Worker arrêté : plus rien en cours.")
    return OK


def _on_signals(loom: Loom) -> Callable[[], None]:
    """Fait finir le worker proprement sur SIGINT et SIGTERM ; rend de quoi défaire.

    Un arrêt demandé n'interrompt pas la tâche en cours : elle va au bout, et
    c'est ce qui évite de la faire redélivrer pour rien.
    """
    loop = asyncio.get_running_loop()
    signals = (signal.SIGINT, signal.SIGTERM)
    # Gardée le temps de l'arrêt : une tâche sans référence peut être ramassée.
    asking: set[asyncio.Task[None]] = set()

    def asked() -> None:
        print("\nArrêt demandé : la tâche en cours va au bout.", file=sys.stderr, flush=True)
        task = asyncio.create_task(loom.stop_work())
        asking.add(task)
        task.add_done_callback(asking.discard)

    for number in signals:
        loop.add_signal_handler(number, asked)

    def undo() -> None:
        for number in signals:
            loop.remove_signal_handler(number)

    return undo


def _bus_line(bus: BusStorage) -> str:
    """Le bus tel que ``loom validate`` l'affiche, sans jamais son raccordement."""
    if bus.variable is None:
        return f"{bus.backend} (les nouvelles ne sortent pas de ce process)"
    lue = "renseignée" if os.environ.get(bus.variable) else "ABSENTE"
    return f"{bus.backend} ({bus.variable} : {lue})"


def _queue_line(queue: QueueStorage) -> str:
    if queue.url_env is None:
        return queue.backend
    renseignee = "renseignée" if os.environ.get(queue.url_env) else "ABSENTE"
    return f"{queue.backend} (URL dans {queue.url_env} : {renseignee})"


def cmd_storage_sql(args: argparse.Namespace) -> int:
    """Le SQL à appliquer pour le stockage Postgres que la config déclare.

    Rien n'est exécuté : la sortie se relit, se met en revue et s'applique
    avec le rôle qui en a le droit (``psql -f``). loom l'applique aussi de
    lui-même à la première ouverture, si le rôle connecté le peut.
    """
    print(postgres_ddl(load_config(args.config, profile=args.profile)), end="")
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
    """Ouvre les sources de l'agent — serveurs MCP, sources de paquet — et liste leurs outils."""
    if not tools.sources:
        return
    packaged = {s.name: s for s in tools.sources if isinstance(s, PackagedSource)}
    servers = [s for s in tools.sources if not isinstance(s, PackagedSource)]
    context = SourceContext(
        tenant_id=DEFAULT_TENANT,
        session_id=SessionId("validate"),
        run_id=RunId("validate"),
        agent=agent,
    )
    async with tools.opened(context) as opened:
        if servers:
            found = [spec.name for spec in opened.tools.specs if spec.kind == "mcp"]
            print(f"    MCP : {_listed(found)}")
        for name, source in packaged.items():
            mine = f"{source.prefix}{MCP_PREFIX_SEPARATOR}"
            found = [spec.name for spec in opened.tools.specs if spec.name.startswith(mine)]
            print(f"    source {name} : {_listed(found)}")
        for missing in opened.unavailable:
            required = " (requis)" if missing.required else ""
            kind = "source" if missing.source in packaged else "MCP"
            print(f"    {kind} {missing.source} indisponible{required} : {missing.error}")


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


def _delay(seconds: float) -> str:
    """Un délai à attendre, arrondi par excès : ``45 s``, ``12 min``, ``7 h 12 min``."""
    whole = max(1, math.ceil(seconds))
    if whole < 120:
        return f"{whole} s"
    minutes = math.ceil(whole / 60)
    if minutes < 120:
        return f"{minutes} min"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes:02d} min"
