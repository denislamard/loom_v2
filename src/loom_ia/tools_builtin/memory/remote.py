# SPDX-License-Identifier: Apache-2.0
"""Les modèles de la mémoire servis par ``loom-mem-gpu`` : le client, côté loom.

``RemoteEmbedder`` et ``RemoteReranker`` ont l'API de ``BgeM3Embedder`` et
``BgeReranker`` : ``MemoryService`` ne voit pas la différence. Ils partagent
une ``Connection`` : un client HTTP et ce que l'application a dit d'elle.

- **Premier contact**, une fois par process : ``/v1/health``. L'application
  doit annoncer le protocole, l'algorithme, les modèles et la dimension
  attendus ; sinon ``ModelLoadError``, comme pour une base indexée par un
  autre modèle. Encore en chargement (503 ``loading``), elle est attendue au
  plus ``READY_WAIT`` secondes.
- **Calcul** : une requête par lot de ``BATCH`` textes ; ``cancel`` est
  regardé entre deux lots, comme en local.
- **Erreurs** : injoignable → ``ServerUnavailable`` (une ``ModelLoadError``)
  qui dit comment la démarrer ; file pleine (503) → nouvel essai après
  ``Retry-After`` ; 500 → ``ModelRunError``. Après une coupure, le premier
  contact est refait : une application relancée peut ne plus être la même
  version.
- **Repli** (``server.fallback``) : quand l'application est indisponible —
  injoignable, encore en chargement après ``READY_WAIT``, ou sans modèles —,
  le lot est calculé dans ce process par les modèles locaux, chargés au
  premier repli. Une ligne d'avertissement, une seule fois ; l'application
  est ressayée au premier lot après ``REPROBE`` secondes, et reprend la main
  dès qu'elle répond. Un jeton refusé, des modèles qui ne sont pas les bons,
  un calcul en échec ne se replient pas : ce sont des erreurs, pas une panne.

Sans repli, torch n'est jamais importé : un poste sans l'extra ``memory``
calcule par l'application. Le client ignore les proxys de l'environnement (``trust_env``) :
la boucle locale ne passe pas par eux.
"""

import logging
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Final

import httpx2
from pydantic import BaseModel, ValidationError

from loom_ia.tools_builtin.memory import bge_m3
from loom_ia.tools_builtin.memory.models import (
    Embedding,
    ModelLoadError,
    ModelRunError,
    Models,
    checkpoint,
    first_line,
    shared_models,
)
from loom_ia.tools_builtin.memory.protocol import (
    APP,
    PROTOCOL,
    EmbedRequest,
    EmbedResponse,
    ErrorBody,
    Health,
    ScoreRequest,
    ScoreResponse,
    from_item,
)
from loom_ia.tools_builtin.memory_params import ModelsSpec

log = logging.getLogger("loom_ia.memory")

# Après un repli, l'application est ressayée au premier lot passé ce délai, en secondes.
REPROBE: Final = 30.0
# Attente d'une application en cours de chargement, en secondes, et pas de la sonde.
READY_WAIT: Final = 60.0
READY_POLL: Final = 1.0
# Attente maximale d'un 503 « file pleine » avant un nouvel essai, en secondes.
BUSY_PAUSE_MAX: Final = 2.0
# Tranche d'attente : un appel annulé n'attend pas plus.
_TICK: Final = 0.1


@dataclass(frozen=True, slots=True)
class Target:
    """Où joindre l'application, une fois ``url_env`` et ``token_env`` lus."""

    url: str
    token: str | None = field(default=None, repr=False)
    connect_timeout: float = 2.0
    read_timeout: float = 60.0


class _Unreachable(Exception):
    """L'application ne répond pas (connexion refusée, délai, coupure)."""


class ServerUnavailable(ModelLoadError):
    """L'application ne peut pas calculer : injoignable, en chargement trop long, sans modèles.

    Le seul cas qui déclenche le repli.
    """


class Connection:
    """Le client HTTP de l'application et ce qu'elle a dit d'elle, partagés par les deux modèles."""

    def __init__(self, target: Target, spec: ModelsSpec) -> None:
        self.target = target
        self._spec = spec
        self._lock = threading.Lock()
        self._client: httpx2.Client | None = None
        self._health: Health | None = None
        # Le repli : ses modèles (construits au premier repli), et jusqu'à quand l'application
        # est tenue pour absente (``None`` : elle calcule).
        self._fallback = spec.fallback()
        self._local: Models | None = None
        self._down_until: float | None = None

    @property
    def device(self) -> str | None:
        """Où calcule le prochain lot : le device annoncé par l'application, ou celui du repli."""
        if self.served:
            health = self._health
            return None if health is None else health.device
        return None if self._local is None else self._local.embedder.device

    @property
    def served(self) -> bool:
        """Vrai si le prochain lot ira à l'application (pas de repli en cours)."""
        with self._lock:
            return self._down_until is None or time.monotonic() >= self._down_until

    @property
    def local(self) -> Models:
        """Les modèles du repli ; ``ValueError`` sans ``server.fallback``."""
        if self._fallback is None:
            raise ValueError("models.server : pas de repli (server.fallback)")
        with self._lock:
            if self._local is None:
                self._local = shared_models(self._fallback)
            return self._local

    def run[R](self, remote: Callable[[], R], local: Callable[[Models], R]) -> R:
        """Un lot : par l'application, ou dans ce process quand elle est indisponible."""
        if self._fallback is None:
            return remote()
        if not self.served:
            return local(self.local)
        try:
            found = remote()
        except ServerUnavailable as exc:
            self._went_down(exc)
            return local(self.local)
        self._came_back()
        return found

    def ensure(self, cancel: threading.Event | None = None) -> Health:
        """Le premier contact, fait une fois : l'application est-elle celle qu'on attend ?"""
        health = self._health
        if health is not None:
            return health
        deadline = time.monotonic() + READY_WAIT
        while True:
            checkpoint(cancel)
            found = self._fetch_health()
            if found.state == "ready":
                break
            if found.state == "failed":
                raise ServerUnavailable(
                    f"{APP} ({self.target.url}) n'a pas chargé ses modèles : {found.error}"
                )
            if time.monotonic() >= deadline:
                raise ServerUnavailable(
                    f"{APP} ({self.target.url}) charge encore ses modèles après "
                    f"{READY_WAIT:.0f} s : réessayer, ou lire son journal "
                    "(journalctl --user -u loom-mem-gpu)"
                )
            _sleep(READY_POLL, cancel)
        self._check(found)
        self._health = found
        return found

    def post[R: BaseModel](
        self,
        path: str,
        payload: BaseModel,
        answer: type[R],
        cancel: threading.Event | None = None,
    ) -> R:
        """Une requête de calcul ; réessaie une file pleine, refait le contact après une coupure."""
        self.ensure(cancel)
        deadline = time.monotonic() + self.target.read_timeout
        while True:
            try:
                response = self._send("POST", path, payload.model_dump_json().encode())
            except _Unreachable as exc:
                self._health = None
                raise self._unreachable(exc) from exc
            if response.status_code == 200:
                try:
                    return answer.model_validate_json(response.content)
                except ValidationError as exc:
                    raise ModelRunError(
                        f"{APP} : réponse illisible à {path} ({first_line(exc)})"
                    ) from exc
            error = _error(response)
            message = error.error
            if response.status_code == 401:
                raise ModelLoadError(f"{APP} ({self.target.url}) : {message} (token_env)")
            if response.status_code == 503:
                if error.state is not None:
                    # Relancée entre-temps : le premier contact est à refaire.
                    self._health = None
                    self.ensure(cancel)
                    continue
                if time.monotonic() < deadline:
                    _sleep(_retry_after(response), cancel)
                    continue
            raise ModelRunError(f"{APP} : {message}")

    def close(self) -> None:
        with self._lock:
            if self._client is not None:
                self._client.close()
                self._client = None

    # --- Interne ---------------------------------------------------------------------

    def _went_down(self, exc: ServerUnavailable) -> None:
        with self._lock:
            first = self._down_until is None
            self._down_until = time.monotonic() + REPROBE
        if first:
            fallback = self._fallback
            assert fallback is not None
            log.warning(
                "%s ; calcul dans ce process (device %s) — %s ressayée dans %.0f s",
                exc,
                fallback.device,
                APP,
                REPROBE,
            )

    def _came_back(self) -> None:
        with self._lock:
            back = self._down_until is not None
            self._down_until = None
        if back:
            log.info("%s (%s) répond de nouveau : les calculs y retournent", APP, self.target.url)

    def _fetch_health(self) -> Health:
        try:
            response = self._send("GET", "/v1/health")
        except _Unreachable as exc:
            raise self._unreachable(exc) from exc
        if response.status_code == 401:
            message = _error(response).error
            raise ModelLoadError(f"{APP} ({self.target.url}) : {message} (token_env)")
        if response.status_code not in (200, 503):
            raise ModelLoadError(
                f"{APP} ({self.target.url}) : /v1/health a répondu {response.status_code}"
            )
        try:
            return Health.model_validate_json(response.content)
        except ValidationError as exc:
            raise ModelLoadError(
                f"{self.target.url} ne répond pas comme {APP} ({first_line(exc)})"
            ) from exc

    def _check(self, health: Health) -> None:
        """L'application doit calculer comme ce process le ferait : sinon, rien ne passe."""
        spec = self._spec
        expected = {
            "protocole": (health.protocol, PROTOCOL),
            "algorithme": (health.algo, bge_m3.ALGO),
            "embedder": (health.embedder, spec.embedder),
            "reranker": (health.reranker, spec.reranker),
            "dimension": (health.dim, bge_m3.DENSE_DIM),
        }
        for what, (served, wanted) in expected.items():
            if served != wanted:
                raise ModelLoadError(
                    f"{APP} ({self.target.url}) sert {what} {served!r}, la config attend "
                    f"{wanted!r} : aligner les modèles (models.embedder, models.reranker) ou "
                    f"les versions de loom ({APP} {health.loom})"
                )

    def _send(self, method: str, path: str, body: bytes | None = None) -> httpx2.Response:
        headers = {"Content-Type": "application/json"} if body is not None else {}
        if self.target.token is not None:
            headers["Authorization"] = f"Bearer {self.target.token}"
        try:
            return self._http().request(
                method, self.target.url.rstrip("/") + path, content=body, headers=headers
            )
        except httpx2.TransportError as exc:
            raise _Unreachable(first_line(exc) or type(exc).__name__) from exc

    def _http(self) -> httpx2.Client:
        with self._lock:
            if self._client is None:
                target = self.target
                self._client = httpx2.Client(
                    timeout=httpx2.Timeout(target.read_timeout, connect=target.connect_timeout),
                    trust_env=False,
                )
            return self._client

    def _unreachable(self, exc: Exception) -> ServerUnavailable:
        return ServerUnavailable(
            f"{APP} injoignable ({self.target.url} : {exc}) : le démarrer "
            f"(systemctl --user start {APP}, ou « {APP} » dans un terminal), ou ajouter "
            "models.server.fallback pour calculer dans loom"
        )


def _error(response: httpx2.Response) -> ErrorBody:
    """Le corps d'une réponse d'erreur ; un corps qui n'en est pas un dit le code HTTP."""
    try:
        return ErrorBody.model_validate_json(response.content)
    except ValidationError:
        return ErrorBody(error=f"HTTP {response.status_code}")


def _retry_after(response: httpx2.Response) -> float:
    try:
        return min(max(float(response.headers.get("Retry-After", "1")), _TICK), BUSY_PAUSE_MAX)
    except ValueError:
        return 1.0


def _sleep(seconds: float, cancel: threading.Event | None) -> None:
    """Attend ``seconds`` ; un appel annulé s'arrête à la tranche suivante."""
    end = time.monotonic() + seconds
    while (left := end - time.monotonic()) > 0:
        checkpoint(cancel)
        time.sleep(min(left, _TICK))
    checkpoint(cancel)


class RemoteEmbedder:
    """BGE-M3 calculé par ``loom-mem-gpu`` : dense et lexical."""

    def __init__(self, connection: Connection, spec: ModelsSpec) -> None:
        self.connection = connection
        self._spec = spec

    @property
    def model_name(self) -> str:
        return self._spec.embedder

    @property
    def dim(self) -> int:
        return bge_m3.DENSE_DIM

    @property
    def device(self) -> str | None:
        return self.connection.device

    def load(self) -> None:
        self.connection.run(self.connection.ensure, lambda local: local.embedder.load())

    def embed_documents(
        self, texts: Sequence[str], cancel: threading.Event | None = None
    ) -> list[Embedding]:
        found: list[Embedding] = []
        for batch in bge_m3.batches(list(texts)):
            checkpoint(cancel)
            found.extend(
                self.connection.run(
                    lambda batch=batch: self._remote(batch, cancel),
                    lambda local, batch=batch: local.embedder.embed_documents(batch, cancel),
                )
            )
        return found

    def _remote(self, batch: Sequence[str], cancel: threading.Event | None) -> list[Embedding]:
        # Coupés comme les modèles le font : l'application ne refuse jamais un texte long.
        texts = [bge_m3.clipped(text) for text in batch]
        answer = self.connection.post("/v1/embed", EmbedRequest(texts=texts), EmbedResponse, cancel)
        if len(answer.items) != len(batch):
            raise ModelRunError(f"{APP} : {len(answer.items)} vecteurs pour {len(batch)}")
        return _decoded(answer)

    def embed_query(self, text: str, cancel: threading.Event | None = None) -> Embedding:
        return self.embed_documents([text], cancel)[0]


class RemoteReranker:
    """Le reranker calculé par ``loom-mem-gpu`` : un score entre 0 et 1 par passage."""

    def __init__(self, connection: Connection, spec: ModelsSpec) -> None:
        self.connection = connection
        self._spec = spec

    @property
    def model_name(self) -> str:
        return self._spec.reranker

    @property
    def device(self) -> str | None:
        return self.connection.device

    def load(self) -> None:
        self.connection.run(self.connection.ensure, lambda local: local.reranker.load())

    def score(
        self, query: str, passages: Sequence[str], cancel: threading.Event | None = None
    ) -> list[float]:
        found: list[float] = []
        for batch in bge_m3.batches(list(passages)):
            checkpoint(cancel)
            found.extend(
                self.connection.run(
                    lambda batch=batch: self._remote(query, batch, cancel),
                    lambda local, batch=batch: local.reranker.score(query, batch, cancel),
                )
            )
        return found

    def _remote(
        self, query: str, batch: Sequence[str], cancel: threading.Event | None
    ) -> list[float]:
        request = ScoreRequest(
            query=bge_m3.clipped(query), passages=[bge_m3.clipped(p) for p in batch]
        )
        answer = self.connection.post("/v1/score", request, ScoreResponse, cancel)
        if len(answer.scores) != len(batch):
            raise ModelRunError(f"{APP} : {len(answer.scores)} scores pour {len(batch)}")
        return answer.scores


def _decoded(answer: EmbedResponse) -> list[Embedding]:
    try:
        return [from_item(item) for item in answer.items]
    except ValueError as exc:
        raise ModelRunError(f"{APP} : vecteur illisible ({exc})") from exc


def remote_models(target: Target, spec: ModelsSpec) -> tuple[RemoteEmbedder, RemoteReranker]:
    connection = Connection(target, spec)
    return RemoteEmbedder(connection, spec), RemoteReranker(connection, spec)
