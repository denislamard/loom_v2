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

Chaque config se rejoue dans son propre processus : les exemples ont chacun
leur module ``outils``, et un processus qui a importé celui d'une config le
sert aux suivantes sous le même nom (``imports``, #50).
"""

import subprocess
import sys
from pathlib import Path

import pytest

from loom_ia.core.model import DEFAULT_TENANT
from loom_ia.replay import BASE_VARIANT, journal_runs, load_suite, read_journal

CORPUS = Path(__file__).parent
SUITES = sorted(CORPUS.glob("j*/*.yaml"))
# Le rejeu d'une suite, hors du processus des essais : ``assert_replays`` sur
# ses journaux, qui lève avec chaque écart.
REJEU = """
import asyncio
import sys

from loom_ia.testing import assert_replays

asyncio.run(assert_replays(sys.argv[1], *sys.argv[2:]))
"""


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
def test_each_journal_replays_identically(path: Path) -> None:
    suite = load_suite(path)
    config = suite.resolved(suite.config)
    assert config is not None
    fichiers = [str(f) for f in journaux(path)]
    assert fichiers
    done = subprocess.run(
        [sys.executable, "-c", REJEU, str(config), *fichiers],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert done.returncode == 0, done.stderr[-4000:]
