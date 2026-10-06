# SPDX-License-Identifier: Apache-2.0
"""Non-régression depuis les traces, dans un test (O2, O3, J6.3b).

    async def test_la_relance_se_rejoue() -> None:
        await assert_replays("loom.yaml", *Path("journaux").glob("*.jsonl"))

Chaque run fini de chaque journal est rejoué **à l'identique** avec la config
donnée : le monde — modèles, outils, approbations — est servi par le journal,
personne n'est appelé, et la première requête qui diffère est dite. Un run
qui se rejoue prouve que la config produit encore exactement les mêmes
requêtes : le prompt, les outils, l'historique, les politiques n'ont pas bougé.
"""

import tempfile
from collections.abc import Mapping
from pathlib import Path

from loom_ia.access import Loom
from loom_ia.config import LoomConfig, load_config
from loom_ia.replay import JournalReplay, ReplayError, isolated


async def assert_replays(
    config: LoomConfig | Path | str,
    *journals: Path | str,
    register: Mapping[str, object] | None = None,
    environ: Mapping[str, str] | None = None,
) -> list[JournalReplay]:
    """Rejoue les journaux ; ``AssertionError`` avec chaque écart, s'il y en a.

    ``config`` : la config chargée, ou le chemin de son fichier. L'agent est
    monté **à part**, comme pour une éval (``isolated``) : rien n'est écrit
    dans le journal, les fichiers ou le magasin d'idempotence que la config
    déclare. ``register`` rend des objets Python référençables par leur nom
    (``Loom.register``) — un outil que la config ne trouve pas dans ses
    ``imports``. Un journal illisible, un run qui ne peut pas être rejoué ou
    qui reste inachevé sont des écarts comme les autres. Sans journal, rien ne
    serait éprouvé : c'est aussi un échec.

    Rend ce que chaque journal a donné, pour qui veut en vérifier davantage.
    """
    if not journals:
        raise AssertionError("assert_replays : aucun journal — rien ne serait éprouvé")
    loaded = config if isinstance(config, LoomConfig) else load_config(config)
    problems: list[str] = []
    replayed: list[JournalReplay] = []
    with tempfile.TemporaryDirectory(prefix="loom-rejeu-") as scratch:
        async with Loom(isolated(loaded, Path(scratch)), environ=environ) as loom:
            for name, obj in (register or {}).items():
                loom.register(name, obj)
            for journal in journals:
                try:
                    result = await loom.replay_journal(journal)
                except ReplayError as error:
                    problems.append(f"{journal} : {error}")
                    continue
                replayed.append(result)
                problems += _problems(journal, result)
    if problems:
        lines = "\n".join(f"  {problem}" for problem in problems)
        raise AssertionError(
            f"Rejeu : {len(problems)} écart(s) sur {len(journals)} journal(aux)\n{lines}"
        )
    return replayed


def _problems(journal: Path | str, result: JournalReplay) -> list[str]:
    problems: list[str] = []
    for report in result.reports:
        divergence = report.divergence
        if divergence is None:
            continue
        said = divergence.where + (f" ; {divergence.detail}" if divergence.detail else "")
        problems.append(f"{journal}, run {report.run_id} : {said}")
    problems += [
        f"{journal}, run {run_id} : inachevé au journal — un rejeu compare un run fini"
        for run_id in result.unfinished
    ]
    return problems
