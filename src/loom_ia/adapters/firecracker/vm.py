# SPDX-License-Identifier: Apache-2.0
"""Une microVM Firecracker construite par ``make_vm.sh``, vue de l'hôte.

Portage de l'ancien client de la plateforme (``vm_client.py``, resté hors du
dépôt), dans sa version qui laisse le verrou d'instance à ``run.sh``
(``OLD/source_python``) : c'est la seule qui
s'accorde avec le ``run.sh`` actuel, qui prend lui-même ``flock -n`` sur
``runtime/vm.lock`` — un verrou déjà pris côté Python le lui ferait refuser.

Ce que ce module garde de la plateforme, et pourquoi :

- les chemins de sockets sont **lus** dans ``vm.env``, jamais recomposés :
  sous jailer ils passent dans le chroot ;
- l'état « lancée » se lit au verrou ``flock`` de ``run.sh``, que le noyau
  relâche à la mort du VMM quelle qu'en soit la cause ;
- la VM est lancée par ``subprocess.Popen`` et non par un sous-process
  asyncio : elle doit pouvoir survivre à son lanceur, et un transport asyncio
  se plaindrait de sa boucle fermée ;
- l'arrêt essaie ``reboot`` sur la console série, puis Ctrl+Alt+Suppr, puis
  SIGTERM et SIGKILL, et dit lequel a abouti.

Laissé de côté parce que loom n'en a pas l'usage : la découverte d'un dossier
de VMs, pause et reprise, le sens invité → hôte du vsock.
"""

import asyncio
import contextlib
import fcntl
import json
import os
import signal
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

__all__ = ["Stopped", "Vm", "VmError", "VsockRefused"]

# La manière dont la VM s'est arrêtée. « console » et « acpi » laissent
# systemd démonter /data et vider ses caches ; les deux autres coupent net.
type Stopped = Literal["console", "acpi", "sigterm", "sigkill"]

# Lancements faits par ce processus, par dossier de VM : leur tube stdin est
# la console série, et les garder ici permet de récolter les zombies (une VM
# est un fils qu'on n'attend jamais). Indexés par dossier et non par le PID du
# verrou : un run.sh qui perd la course rouvre ce fichier avec « > » et
# l'efface, le PID qu'il portait est alors perdu.
_SPAWNED: dict[Path, list[subprocess.Popen[bytes]]] = {}


class VmError(RuntimeError):
    """Échec côté VM : dossier invalide, API en erreur, démarrage ou arrêt impossible."""


class VsockRefused(VmError):
    """Personne n'écoute sur ce port vsock dans l'invité."""


def _parse_env(path: Path) -> dict[str, str]:
    """Lit un ``vm.env`` (``CLÉ="valeur"``) sans l'exécuter."""
    env: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip().strip('"')
    return env


@dataclass(frozen=True, slots=True)
class Vm:
    """Poignée sur un dossier de VM : ``vm.env``, ``run.sh``, ``runtime/``."""

    name: str
    cid: int
    api_sock: Path
    vsock_uds: Path
    directory: Path
    # Port vsock d'execd dans l'invité, tel que vm.env le donne ; None s'il n'y est pas.
    execd_port: int | None
    # Dossier source d'execd retenu à la construction ; vide = image sans execd.
    execd_src: str

    # ------------------------------------------------------------------ #
    # Chargement
    # ------------------------------------------------------------------ #

    @classmethod
    def load(cls, vm_dir: str | os.PathLike[str]) -> Vm:
        directory = Path(vm_dir).expanduser().resolve()
        env_file = directory / "vm.env"
        if not env_file.is_file():
            raise VmError(f"{env_file} introuvable — dossier de VM invalide")
        env = _parse_env(env_file)
        try:
            port = env.get("EXECD_PORT", "")
            return cls(
                name=env["VM_NAME"],
                cid=int(env["GUEST_CID"]),
                api_sock=Path(env["API_SOCK"]),
                vsock_uds=Path(env["VSOCK_UDS"]),
                directory=directory,
                execd_port=int(port) if port else None,
                execd_src=env.get("EXECD_SRC", ""),
            )
        except KeyError as exc:
            raise VmError(
                f"{env_file} : clé {exc} absente — reconstruis la VM avec make_vm.sh"
            ) from exc
        except ValueError as exc:
            raise VmError(f"{env_file} : valeur illisible — {exc}") from exc

    # ------------------------------------------------------------------ #
    # État
    # ------------------------------------------------------------------ #

    @property
    def lock_path(self) -> Path:
        return self.directory / "runtime" / "vm.lock"

    @property
    def console_log(self) -> Path:
        return self.directory / "runtime" / "console.log"

    def is_running(self) -> bool:
        """Le verrou de ``run.sh`` est-il tenu ?

        Un fichier de verrou absent veut dire qu'aucun ``run.sh`` ne l'a
        jamais ouvert : rien ne tourne, et on ne le crée pas pour le savoir.
        """
        self._reap()
        try:
            fd = os.open(self.lock_path, os.O_RDWR)
        except FileNotFoundError:
            return False
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        else:
            # Personne ne le tenait : relâché aussitôt, pour ne pas gêner un
            # démarrage légitime.
            fcntl.flock(fd, fcntl.LOCK_UN)
            return False
        finally:
            os.close(fd)

    @property
    def pid(self) -> int | None:
        """PID inscrit par ``run.sh`` dans le verrou ; ne vaut que si la VM tourne."""
        with contextlib.suppress(OSError, ValueError):
            return int(self.lock_path.read_text().strip())
        return None

    async def is_alive(self) -> bool:
        """Un VMM répond-il sur le socket d'API, verrou tenu ou non ?"""
        with contextlib.suppress(OSError, VmError, TimeoutError):
            await self.api("GET", "/", wait=1.0)
            return True
        return False

    # ------------------------------------------------------------------ #
    # Démarrage
    # ------------------------------------------------------------------ #

    async def start(self, *, wait: float = 15.0, console: bool = True) -> None:
        """Lance la VM par ``run.sh`` et attend que son API réponde.

        L'API répond dès que le VMM tourne, bien avant que l'invité ait
        démarré : c'est ``connect`` avec ``wait`` qui attend le service.
        ``VmError`` si elle tourne déjà, ou si un VMM répond sans tenir le
        verrou (lancé hors de ``run.sh`` : en démarrer un second mettrait deux
        écrivains sur les mêmes images).
        """
        if self.is_running():
            raise VmError(f"{self.name} tourne déjà (pid {self.pid})")
        if await self.is_alive():
            raise VmError(
                f"{self.name} : un VMM répond sur {self.api_sock} sans tenir le verrou — "
                "arrête-le (pgrep -af firecracker), puis régénère run.sh avec make_vm.sh"
            )
        proc = self._spawn(console)
        await self._wait_ready(proc, wait=wait)

    def _spawn(self, console: bool) -> subprocess.Popen[bytes]:
        # stdin reste un tube : c'est la console série de l'invité, par où
        # passe l'arrêt « console ». /dev/null y mettrait un EOF immédiat, et
        # le getty de l'autologin bouclerait.
        self.console_log.parent.mkdir(parents=True, exist_ok=True)
        sink = open(self.console_log, "ab", buffering=0) if console else None
        try:
            proc = subprocess.Popen(
                [str(self.directory / "run.sh")],
                cwd=self.directory,
                stdin=subprocess.PIPE,
                stdout=sink if sink is not None else subprocess.DEVNULL,
                stderr=subprocess.STDOUT,
                # Hors du groupe du terminal : un Ctrl-C sur le lanceur
                # n'emporte pas la VM.
                start_new_session=True,
            )
        finally:
            if sink is not None:
                sink.close()
        _SPAWNED.setdefault(self.directory, []).append(proc)
        return proc

    def _reap(self) -> None:
        for directory, launched in list(_SPAWNED.items()):
            alive = [proc for proc in launched if proc.poll() is None]
            if alive:
                _SPAWNED[directory] = alive
            else:
                del _SPAWNED[directory]

    def _launched(self) -> subprocess.Popen[bytes] | None:
        """Le VMM vivant que ce processus a lancé pour ce dossier, s'il y en a un."""
        self._reap()
        launched = _SPAWNED.get(self.directory, [])
        return launched[-1] if launched else None

    async def _wait_ready(self, proc: subprocess.Popen[bytes], *, wait: float) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + wait
        while True:
            if (code := proc.poll()) is not None:
                # run.sh sort en échec s'il n'obtient pas le verrou : si une VM
                # tourne à cet instant, un démarrage concurrent a gagné.
                if self.is_running():
                    raise VmError(f"{self.name} tourne déjà (pid {self.pid})")
                raise VmError(
                    f"{self.name} : firecracker s'est arrêté (code {code})\n{self._console_tail()}"
                )
            # Le verrou prouve que c'est bien ce lancement qui répond, et non
            # un socket résiduel servi par un ancien VMM.
            if self.is_running():
                with contextlib.suppress(OSError, VmError, TimeoutError):
                    await self.api("GET", "/machine-config", wait=1.0)
                    return
            if loop.time() >= deadline:
                raise VmError(f"{self.name} : API muette après {wait} s\n{self._console_tail()}")
            await asyncio.sleep(0.02)

    async def ensure_started(self, *, wait: float = 15.0, console: bool = True) -> bool:
        """Démarre seulement si nécessaire ; True si ce sont nous qui l'avons démarrée.

        Deux lanceurs simultanés n'obtiennent pas deux VMs : le second perd le
        verrou de ``run.sh``, puis attend que l'API réponde.
        """
        if not self.is_running():
            try:
                await self.start(wait=wait, console=console)
            except VmError as exc:
                if "tourne déjà" not in str(exc):
                    raise
            else:
                return True
        await self._wait_api(wait=wait)
        return False

    async def _wait_api(self, *, wait: float) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + wait
        while True:
            if self.is_running():
                with contextlib.suppress(OSError, VmError, TimeoutError):
                    await self.api("GET", "/machine-config", wait=1.0)
                    return
            if loop.time() >= deadline:
                raise VmError(f"{self.name} : tourne mais API muette après {wait} s")
            await asyncio.sleep(0.02)

    def _console_tail(self, lines: int = 15) -> str:
        with contextlib.suppress(OSError):
            tail = self.console_log.read_text(errors="replace").splitlines()[-lines:]
            return "  console: " + "\n  console: ".join(tail)
        return "  (pas de console.log)"

    # ------------------------------------------------------------------ #
    # Arrêt
    # ------------------------------------------------------------------ #

    def console_shutdown(self) -> bool:
        """Tape ``reboot`` sur la console série ; False si ce n'est pas nous qui la tenons.

        ``reboot`` et non ``poweroff`` : avec ``reboot=k``, le RESET de
        l'invité termine le processus firecracker ; un ``poweroff`` figerait
        l'invité sans rendre la main.
        """
        proc = self._launched()
        if proc is None or proc.stdin is None:
            return False
        try:
            proc.stdin.write(b"\nreboot\n")
            proc.stdin.flush()
        except OSError, ValueError:
            return False
        return True

    async def shutdown(self) -> None:
        """Ctrl+Alt+Suppr par l'API, que systemd traduit en extinction (x86 seulement)."""
        await self.api("PUT", "/actions", {"action_type": "SendCtrlAltDel"})

    async def stop(self, *, grace: float = 10.0, wait: float = 10.0) -> Stopped | None:
        """Éteint la VM et rend la manière qui a abouti ; None si rien ne tournait.

        ``grace`` est laissé à chaque extinction propre (console, puis
        Ctrl+Alt+Suppr), ``wait`` à chaque signal (SIGTERM, puis SIGKILL).
        """
        if not self.is_running() and not await self.is_alive():
            return None
        if self.console_shutdown() and await self._wait_gone(grace):
            return "console"
        with contextlib.suppress(OSError, VmError, TimeoutError):
            await self.shutdown()
        if await self._wait_gone(grace):
            return "acpi"
        launched = self._launched()
        pid = self.pid or (launched.pid if launched is not None else None)
        if pid is None:
            raise VmError(f"{self.name} : toujours vivante après {grace} s et PID inconnu")
        steps: tuple[tuple[signal.Signals, Stopped], ...] = (
            (signal.SIGTERM, "sigterm"),
            (signal.SIGKILL, "sigkill"),
        )
        for sig, how in steps:
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, sig)
            if await self._wait_gone(wait):
                return how
        raise VmError(f"{self.name} : impossible d'arrêter le pid {pid}")

    async def _wait_gone(self, wait: float) -> bool:
        """Disparue = verrou libre ET API muette."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + wait
        while True:
            if not self.is_running() and not await self.is_alive():
                return True
            if loop.time() >= deadline:
                return False
            await asyncio.sleep(0.05)

    # ------------------------------------------------------------------ #
    # API de contrôle — HTTP/1.1 sur socket Unix
    # ------------------------------------------------------------------ #

    async def api(
        self,
        method: str,
        path: str,
        body: dict[str, object] | None = None,
        *,
        wait: float = 5.0,
    ) -> object:
        """Une requête à l'API de Firecracker ; le JSON de la réponse, ou None.

        La réponse se lit par sa longueur et non jusqu'à EOF : Firecracker
        garde la connexion ouverte, un ``read()`` sans borne attendrait le délai.
        """
        async with asyncio.timeout(wait):
            reader, writer = await asyncio.open_unix_connection(self.api_sock)
            try:
                payload = b"" if body is None else json.dumps(body).encode()
                head = [
                    f"{method} {path} HTTP/1.1",
                    "Host: localhost",
                    "Accept: application/json",
                    "Connection: close",
                ]
                if payload:
                    head += ["Content-Type: application/json", f"Content-Length: {len(payload)}"]
                writer.write(("\r\n".join(head) + "\r\n\r\n").encode() + payload)
                await writer.drain()
                raw_head = await reader.readuntil(b"\r\n\r\n")
                lines = raw_head.decode("latin-1").split("\r\n")
                try:
                    status = int(lines[0].split()[1])
                except (IndexError, ValueError) as exc:
                    raise VmError(f"{method} {path} : réponse illisible {lines[0]!r}") from exc
                headers = {
                    key.strip().lower(): value.strip()
                    for key, _, value in (line.partition(":") for line in lines[1:] if line)
                }
                length = int(headers.get("content-length", "0") or 0)
                data = await reader.readexactly(length) if length else b""
            finally:
                writer.close()
                with contextlib.suppress(Exception):
                    await writer.wait_closed()
        if status >= 400:
            raise VmError(f"{method} {path} -> HTTP {status} : {data.decode(errors='replace')!r}")
        return cast(object, json.loads(data)) if data else None

    async def info(self) -> dict[str, object]:
        """``GET /`` : id, état (Not started, Running, Paused), version du VMM."""
        found = await self.api("GET", "/")
        if not isinstance(found, dict):
            raise VmError(f"GET / : objet JSON attendu, reçu {type(found).__name__}")
        return cast(dict[str, object], found)

    # ------------------------------------------------------------------ #
    # vsock, sens hôte -> invité
    # ------------------------------------------------------------------ #

    async def connect(
        self,
        port: int,
        *,
        wait: float = 0.0,
        retry_delay: float = 0.05,
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        """Un flux vers le service qui écoute sur ``port`` dans l'invité.

        Firecracker attend une ligne ``CONNECT <port>`` et répond
        ``OK <port_hôte>`` avant de s'effacer ; cette ligne est consommée ici.
        ``wait`` couvre le démarrage de l'invité : tant que rien n'écoute, on
        réessaie. Chaque tentative est bornée : une poignée de main sans
        réponse compte comme un refus.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + wait
        while True:
            try:
                reader, writer = await asyncio.open_unix_connection(self.vsock_uds)
            except (FileNotFoundError, ConnectionRefusedError) as exc:
                if loop.time() >= deadline:
                    raise VmError(
                        f"{self.name} : {self.vsock_uds} injoignable — VM arrêtée ?"
                    ) from exc
                await asyncio.sleep(retry_delay)
                continue
            greeting = b""
            with contextlib.suppress(OSError, TimeoutError):
                async with asyncio.timeout(max(1.0, min(5.0, deadline - loop.time()))):
                    writer.write(f"CONNECT {port}\n".encode())
                    await writer.drain()
                    greeting = await reader.readline()
            if greeting.startswith(b"OK "):
                return reader, writer
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()
            if loop.time() >= deadline:
                raise VsockRefused(f"{self.name} : personne n'écoute sur le port vsock {port}")
            await asyncio.sleep(retry_delay)
