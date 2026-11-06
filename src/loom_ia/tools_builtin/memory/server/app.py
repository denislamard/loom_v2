# SPDX-License-Identifier: Apache-2.0
"""``loom-mem-gpu`` : BGE-M3 et son reranker chargés une fois, servis en HTTP sur la boucle locale.

Bibliothèque standard seulement (``http.server``) : un thread par requête,
les modèles étant synchrones. Chaque modèle calcule un lot à la fois (verrou
de ``LazyModel``) ; l'embedder et le reranker, en parallèle.

L'ordre du démarrage (``run``) :

1. réglages validés — sinon sortie 2 ;
2. port ouvert **avant** tout chargement — pris, sortie 3 sans rien charger :
   la VRAM n'est jamais occupée deux fois ;
3. le serveur répond aussitôt ; ``/v1/health`` dit ``loading`` (503) ;
4. les modèles se chargent dans un thread, avec leur passage d'essai — en
   échec, ``/v1/health`` dit ``failed`` (503) quelques secondes, puis sortie 4 ;
5. ``ready`` : les modèles ne sont plus jamais déchargés.

SIGTERM ou SIGINT : plus de nouvelle requête, celles en cours finissent (un
lot au plus), sortie 0. L'application ne garde rien : ni base, ni texte. Son
journal dit la requête, son code, le nombre de textes et la durée, jamais leur
contenu.
"""

import errno
import hmac
import importlib
import importlib.util
import json
import logging
import signal
import socket
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import FrameType
from typing import Any, Final, cast

from pydantic import BaseModel, ValidationError

from loom_ia import __version__
from loom_ia.tools_builtin.memory import bge_m3
from loom_ia.tools_builtin.memory.models import (
    Embedder,
    Embedding,
    ModelError,
    Reranker,
    first_line,
    resolve_device,
)
from loom_ia.tools_builtin.memory.protocol import (
    MAX_BODY_BYTES,
    MAX_CHARS,
    MAX_TEXTS,
    PROTOCOL,
    EmbedRequest,
    EmbedResponse,
    ErrorBody,
    Health,
    ScoreRequest,
    ScoreResponse,
    State,
    over_caps,
    to_item,
)
from loom_ia.tools_builtin.memory.server.settings import ServiceSettings, SettingsError

log = logging.getLogger("loom_ia.memory.server")

EXIT_OK: Final = 0
EXIT_SETTINGS: Final = 2
EXIT_PORT: Final = 3
EXIT_LOAD: Final = 4

# Requêtes servies à la fois ; au-delà, 503 et Retry-After.
QUEUE: Final = 32
# Délai de socket d'une requête, en secondes : un client muet ne garde pas de thread.
SOCKET_TIMEOUT: Final = 30.0
# Après un chargement raté, le temps laissé à ``status`` pour lire l'erreur, en secondes.
FAILED_GRACE: Final = 5.0
RETRY_AFTER: Final = "1"
# Ce que l'extra ``memory`` apporte, et que le chargement des vrais modèles importe.
MODULES: Final = ("torch", "transformers")


class _Reply(Exception):
    """Une réponse d'erreur : son code, son message, ses en-têtes."""

    def __init__(
        self,
        status: HTTPStatus,
        message: str,
        *,
        retry: bool = False,
        state: State | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        self.retry = retry
        self.state: State | None = state


# --- Les modèles servis --------------------------------------------------------------


@dataclass(slots=True)
class Host:
    """Les deux modèles servis, leur chargement et leur état.

    ``prepare`` tourne avant le chargement (plafond de VRAM) ; ``vram`` lit la
    mémoire de la carte réservée par le process, ou ``None`` hors carte.
    """

    embedder: Embedder
    reranker: Reranker
    algo: str
    fp16: bool
    prepare: Callable[[], None] | None = None
    vram: Callable[[], int | None] | None = None
    state: State = "loading"
    error: str | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def load(self) -> None:
        """Charge les deux modèles et les essaie ; l'échec est gardé dans ``state``."""
        try:
            if self.prepare is not None:
                self.prepare()
            self.embedder.load()
            self.reranker.load()
        except ModelError as exc:
            self._fail(str(exc))
            return
        except Exception as exc:
            self._fail(f"chargement impossible : {first_line(exc)}")
            return
        with self._lock:
            self.state = "ready"
        used = self.vram_used_mb()
        held = f", {used} Mo de VRAM réservés" if used is not None else ""
        log.info(
            "prêt : %s et %s sur %s%s",
            self.embedder.model_name,
            self.reranker.model_name,
            self.embedder.device,
            held,
        )

    def _fail(self, message: str) -> None:
        with self._lock:
            self.state = "failed"
            self.error = message
        log.error("modèles non chargés : %s", message)

    def vram_used_mb(self) -> int | None:
        if self.vram is None:
            return None
        try:
            return self.vram()
        except Exception:
            return None

    def health(self) -> Health:
        with self._lock:
            state, error = self.state, self.error
        if state != "ready":
            return Health(protocol=PROTOCOL, state=state, error=error)
        device = self.embedder.device
        return Health(
            protocol=PROTOCOL,
            state=state,
            loom=__version__,
            algo=self.algo,
            embedder=self.embedder.model_name,
            reranker=self.reranker.model_name,
            dim=self.embedder.dim,
            device=device,
            fp16=device is not None and bge_m3.half_precision(self.fp16, device),
            vram_used_mb=self.vram_used_mb(),
            max_texts=MAX_TEXTS,
            max_chars=MAX_CHARS,
        )

    def embed(self, texts: Sequence[str]) -> list[Embedding]:
        return self.embedder.embed_documents(texts)

    def score(self, query: str, passages: Sequence[str]) -> list[float]:
        return self.reranker.score(query, passages)


def bge_host(settings: ServiceSettings) -> Host:
    """BGE-M3 et son reranker, avec les réglages de l'application ; rien n'est chargé ici."""
    spec = settings.models()
    fraction = settings.max_vram_fraction

    def prepare() -> None:
        if fraction is None:
            return
        device = resolve_device(spec.device)
        torch: Any = importlib.import_module("torch")
        torch.cuda.set_per_process_memory_fraction(fraction, _index(device))
        log.info("VRAM plafonnée à %.0f %% de la carte %s", fraction * 100, device)

    def vram() -> int | None:
        device = resolve_device(spec.device)
        if not device.startswith("cuda"):
            return None
        torch: Any = importlib.import_module("torch")
        return int(torch.cuda.memory_reserved(_index(device))) // (1024 * 1024)

    return Host(
        embedder=bge_m3.BgeM3Embedder(spec),
        reranker=bge_m3.BgeReranker(spec),
        algo=bge_m3.ALGO,
        fp16=spec.fp16,
        prepare=prepare,
        vram=vram,
    )


def _index(device: str) -> int:
    _, _, number = device.partition(":")
    return int(number) if number else 0


# --- HTTP ----------------------------------------------------------------------------


class ModelServer(ThreadingHTTPServer):
    """Le serveur : un thread par requête ; à l'arrêt, les requêtes en cours finissent."""

    daemon_threads = False
    block_on_close = True
    # Deux instances sur un même port : la seconde doit échouer, pas partager le port.
    allow_reuse_port = False

    def __init__(self, address: tuple[str, int], host: Host, token: str | None) -> None:
        if ":" in address[0]:
            self.address_family = socket.AF_INET6
        self.host = host
        self.token = token
        self.slots = threading.BoundedSemaphore(QUEUE)
        super().__init__(address, _Handler)


class _Handler(BaseHTTPRequestHandler):
    timeout = SOCKET_TIMEOUT
    server_version = "loom-mem-gpu"
    sys_version = ""

    @property
    def models(self) -> ModelServer:
        return cast(ModelServer, self.server)

    def log_message(self, format: str, *args: Any) -> None:
        # Le journal d'accès est écrit par ``_respond`` : sans adresse ni contenu.
        pass

    def do_GET(self) -> None:
        started = time.monotonic()
        try:
            self._authorize()
            if self._path() != "/v1/health":
                raise _Reply(HTTPStatus.NOT_FOUND, f"{self._path()} : point d'accès inconnu")
            health = self.models.host.health()
            status = HTTPStatus.OK if health.state == "ready" else HTTPStatus.SERVICE_UNAVAILABLE
            self._respond(
                status, health, started, retry=health.state == "loading", exclude_none=True
            )
        except _Reply as reply:
            self._refuse(reply, started)

    def do_POST(self) -> None:
        started = time.monotonic()
        routes: dict[str, Callable[[bytes], tuple[BaseModel, int]]] = {
            "/v1/embed": self._embed,
            "/v1/score": self._score,
        }
        try:
            self._authorize()
            route = routes.get(self._path())
            if route is None:
                raise _Reply(HTTPStatus.NOT_FOUND, f"{self._path()} : point d'accès inconnu")
            self._ready()
            body = self._body()
            if not self.models.slots.acquire(blocking=False):
                raise _Reply(
                    HTTPStatus.SERVICE_UNAVAILABLE, "file d'attente pleine : réessayer", retry=True
                )
            try:
                payload, count = route(body)
            finally:
                self.models.slots.release()
            self._respond(HTTPStatus.OK, payload, started, count=count)
        except _Reply as reply:
            self._refuse(reply, started)

    # --- Routes ----------------------------------------------------------------------

    def _embed(self, body: bytes) -> tuple[BaseModel, int]:
        request = _parse(EmbedRequest, body)
        problem = over_caps(request.texts)
        if problem is not None:
            raise _Reply(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, problem)
        found = _compute(lambda: self.models.host.embed(request.texts))
        return EmbedResponse(items=[to_item(e) for e in found]), len(request.texts)

    def _score(self, body: bytes) -> tuple[BaseModel, int]:
        request = _parse(ScoreRequest, body)
        problem = over_caps([request.query], "question") or over_caps(request.passages, "passages")
        if problem is not None:
            raise _Reply(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, problem)
        scores = _compute(lambda: self.models.host.score(request.query, request.passages))
        return ScoreResponse(scores=scores), len(request.passages)

    # --- Contrôles -------------------------------------------------------------------

    def _path(self) -> str:
        return self.path.split("?", 1)[0]

    def _authorize(self) -> None:
        expected = self.models.token
        if expected is None:
            return
        given = self.headers.get("Authorization", "")
        scheme, _, value = given.partition(" ")
        # Comparaison en temps constant : la durée ne dit rien du jeton.
        if scheme.lower() != "bearer" or not hmac.compare_digest(
            value.strip().encode(), expected.encode()
        ):
            raise _Reply(HTTPStatus.UNAUTHORIZED, "jeton absent ou refusé")

    def _ready(self) -> None:
        health = self.models.host.health()
        if health.state == "loading":
            raise _Reply(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "modèles en cours de chargement",
                retry=True,
                state="loading",
            )
        if health.state == "failed":
            raise _Reply(
                HTTPStatus.SERVICE_UNAVAILABLE,
                f"modèles non chargés : {health.error}",
                state="failed",
            )

    def _body(self) -> bytes:
        """Le corps, lu seulement si ``Content-Length`` est donné et sous le plafond."""
        given = self.headers.get("Content-Length")
        if given is None:
            raise _Reply(HTTPStatus.BAD_REQUEST, "Content-Length absent")
        try:
            length = int(given)
        except ValueError:
            raise _Reply(HTTPStatus.BAD_REQUEST, "Content-Length illisible") from None
        if length < 0:
            raise _Reply(HTTPStatus.BAD_REQUEST, "Content-Length illisible")
        if length > MAX_BODY_BYTES:
            raise _Reply(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                f"corps de {length} octets : au plus {MAX_BODY_BYTES}",
            )
        return self.rfile.read(length)

    # --- Réponses --------------------------------------------------------------------

    def _refuse(self, reply: _Reply, started: float) -> None:
        body = ErrorBody(error=reply.message, state=reply.state)
        self._respond(reply.status, body, started, retry=reply.retry, exclude_none=True)

    def _respond(
        self,
        status: HTTPStatus,
        payload: BaseModel,
        started: float,
        *,
        count: int = 0,
        retry: bool = False,
        exclude_none: bool = False,
    ) -> None:
        data = payload.model_dump_json(exclude_none=exclude_none).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        if retry:
            self.send_header("Retry-After", RETRY_AFTER)
        self.end_headers()
        self.wfile.write(data)
        took = (time.monotonic() - started) * 1000
        log.info("%s %s %d %d texte(s) %.0f ms", self.command, self._path(), status, count, took)


def _parse[M: BaseModel](model: type[M], body: bytes) -> M:
    try:
        data: object = json.loads(body)
    except ValueError as exc:
        raise _Reply(HTTPStatus.BAD_REQUEST, f"JSON illisible : {exc}") from None
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        raise _Reply(HTTPStatus.UNPROCESSABLE_ENTITY, _invalid(exc)) from None


def _invalid(exc: ValidationError) -> str:
    said = [
        f"{'.'.join(str(p) for p in error['loc']) or 'corps'} : {error['msg']}"
        for error in exc.errors()[:5]
    ]
    return "champs invalides — " + " ; ".join(said)


def _compute[R](fn: Callable[[], R]) -> R:
    try:
        return fn()
    except ModelError as exc:
        raise _Reply(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc)) from None
    except Exception as exc:
        log.exception("calcul impossible")
        raise _Reply(
            HTTPStatus.INTERNAL_SERVER_ERROR, f"calcul impossible : {first_line(exc)}"
        ) from None


# --- Le service ----------------------------------------------------------------------


class PortTaken(Exception):
    """Le port est déjà pris : une autre instance tourne, ou un autre programme."""


class Service:
    """Le serveur et ses modèles : ouvert, chargé, servi, arrêté."""

    def __init__(self, host: Host, token: str | None) -> None:
        self.host = host
        self.token = token
        self.exit_code = EXIT_OK
        self._server: ModelServer | None = None
        self._stopping = threading.Event()

    @property
    def server(self) -> ModelServer:
        return self._require()

    @property
    def address(self) -> tuple[str, int]:
        server = self._require()
        found = cast(tuple[Any, ...], server.server_address)
        return str(found[0]), int(found[1])

    def bind(self, host: str, port: int) -> None:
        """Ouvre le port ; ``PortTaken`` s'il est déjà pris. Rien n'est chargé."""
        try:
            self._server = ModelServer((host, port), self.host, self.token)
        except OSError as exc:
            if exc.errno == errno.EADDRINUSE:
                raise PortTaken(f"{host}:{port} déjà pris") from exc
            raise

    def start_loading(self) -> threading.Thread:
        """Charge les modèles dans un thread ; en échec, arrête le service après un délai."""

        def load() -> None:
            self.host.load()
            if self.host.state == "failed" and not self._stopping.wait(FAILED_GRACE):
                self.stop(EXIT_LOAD)

        loading = threading.Thread(target=load, name="loom-mem-gpu-chargement", daemon=True)
        loading.start()
        return loading

    def serve_forever(self) -> None:
        self._require().serve_forever(poll_interval=0.2)

    def stop(self, code: int = EXIT_OK) -> None:
        """Demande l'arrêt ; sans attendre (appelable depuis un gestionnaire de signal)."""
        if self._stopping.is_set():
            return
        self._stopping.set()
        self.exit_code = code
        server = self._require()
        threading.Thread(target=server.shutdown, name="loom-mem-gpu-arret", daemon=True).start()

    def close(self) -> None:
        """Ferme le port, une fois les requêtes en cours finies."""
        if self._server is not None:
            self._server.server_close()

    def _require(self) -> ModelServer:
        if self._server is None:
            raise RuntimeError("service non ouvert : appeler bind")
        return self._server


def missing_modules() -> list[str]:
    """Les paquets de l'extra ``memory`` absents de ce Python."""
    return [name for name in MODULES if importlib.util.find_spec(name) is None]


def preflight(settings: ServiceSettings) -> str | None:
    """Ce qui empêcherait de démarrer, vu sans rien charger ni ouvrir ; ``None`` si rien.

    Le jeton (``token_env``) doit être lu, et l'extra ``memory`` installé. ``run`` le
    vérifie avant d'ouvrir le port, ``serve --check`` (l'aide à l'installation) aussi.
    """
    try:
        settings.token()
    except SettingsError as exc:
        return str(exc)
    missing = missing_modules()
    if missing:
        return (
            f"dépendance(s) absente(s) : {', '.join(missing)} — installer l'extra : loom-ia[memory]"
        )
    return None


def run(settings: ServiceSettings) -> int:
    """Lance l'application jusqu'à son arrêt ; rend le code de sortie."""
    problem = preflight(settings)
    if problem is not None:
        log.error("%s", problem)
        return EXIT_SETTINGS
    service = Service(bge_host(settings), settings.token())
    try:
        service.bind(settings.host, settings.port)
    except PortTaken as exc:
        log.error("%s : une autre instance tourne peut-être ; rien n'est chargé", exc)
        return EXIT_PORT
    host, port = service.address
    log.info(
        "loom-mem-gpu %s à l'écoute sur %s:%d, chargement des modèles…", __version__, host, port
    )

    def on_signal(number: int, _frame: FrameType | None) -> None:
        log.info("signal %s : arrêt après les requêtes en cours", signal.Signals(number).name)
        service.stop(EXIT_OK)

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)
    service.start_loading()
    try:
        service.serve_forever()
    finally:
        service.close()
    log.info("arrêt (code %d)", service.exit_code)
    return service.exit_code
