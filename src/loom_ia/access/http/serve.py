# SPDX-License-Identifier: Apache-2.0
"""Serveur HTTP de développement (N2), et son rechargement (#48, J6.4a).

``uvicorn`` sert l'application ; la configuration donne l'adresse et le port,
que la ligne de commande peut remplacer. La journalisation reste celle de
loom (``telemetry.logging``) : uvicorn ne réinstalle pas la sienne.

``loom serve --reload`` (``serve_reloading``) relance le **process** qui sert
quand un fichier change — config relue, agents remontés, modules voisins
réimportés, serveurs MCP relancés ; rien ne se recharge à chaud dans un
process vivant. Un superviseur garde la socket et surveille les fichiers ; à
chaque changement :

1. un **nouveau** process charge la config et monte chaque agent pour chaque
   client, pendant que l'ancien sert encore ;
2. s'il échoue — config cassée, module voisin qui ne s'importe pas, outil
   introuvable —, il le dit et sort : l'ancien continue de servir ;
3. sinon il prend la socket, et l'ancien s'arrête comme sur un Ctrl+C : il
   ne prend plus de requêtes, finit celles en cours et ses runs (jusqu'à
   ``execution.shutdown_timeout``), puis sort.

Les deux process partagent la socket : une requête qui arrive pendant la
relève attend dans sa file, elle n'est pas refusée.

Ce qui est surveillé : tout le dossier de la config (et ``agents_dir``,
``prompts_dir`` s'ils sont ailleurs), **sauf** ce que la config écrit —
journal, fichiers, bases SQLite, ceux de chaque client — et le bruit d'usage
(``__pycache__``, ``.git``, ``.venv``, fichiers temporaires d'éditeur).

Rien ne passe entre deux guetteurs. « Rechargement : surveille … » n'est dit
qu'une fois le guetteur armé ; et ce qui a changé avant — pendant que le
premier process démarrait, ou que les dossiers surveillés changeaient — se
voit en comparant l'état des fichiers (date, taille, inode) que le process a
relevé avant de lire sa config à celui du disque, guetteur armé. Un écart
recharge, comme un changement vu par le guetteur.

Le montage d'essai remplace les clients de modèle par des clients jamais
appelés : une clé d'API absente ne se voit qu'au premier run, comme sans
``--reload``. Refusé en profil ``prod``.
"""

import asyncio
import multiprocessing
import os
import re
import signal
import socket
import sys
import threading
import traceback
from collections.abc import AsyncGenerator, Callable, Iterable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from multiprocessing.connection import Connection
from multiprocessing.context import SpawnContext
from multiprocessing.process import BaseProcess
from pathlib import Path
from types import FrameType
from typing import TYPE_CHECKING, Final

import uvicorn

from loom_ia.access.api import Loom
from loom_ia.access.http.app import create_app
from loom_ia.adapters.stores import InMemoryEventStore
from loom_ia.config import ConfigError, LoomConfig, load_config
from loom_ia.config.models import StorageConfig
from loom_ia.core.model import ModelChunk, ModelRequest, ModelSpec
from loom_ia.runtime import apply_logging, build_agent

if TYPE_CHECKING:
    from watchfiles import Change

# Bandeau imprimé par chaque process qui prend la main : la config, le profil
# demandé par l'option, l'adresse servie.
type Banner = Callable[[LoomConfig, str | None, str, int], None]

# Code de sortie d'un process qui refuse sa config : celui de la CLI.
REFUSED: Final = 2
# Code d'un process dont l'application n'a pas démarré (uvicorn le dit).
NOT_STARTED: Final = 3
# Dossiers de bruit, où qu'ils soient sous un dossier surveillé : ceux que
# ``watchfiles`` écarte, plus le cache de ruff.
NOISE_DIRS: Final = frozenset(
    {
        "__pycache__",
        ".git",
        ".hg",
        ".svn",
        ".tox",
        ".venv",
        ".idea",
        "node_modules",
        ".mypy_cache",
        ".pytest_cache",
        ".hypothesis",
        ".ruff_cache",
    }
)
# Fichiers de bruit, par leur nom : compilés, temporaires d'éditeur.
NOISE_FILES: Final = tuple(
    re.compile(pattern)
    for pattern in (
        r"\.py[cod]$",
        r"\.___jb_...___$",
        r"\.sw.$",
        r"~$",
        r"^\.\#",
        r"^\.DS_Store$",
        r"^flycheck_",
    )
)
# Changements montrés en entier ; au-delà, le compte.
SHOWN_CHANGES: Final = 5
# Ce qu'un process qui s'arrête dit quand il ne prend plus de requêtes, et
# combien de temps on l'attend (secondes) : au-delà, sa boucle est bloquée, et
# elle ne prend rien non plus.
DEAF: Final = "sourd"
DEAFNESS: Final = 5.0
# Tous les combien un process qui sert vérifie que son superviseur est là (secondes).
ORPHANED_EVERY: Final = 0.5
# Tous les combien le guetteur rend la main sans changement (millisecondes) :
# sa première main dit qu'il est armé.
WAKE_MS: Final = 200

# Lectures de la config au plus, au démarrage d'un process, pour qu'elle et
# son relevé portent sur les mêmes dossiers.
READS: Final = 3

# L'état d'un fichier surveillé : date de modification (ns), taille, inode.
type FileState = tuple[int, int, int]


def serve(loom: Loom, *, host: str | None = None, port: int | None = None) -> None:
    """Sert l'instance jusqu'à l'arrêt du process, puis la ferme.

    ``host`` remplace celui de la config, et c'est lui que juge l'avertissement d'une API
    sans clé déclarée (une erreur en profil prod).
    """
    http = loom.config.server.http
    uvicorn.run(
        create_app(loom, own=True, host=host),
        host=host or http.host,
        port=port or http.port,
        log_config=None,
    )


# --- Ce qui est surveillé ------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Watched:
    """Ce que le rechargement surveille, et ce qu'il écarte parce que loom l'écrit."""

    # Dossiers surveillés, sous-dossiers compris.
    roots: tuple[Path, ...]
    # Dossiers écrits par loom : journaux JSONL, fichiers des runs.
    dirs: tuple[Path, ...]
    # Fichiers écrits par loom : bases SQLite (leurs ``-wal``, ``-shm`` et
    # ``-journal`` avec elles).
    files: tuple[Path, ...]

    def counts(self, path: Path) -> bool:
        """Vrai si un changement de ce fichier doit relancer le serveur."""
        root = next((r for r in self.roots if path.is_relative_to(r)), None)
        if root is None:
            return False
        parts = path.relative_to(root).parts
        if any(part in NOISE_DIRS for part in parts[:-1]):
            return False
        if parts and any(p.search(parts[-1]) for p in NOISE_FILES):
            return False
        if any(path.is_relative_to(d) for d in self.dirs):
            return False
        return not any(
            path.parent == f.parent and (path.name == f.name or path.name.startswith(f"{f.name}-"))
            for f in self.files
        )

    def filter(self, change: Change, raw: str) -> bool:
        """Le filtre donné à ``watchfiles`` : ``counts`` sur le chemin reçu."""
        return self.counts(Path(raw))

    def present(self) -> frozenset[Path]:
        """Les fichiers qui comptent, tels qu'ils sont sur le disque maintenant."""
        return frozenset(self.state())

    def state(self) -> dict[Path, FileState]:
        """Les fichiers qui comptent, et l'état de chacun (date, taille, inode)."""
        found: dict[Path, FileState] = {}
        for root in self.roots:
            for folder, subdirs, names in os.walk(root):
                here = Path(folder)
                subdirs[:] = [
                    name
                    for name in subdirs
                    if name not in NOISE_DIRS
                    and not any((here / name).is_relative_to(d) for d in self.dirs)
                ]
                for name in names:
                    path = here / name
                    if not self.counts(path):
                        continue
                    try:
                        stat = path.stat()
                    except FileNotFoundError:
                        # Parti entre la liste du dossier et son état.
                        continue
                    found[path] = (stat.st_mtime_ns, stat.st_size, stat.st_ino)
        return found

    def describe(self) -> str:
        """Une ligne : les dossiers surveillés, puis ce qui en est écarté."""
        base = self.roots[0]

        def shown(path: Path, *, folder: bool) -> str:
            name = str(path.relative_to(base)) if path.is_relative_to(base) else str(path)
            return f"{name}{os.sep}" if folder else name

        def covered(path: Path) -> bool:
            return any(path != d and path.is_relative_to(d) for d in self.dirs)

        roots = ", ".join(str(root) for root in self.roots)
        excluded = [
            *(shown(d, folder=True) for d in self.dirs if _under(d, self.roots) and not covered(d)),
            *(
                shown(f, folder=False)
                for f in self.files
                if _under(f, self.roots) and not covered(f)
            ),
        ]
        return f"{roots} (sauf {', '.join(excluded)})" if excluded else roots


def watched(config: LoomConfig) -> Watched:
    """Ce que le rechargement surveille pour cette config.

    Lève ``ConfigError`` si un dossier que loom écrit contient un dossier
    surveillé : le rechargement ne saurait pas distinguer ses écritures de
    celles du développeur.
    """
    base = config.base_dir if config.base_dir is not None else Path.cwd()
    roots = [base]
    for extra in (config.agents_dir, config.prompts_dir):
        if not extra.is_relative_to(base) and extra not in roots:
            roots.append(extra)
    storages = [config.storage, *(t.storage for t in config.tenants if t.storage is not None)]
    dirs: list[Path] = []
    files: list[Path] = []
    for storage in storages:
        written_dirs, written_files = _written(storage)
        dirs += [d for d in written_dirs if d not in dirs]
        files += [f for f in written_files if f not in files]
    for folder in dirs:
        for root in roots:
            if root.is_relative_to(folder):
                raise ConfigError(
                    f"--reload : {folder} est écrit par loom (journal ou fichiers) et contient "
                    f"{root}, surveillé — mettre les données dans un sous-dossier"
                )
    return Watched(roots=tuple(roots), dirs=tuple(dirs), files=tuple(files))


def _written(storage: StorageConfig) -> tuple[list[Path], list[Path]]:
    """Les dossiers et les fichiers qu'un stockage écrit sur le disque."""
    dirs: list[Path] = []
    files: list[Path] = []
    events = storage.events
    if events.path is not None:
        (dirs if events.backend == "jsonl" else files).append(events.path)
    if storage.artifacts_path is not None:
        dirs.append(storage.artifacts_path)
    if storage.idempotency.path is not None:
        files.append(storage.idempotency.path)
    return dirs, files


def _under(path: Path, roots: Iterable[Path]) -> bool:
    return any(path.is_relative_to(root) for root in roots)


def changed(
    changes: Iterable[tuple[Change, str]],
    before: Mapping[Path, FileState],
    after: Mapping[Path, FileState],
    base: Path,
) -> str:
    """Les fichiers changés, en clair (``prompts/relance.md modifié, …``) ; vide si aucun.

    Le verbe se lit sur le disque, avant et après, pas sur l'événement : un
    éditeur qui enregistre par renommage remplace le fichier (« modifié », pas
    « ajouté »), et un fichier temporaire apparu puis disparu dans le même lot
    n'a rien changé — un lot qui n'a que ceux-là ne recharge pas.

    L'état ajoute ce qu'aucun événement n'a dit : un changement fait avant
    que le guetteur soit armé. Il n'en retire rien — une modification de
    même taille dans le même tic d'horloge du disque ne change pas l'état,
    l'événement la dit.
    """
    told: dict[Path, str] = {}
    for _, raw in changes:
        path = Path(raw)
        if path in after:
            told[path] = "modifié" if path in before else "ajouté"
        elif path in before:
            told[path] = "supprimé"
    for path in before.keys() | after.keys():
        if path in told:
            continue
        if path not in after:
            told[path] = "supprimé"
        elif path not in before:
            told[path] = "ajouté"
        elif before[path] != after[path]:
            told[path] = "modifié"
    items = [
        f"{path.relative_to(base) if path.is_relative_to(base) else path} {verb}"
        for path, verb in sorted(told.items())
    ]
    if len(items) > SHOWN_CHANGES:
        rest = len(items) - SHOWN_CHANGES
        items = [*items[:SHOWN_CHANGES], f"et {rest} autre(s)"]
    return ", ".join(items)


# --- Le superviseur ---------------------------------------------------------------------


def serve_reloading(
    path: Path,
    *,
    profile: str | None,
    host: str,
    port: int,
    banner: Banner,
) -> int:
    """Sert la config de ``path`` et la recharge à chaque changement ; rend le code de sortie.

    Rend 0 à l'arrêt (Ctrl+C, ``SIGTERM``), 2 si le premier process refuse sa
    config ou si la socket ne s'ouvre pas : il n'y a alors rien à garder.
    ``banner`` est appelée par chaque process qui prend la main ; elle doit
    être une fonction de module (le process neuf la retrouve par son nom).
    """
    from watchfiles import watch

    try:
        sock = _bound(host, port)
    except OSError as exc:
        print(f"Impossible d'écouter sur {host}:{port} : {exc.strerror}", file=sys.stderr)
        return REFUSED
    context = multiprocessing.get_context("spawn")
    stopping = threading.Event()

    def stop(signum: int, frame: FrameType | None) -> None:
        stopping.set()

    previous = {sig: signal.signal(sig, stop) for sig in (signal.SIGINT, signal.SIGTERM)}
    launch = _Launcher(context, path, profile, host, port, sock, banner, stopping)
    leaving: list[_Serving] = []
    current: _Serving | None = None
    try:
        started = launch()
        if started is None:
            if not stopping.is_set():
                print("Le serveur n'a pas démarré.", file=sys.stderr)
                return REFUSED
            return 0
        # ``known`` : l'état des fichiers que le process qui sert reflète —
        # relevé par lui avant de lire sa config, puis à chaque lot.
        current, spec, known = started
        while not stopping.is_set():
            # Un nouveau dossier surveillé (agents_dir déplacé) demande un
            # nouveau guetteur ; le reste du temps, le même sert. Il ne
            # surveille qu'une fois créé, à sa première main : jusque-là,
            # seul l'état des fichiers dit ce qui a changé.
            guard = watch(
                *spec.roots,
                watch_filter=spec.filter,
                stop_event=stopping,
                raise_interrupt=False,
                rust_timeout=WAKE_MS,
                yield_on_timeout=True,
            )
            armed = False
            try:
                for changes in guard:
                    if not armed:
                        armed = True
                        _say(f"Rechargement : surveille {spec.describe()}")
                    elif not changes:
                        continue
                    leaving = _reaped(leaving)
                    now = spec.state()
                    told = changed(changes, known, now, spec.roots[0])
                    known = now
                    if not told:
                        continue
                    _say(f"Rechargement : {told}")
                    started = launch()
                    if started is None:
                        if stopping.is_set():
                            break
                        _say("Rechargement refusé : l'ancien process continue de servir.")
                        continue
                    # La main ne passe qu'une fois l'ancien sourd : jusque-là, il
                    # prend encore des requêtes sur la socket commune.
                    current.leave()
                    leaving.append(current)
                    current, renewed, known = started
                    _say("Rechargé : le nouveau process sert ; l'ancien finit ce qu'il a en cours.")
                    if renewed != spec:
                        spec = renewed
                        break
            finally:
                guard.close()
    finally:
        everyone = (*leaving, *((current,) if current is not None else ()))
        for served in everyone:
            if served.process.is_alive():
                served.process.terminate()
        for served in everyone:
            served.close()
        sock.close()
        for sig, handler in previous.items():
            signal.signal(sig, handler)
    return 0


@dataclass(slots=True)
class _Serving:
    """Un process qui sert, et le tube par lequel il dit où il en est."""

    process: BaseProcess
    told: Connection

    def leave(self) -> None:
        """Lui demande de s'arrêter, et attend qu'il ne prenne plus de requêtes.

        Il le dit dès qu'il a cessé d'écouter, avant de finir ses requêtes et
        ses runs ; un process qui ne le dit pas dans ``DEAFNESS`` est laissé à
        sa fin.
        """
        self.process.terminate()
        try:
            if self.told.poll(DEAFNESS):
                self.told.recv()
        except EOFError, OSError:
            pass

    def close(self) -> None:
        self.process.join()
        self.told.close()


class _Launcher:
    """Démarre un process qui sert, et attend qu'il ait pris la main ou renoncé."""

    def __init__(
        self,
        context: SpawnContext,
        path: Path,
        profile: str | None,
        host: str,
        port: int,
        sock: socket.socket,
        banner: Banner,
        stopping: threading.Event,
    ) -> None:
        self.context = context
        self.args = (path, profile, host, port, sock, banner)
        self.stopping = stopping

    def __call__(self) -> tuple[_Serving, Watched, dict[Path, FileState]] | None:
        reader, writer = self.context.Pipe(duplex=False)
        process = self.context.Process(
            target=_serving, args=(*self.args, writer), name="loom-serve"
        )
        process.start()
        # Seul le process neuf écrit : sa sortie ferme le tube, ce qui se lit.
        writer.close()
        while not reader.poll(0.1):
            if self.stopping.is_set():
                process.terminate()
                process.join()
                reader.close()
                return None
        try:
            spec: Watched
            seen: dict[Path, FileState]
            spec, seen = reader.recv()
        except EOFError:
            process.join()
            reader.close()
            return None
        return _Serving(process, reader), spec, seen


def _bound(host: str, port: int) -> socket.socket:
    """La socket que tous les process serviront, à l'écoute dès maintenant."""
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    sock = socket.socket(family=family)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, port))
        # Écouter tout de suite : une requête qui arrive avant le premier
        # process, ou pendant une relève, attend dans la file.
        sock.listen(2048)
    except OSError:
        sock.close()
        raise
    return sock


def _reaped(leaving: list[_Serving]) -> list[_Serving]:
    """Les process encore en train de finir ; ceux qui sont sortis sont relevés."""
    alive: list[_Serving] = []
    for served in leaving:
        if served.process.is_alive():
            alive.append(served)
        else:
            served.close()
    return alive


def _say(line: str) -> None:
    print(line, flush=True)


# --- Le process qui sert ----------------------------------------------------------------


def _serving(
    path: Path,
    profile: str | None,
    host: str,
    port: int,
    sock: socket.socket,
    banner: Banner,
    ready: Connection,
) -> None:
    """Corps d'un process qui sert : charger, monter, puis prendre la socket."""
    # Le superviseur, tant qu'il est là : un process qui lui survit s'arrête.
    supervisor = os.getppid()
    try:
        started = asyncio.run(_served(path, profile, host, port, sock, banner, ready, supervisor))
    except KeyboardInterrupt:
        return
    except ConfigError as exc:
        print(f"Config refusée : {exc}", file=sys.stderr, flush=True)
        sys.exit(REFUSED)
    except Exception as exc:
        # Un module voisin qui ne s'importe pas, une erreur dans le code de
        # l'utilisateur : la trace dit la ligne.
        print(f"Config refusée :\n{_trace(exc, path.resolve().parent)}", file=sys.stderr, end="")
        sys.stderr.flush()
        sys.exit(REFUSED)
    if not started:
        sys.exit(NOT_STARTED)


def _trace(exc: BaseException, base: Path) -> str:
    """La trace d'une erreur, réduite aux lignes du dossier de la config s'il y en a.

    Une erreur de syntaxe porte déjà son fichier et sa ligne : elle se dit sans
    pile. Une erreur sans aucune ligne de l'utilisateur est une erreur de
    loom : elle garde toute sa pile.
    """
    told = traceback.TracebackException.from_exception(exc)
    own = [frame for frame in told.stack if Path(frame.filename).is_relative_to(base)]
    if own or isinstance(exc, SyntaxError):
        told.stack = traceback.StackSummary.from_list(own)
    return "".join(told.format(chain=False))


async def _served(
    path: Path,
    profile: str | None,
    host: str,
    port: int,
    sock: socket.socket,
    banner: Banner,
    ready: Connection,
    supervisor: int,
) -> bool:
    config, spec, seen = _read(path, profile)
    apply_logging(config)
    if config.strict:
        raise ConfigError(
            "--reload est refusé en profil prod : un service ne se relance pas sur une "
            "modification de fichier"
        )
    loom = Loom(config)
    try:
        await _mounted(loom)
        app = create_app(loom, own=True, host=host)
    except BaseException:
        await loom.aclose()
        raise

    def taken() -> None:
        banner(config, profile, host, port)
        sys.stdout.flush()
        ready.send((spec, seen))

    def deaf() -> None:
        # Le superviseur peut être parti (Ctrl+C) : il n'y a plus personne à prévenir.
        with suppress(OSError):
            ready.send(DEAF)

    server = _Taking(
        uvicorn.Config(app, host=host, port=port, log_config=None), taken, deaf, supervisor
    )
    try:
        await server.serve(sockets=[sock])
    finally:
        ready.close()
    return server.started


def _read(path: Path, profile: str | None) -> tuple[LoomConfig, Watched, dict[Path, FileState]]:
    """La config qui sert, ce qu'elle fait surveiller, et l'état des fichiers relevé avant elle.

    Une première lecture dit quoi relever ; la config qui sert est lue après
    le relevé. Un fichier changé entre les deux est lu dans sa nouvelle
    version ; changé après, le superviseur le voit en comparant l'état — il
    n'échappe jamais aux deux. Si la relecture fait surveiller d'autres
    dossiers, le relevé est refait sur eux.
    """
    spec = watched(load_config(path, profile=profile))
    reads = 1
    while True:
        seen = spec.state()
        config = load_config(path, profile=profile)
        read = watched(config)
        reads += 1
        if read == spec or reads >= READS:
            return config, read, seen
        spec = read


class _Taking(uvicorn.Server):
    """Un serveur uvicorn qui prévient quand il écoute — il prend la main —, et quand il cesse."""

    def __init__(
        self,
        config: uvicorn.Config,
        taken: Callable[[], None],
        deaf: Callable[[], None],
        supervisor: int,
    ) -> None:
        super().__init__(config)
        self.taken = taken
        self.deaf = deaf
        self.supervisor = supervisor
        self.orphaned: asyncio.Task[None] | None = None

    async def startup(self, sockets: list[socket.socket] | None = None) -> None:
        await super().startup(sockets=sockets)
        if self.started:
            self.taken()
            self.orphaned = asyncio.create_task(self._abandoned())

    async def _abandoned(self) -> None:
        """S'arrête si le superviseur disparaît sans avoir pu arrêter ses process (``kill -9``).

        Sans lui, personne ne relancera ni n'arrêtera ce process, qui garderait
        la socket — et le port, qu'un nouveau ``loom serve`` ne pourrait prendre.
        """
        while not self.should_exit:
            await asyncio.sleep(ORPHANED_EVERY)
            if os.getppid() != self.supervisor:
                print(
                    "Le superviseur a disparu : ce process s'arrête.", file=sys.stderr, flush=True
                )
                self.should_exit = True

    async def shutdown(self, sockets: list[socket.socket] | None = None) -> None:
        # Cesser d'écouter d'abord, comme uvicorn le fait (le refaire ne coûte
        # rien), et le dire : le superviseur attend ça pour passer la main.
        for server in self.servers:
            server.close()
        self.deaf()
        await super().shutdown(sockets=sockets)


async def _mounted(loom: Loom) -> None:
    """Monte chaque agent pour chaque client, sans appeler aucun modèle.

    Ce que le montage refuse — un outil, une politique ou un juge introuvable,
    un serveur MCP mal déclaré — refuse la config avant qu'elle prenne la main.
    Les clients de modèle sont remplacés : une clé absente ne bloque pas le
    rechargement d'un agent qu'on ne lance pas.
    """
    config = loom.config
    for tenant_id in config.tenant_ids:
        tenant = loom.tenant(tenant_id)
        for agent in config.agents:
            if not tenant.allows(agent.name):
                continue
            built = build_agent(
                tenant.config,
                agent.name,
                InMemoryEventStore(),
                registry=loom.registry,
                tenant=tenant,
                models=_Unused,
            )
            await built.aclose()


class _Unused:
    """Client de modèle du montage d'essai : il n'est jamais appelé."""

    def __init__(self, spec: ModelSpec) -> None:
        self.spec = spec

    @property
    def provider(self) -> str:
        return self.spec.sdk

    async def stream(self, request: ModelRequest) -> AsyncGenerator[ModelChunk]:
        raise RuntimeError(f"Montage d'essai : le modèle {self.spec.id} n'est pas appelé")
        yield  # pragma: no cover — un générateur, pour respecter le protocole

    async def aclose(self) -> None:
        pass
