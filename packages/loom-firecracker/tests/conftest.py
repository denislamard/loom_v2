# SPDX-License-Identifier: Apache-2.0
"""Montages des essais de loom-firecracker : dossiers de VM factices et execd réel.

Un dossier de VM de test a la forme de ceux de ``make_vm.sh`` (``vm.env``,
``vm-config.json``, ``run.sh`` avec son verrou ``flock``), mais son ``run.sh``
lance ``faux_firecracker.py`` : tout ce qui se joue côté hôte est éprouvé,
pas le boot.

execd, lui, est le vrai : celui du dossier ``service/`` de la plateforme,
désigné par ``LOOM_EXECD_SERVICE`` et lancé en socket Unix (``EXECD_UDS``),
sans VM. Sans la variable, les essais qui en ont besoin sont sautés — même
sous ``--require-services`` : ce service n'est pas dans le dépôt, la CI ne
l'a pas.

Les dossiers sont pris sous ``/tmp`` avec des noms courts : un chemin de
socket Unix ne dépasse pas 107 octets.
"""

import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

SERVICE_ENV = "LOOM_EXECD_SERVICE"
FAUX = Path(__file__).with_name("faux_firecracker.py")

RUN_SH = """#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p runtime
exec 9>runtime/vm.lock
flock -n 9 || {{ echo "VM déjà lancée" >&2; exit 1; }}
printf '%s\\n' $$ >&9
rm -f runtime/v.sock runtime/v.sock_* runtime/fc.sock
exec "{python}" "{faux}" --api-sock runtime/fc.sock --config-file vm-config.json
"""


def _short_dir(prefix: str) -> Path:
    return Path(tempfile.mkdtemp(prefix=prefix, dir="/tmp"))


def write_vm(directory: Path, *, name: str = "essai", execd_port: int | None = 5100) -> Path:
    """Un dossier de VM à la manière de make_vm.sh, dont le VMM est le faux."""
    directory.mkdir(parents=True, exist_ok=True)
    lines = [
        f'VM_NAME="{name}"',
        'GUEST_CID="3"',
        f'API_SOCK="{directory}/runtime/fc.sock"',
        f'VSOCK_UDS="{directory}/runtime/v.sock"',
        'EXECD_SRC="/chemin/de/service"',
    ]
    if execd_port is not None:
        lines.append(f'EXECD_PORT="{execd_port}"')
    (directory / "vm.env").write_text("# vm.env de test\n" + "\n".join(lines) + "\n")
    (directory / "vm-config.json").write_text(
        '{"vsock": {"guest_cid": 3, "uds_path": "runtime/v.sock"}}'
    )
    run = directory / "run.sh"
    run.write_text(RUN_SH.format(python=sys.executable, faux=FAUX))
    run.chmod(0o755)
    return directory


def _kill_holder(directory: Path) -> None:
    """Tue un faux VMM resté en vie, pour ne laisser aucun orphelin."""
    lock = directory / "runtime" / "vm.lock"
    try:
        pid = int(lock.read_text().strip())
    except OSError, ValueError:
        return
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


type MakeVm = Callable[..., Path]


@pytest.fixture
def make_vm() -> Iterator[MakeVm]:
    """Fabrique de dossiers de VM factices (``write_vm``) ; leurs faux VMM sont tués à la fin."""
    made: list[Path] = []

    def make(*, name: str = "essai", execd_port: int | None = 5100) -> Path:
        root = _short_dir("fcvm-")
        made.append(root)
        return write_vm(root / "vm", name=name, execd_port=execd_port)

    try:
        yield make
    finally:
        for root in made:
            _kill_holder(root / "vm")
            shutil.rmtree(root, ignore_errors=True)


@pytest.fixture
def vm_dir(make_vm: MakeVm) -> Path:
    """Un dossier de VM factice, sous un chemin court."""
    return make_vm()


@dataclass(frozen=True, slots=True)
class Execd:
    """Un execd réel lancé en socket Unix."""

    socket: Path
    jobs: Path
    process: subprocess.Popen[bytes]


def start_execd(service: Path, root: Path, *, max_sessions: int = 4) -> Execd:
    socket_path = root / "execd.sock"
    jobs = root / "jobs"
    env = {
        **os.environ,
        "EXECD_UDS": str(socket_path),
        "EXECD_JOBS_DIR": str(jobs),
        "EXECD_PYDEPS": str(root / "pydeps"),
        "EXECD_MAX_SESSIONS": str(max_sessions),
        "EXECD_LOG_LEVEL": "WARNING",
    }
    # Pas d'abandon de droits hors de la VM : execd tourne sous notre uid.
    env.pop("EXECD_UID", None)
    env.pop("EXECD_GID", None)
    process = subprocess.Popen(
        [sys.executable, str(service / "execd.py")],
        cwd=service,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    deadline = time.monotonic() + 10
    while not socket_path.exists():
        if process.poll() is not None or time.monotonic() > deadline:
            process.kill()
            error = process.stderr.read().decode() if process.stderr else ""
            raise RuntimeError(f"execd n'a pas démarré : {error}")
        time.sleep(0.02)
    return Execd(socket=socket_path, jobs=jobs, process=process)


def stop_execd(execd: Execd) -> None:
    execd.process.terminate()
    try:
        execd.process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        execd.process.kill()
        execd.process.wait()
    if execd.process.stderr is not None:
        execd.process.stderr.close()


@pytest.fixture
def execd_service() -> Path:
    """Le dossier ``service/`` de la plateforme ; saute l'essai s'il n'est pas désigné."""
    found = os.environ.get(SERVICE_ENV, "")
    if not found:
        pytest.skip(f"{SERVICE_ENV} absent : pas d'execd réel pour cet essai")
    service = Path(found).expanduser()
    if not (service / "execd.py").is_file():
        pytest.fail(f"{SERVICE_ENV}={found} : execd.py introuvable")
    return service


type MakeExecd = Callable[..., Path]


@pytest.fixture
def make_execd(execd_service: Path) -> Iterator[MakeExecd]:
    """Fabrique d'execd réels ; rend leur socket Unix (dossier des sessions : ``jobs`` à côté)."""
    started: list[tuple[Execd, Path]] = []

    def make(*, max_sessions: int = 4) -> Path:
        root = _short_dir("fcx-")
        running = start_execd(execd_service, root, max_sessions=max_sessions)
        started.append((running, root))
        return running.socket

    try:
        yield make
    finally:
        for running, root in started:
            stop_execd(running)
            shutil.rmtree(root, ignore_errors=True)


@pytest.fixture
def execd(make_execd: MakeExecd) -> Path:
    """La socket Unix d'un execd réel."""
    return make_execd()
