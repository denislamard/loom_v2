# SPDX-License-Identifier: Apache-2.0
"""Dépendances : tout paquet tiers que le code importe est déclaré dans ``pyproject.toml``."""

import ast
import re
import shutil
import subprocess
import sys
import tomllib
from importlib.metadata import packages_distributions
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _normalise(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _declared() -> set[str]:
    """Les paquets que le projet déclare : noyau et extras (les groupes servent aux tests)."""
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    requirements = list(project["dependencies"])
    for extra in project.get("optional-dependencies", {}).values():
        requirements.extend(extra)
    return {_normalise(re.split(r"[\s\[<>=!~;]", req, maxsplit=1)[0]) for req in requirements}


def _imported_modules() -> dict[str, Path]:
    """Chaque module tiers de premier niveau que ``src`` importe, avec un fichier qui l'importe."""
    found: dict[str, Path] = {}
    for path in sorted((ROOT / "src").rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            else:
                continue
            for name in names:
                top = name.split(".", 1)[0]
                if top != "loom_ia" and top not in sys.stdlib_module_names:
                    found.setdefault(top, path)
    return found


def test_every_third_party_import_is_declared() -> None:
    """Un import direct se déclare : ``referencing`` ne l'était pas (CIP-9)."""
    declared = _declared()
    distributions = packages_distributions()
    missing: list[str] = []
    for module, path in _imported_modules().items():
        owners = {_normalise(name) for name in distributions.get(module, [])}
        if owners and not owners & declared:
            missing.append(f"{module} (importé par {path.relative_to(ROOT)}) : {sorted(owners)}")
    assert not missing, "Importés sans être déclarés : " + "; ".join(missing)


# Un essai qui saute faute d'un paquet, de trois façons : au chargement de son
# module, par une marque, dans son corps. Et deux sauts qui n'en sont pas.
_SKIPPED_MODULE = """
import pytest

pytest.importorskip("loom_paquet_absent", reason="extra 'exemple' absent")


def test_module() -> None:
    pass
"""
_SKIPPED_TESTS = """
from importlib.util import find_spec

import pytest


@pytest.mark.skipif(find_spec("loom_paquet_absent") is None, reason="extra 'exemple' absent")
def test_marque() -> None:
    pass


def test_corps() -> None:
    pytest.importorskip("loom_paquet_absent", reason="client HTTP de test absent")


@pytest.mark.skipif(True, reason="extra 'exemple' présent")
def test_present() -> None:
    pass


def test_autre() -> None:
    pytest.skip("rien à voir avec un paquet")
"""


def _pytest(where: Path, *options: str) -> str:
    """Lance pytest sur ``where`` avec le ``conftest.py`` des essais, et rend sa sortie."""
    done = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q", "-rA", *options, "."],
        cwd=where,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    return done.stdout + done.stderr


def test_a_missing_extra_fails_under_require_services(tmp_path: Path) -> None:
    """Sous ``--require-services``, un saut faute d'un paquet est un échec (TST-3).

    La CI complète installe tous les extras : un essai qui y saute faute de l'un
    d'eux n'a rien éprouvé, et la suite resterait verte. Sans le drapeau, le
    saut reste un saut, comme dans la passe « noyau seul ».
    """
    shutil.copy(ROOT / "tests" / "conftest.py", tmp_path / "conftest.py")
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (tmp_path / "test_module.py").write_text(_SKIPPED_MODULE, encoding="utf-8")
    (tmp_path / "test_essais.py").write_text(_SKIPPED_TESTS, encoding="utf-8")

    said = _pytest(tmp_path)
    assert "5 skipped" in said, said

    said = _pytest(tmp_path, "--require-services", "--continue-on-collection-errors")
    assert "1 failed, 2 skipped, 2 errors" in said, said
    assert "ERROR collecting test_module.py" in said, said
    assert "ERROR at setup of test_marque" in said, said
    assert "FAILED test_essais.py::test_corps" in said, said
    exige = "et --require-services exige que cet essai tourne"
    assert said.count(f"\nextra 'exemple' absent — {exige}\n") == 2, said
    assert f"\nclient HTTP de test absent — {exige}\n" in said, said
    assert "SKIPPED [1] test_essais.py:16: extra 'exemple' présent" in said, said
    assert "SKIPPED [1] test_essais.py:22: rien à voir avec un paquet" in said, said
