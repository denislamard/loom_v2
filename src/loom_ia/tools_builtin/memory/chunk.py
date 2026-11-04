# SPDX-License-Identifier: Apache-2.0
"""Texte des documents : empreinte, découpage en passages, extraits.

Découpage structurel : par titres markdown, puis retaille des sections trop
longues en paragraphes avec chevauchement. Chaque passage connaît son chemin
de titres, qui préfixe le texte vectorisé.

Ce qui est fait pour ne pas couper le sens :

- un paragraphe qui annonce la suite (question de FAQ, « … : », étiquette) reste
  avec ce qu'il annonce, en fin de passage comme en fin de section ;
- un morceau trop court rejoint un voisin, et ses titres passent dans le texte ;
  le premier du document, qui n'a pas de précédent, rejoint le suivant.
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

# Un paragraphe qui annonce la suite — une question, une phrase qui finit par « : », une
# étiquette (« **Règles générales** ») — n'est jamais laissé en fin de passage ni en fin de
# section : il rejoint ce qu'il annonce. Au-delà de cette longueur, ce n'est plus une annonce.
LEAD_CHARS: Final = 200
_TRAILING_MARKS: Final = re.compile(r"[\s*_»\"')\]]+$")
_LIST_ITEM: Final = re.compile(r"^(?:[-*+]|\d+[.)])\s")
_NOT_A_LABEL: Final = frozenset(".!…;,")

type _Headings = tuple[tuple[int, str], ...]


@dataclass(slots=True)
class _Section:
    heading_path: str
    # Titres sous celui du document : (niveau, texte), du plus haut au plus bas.
    headings: _Headings
    body: str


@dataclass(frozen=True, slots=True)
class _Piece:
    heading_path: str
    headings: _Headings
    body: str


def chunk_document(doc_id: str, title: str, text: str, spec: ChunkingSpec) -> list[Chunk]:
    """Les passages du document, numérotés à partir de 0."""
    pieces = [
        _Piece(section.heading_path, section.headings, body)
        for section in _carry_leads(_split_sections(title, text))
        for body in _split_body(section.body, spec)
    ]
    return [
        Chunk(doc_id=doc_id, index=i, heading_path=piece.heading_path, text=piece.body)
        for i, piece in enumerate(_merge_tiny(pieces, spec))
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
            sections.append(_Section(_path(title, stack), tuple(stack), body))
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
    return sections or [_Section(title, (), "")]


def _path(title: str, stack: list[tuple[int, str]]) -> str:
    return " > ".join([title, *(heading for _, heading in stack)])


def announces(paragraph: str) -> bool:
    """Vrai si le paragraphe annonce ce qui le suit : question, « … : », ou étiquette.

    L'étiquette est une ligne courte sans ponctuation finale, hors élément de liste ;
    l'emphase et les guillemets qui la ferment ne comptent pas (« **Q ?** » est une
    question). Code, tableau et HTML n'annoncent rien.
    """
    said = _TRAILING_MARKS.sub("", paragraph.strip())
    if not said or len(said) > LEAD_CHARS or said.startswith(("```", "~~~", "|", "<")):
        return False
    if said[-1] in "?:":
        return True
    return "\n" not in said and not _LIST_ITEM.match(said) and said[-1] not in _NOT_A_LABEL


def _carry_leads(sections: list[_Section]) -> list[_Section]:
    """Une annonce en fin de section passe en tête de la section suivante.

    C'est l'étiquette d'une page web posée au-dessus d'un titre (« Contact »), ou la
    phrase « … : » qui introduit la section suivante. Parcouru de la fin : une annonce
    reçue n'est pas renvoyée plus loin. Une section réduite à son annonce la garde.
    """
    for i in range(len(sections) - 2, -1, -1):
        section = sections[i]
        breaks = list(_PARA_SPLIT.finditer(section.body))
        if not breaks:
            continue
        found = breaks[-1]
        rest, last = section.body[: found.start()].rstrip(), section.body[found.end() :]
        if not rest or not announces(last) or _open_fence(rest):
            continue
        section.body = rest
        following = sections[i + 1]
        following.body = f"{last.strip()}\n\n{following.body}"
    return sections


def _open_fence(text: str) -> bool:
    """Vrai si un bloc de code est ouvert à la fin du texte."""
    return sum(1 for line in text.splitlines() if _FENCE.match(line)) % 2 == 1


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
    # Vrai si ``current`` commence par le chevauchement du passage précédent : il ne
    # compte pas comme une unité du passage.
    overlapped = False
    for unit in units:
        if current and size + len(unit) + 2 > spec.target_chars:
            own = len(current) - overlapped
            if own == 1 and announces(current[-1]) and size + len(unit) + 2 <= spec.max_chars:
                # Une annonce seule garde ce qu'elle annonce, jusqu'à max_chars.
                current.append(unit)
                size += len(unit) + 2
                continue
            # L'annonce qui finirait le passage passe en tête du suivant.
            carried = [current.pop()] if own >= 2 and announces(current[-1]) else []
            chunks.append("\n\n".join(current))
            tail = _tail(chunks[-1], spec.overlap_chars)
            overlapped = bool(tail)
            current = [tail, *carried] if tail else carried
            size = sum(len(part) + 2 for part in current)
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


def _merge_tiny(pieces: list[_Piece], spec: ChunkingSpec) -> list[_Piece]:
    """Absorbe les morceaux trop courts dans un voisin, quand ça tient sous ``max_chars``.

    Un morceau court rejoint le précédent, qui garde son chemin. Le premier du
    document, qui n'en a pas, rejoint le suivant et prend son chemin, plus précis —
    si son propre chemin y est inclus (le texte d'avant le premier titre, en
    général). Les titres du morceau absorbé que l'autre n'a pas sont écrits dans le
    texte : le chemin n'en garde qu'un, et la recherche ne doit pas perdre l'autre.
    """
    merged: list[_Piece] = []
    first: _Piece | None = None
    for piece in pieces:
        if first is not None:
            joined = _joined(first, piece)
            first = None
            if len(joined) <= spec.max_chars:
                merged.append(_Piece(piece.heading_path, piece.headings, joined))
                continue
            merged.append(pieces[0])
        tiny = len(piece.body) < spec.min_chars
        if tiny and not merged and piece is pieces[0] and len(pieces) > 1:
            if piece.headings == pieces[1].headings[: len(piece.headings)]:
                first = piece
                continue
        if tiny and merged:
            joined = _joined(merged[-1], piece)
            if len(joined) <= spec.max_chars:
                previous = merged[-1]
                merged[-1] = _Piece(previous.heading_path, previous.headings, joined)
                continue
        merged.append(piece)
    return merged


def _joined(first: _Piece, second: _Piece) -> str:
    """Les textes de deux morceaux voisins, avec les titres du second que le premier n'a pas."""
    shared = 0
    for mine, theirs in zip(first.headings, second.headings, strict=False):
        if mine != theirs:
            break
        shared += 1
    titles = [f"{'#' * level} {heading}" for level, heading in second.headings[shared:]]
    return "\n\n".join([first.body, *titles, second.body])
