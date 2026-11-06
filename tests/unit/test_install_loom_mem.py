# SPDX-License-Identifier: Apache-2.0
"""``install-loom-mem`` : l'aide à l'installation du service loom-mem-gpu, rejouée sans doublon.

Le script tourne pour de vrai (bash), dans un faux venv où ``loom-mem-gpu`` est un
remplaçant, avec de faux ``systemctl``, ``journalctl``, ``loginctl`` et ``nvidia-smi``
qui notent leurs appels. Ce qui est éprouvé : l'unité écrite, le rejeu sans
redémarrage, la différence montrée, le port pris par un autre, la validation déléguée
à l'application, le dry-run et la désinstallation.
"""

import os
import shutil
import socket
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "linux" or shutil.which("bash") is None, reason="bash et systemd : Linux"
)

SCRIPT: Final = Path(__file__).resolve().parents[2] / "scripts" / "bin" / "install-loom-mem"

FAKE_APP: Final = """#!/usr/bin/env bash
echo "loom-mem-gpu $*" >>"$STATE/calls"
if [[ "$1" == serve ]]; then
    for arg in "$@"; do
        if [[ "$arg" == bogus ]]; then
            echo "loom-mem-gpu : réglages refusés (options) : device : pattern" >&2
            exit 2
        fi
    done
    echo "loom-mem-gpu : réglages valides"
    exit 0
fi
if [[ "$1" == status ]]; then
    [[ -f "$STATE/ready" ]] || exit 1
    echo "http://127.0.0.1 : ready (protocole 1, loom 2.0.18)"
    exit 0
fi
exit 9
"""

FAKE_SYSTEMCTL: Final = """#!/usr/bin/env bash
echo "systemctl $*" >>"$STATE/calls"
shift  # --user
case "$1" in
    show-environment) exit 0 ;;
    is-active) [[ -f "$STATE/active" ]] ;;
    is-enabled) [[ -f "$STATE/enabled" ]] ;;
    is-failed) [[ -f "$STATE/failed" ]] ;;
    daemon-reload) exit 0 ;;
    enable) touch "$STATE/enabled" ;;
    restart)
        if [[ -f "$STATE/fails" ]]; then touch "$STATE/failed"; exit 0; fi
        touch "$STATE/active" "$STATE/ready" ;;
    disable) rm -f "$STATE/enabled" "$STATE/active" "$STATE/ready" ;;
    *) exit 9 ;;
esac
"""

FAKE_LOGINCTL: Final = """#!/usr/bin/env bash
echo "loginctl $*" >>"$STATE/calls"
[[ "$1" == show-user ]] && echo no
exit 0
"""

FAKE_NVIDIA_SMI: Final = """#!/usr/bin/env bash
[[ -f "$STATE/nogpu" ]] && exit 9
echo "GPU 0: NVIDIA GeForce RTX 5060 Ti"
"""

FAKE_JOURNALCTL: Final = """#!/usr/bin/env bash
echo "ERROR loom_ia.memory.server : modèles non chargés : absent du cache"
"""


@dataclass
class Done:
    code: int
    out: str
    err: str


class Machine:
    """Un poste factice : un venv avec le script et un faux loom-mem-gpu, un faux systemd."""

    def __init__(self, root: Path) -> None:
        self.home = root / "home"
        self.state = root / "state"
        self.venv_bin = root / "venv" / "bin"
        self.stubs = root / "stubs"
        for folder in (self.home, self.state, self.venv_bin, self.stubs):
            folder.mkdir(parents=True)
        shutil.copy(SCRIPT, self.venv_bin / "install-loom-mem")
        self._write(self.venv_bin / "loom-mem-gpu", FAKE_APP)
        self._write(self.stubs / "systemctl", FAKE_SYSTEMCTL)
        self._write(self.stubs / "loginctl", FAKE_LOGINCTL)
        self._write(self.stubs / "nvidia-smi", FAKE_NVIDIA_SMI)
        self._write(self.stubs / "journalctl", FAKE_JOURNALCTL)
        self.unit = self.home / ".config" / "systemd" / "user" / "loom-mem-gpu.service"

    @staticmethod
    def _write(path: Path, text: str) -> None:
        path.write_text(text, encoding="utf-8")
        path.chmod(0o755)

    def run(self, *args: str, extra: dict[str, str] | None = None) -> Done:
        env = {
            "PATH": f"{self.stubs}:/usr/bin:/bin",
            "HOME": str(self.home),
            "USER": "denis",
            "STATE": str(self.state),
            "LOOM_MEM_READY_WAIT": "4",
            **(extra or {}),
        }
        done = subprocess.run(
            ["bash", str(self.venv_bin / "install-loom-mem"), *args],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        return Done(done.returncode, done.stdout, done.stderr)

    def calls(self) -> list[str]:
        path = self.state / "calls"
        found = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
        path.unlink(missing_ok=True)
        return found

    def systemd(self) -> list[str]:
        """Les appels qui changent quelque chose dans systemd."""
        kept = ("daemon-reload", "enable", "restart", "disable", "enable-linger")
        return [c for c in self.calls() if any(f" {word}" in c for word in kept)]


@pytest.fixture
def machine(tmp_path: Path) -> Machine:
    return Machine(tmp_path)


@pytest.fixture
def port() -> int:
    with socket.socket() as free:
        free.bind(("127.0.0.1", 0))
        return int(free.getsockname()[1])


@pytest.fixture
def taken(port: int) -> Iterator[int]:
    """Le port tenu par un autre programme."""
    with socket.socket() as other:
        other.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        other.bind(("127.0.0.1", port))
        other.listen()
        yield port


def test_the_first_install_writes_the_unit_and_starts_it(machine: Machine, port: int) -> None:
    done = machine.run("--port", str(port), "--offline")
    assert done.code == 0, done.err
    unit = machine.unit.read_text(encoding="utf-8")
    app = machine.venv_bin / "loom-mem-gpu"
    assert f'ExecStart="{app}" serve --device cuda --port {port} --offline' in unit
    assert "Environment=PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True" in unit
    assert "RestartPreventExitStatus=2 3" in unit
    assert "prêt" in done.out and "ready" in done.out
    assert "pour qu'il tourne sans session : --linger" in done.out


def test_replaying_the_same_install_changes_nothing(machine: Machine, port: int) -> None:
    assert machine.run("--port", str(port)).code == 0
    machine.calls()
    # Le service tourne et occupe son port : c'est le sien, pas un autre programme.
    with socket.socket() as ours:
        ours.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        ours.bind(("127.0.0.1", port))
        ours.listen()
        done = machine.run("--port", str(port))
    assert done.code == 0, done.err
    assert "déjà installé et actif, unité à jour : rien à faire" in done.out
    assert machine.systemd() == []


def test_a_changed_setting_shows_the_difference_and_restarts_once(
    machine: Machine, port: int
) -> None:
    assert machine.run("--port", str(port)).code == 0
    machine.calls()
    done = machine.run("--port", str(port), "--max-vram-fraction", "0.3")
    assert done.code == 0, done.err
    assert "l'unité change" in done.out
    assert "+ExecStart=" in done.out and "--max-vram-fraction 0.3" in done.out
    assert machine.systemd() == [
        "systemctl --user daemon-reload",
        "systemctl --user enable loom-mem-gpu.service",
        "systemctl --user restart loom-mem-gpu.service",
    ]
    assert machine.unit.read_text(encoding="utf-8").count("ExecStart=") == 1


def test_a_port_held_by_another_program_stops_before_writing(machine: Machine, taken: int) -> None:
    done = machine.run("--port", str(taken))
    assert done.code == 3
    assert "déjà pris, et pas par ce service" in done.err
    assert not machine.unit.exists()
    assert machine.systemd() == []


def test_the_settings_are_validated_by_the_application(machine: Machine, port: int) -> None:
    done = machine.run("--port", str(port), "--device", "bogus")
    assert done.code == 2
    assert "réglages refusés" in done.err
    assert not machine.unit.exists()
    assert any(c.endswith("--check") for c in machine.calls())


def test_cuda_without_a_card_is_refused(machine: Machine, port: int) -> None:
    (machine.state / "nogpu").touch()
    done = machine.run("--port", str(port))
    assert done.code == 2 and "nvidia-smi ne voit aucune carte" in done.err
    assert not machine.unit.exists()
    assert machine.run("--port", str(port), "--device", "cpu").code == 0


def test_dry_run_changes_nothing(machine: Machine, port: int) -> None:
    done = machine.run("--port", str(port), "--dry-run", "--linger")
    assert done.code == 0, done.err
    assert "unité à écrire" in done.out and "ExecStart=" in done.out
    assert "[dry-run] systemctl --user restart loom-mem-gpu.service" in done.out
    assert "[dry-run] loginctl enable-linger denis" in done.out
    assert not machine.unit.exists()
    assert machine.systemd() == []


def test_a_service_that_fails_shows_its_journal(machine: Machine, port: int) -> None:
    (machine.state / "fails").touch()
    done = machine.run("--port", str(port))
    assert done.code == 1
    assert "absent du cache" in done.err


def test_uninstall_stops_and_removes_then_has_nothing_to_do(machine: Machine, port: int) -> None:
    assert machine.run("--port", str(port)).code == 0
    machine.calls()
    done = machine.run("--uninstall")
    assert done.code == 0, done.err
    assert not machine.unit.exists()
    assert machine.systemd() == [
        "systemctl --user disable --now loom-mem-gpu.service",
        "systemctl --user daemon-reload",
    ]
    again = machine.run("--uninstall")
    assert again.code == 0 and "rien à désinstaller" in again.out


def test_the_model_cache_of_the_shell_follows_into_the_unit(machine: Machine, port: int) -> None:
    done = machine.run("--port", str(port), extra={"HF_HOME": "/data/hf"})
    assert done.code == 0, done.err
    assert "Environment=HF_HOME=/data/hf" in machine.unit.read_text(encoding="utf-8")


def test_unknown_options_and_missing_values_are_refused(machine: Machine) -> None:
    assert machine.run("--prot", "8800").code == 2
    assert machine.run("--port").code == 2
    assert machine.run("--port", "huit").code == 2


def test_the_script_is_installed_with_the_package() -> None:
    import tomllib

    pyproject = tomllib.loads((SCRIPT.parents[2] / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["tool"]["uv"]["build-backend"]["data"]["scripts"] == "scripts/bin"
    assert os.access(SCRIPT, os.X_OK)
