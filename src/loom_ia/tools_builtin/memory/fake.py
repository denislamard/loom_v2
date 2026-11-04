# SPDX-License-Identifier: Apache-2.0
"""Modèles factices et déterministes (``models.kind: fake``) : pas de torch, pas de téléchargement.

Le dense est un sac de mots haché, le lexical compte les mots, le reranker
mesure la part des mots de la question présents dans le passage : assez pour
que la recherche se comporte de façon plausible dans les essais et les
démonstrations. Ce n'est pas une recherche par le sens.
"""

import hashlib
import math
import re
import threading
from collections import Counter
from collections.abc import Sequence
from typing import Final

from loom_ia.tools_builtin.memory.models import Embedding, SparseVec, checkpoint

_TOKEN: Final = re.compile(r"\w+", re.UNICODE)


def _tokens(text: str) -> list[str]:
    return [t.casefold() for t in _TOKEN.findall(text) if len(t) > 1]


def _bucket(token: str, mod: int) -> int:
    return int.from_bytes(hashlib.blake2b(token.encode(), digest_size=4).digest(), "big") % mod


class FakeEmbedder:
    def __init__(self, dim: int = 64) -> None:
        self._dim = dim

    @property
    def model_name(self) -> str:
        return f"fake-{self._dim}"

    @property
    def dim(self) -> int:
        return self._dim

    @property
    def device(self) -> str | None:
        return None

    def load(self) -> None:
        pass

    def _one(self, text: str) -> Embedding:
        counts = Counter(_tokens(text))
        dense = [0.0] * self._dim
        for token, n in counts.items():
            dense[_bucket(token, self._dim)] += float(n)
        norm = math.sqrt(sum(x * x for x in dense)) or 1.0
        dense = [x / norm for x in dense]
        lexical: dict[int, float] = {}
        for token, n in counts.items():
            index = _bucket(token, 1 << 20)
            lexical[index] = lexical.get(index, 0.0) + float(n)
        items = sorted(lexical.items())
        return Embedding(
            dense=dense,
            sparse=SparseVec(indices=[i for i, _ in items], values=[v for _, v in items]),
        )

    def embed_documents(
        self, texts: Sequence[str], cancel: threading.Event | None = None
    ) -> list[Embedding]:
        found: list[Embedding] = []
        for text in texts:
            checkpoint(cancel)
            found.append(self._one(text))
        return found

    def embed_query(self, text: str, cancel: threading.Event | None = None) -> Embedding:
        checkpoint(cancel)
        return self._one(text)


class FakeReranker:
    @property
    def model_name(self) -> str:
        return "fake-reranker"

    @property
    def device(self) -> str | None:
        return None

    def load(self) -> None:
        pass

    def score(
        self, query: str, passages: Sequence[str], cancel: threading.Event | None = None
    ) -> list[float]:
        checkpoint(cancel)
        asked = set(_tokens(query))
        if not asked:
            return [0.0] * len(passages)
        return [len(asked & set(_tokens(passage))) / len(asked) for passage in passages]
