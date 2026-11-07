# SPDX-License-Identifier: Apache-2.0
"""``install-loom-mem`` : le service loom-mem-gpu, partagé par les agents du poste.

Le script tourne pour de vrai (bash), depuis de faux venvs d'agents (``pyvenv.cfg``,
métadonnées de loom-ia et de torch) où ``loom-mem-gpu serve`` et ``--version`` lancent
le vrai ``main`` (sans l'extra ``memory`` exigé), avec de faux ``uv``, ``systemctl``,
``journalctl``, ``loginctl`` et ``nvidia-smi`` qui notent leurs appels. Ce qui est
éprouvé : l'environnement du service (même source que l'agent, torch épinglé), le
registre des agents (sans doublon, agents disparus retirés, sous verrou), ``--list``,
l'unité écrite (réglages en variables), le rejeu sans redémarrage, la différence
montrée, le port pris par un autre, les surcharges respectées, la validation déléguée
à l'application, le dry-run et la désinstallation par le dernier agent.
"""

import json
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
LOOM: Final = "2.0.19"
TORCH: Final = "2.15.0"
EDITABLE: Final = "/src/loom_v2"
# Le direct_url.json d'un loom-ia installé autrement que depuis un index.
DIRECT_URLS: Final[dict[str, dict[str, object]]] = {
    "editable": {"url": f"file://{EDITABLE}", "dir_info": {"editable": True}},
    "path": {"url": "file:///src/loom_ia-2.0.19.tar.gz", "archive_info": {}},
    "git": {
        "url": "https://github.com/app-novative/loom_v2.git",
        "vcs_info": {"vcs": "git", "commit_id": "abc123"},
    },
}

FAKE_APP: Final = """#!/usr/bin/env bash
echo "loom-mem-gpu $*" >>"$STATE/calls"
if [[ "$1" == serve || "$1" == --version ]]; then
    exec "$PYTHON" -c '
import sys
from loom_ia.tools_builtin.memory.server import app, main
app.missing_modules = list
sys.exit(main(sys.argv[1:]))
' "$@"
fi
if [[ "$1" == status ]]; then
    [[ -f "$STATE/ready" ]] || exit 1
    echo "http://127.0.0.1 : ready (protocole 1, loom 2.0.19)"
    exit 0
fi
exit 9
"""

# uv : ``venv`` fait un venv (python, pyvenv.cfg) ; ``pip install`` y pose torch et, si
# loom-ia est demandé, un loom-mem-gpu et ses métadonnées (direct_url si ``-e``).
FAKE_UV: Final = """#!/usr/bin/env bash
echo "uv $*" >>"$STATE/calls"
[[ -f "$STATE/uvfails" ]] && exit 1
[[ -f "$STATE/pipfails" && "$1" == pip ]] && exit 1
if [[ "$1" == venv ]]; then
    dir="${*: -1}"
    mkdir -p "$dir/bin" "$dir/lib/python3.14/site-packages"
    ln -sf "$PYTHON" "$dir/bin/python"
    printf 'home = %s\nversion_info = 3.14\n' "$(dirname "$PYTHON")" >"$dir/pyvenv.cfg"
    exit 0
fi
[[ "$1 $2" == "pip install" ]] || exit 9
shift 2
py=""; editable=""; loom=0; torch=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --python) py="$2"; shift 2 ;;
        --index-url) shift 2 ;;
        --quiet) shift ;;
        -e) editable="${2%"[memory]"}"; loom=1; shift 2 ;;
        torch==*) torch="${1#torch==}"; shift ;;
        *loom*) loom=1; shift ;;
        *) shift ;;
    esac
done
site="$(dirname "$(dirname "$py")")/lib/python3.14/site-packages"
if [[ -n "$torch" && ! -d "$site"/torch-*.dist-info ]]; then
    mkdir -p "$site/torch-$torch.dist-info"
    printf 'Name: torch\nVersion: %s\n' "$torch" >"$site/torch-$torch.dist-info/METADATA"
fi
if ((loom)); then
    info="$site/loom_ia-2.0.19.dist-info"
    mkdir -p "$info"
    printf 'Name: loom-ia\nVersion: 2.0.19\n' >"$info/METADATA"
    url='{"url": "file://%s", "dir_info": {"editable": true}}'
    [[ -z "$editable" ]] || printf "$url" "$editable" >"$info/direct_url.json"
    cp "$FAKE_APP_TEMPLATE" "$(dirname "$py")/loom-mem-gpu"
    chmod +x "$(dirname "$py")/loom-mem-gpu"
fi
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
    """Un poste factice : des venvs d'agents avec le script, un faux uv, un faux systemd."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.home = root / "home"
        self.state = root / "state"
        self.stubs = root / "stubs"
        for folder in (self.home, self.state, self.stubs):
            folder.mkdir(parents=True)
        self._write(self.stubs / "fake-app", FAKE_APP)
        self._write(self.stubs / "uv", FAKE_UV)
        self._write(self.stubs / "systemctl", FAKE_SYSTEMCTL)
        self._write(self.stubs / "loginctl", FAKE_LOGINCTL)
        self._write(self.stubs / "nvidia-smi", FAKE_NVIDIA_SMI)
        self._write(self.stubs / "journalctl", FAKE_JOURNALCTL)
        self.agent = self.make_agent(root / "venv")
        self.venv_bin = self.agent / "bin"
        self.unit = self.home / ".config" / "systemd" / "user" / "loom-mem-gpu.service"
        self.dropins = self.unit.parent / "loom-mem-gpu.service.d"
        self.env_file = self.home / ".config" / "loom-mem-gpu" / "env"
        self.service_venv = self.home / ".local" / "share" / "loom-mem-gpu" / "venv"
        self.service_app = self.service_venv / "bin" / "loom-mem-gpu"
        self.registry = self.home / ".local" / "state" / "loom-mem-gpu" / "agents"

    @staticmethod
    def _write(path: Path, text: str) -> None:
        path.write_text(text, encoding="utf-8")
        path.chmod(0o755)

    def make_agent(self, venv: Path, source: str = "editable", torch: str | None = TORCH) -> Path:
        """Le venv d'un agent : le script, un loom-mem-gpu, loom-ia (et torch) installés."""
        bin_dir = venv / "bin"
        site = venv / "lib" / "python3.14" / "site-packages"
        bin_dir.mkdir(parents=True)
        shutil.copy(SCRIPT, bin_dir / "install-loom-mem")
        self._write(bin_dir / "loom-mem-gpu", FAKE_APP)
        (bin_dir / "python").symlink_to(sys.executable)
        (venv / "pyvenv.cfg").write_text(
            f"home = {Path(sys.executable).parent}\nversion_info = 3.14\n", encoding="utf-8"
        )
        info = site / f"loom_ia-{LOOM}.dist-info"
        info.mkdir(parents=True)
        (info / "METADATA").write_text(f"Name: loom-ia\nVersion: {LOOM}\n", encoding="utf-8")
        direct = DIRECT_URLS.get(source)
        if direct is not None:
            (info / "direct_url.json").write_text(json.dumps(direct), encoding="utf-8")
        if torch is not None:
            torch_info = site / f"torch-{torch}.dist-info"
            torch_info.mkdir()
            (torch_info / "METADATA").write_text(
                f"Name: torch\nVersion: {torch}\n", encoding="utf-8"
            )
        return venv

    def env(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        return {
            "PATH": f"{self.stubs}:/usr/bin:/bin",
            "HOME": str(self.home),
            "USER": "denis",
            "STATE": str(self.state),
            "LOOM_MEM_READY_WAIT": "4",
            "PYTHON": sys.executable,
            "FAKE_APP_TEMPLATE": str(self.stubs / "fake-app"),
            **(extra or {}),
        }

    def run(
        self, *args: str, agent: Path | None = None, extra: dict[str, str] | None = None
    ) -> Done:
        script = (agent or self.agent) / "bin" / "install-loom-mem"
        done = subprocess.run(
            ["bash", str(script), *args],
            env=self.env(extra),
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

    def agents(self) -> list[list[str]]:
        """Les lignes du registre, champs séparés."""
        text = self.registry.read_text(encoding="utf-8")
        return [line.split("\t") for line in text.splitlines() if not line.startswith("#")]


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
    # Le service tourne depuis son environnement, pas depuis le venv de l'agent.
    # Tous les réglages en variables, défauts compris ; ExecStart sans option.
    assert f'ExecStart="{machine.service_app}" serve\n' in unit
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
    calls = machine.calls()
    assert any(c.endswith("--check --env") for c in calls)
    # Refusé par le loom-mem-gpu de l'agent, avant de créer l'environnement du service.
    assert not [c for c in calls if c.startswith("uv ")]
    assert not machine.service_venv.exists()


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
    assert "[dry-run] uv venv --quiet --python 3.14" in done.out
    assert not machine.unit.exists()
    assert not machine.service_venv.exists() and not machine.registry.exists()
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
    # Le dernier agent : l'environnement du service et le registre partent aussi.
    assert not machine.service_venv.exists() and not machine.registry.exists()
    assert "dernier agent" in done.out
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
    assert f'-ExecStart="{app}" serve --device cuda --port {port}' in done.out
    assert f'+ExecStart="{machine.service_app}" serve' in done.out
    assert [row[0] for row in machine.agents()] == [str(machine.agent)]
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


# --- L'environnement du service (lot 10, §8.5.1) -------------------------------------


def uv_calls(calls: list[str]) -> list[str]:
    return [c for c in calls if c.startswith("uv ")]


def test_the_service_gets_its_own_environment_from_the_agent_source(
    machine: Machine, port: int
) -> None:
    done = machine.run("--port", str(port))
    assert done.code == 0, done.err
    python = machine.service_venv / "bin" / "python"
    assert uv_calls(machine.calls()) == [
        f"uv venv --quiet --python 3.14 {machine.service_venv}",
        f"uv pip install --quiet --python {python} -e {EDITABLE}[memory] torch=={TORCH}",
    ]
    assert f"source -e {EDITABLE}, torch {TORCH}, Python 3.14" in done.out
    assert machine.service_app.exists()
    # L'environnement ne dépend pas du venv de l'agent : on peut le supprimer.
    assert str(machine.agent) not in machine.unit.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("source", "requirement"),
    [
        ("path", "/src/loom_ia-2.0.19.tar.gz[memory]"),
        ("git", "loom-ia[memory] @ git+https://github.com/app-novative/loom_v2.git@abc123"),
        ("index", f"loom-ia[memory]=={LOOM}"),
    ],
)
def test_the_service_takes_the_same_source_as_the_agent(
    tmp_path: Path, port: int, source: str, requirement: str
) -> None:
    machine = Machine(tmp_path)
    agent = machine.make_agent(tmp_path / f"agent-{source}", source=source)
    assert machine.run("--port", str(port), agent=agent).code == 0
    python = machine.service_venv / "bin" / "python"
    assert uv_calls(machine.calls())[1] == (
        f"uv pip install --quiet --python {python} {requirement} torch=={TORCH}"
    )


def test_a_local_torch_build_comes_from_the_pytorch_index_first(tmp_path: Path, port: int) -> None:
    machine = Machine(tmp_path)
    agent = machine.make_agent(tmp_path / "agent-cpu", torch="2.15.0+cpu")
    assert machine.run("--port", str(port), "--device", "cpu", agent=agent).code == 0
    python = machine.service_venv / "bin" / "python"
    assert uv_calls(machine.calls())[1:] == [
        f"uv pip install --quiet --python {python} torch==2.15.0+cpu "
        "--index-url https://download.pytorch.org/whl/cpu",
        f"uv pip install --quiet --python {python} -e {EDITABLE}[memory] torch==2.15.0",
    ]


def test_a_working_environment_is_not_reinstalled(machine: Machine, port: int) -> None:
    assert machine.run("--port", str(port)).code == 0
    machine.calls()
    with held(port):
        done = machine.run("--port", str(port))
    assert done.code == 0, done.err
    assert uv_calls(machine.calls()) == []


def test_a_broken_environment_is_rebuilt(machine: Machine, port: int) -> None:
    machine.service_venv.mkdir(parents=True)  # un venv à moitié fait, sans loom-mem-gpu
    (machine.service_venv / "reste").write_text("", encoding="utf-8")
    assert machine.run("--port", str(port)).code == 0
    assert not (machine.service_venv / "reste").exists()
    assert machine.service_app.exists()


def test_a_failed_environment_leaves_nothing_behind(machine: Machine, port: int) -> None:
    (machine.state / "uvfails").touch()
    done = machine.run("--port", str(port))
    assert done.code == 1
    assert "environnement du service non créé" in done.err
    assert not machine.unit.exists() and not machine.registry.exists()
    assert machine.systemd() == []


def test_a_failed_install_into_the_new_environment_removes_it(machine: Machine, port: int) -> None:
    (machine.state / "pipfails").touch()  # uv venv réussit, uv pip install échoue
    done = machine.run("--port", str(port))
    assert done.code == 1
    assert "loom-ia n'a pas pu être installé" in done.err
    assert not machine.service_venv.exists()
    assert not machine.unit.exists() and not machine.registry.exists()


def test_an_agent_without_torch_is_refused(tmp_path: Path, port: int) -> None:
    machine = Machine(tmp_path)
    agent = machine.make_agent(tmp_path / "sans-torch", torch=None)
    done = machine.run("--port", str(port), agent=agent)
    assert done.code == 2 and "extra memory" in done.err
    assert not machine.service_venv.exists()


def test_the_script_must_run_from_a_venv(machine: Machine, port: int) -> None:
    (machine.agent / "pyvenv.cfg").unlink()
    done = machine.run("--port", str(port))
    assert done.code == 2 and "pyvenv.cfg" in done.err


# --- Le registre des agents (§8.5.2) -----------------------------------------------


def test_the_installing_agent_is_registered_once(machine: Machine, port: int) -> None:
    done = machine.run("--port", str(port))
    assert done.code == 0, done.err
    assert f"agent inscrit : {machine.agent} (loom {LOOM})" in done.out
    text = machine.registry.read_text(encoding="utf-8")
    assert text.startswith("# Agents qui utilisent loom-mem-gpu.")
    [(venv, loom, since)] = machine.agents()
    assert (venv, loom) == (str(machine.agent), LOOM) and since[:2] == "20"
    with held(port):
        again = machine.run("--port", str(port))
    assert again.code == 0 and "agent inscrit" not in again.out
    assert machine.agents() == [[venv, loom, since]]  # pas de doublon, date gardée


def test_a_second_agent_with_the_same_settings_is_registered_without_restart(
    machine: Machine, port: int
) -> None:
    assert machine.run("--port", str(port)).code == 0
    second = machine.make_agent(machine.root / "agent-b")
    machine.calls()
    with held(port):
        done = machine.run("--port", str(port), agent=second)
    assert done.code == 0, done.err
    assert f"agent inscrit : {second}" in done.out and "rien à faire" in done.out
    assert machine.systemd() == []
    assert [row[0] for row in machine.agents()] == [str(machine.agent), str(second)]


def test_a_vanished_agent_is_dropped_from_the_registry(machine: Machine, port: int) -> None:
    gone = machine.make_agent(machine.root / "parti")
    assert machine.run("--port", str(port), agent=gone).code == 0
    shutil.rmtree(gone)
    with held(port):
        done = machine.run("--port", str(port))
    assert done.code == 0, done.err
    assert f"agent disparu, retiré : {gone}" in done.out
    assert [row[0] for row in machine.agents()] == [str(machine.agent)]


def test_two_installs_at_once_take_turns(machine: Machine, port: int) -> None:
    second = machine.make_agent(machine.root / "agent-b")
    runs = [
        subprocess.Popen(
            ["bash", str(agent / "bin" / "install-loom-mem"), "--port", str(port)],
            env=machine.env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for agent in (machine.agent, second)
    ]
    for started in runs:
        _, err = started.communicate(timeout=60)
        assert started.returncode == 0, err
    assert sorted(row[0] for row in machine.agents()) == sorted([str(machine.agent), str(second)])
    assert machine.registry.read_text(encoding="utf-8").count("# Agents") == 1
    assert len(uv_calls(machine.calls())) == 2  # un seul environnement créé


def test_a_venv_path_with_a_tab_is_refused(tmp_path: Path, port: int) -> None:
    machine = Machine(tmp_path)
    agent = machine.make_agent(tmp_path / "agent\tb")
    done = machine.run("--port", str(port), agent=agent)
    assert done.code == 2 and "tabulation" in done.err
    assert not machine.registry.exists()


def test_list_shows_the_service_and_its_agents_and_changes_nothing(
    machine: Machine, port: int
) -> None:
    assert machine.run("--port", str(port)).code == 0
    gone = machine.make_agent(machine.root / "parti")
    with held(port):
        assert machine.run("--port", str(port), agent=gone).code == 0
    shutil.rmtree(gone)
    before = machine.registry.read_text(encoding="utf-8")
    done = machine.run("--list")
    assert done.code == 0, done.err
    lines = done.out.splitlines()
    assert lines[0].startswith(f"loom-mem-gpu : actif, http://127.0.0.1:{port}, loom ")
    assert "(protocole 1, " in lines[0]
    assert lines[1] == f"  source : -e {EDITABLE}   environnement : {machine.service_venv}"
    assert lines[2] == "agents (2) :"
    assert lines[3].startswith(f"  {machine.agent}   loom {LOOM}   inscrit le 20")
    assert lines[4].endswith("(disparu)")
    assert machine.registry.read_text(encoding="utf-8") == before  # lecture seule


def test_list_without_a_service(machine: Machine) -> None:
    done = machine.run("--list")
    assert done.code == 0 and "n'est pas installé" in done.out and "agents (0) :" in done.out


# --- Désinstaller : seulement par le dernier agent (§8.5.4) --------------------------


def test_uninstall_keeps_the_service_while_other_agents_use_it(machine: Machine, port: int) -> None:
    assert machine.run("--port", str(port)).code == 0
    second = machine.make_agent(machine.root / "agent-b")
    with held(port):
        assert machine.run("--port", str(port), agent=second).code == 0
    machine.calls()
    done = machine.run("--uninstall")
    assert done.code == 0, done.err
    assert f"agent retiré : {machine.agent}" in done.out
    assert "service gardé, encore utilisé par :" in done.out and str(second) in done.out
    assert machine.systemd() == []
    assert machine.unit.exists() and machine.service_app.exists()
    assert [row[0] for row in machine.agents()] == [str(second)]
    # Le dernier retire tout.
    last = machine.run("--uninstall", agent=second)
    assert last.code == 0 and "dernier agent" in last.out
    assert not machine.unit.exists() and not machine.service_venv.exists()
    assert not machine.registry.exists()


def test_uninstall_from_an_unregistered_agent_does_nothing(machine: Machine, port: int) -> None:
    assert machine.run("--port", str(port)).code == 0
    stranger = machine.make_agent(machine.root / "inconnu")
    machine.calls()
    done = machine.run("--uninstall", agent=stranger)
    assert done.code == 0, done.err
    assert "cet agent n'est pas inscrit" in done.out and str(machine.agent) in done.out
    assert machine.systemd() == [] and machine.unit.exists()


def test_uninstall_dry_run_changes_nothing(machine: Machine, port: int) -> None:
    assert machine.run("--port", str(port)).code == 0
    before = machine.registry.read_text(encoding="utf-8")
    done = machine.run("--uninstall", "--dry-run")
    assert done.code == 0, done.err
    assert f"[dry-run] rm -rf {machine.service_venv}" in done.out
    assert machine.unit.exists() and machine.service_app.exists()
    assert machine.registry.read_text(encoding="utf-8") == before


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
