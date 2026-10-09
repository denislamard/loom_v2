# SPDX-License-Identifier: Apache-2.0
"""``Session`` : les trames, un serveur scripté pour les pannes, puis le vrai execd.

Les essais « execd » parlent au service de la plateforme, ``firecracker/service/``,
lancé en socket Unix.
"""

import asyncio
import hashlib
import importlib
import json
import os
import shutil
import stat
import sys
import tempfile
import time
from collections.abc import AsyncGenerator, Awaitable, Callable, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from loom_ia.adapters.firecracker import ExecdError, ProtocolError, Session
from loom_ia.adapters.firecracker.session import MAX_BODY, MAX_HEADER, encode_frame, read_frame

type MakeExecd = Callable[..., Path]
type Reply = Callable[[dict[str, object]], Awaitable[dict[str, object] | None]]

HELLO_OK: dict[str, object] = {
    "ok": True,
    "protocol": 1,
    "session_id": "s1",
    "python": "3.12.3",
    "runner": "0.1.0",
    "caps": {"max_body": MAX_BODY, "max_code_bytes": 1 << 24, "max_upload_total": 1 << 31},
    "limits_ceiling": {"wall_ms": 300_000},
}

SCRIPT = b"""import os, sys, time


def principal(n):
    print("sortie", n)
    print("erreur", n, file=sys.stderr)
    with open(os.path.join(os.environ["OUT_DIR"], "carres.txt"), "w") as f:
        f.write(" ".join(str(i * i) for i in range(n)))
    return {"n": n, "somme": sum(range(n))}


def echoue():
    print("avant l'erreur")
    raise ValueError("valeur refusee")


def dort(secondes):
    time.sleep(secondes)


def taille(nom):
    return os.path.getsize(os.path.join(os.environ["CODE_DIR"], nom))


def lit(nom):
    with open(os.path.join(os.environ["CODE_DIR"], nom)) as f:
        return f.read()


def recopie(nom):
    with open(os.path.join(os.environ["CODE_DIR"], nom), "rb") as source:
        with open(os.path.join(os.environ["OUT_DIR"], nom), "wb") as copie:
            copie.write(source.read())
"""


def _reader(data: bytes) -> asyncio.StreamReader:
    reader = asyncio.StreamReader()
    reader.feed_data(data)
    reader.feed_eof()
    return reader


async def _open(socket: Path) -> Session:
    reader, writer = await asyncio.open_unix_connection(socket)
    return await Session.open(reader, writer)


# ---------------------------------------------------------------------- #
# Trames
# ---------------------------------------------------------------------- #


async def test_a_frame_reads_back() -> None:
    frame = encode_frame({"method": "file.put", "path": "é.txt"}, b"\x00\x01")
    header, body = await read_frame(_reader(frame))
    assert header == {"method": "file.put", "path": "é.txt"}
    assert body == b"\x00\x01"


def test_an_outgoing_body_over_the_bound_is_refused() -> None:
    with pytest.raises(ProtocolError, match="corps sortant"):
        encode_frame({}, b"x" * (MAX_BODY + 1))


@pytest.mark.parametrize(
    ("data", "said"),
    [
        ((0).to_bytes(4, "big"), "hors bornes"),
        ((MAX_HEADER + 1).to_bytes(4, "big"), "hors bornes"),
        ((2).to_bytes(4, "big") + b"[]" + (0).to_bytes(4, "big"), "objet attendu"),
        ((2).to_bytes(4, "big") + b"{x" + (0).to_bytes(4, "big"), "JSON invalide"),
        ((2).to_bytes(4, "big") + b"{}" + (MAX_BODY + 1).to_bytes(4, "big"), "hors bornes"),
        ((2).to_bytes(4, "big") + b"{}" + (5).to_bytes(4, "big") + b"ab", "au milieu"),
    ],
)
async def test_an_unreadable_frame_is_a_protocol_error(data: bytes, said: str) -> None:
    with pytest.raises(ProtocolError, match=said):
        await read_frame(_reader(data))


# ---------------------------------------------------------------------- #
# Serveur scripté : ce qu'execd ne fait pas de lui-même
# ---------------------------------------------------------------------- #


@asynccontextmanager
async def _scripted(reply: Reply) -> AsyncGenerator[Path]:
    """Un serveur qui répond à chaque requête par ``reply`` ; None = ne répond pas."""
    folder = tempfile.mkdtemp(prefix="fcs-", dir="/tmp")
    path = Path(folder) / "s.sock"

    async def serve(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while True:
                header, _ = await read_frame(reader)
                answer = await reply(header)
                if answer is not None:
                    writer.write(encode_frame(answer))
                    await writer.drain()
        except ProtocolError:
            pass
        finally:
            writer.close()

    server = await asyncio.start_unix_server(serve, path=str(path))
    try:
        yield path
    finally:
        # Les clients d'abord : un essai en échec laisse sa session ouverte,
        # et wait_closed attendrait qu'elle se ferme. Borné : une session
        # que le client ne ferme pas fait tomber l'essai au lieu de le figer.
        server.close()
        server.close_clients()
        async with asyncio.timeout(5):
            await server.wait_closed()
        shutil.rmtree(folder, ignore_errors=True)


async def test_a_reply_out_of_sequence_closes_the_session() -> None:
    async def reply(header: dict[str, object]) -> dict[str, object]:
        if header["method"] == "hello":
            return {**HELLO_OK, "seq": header["seq"]}
        return {"ok": True, "seq": 99}

    async with _scripted(reply) as path:
        session = await _open(path)
        with pytest.raises(ProtocolError, match="désynchronisée"):
            await session.reset()
        assert session.closed
        with pytest.raises(ProtocolError, match="session fermée"):
            await session.reset()


async def test_a_silent_execd_times_out_and_the_session_closes() -> None:
    async def reply(header: dict[str, object]) -> dict[str, object] | None:
        if header["method"] == "hello":
            return {**HELLO_OK, "seq": header["seq"]}
        return None

    async with _scripted(reply) as path:
        session = await _open(path)
        began = time.monotonic()
        with pytest.raises(TimeoutError, match=r"tool\.exec en 0\.3 s"):
            await session.exec("m:f", wait=0.3)
        assert time.monotonic() - began < 2
        assert session.closed


async def test_a_cancelled_request_closes_the_session() -> None:
    async def reply(header: dict[str, object]) -> dict[str, object] | None:
        if header["method"] == "hello":
            return {**HELLO_OK, "seq": header["seq"]}
        return None

    async with _scripted(reply) as path:
        session = await _open(path)
        running = asyncio.create_task(session.exec("m:f"))
        await asyncio.sleep(0.2)
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running
        # Sa réponse arriverait plus tard et passerait pour celle de la suivante.
        assert session.closed
        with pytest.raises(ProtocolError, match="session fermée"):
            await session.reset()


async def test_another_protocol_version_is_refused() -> None:
    async def reply(header: dict[str, object]) -> dict[str, object]:
        return {**HELLO_OK, "protocol": 2, "seq": header["seq"]}

    async with _scripted(reply) as path:
        reader, writer = await asyncio.open_unix_connection(path)
        with pytest.raises(ProtocolError, match="protocole 2"):
            await Session.open(reader, writer)
        assert writer.is_closing()


async def test_the_exec_wait_follows_the_wall_limit() -> None:
    asked: list[dict[str, object]] = []

    async def reply(header: dict[str, object]) -> dict[str, object] | None:
        asked.append(header)
        if header["method"] == "hello":
            return {**HELLO_OK, "seq": header["seq"]}
        return None

    async with _scripted(reply) as path:
        session = await _open(path)
        began = time.monotonic()
        with pytest.raises(TimeoutError):
            # wall_ms tout petit : l'attente vaut ~ EXEC_MARGIN, pas l'infini.
            await session.exec("m:f", limits={"wall_ms": 1}, wait=0.2)
        assert time.monotonic() - began < 2
    assert asked[-1]["limits"] == {"wall_ms": 1}
    assert asked[-1]["args"] == {}
    assert "env" not in asked[-1]


# ---------------------------------------------------------------------- #
# Le vrai execd
# ---------------------------------------------------------------------- #


async def test_hello_tells_the_guest(execd: Path) -> None:
    async with await _open(execd) as session:
        assert session.hello.protocol == 1
        assert session.hello.session_id
        assert session.hello.python.count(".") == 2
        assert session.hello.ceilings["wall_ms"] == 300_000
        assert session.hello.max_body == MAX_BODY


async def test_a_job_runs_and_its_output_comes_back(execd: Path) -> None:
    async with await _open(execd) as session:
        await session.put_code("essai.py", SCRIPT)
        done = await session.exec("essai:principal", args={"n": 5})
        assert done.ok, done.error
        assert done.error is None
        assert done.result == {"n": 5, "somme": 10}
        assert done.stdout == "sortie 5\n"
        assert done.stderr == "erreur 5\n"
        assert not done.stdout_truncated
        assert done.duration_ms is not None
        assert done.limits_applied["wall_ms"] == 30_000
        [output] = done.outputs
        assert output.path == "carres.txt"
        data = await session.get_file("carres.txt")
        assert data == b"0 1 4 9 16"
        assert output.size == len(data)
        assert output.sha256 == hashlib.sha256(data).hexdigest()


async def test_a_failing_job_is_an_execution_not_an_exception(execd: Path) -> None:
    async with await _open(execd) as session:
        await session.put_code("essai.py", SCRIPT)
        done = await session.exec("essai:echoue")
        assert not done.ok
        assert done.error is not None
        assert done.error.kind == "tool_raised"
        assert "valeur refusee" in done.error.message
        assert "essai.py" in json.dumps(done.error.detail)
        assert done.stdout == "avant l'erreur\n"
        # La session sert encore.
        assert (await session.exec("essai:principal", args={"n": 1})).ok


async def test_a_job_over_its_wall_limit_is_stopped(execd: Path) -> None:
    async with await _open(execd) as session:
        await session.put_code("essai.py", SCRIPT)
        began = time.monotonic()
        done = await session.exec("essai:dort", args={"secondes": 5}, limits={"wall_ms": 500})
        assert time.monotonic() - began < 4
        assert done.error is not None
        assert done.error.kind == "timeout"


async def test_limits_over_the_ceiling_are_capped(execd: Path) -> None:
    async with await _open(execd) as session:
        await session.put_code("essai.py", SCRIPT)
        done = await session.exec(
            "essai:principal", args={"n": 1}, limits={"wall_ms": 10**9}, wait=60
        )
        assert done.ok
        assert done.limits_applied["wall_ms"] == session.hello.ceilings["wall_ms"]


async def test_a_refusal_leaves_the_session_usable(execd: Path) -> None:
    async with await _open(execd) as session:
        with pytest.raises(ExecdError) as caught:
            await session.put_code("module.so", b"\x7fELF")
        assert caught.value.kind == "native_not_allowed"
        with pytest.raises(ExecdError) as caught:
            await session.put_file("../hors.txt", b"x")
        assert caught.value.kind == "bad_path"
        await session.put_code("essai.py", SCRIPT)
        assert (await session.exec("essai:principal", args={"n": 2})).ok


async def test_a_large_file_goes_and_comes_back_in_several_pieces(execd: Path) -> None:
    big = bytes(range(256)) * (10 * 1024 + 7)
    async with await _open(execd) as session:
        await session.put_code("essai.py", SCRIPT)
        await session.put_code("gros.txt", big)
        done = await session.exec("essai:taille", args={"nom": "gros.txt"})
        assert done.result == len(big)
        assert (await session.exec("essai:recopie", args={"nom": "gros.txt"})).ok
        assert await session.get_file("gros.txt") == big


async def test_a_file_put_twice_holds_the_second_content(execd: Path) -> None:
    async with await _open(execd) as session:
        await session.put_code("essai.py", SCRIPT)
        await session.put_code("note.txt", b"premier")
        await session.put_code("note.txt", b"2e")
        assert (await session.exec("essai:lit", args={"nom": "note.txt"})).result == "2e"


async def test_get_file_stops_at_its_bound(execd: Path) -> None:
    async with await _open(execd) as session:
        await session.put_code("essai.py", SCRIPT)
        assert (await session.exec("essai:principal", args={"n": 50})).ok
        with pytest.raises(ValueError, match="dépasse 10 octets"):
            await session.get_file("carres.txt", max_bytes=10)
        with pytest.raises(ExecdError) as caught:
            await session.get_file("absent.txt")
        assert caught.value.kind == "not_found"


async def test_reset_empties_the_session(execd: Path) -> None:
    async with await _open(execd) as session:
        await session.put_code("essai.py", SCRIPT)
        await session.reset()
        done = await session.exec("essai:principal", args={"n": 1})
        assert done.error is not None
        assert done.error.kind == "code_invalid"


async def test_sessions_over_the_limit_are_busy(make_execd: MakeExecd) -> None:
    socket = make_execd(max_sessions=2)
    first, second = await _open(socket), await _open(socket)
    try:
        with pytest.raises(ExecdError) as caught:
            await _open(socket)
        assert caught.value.kind == "busy"
    finally:
        await first.close()
        await second.close()


def _sessions(jobs: Path) -> list[str]:
    return [entry.name for entry in jobs.iterdir()]


async def _erased(jobs: Path, wait: float) -> None:
    deadline = time.monotonic() + wait
    while _sessions(jobs):
        assert time.monotonic() < deadline, "dossier de session jamais effacé"
        await asyncio.sleep(0.05)


# execd lit la connexion pendant un job : une session fermée arrête son job
# tout de suite — sa place d'exécution est rendue, son dossier effacé —, au
# lieu de le laisser courir jusqu'à son wall_ms.


async def test_closing_during_a_job_stops_it_and_erases_the_session(execd: Path) -> None:
    jobs = execd.with_name("jobs")
    session = await _open(execd)
    await session.put_code("essai.py", SCRIPT)
    running = asyncio.create_task(
        session.exec("essai:dort", args={"secondes": 30}, limits={"wall_ms": 20_000})
    )
    await asyncio.sleep(0.5)
    assert _sessions(jobs) == [session.hello.session_id]
    await session.close()
    with pytest.raises(ProtocolError):
        await running
    await _erased(jobs, wait=1.5)


async def test_a_job_left_by_a_closed_session_gives_its_place_back(execd: Path) -> None:
    """Un job à la fois (``EXECD_MAX_CONCURRENT_EXEC``, 1 par défaut) : le suivant n'attend pas."""
    left = await _open(execd)
    await left.put_code("essai.py", SCRIPT)
    running = asyncio.create_task(
        left.exec("essai:dort", args={"secondes": 30}, limits={"wall_ms": 20_000})
    )
    await asyncio.sleep(0.5)
    await left.close()
    with pytest.raises(ProtocolError):
        await running
    other = await _open(execd)
    try:
        await other.put_code("essai.py", SCRIPT)
        begun = time.monotonic()
        done = await other.exec("essai:principal", args={"n": 3}, wait=5)
        assert done.ok and time.monotonic() - begun < 3
    finally:
        await other.close()


async def test_a_request_sent_during_a_job_waits_its_turn(execd: Path) -> None:
    """Le protocole est séquentiel : une requête envoyée en avance est servie après le job."""
    reader, writer = await asyncio.open_unix_connection(execd)
    try:
        writer.write(
            encode_frame({"method": "code.put", "seq": 1, "path": "essai.py", "eof": True}, SCRIPT)
        )
        writer.write(
            encode_frame(
                {
                    "method": "tool.exec",
                    "seq": 2,
                    "entrypoint": "essai:dort",
                    "args": {"secondes": 0.5},
                    "limits": {"wall_ms": 5000},
                }
            )
        )
        writer.write(encode_frame({"method": "hello", "seq": 3, "protocol": 1}))
        await writer.drain()
        replies = [(await asyncio.wait_for(read_frame(reader), 10))[0] for _ in range(3)]
    finally:
        writer.close()
    assert [reply.get("seq") for reply in replies] == [1, 2, 3]
    assert replies[1]["ok"] is True and replies[1]["result"] is None
    assert replies[2]["protocol"] == 1


async def test_a_host_wait_shorter_than_the_job_closes_the_session(execd: Path) -> None:
    jobs = execd.with_name("jobs")
    session = await _open(execd)
    await session.put_code("essai.py", SCRIPT)
    with pytest.raises(TimeoutError):
        await session.exec(
            "essai:dort", args={"secondes": 30}, limits={"wall_ms": 20_000}, wait=0.5
        )
    assert session.closed
    await _erased(jobs, wait=1.5)


# ---------------------------------------------------------------------- #
# Les liens symboliques que le job pose dans ce qui lui appartient
# ---------------------------------------------------------------------- #
#
# execd tourne en root dans la VM, le job sous l'uid 1500. Un lien posé par le job ne doit
# jamais faire changer le propriétaire, le mode ni le contenu de sa cible. Ces essais se
# passent de root : ``os.geteuid`` rend 0, et ``chown`` et ``fchown`` sont des espions qui
# notent l'objet que le noyau aurait atteint, sans rien changer.

type FileId = tuple[int, int]


def _file_id(path: Path, *, follow: bool = True) -> FileId:
    found = path.stat() if follow else path.lstat()
    return found.st_dev, found.st_ino


class _Chowns:
    """Espion de ``chown`` et ``fchown`` : retient les objets que le noyau aurait atteints."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.reached: set[FileId] = set()
        monkeypatch.setattr(os, "geteuid", lambda: 0)
        monkeypatch.setattr(os, "chown", self._chown)
        monkeypatch.setattr(os, "fchown", self._fchown)

    def _chown(
        self,
        path: str | os.PathLike[str],
        uid: int,
        gid: int,
        *,
        dir_fd: int | None = None,
        follow_symlinks: bool = True,
    ) -> None:
        found = os.stat(path, dir_fd=dir_fd, follow_symlinks=follow_symlinks)
        self.reached.add((found.st_dev, found.st_ino))

    def _fchown(self, fd: int, uid: int, gid: int) -> None:
        found = os.fstat(fd)
        self.reached.add((found.st_dev, found.st_ino))


@pytest.fixture
def service(execd_service: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[ModuleType]:
    """Le module ``session`` du service, importé comme la VM le fait (voisins à plat)."""
    names = ("session", "protocol", "limits")
    for name in names:
        monkeypatch.delitem(sys.modules, name, raising=False)
    monkeypatch.setattr(sys, "path", [str(execd_service), *sys.path])
    importlib.invalidate_caches()
    try:
        yield importlib.import_module("session")
    finally:
        for name in names:
            sys.modules.pop(name, None)


def _guest_session(service: ModuleType, jobs: Path) -> Any:
    cfg = SimpleNamespace(jobs_dir=str(jobs), uid=1500, gid=1500, version="essai")
    return service.Session(conn=None, cfg=cfg, exec_sem=None)


def test_the_session_root_stays_root_s_and_the_sandbox_gets_its_folders(
    service: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """La racine reste à root (0711) ; le sandbox reçoit ses dossiers (0700), pas la racine."""
    chowns = _Chowns(monkeypatch)
    session = _guest_session(service, tmp_path / "jobs")
    session.setup()
    root: Path = session.root
    assert stat.S_IMODE(root.stat().st_mode) == 0o711
    assert sorted(entry.name for entry in root.iterdir()) == ["code", "in", "out", "run", "work"]
    for entry in root.iterdir():
        assert stat.S_IMODE(entry.stat().st_mode) == 0o700
        assert _file_id(entry) in chowns.reached
    assert _file_id(root) not in chowns.reached


def test_owning_a_tree_never_follows_a_link_planted_in_it(
    service: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Le sandbox reçoit l'arbre et ses liens, jamais les fichiers de root où ils mènent."""
    victim = tmp_path / "victime"
    victim.write_text("a root")
    folder = tmp_path / "dossier"
    folder.mkdir()
    (folder / "contenu").write_text("a root")
    tree = tmp_path / "arbre"
    tree.mkdir()
    (tree / "a.txt").write_text("a")
    (tree / "d").mkdir()
    (tree / "d" / "b.txt").write_text("b")
    (tree / "d" / "vers_fichier").symlink_to(victim)
    (tree / "vers_dossier").symlink_to(folder)
    (tree / "pendu").symlink_to(tmp_path / "absent")
    head = tmp_path / "tete"
    head.symlink_to(folder)  # le dossier lui-même remplacé par un lien

    chowns = _Chowns(monkeypatch)
    session = _guest_session(service, tmp_path / "jobs")
    session._own(tree)
    session._own(head)

    for protected in (victim, folder, folder / "contenu"):
        assert _file_id(protected) not in chowns.reached
    ours = [tree, tree / "a.txt", tree / "d", tree / "d" / "b.txt"]
    assert {_file_id(path) for path in ours} <= chowns.reached
    assert _file_id(tree / "d" / "vers_fichier", follow=False) in chowns.reached


async def test_reset_does_not_follow_a_link_left_in_the_session_root(
    service: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un lien posé à côté des dossiers ne mène pas le reset à donner sa cible au sandbox."""
    victim = tmp_path / "victime"
    victim.write_text("a root")
    session = _guest_session(service, tmp_path / "jobs")
    session.setup()
    (session.root / "piege").symlink_to(victim)

    chowns = _Chowns(monkeypatch)
    await session.m_reset({}, b"")
    assert _file_id(victim) not in chowns.reached
    assert _file_id(session.root) not in chowns.reached
    assert _file_id(session.root / "work") in chowns.reached


def test_job_json_is_recreated_without_writing_through_a_link(
    service: ModuleType, tmp_path: Path
) -> None:
    """job.json est recréé : un lien à sa place n'est pas traversé, et le mode reste 0644."""
    victim = tmp_path / "victime.conf"
    victim.write_text("CONFIG\n")
    victim.chmod(0o600)
    root = tmp_path / "racine"
    root.mkdir()
    job_path, result_path = root / "job.json", root / "result.json"
    job_path.symlink_to(victim)
    result_path.write_text("ancien")

    previous = os.umask(0o077)
    try:
        service._write_job(job_path, {"entrypoint": "x:y"}, result_path)
        service._write_job(job_path, {"entrypoint": "z:w"}, result_path)  # job.json existe déjà
    finally:
        os.umask(previous)

    assert victim.read_text() == "CONFIG\n"
    assert stat.S_IMODE(victim.stat().st_mode) == 0o600
    assert not job_path.is_symlink()
    assert json.loads(job_path.read_text()) == {"entrypoint": "z:w"}
    assert stat.S_IMODE(job_path.stat().st_mode) == 0o644
    assert not result_path.exists()


async def test_the_end_of_a_put_sets_the_file_it_wrote_not_the_name(
    service: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un lien posé à la place du fichier au moment du mode et du propriétaire n'est pas suivi."""
    victim = tmp_path / "victime"
    victim.write_text("a root")
    victim.chmod(0o600)
    session = _guest_session(service, tmp_path / "jobs")
    session.setup()
    target: Path = session.root / "in" / "f.txt"

    chowns = _Chowns(monkeypatch)
    written: list[FileId] = []

    def swap() -> None:
        # Le nom devient un lien à l'instant où execd pose le mode du fichier.
        if not written:
            written.append(_file_id(target))
            target.unlink()
            target.symlink_to(victim)

    real_chmod, real_fchmod = os.chmod, os.fchmod

    def chmod(path: str | os.PathLike[str], mode: int) -> None:
        swap()
        real_chmod(path, mode)

    def fchmod(fd: int, mode: int) -> None:
        swap()
        real_fchmod(fd, mode)

    monkeypatch.setattr(os, "chmod", chmod)
    monkeypatch.setattr(os, "fchmod", fchmod)

    reply, _ = await session.m_file_put({"path": "f.txt", "eof": True}, b"contenu")
    assert reply["ok"] is True
    assert stat.S_IMODE(victim.stat().st_mode) == 0o600
    assert _file_id(victim) not in chowns.reached
    assert written
    assert written[0] in chowns.reached


async def test_a_put_refuses_a_folder_replaced_by_a_link(
    service: ModuleType, tmp_path: Path
) -> None:
    """Un dossier de la session devenu lien : rien n'est écrit au-delà, tout chemin y passait."""
    outside = tmp_path / "dehors"
    outside.mkdir()
    session = _guest_session(service, tmp_path / "jobs")
    session.setup()
    shutil.rmtree(session.root / "in")
    (session.root / "in").symlink_to(outside)

    with pytest.raises(service.ExecdError) as caught:
        await session.m_file_put({"path": "f.txt", "eof": True}, b"contenu")
    assert caught.value.kind == "bad_path"
    assert caught.value.detail["reason"] == "symlink"
    assert list(outside.iterdir()) == []
