# SPDX-License-Identifier: Apache-2.0
"""``jailer-run.sh`` : un identifiant de VM contrôlé, et tous les disques de la jail (SBX-10).

Le script lance le jailer en root ; ces essais le jouent hors de root, dans un dossier
temporaire : un faux ``id`` répond 0, un faux ``jailer`` note ses arguments, et les disques
sont de petits fichiers. Le script n'est pas employé par le flux de loom (la sandbox ne passe
pas par le jailer) : il est éprouvé pour ce qu'il fait si quelqu'un le lance.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[2] / "firecracker" / "jailer-run.sh"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="bash absent")


def _executable(path: Path, text: str) -> None:
    path.write_text(text)
    path.chmod(0o755)


@pytest.fixture
def atelier(tmp_path: Path) -> Path:
    """Le dossier d'une VM à la façon de ``make_vm.sh``, avec un faux jailer et un faux ``id``."""
    vm = tmp_path / "vm"
    vm.mkdir()
    shutil.copy(SCRIPT, vm / "jailer-run.sh")
    _executable(vm / "firecracker", "#!/bin/sh\n")
    _executable(vm / "jailer", '#!/bin/sh\nprintf "%s\\n" "$@" > "$(dirname "$0")/jailer.args"\n')
    (vm / "vmlinux-test").write_bytes(b"noyau")
    (vm / "rootfs.ext4").write_bytes(b"rootfs")
    (vm / "data.ext4").write_bytes(b"data")
    config = {
        "boot-source": {"kernel_image_path": "vmlinux-test"},
        "drives": [
            {"drive_id": "rootfs", "path_on_host": "rootfs.ext4", "is_root_device": True},
            {"drive_id": "data", "path_on_host": "data.ext4", "is_root_device": False},
        ],
        "vsock": {"guest_cid": 3, "uds_path": "runtime/v.sock"},
    }
    (vm / "vm-config.json").write_text(json.dumps(config))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _executable(bin_dir / "id", '#!/bin/sh\n[ "$1" = "-u" ] && echo 0 || echo 0\n')
    return vm


def _run(atelier: Path, vm_id: str, **extra: str) -> subprocess.CompletedProcess[str]:
    tmp = atelier.parent
    env = {
        **os.environ,
        "PATH": f"{tmp / 'bin'}:{os.environ['PATH']}",
        "CHROOT_BASE": str(tmp / "chroot"),
        "VM_ID": vm_id,
        "JAIL_UID": str(os.getuid()),
        "JAIL_GID": str(os.getgid()),
        "INTERACTIVE": "0",
        **extra,
    }
    return subprocess.run(
        ["bash", str(atelier / "jailer-run.sh")],
        cwd=atelier,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


@pytest.mark.parametrize(
    ("vm_id", "cible"),
    [
        # ``chroot/firecracker/..`` est la base de la jail ; ``../../victime`` en sort.
        ("..", "chroot/temoin"),
        ("../../victime", "victime/temoin"),
        ("a/..", "chroot/firecracker/temoin"),
        ("a b", "chroot/temoin"),
        ("x" * 65, "chroot/temoin"),
        ("é", "chroot/temoin"),
    ],
)
def test_a_vm_id_that_is_not_alphanumeric_is_refused_before_anything_is_removed(
    atelier: Path, vm_id: str, cible: str
) -> None:
    """Un ``VM_ID`` comme ``..`` ne vise plus ce que le ``rm -rf`` de la jail effacerait."""
    temoin = atelier.parent / cible
    temoin.parent.mkdir(parents=True, exist_ok=True)
    temoin.write_text("à garder")
    (atelier.parent / "chroot" / "firecracker").mkdir(parents=True, exist_ok=True)
    done = _run(atelier, vm_id)
    assert done.returncode != 0
    assert "VM_ID" in done.stderr
    assert temoin.read_text() == "à garder"
    assert not (atelier / "jailer.args").exists()


def test_a_valid_vm_id_builds_the_jail_with_every_drive(atelier: Path) -> None:
    done = _run(atelier, "agent-01")
    assert done.returncode == 0, done.stderr
    jail = atelier.parent / "chroot" / "firecracker" / "agent-01" / "root"
    assert (jail / "rootfs.ext4").read_bytes() == b"rootfs"
    # Le disque de données est posé lui aussi, et chaque drive pointe vers le sien.
    assert (jail / "data.ext4").read_bytes() == b"data"
    drives = {
        d["drive_id"]: d["path_on_host"]
        for d in json.loads((jail / "vm-config.json").read_text())["drives"]
    }
    assert drives == {"rootfs": "rootfs.ext4", "data": "data.ext4"}
    assert oct((jail / "data.ext4").stat().st_mode & 0o777) == "0o600"
    assert "agent-01" in (atelier / "jailer.args").read_text()


def test_a_drive_missing_from_the_vm_folder_is_refused(atelier: Path) -> None:
    (atelier / "data.ext4").unlink()
    done = _run(atelier, "agent-01")
    assert done.returncode != 0
    assert "data.ext4" in done.stderr
    assert not (atelier / "jailer.args").exists()


def test_the_jail_of_the_same_id_is_rebuilt_and_the_vm_files_stay(atelier: Path) -> None:
    assert _run(atelier, "agent-01").returncode == 0
    assert _run(atelier, "agent-01").returncode == 0
    # La jail précédente est effacée ; les disques du dossier de la VM, eux, restent.
    assert (atelier / "rootfs.ext4").read_bytes() == b"rootfs"
    assert (atelier / "data.ext4").read_bytes() == b"data"
