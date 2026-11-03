# SPDX-License-Identifier: Apache-2.0
"""Entretien d'une base de mémoire, hors de tout run : ce que fait ``loom memory``.

- ``export`` : un document JSON par ligne, texte intégral et métadonnées — la
  sauvegarde de référence, les vecteurs se recalculant ;
- ``import_`` : réimporte un export (de loom.memory ou de loom-notes, même
  format), en sautant les ``doc_id`` déjà présents et les contenus déjà rangés
  dans le même projet ;
- ``reindex`` : recalcule tous les passages avec le modèle de la config, après
  un changement de modèle ou de découpage ; interrompue, elle se relance ;
- ``evaluate`` : mesure la recherche sur un jeu doré (requête → document
  attendu, ou ``null`` pour une question hors sujet) : recall@1, recall@5, MRR
  sur les questions qui attendent un document, et un ``min_score`` qui ne perd
  aucune réponse attendue ; avec des questions hors sujet, celui qui les sépare
  du bruit, s'il existe.
"""

import asyncio
import json
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from loom_ia.core.ports import ToolError
from loom_ia.tools_builtin.memory.records import Document, Hit
from loom_ia.tools_builtin.memory.service import MemoryService


async def export(service: MemoryService, path: Path) -> int:
    """Écrit tous les documents dans ``path``, qui ne doit pas exister ; rend leur nombre."""
    await service.start()
    documents = await asyncio.to_thread(service.store.documents)

    def write() -> None:
        # « x » : un fichier existant n'est jamais écrasé.
        with path.open("x", encoding="utf-8") as handle:
            for doc in documents:
                handle.write(doc.model_dump_json() + "\n")

    await asyncio.to_thread(write)
    return len(documents)


class Imported(BaseModel):
    imported: int
    skipped: int


async def import_(service: MemoryService, path: Path) -> Imported:
    """Réimporte un export ; un document déjà présent (identifiant ou contenu) est sauté."""
    await service.start()
    lines = await asyncio.to_thread(path.read_text, encoding="utf-8")
    imported = skipped = 0
    for number, line in enumerate(lines.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            doc = Document.model_validate(json.loads(line))
        except (ValueError, ValidationError) as exc:
            raise ToolError(f"{path.name}, ligne {number} : document illisible ({exc}).") from exc
        present = await asyncio.to_thread(service.store.get, doc.doc_id)
        same = await asyncio.to_thread(service.store.find_by_hash, doc.content_hash, doc.project)
        if present is not None or same is not None:
            skipped += 1
            continue
        await service.index_document(doc)
        imported += 1
    return Imported(imported=imported, skipped=skipped)


async def reindex(service: MemoryService) -> int:
    """Recalcule les passages de tous les documents avec le modèle de la config."""
    await service.start(check_model=False)
    embedder = service.models.embedder
    documents = await asyncio.to_thread(service.store.documents)
    await asyncio.to_thread(service.store.reset_index, embedder.model_name, embedder.dim)
    for doc in documents:
        await service.index_document(doc)
    return len(documents)


# --- Évaluation ---------------------------------------------------------------------


class GoldenCase(BaseModel):
    """Un cas du jeu doré : une requête, et le document qu'elle doit trouver.

    ``doc_id: null`` : une question hors sujet, qui ne doit rien trouver. Le
    champ reste obligatoire : un oubli ne devient pas une question hors sujet.
    """

    query: str
    doc_id: str | None
    # Informatif, pour relire le fichier.
    title: str | None = None
    # Filtre appliqué à la recherche, comme le ferait un agent au projet imposé.
    project: str | None = None


class CaseResult(BaseModel):
    query: str
    # None : question hors sujet.
    doc_id: str | None
    rank: int | None = Field(description="Rang du document attendu (1 = premier), None si absent")
    expected_score: float | None
    top_doc_id: str | None
    top_score: float | None


class EvalReport(BaseModel):
    # Les questions qui attendent un document : recall et MRR portent sur elles.
    cases: int
    # Les questions hors sujet (``doc_id: null``).
    off_topic: int = 0
    recall_at_1: float
    recall_at_5: float
    mrr: float
    lowest_expected: float | None = Field(
        default=None, description="Plus bas score d'un document attendu et trouvé"
    )
    highest_off_topic: float | None = Field(
        default=None, description="Plus haut score obtenu par une question hors sujet"
    )
    separable: bool | None = Field(
        default=None,
        description="Un seuil sépare-t-il les réponses attendues du hors-sujet ? "
        "None sans question hors sujet",
    )
    suggested_min_score: float | None = Field(
        description="Sans question hors sujet : le plus haut seuil qui ne perd aucun cas trouvé. "
        "Avec : le milieu entre le hors-sujet le plus fort et la réponse attendue la plus "
        "faible, s'ils sont séparables ; None sinon"
    )
    noise_removed_at_suggested: float | None = Field(
        description="Part des résultats hors document attendu qui tomberaient sous ce seuil"
    )
    results: list[CaseResult]


def load_golden(path: Path) -> list[GoldenCase]:
    """Les cas du fichier JSONL, un par ligne ; ``ToolError`` sur une ligne illisible."""
    cases: list[GoldenCase] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            cases.append(GoldenCase.model_validate(json.loads(line)))
        except (ValueError, ValidationError) as exc:
            raise ToolError(f"{path.name}, ligne {number} : cas illisible ({exc}).") from exc
    return cases


async def evaluate(service: MemoryService, cases: list[GoldenCase], k: int = 10) -> EvalReport:
    """Joue chaque cas sans seuil, et dit où se range le document attendu.

    Une question hors sujet (``doc_id`` à None) ne compte pas dans recall et
    MRR : son meilleur score situe le bruit, que le seuil suggéré doit écarter.
    """
    results: list[CaseResult] = []
    positives: list[float] = []
    negatives: list[float] = []
    off_topic: list[float] = []
    for case in cases:
        hits = await service.search(case.query, project=case.project, k=k, min_score=0.0)
        order = _documents(hits)
        rank = order.index(case.doc_id) + 1 if case.doc_id in order else None
        expected = next((hit.score for hit in hits if hit.doc_id == case.doc_id), None)
        if expected is not None:
            positives.append(expected)
        negatives.extend(hit.score for hit in hits if hit.doc_id != case.doc_id)
        if case.doc_id is None and hits:
            off_topic.append(hits[0].score)
        results.append(
            CaseResult(
                query=case.query,
                doc_id=case.doc_id,
                rank=rank,
                expected_score=expected,
                top_doc_id=hits[0].doc_id if hits else None,
                top_score=hits[0].score if hits else None,
            )
        )
    expecting = [result for result in results if result.doc_id is not None]
    count = len(expecting)
    found = [result.rank for result in expecting if result.rank is not None]
    lowest = min(positives) if positives else None
    highest = max(off_topic) if off_topic else None
    off = len(results) - count
    separable, suggested = _threshold(lowest, highest if off else None)
    noise = (
        sum(1 for score in negatives if score < suggested) / len(negatives)
        if suggested is not None and negatives
        else None
    )
    return EvalReport(
        cases=count,
        off_topic=off,
        recall_at_1=(sum(1 for rank in found if rank == 1) / count) if count else 0.0,
        recall_at_5=(sum(1 for rank in found if rank <= 5) / count) if count else 0.0,
        mrr=(sum(1 / rank for rank in found) / count) if count else 0.0,
        lowest_expected=round(lowest, 3) if lowest is not None else None,
        highest_off_topic=round(highest, 3) if highest is not None else None,
        separable=separable,
        suggested_min_score=suggested,
        noise_removed_at_suggested=round(noise, 3) if noise is not None else None,
        results=results,
    )


def _threshold(lowest: float | None, highest: float | None) -> tuple[bool | None, float | None]:
    """Séparables ?, et le seuil suggéré, d'après la réponse attendue la plus faible et le
    hors-sujet le plus fort (None : pas de question hors sujet, ou aucun résultat).

    Un passage est rendu si son score atteint ``min_score`` : le seuil doit être au plus
    ``lowest`` et au-dessus de ``highest``. Le milieu laisse de la marge des deux côtés.
    """
    if lowest is None:
        return None, None
    if highest is None:
        return None, round(lowest, 3)
    if highest >= lowest:
        return False, None
    middle = (highest + lowest) / 2
    rounded = round(middle, 3)
    return True, rounded if highest < rounded <= lowest else middle


def _documents(hits: list[Hit]) -> list[str]:
    seen: dict[str, None] = {}
    for hit in hits:
        seen.setdefault(hit.doc_id, None)
    return list(seen)
