# SPDX-License-Identifier: Apache-2.0
"""Dépendances : tout paquet tiers que le code importe est déclaré dans ``pyproject.toml``."""

import ast
import re
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
