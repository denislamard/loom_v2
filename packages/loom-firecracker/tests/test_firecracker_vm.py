# SPDX-License-Identifier: Apache-2.0
"""``Vm`` contre un faux firecracker : vm.env, verrou de run.sh, démarrage, arrêt, vsock.

Le boot n'est pas éprouvé ici (pas de KVM) : c'est ce que fait
``scripts/essai_vm.py`` sur une vraie VM.
"""

import asyncio
import os
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from loom_firecracker import Session, Vm, VmError, VsockRefused

type MakeVm = Callable[..., Path]

FAUX = Path(__file__).with_name("faux_firecracker.py")


def _rewrite(vm_dir: Path, old: str, new: str) -> None:
    env = vm_dir / "vm.env"
    env.write_text(env.read_text().replace(old, new))


async def _until(check: Callable[[], bool], wait: float = 10.0) -> None:
    deadline = time.monotonic() + wait
    while not check():
        assert time.monotonic() < deadline, "condition jamais atteinte"
        await asyncio.sleep(0.02)


# ---------------------------------------------------------------------- #
# vm.env
# ---------------------------------------------------------------------- #


def test_load_reads_vm_env(vm_dir: Path) -> None:
    vm = Vm.load(vm_dir)
    assert vm.name == "essai"
    assert vm.cid == 3
    assert vm.api_sock == vm_dir / "runtime" / "fc.sock"
    assert vm.vsock_uds == vm_dir / "runtime" / "v.sock"
    assert vm.directory == vm_dir.resolve()
    assert vm.execd_port == 5100
    assert vm.execd_src == "/chemin/de/service"


def test_load_without_execd_port(make_vm: MakeVm) -> None:
    assert Vm.load(make_vm(execd_port=None)).execd_port is None


def test_load_names_the_missing_key(vm_dir: Path) -> None:
    _rewrite(vm_dir, "VSOCK_UDS=", "AUTRE=")
    with pytest.raises(VmError, match="VSOCK_UDS"):
        Vm.load(vm_dir)


def test_load_refuses_an_unreadable_number(vm_dir: Path) -> None:
    _rewrite(vm_dir, 'GUEST_CID="3"', 'GUEST_CID="trois"')
    with pytest.raises(VmError, match="illisible"):
        Vm.load(vm_dir)


def test_load_refuses_a_folder_without_vm_env(tmp_path: Path) -> None:
    with pytest.raises(VmError, match="introuvable"):
        Vm.load(tmp_path)


def test_a_never_started_vm_is_not_running_and_nothing_is_created(vm_dir: Path) -> None:
    vm = Vm.load(vm_dir)
    assert not vm.is_running()
    assert not (vm_dir / "runtime").exists()


# ---------------------------------------------------------------------- #
# Démarrage
# ---------------------------------------------------------------------- #


async def test_start_then_stop_by_the_console(vm_dir: Path) -> None:
    vm = Vm.load(vm_dir)
    await vm.start(wait=10)
    assert vm.is_running()
    pid = vm.pid
    assert pid is not None
    os.kill(pid, 0)
    assert (await vm.info())["state"] == "Running"
    assert "faux firecracker : prêt" in vm.console_log.read_text()

    assert await vm.stop(grace=5, wait=5) == "console"
    assert not vm.is_running()
    assert not await vm.is_alive()
    assert await vm.stop() is None


async def test_start_refuses_a_running_vm(vm_dir: Path) -> None:
    vm = Vm.load(vm_dir)
    await vm.start(wait=10)
    with pytest.raises(VmError, match="tourne déjà"):
        await Vm.load(vm_dir).start(wait=10)
    assert await vm.stop(grace=5, wait=5) == "console"


async def test_two_launchers_get_one_vm(vm_dir: Path) -> None:
    first, second = Vm.load(vm_dir), Vm.load(vm_dir)
    started = await asyncio.gather(first.ensure_started(wait=10), second.ensure_started(wait=10))
    assert sorted(started) == [False, True]
    assert await first.ensure_started(wait=10) is False
    assert (await second.info())["state"] == "Running"
    assert await first.stop(grace=5, wait=5) == "console"


async def test_a_second_run_sh_wipes_the_pid_and_stop_still_uses_the_console(
    vm_dir: Path,
) -> None:
    # run.sh rouvre le verrou avec « > » avant flock : un second lancement,
    # refusé, efface le PID que le premier y avait inscrit.
    vm = Vm.load(vm_dir)
    await vm.start(wait=10)
    refused = await asyncio.create_subprocess_exec(
        vm_dir / "run.sh", stdout=subprocess.DEVNULL, stderr=subprocess.PIPE
    )
    _, said = await refused.communicate()
    assert refused.returncode == 1
    assert "déjà lancée" in said.decode()
    assert vm.is_running()
    assert vm.pid is None
    assert await vm.stop(grace=5, wait=5) == "console"


async def test_a_wiped_pid_still_lets_the_signals_through(
    vm_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAUX_IGNORE", "console,acpi")
    vm = Vm.load(vm_dir)
    await vm.start(wait=10)
    refused = await asyncio.create_subprocess_exec(
        vm_dir / "run.sh", stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    assert await refused.wait() == 1
    assert vm.pid is None
    assert await vm.stop(grace=0.5, wait=5) == "sigterm"


async def test_start_reports_a_crash_with_the_console(
    vm_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAUX_CRASH", "noyau introuvable")
    with pytest.raises(VmError, match=r"code 1[\s\S]*noyau introuvable"):
        await Vm.load(vm_dir).start(wait=10)


async def test_start_refuses_a_vmm_that_holds_no_lock(vm_dir: Path) -> None:
    (vm_dir / "runtime").mkdir()
    stray = await asyncio.create_subprocess_exec(
        sys.executable,
        FAUX,
        "--api-sock",
        "runtime/fc.sock",
        "--config-file",
        "vm-config.json",
        cwd=vm_dir,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
    )
    try:
        vm = Vm.load(vm_dir)
        deadline = time.monotonic() + 10
        while not await vm.is_alive():
            assert time.monotonic() < deadline, "le VMM sans verrou ne répond pas"
            await asyncio.sleep(0.02)
        with pytest.raises(VmError, match="sans tenir le verrou"):
            await vm.start(wait=10)
    finally:
        stray.kill()
        await stray.wait()


# ---------------------------------------------------------------------- #
# Arrêt
# ---------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("ignored", "expected"),
    [("console", "acpi"), ("console,acpi", "sigterm"), ("console,acpi,sigterm", "sigkill")],
)
async def test_stop_escalates(
    vm_dir: Path, monkeypatch: pytest.MonkeyPatch, ignored: str, expected: str
) -> None:
    monkeypatch.setenv("FAUX_IGNORE", ignored)
    vm = Vm.load(vm_dir)
    await vm.start(wait=10)
    assert await vm.stop(grace=0.5, wait=5) == expected
    assert not vm.is_running()
    assert not await vm.is_alive()


async def test_a_vm_launched_elsewhere_stops_through_the_api(vm_dir: Path) -> None:
    # Lancée hors de ce processus : nous ne tenons pas sa console.
    elsewhere = await asyncio.create_subprocess_exec(
        vm_dir / "run.sh",
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        vm = Vm.load(vm_dir)
        await _until(vm.is_running)
        await vm.ensure_started(wait=10)
        assert not vm.console_shutdown()
        assert await vm.stop(grace=5, wait=5) == "acpi"
    finally:
        if elsewhere.returncode is None:
            elsewhere.kill()
        await elsewhere.wait()


# ---------------------------------------------------------------------- #
# vsock
# ---------------------------------------------------------------------- #


async def test_connect_to_a_stopped_vm(vm_dir: Path) -> None:
    with pytest.raises(VmError, match="injoignable") as caught:
        await Vm.load(vm_dir).connect(5100, wait=0.2)
    assert not isinstance(caught.value, VsockRefused)


async def test_connect_is_refused_when_nothing_listens(vm_dir: Path) -> None:
    vm = Vm.load(vm_dir)
    await vm.start(wait=10)
    try:
        with pytest.raises(VsockRefused, match="5100"):
            await vm.connect(5100, wait=0.3)
    finally:
        await vm.stop(grace=5, wait=5)


async def test_connect_waits_for_the_guest_to_boot(
    vm_dir: Path, execd: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAUX_EXECD", str(execd))
    monkeypatch.setenv("FAUX_BOOT", "1.0")
    vm = Vm.load(vm_dir)
    await vm.start(wait=10)
    try:
        with pytest.raises(VsockRefused):
            await vm.connect(5101, wait=0.2)
        began = time.monotonic()
        reader, writer = await vm.connect(5100, wait=10)
        assert time.monotonic() - began > 0.5
        async with await Session.open(reader, writer) as session:
            assert session.hello.protocol == 1
    finally:
        await vm.stop(grace=5, wait=5)


async def test_from_the_vm_folder_to_a_job(
    vm_dir: Path, execd: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAUX_EXECD", str(execd))
    vm = Vm.load(vm_dir)
    assert await vm.ensure_started(wait=10) is True
    assert vm.execd_port is not None
    reader, writer = await vm.connect(vm.execd_port, wait=10)
    async with await Session.open(reader, writer) as session:
        await session.put_code("calcul.py", b"def double(n):\n    return 2 * n\n")
        done = await session.exec("calcul:double", args={"n": 21})
    assert done.ok
    assert done.result == 42
    assert await vm.stop(grace=5, wait=5) == "console"
