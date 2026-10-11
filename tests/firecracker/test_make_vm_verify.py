# SPDX-License-Identifier: Apache-2.0
"""``make_vm.sh`` : la vérification de l'image lit la sortie de ``debugfs``, pas son code.

``debugfs -R "stat <chemin>"`` rend 0 même quand le chemin manque : un test sur son code de
sortie ne peut pas échouer.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "firecracker" / "make_vm.sh"

needs_e2fsprogs = pytest.mark.skipif(
    shutil.which("mkfs.ext4") is None or shutil.which("debugfs") is None,
    reason="e2fsprogs absent",
)


def _img_has() -> str:
    """Le texte de la fonction ``img_has``, tel que le script la définit."""
    found = re.search(r"^img_has\(\) \{\n.*?^\}\n", SCRIPT.read_text(), re.S | re.M)
    assert found is not None, "img_has absente de make_vm.sh"
    return found.group(0)


def _image(tmp_path: Path) -> Path:
    """Une image ext4 sans droits root : ``mkfs.ext4 -d`` la remplit depuis un dossier."""
    tree = tmp_path / "tree"
    (tree / "etc" / "systemd" / "system").mkdir(parents=True)
    (tree / "etc" / "systemd" / "system" / "present.service").write_text("[Unit]\n")
    image = tmp_path / "rootfs.ext4"
    subprocess.run(
        ["mkfs.ext4", "-q", "-F", "-d", str(tree), str(image), "8M"],
        check=True,
        capture_output=True,
    )
    return image


def _has(image: Path, path: str) -> int:
    script = f"set -euo pipefail\n{_img_has()}\nimg_has '{image}' '{path}'"
    return subprocess.run(["bash", "-c", script], capture_output=True).returncode


@needs_e2fsprogs
def test_img_has_tells_a_present_path_from_an_absent_one(tmp_path: Path) -> None:
    image = _image(tmp_path)
    # Le piège : debugfs sort en 0 dans les deux cas.
    for path in ("/etc/systemd/system/present.service", "/etc/systemd/system/absent.service"):
        out = subprocess.run(["debugfs", "-R", f"stat {path}", str(image)], capture_output=True)
        assert out.returncode == 0
    assert _has(image, "/etc/systemd/system/present.service") == 0
    assert _has(image, "/etc/systemd/system/absent.service") == 1
    assert _has(image, "/opt/agent/executor.py") == 1


def test_verify_never_tests_a_path_by_the_exit_code_of_debugfs() -> None:
    text = SCRIPT.read_text()
    # Un « debugfs -R "stat … » dont on ne lit que le code de sortie est le défaut même.
    assert not re.search(r'if debugfs -R "stat [^"]*"[^\n]*\\?\n?[^\n]*>/dev/null 2>&1; then', text)
