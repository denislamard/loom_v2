# SPDX-License-Identifier: Apache-2.0
"""Montages des essais de la sandbox Firecracker : dossiers de VM factices et execd réel.

Un dossier de VM de test a la forme de ceux de ``make_vm.sh`` (``vm.env``,
``vm-config.json``, ``run.sh`` avec son verrou ``flock``), mais son ``run.sh``
lance ``faux_firecracker.py`` : tout ce qui se joue côté hôte est éprouvé,
pas le boot.

execd, lui, est le vrai : celui de la plateforme, ``firecracker/service/``
dans le dépôt, lancé en socket Unix (``EXECD_UDS``), sans VM, sous le Python
des essais. ``LOOM_EXECD_SERVICE`` en désigne un autre (celui d'une VM
construite ailleurs, par exemple).

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
# Le service de la plateforme, dans le dépôt.
SERVICE = Path(__file__).parents[2] / "firecracker" / "service"
FAUX = Path(__file__).with_name("faux_firecracker.py")

# Le run.sh de make_vm.sh : il ouvre le verrou sans le tronquer (``9<>``),
# et n'y écrit son PID qu'une fois le verrou pris.
RUN_SH = """#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p runtime
exec 9<>runtime/vm.lock
flock -n 9 || {{ echo "VM déjà lancée (pid $(cat runtime/vm.lock 2>/dev/null))" >&2; exit 1; }}
printf '%s\\n' $$ > runtime/vm.lock
rm -f runtime/v.sock runtime/v.sock_* runtime/fc.sock
exec "{python}" "{faux}" --api-sock runtime/fc.sock --config-file vm-config.json
"""
# Celui d'avant le 08/10, qu'ont encore les VM construites avant : il ouvre le
# verrou avec ``>``, qui le vide avant même de tenter ``flock`` — un lancement
# refusé efface le PID de celui qui tourne.
RUN_SH_WIPING = RUN_SH.replace("exec 9<>runtime/vm.lock", "exec 9>runtime/vm.lock").replace(
    "printf '%s\\n' $$ > runtime/vm.lock", "printf '%s\\n' $$ >&9"
)


def _short_dir(prefix: str) -> Path:
    return Path(tempfile.mkdtemp(prefix=prefix, dir="/tmp"))


def write_vm(
    directory: Path, *, name: str = "essai", execd_port: int | None = 5100, run_sh: str = RUN_SH
) -> Path:
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
    run.write_text(run_sh.format(python=sys.executable, faux=FAUX))
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

    def make(*, name: str = "essai", execd_port: int | None = 5100, run_sh: str = RUN_SH) -> Path:
        root = _short_dir("fcvm-")
        made.append(root)
        return write_vm(root / "vm", name=name, execd_port=execd_port, run_sh=run_sh)

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


@pytest.fixture
def wiping_vm_dir(make_vm: MakeVm) -> Path:
    """Un dossier de VM factice au run.sh d'avant le 08/10, qui vide le PID sur un refus."""
    return make_vm(run_sh=RUN_SH_WIPING)


@dataclass(frozen=True, slots=True)
class Execd:
    """Un execd réel lancé en socket Unix."""

    socket: Path
    jobs: Path
    process: subprocess.Popen[bytes]


# Les identifiants du compte sandbox de la VM (uid 1500) : ceux de l'abandon de droits réel.
SANDBOX_UID = 1500
SANDBOX_GID = 1500


def start_execd(
    service: Path,
    root: Path,
    *,
    max_sessions: int = 4,
    drop_to: tuple[int, int] | None = None,
    python: str | None = None,
) -> Execd:
    """Lance execd ; ``drop_to=(uid, gid)`` : root, qui abandonne ses droits comme dans la VM."""
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
    # Sauf demande (``drop_to``, root requis), pas d'abandon de droits hors de la VM :
    # execd tourne sous notre uid.
    env.pop("EXECD_UID", None)
    env.pop("EXECD_GID", None)
    if drop_to is not None:
        env["EXECD_UID"], env["EXECD_GID"] = (str(number) for number in drop_to)
    process = subprocess.Popen(
        [python or sys.executable, str(service / "execd.py")],
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
    """Le dossier ``service/`` de la plateforme : celui du dépôt, ou ``LOOM_EXECD_SERVICE``."""
    found = os.environ.get(SERVICE_ENV, "")
    service = Path(found).expanduser() if found else SERVICE
    if not (service / "execd.py").is_file():
        said = f"{SERVICE_ENV}={found}" if found else str(service)
        pytest.fail(f"{said} : execd.py introuvable")
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
def root_execd(execd_service: Path) -> Iterator[Execd]:
    """Un execd réel lancé en **root**, qui abandonne ses droits pour l'uid 1500 comme dans la VM.

    Sauté hors de root, où l'abandon de droits est simulé ailleurs — sauf si
    ``LOOM_TEST_AS_ROOT`` est posée : la CI qui lance cet essai sous ``sudo`` la pose, et un
    essai sauté y serait un job vert qui n'a rien éprouvé. Le service est copié
    sous ``/tmp`` (lisible par le compte sandbox, où que soit le dépôt) et lancé par
    ``/usr/bin/python3``, l'interpréteur du système, que ce compte peut exécuter — celui des
    essais vit souvent sous le dossier personnel de l'utilisateur, fermé aux autres.
    """
    expected = bool(os.environ.get("LOOM_TEST_AS_ROOT"))
    absent = pytest.fail if expected else pytest.skip
    if os.geteuid() != 0:
        absent("abandon de droits réel : exige root (LOOM_TEST_AS_ROOT=1 sous sudo, en CI)")
    system_python = Path("/usr/bin/python3")
    if not os.access(system_python, os.X_OK):
        absent("/usr/bin/python3 absent")
    root = _short_dir("fcr-")
    root.chmod(0o711)  # le compte sandbox traverse, il ne liste ni n'écrit
    service = root / "service"
    shutil.copytree(execd_service, service, ignore=shutil.ignore_patterns("__pycache__"))
    subprocess.run(["chmod", "-R", "a+rX", str(service)], check=True)
    # root n'a souvent aucun groupe supplémentaire : on lui en donne un, sinon l'essai ne
    # verrait pas la différence entre « groupes retirés » et « jamais eus ».
    groups_before = os.getgroups()
    os.setgroups([0, SANDBOX_GID + 1])
    try:
        running = start_execd(
            service, root, drop_to=(SANDBOX_UID, SANDBOX_GID), python=str(system_python)
        )
    finally:
        os.setgroups(groups_before)
    try:
        yield running
    finally:
        stop_execd(running)
        shutil.rmtree(root, ignore_errors=True)


@pytest.fixture
def execd(make_execd: MakeExecd) -> Path:
    """La socket Unix d'un execd réel."""
    return make_execd()
