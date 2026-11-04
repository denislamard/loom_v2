# SPDX-License-Identifier: Apache-2.0
"""Mémoire (``loom.memory``) : opérations, base SQLite, garanties d'écriture.

Modèles factices, base dans un dossier temporaire : ni torch ni réseau.
"""

import asyncio
import sqlite3
import threading
import time
from collections.abc import Sequence
from pathlib import Path
from typing import cast

import pytest

import loom_ia.tools_builtin.memory.store as store_module
from loom_ia.core.ports import ToolError
from loom_ia.tools_builtin.memory.fake import FakeEmbedder, FakeReranker
from loom_ia.tools_builtin.memory.models import (
    Embedding,
    LazyModel,
    Models,
    checkpoint,
    shared_models,
)
from loom_ia.tools_builtin.memory.service import Extracted, MemoryService
from loom_ia.tools_builtin.memory.store import MemoryStore
from loom_ia.tools_builtin.memory_params import ChunkingSpec, ModelsSpec, RetrievalSpec

NOTE = "Le devis D-2026-042 de Mme Martin porte sur un chauffe-eau de 200 L."


def service_on(path: Path, **chunking: int) -> MemoryService:
    return MemoryService(
        MemoryStore(path),
        shared_models(ModelsSpec(kind="fake")),
        RetrievalSpec(),
        ChunkingSpec(**chunking),
    )


@pytest.fixture
def memory(tmp_path: Path) -> MemoryService:
    return service_on(tmp_path / "memoire.db")


async def add(memory: MemoryService, text: str, title: str, project: str = "dupont") -> str:
    done = await memory.add(Extracted(title, text), kind="text", source=None, project=project)
    return done.doc_id


async def test_a_note_is_found_by_its_words_within_its_project(memory: MemoryService) -> None:
    doc_id = await add(memory, NOTE, "Devis Martin")
    await add(memory, "Le carrelage de la salle de bains est posé.", "Chantier", project="autre")
    found = await memory.search("chauffe-eau Martin", project="dupont")
    assert [hit.doc_id for hit in found] == [doc_id]
    assert found[0].snippet == NOTE
    assert not found[0].truncated
    everywhere = await memory.search("chauffe-eau carrelage", project=None)
    assert {hit.project for hit in everywhere} == {"dupont", "autre"}


async def test_tags_filter_the_search(memory: MemoryService) -> None:
    first = await memory.add(
        Extracted("Devis", NOTE), kind="text", source=None, project="p", tags=["Devis", " 2026 "]
    )
    await memory.add(Extracted("Autre", NOTE + " Bis."), kind="text", source=None, project="p")
    found = await memory.search("chauffe-eau", project="p", tags=["devis"])
    assert [hit.doc_id for hit in found] == [first.doc_id]
    doc = await memory.get(first.doc_id, project="p")
    assert doc.tags == ["devis", "2026"]


async def test_the_same_text_is_kept_once_per_project(memory: MemoryService) -> None:
    doc_id = await add(memory, NOTE, "Devis Martin")
    again = await memory.add(
        Extracted(
            "Autre titre", "  le DEVIS d-2026-042 de mme martin porte sur un chauffe-eau de 200 l. "
        ),
        kind="text",
        source=None,
        project="dupont",
    )
    assert again.duplicate_of == doc_id
    assert again.chunks == 0
    elsewhere = await add(memory, NOTE, "Devis Martin", project="martin")
    assert elsewhere != doc_id
    assert await memory.projects() == {"dupont": 1, "martin": 1}


async def test_a_source_is_one_document_per_project_and_never_moves(
    memory: MemoryService,
) -> None:
    first = await memory.add(
        Extracted("Page", "Version 1."), kind="url", source="https://x.fr/p", project="a"
    )
    again = await memory.add(
        Extracted("Page", "Version 2."), kind="url", source="https://x.fr/p", project="a"
    )
    assert again.doc_id == first.doc_id
    assert again.updated
    other = await memory.add(
        Extracted("Page", "Version 3."), kind="url", source="https://x.fr/p", project="b"
    )
    # Dans un autre projet, la même source est un autre document : celui de « a » reste.
    assert other.doc_id != first.doc_id
    assert (await memory.get(first.doc_id, project="a")).text == "Version 2."
    assert (await memory.get(other.doc_id, project="b")).text == "Version 3."


async def test_update_keeps_the_identity_and_reindexes(memory: MemoryService) -> None:
    doc_id = await add(memory, NOTE, "Devis Martin")
    done = await memory.update(doc_id, "Le chantier Dupont commence lundi.", project=None)
    assert done.updated and done.doc_id == doc_id
    doc = await memory.get(doc_id, project=None)
    assert doc.title == "Devis Martin"
    assert doc.updated_at is not None
    assert [hit.doc_id for hit in await memory.search("chantier lundi", project=None)] == [doc_id]
    assert await memory.search("chauffe-eau", project=None) == []


async def test_update_refuses_the_text_of_another_document(memory: MemoryService) -> None:
    first = await add(memory, NOTE, "Devis Martin")
    second = await add(memory, "Une autre note.", "Autre")
    with pytest.raises(ToolError, match=f"déjà celui du document {first}"):
        await memory.update(second, NOTE, project=None)
    assert (await memory.get(second, project=None)).text == "Une autre note."


async def test_update_with_the_same_text_writes_nothing(memory: MemoryService) -> None:
    doc_id = await add(memory, NOTE, "Devis Martin")
    done = await memory.update(doc_id, NOTE.upper(), project=None)
    assert done.duplicate_of == doc_id and not done.updated
    assert (await memory.get(doc_id, project=None)).updated_at is None


async def test_a_shorter_rewrite_leaves_no_stale_passage(tmp_path: Path) -> None:
    memory = service_on(tmp_path / "m.db", target_chars=200, max_chars=200, overlap_chars=0)
    long_text = "\n\n".join(f"Paragraphe {i} " + "mot " * 40 for i in range(6))
    doc_id = await add(memory, long_text, "Long")
    assert memory.store.count()[1] > 1
    await memory.update(doc_id, "Court.", project=None)
    assert memory.store.count() == (1, 1)


async def test_a_failed_write_leaves_nothing_behind(
    memory: MemoryService, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = 0
    real = store_module.pack_sparse

    def failing(indices: list[int], values: list[float]) -> bytes:
        nonlocal calls
        calls += 1
        if calls > 1:
            raise RuntimeError("disque plein")
        return real(indices, values)

    monkeypatch.setattr(store_module, "pack_sparse", failing)
    text = "\n\n".join(f"## Partie {i}\n\n" + "mot " * 80 for i in range(3))
    with pytest.raises(RuntimeError, match="disque plein"):
        await add(memory, text, "Trois parties")
    assert memory.store.count() == (0, 0)


async def test_delete_removes_the_document_and_its_passages(memory: MemoryService) -> None:
    doc_id = await add(memory, NOTE, "Devis Martin")
    gone = await memory.delete(doc_id, project=None)
    assert gone.doc_id == doc_id and gone.title == "Devis Martin"
    assert memory.store.count() == (0, 0)
    with pytest.raises(ToolError, match="Document inconnu"):
        await memory.delete(doc_id, project=None)


async def test_an_imposed_project_hides_the_others(memory: MemoryService) -> None:
    doc_id = await add(memory, NOTE, "Devis Martin", project="desktop")
    for call in (
        memory.get(doc_id, project="documentation"),
        memory.update(doc_id, "Effacé.", project="documentation"),
        memory.delete(doc_id, project="documentation"),
    ):
        with pytest.raises(ToolError, match="Document inconnu dans le projet 'documentation'"):
            await call
    assert (await memory.get(doc_id, project="desktop")).text == NOTE


async def test_what_is_refused_before_writing(memory: MemoryService) -> None:
    with pytest.raises(ToolError, match="Titre trop court"):
        await add(memory, NOTE, "ab")
    with pytest.raises(ToolError, match="Contenu vide"):
        await add(memory, "   ", "Vide")
    with pytest.raises(ToolError, match="'project' est obligatoire"):
        await memory.add(Extracted("Titre", NOTE), kind="text", source=None, project=" ")
    with pytest.raises(ToolError, match="'query' est vide"):
        await memory.search("  ", project=None)
    # Refusé avant tout accès : la base n'a même pas été créée.
    assert not memory.store.path.exists()


async def test_list_docs_newest_first(memory: MemoryService) -> None:
    first = await add(memory, "Premier.", "Premier")
    second = await add(memory, "Second.", "Second")
    listed = await memory.list_docs(project=None, n=10)
    assert [d.doc_id for d in listed] == [second, first]
    assert listed[0].chars == len("Second.")
    assert [d.doc_id for d in await memory.list_docs(project=None, n=1)] == [second]


async def test_a_long_passage_is_returned_as_a_snippet(tmp_path: Path) -> None:
    memory = MemoryService(
        MemoryStore(tmp_path / "m.db"),
        shared_models(ModelsSpec(kind="fake")),
        RetrievalSpec(min_score=0.0, full_chunk_chars=50, snippet_chars=40),
        ChunkingSpec(),
    )
    await add(memory, "Chauffe-eau " + "très long texte " * 20, "Long")
    [hit] = await memory.search("chauffe-eau", project=None)
    assert hit.truncated
    assert hit.snippet.endswith("…") and len(hit.snippet) <= 41


async def test_another_process_writing_refreshes_the_index(tmp_path: Path) -> None:
    path = tmp_path / "partagee.db"
    reader, writer = service_on(path), service_on(path)
    await add(writer, NOTE, "Devis Martin")
    assert len(await reader.search("chauffe-eau", project=None)) == 1
    other = await add(writer, "Un chauffe-eau neuf chez Dupont.", "Dupont")
    assert other in {hit.doc_id for hit in await reader.search("chauffe-eau", project=None)}


async def test_concurrent_writes_on_one_file_all_land(tmp_path: Path) -> None:
    path = tmp_path / "concurrente.db"
    services = [service_on(path) for _ in range(4)]
    await asyncio.gather(
        *(add(service, f"Note numéro {i}.", f"Note {i}") for i, service in enumerate(services))
    )
    assert services[0].store.count() == (4, 4)


class Contended:
    """Une connexion dont le passage en WAL est refusé ``busy`` fois, puis accepté."""

    def __init__(self, busy: int, code: int = sqlite3.SQLITE_BUSY) -> None:
        self.busy = busy
        self.code = code
        self.tries = 0

    def execute(self, statement: str) -> None:
        assert statement == "PRAGMA journal_mode = WAL"
        self.tries += 1
        if self.tries <= self.busy:
            error = sqlite3.OperationalError("database is locked")
            error.sqlite_errorcode = self.code
            raise error


def test_switching_to_wal_is_retried_while_the_base_is_busy() -> None:
    """Une base neuve ouverte par plusieurs connexions : « occupée » sur-le-champ, puis libre."""
    connection = Contended(busy=3)
    store_module.use_wal(cast(sqlite3.Connection, connection))
    assert connection.tries == 4


def test_switching_to_wal_gives_up_after_the_timeout_or_on_another_error() -> None:
    busy = Contended(busy=1_000)
    with pytest.raises(sqlite3.OperationalError, match="locked"):
        store_module.use_wal(cast(sqlite3.Connection, busy), timeout=0.05)
    assert busy.tries > 1
    broken = Contended(busy=1, code=sqlite3.SQLITE_IOERR)
    with pytest.raises(sqlite3.OperationalError):
        store_module.use_wal(cast(sqlite3.Connection, broken))
    assert broken.tries == 1


async def test_a_base_indexed_by_another_model_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "m.db"
    await add(service_on(path), NOTE, "Devis Martin")
    connection = sqlite3.connect(path)
    with connection:
        connection.execute("UPDATE meta SET value = 'autre-modele' WHERE key = 'embedder'")
    connection.close()
    with pytest.raises(ToolError, match=r"indexée avec autre-modele.*loom memory reindex"):
        await service_on(path).search("chauffe-eau", project=None)


# --- Calcul abandonné -----------------------------------------------------------------


class SlowEmbedder(FakeEmbedder):
    """Un texte toutes les 50 ms, sous un seul verrou, comme un vrai modèle sur CPU."""

    def __init__(self) -> None:
        super().__init__()
        self.lazy = LazyModel[FakeEmbedder]("lent", lambda: "cpu", lambda _: FakeEmbedder(), id)
        self.computed = 0

    def embed_documents(
        self, texts: Sequence[str], cancel: threading.Event | None = None
    ) -> list[Embedding]:
        def run(model: FakeEmbedder) -> list[Embedding]:
            found: list[Embedding] = []
            for text in texts:
                checkpoint(cancel)
                time.sleep(0.05)
                self.computed += 1
                found.append(model.embed_query(text))
            return found

        return self.lazy.run(run, cancel)

    def embed_query(self, text: str, cancel: threading.Event | None = None) -> Embedding:
        return self.lazy.run(lambda model: model.embed_query(text), cancel)


async def test_an_add_cut_by_its_timeout_frees_the_model_at_once(tmp_path: Path) -> None:
    slow = SlowEmbedder()
    memory = MemoryService(
        MemoryStore(tmp_path / "memoire.db"),
        Models(slow, FakeReranker()),
        RetrievalSpec(),
        ChunkingSpec(target_chars=200, max_chars=300, overlap_chars=0, min_chars=0),
    )
    await add(memory, NOTE, "Devis Martin")
    # Un passage par paragraphe : 100 passages, 5 s de calcul.
    long_text = "\n\n".join(f"Paragraphe {i} : " + "mot " * 40 for i in range(100))
    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.3):
            await add(memory, long_text, "Long document")
    started = time.monotonic()
    found = await memory.search("chauffe-eau de Mme Martin", project="dupont")
    # La recherche n'attend pas la fin d'un calcul que personne ne lira.
    assert time.monotonic() - started < 1.0
    assert found[0].title == "Devis Martin"
    assert slow.computed < 20
    # Et rien n'a été écrit pour l'ajout abandonné.
    assert [d.title for d in await memory.list_docs(project="dupont")] == ["Devis Martin"]
