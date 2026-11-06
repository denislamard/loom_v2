# SPDX-License-Identifier: Apache-2.0
"""BGE-M3 et son reranker sur ``transformers`` (extra ``memory``), sur CPU ou GPU.

Les mêmes calculs que FlagEmbedding, sans lui :

- **dense** : le vecteur du premier token (``[CLS]``) de la dernière couche,
  normé (1024 dimensions) ;
- **lexical** : la tête ``sparse_linear.pt`` du dépôt du modèle, appliquée à
  chaque token de la dernière couche, puis ReLU ; un poids par identifiant de
  token (le plus fort s'il revient), sans les tokens spéciaux ni les poids nuls ;
- **reranker** : un classifieur de paires (question, passage), son logit passé
  dans une sigmoïde, d'où un score entre 0 et 1.

torch et transformers ne sont importés qu'au chargement des poids, au premier
calcul ou au préchargement : ce module s'importe sans eux. Demi-précision sur
GPU (``fp16``, faite avant le transfert vers la carte), float32 sur CPU. Lots
de 16 textes ; au plus 1024 tokens par texte, 512 par paire pour le reranker.
Un calcul abandonné (``cancel``) s'arrête avant le lot suivant : sur CPU, un lot
prend de l'ordre de 30 s.

Les barres de progression de transformers et de huggingface_hub (« Loading
weights », téléchargements) sont coupées au chargement : un outil n'écrit rien
dans la console. Le réglage vaut pour le process. Avec ``models.offline``, les
fichiers sont lus dans le cache local seulement, sans requête vers le Hub.
"""

import contextlib
import importlib
import threading
from collections.abc import Generator, Sequence
from dataclasses import dataclass
from typing import Any, Final

from loom_ia.tools_builtin.memory.models import (
    Embedding,
    LazyModel,
    ModelNotCached,
    SparseVec,
    checkpoint,
    first_line,
    resolve_device,
)
from loom_ia.tools_builtin.memory_params import ModelsSpec

# Version du calcul : dense, lexical, troncature, normalisation, sigmoïde du reranker.
# À incrémenter dès que l'un d'eux change : loom-mem-gpu l'annonce, et un client d'une
# autre version refuse ses vecteurs plutôt que d'en mêler deux sortes dans une base.
ALGO: Final = "bge-m3/2"
# Dimension du dense des modèles de la famille BGE-M3 : la base l'inscrit avant tout calcul.
DENSE_DIM: Final = 1024
BATCH: Final = 16
# Caractères lus au plus par texte (question, passage), ici comme par loom-mem-gpu : bien
# au-delà de ce que lit le tokenizer (1024 tokens, environ 4 caractères chacun en
# français), la coupe ne change le calcul que pour un texte dont les tokens feraient en
# moyenne plus de 19 caractères. Elle donne aux deux chemins la même borne : sans elle,
# un texte plus long passait ici et était refusé par l'application (413).
MAX_CHARS: Final = 20_000
MAX_TOKENS: Final = 1024
MAX_PAIR_TOKENS: Final = 512
SPARSE_HEAD: Final = "sparse_linear.pt"


def _modules() -> tuple[Any, Any]:
    """torch et transformers ; ``ImportError`` (nommant le paquet) sans l'extra ``memory``."""
    torch: Any = importlib.import_module("torch")
    transformers: Any = importlib.import_module("transformers")
    transformers.utils.logging.disable_progress_bar()
    return torch, transformers


@contextlib.contextmanager
def _from_hub(offline: bool) -> Generator[None]:
    """Hors ligne, un fichier absent du cache (``OSError``) devient ``ModelNotCached``."""
    try:
        yield
    except OSError as exc:
        if not offline:
            raise
        raise ModelNotCached(first_line(exc)) from exc


def half_precision(fp16: bool, device: str) -> bool:
    """Demi-précision : demandée (``fp16``) et sur une carte ; jamais sur CPU ni mps."""
    return fp16 and device.startswith("cuda")


def clipped(text: str) -> str:
    """Le texte borné à ``MAX_CHARS`` caractères, comme le lisent les deux chemins."""
    return text[:MAX_CHARS]


def batches[T](items: Sequence[T]) -> list[Sequence[T]]:
    """Les lots de ``BATCH`` éléments : ceux du calcul local et des requêtes à l'application."""
    return [items[i : i + BATCH] for i in range(0, len(items), BATCH)]


def _placed(module: Any, device: str, half: bool) -> Any:
    # Demi-précision avant le transfert : la carte ne reçoit jamais les poids en float32,
    # deux fois plus lourds (2,2 Go pour BGE-M3), ce qui ferait déborder une petite carte.
    if half:
        module = module.half()
    module = module.to(device)
    module.eval()
    return module


@dataclass(frozen=True, slots=True)
class _Encoder:
    torch: Any
    tokenizer: Any
    model: Any
    head: Any
    device: str
    special: frozenset[int]

    def encode(
        self, texts: Sequence[str], cancel: threading.Event | None = None
    ) -> list[Embedding]:
        found: list[Embedding] = []
        torch = self.torch
        with torch.inference_mode():
            for batch in batches(texts):
                checkpoint(cancel)
                inputs = self.tokenizer(
                    [clipped(text) for text in batch],
                    padding=True,
                    truncation=True,
                    max_length=MAX_TOKENS,
                    return_tensors="pt",
                ).to(self.device)
                hidden = self.model(**inputs, return_dict=True).last_hidden_state
                dense = torch.nn.functional.normalize(hidden[:, 0].float(), dim=-1)
                weights = torch.relu(self.head(hidden)).squeeze(-1).float()
                ids = inputs["input_ids"]
                for row in range(len(batch)):
                    found.append(
                        Embedding(
                            dense=[float(x) for x in dense[row].tolist()],
                            sparse=self._lexical(weights[row].tolist(), ids[row].tolist()),
                        )
                    )
        return found

    def _lexical(self, weights: list[float], ids: list[int]) -> SparseVec:
        best: dict[int, float] = {}
        for weight, token in zip(weights, ids, strict=True):
            if token in self.special or weight <= 0.0:
                continue
            if weight > best.get(token, 0.0):
                best[token] = float(weight)
        items = sorted(best.items())
        return SparseVec(indices=[i for i, _ in items], values=[v for _, v in items])


@dataclass(frozen=True, slots=True)
class _PairScorer:
    torch: Any
    tokenizer: Any
    model: Any
    device: str

    def score(
        self, query: str, passages: Sequence[str], cancel: threading.Event | None = None
    ) -> list[float]:
        found: list[float] = []
        torch = self.torch
        with torch.inference_mode():
            for batch in batches(passages):
                checkpoint(cancel)
                inputs = self.tokenizer(
                    [clipped(query)] * len(batch),
                    [clipped(passage) for passage in batch],
                    padding=True,
                    truncation="only_second",
                    max_length=MAX_PAIR_TOKENS,
                    return_tensors="pt",
                ).to(self.device)
                logits = self.model(**inputs, return_dict=True).logits.view(-1).float()
                found.extend(float(x) for x in torch.sigmoid(logits).tolist())
        return found


class BgeM3Embedder:
    """BGE-M3 : dense et lexical en un seul passage du modèle."""

    def __init__(self, spec: ModelsSpec) -> None:
        self._spec = spec
        self._model = LazyModel[_Encoder](
            spec.embedder,
            lambda: resolve_device(spec.device),
            self._build,
            lambda encoder: encoder.encode(["warmup"]),
        )

    @property
    def model_name(self) -> str:
        return self._spec.embedder

    @property
    def dim(self) -> int:
        return DENSE_DIM

    @property
    def device(self) -> str | None:
        return self._model.device

    def load(self) -> None:
        self._model.load()

    def _build(self, device: str) -> _Encoder:
        torch, transformers = _modules()
        name = self._spec.embedder
        offline = self._spec.offline
        half = half_precision(self._spec.fp16, device)
        with _from_hub(offline):
            tokenizer = transformers.AutoTokenizer.from_pretrained(name, local_files_only=offline)
            model = transformers.AutoModel.from_pretrained(name, local_files_only=offline)
            weights = transformers.utils.cached_file(name, SPARSE_HEAD, local_files_only=offline)
        head = torch.nn.Linear(model.config.hidden_size, 1)
        head.load_state_dict(torch.load(weights, map_location="cpu", weights_only=True))
        special = {
            tokenizer.cls_token_id,
            tokenizer.eos_token_id,
            tokenizer.pad_token_id,
            tokenizer.unk_token_id,
        }
        return _Encoder(
            torch=torch,
            tokenizer=tokenizer,
            model=_placed(model, device, half),
            head=_placed(head, device, half),
            device=device,
            special=frozenset(int(i) for i in special if i is not None),
        )

    def embed_documents(
        self, texts: Sequence[str], cancel: threading.Event | None = None
    ) -> list[Embedding]:
        if not texts:
            return []
        batch = list(texts)
        return self._model.run(lambda encoder: encoder.encode(batch, cancel), cancel)

    def embed_query(self, text: str, cancel: threading.Event | None = None) -> Embedding:
        return self._model.run(lambda encoder: encoder.encode([text], cancel), cancel)[0]


class BgeReranker:
    """bge-reranker-v2-m3 : un score entre 0 et 1 par paire (question, passage)."""

    def __init__(self, spec: ModelsSpec) -> None:
        self._spec = spec
        self._model = LazyModel[_PairScorer](
            spec.reranker,
            lambda: resolve_device(spec.device),
            self._build,
            lambda scorer: scorer.score("warmup", ["warmup"]),
        )

    @property
    def model_name(self) -> str:
        return self._spec.reranker

    @property
    def device(self) -> str | None:
        return self._model.device

    def load(self) -> None:
        self._model.load()

    def _build(self, device: str) -> _PairScorer:
        torch, transformers = _modules()
        name = self._spec.reranker
        offline = self._spec.offline
        half = half_precision(self._spec.fp16, device)
        with _from_hub(offline):
            tokenizer = transformers.AutoTokenizer.from_pretrained(name, local_files_only=offline)
            model = transformers.AutoModelForSequenceClassification.from_pretrained(
                name, local_files_only=offline
            )
        return _PairScorer(
            torch=torch,
            tokenizer=tokenizer,
            model=_placed(model, device, half),
            device=device,
        )

    def score(
        self, query: str, passages: Sequence[str], cancel: threading.Event | None = None
    ) -> list[float]:
        if not passages:
            return []
        batch = list(passages)
        return self._model.run(lambda scorer: scorer.score(query, batch, cancel), cancel)
