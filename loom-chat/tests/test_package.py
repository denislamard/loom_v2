"""Le paquet : loom-chat suit les versions de loom-ia, exactement."""

import tomllib
from importlib.metadata import version
from pathlib import Path

from packaging.requirements import Requirement

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


def test_version_follows_loom_ia() -> None:
    """La version de loom-chat, sa dépendance à loom-ia et le loom-ia installé portent le même numéro."""
    project = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]
    loom_ia = next(Requirement(dep) for dep in project["dependencies"] if Requirement(dep).name == "loom-ia")
    assert str(loom_ia.specifier) == f"=={project['version']}"
    assert version("loom-ia") == project["version"]
