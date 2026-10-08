# SPDX-License-Identifier: Apache-2.0
"""Une session execd : une connexion au service d'exécution de l'invité.

Le protocole est celui d'execd, ``firecracker/service/`` dans le dépôt (version 1) :

    trame = u32 BE longueur d'en-tête | en-tête JSON | u32 BE longueur du corps | corps

Une connexion est une session : execd lui donne un dossier de travail
(``code/ in/ out/ work/``) qu'il efface quand elle se ferme. Une requête, une
réponse, dans l'ordre ; chaque requête porte un ``seq`` que la réponse doit
rendre, ce qui détecte un flux désynchronisé.

Deux sortes d'échec, à ne pas confondre :

- ``ExecdError`` : execd a répondu non (chemin refusé, extension native,
  session de trop…). La session reste utilisable.
- ``ProtocolError`` ou ``TimeoutError`` : trame illisible, réponse
  désynchronisée, connexion coupée, délai dépassé, appel annulé. La session
  est fermée ; execd arrête le job en cours et efface son dossier. (Un execd
  d'avant le 08/10 ne voit la fermeture qu'à la fin du job, au plus tard à
  son ``wall_ms``.)

Un job qui échoue (exception, délai, mémoire) n'est ni l'un ni l'autre :
``exec`` rend une ``Execution`` dont ``ok`` est faux et ``error`` dit pourquoi,
avec les sorties du job — c'est ce que le modèle doit voir pour se corriger.
"""

import asyncio
import contextlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Final, Self, cast

__all__ = [
    "PROTOCOL",
    "ExecdError",
    "Execution",
    "Hello",
    "Output",
    "ProtocolError",
    "Session",
    "encode_frame",
    "read_frame",
]

# Version du protocole parlée par ce client ; hello la vérifie.
PROTOCOL: Final = 1
# Bornes de cadrage d'execd : au-delà, c'est un défaut de protocole.
MAX_HEADER: Final = 1 << 20
MAX_BODY: Final = 8 << 20
# Taille des morceaux envoyés et demandés.
CHUNK: Final = 1 << 20
# Délai d'une requête courte : hello, put, get, reset.
REQUEST_TIMEOUT: Final = 30.0
# wall_ms qu'execd applique quand on ne demande rien.
DEFAULT_WALL_MS: Final = 30_000
# Marge ajoutée au wall_ms d'un job : attente derrière les jobs des autres
# sessions (execd n'en exécute qu'un à la fois) et 5 s de mise à mort.
EXEC_MARGIN: Final = 30.0
# Ce que get_file accepte de rapatrier par défaut.
MAX_FILE: Final = 16 << 20


class ProtocolError(Exception):
    """Dialogue impossible avec execd ; la session est fermée."""


class ExecdError(Exception):
    """Refus d'execd, ou cause de l'échec d'un job : ``kind`` est stable, ``message`` lisible."""

    def __init__(self, kind: str, message: str, detail: Mapping[str, object] | None = None) -> None:
        super().__init__(f"{kind} : {message}")
        self.kind = kind
        self.message = message
        self.detail: Mapping[str, object] = detail or {}


@dataclass(frozen=True, slots=True)
class Hello:
    """Ce que l'invité annonce à l'ouverture de la session."""

    protocol: int
    session_id: str
    # Version de Python dans l'invité, et du service.
    python: str
    runner: str
    max_body: int
    max_code_bytes: int
    max_upload_total: int
    # Plafonds des limites d'un job : au-delà, execd ramène au plafond.
    ceilings: Mapping[str, int]


@dataclass(frozen=True, slots=True)
class Output:
    """Un fichier produit par le job dans ``out/``."""

    path: str
    size: int
    sha256: str


@dataclass(frozen=True, slots=True)
class Execution:
    """Le compte rendu d'un ``tool.exec``."""

    ok: bool
    # Valeur JSON rendue par le point d'entrée, si ok.
    result: object = None
    # Pourquoi le job a échoué, si pas ok.
    error: ExecdError | None = None
    stdout: str = ""
    stderr: str = ""
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    duration_ms: int | None = None
    # Limites réellement appliquées par l'invité (après plafonnement).
    limits_applied: Mapping[str, int] = field(default_factory=dict[str, int])
    outputs: tuple[Output, ...] = ()
    # Entrées de out/ ignorées : liens symboliques, fichiers spéciaux.
    outputs_skipped: tuple[str, ...] = ()


# ---------------------------------------------------------------------- #
# Trames
# ---------------------------------------------------------------------- #


def encode_frame(header: Mapping[str, object], body: bytes = b"") -> bytes:
    """Une trame complète ; ``ProtocolError`` si elle dépasse les bornes d'execd."""
    raw = json.dumps(header, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(raw) > MAX_HEADER:
        raise ProtocolError(f"en-tête sortant de {len(raw)} octets (max {MAX_HEADER})")
    if len(body) > MAX_BODY:
        raise ProtocolError(f"corps sortant de {len(body)} octets (max {MAX_BODY})")
    return len(raw).to_bytes(4, "big") + raw + len(body).to_bytes(4, "big") + body


async def read_frame(reader: asyncio.StreamReader) -> tuple[dict[str, object], bytes]:
    """La trame suivante : en-tête JSON (un objet) et corps brut."""
    try:
        header_len = int.from_bytes(await reader.readexactly(4), "big")
        if header_len == 0 or header_len > MAX_HEADER:
            raise ProtocolError(f"longueur d'en-tête hors bornes : {header_len}")
        raw = await reader.readexactly(header_len)
        body_len = int.from_bytes(await reader.readexactly(4), "big")
        if body_len > MAX_BODY:
            raise ProtocolError(f"longueur de corps hors bornes : {body_len}")
        body = await reader.readexactly(body_len)
    except asyncio.IncompleteReadError as exc:
        raise ProtocolError("connexion fermée par execd au milieu d'une trame") from exc
    try:
        header: object = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProtocolError(f"en-tête JSON invalide : {exc}") from exc
    if not isinstance(header, dict):
        raise ProtocolError(f"en-tête JSON : objet attendu, reçu {type(header).__name__}")
    return cast(dict[str, object], header), body


# ---------------------------------------------------------------------- #
# Lecture défensive des réponses
# ---------------------------------------------------------------------- #


def _mapping(value: object) -> dict[str, object]:
    return cast(dict[str, object], value) if isinstance(value, dict) else {}


def _str(value: object) -> str:
    return value if isinstance(value, str) else ""


def _int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _list(value: object) -> list[object]:
    return cast(list[object], value) if isinstance(value, list) else []


def _ints(value: object) -> dict[str, int]:
    return {key: found for key, raw in _mapping(value).items() if (found := _int(raw)) is not None}


def _error(reply: Mapping[str, object]) -> ExecdError:
    error = _mapping(reply.get("error"))
    return ExecdError(
        _str(error.get("kind")) or "unknown",
        _str(error.get("message")) or "erreur sans message",
        _mapping(error.get("detail")),
    )


def _outputs(value: object) -> tuple[Output, ...]:
    found: list[Output] = []
    for raw in _list(value):
        entry = _mapping(raw)
        size = _int(entry.get("size"))
        if size is None or not _str(entry.get("path")):
            raise ProtocolError(f"manifeste de out/ illisible : {raw!r}")
        found.append(Output(_str(entry.get("path")), size, _str(entry.get("sha256"))))
    return tuple(found)


def _execution(reply: Mapping[str, object]) -> Execution:
    ok = reply.get("ok") is True
    return Execution(
        ok=ok,
        result=reply.get("result") if ok else None,
        error=None if ok else _error(reply),
        stdout=_str(reply.get("stdout")),
        stderr=_str(reply.get("stderr")),
        stdout_truncated=reply.get("stdout_truncated") is True,
        stderr_truncated=reply.get("stderr_truncated") is True,
        duration_ms=_int(reply.get("duration_ms")),
        limits_applied=_ints(reply.get("limits_applied")),
        outputs=_outputs(reply.get("outputs")),
        outputs_skipped=tuple(_str(path) for path in _list(reply.get("outputs_skipped"))),
    )


# ---------------------------------------------------------------------- #
# Session
# ---------------------------------------------------------------------- #


class Session:
    """Une connexion à execd, ouverte par ``Session.open`` sur un flux déjà établi.

    Le flux vient de ``Vm.connect(port)`` (vsock) ou d'une socket Unix (execd
    lancé avec ``EXECD_UDS``, sans VM). Les requêtes passent une à une, même
    si plusieurs tâches appellent la même session.
    """

    def __init__(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, hello: Hello
    ) -> None:
        self._reader = reader
        self._writer = writer
        self.hello = hello
        self._seq = 0
        self._lock = asyncio.Lock()
        self._closed = False

    @classmethod
    async def open(
        cls,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        *,
        wait: float = REQUEST_TIMEOUT,
    ) -> Self:
        """Salue execd et vérifie la version du protocole ; ferme le flux en cas d'échec.

        ``ExecdError`` de genre ``busy`` si l'invité a déjà toutes les
        sessions qu'il accepte.
        """
        session = cls(reader, writer, _NO_HELLO)
        try:
            reply, _ = await session._call("hello", {"protocol": PROTOCOL}, wait=wait)
            if reply.get("ok") is not True:
                raise _error(reply)
            protocol = _int(reply.get("protocol"))
            if protocol != PROTOCOL:
                raise ProtocolError(f"execd parle le protocole {protocol}, ce client {PROTOCOL}")
            caps = _mapping(reply.get("caps"))
            session.hello = Hello(
                protocol=protocol,
                session_id=_str(reply.get("session_id")),
                python=_str(reply.get("python")),
                runner=_str(reply.get("runner")),
                max_body=_int(caps.get("max_body")) or MAX_BODY,
                max_code_bytes=_int(caps.get("max_code_bytes")) or 0,
                max_upload_total=_int(caps.get("max_upload_total")) or 0,
                ceilings=_ints(reply.get("limits_ceiling")),
            )
        except BaseException:
            await session.close()
            raise
        return session

    @property
    def closed(self) -> bool:
        return self._closed

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def close(self) -> None:
        """Ferme la connexion : execd arrête le job en cours et efface le dossier de la session.

        Le job est tué aussitôt, et sa place d'exécution rendue aux autres
        sessions. Un execd d'avant le 08/10 ne voyait la fermeture qu'à la fin
        du job, au plus tard à son ``wall_ms``.
        """
        if self._closed:
            return
        self._abort()
        with contextlib.suppress(Exception):
            await self._writer.wait_closed()

    def _abort(self) -> None:
        self._closed = True
        self._writer.close()

    # -- méthodes du protocole --------------------------------------------- #

    async def put_code(self, path: str, data: bytes) -> None:
        """Dépose un fichier dans ``code/`` (``.py``, ``.json``, ``.txt``… ; jamais de natif)."""
        await self._put("code.put", path, data)

    async def put_file(self, path: str, data: bytes) -> None:
        """Dépose un fichier d'entrée dans ``in/``."""
        await self._put("file.put", path, data)

    async def exec(
        self,
        entrypoint: str,
        *,
        args: Mapping[str, object] | None = None,
        limits: Mapping[str, int] | None = None,
        env: Mapping[str, str] | None = None,
        wait: float | None = None,
    ) -> Execution:
        """Exécute ``module:fonction`` de ``code/`` avec ces arguments nommés.

        ``limits`` propose (``wall_ms``, ``cpu_ms``, ``mem_bytes``…), l'invité
        plafonne ; ``limits_applied`` dit ce qui a été appliqué. L'attente de la
        réponse (``wait``) vaut par défaut ``wall_ms`` plus ``EXEC_MARGIN`` ;
        dépassée, la session est fermée et ``TimeoutError`` levée.
        """
        header: dict[str, object] = {"entrypoint": entrypoint, "args": dict(args or {})}
        if limits is not None:
            header["limits"] = dict(limits)
        if env is not None:
            header["env"] = dict(env)
        if wait is None:
            wall_ms = (limits or {}).get("wall_ms", DEFAULT_WALL_MS)
            wait = wall_ms / 1000 + EXEC_MARGIN
        reply, _ = await self._call("tool.exec", header, wait=wait)
        return _execution(reply)

    async def get_file(self, path: str, *, max_bytes: int = MAX_FILE) -> bytes:
        """Rapatrie un fichier de ``out/`` produit par le dernier job.

        ``ValueError`` s'il dépasse ``max_bytes`` : le manifeste de
        l'``Execution`` donne la taille avant de décider.
        """
        data = bytearray()
        while True:
            want = min(CHUNK, max_bytes - len(data) + 1)
            reply, body = await self._call(
                "file.get", {"path": path, "offset": len(data), "len": want}
            )
            if reply.get("ok") is not True:
                raise _error(reply)
            data += body
            if len(data) > max_bytes:
                raise ValueError(f"{path} dépasse {max_bytes} octets")
            if reply.get("eof") is True or not body:
                return bytes(data)

    async def reset(self) -> None:
        """Vide ``code/ in/ out/ work/`` et tue un job en cours, sans fermer la session."""
        reply, _ = await self._call("reset", {})
        if reply.get("ok") is not True:
            raise _error(reply)

    # -- transport --------------------------------------------------------- #

    async def _put(self, method: str, path: str, data: bytes) -> None:
        pieces = [data[at : at + CHUNK] for at in range(0, len(data), CHUNK)] or [b""]
        for index, piece in enumerate(pieces):
            header = {"path": path, "eof": index == len(pieces) - 1}
            reply, _ = await self._call(method, header, piece)
            if reply.get("ok") is not True:
                raise _error(reply)

    async def _call(
        self,
        method: str,
        header: Mapping[str, object],
        body: bytes = b"",
        *,
        wait: float = REQUEST_TIMEOUT,
    ) -> tuple[dict[str, object], bytes]:
        async with self._lock:
            if self._closed:
                raise ProtocolError(f"{method} : session fermée")
            self._seq += 1
            seq = self._seq
            frame = encode_frame({"method": method, "seq": seq, **header}, body)
            try:
                async with asyncio.timeout(wait):
                    self._writer.write(frame)
                    await self._writer.drain()
                    reply, data = await read_frame(self._reader)
            except TimeoutError:
                await self.close()
                raise TimeoutError(
                    f"execd : pas de réponse à {method} en {wait:g} s — session fermée"
                ) from None
            except ProtocolError:
                await self.close()
                raise
            except OSError as exc:
                await self.close()
                raise ProtocolError(f"{method} : connexion à execd perdue — {exc}") from exc
            except BaseException:
                # Annulée en route (délai de l'appelant, run arrêté) : la
                # réponse arriverait plus tard et désynchroniserait la
                # suivante. Fermée tout de suite, sans attendre : execd voit
                # la connexion se fermer et efface la session.
                self._abort()
                raise
            if reply.get("seq") != seq:
                await self.close()
                raise ProtocolError(
                    f"{method} : réponse désynchronisée (seq {reply.get('seq')!r}, attendu {seq})"
                )
            return reply, data


# Valeur provisoire, le temps du hello.
_NO_HELLO: Final = Hello(
    protocol=0,
    session_id="",
    python="",
    runner="",
    max_body=MAX_BODY,
    max_code_bytes=0,
    max_upload_total=0,
    ceilings={},
)
