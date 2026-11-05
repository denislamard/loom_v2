# SPDX-License-Identifier: Apache-2.0
"""Le contrat du fil entre loom et ``loom-mem-gpu`` (protocole 1), partagé par les deux côtés.

Module léger — Pydantic, la bibliothèque standard, et ``bge_m3`` pour ses
constantes (qui n'importe torch qu'au chargement des poids) — : le client de
loom l'importe sans torch.

    GET  /v1/health   l'état de l'application, ses modèles, ses plafonds
    POST /v1/embed    {texts}            → {items: [{dense, sparse}]}
    POST /v1/score    {query, passages}  → {scores}

Le dense voyage en base64 de float32 petit-boutiste : c'est ce que la base
range (``store.py``), donc rien ne se perd en route. Le lexical voyage tel
quel : identifiants de tokens triés et leurs poids.

Les plafonds valent par requête. Le client envoie des lots de ``BATCH``
textes : il ne les atteint jamais.
"""

import base64
import sys
from array import array
from collections.abc import Sequence
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict

from loom_ia.tools_builtin.memory import bge_m3
from loom_ia.tools_builtin.memory.models import Embedding, SparseVec

PROTOCOL: Final = 1
# Textes (ou passages) au plus par requête, caractères au plus par texte, octets au plus
# par corps de requête (lu seulement après ce contrôle).
MAX_TEXTS: Final = 32
MAX_CHARS: Final = 20_000
MAX_BODY_BYTES: Final = 2_000_000
# Textes par requête du client : les lots de BGE-M3. Un appel annulé s'arrête au suivant.
BATCH: Final = bge_m3.BATCH

type State = Literal["loading", "ready", "failed"]


class _Wire(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class EmbedRequest(_Wire):
    texts: list[str]


class SparseWire(_Wire):
    indices: list[int]
    values: list[float]


class EmbedItem(_Wire):
    # base64 de float32 petit-boutiste.
    dense: str
    sparse: SparseWire


class EmbedResponse(_Wire):
    items: list[EmbedItem]


class ScoreRequest(_Wire):
    query: str
    passages: list[str]


class ScoreResponse(_Wire):
    scores: list[float]


class Health(_Wire):
    """L'état de l'application. Avant ``ready``, seuls ``protocol``, ``state`` et ``error``."""

    protocol: int
    state: State
    error: str | None = None
    loom: str | None = None
    algo: str | None = None
    embedder: str | None = None
    reranker: str | None = None
    dim: int | None = None
    device: str | None = None
    fp16: bool | None = None
    vram_used_mb: int | None = None
    max_texts: int | None = None
    max_chars: int | None = None


class ErrorBody(_Wire):
    error: str


# --- Plafonds -------------------------------------------------------------------------


def over_caps(texts: Sequence[str], what: str = "textes") -> str | None:
    """Pourquoi ces textes dépassent les plafonds d'une requête, ou ``None``."""
    if len(texts) > MAX_TEXTS:
        return f"{len(texts)} {what} : au plus {MAX_TEXTS} par requête"
    for position, text in enumerate(texts):
        if len(text) > MAX_CHARS:
            return f"{what} n° {position + 1} : {len(text)} caractères, au plus {MAX_CHARS}"
    return None


# --- Encodage --------------------------------------------------------------------------


def encode_dense(values: Sequence[float]) -> str:
    """Le dense en base64 de float32 petit-boutiste."""
    packed = array("f", values)
    if sys.byteorder == "big":
        packed.byteswap()
    return base64.b64encode(packed.tobytes()).decode("ascii")


def decode_dense(text: str) -> list[float]:
    """L'inverse d'``encode_dense`` ; ``ValueError`` si le texte n'en est pas un."""
    raw = base64.b64decode(text, validate=True)
    if len(raw) % 4:
        raise ValueError("dense : longueur qui n'est pas un multiple de 4 octets")
    packed = array("f")
    packed.frombytes(raw)
    if sys.byteorder == "big":
        packed.byteswap()
    return packed.tolist()


def to_item(embedding: Embedding) -> EmbedItem:
    sparse = embedding.sparse
    return EmbedItem(
        dense=encode_dense(embedding.dense),
        sparse=SparseWire(indices=list(sparse.indices), values=list(sparse.values)),
    )


def from_item(item: EmbedItem) -> Embedding:
    if len(item.sparse.indices) != len(item.sparse.values):
        raise ValueError("lexical : indices et poids de longueurs différentes")
    return Embedding(
        dense=decode_dense(item.dense),
        sparse=SparseVec(indices=list(item.sparse.indices), values=list(item.sparse.values)),
    )
