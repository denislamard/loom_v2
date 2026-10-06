# SPDX-License-Identifier: Apache-2.0
"""Non-régression depuis les traces (O3, J6.3b) : le corpus tiré des exemples J1 à J5.

Chaque suite de ce dossier (``jN/<config>.yaml``) désigne la config d'un
exemple ; ses cas ont été joués en simulé et leurs runs exportés dans
``jN/journaux/``. Chaque journal doit se rejouer **à l'identique** avec la
config d'aujourd'hui : un changement du moteur, d'un prompt, d'un outil, d'une
politique qui modifie une requête au modèle fait tomber l'essai, et dit
laquelle et ce qui a changé.

Un changement voulu se réenregistre — la commande est en tête de chaque suite —
et le diff des journaux se relit avant de les committer.

Les cinq configs se rejouent dans le même processus, chacune avec son module
``outils`` : loom réimporte les modules voisins de chaque config (#50, 6.3c).
"""

from pathlib import Path

import pytest

from loom_ia.config import load_config
from loom_ia.core.model import DEFAULT_TENANT
from loom_ia.replay import BASE_VARIANT, journal_runs, load_suite, read_journal
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
    ]


@pytest.mark.parametrize("path", SUITES, ids=named)
def test_each_case_has_its_journal_and_nothing_else(path: Path) -> None:
    """Un cas sans journal n'est pas éprouvé ; un journal sans cas reste d'un cas disparu.

    Chaque journal porte un run fini, de l'agent de la suite, au nom du client du cas.
    """
    suite = load_suite(path)
    assert not suite.variants and suite.repeat == 1
    attendus = {f"{BASE_VARIANT}--{case.name}--1.jsonl": case for case in suite.cases}
    assert {f.name for f in journaux(path)} == set(attendus)
    for fichier in journaux(path):
        [run] = journal_runs(read_journal(fichier))
        case = attendus[fichier.name]
        assert run.finished and run.agent == suite.agent
        assert run.tenant_id == (suite.tenant_of(case) or DEFAULT_TENANT)


@pytest.mark.parametrize("path", SUITES, ids=named)
async def test_each_journal_replays_identically(path: Path) -> None:
    suite = load_suite(path)
    config = suite.resolved(suite.config)
    assert config is not None
    replayed = await assert_replays(load_config(config), *journaux(path))
    assert [len(r.reports) for r in replayed] == [1] * len(journaux(path))
