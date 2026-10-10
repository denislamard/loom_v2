"""execd — service d'execution de tools, ecoute sur vsock.

Recoit un arbre de code et des fichiers, execute une fonction sous contrainte
de ressources, rend le resultat et les fichiers produits.

CE QUE execd NE FAIT PAS

Resoudre un identifiant de tool, connaitre un tenant, persister quoi que ce
soit au-dela d'une connexion, acceder au reseau, decider si un code a le droit
de s'executer. Toutes ces responsabilites appartiennent a l'hote. Une VM qui
resoudrait « tool_id -> code » deviendrait une autorite de decision sur ce qui
s'execute ; c'est pourquoi tool.exec transporte le CODE, jamais une reference.

Corollaire pratique : la suppression d'un tool n'est pas un probleme de la VM.
Retirer une entree du catalogue hote suffit — rien a invalider ici.

ECOUTE

AF_VSOCK par defaut. EXECD_UDS bascule sur AF_UNIX, ce qui permet de faire
tourner et de tester le service sur l'hote, sans VM ni KVM : le protocole est
identique, seule la famille de socket change.
"""

from __future__ import annotations

import asyncio
import contextlib
import errno
import logging
import os
import shutil
import signal
import socket
import sys
from dataclasses import dataclass
from pathlib import Path

from protocol import Conn, ExecdError, ProtocolError
from session import Session

VERSION = "0.1.0"

log = logging.getLogger("execd")

# Pause apres un accept en erreur (descripteurs epuises...), avant de reessayer.
ACCEPT_RETRY = 0.1


@dataclass(frozen=True)
class Config:
    port: int
    uds: str | None
    jobs_dir: str
    pydeps: str
    uid: int | None
    gid: int | None
    max_sessions: int
    max_concurrent_exec: int
    version: str = VERSION


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        log.warning("%s=%r invalide, valeur par defaut %d", name, raw, default)
        return default


def load_config() -> Config:
    uid = _int_env("EXECD_UID", -1)
    gid = _int_env("EXECD_GID", -1)
    return Config(
        port=_int_env("EXECD_PORT", 5100),
        uds=os.environ.get("EXECD_UDS") or None,
        jobs_dir=os.environ.get("EXECD_JOBS_DIR", "/data/jobs"),
        pydeps=os.environ.get("EXECD_PYDEPS", "/opt/pydeps"),
        uid=uid if uid >= 0 else None,
        gid=gid if gid >= 0 else None,
        max_sessions=_int_env("EXECD_MAX_SESSIONS", 4),
        max_concurrent_exec=_int_env("EXECD_MAX_CONCURRENT_EXEC", 1),
    )


def make_listener(cfg: Config) -> socket.socket:
    """Cree la socket d'ecoute, liee une fois pour la duree du processus.

    Le listener ne doit JAMAIS etre reouvert en cours de vie : une sonde de
    disponibilite qui ouvre puis jette une connexion forcerait un rebind, et
    creerait une fenetre pendant laquelle la vraie connexion ne trouve aucun
    listener. La logique de reessai appartient au client.
    """
    if cfg.uds:
        Path(cfg.uds).parent.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(FileNotFoundError):
            os.unlink(cfg.uds)
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.bind(cfg.uds)
        os.chmod(cfg.uds, 0o660)
        log.info("ecoute sur AF_UNIX %s (mode developpement)", cfg.uds)
    else:
        sock = socket.socket(socket.AF_VSOCK, socket.SOCK_STREAM)
        # VMADDR_CID_ANY : on ecoute quel que soit le CID attribue a la VM,
        # qui est choisi par l'hote et que l'invite n'a pas a connaitre.
        sock.bind((socket.VMADDR_CID_ANY, cfg.port))
        log.info("ecoute sur AF_VSOCK port %d", cfg.port)

    sock.listen(16)
    sock.setblocking(False)
    return sock


def prepare_jobs_dir(cfg: Config) -> None:
    """Repart d'un /data/jobs vide a chaque demarrage.

    Un workdir n'a de sens que pendant la vie de la connexion qui le possede.
    Si execd redemarre — crash ou reboot — toutes les sessions sont mortes et
    leurs workdirs sont des residus. Les purger ici evite d'ecrire un
    ramasse-miettes et garantit qu'une VM reutilisee repart propre.
    """
    root = Path(cfg.jobs_dir)
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True, exist_ok=True)
    # 0711 : le compte sandbox doit TRAVERSER pour atteindre son workdir, sans
    # pouvoir enumerer le repertoire et donc decouvrir les sessions voisines.
    os.chmod(root, 0o711)


async def refuse(conn: Conn, cfg: Config) -> None:
    """Repond `busy` a la premiere requete, puis ferme.

    On ATTEND la requete plutot que d'emettre le refus des l'accept : le
    protocole n'admet aucun message non sollicite du serveur, et un client qui
    envoie avant de lire tomberait sur une socket deja fermee — il verrait un
    EPIPE au lieu du motif du refus.
    """
    with contextlib.suppress(Exception):
        header, _ = await asyncio.wait_for(conn.read_frame(), timeout=10)
        payload = ExecdError(
            "busy", "trop de sessions simultanees", max_sessions=cfg.max_sessions
        ).as_payload()
        if header.get("seq") is not None:
            payload["seq"] = header["seq"]
        await conn.write_frame(payload)


async def handle(
    sock: socket.socket, cfg: Config, exec_sem: asyncio.Semaphore, admitted: bool
) -> None:
    conn = Conn(sock)
    session: Session | None = None
    try:
        if not admitted:
            await refuse(conn, cfg)
            return

        session = Session(conn, cfg, exec_sem)
        session.setup()
        log.info("session %s ouverte", session.id)
        await session.serve()

    except ProtocolError as exc:
        # Defaut de cadrage : on ferme sans negocier. Poursuivre la lecture
        # d'un flux desynchronise ne menerait a rien de bon.
        log.warning("protocole invalide : %s", exc)
    except (ConnectionResetError, BrokenPipeError):
        pass
    except OSError as exc:
        if exc.errno not in (errno.ECONNRESET, errno.EPIPE):
            log.exception("erreur de connexion")
    except Exception:
        log.exception("erreur non rattrapee dans la session")
    finally:
        if session is not None:
            await session.cleanup()
            log.info("session %s fermee", session.id)
        conn.close()


async def serve(cfg: Config) -> None:
    loop = asyncio.get_running_loop()
    listener = make_listener(cfg)
    exec_sem = asyncio.Semaphore(cfg.max_concurrent_exec)
    live: set[asyncio.Task] = set()

    stopping = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stopping.set)

    # Compteur de sessions ADMISES, et non de taches en vol. Compter les
    # taches faisait qu'une rafale de connexions simultanees se refusait
    # elle-meme : toutes etaient creees avant qu'aucune n'ait demarre, et
    # chacune voyait alors les autres comme deja installees.
    #
    # L'increment se fait dans la boucle d'accept, de facon synchrone : asyncio
    # etant mono-tache, il n'y a pas de fenetre entre le test et l'increment.
    active = 0

    async def accept_loop() -> None:
        nonlocal active
        while True:
            try:
                sock, _ = await loop.sock_accept(listener)
            except ConnectionError:
                # Un client a renonce avant l'accept : la connexion suivante n'y est pour rien.
                continue
            except OSError as exc:
                # EMFILE, ENFILE, ENOBUFS, ENOMEM… : une rafale de connexions ne doit pas
                # arreter l'ecoute. On attend un peu que des descripteurs se liberent.
                log.error("accept impossible (%s) : nouvel essai dans %.1f s", exc, ACCEPT_RETRY)
                await asyncio.sleep(ACCEPT_RETRY)
                continue
            admitted = active < cfg.max_sessions
            if admitted:
                active += 1

            task = loop.create_task(handle(sock, cfg, exec_sem, admitted))
            live.add(task)

            def done(t: asyncio.Task, was_admitted: bool = admitted) -> None:
                nonlocal active
                live.discard(t)
                if was_admitted:
                    active -= 1

            task.add_done_callback(done)

    def accept_ended(task: asyncio.Task) -> None:
        # Sans ceci, une boucle d'accept morte laissait le processus en vie, le listener
        # ouvert et plus personne de servi : systemd ne voyait aucun echec a relancer.
        if not task.cancelled() and task.exception() is not None:
            log.error("boucle d'accept arretee", exc_info=task.exception())
            stopping.set()

    accepter = loop.create_task(accept_loop())
    accepter.add_done_callback(accept_ended)
    log.info("execd %s pret (jobs=%s, uid=%s)", VERSION, cfg.jobs_dir, cfg.uid)

    await stopping.wait()
    log.info("arret demande")
    crash = None if accepter.cancelled() or not accepter.done() else accepter.exception()

    accepter.cancel()
    # Une boucle morte d'une erreur la relance a l'``await`` : elle est deja gardee dans ``crash``.
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await accepter
    listener.close()
    if cfg.uds:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(cfg.uds)

    # Les sessions en cours sont annulees : leur `finally` purge le workdir et
    # tue le groupe de processus du job.
    for task in list(live):
        task.cancel()
    if live:
        await asyncio.gather(*live, return_exceptions=True)
    if crash is not None:
        # Le processus sort en erreur : c'est ce qui fait relancer l'unite (Restart=always).
        raise RuntimeError("boucle d'accept arretee") from crash


def main() -> int:
    logging.basicConfig(
        level=os.environ.get("EXECD_LOG_LEVEL", "INFO").upper(),
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    cfg = load_config()

    if not cfg.uds and not hasattr(socket, "AF_VSOCK"):
        log.error("AF_VSOCK indisponible dans ce Python")
        return 1

    try:
        prepare_jobs_dir(cfg)
    except OSError as exc:
        log.error("%s inutilisable : %s", cfg.jobs_dir, exc)
        return 1

    try:
        asyncio.run(serve(cfg))
    except KeyboardInterrupt:
        pass
    except Exception:
        log.exception("execd arrete sur une erreur")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
