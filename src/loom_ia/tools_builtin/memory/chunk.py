# SPDX-License-Identifier: Apache-2.0
"""Texte des documents : empreinte, découpage en passages, extraits.

Découpage structurel : par titres markdown, puis retaille des sections trop
longues en paragraphes avec chevauchement. Chaque passage connaît son chemin
de titres, qui préfixe le texte vectorisé.
"""

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from typing import Final

from loom_ia.tools_builtin.memory.records import Chunk
from loom_ia.tools_builtin.memory_params import ChunkingSpec

_WS: Final = re.compile(r"\s+")
_HEADING: Final = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_FENCE: Final = re.compile(r"^\s*(```|~~~)")
_PARA_SPLIT: Final = re.compile(r"\n\s*\n")
_SENTENCE_END: Final = re.compile(r"(?<=[.!?…])\s+")


# --- Empreinte -------------------------------------------------------------------


def normalize(text: str) -> str:
    """Le texte sans ce qui ne change pas son contenu : casse, espaces, formes Unicode."""
    text = unicodedata.normalize("NFKC", text).casefold()
    return _WS.sub(" ", text).strip()


def content_hash(text: str) -> str:
    """Empreinte du contenu, insensible à la casse et aux espaces : la déduplication."""
    return hashlib.sha256(normalize(text).encode("utf-8")).hexdigest()


# --- Extraits --------------------------------------------------------------------


def snippet(text: str, limit: int) -> str:
    """Le début du texte, sur une ligne, coupé à un espace et marqué « … »."""
    flat = " ".join(text.split())
    if len(flat) <= limit:
        return flat
    cut = flat.rfind(" ", 0, limit)
    return flat[: cut if cut > limit // 2 else limit].rstrip() + "…"


# --- Découpage -------------------------------------------------------------------


@dataclass(slots=True)
class _Section:
    heading_path: str
    body: str


def chunk_document(doc_id: str, title: str, text: str, spec: ChunkingSpec) -> list[Chunk]:
    """Les passages du document, numérotés à partir de 0."""
    pieces: list[tuple[str, str]] = []
    for section in _split_sections(title, text):
        for piece in _split_body(section.body, spec):
            pieces.append((section.heading_path, piece))
    pieces = _merge_tiny(pieces, spec)
    return [
        Chunk(doc_id=doc_id, index=i, heading_path=path, text=body)
        for i, (path, body) in enumerate(pieces)
    ]


def _split_sections(title: str, text: str) -> list[_Section]:
    """Une section par titre rencontré ; le titre du document est la racine du chemin."""
    stack: list[tuple[int, str]] = []
    sections: list[_Section] = []
    buffer: list[str] = []
    in_fence = False

    def flush() -> None:
        body = "\n".join(buffer).strip()
        if body:
            sections.append(_Section(_path(title, stack), body))
        buffer.clear()

    for line in text.splitlines():
        if _FENCE.match(line):
            in_fence = not in_fence
            buffer.append(line)
            continue
        found = None if in_fence else _HEADING.match(line)
        if found is None:
            buffer.append(line)
            continue
        flush()
        level, heading = len(found.group(1)), found.group(2).strip()
        while stack and stack[-1][0] >= level:
            stack.pop()
        # Le titre du document n'est pas répété s'il est aussi le premier H1.
        if not (level == 1 and not stack and heading == title):
            stack.append((level, heading))
    flush()
    return sections or [_Section(title, "")]


def _path(title: str, stack: list[tuple[int, str]]) -> str:
    return " > ".join([title, *(heading for _, heading in stack)])


def _split_body(body: str, spec: ChunkingSpec) -> list[str]:
    if not body:
        return []
    if len(body) <= spec.max_chars:
        return [body]
    paragraphs = [p.strip() for p in _PARA_SPLIT.split(body) if p.strip()]
    # Une unité seule doit tenir dans un passage avec le chevauchement qui la précède.
    unit_max = max(spec.target_chars - spec.overlap_chars, 1)
    units: list[str] = []
    for paragraph in paragraphs:
        units.extend(_split_long(paragraph, unit_max) if len(paragraph) > unit_max else [paragraph])

    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for unit in units:
        if current and size + len(unit) + 2 > spec.target_chars:
            chunks.append("\n\n".join(current))
            tail = _tail(chunks[-1], spec.overlap_chars)
            current = [tail] if tail else []
            size = len(tail)
        current.append(unit)
        size += len(unit) + 2
    if current:
        chunks.append("\n\n".join(current))
    return chunks


def _split_long(paragraph: str, max_chars: int) -> list[str]:
    """Un paragraphe seul plus long que ``max_chars`` : coupé aux phrases, sinon en dur."""
    out: list[str] = []
    current = ""
    for sentence in _SENTENCE_END.split(paragraph):
        if len(sentence) > max_chars:
            if current:
                out.append(current)
                current = ""
            out.extend(sentence[i : i + max_chars] for i in range(0, len(sentence), max_chars))
            continue
        if current and len(current) + len(sentence) + 1 > max_chars:
            out.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        out.append(current)
    return out


def _tail(text: str, overlap: int) -> str:
    if overlap <= 0 or len(text) <= overlap:
        return ""
    tail = text[-overlap:]
    cut = tail.find(" ")
    return tail[cut + 1 :] if cut != -1 else tail


def _merge_tiny(pieces: list[tuple[str, str]], spec: ChunkingSpec) -> list[tuple[str, str]]:
    """Absorbe les morceaux trop courts dans le précédent quand ça tient."""
    merged: list[tuple[str, str]] = []
    for path, body in pieces:
        if (
            merged
            and len(body) < spec.min_chars
            and len(merged[-1][1]) + len(body) + 2 <= spec.max_chars
        ):
            previous_path, previous_body = merged[-1]
            merged[-1] = (previous_path, f"{previous_body}\n\n{body}")
        else:
            merged.append((path, body))
    return merged
