# SPDX-License-Identifier: Apache-2.0
"""``install-loom-mem`` : l'aide à l'installation du service loom-mem-gpu, rejouée sans doublon.

Le script tourne pour de vrai (bash), dans un faux venv où ``loom-mem-gpu serve``
lance le vrai ``main`` (sans l'extra ``memory`` exigé), avec de faux ``systemctl``,
``journalctl``, ``loginctl`` et ``nvidia-smi`` qui notent leurs appels. Ce qui est
éprouvé : l'unité écrite (réglages en variables), le rejeu sans redémarrage, la
différence montrée, le port pris par un autre, les surcharges respectées, la
validation déléguée à l'application, le dry-run et la désinstallation.
"""

import os
import shutil
import socket
import subprocess
import sys
from collections.abc import Generator, Iterator
from contextlib import contextmanager
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
    exec "$PYTHON" -c '
import sys
from loom_ia.tools_builtin.memory.server import app, main
app.missing_modules = list
sys.exit(main(sys.argv[1:]))
' "$@"
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
UNIT="$HOME/.config/systemd/user/loom-mem-gpu.service"
case "$1" in
    show-environment) exit 0 ;;
    show)  # -p Environment --value : l'unité et ses surcharges, sur une ligne
        cat "$UNIT" "$UNIT.d"/*.conf 2>/dev/null \
            | sed -n 's/^Environment=//p' | tr -d '"' | tr '\\n' ' '
        echo ;;
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
        self.dropins = self.unit.parent / "loom-mem-gpu.service.d"
        self.env_file = self.home / ".config" / "loom-mem-gpu" / "env"

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
            "PYTHON": sys.executable,
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
    return free_port()


def free_port() -> int:
    with socket.socket() as free:
        free.bind(("127.0.0.1", 0))
        return int(free.getsockname()[1])


@contextmanager
def held(port: int) -> Generator[int]:
    """Le port écouté : par le service, ou par un autre programme."""
    with socket.socket() as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", port))
        listener.listen()
        yield port


@pytest.fixture
def taken(port: int) -> Iterator[int]:
    """Le port tenu par un autre programme."""
    with held(port):
        yield port


def test_the_first_install_writes_the_unit_and_starts_it(machine: Machine, port: int) -> None:
    done = machine.run("--port", str(port), "--offline")
    assert done.code == 0, done.err
    unit = machine.unit.read_text(encoding="utf-8")
    app = machine.venv_bin / "loom-mem-gpu"
    # Tous les réglages en variables, défauts compris ; ExecStart sans option.
    assert f'ExecStart="{app}" serve\n' in unit
    for line in (
        "Environment=LOOM_MEM_GPU_HOST=127.0.0.1",
        f"Environment=LOOM_MEM_GPU_PORT={port}",
        "Environment=LOOM_MEM_GPU_DEVICE=cuda",
        "Environment=LOOM_MEM_GPU_FP16=true",
        "Environment=LOOM_MEM_GPU_OFFLINE=true",
        "Environment=LOOM_MEM_GPU_EMBEDDER=BAAI/bge-m3",
        "Environment=LOOM_MEM_GPU_RERANKER=BAAI/bge-reranker-v2-m3",
        "# Environment=LOOM_MEM_GPU_TOKEN_ENV=LOOM_MEM_GPU_TOKEN",
        "# Environment=LOOM_MEM_GPU_MAX_VRAM_FRACTION=0.3",
        "Environment=LOOM_MEM_GPU_LOG_LEVEL=INFO",
        "Environment=PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True",
        "# Environment=HF_HOME=",
        "# Environment=CUDA_VISIBLE_DEVICES=0",
        "# Environment=OMP_NUM_THREADS=4",
        "#   HF_TOKEN=",
        "EnvironmentFile=-%h/.config/loom-mem-gpu/env",
    ):
        assert f"\n{line}" in unit, line
    assert "RestartPreventExitStatus=2 3" in unit
    # Chaque variable dit ce qu'elle fait : la ligne d'avant est un commentaire.
    lines = unit.splitlines()
    for index, line in enumerate(lines):
        if line.removeprefix("# ").startswith("Environment="):
            assert lines[index - 1].startswith("# ") and "Environment=" not in lines[index - 1]
    # Le fichier du jeton n'est jamais créé par le script.
    assert not machine.env_file.parent.exists()
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
    assert "-# Environment=LOOM_MEM_GPU_MAX_VRAM_FRACTION=0.3" in done.out
    assert "+Environment=LOOM_MEM_GPU_MAX_VRAM_FRACTION=0.3" in done.out
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
    assert any(c.endswith("--check --env") for c in machine.calls())


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


def test_a_model_cache_with_spaces_is_escaped_for_systemd(machine: Machine, port: int) -> None:
    done = machine.run("--port", str(port), extra={"HF_HOME": '/data/mes "hf" 100%'})
    assert done.code == 0, done.err
    unit = machine.unit.read_text(encoding="utf-8")
    assert '\nEnvironment="HF_HOME=/data/mes \\"hf\\" 100%%"\n' in unit


def test_overrides_without_a_port_leave_the_port_alone(machine: Machine, port: int) -> None:
    # Le cas courant : un fichier avec le seul jeton, une surcharge sans Environment=.
    machine.env_file.parent.mkdir(parents=True)
    machine.env_file.write_text("LOOM_MEM_GPU_TOKEN=s3cret\n", encoding="utf-8")
    machine.dropins.mkdir(parents=True)
    (machine.dropins / "override.conf").write_text("[Service]\nNice=5\n", encoding="utf-8")
    done = machine.run("--port", str(port))
    assert done.code == 0, done.err
    assert "l'emporte" not in done.out and "prêt" in done.out
    assert f"loom-mem-gpu status --url http://127.0.0.1:{port}" in machine.calls()


def test_the_variables_of_the_shell_do_not_leak_into_the_unit(machine: Machine, port: int) -> None:
    shell = {"LOOM_MEM_GPU_PORT": "9999", "LOOM_MEM_GPU_DEVICE": "cpu", "LOOM_MEM_GPU_PROT": "1"}
    done = machine.run("--port", str(port), extra=shell)
    assert done.code == 0, done.err
    unit = machine.unit.read_text(encoding="utf-8")
    assert f"Environment=LOOM_MEM_GPU_PORT={port}\n" in unit
    assert "Environment=LOOM_MEM_GPU_DEVICE=cuda\n" in unit


def test_a_port_set_by_systemctl_edit_is_kept_and_followed(machine: Machine, port: int) -> None:
    assert machine.run("--port", str(port)).code == 0
    unit = machine.unit.read_text(encoding="utf-8")
    other = free_port()
    machine.dropins.mkdir()
    override = machine.dropins / "override.conf"
    override.write_text(f"[Service]\nEnvironment=LOOM_MEM_GPU_PORT={other}\n", encoding="utf-8")
    machine.calls()
    with held(other):  # le service redémarré par l'utilisateur écoute le port de la surcharge
        done = machine.run("--port", str(port))
    assert done.code == 0, done.err
    assert f"le port {other}" in done.out and "rien à faire" in done.out
    calls = machine.calls()
    assert f"loom-mem-gpu status --url http://127.0.0.1:{other}" in calls
    assert not [c for c in calls if " restart" in c or " daemon-reload" in c]
    assert machine.unit.read_text(encoding="utf-8") == unit
    assert override.read_text(encoding="utf-8").endswith(f"={other}\n")


def test_the_environment_file_port_wins_and_is_never_written(machine: Machine, port: int) -> None:
    other = free_port()
    machine.env_file.parent.mkdir(parents=True)
    text = f"LOOM_MEM_GPU_PORT={other}\nLOOM_MEM_GPU_TOKEN=s3cret\n"
    machine.env_file.write_text(text, encoding="utf-8")
    done = machine.run("--port", str(port))
    assert done.code == 0, done.err
    assert f"loom-mem-gpu status --url http://127.0.0.1:{other}" in machine.calls()
    assert machine.env_file.read_text(encoding="utf-8") == text
    assert "s3cret" not in machine.unit.read_text(encoding="utf-8")
    # Rejoué pendant que le service tient ce port : c'est le sien, rien à faire.
    with held(other):
        again = machine.run("--port", str(port))
    assert again.code == 0, again.err
    assert "rien à faire" in again.out
    assert machine.systemd() == []


def test_an_older_unit_with_options_is_migrated_without_a_port_clash(
    machine: Machine, port: int
) -> None:
    app = machine.venv_bin / "loom-mem-gpu"
    machine.unit.parent.mkdir(parents=True)
    machine.unit.write_text(
        f'[Service]\nExecStart="{app}" serve --device cuda --port {port}\n', encoding="utf-8"
    )
    (machine.state / "active").touch()
    with held(port):  # l'ancienne unité tourne sur son port
        done = machine.run("--port", str(port))
    assert done.code == 0, done.err
    assert "l'unité change" in done.out
    assert f'+ExecStart="{app}" serve' in done.out
    assert machine.systemd() == [
        "systemctl --user daemon-reload",
        "systemctl --user enable loom-mem-gpu.service",
        "systemctl --user restart loom-mem-gpu.service",
    ]


def test_uninstall_keeps_what_the_user_wrote(machine: Machine, port: int) -> None:
    assert machine.run("--port", str(port)).code == 0
    machine.dropins.mkdir()
    (machine.dropins / "override.conf").write_text("[Service]\n", encoding="utf-8")
    machine.env_file.parent.mkdir(parents=True)
    machine.env_file.write_text("LOOM_MEM_GPU_TOKEN=s3cret\n", encoding="utf-8")
    done = machine.run("--uninstall")
    assert done.code == 0, done.err
    assert "surcharges gardées" in done.out and "fichier d'environnement gardé" in done.out
    assert (machine.dropins / "override.conf").exists() and machine.env_file.exists()


def test_without_user_in_the_environment_the_account_is_asked(machine: Machine, port: int) -> None:
    import getpass

    done = machine.run("--port", str(port), "--dry-run", "--linger", extra={"USER": ""})
    assert done.code == 0, done.err
    assert f"[dry-run] loginctl enable-linger {getpass.getuser()}" in done.out


def test_unknown_options_and_missing_values_are_refused(machine: Machine) -> None:
    assert machine.run("--prot", "8800").code == 2
    assert machine.run("--port").code == 2
    assert machine.run("--port", "huit").code == 2


BIN: Final = SCRIPT.parent
# Les commandes livrées dans le bin du venv, et leur interpréteur. ``#!python`` : le
# Python du venv, que l'installeur écrit à la place (``/usr/bin/env python3`` serait
# celui du système, peut-être trop ancien).
COMMANDS: Final = {
    "install-loom-mem": "#!/usr/bin/env bash",
    "create-loom-project": "#!/usr/bin/env bash",
    "view-loom-journal": "#!python",
}


def test_the_commands_are_installed_with_the_package() -> None:
    import tomllib

    pyproject = tomllib.loads((SCRIPT.parents[2] / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["tool"]["uv"]["build-backend"]["data"]["scripts"] == "scripts/bin"
    assert sorted(path.name for path in BIN.iterdir()) == sorted(COMMANDS)
    for name, first_line in COMMANDS.items():
        command = BIN / name
        assert os.access(command, os.X_OK), name
        assert command.read_text(encoding="utf-8").splitlines()[0] == first_line, name


def test_the_commands_say_their_own_name() -> None:
    journal = subprocess.run(
        [sys.executable, str(BIN / "view-loom-journal"), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert journal.returncode == 0, journal.stderr
    assert journal.stdout.startswith("usage: view-loom-journal")
    project = subprocess.run(
        ["bash", str(BIN / "create-loom-project")], capture_output=True, text=True, check=False
    )
    assert project.returncode == 2  # sans arguments : l'usage
    assert "create-loom-project <projet> <agent> <prompt>" in project.stdout + project.stderr
