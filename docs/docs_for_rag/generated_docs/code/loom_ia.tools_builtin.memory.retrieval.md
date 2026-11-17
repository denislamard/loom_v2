# loom_ia.tools_builtin.memory.retrieval

Module `loom_ia.tools_builtin.memory.retrieval` de loom_v2 (fichier `src/loom_ia/tools_builtin/memory/retrieval.py`).

Recherche hybride : deux listes de candidats, fusion RRF, reranking, regroupement.

1. La question est vectorisée (dense et lexical, un seul passage du modèle).
2. Parmi les passages du projet et des tags demandés, deux listes de
   candidats : les `prefetch_limit` plus proches en dense (produit scalaire,
   les vecteurs étant normés) et en lexical (produit des poids communs).
3. Fusion par rangs (RRF) : chaque liste apporte 1 / (k + rang), k = 2,
   rang compté à partir de 0. Les `rerank_candidates` premiers passent au
   reranker, qui décide de l'ordre final : la fusion ne fait que choisir.
4. Sous `min_score`, plus rien n'est rendu ; au plus `max_chunks_per_doc`
   passages par document, `k` résultats en tout.

Les comparaisons se font en Python (`math.sumprod`, écrit en C) : pour une
mémoire de quelques milliers de passages, quelques millisecondes.

## Constantes de `loom_ia.tools_builtin.memory.retrieval`

- `RRF_K = 2` : Le `k` de la fusion par rangs : chaque liste apporte 1 / (k + rang).

## Fonction `loom_ia.tools_builtin.memory.retrieval.selected`

```python
def selected(index: Sequence[IndexedChunk], project: str | None, tags: Collection[str]) -> list[IndexedChunk]
```

Les passages du projet (tous sans projet) qui portent tous les tags demandés.

## Fonction `loom_ia.tools_builtin.memory.retrieval.fused`

```python
def fused(query: Embedding, pool: Sequence[IndexedChunk], spec: RetrievalSpec) -> list[IndexedChunk]
```

Les candidats à passer au reranker, du meilleur au moins bon.

## Fonction `loom_ia.tools_builtin.memory.retrieval.hits`

```python
def hits(candidates: Sequence[IndexedChunk], scores: Sequence[float], k: int, spec: RetrievalSpec, *, min_score: float | None=None) -> list[Hit]
```

Les résultats rendus : triés par score du reranker, seuil, regroupement par document.

## Fonction `loom_ia.tools_builtin.memory.retrieval.passage`

```python
def passage(chunk: IndexedChunk) -> str
```

Le texte que lit le reranker : le chemin de titres, puis le passage.
