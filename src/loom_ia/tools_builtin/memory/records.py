# SPDX-License-Identifier: Apache-2.0
"""Ce que la mémoire range et rend : documents, passages, résultats.

``Document`` a les champs d'un export de loom-notes (une ligne JSON par
document) : un tel export se réimporte tel quel avec ``loom memory import``.
"""

import uuid
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

type SourceKind = Literal["text", "url", "file"]


def utc_now_iso() -> str:
    # Microsecondes conservées : added_at sert de clé de tri, deux ajouts dans la même
    # seconde doivent rester ordonnés.
    return datetime.now(UTC).isoformat()


def new_doc_id() -> str:
    return str(uuid.uuid4())


class Document(BaseModel):
    """Un document de la mémoire : son texte intégral et ses métadonnées."""

    model_config = ConfigDict(frozen=True)

    doc_id: str
    title: str
    project: str
    tags: list[str] = Field(default_factory=list[str])
    source_kind: SourceKind
    source: str | None = None
    added_at: str = Field(default_factory=utc_now_iso)
    updated_at: str | None = None
    content_hash: str
    text: str


class Chunk(BaseModel):
    """Un passage d'un document, tel qu'il est vectorisé."""

    model_config = ConfigDict(frozen=True)

    doc_id: str
    index: int
    heading_path: str
    text: str

    @property
    def embed_text(self) -> str:
        """Texte réellement vectorisé : le chemin de titres préfixe le contenu."""
        return f"{self.heading_path}\n\n{self.text}" if self.heading_path else self.text


class DocSummary(BaseModel):
    """Un document sans son texte."""

    doc_id: str
    title: str
    project: str
    tags: list[str]
    source_kind: SourceKind
    source: str | None
    added_at: str
    updated_at: str | None
    chars: int

    @classmethod
    def of(cls, doc: Document) -> DocSummary:
        return cls(
            doc_id=doc.doc_id,
            title=doc.title,
            project=doc.project,
            tags=list(doc.tags),
            source_kind=doc.source_kind,
            source=doc.source,
            added_at=doc.added_at,
            updated_at=doc.updated_at,
            chars=len(doc.text),
        )


class Hit(BaseModel):
    """Un passage trouvé par ``search``."""

    doc_id: str
    title: str
    project: str
    score: float
    snippet: str = Field(
        description="Le passage entier s'il est court, sinon un extrait (voir `truncated`)."
    )
    truncated: bool = Field(description="Vrai si `snippet` est un extrait ; `get` donne le reste.")
    heading_path: str
    source: str | None
    added_at: str


class IngestResult(BaseModel):
    """Ce qu'un ajout ou une mise à jour a réellement fait."""

    doc_id: str
    title: str
    project: str
    chunks: int
    updated: bool = Field(default=False, description="Vrai si un document existant a été remplacé.")
    duplicate_of: str | None = Field(
        default=None, description="doc_id d'un document au contenu identique ; rien n'a été écrit."
    )
