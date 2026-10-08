"""Client minimal pour exercer le protocole execd. Outil de DEVELOPPEMENT.

Ce n'est pas le client de production : celui-la vivra cote hote, dans le
projet de l'agent, et portera la logique de reessai, l'ArtifactStore et la
traduction des erreurs vers le modele. Ici on ne veut qu'un moyen de parler au
service pour le tester.

    python3 devclient.py --uds /tmp/execd.sock ...
    python3 devclient.py --cid 3 --port 5100 ...
"""

from __future__ import annotations

import json
import socket
from pathlib import Path
from typing import Any


class Client:
    def __init__(self, sock: socket.socket) -> None:
        self.sock = sock

    @classmethod
    def connect_uds(cls, path: str) -> Client:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect(path)
        return cls(sock)

    @classmethod
    def connect_vsock(cls, cid: int, port: int) -> Client:
        """Handshake Firecracker cote hote : CONNECT <port>\\n -> OK <port>\\n."""
        sock = socket.socket(socket.AF_VSOCK, socket.SOCK_STREAM)
        sock.connect((cid, port))
        return cls(sock)

    @classmethod
    def connect_firecracker(cls, uds: str, port: int) -> Client:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect(uds)
        sock.sendall(f"CONNECT {port}\n".encode())
        line = b""
        while not line.endswith(b"\n"):
            chunk = sock.recv(1)
            if not chunk:
                raise ConnectionError("handshake interrompu")
            line += chunk
        if not line.startswith(b"OK"):
            raise ConnectionError(f"handshake refuse : {line!r}")
        return cls(sock)

    def _recv(self, n: int) -> bytes:
        buf = bytearray()
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("connexion fermee")
            buf += chunk
        return bytes(buf)

    def call(self, method: str, body: bytes = b"", **header: Any) -> tuple[dict, bytes]:
        head = {"method": method, **header}
        raw = json.dumps(head).encode()
        self.sock.sendall(len(raw).to_bytes(4, "big") + raw + len(body).to_bytes(4, "big") + body)
        hlen = int.from_bytes(self._recv(4), "big")
        reply = json.loads(self._recv(hlen))
        blen = int.from_bytes(self._recv(4), "big")
        return reply, self._recv(blen)

    # -- confort ----------------------------------------------------------- #
    def put_code_tree(self, root: str | Path, chunk: int = 1 << 20) -> None:
        root = Path(root)
        for path in sorted(root.rglob("*")):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            self.put_stream("code.put", str(path.relative_to(root)), path.read_bytes(), chunk)

    def put_file(self, rel: str, data: bytes, chunk: int = 1 << 20) -> None:
        self.put_stream("file.put", rel, data, chunk)

    def put_stream(self, method: str, rel: str, data: bytes, chunk: int) -> None:
        if not data:
            reply, _ = self.call(method, b"", path=rel, eof=True)
            if not reply.get("ok"):
                raise RuntimeError(reply)
            return
        for offset in range(0, len(data), chunk):
            piece = data[offset : offset + chunk]
            last = offset + len(piece) >= len(data)
            reply, _ = self.call(method, piece, path=rel, eof=last)
            if not reply.get("ok"):
                raise RuntimeError(reply)

    def get_file(self, rel: str) -> bytes:
        out = bytearray()
        while True:
            reply, body = self.call("file.get", path=rel, offset=len(out), len=1 << 20)
            if not reply.get("ok"):
                raise RuntimeError(reply)
            out += body
            if reply.get("eof") or not body:
                return bytes(out)

    def close(self) -> None:
        self.sock.close()
