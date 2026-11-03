# SPDX-License-Identifier: Apache-2.0
"""``loom memory`` : entretenir la base d'un agent qui active ``loom.memory``, hors de tout run.

    loom memory export <agent> <fichier.jsonl>    un document JSON par ligne (fichier neuf)
    loom memory import <agent> <fichier.jsonl>    réimporte un export, sans doublon
    loom memory reindex <agent>                   recalcule les passages (modèle de la config)
    loom memory eval <agent> <golden.jsonl>       recall@1, recall@5, MRR, seuil suggéré

La base est celle que l'agent monte pour le client (``--tenant``) : son
``store``, et ``path_env`` lu dans les secrets de ce client. Les modèles de
langage ne sont pas créés (aucune clé d'API n'est lue) ; ceux de la mémoire
servent à ``import``, ``reindex`` et ``eval``, pas à ``export``. L'outil n'est
importé qu'à l'exécution de la commande.
"""

import argparse
import asyncio
import sys
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Final

from loom_ia.access.api import Loom
from loom_ia.adapters.models.fake import FakeModel
from loom_ia.config import LoomConfig
from loom_ia.core.model import TenantId
from loom_ia.core.ports import ToolError
from loom_ia.tools import innermost

if TYPE_CHECKING:
    from loom_ia.tools_builtin.memory.maintenance import EvalReport

TOOL: Final = "memory"
OK: Final = 0
FAILED: Final = 1
REFUSED: Final = 2


def add_actions(
    memory: argparse.ArgumentParser,
    tenanted: Callable[[argparse.ArgumentParser], argparse.ArgumentParser],
    handler: Callable[[argparse.Namespace], int],
) -> None:
    """Les actions de la commande ``memory``."""
    actions = memory.add_subparsers(dest="action", required=True)
    export = tenanted(actions.add_parser("export", help="écrit les documents en JSONL"))
    export.add_argument("agent")
    export.add_argument("file", type=Path, help="fichier à créer (jamais écrasé)")
    imported = tenanted(actions.add_parser("import", help="réimporte un export JSONL"))
    imported.add_argument("agent")
    imported.add_argument("file", type=Path)
    reindex = tenanted(
        actions.add_parser("reindex", help="recalcule les passages avec le modèle de la config")
    )
    reindex.add_argument("agent")
    evaluate = tenanted(actions.add_parser("eval", help="mesure la recherche sur un jeu doré"))
    evaluate.add_argument("agent")
    evaluate.add_argument("file", type=Path, help="jeu doré JSONL : {query, doc_id, project?}")
    evaluate.add_argument("--k", type=int, default=10, choices=range(1, 51), metavar="K")
    evaluate.add_argument("--json", action="store_true", help="rapport complet en JSON")
    for command in (export, imported, reindex, evaluate):
        command.set_defaults(handler=handler)


def run(config: LoomConfig, args: argparse.Namespace) -> int:
    return asyncio.run(_run(config, args))


async def _run(config: LoomConfig, args: argparse.Namespace) -> int:
    from loom_ia.tools_builtin.memory import MemoryTool, maintenance

    # Les modèles de langage ne servent pas : aucune clé d'API n'est lue.
    clients = {spec.id: FakeModel(spec) for spec in config.models}
    async with Loom(config, models=clients) as loom:
        tenant = loom.tenant(None if args.tenant is None else TenantId(args.tenant))
        context = loom.context(args.agent, tenant.id)
        found = innermost(context.tools.get(TOOL))
        if not isinstance(found, MemoryTool):
            print(f"L'agent {args.agent!r} n'active pas loom.memory.", file=sys.stderr)
            return REFUSED
        service = found.service
        try:
            match args.action:
                case "export":
                    count = await maintenance.export(service, args.file)
                    print(f"{count} document(s) écrit(s) dans {args.file}")
                case "import":
                    done = await maintenance.import_(service, args.file)
                    print(f"{done.imported} document(s) importé(s), {done.skipped} déjà présent(s)")
                case "reindex":
                    count = await maintenance.reindex(service)
                    print(f"{count} document(s) réindexé(s) dans {found.store.path}")
                case "eval":
                    cases = maintenance.load_golden(args.file)
                    if not cases:
                        print(f"Aucun cas dans {args.file}.", file=sys.stderr)
                        return REFUSED
                    report = await maintenance.evaluate(service, cases, args.k)
                    _print_report(report, as_json=args.json)
                case _:
                    raise AssertionError(args.action)
        except ToolError as error:
            print(error.message, file=sys.stderr)
            return REFUSED
        except FileExistsError:
            print(f"{args.file} existe déjà : export n'écrase rien.", file=sys.stderr)
            return REFUSED
    return OK


def _print_report(report: EvalReport, *, as_json: bool) -> None:
    if as_json:
        print(report.model_dump_json(indent=2))
        return
    for result in report.results:
        mark = "ok " if result.rank == 1 else ("~  " if result.rank else "KO ")
        rank = f"#{result.rank}" if result.rank else "absent"
        score = f"{result.expected_score:.3f}" if result.expected_score is not None else "  -  "
        print(f"{mark} {rank:>7}  {score}  {result.query}")
    print(
        f"\n{report.cases} cas — recall@1 {report.recall_at_1:.2f}  "
        f"recall@5 {report.recall_at_5:.2f}  MRR {report.mrr:.2f}"
    )
    if report.suggested_min_score is not None:
        noise = report.noise_removed_at_suggested
        tail = f" (aucun cas perdu ; {noise:.0%} du bruit filtré)" if noise is not None else ""
        print(f"min_score suggéré : {report.suggested_min_score}{tail}")
