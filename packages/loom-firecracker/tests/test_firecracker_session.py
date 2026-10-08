# SPDX-License-Identifier: Apache-2.0
"""``Session`` : les trames, un serveur scripté pour les pannes, puis le vrai execd.

Les essais « execd » parlent au service de la plateforme lancé en socket Unix
(``LOOM_EXECD_SERVICE``) ; ils sont sautés sans lui.
"""

import asyncio
import hashlib
import json
import shutil
import tempfile
import time
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from loom_firecracker import ExecdError, ProtocolError, Session
from loom_firecracker.session import MAX_BODY, MAX_HEADER, encode_frame, read_frame

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
