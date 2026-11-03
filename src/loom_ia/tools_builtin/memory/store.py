# SPDX-License-Identifier: Apache-2.0
"""La base de la mémoire : un fichier SQLite, la bibliothèque standard, rien d'autre.

Quatre tables :

- ``documents`` : texte intégral et métadonnées, la source de vérité ; un même
  contenu (``content_hash``) et une même source n'y figurent qu'une fois par projet ;
- ``chunks`` : les passages et leurs vecteurs (dense en float32, lexical en
  paires indice/poids), reconstruits depuis ``documents`` par ``reindex`` ;
- ``doc_tags`` : les tags, pour filtrer ;
- ``meta`` : le modèle qui a indexé la base, sa dimension, la version du
  schéma et une génération que chaque écriture incrémente.

Un document et ses passages s'écrivent dans **une** transaction : un arrêt en
cours de route ne laisse rien à moitié. WAL et ``busy_timeout`` : plusieurs
process (workers, ``serve --reload``, la CLI) partagent la base. Une
connexion par opération, ouverte et fermée dans le thread qui l'utilise.
"""

import sqlite3
import time
from array import array
from collections.abc import Callable, Generator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from loom_ia.tools_builtin.memory.models import Embedding
from loom_ia.tools_builtin.memory.records import Chunk, Document, SourceKind

SCHEMA_VERSION: Final = "1"
BUSY_TIMEOUT_S: Final = 10.0
# Un coup d'œil avant un appel tourne dans la boucle du moteur : il n'attend pas un verrou.
PEEK_TIMEOUT_S: Final = 0.5

# Attentes entre deux essais du passage en WAL, en secondes : de 10 ms à 200 ms.
_WAL_FIRST_WAIT: Final = 0.01
_WAL_LONGEST_WAIT: Final = 0.2
_BUSY: Final = frozenset({sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED})


def use_wal(connection: sqlite3.Connection, timeout: float = BUSY_TIMEOUT_S) -> None:
    """Passe la base en WAL, en réessayant tant qu'elle est occupée, au plus ``timeout``.

    Le passage demande un verrou exclusif. Quand plusieurs connexions ouvrent en
    même temps une base neuve, SQLite peut répondre « occupée » sur-le-champ, sans
    attendre ``busy_timeout`` (il y voit un interblocage) : l'essai est refait.
    Une base déjà en WAL le reste, sans verrou.
    """
    deadline = time.monotonic() + timeout
    wait = _WAL_FIRST_WAIT
    while True:
        try:
            connection.execute("PRAGMA journal_mode = WAL")
            return
        except sqlite3.OperationalError as exc:
            if exc.sqlite_errorcode not in _BUSY or time.monotonic() >= deadline:
                raise
        time.sleep(wait)
        wait = min(wait * 2, _WAL_LONGEST_WAIT)


_DDL: Final = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS documents (
    doc_id TEXT PRIMARY KEY,
    project TEXT NOT NULL,
    title TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    source TEXT,
    added_at TEXT NOT NULL,
    updated_at TEXT,
    content_hash TEXT NOT NULL,
    text TEXT NOT NULL,
    UNIQUE (project, content_hash),
    UNIQUE (project, source)
);
CREATE INDEX IF NOT EXISTS documents_by_project ON documents (project, added_at);
CREATE TABLE IF NOT EXISTS doc_tags (
    doc_id TEXT NOT NULL REFERENCES documents (doc_id) ON DELETE CASCADE,
    tag TEXT NOT NULL,
    position INTEGER NOT NULL,
    PRIMARY KEY (doc_id, tag)
);
CREATE TABLE IF NOT EXISTS chunks (
    doc_id TEXT NOT NULL REFERENCES documents (doc_id) ON DELETE CASCADE,
    idx INTEGER NOT NULL,
    heading_path TEXT NOT NULL,
    text TEXT NOT NULL,
    dense BLOB NOT NULL,
    sparse BLOB NOT NULL,
    PRIMARY KEY (doc_id, idx)
);
"""
_DOCUMENT_COLUMNS: Final = (
    "doc_id, project, title, source_kind, source, added_at, updated_at, content_hash, text"
)


class ModelMismatchError(Exception):
    """La base a été indexée avec un autre modèle que celui de la config."""


@dataclass(frozen=True, slots=True)
class Peeked:
    """Ce qu'un coup d'œil à la base dit d'un document (``MemoryStore.peek``)."""

    doc: Document | None
    # Dans le projet de ``doc``, le document qui a déjà le contenu cherché.
    same: Document | None = None


@dataclass(frozen=True, slots=True)
class IndexedChunk:
    """Un passage tel que la recherche le compare : ses vecteurs et ce qu'il faut pour le rendre."""

    doc_id: str
    index: int
    heading_path: str
    text: str
    title: str
    project: str
    tags: frozenset[str]
    source: str | None
    added_at: str
    dense: array[float]
    sparse: Mapping[int, float]


# --- Vecteurs en octets ------------------------------------------------------------


def pack_dense(values: Sequence[float]) -> bytes:
    return array("f", values).tobytes()


def unpack_dense(blob: bytes) -> array[float]:
    found = array("f")
    found.frombytes(blob)
    return found


def pack_sparse(indices: Sequence[int], values: Sequence[float]) -> bytes:
    if len(indices) != len(values):
        raise ValueError("indices et poids de longueurs différentes")
    return array("I", indices).tobytes() + array("f", values).tobytes()


def unpack_sparse(blob: bytes) -> dict[int, float]:
    count = len(blob) // 8
    indices = array("I")
    indices.frombytes(blob[: count * 4])
    values = array("f")
    values.frombytes(blob[count * 4 :])
    return dict(zip(indices, values, strict=True))


# --- La base -------------------------------------------------------------------------


class MemoryStore:
    """Un fichier SQLite de mémoire. Toutes les méthodes sont synchrones : à appeler hors boucle."""

    def __init__(self, path: Path) -> None:
        self.path = path

    @contextmanager
    def _connection(self) -> Generator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=BUSY_TIMEOUT_S, isolation_level=None)
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            yield connection
        finally:
            connection.close()

    @contextmanager
    def _writing(self) -> Generator[sqlite3.Connection]:
        """Une transaction d'écriture : tout ou rien, la génération incrémentée."""
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
                connection.execute(
                    "UPDATE meta SET value = CAST(value AS INTEGER) + 1 WHERE key = 'generation'"
                )
                connection.execute("COMMIT")
            except BaseException:
                connection.execute("ROLLBACK")
                raise

    # --- Schéma -------------------------------------------------------------------

    def ensure(self, model: str, dim: int, *, check: bool = True) -> None:
        """Crée le schéma s'il manque ; vérifie que la base a été indexée avec ce modèle.

        ``check=False`` : seulement pour ``reindex``, qui remplace l'index.
        """
        with self._connection() as connection:
            use_wal(connection)
            connection.execute("BEGIN IMMEDIATE")
            try:
                for statement in _DDL.split(";"):
                    if statement.strip():
                        connection.execute(statement)
                meta = dict(connection.execute("SELECT key, value FROM meta").fetchall())
                if "schema" in meta and meta["schema"] != SCHEMA_VERSION:
                    raise ModelMismatchError(
                        f"{self.path} : schéma de mémoire {meta['schema']}, cette version de "
                        f"loom-ia lit le schéma {SCHEMA_VERSION}"
                    )
                indexed = (meta.get("embedder"), meta.get("dim"))
                if check and indexed[0] is not None and indexed != (model, str(dim)):
                    raise ModelMismatchError(
                        f"{self.path} a été indexée avec {indexed[0]} ({indexed[1]}), la config "
                        f"demande {model} ({dim}) : lancer « loom memory reindex »"
                    )
                defaults = {"schema": SCHEMA_VERSION, "generation": "0"}
                if indexed[0] is None:
                    defaults |= {"embedder": model, "dim": str(dim)}
                connection.executemany(
                    "INSERT OR IGNORE INTO meta (key, value) VALUES (?, ?)", defaults.items()
                )
                connection.execute("COMMIT")
            except BaseException:
                connection.execute("ROLLBACK")
                raise

    # --- Lecture --------------------------------------------------------------------

    def generation(self) -> int:
        with self._connection() as connection:
            row = connection.execute("SELECT value FROM meta WHERE key = 'generation'").fetchone()
        return int(row[0]) if row else 0

    def get(self, doc_id: str) -> Document | None:
        return self._one("doc_id = ?", (doc_id,))

    def find_by_hash(self, content_hash: str, project: str) -> Document | None:
        return self._one("content_hash = ? AND project = ?", (content_hash, project))

    def find_by_source(self, source: str, project: str) -> Document | None:
        return self._one("source = ? AND project = ?", (source, project))

    def list_documents(self, project: str | None, limit: int) -> list[Document]:
        where, args = ("project = ?", (project,)) if project is not None else ("1", ())
        return self._many(f"{where} ORDER BY added_at DESC LIMIT ?", (*args, limit))

    def documents(self) -> list[Document]:
        """Tous les documents, du plus ancien au plus récent : l'export, la réindexation."""
        return self._many("1 ORDER BY added_at, doc_id", ())

    def project_counts(self) -> dict[str, int]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT project, count(*) FROM documents GROUP BY project ORDER BY project"
            ).fetchall()
        return {str(project): int(count) for project, count in rows}

    def count(self) -> tuple[int, int]:
        """Nombre de documents et de passages."""
        with self._connection() as connection:
            documents = connection.execute("SELECT count(*) FROM documents").fetchone()[0]
            chunks = connection.execute("SELECT count(*) FROM chunks").fetchone()[0]
        return int(documents), int(chunks)

    def index(self) -> tuple[int, list[IndexedChunk]]:
        """La génération et tous les passages, lus dans une même transaction."""
        with self._connection() as connection:
            connection.execute("BEGIN")
            try:
                row = connection.execute(
                    "SELECT value FROM meta WHERE key = 'generation'"
                ).fetchone()
                generation = int(row[0]) if row else 0
                tags: dict[str, set[str]] = {}
                for doc_id, tag in connection.execute("SELECT doc_id, tag FROM doc_tags"):
                    tags.setdefault(str(doc_id), set()).add(str(tag))
                chunks = [
                    IndexedChunk(
                        doc_id=str(doc_id),
                        index=int(idx),
                        heading_path=str(heading_path),
                        text=str(text),
                        title=str(title),
                        project=str(project),
                        tags=frozenset(tags.get(str(doc_id), ())),
                        source=None if source is None else str(source),
                        added_at=str(added_at),
                        dense=unpack_dense(bytes(dense)),
                        sparse=unpack_sparse(bytes(sparse)),
                    )
                    for (
                        doc_id,
                        idx,
                        heading_path,
                        text,
                        dense,
                        sparse,
                        title,
                        project,
                        source,
                        added_at,
                    ) in connection.execute(
                        "SELECT c.doc_id, c.idx, c.heading_path, c.text, c.dense, c.sparse, "
                        "d.title, d.project, d.source, d.added_at "
                        "FROM chunks c JOIN documents d ON d.doc_id = c.doc_id "
                        "ORDER BY c.doc_id, c.idx"
                    )
                ]
            finally:
                connection.execute("COMMIT")
        return generation, chunks

    def _one(self, where: str, args: tuple[object, ...]) -> Document | None:
        found = self._many(f"{where} LIMIT 1", args)
        return found[0] if found else None

    def _many(self, where: str, args: tuple[object, ...]) -> list[Document]:
        with self._connection() as connection:
            return _documents(connection, where, args)

    # --- Coup d'œil, avant un appel --------------------------------------------------

    def peek(self, doc_id: str, content_hash: str | None = None) -> Peeked | None:
        """Le document ``doc_id``, et celui qui a déjà ``content_hash`` dans son projet.

        Pour juger un appel avant de le soumettre à un humain. En lecture seule : la
        base n'est jamais créée ni modifiée, et une base absente n'a aucun document.
        ``None`` quand la base ne peut pas être lue (verrou au-delà de
        ``PEEK_TIMEOUT_S``, schéma absent, fichier abîmé) : on ne sait pas.
        """

        def look(connection: sqlite3.Connection) -> Peeked:
            found = _documents(connection, "doc_id = ? LIMIT 1", (doc_id,))
            doc = found[0] if found else None
            if doc is None or content_hash is None:
                return Peeked(doc=doc)
            return Peeked(doc=doc, same=_with_content(connection, content_hash, doc.project))

        return self._read_only(look)

    def peek_content(self, content_hash: str, project: str) -> Peeked | None:
        """Le document de ``project`` qui a déjà ce contenu (``same``) ; comme ``peek``."""

        def look(connection: sqlite3.Connection) -> Peeked:
            return Peeked(doc=None, same=_with_content(connection, content_hash, project))

        return self._read_only(look)

    def _read_only(self, look: Callable[[sqlite3.Connection], Peeked]) -> Peeked | None:
        if not self.path.exists():
            return Peeked(doc=None)
        try:
            connection = sqlite3.connect(
                f"{self.path.resolve().as_uri()}?mode=ro", uri=True, timeout=PEEK_TIMEOUT_S
            )
        except sqlite3.Error:
            return None
        try:
            return look(connection)
        except sqlite3.Error:
            return None
        finally:
            connection.close()

    # --- Écriture -------------------------------------------------------------------

    def write(
        self, doc: Document, chunks: Sequence[Chunk], embeddings: Sequence[Embedding]
    ) -> None:
        """Écrit le document et ses passages (ceux d'avant remplacés), en une transaction."""
        if len(chunks) != len(embeddings):
            raise ValueError("passages et vecteurs de longueurs différentes")
        with self._writing() as connection:
            connection.execute(
                f"INSERT INTO documents ({_DOCUMENT_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (doc_id) DO UPDATE SET project = excluded.project, "
                "title = excluded.title, source_kind = excluded.source_kind, "
                "source = excluded.source, added_at = excluded.added_at, "
                "updated_at = excluded.updated_at, content_hash = excluded.content_hash, "
                "text = excluded.text",
                (
                    doc.doc_id,
                    doc.project,
                    doc.title,
                    doc.source_kind,
                    doc.source,
                    doc.added_at,
                    doc.updated_at,
                    doc.content_hash,
                    doc.text,
                ),
            )
            connection.execute("DELETE FROM doc_tags WHERE doc_id = ?", (doc.doc_id,))
            connection.executemany(
                "INSERT INTO doc_tags (doc_id, tag, position) VALUES (?, ?, ?)",
                [(doc.doc_id, tag, position) for position, tag in enumerate(doc.tags)],
            )
            connection.execute("DELETE FROM chunks WHERE doc_id = ?", (doc.doc_id,))
            connection.executemany(
                "INSERT INTO chunks (doc_id, idx, heading_path, text, dense, sparse) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (
                        doc.doc_id,
                        chunk.index,
                        chunk.heading_path,
                        chunk.text,
                        pack_dense(embedding.dense),
                        pack_sparse(embedding.sparse.indices, embedding.sparse.values),
                    )
                    for chunk, embedding in zip(chunks, embeddings, strict=True)
                ],
            )

    def delete(self, doc_id: str) -> bool:
        """Supprime le document et ses passages ; faux s'il n'existait pas."""
        with self._writing() as connection:
            cursor = connection.execute("DELETE FROM documents WHERE doc_id = ?", (doc_id,))
            return cursor.rowcount > 0

    def reset_index(self, model: str, dim: int) -> None:
        """Vide les passages et inscrit le nouveau modèle : la réindexation suit."""
        with self._writing() as connection:
            connection.execute("DELETE FROM chunks")
            connection.executemany(
                "INSERT INTO meta (key, value) VALUES (?, ?) "
                "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                [("embedder", model), ("dim", str(dim))],
            )


def _documents(
    connection: sqlite3.Connection, where: str, args: tuple[object, ...]
) -> list[Document]:
    rows = connection.execute(
        f"SELECT {_DOCUMENT_COLUMNS} FROM documents WHERE {where}", args
    ).fetchall()
    documents: list[Document] = []
    for row in rows:
        tags = [
            str(tag)
            for (tag,) in connection.execute(
                "SELECT tag FROM doc_tags WHERE doc_id = ? ORDER BY position", (row[0],)
            )
        ]
        documents.append(_document(row, tags))
    return documents


def _with_content(
    connection: sqlite3.Connection, content_hash: str, project: str
) -> Document | None:
    found = _documents(
        connection, "content_hash = ? AND project = ? LIMIT 1", (content_hash, project)
    )
    return found[0] if found else None


def _document(row: Sequence[object], tags: list[str]) -> Document:
    doc_id, project, title, source_kind, source, added_at, updated_at, digest, text = row
    kind: SourceKind = (
        "url" if source_kind == "url" else "file" if source_kind == "file" else "text"
    )
    return Document(
        doc_id=str(doc_id),
        title=str(title),
        project=str(project),
        tags=tags,
        source_kind=kind,
        source=None if source is None else str(source),
        added_at=str(added_at),
        updated_at=None if updated_at is None else str(updated_at),
        content_hash=str(digest),
        text=str(text),
    )
