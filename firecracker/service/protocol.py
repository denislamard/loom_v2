"""Cadrage des frames et erreurs typees du protocole execd.

FORMAT D'UNE FRAME (identique dans les deux sens)

    u32 BE header_len | header (JSON, UTF-8) | u32 BE body_len | body (octets)

Les 4 octets de `body_len` sont TOUJOURS presents, meme quand le corps est
vide : un lecteur n'a jamais a deviner si le champ existe, et le meme code
lit un message de controle et un chunk de fichier.

POURQUOI PAS DE BASE64

Encoder les octets dans le JSON couterait 33 % de volume et surtout une copie
integrale des deux cotes. Ici le corps binaire ne traverse jamais le parseur
JSON : il est lu par sa longueur et ecrit tel quel.
"""

from __future__ import annotations

import asyncio
import json
import socket
from typing import Any

# Bornes de cadrage. Un depassement est un defaut de PROTOCOLE, pas une erreur
# applicative : la connexion est fermee sans negociation. Laisser passer une
# longueur arbitraire reviendrait a offrir une allocation memoire pilotee par
# le pair.
MAX_HEADER = 1 << 20  # 1 MiB
MAX_BODY = 8 << 20  # 8 MiB


class ProtocolError(Exception):
    """Frame illisible. Fatal pour la connexion."""


class ConnectionClosed(Exception):
    """Le pair a ferme. Sortie NORMALE de la boucle de session."""


class ExecdError(Exception):
    """Erreur applicative, renvoyee au client sans fermer la connexion.

    `kind` est un identifiant stable et enumerable (cf. chapitre 11 de la
    spec). C'est lui que le code hote lira pour decider quoi faire ; le
    message est destine a l'humain et au LLM.
    """

    def __init__(self, kind: str, message: str, **detail: Any) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.detail = detail

    def as_payload(self) -> dict[str, Any]:
        err: dict[str, Any] = {"kind": self.kind, "message": self.message}
        if self.detail:
            err["detail"] = self.detail
        return {"ok": False, "error": err}


class Conn:
    """Une connexion cadree, au-dessus d'une socket brute non bloquante.

    On travaille sur la socket plutot que sur un StreamReader asyncio : les
    transports asyncio font des hypotheses (getsockname, getpeername, options
    de socket) qui ne sont pas toutes vraies sur AF_VSOCK selon les versions.
    `loop.sock_recv` / `loop.sock_sendall` fonctionnent sur n'importe quel
    descripteur et ne coutent rien de plus ici.
    """

    def __init__(self, sock: socket.socket) -> None:
        self._sock = sock
        self._sock.setblocking(False)
        self._loop = asyncio.get_running_loop()

    @property
    def sock(self) -> socket.socket:
        return self._sock

    async def _recv_exactly(self, n: int) -> bytes:
        """Lit exactement n octets. TCP/vsock ne garantit pas la taille d'un
        segment : un `recv(n)` unique renverrait couramment moins."""
        if n == 0:
            return b""
        buf = bytearray()
        while len(buf) < n:
            chunk = await self._loop.sock_recv(self._sock, min(65536, n - len(buf)))
            if not chunk:
                raise ConnectionClosed()
            buf += chunk
        return bytes(buf)

    async def read_frame(self) -> tuple[dict[str, Any], bytes]:
        raw = await self._recv_exactly(4)
        header_len = int.from_bytes(raw, "big")
        if header_len == 0 or header_len > MAX_HEADER:
            raise ProtocolError(f"header_len hors bornes : {header_len}")

        header_bytes = await self._recv_exactly(header_len)

        raw = await self._recv_exactly(4)
        body_len = int.from_bytes(raw, "big")
        if body_len > MAX_BODY:
            raise ProtocolError(f"body_len hors bornes : {body_len}")

        body = await self._recv_exactly(body_len)

        try:
            header = json.loads(header_bytes)
        except json.JSONDecodeError as exc:
            raise ProtocolError(f"header JSON invalide : {exc}") from exc
        if not isinstance(header, dict):
            raise ProtocolError("header JSON : objet attendu")

        return header, body

    async def write_frame(self, header: dict[str, Any], body: bytes = b"") -> None:
        # separators compact : le header voyage souvent a vide ou presque,
        # autant ne pas payer les espaces.
        header_bytes = json.dumps(header, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(header_bytes) > MAX_HEADER:
            raise ProtocolError("header sortant trop grand")
        if len(body) > MAX_BODY:
            raise ProtocolError("body sortant trop grand")

        # Un seul sendall : evite d'entrelacer deux reponses si un jour la
        # boucle devenait concurrente sur une meme connexion.
        frame = (
            len(header_bytes).to_bytes(4, "big")
            + header_bytes
            + len(body).to_bytes(4, "big")
            + body
        )
        await self._loop.sock_sendall(self._sock, frame)

    def close(self) -> None:
        try:
            self._sock.close()
        except OSError:
            pass
