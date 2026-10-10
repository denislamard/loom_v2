"""Une session execd = une connexion.

Il n'existe pas de `session.open` ni de `session.close`. Le workdir nait a
l'accept et meurt a la fermeture du socket. Ce choix fait porter le nettoyage
par le NOYAU : si l'orchestrateur hote disparait, le socket se ferme, et la
purge a lieu de toute facon. Aucune session zombie, aucun TTL a surveiller,
aucun ramasse-miettes a ecrire.

Corollaire : le protocole est strictement sequentiel sur une connexion — une
requete, une reponse. Pas de multiplexage, donc pas d'identifiants de
correlation obligatoires. Un tool qui doit parler a l'hote pendant son
execution ouvre une connexion guest->host separee ; ce canal ne passe pas par
ici.

Pendant un job, la connexion reste lue : un client qui s'en va (socket
fermee) arrete son job tout de suite, au lieu de le laisser courir jusqu'a
wall_ms en gardant sa place d'execution — les jobs des autres sessions
attendraient derriere lui. Une requete arrivee en avance (le protocole ne le
permet pas) attend la fin du job, comme avant.
"""

from __future__ import annotations

import asyncio
import contextlib
import errno
import hashlib
import json
import os
import shutil
import signal
import stat
import sys
import uuid
from pathlib import Path
from typing import Any, BinaryIO

import limits as limits_mod
from protocol import MAX_HEADER, Conn, ConnectionClosed, ExecdError, ProtocolError

PROTOCOL_VERSION = 1
# runner vit a cote de ce module — c'est vrai en production (/opt/execd)
# comme en developpement. Un chemin absolu code en dur ne serait juste que
# dans la VM, et rendrait le service intestable sur l'hote.
RUNNER = os.environ.get("EXECD_RUNNER") or str(Path(__file__).resolve().parent / "runner.py")

# Extensions acceptees par code.put. La liste est FERMEE : ce qui n'y figure
# pas est refuse, plutot que l'inverse.
CODE_EXTENSIONS = {".py", ".pyi", ".json", ".txt", ".toml", ".md", ".cfg", ".ini"}

# Extensions natives, refusees explicitement pour produire un diagnostic
# utile. /data est monte noexec : un .so depose ici echouerait au mmap avec un
# message qui n'oriente vers rien. On refuse a l'ECRITURE, au moment ou l'on
# peut encore expliquer pourquoi.
NATIVE_EXTENSIONS = {".so", ".pyd", ".dylib", ".a", ".o", ".dll"}

MAX_CODE_BYTES = 16 * 1024 * 1024
MAX_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024
MAX_PATH_LEN = 1024
MAX_COMPONENT_LEN = 255

# Fin d'un job (secondes). Une fois le groupe tue, les tuyaux stdout/stderr se
# ferment tout de suite : PIPE_GRACE ne sert qu'a un descendant sorti du groupe
# (setsid) qui les tient encore. EXIT_POLL : voir _exited.
PIPE_GRACE = 2.0
EXIT_POLL = 0.02


# --------------------------------------------------------------------------- #
# Validation des chemins
# --------------------------------------------------------------------------- #
def safe_join(root: Path, rel: str) -> Path:
    """Resout `rel` sous `root`, ou echoue.

    La verification porte sur le chemin RESOLU compare au prefixe, pas sur la
    chaine d'entree : un controle purement textuel se contourne par un lien
    symbolique intermediaire deja present dans l'arborescence.
    """
    if not isinstance(rel, str) or not rel:
        raise ExecdError("bad_path", "chemin manquant", path=rel, reason="vide")
    if len(rel) > MAX_PATH_LEN:
        raise ExecdError("bad_path", "chemin trop long", path=rel[:80], reason="longueur")
    if "\x00" in rel or any(ord(c) < 32 for c in rel):
        raise ExecdError("bad_path", "caractere de controle dans le chemin", reason="caracteres")
    if rel.startswith("/") or (len(rel) > 1 and rel[1] == ":"):
        raise ExecdError("bad_path", "chemin absolu refuse", path=rel, reason="absolu")

    parts = [p for p in rel.replace("\\", "/").split("/") if p not in ("", ".")]
    if not parts:
        raise ExecdError("bad_path", "chemin vide apres normalisation", path=rel, reason="vide")
    for part in parts:
        if part == "..":
            raise ExecdError("bad_path", "remontee de repertoire refusee", path=rel, reason="..")
        if len(part) > MAX_COMPONENT_LEN:
            raise ExecdError("bad_path", "composant trop long", path=rel, reason="composant")

    # Le dossier de base lui-meme ne doit pas etre un lien : realpath le suivrait, et le
    # prefixe de comparaison serait la cible du lien, pas le dossier de la session —
    # tout chemin sous cette cible passerait pour « dedans ».
    if os.path.islink(root):
        raise ExecdError(
            "bad_path", "le dossier de base est un lien symbolique", path=rel, reason="symlink"
        )

    target = root.joinpath(*parts)

    # strict=False : la cible n'existe pas encore lors d'un put. Ce sont les
    # composants EXISTANTS qui sont resolus, donc les liens deja poses.
    resolved = Path(os.path.realpath(target))
    root_resolved = Path(os.path.realpath(root))
    if resolved != root_resolved and root_resolved not in resolved.parents:
        raise ExecdError("bad_path", "chemin hors du repertoire cible", path=rel, reason="evasion")

    return target


# --------------------------------------------------------------------------- #
# Capture bornee
# --------------------------------------------------------------------------- #
async def _read_block(stream: asyncio.StreamReader, abandon: asyncio.Event | None) -> bytes | None:
    """Le bloc suivant du flux ; None si `abandon` est pose avant qu'il n'arrive."""
    if abandon is None:
        return await stream.read(65536)
    if abandon.is_set():
        return None
    reading = asyncio.ensure_future(stream.read(65536))
    leaving = asyncio.ensure_future(abandon.wait())
    try:
        await asyncio.wait({reading, leaving}, return_when=asyncio.FIRST_COMPLETED)
        return reading.result() if reading.done() else None
    finally:
        reading.cancel()
        leaving.cancel()


async def drain_bounded(
    stream: asyncio.StreamReader, cap: int, abandon: asyncio.Event | None = None
) -> tuple[str, bool]:
    """Lit un flux jusqu'a EOF en ne conservant que `cap` octets.

    Il faut CONTINUER a lire meme au-dela du plafond : arreter remplirait le
    tube et bloquerait le tool sur son propre write, transformant un job
    bavard en job qui ne se termine jamais.

    On garde la TETE et la QUEUE. Sur une sortie tronquee, l'information de
    diagnostic est aux deux extremites : le debut dit ce qui a demarre, la fin
    dit ce qui a casse. Le milieu est du bruit.

    `abandon`, une fois pose, arrete la lecture la ou elle en est et le flux est
    dit tronque : un descendant sorti du groupe du job peut tenir le tuyau
    ouvert bien apres lui, et l'EOF n'arriverait jamais.
    """
    head = bytearray()
    tail = bytearray()
    total = 0
    half = cap // 2
    cut = False

    while True:
        chunk = await _read_block(stream, abandon)
        if chunk is None:
            cut = True
            break
        if not chunk:
            break
        total += len(chunk)
        if len(head) < half:
            take = half - len(head)
            head += chunk[:take]
            chunk = chunk[take:]
        if chunk:
            tail += chunk
            if len(tail) > half:
                del tail[: len(tail) - half]

    if total <= cap:
        data = bytes(head) + bytes(tail)
        return data.decode("utf-8", "replace"), cut

    omitted = total - len(head) - len(tail)
    marker = f"\n... [{omitted} octets omis] ...\n".encode()
    return (bytes(head) + marker + bytes(tail)).decode("utf-8", "replace"), True


# --------------------------------------------------------------------------- #
# Operations bloquantes, appelees via asyncio.to_thread
# --------------------------------------------------------------------------- #
def _open_regular(path: Path) -> BinaryIO:
    """Ouvre en lecture un fichier ORDINAIRE, sans attendre ni suivre de lien.

    out/ et run/ sont ecrits par le job, qui peut y poser un tube nomme (FIFO) a la
    place d'un fichier : son ouverture en lecture attend un ecrivain qui ne viendra
    jamais, le thread qui l'ouvre ne revient pas, et avec lui la requete — et, pour
    result.json, la place d'execution de toute la VM. O_NONBLOCK rend l'ouverture
    immediate ; fstat, sur le descripteur deja ouvert, dit ce qu'on a ouvert (il n'y
    a pas d'intervalle entre le controle et la lecture) ; O_NOFOLLOW refuse un lien
    pose a la place du fichier.
    """
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError(errno.EINVAL, "pas un fichier ordinaire")
        return os.fdopen(fd, "rb")
    except BaseException:
        os.close(fd)
        raise


def _read_at(path: Path, offset: int, want: int) -> tuple[int, bytes]:
    with _open_regular(path) as handle:
        size = os.fstat(handle.fileno()).st_size
        handle.seek(offset)
        return size, handle.read(want)


# --------------------------------------------------------------------------- #
# Une reponse doit tenir dans un en-tete de trame
# --------------------------------------------------------------------------- #
def _header_size(reply: dict) -> int:
    return len(json.dumps(reply, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _shortened(text: str, budget: int) -> tuple[str, bool]:
    """`text` ramene a `budget` caracteres, tete et queue gardees, et s'il l'a ete."""
    if len(text) <= budget:
        return text, False
    half = budget // 2
    omitted = len(text) - 2 * half
    return f"{text[:half]}\n... [{omitted} caracteres omis] ...\n{text[len(text) - half :]}", True


def _fitted(reply: dict) -> dict:
    """La reponse ramenee a la taille d'un en-tete de trame, ou remplacee par une erreur.

    stdout et stderr sont limites en OCTETS (CAPTURE_LIMIT), mais voyagent en JSON :
    un octet de controle y coute six octets (\\u00XX), un guillemet deux. Deux flux
    pleins peuvent donc depasser seuls MAX_HEADER. Ils sont alors raccourcis, par
    moities successives et en le disant (`stdout_truncated`, `stderr_truncated`),
    jusqu'a ce que la reponse tienne. Si le reste — resultat, manifeste de out/ — ne
    tient pas non plus, ou si un texte n'est pas de l'UTF-8 (result.json peut porter
    un « \\ud800 » : du JSON valide, mais pas du texte), le client recoit une erreur
    qui le dit, et non une connexion coupee sans un mot.
    """
    fitted = dict(reply)
    try:
        budget = limits_mod.CAPTURE_LIMIT
        size = _header_size(fitted)
        while size > MAX_HEADER and budget > 1024:
            budget //= 2
            for stream in ("stdout", "stderr"):
                text = reply.get(stream)
                if isinstance(text, str):
                    fitted[stream], cut = _shortened(text, budget)
                    if cut:
                        fitted[f"{stream}_truncated"] = True
            size = _header_size(fitted)
    except UnicodeEncodeError:
        error = ExecdError("bad_result", "la reponse contient un texte qui n'est pas de l'UTF-8")
    else:
        if size <= MAX_HEADER:
            return fitted
        error = ExecdError(
            "limit_exceeded",
            f"reponse trop grosse pour une trame : {size} octets (plafond {MAX_HEADER})",
            limit="response_bytes",
            value=size,
            ceiling=MAX_HEADER,
        )
    refusal = error.as_payload()
    if "seq" in reply:
        refusal["seq"] = reply["seq"]
    return refusal


def _write_job(job_path: Path, job: dict, result_path: Path) -> None:
    # job.json est RECREE, jamais reecrit en place : supprimer un lien pose a sa place
    # ne touche pas sa cible, et O_EXCL|O_NOFOLLOW refuse de passer par un lien qui
    # reapparaitrait entre le unlink et l'open. Ecrire « a travers » donnerait a un
    # lien pose par le job le contenu, puis le mode, d'un fichier de root.
    job_path.unlink(missing_ok=True)
    fd = os.open(job_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(job))
        # 0644 : runner tourne sous le compte sandbox et doit LIRE ce fichier,
        # jamais l'ecrire — il ne doit pas pouvoir relever ses propres limites.
        # Pose sur le descripteur, donc independante de l'umask et du chemin.
        os.fchmod(handle.fileno(), 0o644)
    result_path.unlink(missing_ok=True)


async def _exited(proc: asyncio.subprocess.Process) -> None:
    """Rend la main quand le runner est sorti, tuyaux ouverts ou non.

    `proc.wait()` ne convient pas : sous Python 3.12 (celui de la VM), il n'est
    reveille qu'une fois les tuyaux fermes, donc jamais tant qu'un descendant du
    job les tient. `returncode`, lui, est pose des la sortie du process, mais
    asyncio n'offre aucun evenement pour l'attendre : d'ou le sondage.
    """
    while True:
        if proc.returncode is not None:
            return
        await asyncio.sleep(EXIT_POLL)


# --------------------------------------------------------------------------- #
# Session
# --------------------------------------------------------------------------- #
class _ConnectionEnded(Exception):
    """La lecture de la connexion a pris fin pendant un job : le job a ete arrete."""


class Session:
    def __init__(self, conn: Conn, cfg: Any, exec_sem: asyncio.Semaphore) -> None:
        self.conn = conn
        self.cfg = cfg
        self.exec_sem = exec_sem
        self.id = uuid.uuid4().hex
        self.root = Path(cfg.jobs_dir) / self.id
        self.state = "READY"
        self._open_files: dict[str, BinaryIO] = {}
        self._uploaded = 0
        self._code_bytes = 0
        self._proc: asyncio.subprocess.Process | None = None

    # -- cycle de vie ------------------------------------------------------ #
    def setup(self) -> None:
        # La racine de session RESTE a root (0711) : le compte sandbox la traverse, mais
        # n'y peut ni creer, ni renommer, ni supprimer. Il ne peut donc ni remplacer
        # code/, in/, out/, work/ par un lien, ni poser un piege a cote de job.json — ce
        # que le chown de _own ou une ecriture de root suivraient. Seuls ces dossiers
        # lui appartiennent ; run/ porte result.json, que runner doit pouvoir ecrire.
        self.root.mkdir(parents=True, exist_ok=True)
        os.chmod(self.root, 0o711)
        for sub in ("code", "in", "out", "work", "run"):
            (self.root / sub).mkdir(mode=0o700, exist_ok=True)
            self._own(self.root / sub)

    @property
    def _result_path(self) -> Path:
        return self.root / "run" / "result.json"

    def _own(self, path: Path) -> None:
        """Donne l'arborescence au compte non privilegie qui executera le code.

        Sans droit root (execution de developpement sur l'hote), on ne fait
        rien : le processus tourne alors sous l'utilisateur courant, qui est
        deja proprietaire.

        Aucun lien symbolique n'est suivi : le job en pose dans ce qu'il possede,
        et ce parcours tourne en root. fwalk descend par descripteur de dossier (un
        dossier remplace par un lien en cours de route n'est pas parcouru, pas plus
        qu'un `path` qui serait lui-meme un lien) et chown agit sur l'entree, jamais
        sur sa cible.
        """
        if self.cfg.uid is None or os.geteuid() != 0:
            return
        for _, dirs, files, dirfd in os.fwalk(path, follow_symlinks=False):
            os.fchown(dirfd, self.cfg.uid, self.cfg.gid)
            for name in dirs + files:
                with contextlib.suppress(OSError):
                    os.chown(name, self.cfg.uid, self.cfg.gid, dir_fd=dirfd, follow_symlinks=False)

    async def cleanup(self) -> None:
        """Tue le job en cours puis efface le workdir. Toujours appele."""
        self._close_handles()
        if self._proc is not None and self._proc.returncode is None:
            self._kill_group()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._proc.wait(), timeout=5)
        shutil.rmtree(self.root, ignore_errors=True)

    def _close_handles(self) -> None:
        for handle in self._open_files.values():
            with contextlib.suppress(OSError):
                handle.close()
        self._open_files.clear()

    def _kill_group(self) -> None:
        """SIGKILL au GROUPE, pas au PID.

        runner est lance avec start_new_session, donc son pid est le pgid. Un
        tool qui a forke laisserait sinon un orphelin vivant, qui pourrirait
        une VM reutilisee — et consommerait du CPU sans que rien ne l'explique.

        Le groupe survit au runner : ses descendants restent, et gardent les
        tuyaux et work/. Le numero de groupe ne peut etre reattribue tant que
        l'un d'eux vit ; une fois le groupe vide, killpg echoue (ESRCH).
        """
        if self._proc is None:
            return
        with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
            os.killpg(self._proc.pid, signal.SIGKILL)

    # -- boucle ------------------------------------------------------------ #
    async def serve(self) -> None:
        # La lecture de la trame suivante, quand elle a ete lancee pendant un
        # job : la boucle la reprend telle quelle, sans en lancer une autre.
        reading: asyncio.Future | None = None
        try:
            while True:
                if reading is None:
                    reading = asyncio.ensure_future(self.conn.read_frame())
                try:
                    header, body = await reading
                except ConnectionClosed:
                    return
                reading = None

                seq = header.get("seq")
                method = header.get("method")
                if method == "tool.exec":
                    reading = asyncio.ensure_future(self.conn.read_frame())

                try:
                    reply, out_body = await self._watched(
                        self.dispatch(method, header, body), reading
                    )
                except _ConnectionEnded:
                    # Le job est arrete ; la cause de la fin de lecture decide
                    # de la suite : une fermeture est une sortie normale, une
                    # trame illisible ou une erreur de socket remonte.
                    assert reading is not None
                    with contextlib.suppress(ConnectionClosed):
                        reading.result()
                    return
                except ExecdError as exc:
                    reply, out_body = exc.as_payload(), b""
                except Exception as exc:  # defaut du service : on le dit, on continue
                    reply = ExecdError("runner_failure", f"erreur interne : {exc}").as_payload()
                    out_body = b""

                if seq is not None:
                    reply["seq"] = seq
                await self._send(reply, out_body)
        finally:
            if reading is not None and not reading.done():
                reading.cancel()

    async def _send(self, reply: dict, body: bytes) -> None:
        """Ecrit la reponse ; si elle ne tient pas dans une trame, en ecrit une qui tient.

        Une trame refusee n'est jamais envoyee a moitie. Laisser l'exception remonter de
        `serve` fermait la connexion : le client attendait une reponse qui n'etait pas
        perdue, mais jamais ecrite.
        """
        try:
            await self.conn.write_frame(reply, body)
        except (ProtocolError, UnicodeEncodeError):
            await self.conn.write_frame(_fitted(reply), body)

    @staticmethod
    async def _watched(work: Any, reading: asyncio.Future | None) -> tuple[dict, bytes]:
        """Execute une requete ; pendant un job, s'arrete si la connexion prend fin.

        `reading` est la lecture de la trame suivante, lancee avec le job. Si
        elle echoue avant lui — le client a ferme, ou la socket est cassee —,
        personne ne lira plus la reponse : le job est annule (`_run` tue alors
        son groupe) et `_ConnectionEnded` le dit. Si elle rend une trame,
        celle-ci attend la fin du job ; la connexion n'est plus surveillee
        jusque-la.
        """
        if reading is None:
            return await work
        job = asyncio.ensure_future(work)
        try:
            await asyncio.wait({job, reading}, return_when=asyncio.FIRST_COMPLETED)
            if not job.done() and reading.done() and reading.exception() is not None:
                job.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await job
                raise _ConnectionEnded()
            return await job
        finally:
            if not job.done():
                job.cancel()

    async def dispatch(self, method: Any, header: dict, body: bytes) -> tuple[dict, bytes]:
        handlers = {
            "hello": self.m_hello,
            "file.put": self.m_file_put,
            "code.put": self.m_code_put,
            "tool.exec": self.m_tool_exec,
            "file.get": self.m_file_get,
            "reset": self.m_reset,
        }
        handler = handlers.get(method) if isinstance(method, str) else None
        if handler is None:
            raise ExecdError(
                "protocol", f"methode inconnue : {method!r}", expected=sorted(handlers), got=method
            )
        if method == "file.get" and self.state != "DONE":
            raise ExecdError(
                "protocol",
                "file.get n'est valide qu'apres tool.exec",
                expected="DONE",
                got=self.state,
            )
        return await handler(header, body)

    # -- methodes ---------------------------------------------------------- #
    async def m_hello(self, header: dict, body: bytes) -> tuple[dict, bytes]:
        asked = header.get("protocol", PROTOCOL_VERSION)
        if asked != PROTOCOL_VERSION:
            raise ExecdError(
                "protocol",
                "version de protocole non supportee",
                expected=PROTOCOL_VERSION,
                got=asked,
            )
        return {
            "ok": True,
            "protocol": PROTOCOL_VERSION,
            "session_id": self.id,
            "python": ".".join(str(v) for v in sys.version_info[:3]),
            "runner": self.cfg.version,
            "caps": {
                "max_body": 8 << 20,
                "max_upload_total": MAX_UPLOAD_BYTES,
                "max_code_bytes": MAX_CODE_BYTES,
                "streaming": False,
            },
            "limits_ceiling": limits_mod.CEILINGS,
        }, b""

    async def _put(self, header: dict, body: bytes, sub: str, is_code: bool) -> tuple[dict, bytes]:
        rel = header.get("path")
        target = safe_join(self.root / sub, rel)

        if is_code:
            ext = target.suffix.lower()
            if ext in NATIVE_EXTENSIONS:
                raise ExecdError(
                    "native_not_allowed",
                    "extension native refusee : le code natif vient du socle de "
                    "l'image, pas du canal de transfert",
                    path=rel,
                    ext=ext,
                )
            if ext not in CODE_EXTENSIONS:
                raise ExecdError(
                    "bad_path",
                    f"extension refusee par code.put : {ext!r}",
                    path=rel,
                    reason="extension",
                )
            self._code_bytes += len(body)
            if self._code_bytes > MAX_CODE_BYTES:
                raise ExecdError(
                    "limit_exceeded",
                    "taille cumulee de code/ depassee",
                    limit="code_bytes",
                    value=self._code_bytes,
                    ceiling=MAX_CODE_BYTES,
                )
        else:
            self._uploaded += len(body)
            if self._uploaded > MAX_UPLOAD_BYTES:
                raise ExecdError(
                    "limit_exceeded",
                    "volume televerse depasse",
                    limit="upload_total",
                    value=self._uploaded,
                    ceiling=MAX_UPLOAD_BYTES,
                )

        key = f"{sub}/{rel}"
        handle = self._open_files.get(key)
        if handle is None:
            target.parent.mkdir(parents=True, exist_ok=True)
            # O_NOFOLLOW : refuse d'ecrire A TRAVERS un lien symbolique deja
            # pose. safe_join controle la destination, ceci controle le
            # composant final au moment exact de l'ouverture.
            try:
                fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
            except OSError as exc:
                if exc.errno in (errno.ELOOP, errno.EMLINK):
                    raise ExecdError(
                        "bad_path", "la cible est un lien symbolique", path=rel, reason="symlink"
                    ) from exc
                raise
            handle = os.fdopen(fd, "wb")
            self._open_files[key] = handle

        # Deportee : un chunk de 8 MiB bloquerait la boucle, donc les
        # frames des autres sessions, pendant toute la duree de l'ecriture.
        await asyncio.to_thread(handle.write, body)

        if header.get("eof"):
            del self._open_files[key]
            mode = header.get("mode")
            # Mode et proprietaire se posent sur le DESCRIPTEUR, ouvert en O_NOFOLLOW,
            # et non sur `target` : un lien pose a sa place entre la fermeture et un
            # chmod/chown par chemin aurait fait changer, en root, le fichier qu'il vise.
            try:
                # Le bit executable est toujours retire : rien de ce qui traverse
                # ce canal n'a vocation a etre lance directement.
                os.fchmod(handle.fileno(), (int(mode) & 0o644) if isinstance(mode, int) else 0o644)
                if self.cfg.uid is not None and os.geteuid() == 0:
                    with contextlib.suppress(OSError):
                        os.fchown(handle.fileno(), self.cfg.uid, self.cfg.gid)
            finally:
                handle.close()

        total = self._code_bytes if is_code else self._uploaded
        return {"ok": True, "path": rel, "written": len(body), "total": total}, b""

    async def m_file_put(self, header: dict, body: bytes) -> tuple[dict, bytes]:
        return await self._put(header, body, "in", is_code=False)

    async def m_code_put(self, header: dict, body: bytes) -> tuple[dict, bytes]:
        return await self._put(header, body, "code", is_code=True)

    async def m_file_get(self, header: dict, body: bytes) -> tuple[dict, bytes]:
        rel = header.get("path")
        target = safe_join(self.root / "out", rel)
        offset = int(header.get("offset", 0) or 0)
        want = int(header.get("len", 1 << 20) or 0)
        want = max(0, min(want, 8 << 20))

        try:
            size, data = await asyncio.to_thread(_read_at, target, offset, want)
        except FileNotFoundError as exc:
            raise ExecdError("not_found", "fichier absent de out/", path=rel) from exc
        except OSError as exc:
            raise ExecdError("not_found", f"lecture impossible : {exc}", path=rel) from exc

        return {
            "ok": True,
            "path": rel,
            "offset": offset,
            "len": len(data),
            "eof": offset + len(data) >= size,
        }, data

    async def m_reset(self, header: dict, body: bytes) -> tuple[dict, bytes]:
        self._close_handles()
        if self._proc is not None and self._proc.returncode is None:
            self._kill_group()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._proc.wait(), timeout=5)
        self._proc = None
        for sub in ("code", "in", "out", "work", "run"):
            shutil.rmtree(self.root / sub, ignore_errors=True)
        self._uploaded = 0
        self._code_bytes = 0
        self.setup()
        self.state = "READY"
        return {"ok": True, "session_id": self.id}, b""

    # -- execution --------------------------------------------------------- #
    async def m_tool_exec(self, header: dict, body: bytes) -> tuple[dict, bytes]:
        entrypoint = header.get("entrypoint")
        if not isinstance(entrypoint, str) or not entrypoint:
            raise ExecdError("protocol", "entrypoint manquant")

        args = header.get("args") or {}
        if not isinstance(args, dict):
            raise ExecdError("protocol", "args : objet JSON attendu (arguments nommes)")

        try:
            resolved = limits_mod.resolve(header.get("limits"))
        except limits_mod.LimitError as exc:
            raise ExecdError(
                "limit_exceeded", str(exc), limit=exc.limit, value=exc.value, ceiling=exc.floor
            ) from exc

        # Les handles encore ouverts sont fermes : un fichier pousse sans eof
        # doit etre lisible par le tool, pas rester dans le tampon.
        self._close_handles()

        # out/ est vide AVANT le job : le manifeste decrit alors exactement la
        # production de CE job, jamais un cumul de plusieurs executions.
        out_dir = self.root / "out"
        shutil.rmtree(out_dir, ignore_errors=True)
        out_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._own(out_dir)

        async with self.exec_sem:
            result = await self._run(entrypoint, args, resolved, header.get("env"))

        self.state = "DONE"
        return result, b""

    def _child_env(self, extra: Any) -> dict[str, str]:
        """Environnement CONSTRUIT, jamais herite.

        PYTHONPATH ne contient QUE le socle de dependances. Le repertoire de
        code est ajoute par runner a sys.path apres ses propres imports : un
        tool qui embarquerait un `json.py` occulterait sinon les modules dont
        runner a besoin pour rapporter l'erreur, et le diagnostic disparaitrait
        au moment ou il est le plus utile.
        """
        env = {
            "PATH": "/usr/bin:/bin",
            "PYTHONPATH": self.cfg.pydeps,
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUNBUFFERED": "1",
            "LANG": "C.UTF-8",
        }
        allowed = {"TZ", "LANG", "LC_ALL", "PYTHONHASHSEED"}
        if isinstance(extra, dict):
            for key, value in extra.items():
                if key in allowed and isinstance(value, str):
                    env[key] = value
        return env

    async def _run(self, entrypoint: str, args: dict, lim: dict, extra_env: Any) -> dict:
        job = {
            "session_id": self.id,
            "entrypoint": entrypoint,
            "args": args,
            "limits": lim,
            "dirs": {s: str(self.root / s) for s in ("code", "in", "out", "work")},
            "result_path": str(self._result_path),
        }
        job_path = self.root / "job.json"
        await asyncio.to_thread(_write_job, job_path, job, Path(job["result_path"]))

        kwargs: dict[str, Any] = {}
        if self.cfg.uid is not None and os.geteuid() == 0:
            # user/group/extra_groups sont traites dans le code C de
            # subprocess, donc sans executer de Python dans l'enfant forke.
            kwargs.update(user=self.cfg.uid, group=self.cfg.gid, extra_groups=[])

        loop = asyncio.get_running_loop()
        started = loop.time()

        spawning = asyncio.ensure_future(
            asyncio.create_subprocess_exec(
                sys.executable,
                RUNNER,
                "--job",
                str(job_path),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self.root / "work"),
                env=self._child_env(extra_env),
                start_new_session=True,  # pid == pgid : la terminaison vise le groupe
                **kwargs,
            )
        )
        try:
            proc = await asyncio.shield(spawning)
        except asyncio.CancelledError:
            # Annule pendant le lancement (la connexion a pris fin) : le process
            # existe peut-etre deja. On attend qu'il soit la pour le tuer, sans
            # quoi il courrait sans personne pour lui appliquer wall_ms.
            with contextlib.suppress(Exception):
                self._proc = await spawning
                self._kill_group()
                await asyncio.wait_for(self._proc.wait(), timeout=5)
            raise
        self._proc = proc

        assert proc.stdout is not None and proc.stderr is not None
        cap = limits_mod.CAPTURE_LIMIT
        abandon = asyncio.Event()
        pump = asyncio.gather(
            drain_bounded(proc.stdout, cap, abandon),
            drain_bounded(proc.stderr, cap, abandon),
        )

        timed_out = False
        try:
            try:
                # wall_ms couvre ce que RLIMIT_CPU ne voit pas : un sleep, une
                # attente d'I/O, un deadlock. Les deux limites sont necessaires.
                # Il court depuis le lancement ; ce qui suit la sortie du runner
                # (mort du groupe, tuyaux) a ses propres delais, courts.
                left = started + lim["wall_ms"] / 1000 - loop.time()
                await asyncio.wait_for(_exited(proc), timeout=max(left, 0))
            except TimeoutError:
                timed_out = True
        finally:
            # Quelle que soit la fin — sortie, delai, annulation (plus personne pour
            # lire la reponse : la connexion a pris fin) —, le GROUPE entier
            # s'arrete, avant toute lecture des tuyaux et AVANT que la place
            # d'execution soit rendue : un job tue ne court jamais en meme temps
            # que le suivant. Un descendant qui a survecu au runner tiendrait
            # sinon les tuyaux, et la lecture ne finirait jamais.
            self._kill_group()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(_exited(proc), timeout=5)
            await asyncio.wait([pump], timeout=PIPE_GRACE)
            abandon.set()

        (stdout, out_trunc), (stderr, err_trunc) = await pump
        duration_ms = int((loop.time() - started) * 1000)
        rc = proc.returncode
        self._proc = None

        if timed_out:
            return ExecdError(
                "timeout",
                f"depassement de wall_ms ({lim['wall_ms']} ms)",
                limit="wall_ms",
                elapsed_ms=duration_ms,
            ).as_payload() | {"stdout": stdout, "stderr": stderr}

        try:
            payload = await asyncio.to_thread(self._read_result, rc)
        except ExecdError as exc:
            merged = exc.as_payload()
            merged.update(stdout=stdout, stderr=stderr, duration_ms=duration_ms)
            return merged
        if payload is None:
            # Mort par signal AVANT d'avoir pu ecrire result.json. Le signal
            # designe la limite franchie ; sans cette traduction, toute limite
            # atteinte remonterait en runner_failure, c'est-a-dire en « le
            # service a un bug » plutot qu'en « ton job a depasse son budget ».
            err = self._signal_error(rc, lim)
            return self._with_streams(err, stderr) | {
                "stdout": stdout,
                "stderr": stderr,
                "duration_ms": duration_ms,
            }

        payload.update(
            stdout=stdout,
            stdout_truncated=out_trunc,
            stderr=stderr,
            stderr_truncated=err_trunc,
            duration_ms=duration_ms,
            limits_applied=lim,
        )

        if payload.get("ok"):
            try:
                # sha256 sur des fichiers volumineux : purement CPU, hors boucle.
                payload["outputs"], payload["outputs_skipped"] = await asyncio.to_thread(
                    self._scan_outputs, lim["out_files"]
                )
            except ExecdError as exc:
                merged = exc.as_payload()
                merged.update(stdout=stdout, stderr=stderr, duration_ms=duration_ms)
                return merged
        return payload

    @staticmethod
    def _signal_error(rc: int | None, lim: dict) -> ExecdError:
        if rc == -signal.SIGXCPU:
            return ExecdError(
                "timeout", f"depassement de cpu_ms ({lim['cpu_ms']} ms)", limit="cpu_ms"
            )
        if rc == -signal.SIGXFSZ:
            return ExecdError(
                "limit_exceeded",
                "taille de fichier depassee",
                limit="fsize_bytes",
                ceiling=lim["fsize_bytes"],
            )
        if rc == -signal.SIGKILL:
            # Nous n'avons pas tue (le timeout wall est traite en amont) : reste
            # le tueur OOM du noyau, qui ne laisse aucune trace exploitable.
            return ExecdError(
                "oom", "processus tue par le noyau (memoire)", mem_bytes=lim["mem_bytes"]
            )
        return ExecdError(
            "runner_failure", f"runner sorti en {rc} sans result.json exploitable", exit_code=rc
        )

    @staticmethod
    def _with_streams(err: ExecdError, stderr: str) -> dict:
        payload = err.as_payload()
        if err.kind == "runner_failure":
            payload["error"].setdefault("detail", {})["stderr_tail"] = stderr[-2000:]
        return payload

    def _read_result(self, rc: int | None) -> dict | None:
        if rc != 0:
            return None
        # Lecture BORNEE : le fichier est ecrit par le job, et sa taille n'a que
        # fsize_bytes pour limite.
        try:
            with _open_regular(self._result_path) as handle:
                raw = handle.read(limits_mod.RESULT_LIMIT + 1)
                size = os.fstat(handle.fileno()).st_size
        except OSError:
            return None
        if len(raw) > limits_mod.RESULT_LIMIT:
            raise ExecdError(
                "limit_exceeded",
                f"resultat trop gros : result.json fait {size} octets"
                f" (plafond {limits_mod.RESULT_LIMIT})",
                limit="result_bytes",
                value=size,
                ceiling=limits_mod.RESULT_LIMIT,
            )
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) and "ok" in payload else None

    def _scan_outputs(self, max_files: int) -> tuple[list[dict], list[str]]:
        """Manifeste de out/ : chemin, taille, empreinte.

        Renvoye AVEC le resultat plutot que sur demande separee : l'hote voit
        immediatement la production et decide quoi rapatrier. Un fichier de
        2 Go qu'il ne veut pas ne coute alors aucun transfert.
        """
        out_dir = self.root / "out"
        entries: list[dict] = []
        skipped: list[str] = []

        for root, dirs, files in os.walk(out_dir, followlinks=False):
            # Les liens symboliques sont ignores : suivre un lien sortirait de
            # out/, et le rapatrier n'aurait pas de sens cote hote.
            dirs[:] = [d for d in dirs if not os.path.islink(os.path.join(root, d))]
            for name in sorted(files):
                full = Path(root) / name
                raw = os.fsencode(full.relative_to(out_dir))
                rel = raw.decode("utf-8", "replace")
                if (
                    full.is_symlink()
                    or not full.is_file()
                    # Un nom qui n'est pas de l'UTF-8 ne tient pas dans la reponse (JSON) et
                    # l'hote ne pourrait pas le demander : il est signale, pas rapatrie.
                    or rel.encode("utf-8") != raw
                ):
                    skipped.append(rel)
                    continue
                if len(entries) >= max_files:
                    raise ExecdError(
                        "limit_exceeded",
                        "trop de fichiers dans out/",
                        limit="out_files",
                        value=len(entries) + 1,
                        ceiling=max_files,
                    )
                try:
                    handle = _open_regular(full)
                except OSError:
                    # Remplace par un lien ou un tube depuis le controle, ou disparu.
                    skipped.append(rel)
                    continue
                digest = hashlib.sha256()
                with handle:
                    size = os.fstat(handle.fileno()).st_size
                    while block := handle.read(1 << 20):
                        digest.update(block)
                entries.append({"path": rel, "size": size, "sha256": digest.hexdigest()})
        return entries, skipped
