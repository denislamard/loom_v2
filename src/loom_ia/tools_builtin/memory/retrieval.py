# SPDX-License-Identifier: Apache-2.0
"""Recherche hybride : deux listes de candidats, fusion RRF, reranking, regroupement.

1. La question est vectorisée (dense et lexical, un seul passage du modèle).
2. Parmi les passages du projet et des tags demandés, deux listes de
   candidats : les ``prefetch_limit`` plus proches en dense (produit scalaire,
   les vecteurs étant normés) et en lexical (produit des poids communs).
3. Fusion par rangs (RRF) : chaque liste apporte 1 / (k + rang), k = 2,
   rang compté à partir de 0. Les ``rerank_candidates`` premiers passent au
   reranker, qui décide de l'ordre final : la fusion ne fait que choisir.
4. Sous ``min_score``, plus rien n'est rendu ; au plus ``max_chunks_per_doc``
   passages par document, ``k`` résultats en tout.

Les comparaisons se font en Python (``math.sumprod``, écrit en C) : pour une
mémoire de quelques milliers de passages, quelques millisecondes.
"""

import math
from collections.abc import Collection, Sequence
from typing import Final

from loom_ia.tools_builtin.memory.chunk import snippet
from loom_ia.tools_builtin.memory.models import Embedding
from loom_ia.tools_builtin.memory.records import Hit
from loom_ia.tools_builtin.memory.store import IndexedChunk
from loom_ia.tools_builtin.memory_params import RetrievalSpec

RRF_K: Final = 2


def selected(
    index: Sequence[IndexedChunk], project: str | None, tags: Collection[str]
) -> list[IndexedChunk]:
    """Les passages du projet (tous sans projet) qui portent tous les tags demandés."""
    wanted = frozenset(tags)
    return [
        chunk
        for chunk in index
        if (project is None or chunk.project == project) and wanted <= chunk.tags
    ]


def fused(
    query: Embedding, pool: Sequence[IndexedChunk], spec: RetrievalSpec
) -> list[IndexedChunk]:
    """Les candidats à passer au reranker, du meilleur au moins bon."""
    if not pool:
        return []
    dense_scores = [math.sumprod(query.dense, chunk.dense) for chunk in pool]
    weights = dict(zip(query.sparse.indices, query.sparse.values, strict=True))
    lexical_scores = [
        sum(weight * chunk.sparse.get(token, 0.0) for token, weight in weights.items())
        for chunk in pool
    ]
    fusion: dict[int, float] = {}
    for scores, keep_zero in ((dense_scores, True), (lexical_scores, False)):
        order = sorted(range(len(pool)), key=lambda i: (-scores[i], _key(pool[i])))
        ranked = [i for i in order if keep_zero or scores[i] > 0][: spec.prefetch_limit]
        for rank, i in enumerate(ranked):
            fusion[i] = fusion.get(i, 0.0) + 1.0 / (RRF_K + rank)
    best = sorted(fusion, key=lambda i: (-fusion[i], _key(pool[i])))
    return [pool[i] for i in best[: spec.rerank_candidates]]


def hits(
    candidates: Sequence[IndexedChunk],
    scores: Sequence[float],
    k: int,
    spec: RetrievalSpec,
    *,
    min_score: float | None = None,
) -> list[Hit]:
    """Les résultats rendus : triés par score du reranker, seuil, regroupement par document."""
    if len(candidates) != len(scores):
        raise ValueError("candidats et scores de longueurs différentes")
    threshold = spec.min_score if min_score is None else min_score
    ranked = sorted(
        zip(candidates, scores, strict=True), key=lambda pair: (-pair[1], _key(pair[0]))
    )
    per_doc: dict[str, int] = {}
    found: list[Hit] = []
    for chunk, score in ranked:
        if score < threshold:
            break
        taken = per_doc.get(chunk.doc_id, 0)
        if taken >= spec.max_chunks_per_doc:
            continue
        per_doc[chunk.doc_id] = taken + 1
        short = len(chunk.text) <= spec.full_chunk_chars
        found.append(
            Hit(
                doc_id=chunk.doc_id,
                title=chunk.title,
                project=chunk.project,
                score=round(score, 4),
                snippet=chunk.text if short else snippet(chunk.text, spec.snippet_chars),
                truncated=not short,
                heading_path=chunk.heading_path,
                source=chunk.source,
                added_at=chunk.added_at,
            )
        )
        if len(found) >= k:
            break
    return found


def passage(chunk: IndexedChunk) -> str:
    """Le texte que lit le reranker : le chemin de titres, puis le passage."""
    return f"{chunk.heading_path}\n\n{chunk.text}" if chunk.heading_path else chunk.text


def _key(chunk: IndexedChunk) -> tuple[str, int]:
    # Départage stable : à score égal, l'ordre ne dépend pas de la lecture de la base.
    return chunk.doc_id, chunk.index
