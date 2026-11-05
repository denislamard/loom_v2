# SPDX-License-Identifier: Apache-2.0
"""Modèles de la mémoire : interfaces, chargement paresseux sous verrou, registre du process.

Les modèles sont synchrones : la mémoire les appelle dans un thread. Ils sont
lourds (BGE-M3 et son reranker font chacun ~568 M de paramètres) : un seul
exemplaire par process et par réglage, partagé par tous les agents et tous
les clients (``shared_models``). Le device se choisit pour le process, pas
pour un appel.

Un calcul s'abandonne entre deux lots (``cancel``) : quand l'appel qui
l'attendait est annulé — délai de l'outil dépassé, run arrêté —, le thread ne
finit pas un calcul dont personne ne lira le résultat, et rend le modèle aux
appels suivants. Un appel annulé pendant qu'il attend le modèle quitte la file
sans calculer.
"""

import functools
import importlib
import logging
import threading
import warnings
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final, Protocol

from loom_ia.tools_builtin.memory_params import MODELS_EXTRA, ModelsSpec

if TYPE_CHECKING:
    from loom_ia.tools_builtin.memory.remote import Target

log = logging.getLogger("loom_ia.memory")


@dataclass(frozen=True, slots=True)
class SparseVec:
    """Poids lexicaux : identifiants de tokens triés, et leurs poids (tous positifs)."""

    indices: list[int]
    values: list[float]


@dataclass(frozen=True, slots=True)
class Embedding:
    dense: list[float]
    sparse: SparseVec


class Embedder(Protocol):
    """Les vecteurs des textes. ``cancel`` levé, le calcul s'arrête au lot suivant."""

    @property
    def model_name(self) -> str: ...

    @property
    def dim(self) -> int: ...

    @property
    def device(self) -> str | None:
        """Le device des poids chargés ; ``None`` tant qu'ils ne le sont pas."""
        ...

    def load(self) -> None:
        """Charge les poids s'ils ne le sont pas : ce que fait le préchargement."""
        ...

    def embed_documents(
        self, texts: Sequence[str], cancel: threading.Event | None = None
    ) -> list[Embedding]: ...

    def embed_query(self, text: str, cancel: threading.Event | None = None) -> Embedding: ...


class Reranker(Protocol):
    @property
    def model_name(self) -> str: ...

    @property
    def device(self) -> str | None: ...

    def load(self) -> None: ...

    def score(
        self, query: str, passages: Sequence[str], cancel: threading.Event | None = None
    ) -> list[float]:
        """Un score par passage, entre 0 et 1."""
        ...


class ModelError(RuntimeError):
    """Un modèle n'a pas pu servir : message lisible, cause en une ligne."""


class ModelLoadError(ModelError):
    """Un modèle n'a pas pu être chargé, ou n'a pas tourné une première fois.

    Rien n'est gardé : l'appel suivant recharge le modèle.
    """


class ModelNotCached(Exception):
    """Un modèle lu hors ligne (``models.offline``) manque au cache local de Hugging Face."""


class ModelRunError(ModelError):
    """Un modèle chargé a échoué sur un calcul (mémoire du GPU saturée, par exemple).

    Le modèle est gardé : l'échec tient au calcul, pas au chargement.
    """


class ModelCancelled(Exception):
    """Calcul abandonné : l'appel qui l'attendait a été annulé (``cancel``).

    Pas une ``ModelError`` : personne n'attend plus le résultat, il n'y a rien à
    dire au modèle. Le modèle chargé est gardé.
    """


# Toutes les combien de secondes un appel qui attend le modèle regarde s'il est annulé.
_CANCEL_POLL: Final = 0.1


def checkpoint(cancel: threading.Event | None) -> None:
    """Lève ``ModelCancelled`` si le calcul a été abandonné : à appeler entre deux lots."""
    if cancel is not None and cancel.is_set():
        raise ModelCancelled


# --- Device ----------------------------------------------------------------------


class UnsupportedDevice(Exception):
    """Le device forcé (``models.device: cuda``) n'est pas utilisable par ce torch."""


@functools.cache
def resolve_device(device: str) -> str:
    """Résout ``auto`` en cuda, sinon mps, sinon cpu ; vérifie un ``cuda`` forcé.

    ``cuda`` n'est choisi que si torch a des noyaux pour la carte : un GPU vu par
    torch mais trop ancien pour la roue installée fait échouer les modèles plus
    loin, sans message utile. Avec ``auto``, il est écarté avec un avertissement ;
    forcé (``cuda``, ``cuda:N``), il lève ``UnsupportedDevice`` qui dit pourquoi,
    comme l'absence de GPU. torch n'est importé que pour ``auto`` et ``cuda``, au
    chargement des vrais modèles ; le résultat est gardé, l'avertissement n'est
    donc écrit qu'une fois.
    """
    if device.startswith("cuda"):
        _check_cuda(device)
        return device
    if device != "auto":
        return device
    torch: Any = importlib.import_module("torch")
    if torch.cuda.is_available():
        problem = unsupported_gpu(torch)
        if problem is None:
            return "cuda"
        log.warning("%s : modèles de la mémoire sur cpu (models.device pour forcer)", problem)
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def _check_cuda(device: str) -> None:
    torch: Any = importlib.import_module("torch")
    _, _, number = device.partition(":")
    index = int(number) if number else 0
    said = f"models.device {device} inutilisable"
    if not torch.cuda.is_available():
        raise UnsupportedDevice(f"{said} : aucun GPU CUDA vu par ce torch ({torch.__version__})")
    count = int(torch.cuda.device_count())
    if index >= count:
        raise UnsupportedDevice(f"{said} : pas de carte {index}, torch en voit {count}")
    problem = unsupported_gpu(torch, index)
    if problem is not None:
        raise UnsupportedDevice(f"{said} : {problem}")


def unsupported_gpu(torch: Any, index: int = 0) -> str | None:
    """Dit pourquoi la carte ``index`` n'est pas utilisable par ce torch, ou ``None`` si elle l'est.

    Même règle que torch : un binaire ``sm_XY`` tourne sur une carte de même
    version majeure X et de version mineure au moins Y ; un code ``compute_XY``
    (PTX) est compilé à la volée pour toute carte de version au moins X.Y. Une
    liste vide ne permet pas de juger : la carte est gardée. Les avertissements
    de torch sur la carte sont tus, celui-ci les remplace.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        major, minor = (int(v) for v in torch.cuda.get_device_capability(index))
        name = str(torch.cuda.get_device_name(index))
        archs = [str(a) for a in torch.cuda.get_arch_list()]
    binaries: list[tuple[int, int]] = []
    ptx: list[tuple[int, int]] = []
    for arch in archs:
        kind, _, version = arch.partition("_")
        if not version.isdigit() or len(version) < 2:
            continue
        target = (int(version[:-1]), int(version[-1]))
        if kind == "sm":
            binaries.append(target)
        elif kind == "compute":
            ptx.append(target)
    if not binaries and not ptx:
        return None
    if any(m == major and n <= minor for m, n in binaries) or any(
        (m, n) <= (major, minor) for m, n in ptx
    ):
        return None
    known = ", ".join(f"sm_{m}{n}" for m, n in sorted(binaries)) or "aucune"
    return (
        f"GPU {name} (sm_{major}{minor}) non pris en charge par ce torch "
        f"({torch.__version__}, architectures : {known})"
    )


def first_line(exc: BaseException) -> str:
    text = str(exc).strip()
    line = text.splitlines()[0].strip() if text else ""
    return line or type(exc).__name__


# --- Chargement paresseux ------------------------------------------------------------


class LazyModel[T]:
    """Modèle chargé à la première utilisation, une seule fois, et utilisé sous verrou.

    Le chargement de transformers n'est pas sûr entre threads : deux chargements
    simultanés — le préchargement et une première requête — peuvent casser le
    modèle. Le verrou les met en file, et sert aussi pour l'usage : un seul device
    ne calcule de toute façon pas deux lots à la fois.

    Le modèle construit tourne une première fois (``check``) avant d'être gardé :
    un modèle qui ne tourne pas est jeté, l'erreur devient ``ModelLoadError``, et
    l'appel suivant recharge.

    ``run`` s'abandonne (``cancel``) en attendant le verrou, avant le chargement,
    et avant le calcul ; pendant le calcul, c'est à ``fn`` de regarder ``cancel``
    entre ses lots. Un chargement commencé va à son terme : il servira à l'appel
    suivant.
    """

    def __init__(
        self,
        name: str,
        device: Callable[[], str],
        build: Callable[[str], T],
        check: Callable[[T], object],
    ) -> None:
        self._name = name
        self._device = device
        self._build = build
        self._check = check
        self._lock = threading.Lock()
        self._model: T | None = None
        self._loaded_on: str | None = None

    @property
    def device(self) -> str | None:
        """Le device où le modèle est chargé ; ``None`` tant qu'il ne l'est pas."""
        return self._loaded_on if self._model is not None else None

    def load(self) -> None:
        """Charge le modèle s'il ne l'est pas déjà."""
        with self._lock:
            if self._model is None:
                self._model = self._load()

    def run[R](self, fn: Callable[[T], R], cancel: threading.Event | None = None) -> R:
        self._acquire(cancel)
        try:
            checkpoint(cancel)
            if self._model is None:
                self._model = self._load()
            checkpoint(cancel)
            try:
                return fn(self._model)
            except ModelCancelled:
                raise
            except Exception as exc:
                raise ModelRunError(
                    f"Calcul du modèle {self._name} impossible : {first_line(exc)}. "
                    "Réessayer ; si l'erreur persiste, relancer loom."
                ) from exc
        finally:
            self._lock.release()

    def _acquire(self, cancel: threading.Event | None) -> None:
        """Le verrou ; un appel annulé pendant l'attente quitte la file (``ModelCancelled``)."""
        if cancel is None:
            self._lock.acquire()
            return
        while not self._lock.acquire(timeout=_CANCEL_POLL):
            checkpoint(cancel)

    def _load(self) -> T:
        device = "?"
        try:
            device = self._device()
            model = self._build(device)
            self._check(model)
        except ImportError as exc:
            missing = exc.name or first_line(exc)
            raise ModelLoadError(
                f"Modèle {self._name} : dépendance {missing!r} absente "
                f"(installer l'extra : loom-ia[{MODELS_EXTRA}])"
            ) from exc
        except UnsupportedDevice as exc:
            raise ModelLoadError(
                f"Modèle {self._name} : {exc}. Mettre models.device à auto (qui se replie "
                "sur cpu quand la carte ne convient pas) ou à cpu"
            ) from exc
        except ModelNotCached as exc:
            raise ModelLoadError(
                f"Modèle {self._name} absent du cache local de Hugging Face "
                f"(models.offline) : le télécharger avec « hf download {self._name} », "
                "ou retirer models.offline"
            ) from exc
        except Exception as exc:
            raise ModelLoadError(
                f"Chargement du modèle {self._name} impossible sur {device} : "
                f"{first_line(exc)}. Réessayer ; si l'erreur persiste, vérifier "
                "models.device et l'installation de torch."
            ) from exc
        log.info("modèle %s chargé sur %s", self._name, device)
        self._loaded_on = device
        return model


# --- Registre du process ---------------------------------------------------------------


@dataclass(slots=True)
class Models:
    """L'embedder et le reranker d'un réglage, partagés par le process."""

    embedder: Embedder
    reranker: Reranker
    _preloading: threading.Thread | None = field(default=None, repr=False)

    def preload(self) -> None:
        """Lance le chargement en tâche de fond, une seule fois ; une erreur est journalisée."""
        if self._preloading is not None:
            return
        parts: tuple[Embedder | Reranker, ...] = (self.embedder, self.reranker)

        def run() -> None:
            for part in parts:
                try:
                    part.load()
                except ModelError as exc:
                    # La requête suivante rechargera le modèle et dira la même erreur.
                    log.error("préchargement impossible : %s", exc)

        self._preloading = threading.Thread(target=run, name="memoire-prechargement", daemon=True)
        self._preloading.start()


_SHARED: Final[dict[tuple[object, ...], Models]] = {}
_GUARD: Final = threading.Lock()


def shared_models(spec: ModelsSpec, target: Target | None = None) -> Models:
    """Les modèles du réglage, construits au premier appel puis partagés par le process.

    Rien n'est chargé ici : les poids le sont au premier calcul, ou par ``preload``.
    Les modèles factices ne sont pas partagés : ils ne coûtent rien. Avec
    ``spec.server``, ``target`` dit où joindre ``loom-mem-gpu`` (``url_env`` et
    ``token_env`` lus) : les modèles sont alors ses clients, une connexion par
    application, et rien ne se charge dans ce process.
    """
    if spec.kind == "fake":
        from loom_ia.tools_builtin.memory.fake import FakeEmbedder, FakeReranker

        return Models(FakeEmbedder(), FakeReranker())
    if spec.server is not None:
        if target is None:
            raise ValueError("models.server : l'adresse de loom-mem-gpu n'a pas été lue")
        return _shared_remote(spec, target)
    key = ("local", spec.embedder, spec.reranker, spec.device, spec.fp16, spec.offline)
    with _GUARD:
        found = _SHARED.get(key)
        if found is None:
            from loom_ia.tools_builtin.memory.bge_m3 import BgeM3Embedder, BgeReranker

            found = Models(BgeM3Embedder(spec), BgeReranker(spec))
            _SHARED[key] = found
        return found


def _shared_remote(spec: ModelsSpec, target: Target) -> Models:
    # ``target`` compte son jeton : deux clients aux jetons différents n'ont pas la même
    # connexion.
    key = ("remote", target, spec.embedder, spec.reranker, spec.fallback())
    with _GUARD:
        found = _SHARED.get(key)
        if found is None:
            from loom_ia.tools_builtin.memory.remote import remote_models

            found = Models(*remote_models(target, spec))
            _SHARED[key] = found
        return found
