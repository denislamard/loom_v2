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
GPU (``fp16``), float32 sur CPU. Lots de 16 textes ; au plus 1024 tokens par
texte, 512 par paire pour le reranker.
"""

import importlib
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Final

from loom_ia.tools_builtin.memory.models import (
    Embedding,
    LazyModel,
    SparseVec,
    resolve_device,
)
from loom_ia.tools_builtin.memory_params import ModelsSpec

# Dimension du dense des modèles de la famille BGE-M3 : la base l'inscrit avant tout calcul.
DENSE_DIM: Final = 1024
BATCH: Final = 16
MAX_TOKENS: Final = 1024
MAX_PAIR_TOKENS: Final = 512
SPARSE_HEAD: Final = "sparse_linear.pt"


def _modules() -> tuple[Any, Any]:
    """torch et transformers ; ``ImportError`` (nommant le paquet) sans l'extra ``memory``."""
    torch: Any = importlib.import_module("torch")
    transformers: Any = importlib.import_module("transformers")
    return torch, transformers


def _placed(module: Any, device: str, half: bool) -> Any:
    module = module.to(device)
    if half:
        module = module.half()
    module.eval()
    return module


def _batches[T](items: Sequence[T]) -> list[Sequence[T]]:
    return [items[i : i + BATCH] for i in range(0, len(items), BATCH)]


@dataclass(frozen=True, slots=True)
class _Encoder:
    torch: Any
    tokenizer: Any
    model: Any
    head: Any
    device: str
    special: frozenset[int]

    def encode(self, texts: Sequence[str]) -> list[Embedding]:
        found: list[Embedding] = []
        torch = self.torch
        with torch.inference_mode():
            for batch in _batches(texts):
                inputs = self.tokenizer(
                    list(batch),
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

    def score(self, query: str, passages: Sequence[str]) -> list[float]:
        found: list[float] = []
        torch = self.torch
        with torch.inference_mode():
            for batch in _batches(passages):
                inputs = self.tokenizer(
                    [query] * len(batch),
                    list(batch),
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

    def load(self) -> None:
        self._model.load()

    def _build(self, device: str) -> _Encoder:
        torch, transformers = _modules()
        name = self._spec.embedder
        half = self._spec.fp16 and device.startswith("cuda")
        tokenizer = transformers.AutoTokenizer.from_pretrained(name)
        model = transformers.AutoModel.from_pretrained(name)
        head = torch.nn.Linear(model.config.hidden_size, 1)
        weights = transformers.utils.cached_file(name, SPARSE_HEAD)
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

    def embed_documents(self, texts: Sequence[str]) -> list[Embedding]:
        if not texts:
            return []
        batch = list(texts)
        return self._model.run(lambda encoder: encoder.encode(batch))

    def embed_query(self, text: str) -> Embedding:
        return self._model.run(lambda encoder: encoder.encode([text]))[0]


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

    def load(self) -> None:
        self._model.load()

    def _build(self, device: str) -> _PairScorer:
        torch, transformers = _modules()
        name = self._spec.reranker
        half = self._spec.fp16 and device.startswith("cuda")
        tokenizer = transformers.AutoTokenizer.from_pretrained(name)
        model = transformers.AutoModelForSequenceClassification.from_pretrained(name)
        return _PairScorer(
            torch=torch,
            tokenizer=tokenizer,
            model=_placed(model, device, half),
            device=device,
        )

    def score(self, query: str, passages: Sequence[str]) -> list[float]:
        if not passages:
            return []
        batch = list(passages)
        return self._model.run(lambda scorer: scorer.score(query, batch))
