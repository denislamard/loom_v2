# SPDX-License-Identifier: Apache-2.0
"""Les opérations de la mémoire, communes à l'outil et à ``loom memory``.

Asynchrones : la base et les modèles travaillent dans des threads. Les
erreurs destinées au modèle sont des ``ToolError``, au message lisible.

Ce que la mémoire garantit, et que vérifient ses essais :

- un même contenu n'est rangé qu'une fois par projet ; le redonner rend le
  document existant (``duplicate_of``) sans rien écrire : un ajout se refait
  sans risque après une interruption ;
- une même source (page, fichier) n'est qu'un document par projet : la
  redonner remplace son texte ; elle ne déplace jamais un document d'un autre
  projet ;
- ``update`` refuse un texte qui est déjà celui d'un autre document du projet,
  au lieu de ne rien faire en silence ;
- avec un projet imposé, ``get``, ``update`` et ``delete`` ne voient que ses
  documents.
"""

import asyncio
import sqlite3
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from loom_ia.core.ports import ToolError
from loom_ia.tools_builtin.memory.chunk import chunk_document, content_hash
from loom_ia.tools_builtin.memory.models import ModelError, Models
from loom_ia.tools_builtin.memory.records import (
    DocSummary,
    Document,
    Hit,
    IngestResult,
    SourceKind,
    new_doc_id,
    utc_now_iso,
)
from loom_ia.tools_builtin.memory.retrieval import fused, hits, passage, selected
from loom_ia.tools_builtin.memory.store import IndexedChunk, MemoryStore, ModelMismatchError
from loom_ia.tools_builtin.memory_params import ChunkingSpec, RetrievalSpec

MIN_TITLE_CHARS = 3


@dataclass(frozen=True, slots=True)
class Extracted:
    """Un texte à ranger et son titre, tels que l'extraction les a lus."""

    title: str
    text: str


class MemoryService:
    """Une base de mémoire, ses modèles et ses réglages."""

    def __init__(
        self,
        store: MemoryStore,
        models: Models,
        retrieval: RetrievalSpec,
        chunking: ChunkingSpec,
    ) -> None:
        self.store = store
        self.models = models
        self._retrieval = retrieval
        self._chunking = chunking
        self._ready = False
        self._ready_lock = asyncio.Lock()
        self._index_lock = threading.Lock()
        self._index: tuple[int, list[IndexedChunk]] | None = None

    async def start(self, *, check_model: bool = True) -> None:
        """Crée le schéma s'il manque et vérifie le modèle d'indexation, une fois."""
        async with self._ready_lock:
            if self._ready:
                return
            embedder = self.models.embedder
            try:
                await asyncio.to_thread(
                    self.store.ensure, embedder.model_name, embedder.dim, check=check_model
                )
            except ModelMismatchError as exc:
                raise ToolError(str(exc)) from exc
            except sqlite3.Error as exc:
                raise ToolError(
                    f"Base de mémoire illisible ({self.store.path.name}) : {exc}"
                ) from exc
            self._ready = check_model

    # --- Lecture ----------------------------------------------------------------------

    async def search(
        self,
        query: str,
        *,
        project: str | None,
        tags: Sequence[str] = (),
        k: int = 5,
        min_score: float | None = None,
    ) -> list[Hit]:
        query = query.strip()
        if not query:
            raise ToolError("'query' est vide.")
        await self.start()
        index = await asyncio.to_thread(self._current_index)
        pool = selected(index, clean_project(project), clean_tags(tags))
        if not pool:
            return []
        embedded = await self._model(self.models.embedder.embed_query, query)
        candidates = fused(embedded, pool, self._retrieval)
        scores = await self._model(
            self.models.reranker.score, query, [passage(chunk) for chunk in candidates]
        )
        return hits(candidates, scores, k, self._retrieval, min_score=min_score)

    async def get(self, doc_id: str, *, project: str | None) -> Document:
        await self.start()
        doc = await asyncio.to_thread(self.store.get, doc_id.strip())
        if doc is None or (project is not None and doc.project != project):
            where = f" dans le projet {project!r}" if project is not None else ""
            raise ToolError(f"Document inconnu{where} : {doc_id}.")
        return doc

    async def list_docs(self, *, project: str | None, n: int = 20) -> list[DocSummary]:
        await self.start()
        docs = await asyncio.to_thread(self.store.list_documents, clean_project(project), n)
        return [DocSummary.of(doc) for doc in docs]

    async def projects(self) -> dict[str, int]:
        await self.start()
        return await asyncio.to_thread(self.store.project_counts)

    # --- Écriture ---------------------------------------------------------------------

    async def add(
        self,
        extracted: Extracted,
        *,
        kind: SourceKind,
        source: str | None,
        project: str | None,
        tags: Sequence[str] = (),
    ) -> IngestResult:
        """Range un texte. Une source déjà connue du projet est remplacée, pas doublée."""
        title = extracted.title.strip()
        if len(title) < MIN_TITLE_CHARS:
            raise ToolError(f"Titre trop court : au moins {MIN_TITLE_CHARS} caractères.")
        text = extracted.text.strip()
        if not text:
            raise ToolError("Contenu vide : rien à mémoriser.")
        place = clean_project(project)
        if place is None:
            raise ToolError("'project' est obligatoire pour mémoriser.")
        await self.start()
        replaced = (
            None
            if source is None
            else await asyncio.to_thread(self.store.find_by_source, source, place)
        )
        digest = content_hash(text)
        same = await asyncio.to_thread(self.store.find_by_hash, digest, place)
        if same is not None:
            # Déjà là (le même texte, ou la même source inchangée) : rien à écrire.
            return IngestResult(
                doc_id=same.doc_id,
                title=same.title,
                project=same.project,
                chunks=0,
                duplicate_of=same.doc_id,
            )
        cleaned = clean_tags(tags)
        doc = Document(
            doc_id=replaced.doc_id if replaced else new_doc_id(),
            title=title,
            project=place,
            tags=list(cleaned) or (list(replaced.tags) if replaced else []),
            source_kind=kind,
            source=source,
            added_at=replaced.added_at if replaced else utc_now_iso(),
            updated_at=utc_now_iso() if replaced else None,
            content_hash=digest,
            text=text,
        )
        chunks = await self.index_document(doc)
        return IngestResult(
            doc_id=doc.doc_id,
            title=doc.title,
            project=doc.project,
            chunks=chunks,
            updated=replaced is not None,
        )

    async def update(self, doc_id: str, text: str, *, project: str | None) -> IngestResult:
        """Remplace le texte d'un document : même identifiant, titre, projet et tags."""
        old = await self.get(doc_id, project=project)
        text = text.strip()
        if not text:
            raise ToolError("Contenu vide : 'delete' supprime un document.")
        digest = content_hash(text)
        same = await asyncio.to_thread(self.store.find_by_hash, digest, old.project)
        if same is not None and same.doc_id != old.doc_id:
            raise ToolError(
                f"Ce texte est déjà celui du document {same.doc_id} (« {same.title} ») : "
                f"{old.doc_id} n'a pas été modifié."
            )
        if same is not None:
            return IngestResult(
                doc_id=old.doc_id,
                title=old.title,
                project=old.project,
                chunks=0,
                duplicate_of=old.doc_id,
            )
        doc = old.model_copy(
            update={"text": text, "content_hash": digest, "updated_at": utc_now_iso()}
        )
        chunks = await self.index_document(doc)
        return IngestResult(
            doc_id=doc.doc_id, title=doc.title, project=doc.project, chunks=chunks, updated=True
        )

    async def delete(self, doc_id: str, *, project: str | None) -> DocSummary:
        doc = await self.get(doc_id, project=project)
        await asyncio.to_thread(self.store.delete, doc.doc_id)
        return DocSummary.of(doc)

    async def index_document(self, doc: Document) -> int:
        """Découpe, vectorise et écrit le document ; rend le nombre de passages."""
        chunks = chunk_document(doc.doc_id, doc.title, doc.text, self._chunking)
        embeddings = await self._model(
            self.models.embedder.embed_documents, [chunk.embed_text for chunk in chunks]
        )
        try:
            await asyncio.to_thread(self.store.write, doc, chunks, embeddings)
        except sqlite3.IntegrityError as exc:
            # Un autre appel vient d'écrire le même contenu ou la même source.
            raise ToolError(f"Écriture concurrente du même document ({exc}) : réessayer.") from exc
        return len(chunks)

    # --- Interne ----------------------------------------------------------------------

    def _current_index(self) -> list[IndexedChunk]:
        """Les passages de la base, relus seulement si une écriture a eu lieu depuis."""
        with self._index_lock:
            generation = self.store.generation()
            if self._index is None or self._index[0] != generation:
                self._index = self.store.index()
            return self._index[1]

    async def _model[**P, R](self, fn: Callable[P, R], *args: P.args, **kwargs: P.kwargs) -> R:
        """Un calcul de modèle, hors de la boucle ; son échec dit au modèle, sans trace."""
        try:
            return await asyncio.to_thread(fn, *args, **kwargs)
        except ModelError as exc:
            raise ToolError(str(exc)) from exc


def clean_project(project: str | None) -> str | None:
    found = (project or "").strip().lower()
    return found or None


def clean_tags(tags: Sequence[str] | None) -> tuple[str, ...]:
    seen: dict[str, None] = {}
    for tag in tags or ():
        cleaned = tag.strip().lower()
        if cleaned:
            seen[cleaned] = None
    return tuple(seen)
