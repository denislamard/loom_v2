# SPDX-License-Identifier: Apache-2.0
"""Non-régression depuis les traces (O3, J6.3b) : le corpus tiré des exemples J1 à J5 et J7.

Chaque suite de ce dossier (``jN/<config>.yaml``) désigne la config d'un
exemple ; ses cas ont été joués en simulé et leurs runs exportés dans
``jN/journaux/``. Chaque journal doit se rejouer **à l'identique** avec la
config d'aujourd'hui : un changement du moteur, d'un prompt, d'un outil, d'une
politique qui modifie une requête au modèle fait tomber l'essai, et dit
laquelle et ce qui a changé.

Le rejeu sert à chaque appel d'outil le résultat du journal : il ne voit pas
ce qui ne change que la logique appliquée à ce résultat, comme les sources
``from`` du state. Chaque suite est donc aussi **jouée** en simulé, avec le
code d'aujourd'hui, et ses cas doivent tenir leurs attendus.

Un changement voulu se réenregistre — la commande est en tête de chaque suite —
et le diff des journaux se relit avant de les committer.

Les six configs se rejouent dans le même processus, chacune avec son module
``outils`` : loom réimporte les modules voisins de chaque config (#50, 6.3c).
Celle de J7 a un state de session : chaque cas pose le sien à l'ouverture de
son run, et son journal se rejoue avec lui. Celle de ``j7_state_v2`` y ajoute un
state que le modèle tient lui-même (``loom.state``).
"""

from pathlib import Path

import pytest

from loom_ia.access.evals import evaluate
from loom_ia.config import load_config
from loom_ia.core.events import RunStarted
from loom_ia.core.model import DEFAULT_TENANT
from loom_ia.replay import BASE_VARIANT, journal_runs, load_suite, read_journal, render_eval
from loom_ia.testing import assert_replays

CORPUS = Path(__file__).parent
SUITES = sorted(CORPUS.glob("j*/*.yaml"))


def named(path: Path) -> str:
    return f"{path.parent.name}/{path.stem}"


def journaux(suite: Path) -> list[Path]:
    return sorted((suite.parent / "journaux").glob("*.jsonl"))


def test_the_corpus_covers_the_examples() -> None:
    assert [named(s) for s in SUITES] == [
        "j1/demo",
        "j2/relance",
        "j3/relance",
        "j4/relance",
        "j5/relance",
        "j7/relance",
        "j7_state_v2/clients",
    ]


@pytest.mark.parametrize("path", SUITES, ids=named)
def test_each_case_has_its_journal_and_nothing_else(path: Path) -> None:
    """Un cas sans journal n'est pas éprouvé ; un journal sans cas reste d'un cas disparu.

    Chaque journal porte un run fini, de l'agent de la suite, au nom du client du cas,
    avec le state que le cas pose.
    """
    suite = load_suite(path)
    assert not suite.variants and suite.repeat == 1
    attendus = {f"{BASE_VARIANT}--{case.name}--1.jsonl": case for case in suite.cases}
    assert {f.name for f in journaux(path)} == set(attendus)
    for fichier in journaux(path):
        events = read_journal(fichier)
        [run] = journal_runs(events)
        case = attendus[fichier.name]
        assert run.finished and run.agent == suite.agent
        assert run.tenant_id == (suite.tenant_of(case) or DEFAULT_TENANT)
        [started] = [
            e.payload
            for e in events
            if isinstance(e.payload, RunStarted) and e.run_id == run.run_id
        ]
        assert started.state == case.state


@pytest.mark.parametrize("path", SUITES, ids=named)
async def test_each_journal_replays_identically(path: Path) -> None:
    suite = load_suite(path)
    config = suite.resolved(suite.config)
    assert config is not None
    replayed = await assert_replays(load_config(config), *journaux(path))
    assert [len(r.reports) for r in replayed] == [1] * len(journaux(path))


@pytest.mark.parametrize("path", SUITES, ids=named)
async def test_each_suite_plays_and_holds_its_expectations(path: Path) -> None:
    report = await evaluate(load_suite(path))
    assert report.passed, "\n".join(render_eval(report))
