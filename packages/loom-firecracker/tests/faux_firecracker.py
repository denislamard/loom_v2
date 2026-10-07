# SPDX-License-Identifier: Apache-2.0
"""Un faux firecracker, pour éprouver ``Vm`` sans KVM.

Lancé par le ``run.sh`` d'un dossier de VM de test, avec les mêmes options
que le vrai (``--api-sock``, ``--config-file``). Il fait ce que l'hôte voit
d'un VMM, et rien de plus :

- l'API HTTP sur socket Unix : ``GET /``, ``GET /machine-config``,
  ``PUT /actions`` (``SendCtrlAltDel`` arrête le processus) ;
- la console série sur stdin : une ligne ``reboot`` arrête le processus ;
- le vsock côté hôte : ``CONNECT <port>`` reçoit ``OK <n>`` puis est relayé
  vers la socket Unix d'un execd lancé avec ``EXECD_UDS``, si ``port`` est
  celui qu'on relaie et que le « boot » est fini ; sinon la connexion est
  fermée sans réponse, comme quand rien n'écoute dans l'invité.

Réglages par variables d'environnement :

- ``FAUX_EXECD`` : socket Unix de l'execd vers lequel relayer ;
- ``FAUX_PORT`` : port vsock relayé (5100 par défaut) ;
- ``FAUX_BOOT`` : secondes pendant lesquelles l'invité « démarre » ;
- ``FAUX_IGNORE`` : arrêts ignorés, parmi ``console``, ``acpi``, ``sigterm`` ;
- ``FAUX_CRASH`` : écrit ce texte sur la console et sort aussitôt en code 1.
"""

import argparse
import asyncio
import contextlib
import json
import os
import signal
import sys
from pathlib import Path


def _stop() -> None:
    # Comme le vrai à la fin de l'invité : le processus sort, ses sockets restent.
    os._exit(0)


async def _api(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    ignored = os.environ.get("FAUX_IGNORE", "").split(",")
    try:
        head = await reader.readuntil(b"\r\n\r\n")
        lines = head.decode("latin-1").split("\r\n")
        method, path = lines[0].split()[:2]
        length = 0
        for line in lines[1:]:
            key, _, value = line.partition(":")
            if key.strip().lower() == "content-length":
                length = int(value.strip())
        body = await reader.readexactly(length) if length else b""
        status, answer = 400, {"fault_message": f"{method} {path} inconnu du faux"}
        if method == "GET" and path == "/":
            status, answer = 200, {"id": "faux", "state": "Running", "vmm_version": "faux"}
        elif method == "GET" and path == "/machine-config":
            status, answer = 200, {"vcpu_count": 2, "mem_size_mib": 1024, "smt": False}
        elif method == "PUT" and path == "/actions":
            action = json.loads(body or b"{}").get("action_type")
            if action == "SendCtrlAltDel":
                writer.write(b"HTTP/1.1 204 No Content\r\nContent-Length: 0\r\n\r\n")
                await writer.drain()
                if "acpi" not in ignored:
                    _stop()
                return
        data = json.dumps(answer).encode()
        writer.write(
            f"HTTP/1.1 {status} X\r\nContent-Type: application/json\r\n"
            f"Content-Length: {len(data)}\r\n\r\n".encode()
            + data
        )
        await writer.drain()
    finally:
        writer.close()


async def _pipe(source: asyncio.StreamReader, sink: asyncio.StreamWriter) -> None:
    with contextlib.suppress(OSError):
        while chunk := await source.read(65536):
            sink.write(chunk)
            await sink.drain()
    with contextlib.suppress(OSError):
        if sink.can_write_eof():
            sink.write_eof()


async def _vsock(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, booted: float) -> None:
    target = os.environ.get("FAUX_EXECD", "")
    port = int(os.environ.get("FAUX_PORT", "5100"))
    try:
        line = (await reader.readline()).decode().split()
        if (
            len(line) != 2
            or line[0] != "CONNECT"
            or int(line[1]) != port
            or not target
            or asyncio.get_running_loop().time() < booted
        ):
            return
        try:
            inner_reader, inner_writer = await asyncio.open_unix_connection(target)
        except OSError:
            return
        writer.write(b"OK 1073741824\n")
        await writer.drain()
        await asyncio.gather(_pipe(reader, inner_writer), _pipe(inner_reader, writer))
        inner_writer.close()
    finally:
        writer.close()


async def _console() -> None:
    ignored = os.environ.get("FAUX_IGNORE", "").split(",")
    loop = asyncio.get_running_loop()
    while True:
        line = await loop.run_in_executor(None, sys.stdin.readline)
        if not line:
            # Lanceur parti : le vrai VMM continue, le faux aussi.
            await asyncio.Event().wait()
        if line.strip() == "reboot" and "console" not in ignored:
            _stop()


async def serve(api_sock: str, vsock_uds: str) -> None:
    loop = asyncio.get_running_loop()
    booted = loop.time() + float(os.environ.get("FAUX_BOOT", "0"))
    api = await asyncio.start_unix_server(_api, path=api_sock)
    vsock = await asyncio.start_unix_server(lambda r, w: _vsock(r, w, booted), path=vsock_uds)
    print("faux firecracker : prêt", flush=True)
    async with api, vsock:
        await _console()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-sock", required=True)
    parser.add_argument("--config-file", required=True)
    options = parser.parse_args()
    if crash := os.environ.get("FAUX_CRASH", ""):
        print(crash, flush=True)
        sys.exit(1)
    if "sigterm" in os.environ.get("FAUX_IGNORE", "").split(","):
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    config = json.loads(Path(options.config_file).read_text())
    asyncio.run(serve(options.api_sock, config["vsock"]["uds_path"]))


if __name__ == "__main__":
    main()
